"""
Some general functions useful for vetting photometry
"""

import logging
from typing import Tuple, Optional, Iterable
from datetime import datetime, timezone, timedelta

from astropy.time import Time
from astropy.stats import akaike_info_criterion_lsq as info_crit
from astropy import units as u
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

from django.conf import settings
from tom_nonlocalizedevents.models import NonLocalizedEvent, EventSequence
from tom_dataproducts.models import ReducedDatum
from trove_targets.models import Target
from candidate_vetting.public_catalogs.phot_catalogs import TNS_Phot
from .tasks import async_atlas_query
from .scoring import get_eventcandidate_default_distance
from custom_code.templatetags.photometry_extras import error_to_snr

logger = logging.getLogger(__name__)

FILTER_PRIORITY_ORDER = ["r", "g", "V", "R", "G"]
PHOT_SCORE_MIN = 0.1
PREDETECTION_SNR_THRESHOLD = (
    5  # require a S/N of 5 for a predetection to be considered real
)


def _powerlaw(x, a, y0):
    """
    Powerlaw that returns a logarithmic y value
    """
    return y0 - a * np.log10(x)


def _broken_powerlaw_concave(x, a1, a2, y0, x0):
    """Smoothly broken powerlaw, CONCAVE branch. Original TROVE model.

    With u = x/x0 the two components are combined additively in flux,

        mag = y0 - log10(u**-a1 + u**-a2)

    `log10(sum of powers of u)` is convex in log10(u), so negating it makes
    this CONCAVE in the (log10 t, mag) plane: the slope d(mag)/d(log10 t) can
    only DECREASE with time. Writing a1 <= a2 (enforced by the fitter, see
    `_fit_broken`), the asymptotes are

        x << x0:  mag -> y0 + a2*log10(x/x0)      slope = a2
        x >> x0:  mag -> y0 + a1*log10(x/x0)      slope = a1   (a1 <= a2)

    Since slope > 0 means fading, this branch covers exactly the shapes whose
    behaviour becomes *more brightening* with time:

        decline -> rise        (rebrightening; SN IIb like SN2025ulz)
        decline -> shallower decline
        rise    -> steeper rise

    It CANNOT represent rise -> decline, decline -> steeper decline, or
    rise -> shallower rise. `_broken_powerlaw_convex` covers those.
    """
    return y0 - np.log10((x / x0) ** -a1 + (x / x0) ** -a2)


def _broken_powerlaw_convex(x, a1, a2, y0, x0):
    """Smoothly broken powerlaw, CONVEX branch. The complement of the above.

        mag = y0 + log10(u**-a1 + u**-a2)

    Same two components, opposite sign on the log, so this is CONVEX in
    (log10 t, mag): the slope can only INCREASE with time. With a1 <= a2,

        x << x0:  mag -> y0 - a2*log10(x/x0)      slope = -a2
        x >> x0:  mag -> y0 - a1*log10(x/x0)      slope = -a1  (-a1 >= -a2)

    covering the three shapes the concave branch cannot:

        rise    -> decline     (a transient peaking at the break)
        decline -> steeper decline
        rise    -> shallower rise

    WHY BOTH ARE NEEDED. The sign of the log term fixes the curvature, and one
    functional form cannot do both: log-sum-exp is convex, its negation
    concave. Fitting only one silently restricts the model to three of the six
    possible break shapes -- and the original bounds (a1 < 0 < a2) narrowed
    that to a single shape. See diagnostics/reports/BROKEN_POWERLAW_SHAPES.md.
    """
    return y0 + np.log10((x / x0) ** -a1 + (x / x0) ** -a2)


def _ordered(base):
    """Reparameterise `base(x, a1, a2, ...)` as `(x, a1, delta, ...)`, a2 = a1 + delta.

    Both broken powerlaws are symmetric under swapping a1 and a2, which gives
    the fit two identical minima and makes convergence and interpretation
    unstable. Requiring `delta >= 0` breaks that symmetry with a box bound,
    and pins a1 as the LATE-time index for both branches, so one expression
    reads the decay rate off either.

    This replaces the original device for breaking the same symmetry -- bounding
    a1 < 0 < a2 -- which also, unintentionally, restricted the model to one of
    the six break shapes.
    """

    def wrapped(x, a1, delta, y0, x0):
        return base(x, a1, a1 + delta, y0, x0)

    return wrapped


def _ssr(model_y, data_y):
    """Sum of the squares of the residuals"""
    residuals = data_y - model_y
    return np.sum(residuals**2)


def _decay_rate_sigma(raw_func, p0, bounds, dt_days, mag, magerr):
    """Uncertainty on parameter 0 of `raw_func`, from a refit weighted by `magerr`.

    `decay_rate` is always +/- parameter 0 of whichever raw fit function won
    (`_powerlaw`'s `a`, or the `_ordered`-reparameterised broken powerlaw's
    `a1`) -- see the sign-convention comment in `estimate_max_find_decay_rate`.
    A sign flip does not change a variance, so this is the uncertainty on
    `decay_rate` itself; no delta method / numerical gradient is needed.

    The point estimate TROVE reports comes from the UNWEIGHTED fit (`sigma`
    is not passed to the primary `curve_fit` call -- see
    diagnostics/reports/DECAY_UNCERTAINTY.md Sec 3), so this refits the same
    model once more with the photometric errors switched on, purely to get a
    covariance the unweighted fit cannot provide. `p0` should be the
    unweighted solution, already inside `bounds`, so this refit is a short
    correction rather than an independent search. Returns NaN if the refit
    does not converge or the covariance is not usable.
    """
    kwargs = dict(
        xdata=dt_days,
        ydata=mag,
        sigma=magerr,
        p0=p0,
        absolute_sigma=True,
        maxfev=5_000,
        ftol=1e-8,
    )
    if bounds is not None:
        kwargs["bounds"] = bounds
    try:
        _, pcov = curve_fit(raw_func, **kwargs)
    except (RuntimeError, TypeError):
        return np.nan
    if pcov is None or not np.all(np.isfinite(pcov)):
        return np.nan
    var = pcov[0, 0]
    return float(np.sqrt(var)) if var > 0 else np.nan


def _flux_to_lum(flux, lumdist):
    """convert flux to lum. Everything should be astropy quantities"""
    return 4 * np.pi * lumdist**2 * flux

def _get_phot(target_id: int, nonlocalized_event: NonLocalizedEvent) -> pd.DataFrame:
    """
    Get the photometry for this target_id and parse into a dataframe for further analysis
    """
    target = Target.objects.filter(id=target_id)[0]

    # get the photometry
    phot = list(ReducedDatum.objects.filter(target=target, data_type="photometry"))

    # clean up the photometry
    fordf = dict(
        telescope=[],
        mjd=[],
        mag=[],
        magerr=[],
        upperlimit=[],
        filter=[],
    )

    if len(phot) == 0:
        # just return an empty dataframe
        return pd.DataFrame(fordf)

    for p in phot:
        if hasattr(p, "source_name"):
            fordf["telescope"].append(p.source_name)
        elif "telescope" in p.value:
            fordf["telescope"].append(p.value["telescope"])
        else:
            fordf["telescope"].append("unknown")

        if not hasattr(p, "timestamp"):
            continue
        fordf["mjd"].append(Time(p.timestamp).mjd)

        if "filter" not in p.value:
            continue
        fordf["filter"].append(p.value["filter"])

        if "magnitude" in p.value:
            fordf["mag"].append(p.value["magnitude"])
            fordf["upperlimit"].append(False)
            if "error" in p.value:
                fordf["magerr"].append(p.value["error"])
            else:
                fordf["magerr"].append(0)
        elif "limit" in p.value:
            fordf["upperlimit"].append(True)
            fordf["mag"].append(p.value["limit"])
            fordf["magerr"].append(np.nan)
        else:
            continue

    fordf["filter"] = standardize_filter_names(fordf["filter"])

    photdf = pd.DataFrame(fordf)

    # clean out the 0's in the magerr column because it breaks the fitting
    # 2.5 / (3 * log(10)) is the constant 3 sigma uncertainty so let's assume this
    # as a worst case scenario
    photdf["magerr"] = photdf.magerr.replace(0, 2.5 / (3 * np.log(10)))

    # compute the days since the nonlocalized event passed in
    # get the GW event discovery date
    gw_disc_date = Time(
        EventSequence.objects.filter(nonlocalizedevent_id=nonlocalized_event.id)
        .last()
        .details["time"]
    ).mjd

    # add a dt column to the dataframe
    photdf["dt"] = photdf.mjd - gw_disc_date

    # add a SNR column to the dataframe
    photdf["snr"] = error_to_snr(photdf.magerr)

    return photdf


def _get_post_disc_phot(
    target_id: int,
    nonlocalized_event: NonLocalizedEvent,
    t_post: float = np.inf,
    t_pre: float = 0,
) -> pd.DataFrame:
    photdf = _get_phot(target_id, nonlocalized_event)
    if not len(photdf):
        return
    phot_post_disc = photdf.loc[(t_post >= photdf.dt) & (photdf.dt >= t_pre)]
    return phot_post_disc


def _get_pre_disc_phot(
    target_id: int,
    nonlocalized_event: NonLocalizedEvent,
    t_pre: float = 0,
) -> pd.DataFrame:
    photdf = _get_phot(target_id, nonlocalized_event)
    if not len(photdf):
        return
    phot_pre_disc = photdf[photdf.dt < t_pre]
    return phot_pre_disc


def _get_window_stats(min_idx, max_idx, isdet):
    return int(sum(isdet[min_idx:max_idx])), int(len(isdet[min_idx:max_idx]))


def standardize_filter_names(
    filters: list[str], delimiters: list[str] = [".", "-", " "]
) -> list[str]:

    newfilters = []
    for filt in filters:
        newfilt = filt
        for delim in delimiters:
            newfilt = newfilt.split(delim)[0]
        newfilters.append(newfilt.strip())
    return newfilters


def estimate_max_find_decay_rate(
    dt_days: Iterable[float],
    mag: Iterable[float],
    magerr: Iterable[float],
    max_decay_fit_time: Optional[int] = 25,
    min_significance: float = 3.0,
    min_baseline_days: Optional[float] = None,
    decay_rate_pass_range: Optional[Tuple[float, float]] = None,
) -> Tuple[float, float, float]:
    """
    Fit's both a single and broken powerlaw to the data, computes the AIC and then
    takes the "better" fit (lower AIC) and uses that to find an analytic time of maximum and decay
    rate over peak_time -> max_decay_fit_time.

    PARAMETERS
    ---------
    dt_days: Iterable[float]
        A list/array of the days since the GW discovery. These should all be positive
    mag: Iterable[float]
        A list/array of the magnitudes since the GW discovery
    magerr: Iterable[float]
        A list/array of the magnitude errors since the GW discovery
    max_decay_fit_time: int
        The maximum time after the GW discovery in days that we should fit the decay to.
        The default is 25 days based on discussion from Rastinejad+2022.
    min_significance: float
        Minimum |decay_rate| / sigma(decay_rate), sigma propagated from the
        photometric errors, required to accept a PASSING decay_rate (see
        `decay_rate_pass_range`) as measured rather than refuse it.
    decay_rate_pass_range: (float, float), optional
        The `[low, high]` window `decay_rate` must fall in to PASS the
        downstream kilonova check (`param_ranges["decay_rate"]` in the
        caller). 
    min_baseline_days: float, optional
        A NAIVE, purely temporal alternative/complement to `min_significance`:
        refuse the fit if `max(dt) - min(dt)` (over the de-duplicated points
        used for fitting) is below this many days. 

    RETURNS
    -------
    max_time: float
        Days since GW discovery for max to occur
    decay_rate: float
        The late-time logarithmic slope of the light curve, in d(mag)/d(log(10))
    """

    # define some useful variables
    pl_nparams = 2  # the degrees of freedom in a powerlaw model (m, y0, x0)
    bpl_nparams = (
        4  # the degrees of freedom in a broken powerlaw model (y0, x0, s, m1, m2)
    )

    # only fit data before `max_decay_fit_time`
    dt_days_tofit = dt_days[dt_days <= max_decay_fit_time]
    mag_tofit = mag[dt_days <= max_decay_fit_time]
    magerr_tofit = magerr[dt_days <= max_decay_fit_time]

    # Drop rows that repeat a measurement already present
    dt_days_tofit = np.asarray(dt_days_tofit, dtype=float)
    mag_tofit = np.asarray(mag_tofit, dtype=float)
    magerr_tofit = np.asarray(magerr_tofit, dtype=float)
    if dt_days_tofit.size:
        _, _keep = np.unique(
            np.column_stack((dt_days_tofit, mag_tofit)), axis=0, return_index=True
        )
        _keep.sort()
        if _keep.size < dt_days_tofit.size:
            logger.info(
                "Dropped %d duplicated photometry row(s) before fitting",
                dt_days_tofit.size - _keep.size,
            )
        dt_days_tofit = dt_days_tofit[_keep]
        mag_tofit = mag_tofit[_keep]
        magerr_tofit = magerr_tofit[_keep]

    n_epochs = int(np.unique(dt_days_tofit).size)
    if n_epochs < 2:
        raise RuntimeError(
            f"Only {n_epochs} distinct epoch(s) within {max_decay_fit_time} d "
            "-- the decay rate is not determined by this data"
        )

    if min_baseline_days is not None:
        baseline_days = float(dt_days_tofit.max() - dt_days_tofit.min())
        if baseline_days < min_baseline_days:
            raise RuntimeError(
                f"baseline {baseline_days:.3g} d < min_baseline_days="
                f"{min_baseline_days:.3g} d -- refusing to fit"
            )

    curve_fit_kwargs = dict(
        xdata=dt_days_tofit,
        ydata=mag_tofit,
        # sigma = magerr_tofit,
        absolute_sigma=True,
        maxfev=5_000,
        ftol=1e-8,
    )

    # ---- candidate 1: a single powerlaw -------------------------------
    try:
        pl_popt, pl_pcov = curve_fit(_powerlaw, **curve_fit_kwargs)
    except RuntimeError:
        # RuntimeError will throw if it doesn't converge
        pl_popt, pl_pcov = None, None

    broken_fits = {}
    if n_epochs > bpl_nparams + 2:
        # a1 free over the whole line -- the sign is no longer used to break
        # the a1 <-> a2 swap symmetry, `delta >= 0` does that instead.
        bpl_bounds = [
            (-np.inf, np.inf),  # a1: the LATE-time index (see `_ordered`)
            (0, np.inf),        # delta = a2 - a1, >= 0 to order the two
            (
                0,
                2 * mag_tofit.max(),
            ),  # y0 bound, really shouldn't be outside this range
            (
                0,
                dt_days_tofit.max(),
            ),  # x0 bound, really shouldn't be greater than max(dt)
        ]
        for label, base in (
            ("broken_concave", _broken_powerlaw_concave),
            ("broken_convex", _broken_powerlaw_convex),
        ):
            try:
                popt, _ = curve_fit(
                    _ordered(base), bounds=list(zip(*bpl_bounds)), **curve_fit_kwargs
                )
            except (RuntimeError, TypeError) as exc:
                # RuntimeError if it doesn't converge; TypeError if there are
                # fewer points than parameters
                logger.warning(f"Failed on the {label} fit with {exc}")
                continue
            a1, delta, y0, x0 = popt
            # store in the model's own (a1, a2, y0, x0) signature
            broken_fits[label] = (base, np.array([a1, a1 + delta, y0, x0]))

    # ---- choose between them on the AIC -------------------------------
    # `mag_slope_per_dex` is d(mag)/d(log10 t) at late times: POSITIVE means
    # the magnitude is rising, i.e. the source is getting FAINTER.
    options = []
    if pl_popt is not None:
        # mag = y0 - a*log10(t)  =>  slope = -a, at all times
        options.append(
            ("powerlaw", _powerlaw, pl_popt, pl_nparams, -pl_popt[0])
        )

    for label, (base, popt) in broken_fits.items():
        # with a1 <= a2 the late-time asymptote is governed by a1:
        #   concave  mag -> y0 + a1*log10(t)   =>  slope = +a1
        #   convex   mag -> y0 - a1*log10(t)   =>  slope = -a1
        slope = popt[0] if base is _broken_powerlaw_concave else -popt[0]
        options.append((label, base, popt, bpl_nparams, slope))

    if not options:
        raise RuntimeError(
            "Both a powerlaw and broken powerlaw failed to fit the data!"
        )

    if len(options) == 1:
        # nothing to compare against; AIC is undefined for n <= n_params + 1
        # and was not computed in this case before either
        label, model, best_fit_params, _nparams, mag_slope_per_dex = options[0]
    else:
        scored = []
        for label, f, popt, k, slope in options:
            ssr = _ssr(f(dt_days_tofit, *popt), mag_tofit)
            scored.append(
                (info_crit(ssr, k, len(mag_tofit)), label, f, popt, slope)
            )
        scored.sort(key=lambda row: row[0])
        _aic, label, model, best_fit_params, mag_slope_per_dex = scored[0]
        logger.info(
            "Model selected: %s (AIC %s)",
            label,
            ", ".join(f"{lb}={ac:.2f}" for ac, lb, *_ in scored),
        )

    # finally, compute the maximum time using a finely spaced array
    # from min -> max of the dt_days array
    xtest = np.linspace(
        np.min(dt_days_tofit), np.max(dt_days_tofit), 100 * max_decay_fit_time
    )
    ytest = model(xtest, *best_fit_params)
    max_time = xtest[
        np.argmin(ytest)
    ]  # need to use min here b/s magnitudes are backwards

    decay_rate = -mag_slope_per_dex

    would_pass = (
        decay_rate_pass_range is None
        or decay_rate_pass_range[0] <= decay_rate <= decay_rate_pass_range[1]
    )
    if would_pass:
        if label == "powerlaw":
            raw_func, p0, bounds = _powerlaw, tuple(best_fit_params), None
        else:
            a1, a2, y0, x0 = best_fit_params
            raw_func = _ordered(model)
            p0 = (a1, a2 - a1, y0, x0)
            bounds = list(zip(*bpl_bounds))
        sigma_decay_rate = _decay_rate_sigma(
            raw_func, p0, bounds, dt_days_tofit, mag_tofit, magerr_tofit
        )
        # Significance = 1 / relative uncertainty
        significance = (
            abs(decay_rate) / sigma_decay_rate
            if sigma_decay_rate and np.isfinite(sigma_decay_rate) and sigma_decay_rate > 0
            else np.nan
        )
        if not (significance >= min_significance):
            sig_str = (
                f"{significance:.2g}" if np.isfinite(significance) else "an unmeasurable number of"
            )
            raise RuntimeError(
                f"decay_rate {decay_rate:.3g} would pass the check but is only "
                f"{sig_str} sigma from zero (need >= {min_significance}) -- the "
                "photometry is not sufficiently separated in time to constrain "
                "a slope"
            )

    return model, best_fit_params, max_time, decay_rate
    
FILTER_EFF_FREQ = {
    'u': 8.468e14 * u.Hz,
    'g': 6.289e14 * u.Hz,
    'r': 4.832e14 * u.Hz,
    'i': 3.948e14 * u.Hz,
    'z': 3.343e14 * u.Hz,
    'y': 3.043e14 * u.Hz,
    'U': 8.468e14 * u.Hz,
    'B': 6.810e14 * u.Hz,
    'V': 5.483e14 * u.Hz,
    'R': 4.610e14 * u.Hz,
    'I': 3.807e14 * u.Hz,
    'c': 5.4e14 * u.Hz, # ATLAS cyan
    'o': 4.8e14 * u.Hz, # ATLAS orange
    'G': 5.5e14 * u.Hz, # Gaia G-band
    'w': 5.208e14 * u.Hz, # Pan-STARRS w (wide)
    'F070W': 4.310e14 * u.Hz, # JWST
    'F090W': 3.362e14 * u.Hz,
    'F115W': 2.629e14 * u.Hz,
    'F150W': 2.019e14 * u.Hz,
    'F182M': 1.631e14 * u.Hz,
    'F200W': 1.526e14 * u.Hz,
    'F250M': 1.199e14 * u.Hz,
    'F277W': 1.101e14 * u.Hz,
    'F300M': 1.006e14 * u.Hz,
    'F335M': 0.894e14 * u.Hz,
    'F356W': 0.850e14 * u.Hz,
    'F360M': 0.829e14 * u.Hz,
    'F444W': 0.690e14 * u.Hz,
    'F560W': 0.537e14 * u.Hz,
    'F770W': 0.399e14 * u.Hz,
    'F1000W': 0.303e14 * u.Hz,
    'F1130W': 0.265e14 * u.Hz,
    'F1280W': 0.236e14 * u.Hz,
    'F1500W': 0.201e14 * u.Hz,
    'F1800W': 0.168e14 * u.Hz,
    'F2100W': 0.146e14 * u.Hz,
    'F2550W': 0.119e14 * u.Hz,
    'F062': 4.962e14 * u.Hz, # Roman
    'F087': 3.494e14 * u.Hz,
    'F106': 2.876e14 * u.Hz,
    'F129': 2.355e14 * u.Hz,
    'F146': 2.355e14 * u.Hz,
    'F158': 1.929e14 * u.Hz,
    'F213': 1.421e14 * u.Hz,
}

def _mag_to_flux(mag, magerr=None):
    """
    Convert AB magnitude to flux in W/m^2/Hz.
    
    For AB magnitudes: flux (Jy) = 10^((8.9 - mag) / 2.5)
    1 Jy = 1e-26 W/m^2/Hz
    """
    flux_jy = 10**((8.9 - mag) / 2.5)
    flux = flux_jy * 1e-26
    
    if magerr is not None:
        dflux = np.abs(flux * magerr * np.log(10) / 2.5)
        return flux, dflux
    return flux

def compute_peak_lum(
    mag: Iterable[float],
    magerr: Iterable[float],
    filters: Iterable[str],
    lumdist: u.Quantity,
    consider_err: bool = True,
) -> float:
    """
    Computes the peak luminosity (nu L_nu) for comparison with models

    Parameters
    ----------
    mag: Iterable[float]
        An array of magnitudes
    magerr: Iterable[float]
        An array of magnitude errors
    filters: Iterable[str]
        The telescope filters used for conversion to optical luminosity
    lumdist: float
        The luminosity distance to calculate the luminosity at as an astropy Quantity
    consider_err: bool
        It is possible that the peak magnitude has large uncertainties. To be
        conservative we can consider the 3-sigma uncertainty on the peak magnitude as
        the peak value to compute the luminosity for. Default is True (to be the most
        conservative with our cuts!)

    Returns
    -------
    The peak luminosity (nu L_nu) in erg/s
    """
    mag = np.asarray(mag)
    magerr = np.asarray(magerr)
    
    if len(mag) == 0:
        return None
    
    flux, dflux = _mag_to_flux(mag, magerr)
    
    fluxmax_idx = np.argmax(flux)
    fluxmax = flux[fluxmax_idx]
    if consider_err:
        fluxmax += 3 * dflux[fluxmax_idx]
    filtermax = filters[fluxmax_idx]
    
    fluxmax = fluxmax * u.Unit("W/m^2/Hz")
    lummax = _flux_to_lum(fluxmax, lumdist).to("erg/s/Hz")

    freq_eff = FILTER_EFF_FREQ.get(filtermax, 5.0e14 * u.Hz)
    nu_lummax = (freq_eff * lummax).to("erg/s")
    return nu_lummax


def get_predetection_stats(
    mjd: list[float], magerr: list[float], det_snr_thresh: int = 5, window_size: int = 5
) -> tuple[list[int], list[int]]:
    """
    Uses a sliding window to find all predetections within window_size and
    returns 1) a list of the number of predetections and 2) a list of the number
    of observations within that window

    Parameters
    ----------
    mjd: list[float]
        A list of the MJDs of the observations
    magerr: list[bool]
        A list the same length as mjd with the uncertainty on the magnitude. We use
        this with `det_snr_thresh` to determine if the observation is a detection
    det_thresh: int
        The required signal to noise ratio for a point to be considered a detection
    window_size: int
        The window size in days. Default is 5.

    Returns
    -------
    Two lists: 1) the number of predetections in each window and 2) the number of
    observations in each window
    """

    # derive an array of if the observation is a detection
    isdet = ~np.isnan(magerr) * (magerr < 2.5 / (det_snr_thresh * np.log(10)))

    # sort both arrays according to the MJD
    sorted_idx = np.argsort(mjd)
    times = mjd[sorted_idx]
    isdet = isdet[sorted_idx]

    # now iterate from 0+window_size to end-window_size
    res = [
        _get_window_stats(
            np.where(times == times[i - window_size])[0][0],
            np.where(times == times[i + window_size])[0][0],
            isdet,
        )
        for i in range(0 + window_size, len(isdet) - window_size, 1)
    ]

    # now we can transpose the result and return
    return tuple(zip(*res))


def find_public_phot(
    target: Target, forced_phot_tol=1, days_ago_max=200, queue_priority=100
) -> None:
    """Query TNS, ATLAS Forced photometry, and other services for publicly available
    photometry. After querying for new photometry it will automatically add it to
    the target.

    Parameters
    ----------
    target: Target
        The Trove Target model object to find data for
    forced_phot_tol: int = 1
        The tolerance on the forced photometry sources. If we have queried for forced
        photometry in the past forced_phot_tol days, we skip querying for more.
    days_ago_max: int = 200
        The days ago to query forced photometry servers for. If forced photometry
        already exists from a service within days_ago, we only query for the days since
        the last existing photometry point.

    Returns
    -------
    A boolean, True if new TNS photometry was created, False if no new photometry was
    found
    """

    # check TNS for any new photometry
    created_new_tns_phot, tns_reply = TNS_Phot("tns").query(target, timelimit=10)

    # query ATLAS for new forced photometry
    # get the most recent ATLAS forced photometry point
    atlas_data = target.reduceddatum_set.filter(
        data_type="photometry", source_name="ATLAS"
    )
    query_atlas = True
    days_ago = (
        days_ago_max  # initialize days_ago as the maximum, and recompute as needed
    )
    if atlas_data.count():  # if this is true there is existing ATLAS data
        last_atlas_point = atlas_data.order_by("timestamp").last()

        now = datetime.now(tz=timezone.utc)
        if last_atlas_point.timestamp < now - timedelta(days=forced_phot_tol):
            # then we should only query ATLAS for this target for forced photometry
            # since the last point we have
            days_ago = (now - last_atlas_point.timestamp).days
            query_atlas = (
                days_ago > 3
            )  # otherwise ATLAS probably won't have anything new
            print(
                f"ATLAS photometry already exists for {target.name}, most recent at "
                + f"{days_ago} days ago"
            )
        else:
            # Then we have already queried ATLAS for this target in the past forced_phot_tol days
            query_atlas = False

    # `SKIP_ATLAS_FORCED_PHOT` is an opt-out for development databases, where a
    # Vet All over a few hundred candidates would otherwise enqueue a forced
    # photometry query per target against the real ATLAS service.
    #
    # The setting already existed in settings_local.py but NOTHING read it, so
    # it protected nothing -- vetting still queued every request. It is honoured
    # here, at the enqueue, rather than inside `async_atlas_query`: a task that
    # is never created cannot be run later by a worker that happens to pick up
    # the atlas_fphot queue.
    if query_atlas and getattr(settings, "SKIP_ATLAS_FORCED_PHOT", False):
        logger.info(
            "SKIP_ATLAS_FORCED_PHOT is set -- not queuing ATLAS forced "
            "photometry for %s", target.name
        )
        query_atlas = False

    if query_atlas:
        print(
            "Asynchronously obtaining ATLAS forced photometry with "
            + f"days_ago = {min(days_ago_max, days_ago):.2f}\n\n"
        )
        async_atlas_query.using(
            priority=queue_priority  # this sets the priority to whatever is passed in
        ).enqueue(
            target.id,
            days_ago=min(
                days_ago_max, days_ago
            ),  # this min ensures we never query more than days_ago_max
        )

    return created_new_tns_phot


def _score_phot(allphot, target, nonlocalized_event, param_ranges, filt=None):

    if allphot is None:  # this is if there is no photometry
        return 1, None, None, None, None, None

    # allphot will have already been filtered not to extend beyond param_ranges['t_post']
    # we still need to toss out (1) upper limits (2) detections below a SNR threshold
    phot = allphot[~allphot.upperlimit]
    phot = phot[phot.snr >= param_ranges["phot_score_snr_min"]]
    if not len(phot):
        # then there is no photometry for this object and we're done!
        return 1, None, None, None, None, None

    # find the filter we will use for the photometry analysis
    if filt is None:
        for filt in FILTER_PRIORITY_ORDER:
            if filt in phot["filter"].values:
                break
        else:
            # This target does not have any photometry with the correct filters!
            # so we return a score of 1
            return 1, None, None, None, None, None

    # now filter down the photometry
    if isinstance(filt, list) or isinstance(filt, set):
        phot = phot[phot["filter"].isin(filt)]
    elif filt != "all" and isinstance(filt, str):
        phot = phot[phot["filter"] == filt]

    # if we've made it to this point we have at least one detection so
    # we can calculate the luminosity
    dist, _ = get_eventcandidate_default_distance(
        target.id, nonlocalized_event.event_id
    )
    lum = compute_peak_lum(phot.mag, phot.magerr, phot["filter"].tolist(), dist * u.Mpc)

    phot_score = 1
    if lum is not None and (
        lum < param_ranges["lum_max"][0] or lum > param_ranges["lum_max"][1]
    ):
        phot_score *= PHOT_SCORE_MIN

    # then we can only do the next stuff if there is more than one photometry point
    # at this filter
    # has to be at least 2 distinct EPOCHS before max_decay_fit_time to fit the
    # powerlaw. Counting rows instead let a measurement ingested twice satisfy
    # this and hand `estimate_max_find_decay_rate` a rank-deficient problem;
    # that function now refuses such data as well, so this is the cheap guard
    # and that one is the authoritative check.
    _in_window = phot.dt[phot.dt < param_ranges["max_decay_fit_time"]]
    if _in_window.nunique() > 1:
        # find the maximum and decay rate
        try:
            _model, _best_fit_params, max_time, decay_rate = (
                estimate_max_find_decay_rate(
                    phot.dt,
                    phot.mag,
                    phot.magerr,
                    max_decay_fit_time=param_ranges["max_decay_fit_time"],
                    min_significance=param_ranges.get(
                        "min_decay_significance", 3.0
                    ),
                    decay_rate_pass_range=param_ranges["decay_rate"],
                )
            )
        except RuntimeError as exc:
            logger.warning(
                "Not setting peak_time or decay_rate for target %s: %s",
                target.id,
                exc,
            )
            return phot_score, lum, None, None, None, None

        # check if these are within the appropriate ranges
        if (
            max_time < param_ranges["peak_time"][0]
            or max_time > param_ranges["peak_time"][1]
        ):
            # this is to make sure we don't bias the score if there are no observations in the peak_time time range
            if phot.dt.min() > param_ranges["peak_time"][1]:
                max_time = None
            else:
                phot_score *= PHOT_SCORE_MIN

        if (
            decay_rate < param_ranges["decay_rate"][0]
            or decay_rate > param_ranges["decay_rate"][1]
        ):
            phot_score *= PHOT_SCORE_MIN

        return phot_score, lum, max_time, decay_rate, _model, _best_fit_params

    return phot_score, lum, None, None, None, None
