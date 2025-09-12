"""
Some general functions useful for vetting photometry
"""
from typing import Tuple, Optional, Iterable

from astropy.time import Time
from astropy.stats import akaike_info_criterion_lsq as aic
import numpy as np
from scipy.optimize import curve_fit

def _powerlaw(x, m, b):
    """
    linear function 
    """
    return m*np.log10(x) + b

def _broken_powerlaw(x, m1, m2, y0, x0, s=1):
    """
    Broken powerlaw with smoothing s
    """
    return y0 * ((x/x0)**(-s*m1) + (x/x0)**(-s*m2))**(-1/s)    

def _ssr(model_y, data_y):
    """Sum of the squares of the residuals"""
    residuals = data_y - model_y
    return np.sum(residuals**2)

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
        absolute_sigma = True
    )
    
    # first fit a regular powerlaw
    pl_popt, pl_pcov = curve_fit(_powerlaw, **curve_fit_kwargs)

    # then fit a broken powerlaw
    bpl_popt, bpl_pcov = curve_fit(_broken_powerlaw, **curve_fit_kwargs)

    # then calculate the reduced chi2 for each of these outputs
    pl_model_y = _powerlaw(dt_days, *pl_popt)
    bpl_model_y = _broken_powerlaw(dt_days, *bpl_popt)

    pl_ssr = _ssr(pl_model_y, mag)
    bpl_ssr = _ssr(bpl_model_y, mag)

    pl_aic = aic(pl_ssr, pl_nparams, len(mag))
    bpl_aic = aic(bpl_ssr, bpl_nparams, len(mag))

    # now we can prefer the model with the lower AIC score
    if pl_aic < bpl_aic:
        model = _powerlaw
        best_fit_params = pl_popt
        decay_rate = pl_popt[0] # this is the slope
    else:
        model = _broken_powerlaw
        best_fit_params = bpl_popt
        decay_rate = bpl_popt[1] # this is the decay slope, bpl_popt[0] is the rise slope

    # finally, compute the maximum time using a finely spaced array
    # from 0 -> max_decay_fit_time
    xtest = np.linspace(0, max_decay_fit_time, 100*max_decay_fit_time)
    ytest = model(xtest, *best_fit_params)
    max_time = xtest[np.argmax(ytest)]
    return model, best_fit_params, max_time, decay_rate
    
    
