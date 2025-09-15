"""
Some general functions useful for vetting photometry
"""
from typing import Tuple, Optional, Iterable

from astropy.time import Time
from astropy.stats import akaike_info_criterion_lsq as aic
from astropy import units as u
import numpy as np
from scipy.optimize import curve_fit

from lightcurve_fitting.lightcurve import LC

from django.conf import settings
cosmo = settings.COSMO

def _powerlaw(x, m, b):
    """
    Powerlaw that returns a logarithmic y value
    """
    return np.log10(b*(x**-m))

def _broken_powerlaw(x, m1, m2, y0, x0, s=1):
    """
    Broken powerlaw with smoothing s that returns a logarithmic y value
    """
    linear_y = y0 * ((x/x0)**(-s*m1) + (x/x0)**(-s*m2))**(-1/s)
    return np.log10(linear_y)

def _ssr(model_y, data_y):
    """Sum of the squares of the residuals"""
    residuals = data_y - model_y
    return np.sum(residuals**2)

def _flux_to_lum(flux, lumdist):
    """convert flux to lum. Everything should be astropy quantities"""
    return 4 * np.pi * lumdist**2 * flux

def estimate_max_find_decay_rate(
        dt_days:Iterable[float],
        mag:Iterable[float],
        magerr:Iterable[float],
        max_decay_fit_time:Optional[int]=25
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
        The default is 25 days based on discussion from Rastinejad+2024.

    RETURNS
    -------
    max_time: float
        Days since GW discovery for max to occur
    decay_slope: float
        The slope of the decay from peak between peak and max_decay_fit_time if the
        if the data has a maximum in the mag array. Otherwise this is just the slope
        of the light curve in mag/day.
    """

    # define some useful variables
    pl_nparams = 2 # the degrees of freedom in a powerlaw model (m, b)
    bpl_nparams = 5 # the degrees of freedom in a broken powerlaw model (y0, x0, s, m1, m2)
    
    curve_fit_kwargs = dict(
        xdata = dt_days,
        ydata = mag,
        sigma = magerr,
        absolute_sigma = True,
        maxfev = 5_000,
        gtol = 1e-4
    )
    
    # first fit a regular powerlaw
    try:
        pl_popt, pl_pcov = curve_fit(_powerlaw, **curve_fit_kwargs)
    except RuntimeError:
        pl_popt, pl_pcov = None, None
        
    # then fit a broken powerlaw
    try:
        bpl_popt, bpl_pcov = curve_fit(_broken_powerlaw, **curve_fit_kwargs)
    except RuntimeError:
        bpl_popt, bpl_pcov = None, None

    
    # then calculate the reduced chi2 for each of these outputs
    pl_failed = False
    if pl_popt is not None:
        pl_model_y = _powerlaw(dt_days, *pl_popt)
        pl_ssr = _ssr(pl_model_y, mag)
        pl_aic = aic(pl_ssr, pl_nparams, len(mag))
    else:
        pl_failed = True

    bpl_failed = False
    if bpl_popt is not None:
        bpl_model_y = _broken_powerlaw(dt_days, *bpl_popt)
        bpl_ssr = _ssr(bpl_model_y, mag)
        bpl_aic = aic(bpl_ssr, bpl_nparams, len(mag))
    else:
        bpl_failed = True
    # now we can prefer the model with the lower AIC score
    if (not pl_failed and bpl_failed) or (pl_aic < bpl_aic and not pl_failed):
        model = _powerlaw
        best_fit_params = pl_popt
        decay_rate = -pl_popt[0] # this is the slope, negate it b/c magnitudes
    elif not bpl_failed:
        model = _broken_powerlaw
        best_fit_params = bpl_popt
        decay_rate = -bpl_popt[1] # this is the decay slope, bpl_popt[0] is the rise slope, negate it b/c magnitudes
    else:
        raise RuntimeError("Both a powerlaw and broken powerlaw failed to fit the data!")
        
    # finally, compute the maximum time using a finely spaced array
    # from 0 -> max_decay_fit_time
    xtest = np.linspace(0, max_decay_fit_time, 100*max_decay_fit_time)
    ytest = model(xtest, *best_fit_params)
    max_time = xtest[np.argmax(ytest)]
    return model, best_fit_params, max_time, decay_rate
    
def compute_peak_lum(
        mag:Iterable[float],
        magerr:Iterable[float],
        filters:Iterable[str],
        lumdist:u.Quantity,
        consider_err:bool=True
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

    lc = LC([mag, magerr, filters], names=['mag', 'dmag', 'filter'])
    lc.calcFlux()
    
    # find the max of the light curve
    fluxmax_idx = np.argmax(lc["flux"])
    fluxmax = lc["flux"][fluxmax_idx]
    if consider_err:
        # turn the flux max into the 3 sigma upper error
        fluxmax += 3*lc["dflux"][fluxmax_idx]
    filtermax = lc["filter"][fluxmax_idx]
    
    # now convert that flux to a luminosity
    fluxmax = fluxmax*u.Unit("W/m^2/Hz")
    lummax = _flux_to_lum(fluxmax, lumdist).to("erg/s/Hz")

    # then we need to multiply be the effective frequency of the filter
    nu_lummax = (filtermax.freq_eff * lummax).to("erg/s")
    return nu_lummax
    
