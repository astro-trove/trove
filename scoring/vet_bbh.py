"""
Vetting candidate counterparts to BBH events as AGN flares: a brightening of a
pre-existing, variable AGN rather than a fresh transient.

Reuses the skymap / host / distance machinery from the KN vetters. What is specific
to this module, in the order it appears below:

* AGN identity -- is the host an AGN at all? Catalog association (Milliquas /
  RomaBzcat) fires for ~0.5% of TROVE's candidates, so mid-IR colour and pre-trigger
  variability are also consulted.
* Flare detection -- a model-agnostic brightening excursion above the host's own
  variability envelope, with the envelope modelled as a DRW structure function so it
  grows with time lag.
* Contaminant discrimination -- scored as *positive predictions of the AGN-flare
  hypothesis* rather than as a checklist of known impostors, so a transient class
  nobody has thought of still has to clear the same bars.
* Flare shape -- model-*dependent* timing check against three published emission
  mechanisms, kept out of the default score by a read-time toggle.

"Model-agnostic" here has always meant "assumes no BBH-flare emission model", since
none is observationally confirmed. It never meant "assumes nothing": supernova and
AGN phenomenology are both far better established than any BBH-flare mechanism.

References
----------
Butler & Bloom 2011, AJ 141, 93                 (variability-selected quasars)
Campanelli et al. 2007, PRL 98, 231102          (spin superkicks)
Gonzalez et al. 2007, PRL 98, 091101            (mass-asymmetry recoil)
Hogg 1999, astro-ph/9905116                     (cosmological distance conventions)
Kelly, Bechtold & Siemiginowska 2009, ApJ 698, 895   (DRW quasar variability)
MacLeod et al. 2010, ApJ 721, 1014              (DRW structure function of SDSS quasars)
McKernan et al. 2019, ApJL 884, L50             (ram-pressure-stripped Hill sphere)
Rodriguez-Ramirez et al. 2025, PhRvD 111, 083020 (jet cocoon)
Rousseeuw & Croux 1993, JASA 88, 1273           (1.4826 MAD -> sigma)
Stern et al. 2012, ApJ 753, 30                  (WISE W1-W2 AGN selection)
Tagawa et al. 2024, ApJ 966, 21                 (jet breakout + shock cooling)
"""

import logging
from typing import Optional, Tuple
from astropy.time import Time, TimeDelta
from astropy import units as u
import numpy as np
import pandas as pd
from scipy.stats import norm

from .scoring import (
    update_score_factor,
    delete_score_factor,
    host_distance_match,
    get_distance_score,
    skymap_association,
    clean_host_df,
    _localization_from_name,
)
from .vet_basic import vet_basic
from .vet_phot import (
    _get_post_disc_phot,
    _get_pre_disc_phot,
    PHOT_SCORE_MIN,
)

from trove_targets.models import Target
from tom_nonlocalizedevents.models import (
    EventCandidate,
    NonLocalizedEvent,
    EventSequence,
)

logger = logging.getLogger(__name__)

PARAM_RANGES = dict(
    t_pre=0, # consider using t_pre < 0
    t_post=400,
    min_baseline_pts=2,  # smallest n whose MAD carries any scatter information:
    # at n=1 the MAD is identically 0 and the "baseline" collapses to a single
    # point plus its own reported error, which inflates flare significance for
    # sparsely sampled filters. Kept low (not 5+) because TROVE photometry is
    # often sparse and KN scoring likewise scores off very few points.
    flare_sigma_thresh=5.0,
    flare_score_center_frac=1.0,  # sigmoid midpoint, as a fraction of
    # flare_sigma_thresh. At 1.0 a 5-sigma excursion scores ~0.55 and ~7.5 sigma
    # saturates; the previous 0.5 centred the sigmoid at 2.5 sigma, which handed
    # 0.55 to excursions nobody would call a detection.
    flare_score_width_frac=0.25,  # sigmoid transition width, as a fraction of flare_sigma_thresh
    agn_boost_multiplier=5.0,
)

# ---------------------------------------------------------------------------
# AGN variability model (DRW)
# ---------------------------------------------------------------------------
# AGN vary stochastically as a damped random walk, whose structure function is
#   SF(dt) = SF_inf * sqrt(1 - exp(-dt/tau))
# i.e. the variability amplitude *grows with time lag* and saturates beyond the
# damping timescale tau (Kelly+2009; MacLeod+2010). This is the right denominator
# for a flare significance: measuring scatter over a few days of baseline and then
# comparing a point 300 days later against it systematically overstates how unusual
# that point is.
DEFAULT_SF_INF = 0.20        # mag, typical of the MacLeod+2010 quasar sample
DEFAULT_TAU_REST_DAYS = 200.0  # REST-frame days -- MacLeod+2010 quote tau in the
# quasar's frame, so the observed damping timescale is (1+z) times this. SF_inf is
# an amplitude in magnitudes and does not time-dilate, though it does depend on
# rest-frame wavelength, which is not corrected for here (see the note on
# K-corrections above `flare_luminosity_erg_s`).
DEFAULT_TAU_DAYS = DEFAULT_TAU_REST_DAYS  # back-compat alias, z-independent default
MIN_SF_MAG = 0.02          # never claim a perfectly steady AGN from sparse data
# Points needed before a measured SF_inf is trusted on its own. Below this the
# estimate is shrunk toward DEFAULT_SF_INF with weight n/(n + SF_SHRINK_N0), so a
# 3-point light curve returns essentially the literature prior and only a
# well-sampled one moves far from it. This is the guard against the estimator
# reporting a confident-looking amplitude that a handful of noisy points cannot
# actually support. Note the shrinkage is on the *number of epochs*, not the number
# of pairs: N points give N(N-1)/2 pairs but only ~N-1 independent differences, so
# counting pairs would overstate the information by a factor that grows with N.
SF_SHRINK_N0 = 10

# Stern et al. 2012 single-colour AGN criterion (Vega). Between the two values the
# colour is ambiguous and the boost is interpolated rather than cliff-edged.
WISE_AGN_W1W2_CUT = 0.8
WISE_GALAXY_W1W2 = 0.5

# 1 arcsec subtends this many kpc at 1 Mpc
KPC_PER_ARCSEC_PER_MPC = 4.84813681e-3
# ground-based astrometric tie precision; offsets at or below this are unresolved
ASTROMETRIC_FLOOR_ARCSEC = 0.3
# a BBH merging in an AGN disk sits sub-parsec from the SMBH, so the question is
# not "how far from the nucleus" but "resolvably off-nucleus at all"
NUCLEAR_SCALE_KPC = 0.5

# TNS gives an object the "SN" prefix only once a classification report carrying a
# spectrum has been accepted; unclassified objects keep "AT". A populated class is
# therefore strong evidence -- but low-S/N spectra, host contamination and early
# phases all produce revisions, and AGN have been mistaken for SNe. Only top-level
# types are used (sub-types like Ia-91T are markedly less reliable), and the result
# is always a penalty, never a veto.
#
# Ordered by how easily the class is confused with a BBH-induced AGN flare, NOT by
# how common it is. TDEs are the hardest case in the table: they are nuclear by
# definition, sit at 1e43-1e45 erg/s, and last months -- overlapping the model
# envelopes almost exactly -- so every geometric test in this module gives a TDE
# full marks. They are penalised hardest here for that reason. Supernovae, which
# actually dominate TROVE's classified candidates (>450 of 509), are easier: they
# are usually resolvably off-nucleus.
SN_LIKE_PREFIXES = ("SN", "SLSN")
GALACTIC_CLASSES = ("CV", "NOVA", "VARSTAR", "STAR", "M DWARF", "LBV")
AGN_LIKE_CLASSES = ("AGN", "QSO", "BLAZAR", "BL LAC", "LINER", "SEYFERT")
# nuclear, luminous, months-long: the most confusable class there is
NUCLEAR_IMPOSTOR_CLASSES = ("TDE",)
# anything classified as *something* that is not AGN-like is a competing
# explanation even when this module has no specific test for it
OTHER_TRANSIENT_CLASSES = ("GALAXY", "MICROLENSING", "GRB", "AGN?", "IMPOSTOR")


def drw_structure_function(
    dt_days: float, sf_inf: float = DEFAULT_SF_INF, tau_days: float = DEFAULT_TAU_DAYS
) -> float:
    """SF(dt) = SF_inf * sqrt(1 - exp(-dt/tau)); see the DRW note above."""
    dt = abs(float(dt_days))
    tau = max(float(tau_days), 1e-6)
    return float(sf_inf * np.sqrt(1.0 - np.exp(-dt / tau)))


def estimate_drw_params(
    phot: Optional[pd.DataFrame], min_pairs: int = 6
) -> Tuple[float, float, str]:
    """
    (SF_inf, tau_days, source) for one filter's pre-trigger photometry.

    Deliberately crude -- pairwise magnitude differences binned by lag with the
    measurement noise subtracted in quadrature -- because TROVE photometry is far
    too sparse for a real DRW likelihood fit. It only needs to answer "roughly how
    much does this wander", which is enough to stop treating measurement precision
    as if it were variability amplitude.

    Sparse data cannot be allowed to produce a confident-looking amplitude, so the
    measured SF_inf is shrunk toward the literature value with weight
    n/(n + SF_SHRINK_N0) in the number of *epochs*.

    Returns (sf_inf, tau_days, source), where source is one of:
      "measured"      -- enough epochs, variability detected above the noise
      "noise-limited" -- enough epochs, and the scatter is fully explained by the
                         quoted errors: a confident non-detection of variability
      "default"       -- too few epochs for either conclusion; prior returned
    """
    if phot is None or len(phot) < 3 or "dt" not in phot.columns:
        return DEFAULT_SF_INF, DEFAULT_TAU_DAYS, "default"

    t = phot.dt.to_numpy(dtype=float)
    m = phot.mag.to_numpy(dtype=float)
    e = phot.magerr.to_numpy(dtype=float)

    # pairwise SF estimator, as used for quasar variability by MacLeod+2010
    i, j = np.triu_indices(len(m), k=1)
    if len(i) < min_pairs:
        return DEFAULT_SF_INF, DEFAULT_TAU_DAYS, "default"
    lags = np.abs(t[i] - t[j])
    dmag = np.abs(m[i] - m[j])
    noise_var = e[i] ** 2 + e[j] ** 2

    long = lags >= np.median(lags)
    if long.sum() < 3:
        return DEFAULT_SF_INF, DEFAULT_TAU_DAYS, "default"

    # <dmag^2> = 2 SF^2 for a Gaussian process
    n_epochs = len(m)
    well_sampled = n_epochs / (n_epochs + SF_SHRINK_N0) > 0.5

    intrinsic_var = np.mean(dmag[long] ** 2 - noise_var[long]) / 2.0
    if not np.isfinite(intrinsic_var) or intrinsic_var <= 0:
        # Measurement noise fully explains the scatter. With enough epochs that is
        # a *confident non-detection* of variability, which is exactly what
        # `quiescent_host_score` needs; with few epochs it means nothing. These are
        # opposite conclusions, so they must not share the "default" label.
        return (MIN_SF_MAG, DEFAULT_TAU_DAYS,
                "noise-limited" if well_sampled else "default")

    sf_raw = float(np.clip(np.sqrt(intrinsic_var), MIN_SF_MAG, 2.0))

    # shrink toward the prior by how much independent information there actually is
    w = n_epochs / (n_epochs + SF_SHRINK_N0)
    sf_inf = float(np.clip(w * sf_raw + (1.0 - w) * DEFAULT_SF_INF, MIN_SF_MAG, 2.0))
    tau = float(np.clip(np.median(lags[long]), 5.0, 2000.0))
    return sf_inf, tau, ("measured" if w > 0.5 else "default")


def fit_agn_baseline(
    prephot: Optional[pd.DataFrame], min_baseline_pts: int = 2,
    redshift: Optional[float] = None,
) -> dict:
    """
    Per-filter pre-merger baseline: median mag, robust (MAD-based) scatter floored
    at measurement error, and the DRW parameters used to grow that scatter with
    time lag in `_flare_significance_series`.

    Deliberately model-agnostic about the *flare* -- no BBH-flare model is assumed.
    The DRW is a model of ordinary AGN variability, which is well established
    observationally and is what the excursion has to be measured against.

    `redshift`, when known, dilates the *default* DRW damping timescale into the
    observed frame; a tau measured from the candidate's own light curve is already
    observed-frame and is left alone.

    Returns dict mapping filter -> dict(mag, std, n, sf_inf, tau, median_err);
    filters below `min_baseline_pts` real detections are absent.
    """
    one_plus_z = 1.0 + float(redshift) if redshift is not None and np.isfinite(redshift) else 1.0
    baseline = {}
    if prephot is None or not len(prephot):
        return baseline

    phot = prephot[~prephot.upperlimit]
    for filt, group in phot.groupby("filter"):
        mags = group.mag.to_numpy(dtype=float)
        if len(mags) < min_baseline_pts:
            continue
        median_mag = float(np.median(mags))
        # 1.4826 rescales the MAD to a Gaussian sigma (Rousseeuw & Croux 1993)
        robust_std = 1.4826 * float(np.median(np.abs(mags - median_mag)))
        # If scatter is less than detector noise, then just use detector noise as the scatter
        median_err = float(np.median(group.magerr.to_numpy(dtype=float)))
        robust_std = max(robust_std, median_err)
        sf_inf, tau, sf_source = estimate_drw_params(group)
        if sf_source != "measured":
            tau *= one_plus_z  # literature tau is rest-frame; observe it dilated
        baseline[filt] = dict(
            mag=median_mag, std=robust_std, n=int(len(mags)),
            median_err=median_err, sf_inf=sf_inf, tau=tau, sf_source=sf_source,
        )
    return baseline


def baseline_sigma(entry: dict, dt_days: Optional[float]) -> float:
    """
    Variability amplitude to compare an excursion against, at lag `dt_days`.

    Takes the larger of the DRW structure function at that lag and the directly
    measured MAD scatter, so modelling can only ever widen the denominator, never
    shrink it below what was actually observed. With no lag available it falls back
    to the measured scatter alone.
    """
    std = float(entry["std"])
    if dt_days is None or not np.isfinite(dt_days):
        return std
    sf = drw_structure_function(dt_days, entry.get("sf_inf", DEFAULT_SF_INF),
                                entry.get("tau", DEFAULT_TAU_DAYS))
    return max(std, sf, MIN_SF_MAG)


def _flare_significance_series(
    postphot: Optional[pd.DataFrame], baseline: dict
) -> Optional[pd.DataFrame]:
    """
    Shared by `detect_flare`/`estimate_flare_extent`: attach a brightening-
    significance column (sigma, baseline vs. observed mag) to every detection in
    `postphot` whose filter has a fitted baseline. None if nothing qualifies.
    """
    if postphot is None or not len(postphot) or not baseline:
        return None

    phot = postphot[~postphot.upperlimit]
    phot = phot[phot["filter"].isin(baseline.keys())]
    if not len(phot):
        return None

    has_dt = "dt" in phot.columns
    significance = [
        (baseline[row["filter"]]["mag"] - row.mag)
        / np.sqrt(
            baseline_sigma(baseline[row["filter"]], row["dt"] if has_dt else None) ** 2
            + row.magerr**2
        )
        for _, row in phot.iterrows()
    ]
    return phot.assign(significance=significance)


def detect_flare(
    postphot: Optional[pd.DataFrame],
    baseline: dict,
    sigma_thresh: float = 5.0,
):
    """
    Largest brightening significance in `postphot` against `baseline`, and its row.
    `sigma_thresh` isn't used to filter here; the caller compares against it.

    Returns (max_significance, best_row), or (np.nan, None) if nothing qualifies.
    """
    phot = _flare_significance_series(postphot, baseline)
    if phot is None:
        return np.nan, None
    idx = phot.significance.idxmax()
    return float(phot.significance.loc[idx]), phot.loc[idx]


def estimate_flare_extent(
    postphot: Optional[pd.DataFrame],
    baseline: dict,
    extent_sigma_thresh: float = 3.0,
):
    """
    Delay-from-merger and duration of a candidate flare, for `flare_shape_score`.
    `postphot` must carry a `dt` column (days since the GW trigger).

    delay_days is `dt` of the most significant point (same as `detect_flare`).
    duration_days spans the earliest-to-latest points anywhere in postphot at or
    above the looser `extent_sigma_thresh`, or None if fewer than two such points
    exist. Both None if no flare is detectable at all.
    """
    phot = _flare_significance_series(postphot, baseline)
    if phot is None:
        return None, None

    idx = phot.significance.idxmax()
    delay_days = float(phot.dt.loc[idx])

    elevated = phot[phot.significance >= extent_sigma_thresh]
    if len(elevated) < 2:
        return delay_days, None
    duration_days = float(elevated.dt.max() - elevated.dt.min())
    return delay_days, duration_days


def flare_confidence_score(
    significance: float,
    thresh: float,
    floor: float = PHOT_SCORE_MIN,
    center_frac: float = 1.0,
    width_frac: float = 0.25,
) -> float:
    # Maps significance to score using normal CDF. The 5-sigma reference matches
    # PREDETECTION_SNR_THRESHOLD's convention in vet_phot.py; the sigmoid itself is
    # TROVE's, not from the literature.
    center = center_frac * thresh
    width = max(width_frac * thresh, 1e-6)
    raw = norm.cdf(significance, loc=center, scale=width)
    return float(np.clip(floor + (1.0 - floor) * raw, floor, 1.0))


# ---------------------------------------------------------------------------
# Is the host an AGN at all?
# ---------------------------------------------------------------------------

def wise_colors_for_host(host_row: dict) -> Optional[Tuple[float, float, float]]:
    """
    (W1, W2, W1-W2) for a host-galaxy row that came from NED-LVS, or None.

    NED-LVS is the only one of TROVE's host catalogs carrying WISE photometry, and
    it already holds m_w1/m_w2 for ~1.77M galaxies in the local catalog DB -- so this
    costs one indexed lookup and no external service.
    """
    if not isinstance(host_row, dict):
        return None
    if str(host_row.get("Source", "")).lower() not in ("nedlvs", "ned-lvs", "ned_lvs"):
        return None
    trove_id = host_row.get("troveID")
    if trove_id is None:
        return None

    from candidate_vetting.public_catalogs.static_catalogs import NedlvsQ3C

    row = NedlvsQ3C.objects.filter(id=int(trove_id)).values("m_w1", "m_w2").first()
    if not row:
        return None
    w1, w2 = row.get("m_w1"), row.get("m_w2")
    if w1 is None or w2 is None:
        return None
    w1, w2 = float(w1), float(w2)
    if not (np.isfinite(w1) and np.isfinite(w2)):
        return None
    return w1, w2, w1 - w2


def wise_agn_score(host_rows, agn_boost: float = 5.0) -> Tuple[Optional[float], dict]:
    """
    AGN-likelihood from the best-matched host's mid-IR colour (Stern+2012), on the
    same "association can only help" convention as the catalog `agn_score`.

    Never penalising: a blue W1-W2 only means "not an obviously dusty AGN", and
    low-luminosity or host-diluted AGN fall below the cut routinely.

    Returns (score or None when no WISE colour is available, info dict).
    """
    if not host_rows:
        return None, {}
    for row in host_rows:  # best match first
        colors = wise_colors_for_host(row)
        if colors is None:
            continue
        w1, w2, w1w2 = colors
        info = dict(w1=w1, w2=w2, w1_minus_w2=round(w1w2, 3), host=row.get("ID"))
        if w1w2 >= WISE_AGN_W1W2_CUT:
            return float(agn_boost), {**info, "verdict": "AGN (Stern+2012)"}
        if w1w2 <= WISE_GALAXY_W1W2:
            return 1.0, {**info, "verdict": "galaxy-like"}
        frac = (w1w2 - WISE_GALAXY_W1W2) / (WISE_AGN_W1W2_CUT - WISE_GALAXY_W1W2)
        return float(1.0 + frac * (agn_boost - 1.0)), {**info, "verdict": "ambiguous"}
    return None, {}


def variability_agn_score(
    prephot: Optional[pd.DataFrame], agn_boost: float = 5.0, min_points: int = 10
) -> Tuple[Optional[float], dict]:
    """
    AGN-likelihood from stochastic variability in the *pre-trigger* light curve: an
    AGN wanders continuously, a supernova's host nucleus is quiescent until it goes.

    Variability is a standard quasar-selection route (Butler & Bloom 2011), but here
    it is not independent evidence -- it reuses the photometry the flare score is
    built from -- so it is a weaker vote than the WISE colour, and like it, boost-only.
    Returns (score or None when the light curve can't support the test, info).
    """
    if prephot is None or not len(prephot):
        return None, {}
    phot = prephot[~prephot.upperlimit]
    if len(phot) < min_points or "dt" not in phot.columns:
        return None, {}

    best = None
    for filt, group in phot.groupby("filter"):
        if len(group) < min_points:
            continue
        sf_inf, tau, source = estimate_drw_params(group)
        if source == "noise-limited":
            return 1.0, {"filter": filt, "n_points": len(group),
                         "verdict": "not detectably variable"}
        if source != "measured":
            # not enough epochs for the amplitude to mean anything on its own
            continue
        median_err = float(np.median(group.magerr.to_numpy(dtype=float)))
        ratio = sf_inf / max(median_err, 1e-3)
        if best is None or ratio > best[1]:
            best = (filt, ratio, sf_inf, tau, len(group))

    if best is None:
        return None, {}
    filt, ratio, sf_inf, tau, n = best
    info = dict(filter=filt, sf_inf=round(sf_inf, 3), tau_days=round(tau, 1),
                n_points=n, sf_over_err=round(ratio, 2))
    if ratio < 1.5:  # consistent with pure measurement noise
        return 1.0, {**info, "verdict": "not detectably variable"}
    # scale the boost by the shrinkage weight too, so a barely-qualifying light
    # curve cannot hand out the full 5x on the strength of a marginal estimate
    w = n / (n + SF_SHRINK_N0)
    frac = float(np.clip((ratio - 1.5) / 1.5, 0.0, 1.0)) * w
    info["shrinkage_weight"] = round(w, 2)
    return float(1.0 + frac * (agn_boost - 1.0)), {**info, "verdict": "variable"}


# ---------------------------------------------------------------------------
# Is it actually a supernova?
# ---------------------------------------------------------------------------

def tns_classification_score(
    classification: Optional[str], floor: float = PHOT_SCORE_MIN,
    agn_boost: float = 5.0,
) -> Tuple[float, dict]:
    """
    Fold a TNS spectroscopic classification into the score.

    TNS (Gal-Yam et al. 2021, TNSAN 1) issues the "SN" prefix only on an accepted
    spectroscopic classification report.

    1.0 for an unclassified object -- the common case, and correctly neutral. A
    spectroscopic supernova classification returns `floor` rather than 0.0: the
    candidate drops far down the ranking but stays recoverable. A BBH-induced AGN
    flare has never been confirmed, so this pipeline must not be able to delete a
    candidate outright on one spectrum.
    """
    if not classification or not str(classification).strip():
        return 1.0, {"verdict": "unclassified (TNS 'AT')"}
    c = str(classification).strip().upper()

    for agn in AGN_LIKE_CLASSES:
        if c.startswith(agn):
            return float(agn_boost), {"verdict": f"TNS '{classification}' supports AGN"}
    for nuc in NUCLEAR_IMPOSTOR_CLASSES:
        if c.startswith(nuc):
            # penalised HARDER than a supernova, not more gently: a TDE passes
            # every geometric test this module applies, so the classification is
            # the only thing standing between it and a top-ranked candidate
            return float(floor), {
                "verdict": f"TNS '{classification}': nuclear impostor, "
                           "indistinguishable geometrically"}
    for sn in SN_LIKE_PREFIXES:
        if c.startswith(sn):
            return float(floor), {"verdict": f"TNS spectroscopic class '{classification}'"}
    for other in GALACTIC_CLASSES + OTHER_TRANSIENT_CLASSES:
        if c.startswith(other):
            return float(floor), {"verdict": f"TNS '{classification}' is not an AGN flare"}
    # Classified as something, but nothing this module recognises. That is still a
    # competing explanation -- a named class means somebody took a spectrum and it
    # was not an AGN -- so it is mildly penalised rather than waved through, which
    # is what an unrecognised label used to get.
    return 0.5, {"verdict": f"TNS '{classification}': classified, not AGN-like"}


def projected_offset_kpc(offset_arcsec: float, distance_mpc: float) -> float:
    """Projected physical separation in kpc for an angular offset at a distance."""
    return float(offset_arcsec) * float(distance_mpc) * KPC_PER_ARCSEC_PER_MPC


def nuclear_offset_score(
    host_rows, floor: float = PHOT_SCORE_MIN, scale_kpc: float = NUCLEAR_SCALE_KPC
) -> Tuple[Optional[float], dict]:
    """
    Is the transient resolvably off-nucleus?

    An earlier version of this score was removed because a BBH-induced flare's true
    offset from the SMBH is far below arcsecond resolution, so offset cannot
    discriminate between BBH-flare *models*. True, and the wrong question: supernovae
    go off throughout their host galaxy, routinely kpc out, and that *is* resolvable.
    Offset is a weak discriminator among flare models and a strong one against
    supernovae, which is the contamination that actually dominates. The sub-pc scale
    of the flare region itself is from McKernan+2019.

    Two fixes over the removed version:
      * It scored angular offset. The same 1.1" is 78 pc at 14 Mpc and 2.0 kpc at
        370 Mpc -- nuclear in one case, a textbook supernova offset in the other.
        Physical offset separates them, and TROVE already stores the host distance.
      * It penalised offsets below the astrometric tie precision, inventing
        distinctions between 0.05" and 0.2" that no measurement supports.
    """
    if not host_rows:
        return None, {}
    for row in host_rows:
        if not isinstance(row, dict):
            continue
        off, dist = row.get("Offset"), row.get("Dist")
        if off is None or dist is None:
            continue
        try:
            off, dist = float(off), float(dist)
        except (TypeError, ValueError):
            continue
        if not (np.isfinite(off) and np.isfinite(dist)) or dist <= 0:
            continue

        resolved_arcsec = max(off - ASTROMETRIC_FLOOR_ARCSEC, 0.0)
        resolved_kpc = projected_offset_kpc(resolved_arcsec, dist)
        score = float(np.clip(scale_kpc / (scale_kpc + resolved_kpc), floor, 1.0))
        return score, dict(
            host=row.get("ID"), offset_arcsec=round(off, 3), distance_mpc=round(dist, 1),
            offset_kpc=round(projected_offset_kpc(off, dist), 3),
            resolved_offset_kpc=round(resolved_kpc, 3),
            verdict=("unresolved / nuclear" if resolved_kpc <= 0
                     else f"resolved {resolved_kpc:.2f} kpc off-nucleus"),
        )
    return None, {}


def quiescent_host_score(
    prephot: Optional[pd.DataFrame], floor: float = PHOT_SCORE_MIN,
    min_points: int = 10,
) -> Tuple[Optional[float], dict]:
    """
    Was the host doing anything *before* the trigger?

    This is the discriminator that generalises. Every mechanism in this module
    requires a flare superposed on an accreting, already-variable AGN. A transient
    in a host with a flat pre-trigger light curve is a one-off event in a quiescent
    galaxy -- which is what a supernova is, and what a tidal disruption event is,
    and what most things that are not AGN flares are. It does not need to know which.

    This matters most for the case every other test in this module gets wrong. A TDE
    is nuclear, so `nuclear_offset_score` gives it full marks; it is luminous and
    months-long, so it lands inside the model envelopes. Pre-trigger quiescence is
    the one geometric-test-independent handle on it -- and TDE hosts are
    preferentially quiescent and post-starburst, so it is a handle that works.

    Deliberately asymmetric with `variability_agn_score`, which only ever boosts:
    absence of a *catalog* match means little (the AGN may not be ingested), but
    absence of *measured variability in a light curve good enough to have shown it*
    is real evidence. So this only fires when the baseline is well enough sampled
    for the non-detection to mean something -- otherwise it returns None.
    """
    if prephot is None or not len(prephot):
        return None, {}
    phot = prephot[~prephot.upperlimit]
    if len(phot) < min_points or "dt" not in phot.columns:
        return None, {}

    best = None
    for filt, group in phot.groupby("filter"):
        if len(group) < min_points:
            continue
        sf_inf, tau, source = estimate_drw_params(group)
        if source == "default":
            continue  # too few epochs for a non-detection to be informative
        median_err = float(np.median(group.magerr.to_numpy(dtype=float)))
        ratio = sf_inf / max(median_err, 1e-3)
        if best is None or ratio > best[1]:
            best = (filt, ratio, len(group))

    if best is None:
        return None, {}
    filt, ratio, n = best
    info = dict(filter=filt, sf_over_err=round(ratio, 2), n_points=n)
    if ratio >= 1.5:
        info["verdict"] = "host varies pre-trigger (consistent with an AGN)"
        return 1.0, info
    # looked properly, saw nothing
    info["verdict"] = "host quiescent pre-trigger (one-off event in a quiet galaxy)"
    return float(floor + (1 - floor) * 0.25), info


def color_evolution_score(
    postphot: Optional[pd.DataFrame], floor: float = PHOT_SCORE_MIN,
    min_epochs: int = 3, max_pair_days: float = 5.0,
) -> Tuple[Optional[float], dict]:
    """
    Expanding, cooling ejecta redden close to monotonically after peak -- true of
    supernovae (Filippenko 1997, ARA&A 35, 309) and of any explosive transient with
    a cooling photosphere. AGN instead run "bluer when brighter" (Vanden Berk et al.
    2004, ApJ 601, 692). The test is on the coherence of the trend, so it does not
    depend on which explosive class produced it.

    Needs contemporaneous two-filter photometry at `min_epochs` separate times,
    which TROVE rarely has -- returns None rather than guessing when it doesn't.
    Only the *consistency* of the trend is tested, not its sign, so which filter is
    nominally bluer does not matter.
    """
    if postphot is None or not len(postphot) or "dt" not in postphot.columns:
        return None, {}
    phot = postphot[~postphot.upperlimit]
    counts = [(f, len(g)) for f, g in phot.groupby("filter") if len(g) >= 2]
    if len(counts) < 2:
        return None, {}
    counts.sort(key=lambda x: -x[1])
    f1, f2 = counts[0][0], counts[1][0]
    a = phot[phot["filter"] == f1].sort_values("dt")
    b = phot[phot["filter"] == f2].sort_values("dt")

    epochs = []
    for _, ra in a.iterrows():
        ra_dt = float(ra["dt"])
        near = b[(b.dt - ra_dt).abs() <= max_pair_days]
        if len(near):
            j = (near.dt - ra_dt).abs().idxmin()
            epochs.append((ra_dt, float(ra.mag - b.loc[j].mag)))
    if len(epochs) < min_epochs:
        return None, {}

    epochs.sort()
    colors = np.array([c for _, c in epochs])
    diffs = np.diff(colors)
    if not len(diffs):
        return None, {}
    frac_mono = float(max((diffs > 0).mean(), (diffs < 0).mean()))
    info = dict(filters=[f1, f2], n_epochs=len(epochs),
                monotonic_fraction=round(frac_mono, 2),
                total_color_change=round(float(colors[-1] - colors[0]), 3))
    if frac_mono >= 0.8 and abs(colors[-1] - colors[0]) > 0.1:
        info["verdict"] = "monotonic colour evolution (supernova-like)"
        return float(np.clip(floor + (1 - floor) * (1 - frac_mono), floor, 1.0)), info
    info["verdict"] = "no coherent colour trend (AGN-like)"
    return 1.0, info


def return_to_baseline_score(
    postphot: Optional[pd.DataFrame], baseline: dict,
    floor: float = PHOT_SCORE_MIN, min_points: int = 4,
) -> Tuple[Optional[float], dict]:
    """
    Explosive transients are single-peaked: they rise, decline and settle back to
    the host baseline within months -- supernovae, TDEs and novae alike. AGN
    variability is red noise and wanders without returning to a well-defined
    quiescent level (Kelly+2009). Again a statement about light-curve morphology,
    not about any particular impostor class.

    Returns None when there are too few post-trigger points to describe a shape.
    """
    if postphot is None or not len(postphot) or not baseline:
        return None, {}
    phot = postphot[~postphot.upperlimit]
    phot = phot[phot["filter"].isin(baseline.keys())]
    if len(phot) < min_points or "dt" not in phot.columns:
        return None, {}

    best_filt = max(baseline.keys(), key=lambda f: len(phot[phot["filter"] == f]))
    g = phot[phot["filter"] == best_filt].sort_values("dt")
    if len(g) < min_points:
        return None, {}

    entry = baseline[best_filt]
    excess = np.array([
        (entry["mag"] - row.mag) / baseline_sigma(entry, row["dt"])
        for _, row in g.iterrows()
    ])
    peak_idx = int(np.argmax(excess))
    half = excess[peak_idx] / 2.0
    above = excess >= half
    crossings = int(np.sum((~above[:-1]) & (above[1:])))
    returned = bool(excess[-1] < 1.0 and peak_idx < len(excess) - 1)

    info = dict(filter=best_filt, n_points=int(len(g)),
                peak_sigma=round(float(excess[peak_idx]), 2),
                final_sigma=round(float(excess[-1]), 2),
                half_max_crossings=crossings, returned_to_baseline=returned)
    if returned and crossings <= 1:
        info["verdict"] = "single peak returning to baseline (supernova-like)"
        return float(floor + (1 - floor) * 0.3), info
    if crossings > 1:
        info["verdict"] = "multiple excursions (AGN-like)"
        return 1.0, info
    info["verdict"] = "still elevated (inconclusive)"
    return 1.0, info


# ---------------------------------------------------------------------------
# GW-informed constraints
# ---------------------------------------------------------------------------

# Non-spinning mass-asymmetry recoil (Gonzalez et al. 2007, PRL 98, 091101):
#   v = A eta^2 sqrt(1 - 4 eta) (1 + B eta),  peaking near 175 km/s at q ~ 0.36.
KICK_A_KMS = 1.2e4
KICK_B = -0.93


def remnant_kick_velocity(mass_ratio: float) -> float:
    """
    Recoil of the merger remnant from mass asymmetry alone, km/s, for q = m2/m1 <= 1.

    NOTE this is the non-spinning term only. With spinning components, in-plane
    spin configurations dominate and give "superkicks" up to thousands of km/s
    (Campanelli et al. 2007), an order of magnitude above anything returned here --
    so this is a strict lower bound. TROVE does not currently ingest masses or
    spins (see `gw_source_parameters`), which is why it is unused by default.
    """
    q = float(np.clip(mass_ratio, 1e-6, 1.0))
    eta = q / (1.0 + q) ** 2
    if eta >= 0.25:
        return 0.0
    return float(KICK_A_KMS * eta**2 * np.sqrt(1.0 - 4.0 * eta) * (1.0 + KICK_B * eta))


# McKernan+2019 tie the flare delay directly to the kick: a fast remnant punches out
# of the disk in under ~3 days, a slow one takes ~300. (v_kick km/s, delay days).
MCK19_KICK_DELAY_ANCHORS = ((100.0, 300.0), (500.0, 3.0))


def kick_informed_delay_window(
    v_kick_kms: float, anchors=MCK19_KICK_DELAY_ANCHORS, width_factor: float = 3.0
) -> Tuple[float, float]:
    """
    Narrow McKernan+2019's delay envelope using a kick estimate.

    The stock `mck19` box spans 0-300 days *because* it marginalises over an unknown
    kick, which is exactly why `flare_shape_score` saturates at 1.0 and carries no
    discriminating power. Given v_kick the delay is a specific value; `width_factor`
    brackets it to stay honest about how coarsely the relation is known.

    Two limits worth knowing before reading anything into the output:

    * Masses alone are not enough. `remnant_kick_velocity` is the non-spinning
      mass-asymmetry term, which peaks at ~175 km/s -- below the 500 km/s anchor
      where McKernan+2019 predict a sub-3-day delay. So mass ratio on its own can
      only ever place a candidate in the slow-kick, long-delay regime; reaching the
      fast-kick regime needs the in-plane spin components (Campanelli+2007). It is
      spins, not masses, that would make this discriminating.
    * v_kick is clipped into the anchor range, so a near-equal-mass binary with
      essentially no recoil is treated as if it were kicked at 100 km/s rather than
      being marked as a channel that should not operate at all. McKernan+2019 do not
      quote behaviour below ~100 km/s, so the clip is an admission of ignorance, not
      a prediction -- do not read a wide window at q ~ 1 as evidence for anything.
    """
    (v_lo, d_lo), (v_hi, d_hi) = anchors
    v = float(np.clip(v_kick_kms, min(v_lo, v_hi), max(v_lo, v_hi)))
    frac = (np.log10(v) - np.log10(v_lo)) / (np.log10(v_hi) - np.log10(v_lo))
    delay = 10 ** (np.log10(d_lo) + frac * (np.log10(d_hi) - np.log10(d_lo)))
    return float(delay / width_factor), float(delay * width_factor)


def gw_source_parameters(nonlocalized_event) -> dict:
    """
    Masses/spins from the ingested alert, when present.

    Measured across 5,994 ingested sequences, LVK low-latency alerts carry only
    `far`, `classification` (BBH/BNS/NSBH/Terrestrial) and `properties` (HasNS,
    HasRemnant, HasMassGap, HasSSM) -- no component masses, no chirp mass, no spins.
    So this returns {} for every event TROVE currently holds, and the kick-informed
    narrowing below stays inert. It is wired up so that ingesting parameter
    estimation later (GraceDB PE, or a GWTC release) switches it on without further
    changes: the AGN-flare search runs on a 400-day window, so unlike KN follow-up
    there is no low-latency pressure and late PE is perfectly usable.
    """
    seq = EventSequence.objects.filter(
        nonlocalizedevent_id=nonlocalized_event.id
    ).last()
    details = (seq.details if seq else None) or {}
    out = {}
    for key in ("mass_1_source", "mass_2_source", "chirp_mass", "chirp_mass_source",
                "mass_ratio", "a_1", "a_2", "chi_eff"):
        if details.get(key) is not None:
            out[key] = details[key]
    if "mass_ratio" not in out and out.get("mass_1_source") and out.get("mass_2_source"):
        m1, m2 = float(out["mass_1_source"]), float(out["mass_2_source"])
        if m1 > 0 and m2 > 0:
            out["mass_ratio"] = min(m1, m2) / max(m1, m2)
    return out


# Predicted BBH-in-AGN-disk flare luminosities span roughly 1e43-1e45 erg/s
# (McKernan+2019; Tagawa+2024). Supernovae overlap at the faint end -- an SN Ia peaks
# near 1e43 -- so this rejects only the clearly-too-faint and is partial power only.
AGN_FLARE_LUM_RANGE_ERG_S = (1e42, 1e46)


def redshift_for(target, host_rows) -> Optional[float]:
    """
    Best available redshift: the target's own, else the best-matched host's.

    Needed because every published flare timescale is a source-frame quantity while
    everything TROVE measures is observed-frame. Returns None when neither is known,
    in which case callers fall back to observed-frame comparisons and the resulting
    scores are biased by (1+z) -- documented rather than silently corrected with a
    guessed redshift.
    """
    z = getattr(target, "redshift", None)
    if z is not None and np.isfinite(z):
        return float(z)
    for row in host_rows or []:
        if not isinstance(row, dict):
            continue
        hz = row.get("z")
        try:
            hz = float(hz)
        except (TypeError, ValueError):
            continue
        if np.isfinite(hz) and hz > 0:
            return hz
    return None


def flare_luminosity_erg_s(peak_mag: float, distance_mpc: float,
                           nu_eff_hz: float = 4.6e14) -> float:
    """
    nu*L_nu for a flare peak magnitude at a known distance, erg/s, using the
    GW-derived distance TROVE already has. `nu_eff_hz` is the *observed* effective
    frequency (r-band by default).

    No explicit (1+z) appears because it cancels for nu*L_nu (Hogg 1999): with
    f_nu(nu_obs) = (1+z) L_nu(nu_rest) / (4 pi D_L^2) and nu_rest = (1+z) nu_obs,

        nu_rest * L_nu(nu_rest) = 4 pi D_L^2 * nu_obs * f_nu(nu_obs)

    which is exactly what is computed below. What does NOT cancel is which part of
    the SED is being sampled: at z = 0.3 an observed r-band measurement probes
    rest-frame ~g. Correcting for that needs an assumed spectrum, and no BBH-flare
    SED is observationally established, so it is deliberately left uncorrected --
    a factor-of-a-few systematic on a quantity only compared against a four-decade
    envelope. One band, no bolometric correction.
    """
    d_cm = float(distance_mpc) * 3.0856775814913673e24
    f_nu = 10 ** (-0.4 * (float(peak_mag) + 48.60))  # erg/s/cm^2/Hz, AB
    return float(4.0 * np.pi * d_cm**2 * f_nu * nu_eff_hz)


# ---------------------------------------------------------------------------
# Flare shape (model-dependent, read-time toggle)
# ---------------------------------------------------------------------------

# (delay_lo, delay_hi), (duration_lo, duration_hi) envelopes, in *REST-FRAME* days
# since the merger, for three published BBH-in-AGN-disk emission mechanisms. These
# are physical timescales -- disk crossing, jet breakout, photon diffusion -- so the
# papers quote them in the source frame, and an observed delay must be divided by
# (1+z) before it is compared against them. At the ~1-2 Gpc where LVK detects BBHs
# that is a 20-36% correction, comparable to the width of the jrr_i box itself.
# Each box is a generous envelope around that paper's own quoted numbers, not a sharp
# physical limit -- survey cadence can't justify more precision than "roughly
# consistent with this channel":
#   mck19 -- McKernan et al. 2019 (ApJL 884, L50), ram-pressure-stripped Hill sphere:
#            delay <3 d (kick >~500 km/s) to ~300 d (<~100 km/s), duration ~1-100 d.
#   jrr_i -- Rodriguez-Ramirez et al. 2025 (PhRvD 111, 083020), jet-cocoon thermal
#            diffusion: needs kick >~200 km/s, delay ~50-100+ d, duration ~20-100+ d
#            (both open-ended upward, capped here at a generous finite value).
#   tgw24 -- Tagawa et al. 2024 (ApJ 966, 21), jet breakout + shock cooling: delay
#            <~50 d for close-in mergers out to 40-300 d at ~1 pc, duration ~10-200+ d.
#
# `amplitude` is the flare's brightening in magnitudes *relative to the host AGN's
# own quiescent level*. Contrast is used rather than absolute luminosity on purpose:
# the predicted luminosity of every one of these mechanisms scales with SMBH mass,
# disk density, jet energy and kick -- all unknown -- and spans more than two decades,
# where supernovae sit squarely in the middle of the range. The *ratio* to the AGN
# continuum cancels most of those unknowns and is what the papers actually constrain.
# It is also genuinely independent of `agn_flare_score`, which measures significance
# in sigma: a 0.1 mag flare on a very stable AGN is high-sigma but low-contrast, and
# the models predict amplitude, not signal-to-noise. Envelopes are deliberately wide;
# for scale, Graham et al. 2020 (PhRvL 124, 251102) report ~0.3 mag for the GW190521
# candidate ZTF19abanrhr.
FLARE_SHAPE_MODELS = dict(
    mck19=dict(delay=(0.0, 300.0), duration=(1.0, 100.0), amplitude=(0.3, 3.0)),
    jrr_i=dict(delay=(50.0, 150.0), duration=(20.0, 150.0), amplitude=(0.5, 4.0)),
    tgw24=dict(delay=(0.0, 300.0), duration=(10.0, 250.0), amplitude=(0.2, 2.0)),
)

# Deliberately NOT scored, though all three models predict it: the shock-cooling and
# cocoon channels (tgw24, jrr_i) predict a hot early spectrum cooling monotonically --
# which is precisely the signature `color_evolution_score` uses to flag supernovae.
# Scoring it as a model match and as SN evidence at the same time would have the
# pipeline reward and penalise one observation for the same reason, so the colour
# behaviour is left to the supernova discriminator alone and this degeneracy is
# recorded here rather than silently resolved one way.


def _box_edge_score(x: float, lo: float, hi: float, margin: float, floor: float) -> float:
    """1.0 inside [lo, hi]; falls off smoothly outside via margin/(margin+excess)."""
    if lo <= x <= hi:
        return 1.0
    excess = (lo - x) if x < lo else (x - hi)
    return float(np.clip(margin / (margin + excess), floor, 1.0))


def flare_amplitude_mag(baseline: dict, flare_row) -> Optional[float]:
    """Brightening of the flare peak above its filter's quiescent baseline, in mag."""
    if flare_row is None or not baseline:
        return None
    entry = baseline.get(flare_row["filter"])
    if entry is None:
        return None
    amp = float(entry["mag"]) - float(flare_row.mag)
    return amp if np.isfinite(amp) else None


def flare_shape_scores_by_model(
    delay_days: float,
    duration_days: Optional[float],
    models: dict = FLARE_SHAPE_MODELS,
    floor: float = PHOT_SCORE_MIN,
    amplitude_mag: Optional[float] = None,
) -> dict:
    """
    Score a *rest-frame* `(delay_days, duration_days)` against each model in
    `models` independently rather than collapsing straight to one aggregate: TROVE can't know
    a priori which mechanism (if any) applies to a candidate, so the breakdown of
    which published picture it resembles is worth showing on the candidate page.
    Each score multiplies a delay, duration and amplitude term, each 1.0 inside the
    model's envelope and falling off smoothly outside it via `_box_edge_score`.
    `duration_days=None` or `amplitude_mag=None` skips that term rather than
    guessing.

    Returns dict mapping model name -> score.
    """
    scores = {}
    for name, model in models.items():
        delay_lo, delay_hi = model["delay"]
        score = _box_edge_score(delay_days, delay_lo, delay_hi, delay_hi - delay_lo, floor)
        if duration_days is not None:
            dur_lo, dur_hi = model["duration"]
            score *= _box_edge_score(duration_days, dur_lo, dur_hi, dur_hi - dur_lo, floor)
        if amplitude_mag is not None and "amplitude" in model:
            amp_lo, amp_hi = model["amplitude"]
            score *= _box_edge_score(amplitude_mag, amp_lo, amp_hi, amp_hi - amp_lo, floor)
        scores[name] = score
    return scores


def _host_rows(target) -> list:
    """The "Host Galaxies" TargetExtra as a list of dicts, best match first."""
    import json
    from tom_targets.models import TargetExtra

    extra = TargetExtra.objects.filter(target_id=target.id, key="Host Galaxies").first()
    if not extra or not extra.value:
        return []
    try:
        rows = json.loads(extra.value) if isinstance(extra.value, str) else extra.value
    except (ValueError, TypeError):
        return []
    return rows if isinstance(rows, list) else []


def vet_bbh(
    target_id: int,
    nonlocalized_event_name: Optional[str] = None,
    param_ranges: dict = PARAM_RANGES,
):
    logger.info("Running BBH vetting (AGN-flare vetting)")

    # get the correct EventCandidate object for this target_id and nonlocalized event
    nonlocalized_event = NonLocalizedEvent.objects.get(event_id=nonlocalized_event_name)
    event_candidate = EventCandidate.objects.get(
        nonlocalizedevent_id=nonlocalized_event.id, target_id=target_id
    )
    target = Target.objects.get(id=target_id)

    ## check skymap association
    if np.isfinite(param_ranges["t_post"]):
        gw_disc_date = (
            EventSequence.objects.filter(  # GW discovery time
                nonlocalizedevent_id=nonlocalized_event.id
            )
            .last()
            .details["time"]
        )
        max_time = Time(gw_disc_date) + TimeDelta(param_ranges["t_post"] * u.day)
    else:  # just use current time
        max_time = Time.now()
    skymap_score = skymap_association(
        nonlocalized_event_name, target_id, max_time=max_time
    )
    update_score_factor(event_candidate, "skymap_score", skymap_score)

    # the skymap and distance scores are only valid for one localization, so
    # record which one produced them (matches vet_kn.py / vet_super_kn.py)
    localization = _localization_from_name(nonlocalized_event_name, max_time=max_time)
    update_score_factor(event_candidate, "localization_id", localization.id)

    ## get dataframes of potential hosts / AGN
    host_df, agn_df, keep_vetting = vet_basic(event_candidate.target.id)
    if not keep_vetting:
        # PS or MPC association already zeroed this candidate's score and the
        # host/AGN associations were not (re)performed -- same as vet_kn.py
        return

    # some cleanup before distance scoring
    host_df = clean_host_df(host_df)

    ## distance scoring
    if target.redshift is not None and not np.isnan(target.redshift):
        # use target redshift, so no need to compute distance scores for galaxies
        host_score, host_name = get_distance_score(
            host_df, target_id, nonlocalized_event_name
        )
        update_score_factor(event_candidate, "host_distance_score", host_score)

    elif len(host_df) != 0:
        # then run the distance comparison for each of these hosts
        host_df = host_distance_match(host_df, target_id, nonlocalized_event_name)

        host_score, host_name, host_catalog = get_distance_score(
            host_df, target_id, nonlocalized_event_name
        )
        update_score_factor(event_candidate, "host_distance_score", host_score)
        update_score_factor(event_candidate, "host_name", host_name)
        update_score_factor(event_candidate, "host_catalog", host_catalog)

    else:
        # if no target redshift is known and no hosts are found, we don't want
        # to bias the final score (host may just be too far / undetected)
        delete_score_factor(event_candidate, "host_distance_score")
        delete_score_factor(event_candidate, "host_name")
        delete_score_factor(event_candidate, "host_catalog")

    ## photometry, needed by both the AGN-identity and flare sections below
    prephot = _get_pre_disc_phot(
        target_id=target.id,
        nonlocalized_event=nonlocalized_event,
        t_pre=param_ranges["t_pre"],
    )
    postphot = _get_post_disc_phot(
        target_id=target_id,
        nonlocalized_event=nonlocalized_event,
        t_post=param_ranges["t_post"],
        t_pre=param_ranges["t_pre"],
    )
    host_rows = _host_rows(target)
    boost = param_ranges["agn_boost_multiplier"]

    # every published flare timescale is source-frame; everything measured here is
    # observed-frame. Record the redshift used so the correction is auditable.
    redshift = redshift_for(target, host_rows)
    one_plus_z = 1.0 + redshift if redshift is not None else 1.0
    if redshift is not None:
        update_score_factor(event_candidate, "redshift_used", redshift)
    else:
        delete_score_factor(event_candidate, "redshift_used")

    ## is the host an AGN? Three lines of evidence, all boost-only. They are
    ## combined with max(), NOT multiplied: catalog membership, red mid-IR colour
    ## and stochastic variability are largely the same claim arrived at three ways,
    ## so multiplying would let one AGN be counted as a 125x boost.
    catalog_agn = boost if len(agn_df) != 0 else 1.0
    wise_agn, wise_info = wise_agn_score(host_rows, agn_boost=boost)
    var_agn, var_info = variability_agn_score(prephot, agn_boost=boost)

    agn_score = max([catalog_agn] + [s for s in (wise_agn, var_agn) if s is not None])
    update_score_factor(event_candidate, "agn_score", agn_score)
    # components stored for display only (not in SUBSCORE_NAMES)
    update_score_factor(event_candidate, "agn_catalog_score", catalog_agn)
    if wise_agn is not None:
        update_score_factor(event_candidate, "agn_wise_score", wise_agn)
        update_score_factor(event_candidate, "agn_wise_w1w2",
                            wise_info.get("w1_minus_w2"))
    else:
        delete_score_factor(event_candidate, "agn_wise_score")
        delete_score_factor(event_candidate, "agn_wise_w1w2")
    if var_agn is not None:
        update_score_factor(event_candidate, "agn_variability_score", var_agn)
    else:
        delete_score_factor(event_candidate, "agn_variability_score")

    ## photometric flare scoring
    baseline = fit_agn_baseline(
        prephot, min_baseline_pts=param_ranges["min_baseline_pts"], redshift=redshift
    )
    max_significance, flare_row = detect_flare(
        postphot, baseline, sigma_thresh=param_ranges["flare_sigma_thresh"]
    )

    if baseline and postphot is not None and len(postphot) and np.isfinite(max_significance):
        agn_flare_score = flare_confidence_score(
            max_significance,
            param_ranges["flare_sigma_thresh"],
            floor=PHOT_SCORE_MIN,
            center_frac=param_ranges["flare_score_center_frac"],
            width_frac=param_ranges["flare_score_width_frac"],
        )
        update_score_factor(event_candidate, "agn_flare_score", agn_flare_score)

        # flare luminosity from the GW-derived distance, when we have one
        try:
            from .scoring import get_eventcandidate_default_distance

            dist_mpc, _ = get_eventcandidate_default_distance(
                target_id, nonlocalized_event_name
            )
            if flare_row is not None and dist_mpc and np.isfinite(dist_mpc) and dist_mpc > 0:
                lum = flare_luminosity_erg_s(float(flare_row.mag), float(dist_mpc))
                update_score_factor(event_candidate, "flare_peak_lum", lum)
            else:
                delete_score_factor(event_candidate, "flare_peak_lum")
        except Exception as exc:  # distance is optional context, never fatal
            logger.info("flare luminosity not computed: %s", exc)
            delete_score_factor(event_candidate, "flare_peak_lum")

        # model-dependent layer, always computed/stored; scoring/util.py's
        # flare_shape_toggle decides at read time whether it joins the score
        # product, the same way agn_toggle does for agn_score -- that's what lets
        # a user flip it live without a re-vet. Per-model scores are display-only
        # (not in SUBSCORE_NAMES), so they never enter the score product.
        flare_shape_model_keys = [f"flare_shape_score_{name}" for name in FLARE_SHAPE_MODELS]
        delay_days, duration_days = estimate_flare_extent(postphot, baseline)
        if delay_days is not None:
            # observed -> rest frame before comparing against source-frame model
            # boxes. With no redshift, one_plus_z is 1 and the comparison is
            # uncorrected; that bias is why `redshift_used` is stored alongside.
            delay_rest = delay_days / one_plus_z
            duration_rest = (duration_days / one_plus_z
                             if duration_days is not None else None)
            # narrow mck19's delay box if the GW alert ever carries masses/spins
            models = dict(FLARE_SHAPE_MODELS)
            gw = gw_source_parameters(nonlocalized_event)
            if gw.get("mass_ratio"):
                v_kick = remnant_kick_velocity(float(gw["mass_ratio"]))
                lo, hi = kick_informed_delay_window(v_kick)
                models["mck19"] = dict(models["mck19"], delay=(lo, hi))
                update_score_factor(event_candidate, "gw_kick_kms", v_kick)
            else:
                delete_score_factor(event_candidate, "gw_kick_kms")

            amplitude = flare_amplitude_mag(baseline, flare_row)
            if amplitude is not None:
                update_score_factor(event_candidate, "flare_amplitude_mag", amplitude)
            else:
                delete_score_factor(event_candidate, "flare_amplitude_mag")
            model_scores = flare_shape_scores_by_model(
                delay_rest, duration_rest, models, amplitude_mag=amplitude
            )
            for name, score in model_scores.items():
                update_score_factor(event_candidate, f"flare_shape_score_{name}", score)
            update_score_factor(
                event_candidate, "flare_shape_score", max(model_scores.values())
            )
        else:
            delete_score_factor(event_candidate, "flare_shape_score")
            for key in flare_shape_model_keys:
                delete_score_factor(event_candidate, key)
    else:
        # not enough baseline and/or post-merger photometry to judge either way --
        # don't bias the score
        delete_score_factor(event_candidate, "agn_flare_score")
        delete_score_factor(event_candidate, "flare_shape_score")
        delete_score_factor(event_candidate, "flare_peak_lum")
        for name in FLARE_SHAPE_MODELS:
            delete_score_factor(event_candidate, f"flare_shape_score_{name}")

    ## competing-explanation discrimination. Framed as the positive predictions of
    ## the AGN-flare hypothesis -- nuclear, on an already-variable AGN, with a
    ## light curve that is not a single cooling explosion -- rather than as a list
    ## of known impostors, so a class nobody enumerated still has to clear them.
    ## Combined with min(), NOT multiplied: these are circumstantial, and a search
    ## for a class of event that has never been confirmed should not be able to
    ## bury a candidate under compounded soft penalties.
    tns_score, tns_info = tns_classification_score(
        getattr(target, "classification", None), floor=PHOT_SCORE_MIN, agn_boost=boost
    )
    offset_score, offset_info = nuclear_offset_score(host_rows, floor=PHOT_SCORE_MIN)
    quiescent_score, quiescent_info = quiescent_host_score(prephot, floor=PHOT_SCORE_MIN)
    color_score, color_info = color_evolution_score(postphot, floor=PHOT_SCORE_MIN)
    shape_score, shape_info = return_to_baseline_score(
        postphot, baseline, floor=PHOT_SCORE_MIN
    )

    components = (
        ("contaminant_tns_score", tns_score),
        ("contaminant_offset_score", offset_score),
        ("contaminant_quiescent_score", quiescent_score),
        ("contaminant_color_score", color_score),
        ("contaminant_shape_score", shape_score),
    )
    for key, val in components:
        if val is None:
            delete_score_factor(event_candidate, key)
        else:
            update_score_factor(event_candidate, key, val)

    # keys used before this was generalised beyond supernovae
    for stale in ("sn_penalty_score", "sn_tns_score", "sn_offset_score",
                  "sn_color_score", "sn_shape_score"):
        delete_score_factor(event_candidate, stale)

    available = [v for _, v in components if v is not None]
    if available:
        # a TNS class of AGN/QSO can push this above 1; that is intended -- it is
        # the one piece of evidence here that argues *for* an AGN flare
        update_score_factor(event_candidate, "contaminant_score", min(available))
    else:
        delete_score_factor(event_candidate, "contaminant_score")

    logger.info(
        "BBH vetting: agn=%.2f (catalog=%.2f wise=%s var=%s) flare=%s contaminant=%s",
        agn_score, catalog_agn, wise_agn, var_agn,
        locals().get("agn_flare_score"), min(available) if available else None,
    )
