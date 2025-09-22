"""
The "pipeline" to vet BNS nonlocalized events
"""
import logging
from typing import Optional
from astropy.time import Time
from astropy import units as u
import pandas as pd
import numpy as np

from .vet import (
    skymap_association,
    point_source_association,
    host_association,
    host_distance_match,
    update_score_factor,
    update_event_candidate_score,
    _distance_at_healpix
)
from .vet_phot import (
    compute_peak_lum,
    estimate_max_find_decay_rate,
    standardize_filter_names,
    _get_post_disc_phot,
    _get_pre_disc_phot,
    get_predetection_stats
)
from trove_mpc import Transient

from trove_targets.models import Target
from tom_dataproducts.models import ReducedDatum
from tom_nonlocalizedevents.models import (
    EventCandidate,
    NonLocalizedEvent,
    EventSequence
)

logger = logging.getLogger(__name__)

PARAM_RANGES = dict(
    lum_max = [0*u.erg/u.s, 1e43*u.erg/u.s],
    peak_time = [0, 4],
    decay_rate = [-np.inf, -0.1],
    max_predets = 3
)
FILTER_PRIORITY_ORDER = ["r", "g", "V", "R", "G"]
PHOT_SCORE_MIN = 0.1
PREDETECTION_SNR_THRESHOLD = 5 # require a S/N of 5 for a predetection to be considered real

def _score_phot(allphot, target, nonlocalized_event, filt=None):
    phot = allphot[~allphot.upperlimit]
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
    dist, dist_err = _distance_at_healpix(nonlocalized_event.event_id, target.id)
    lum = compute_peak_lum(phot.mag, phot.magerr, phot["filter"].tolist(), dist*u.Mpc)

    phot_score = 1
    if lum < PARAM_RANGES["lum_max"][0] or lum > PARAM_RANGES["lum_max"][1]:
        phot_score *= PHOT_SCORE_MIN

    # then we can only do the next stuff if there is more than one photometry point
    # at this filter
    if len(phot) > 1: # has to be at least 2 points to fit the powerlaw        
        # find the maximum and decay rate
        _model,_best_fit_params,max_time,decay_rate = estimate_max_find_decay_rate(
            phot.dt,
            phot.mag,
            phot.magerr
        )
        
        # check if these are within the appropriate ranges
        if max_time < PARAM_RANGES["peak_time"][0] or max_time > PARAM_RANGES["peak_time"][1]:
            phot_score *= PHOT_SCORE_MIN
        
        if decay_rate > PARAM_RANGES["decay_rate"][1]: # if it's greater than the max
            phot_score *= PHOT_SCORE_MIN

        return phot_score, lum, max_time, decay_rate, _model, _best_fit_params
    
    return phot_score, lum, None, None, None, None

def vet_bns(target_id:int, nonlocalized_event_name:Optional[str]=None):

    # get the correct EventCandidate object for this target_id and nonlocalized event
    nonlocalized_event = NonLocalizedEvent.objects.get(
        event_id=nonlocalized_event_name
    )
    event_candidate = EventCandidate.objects.get(
        nonlocalizedevent_id = nonlocalized_event.id,
        target_id = target_id
    )
    
    # check skymap association
    skymap_score = skymap_association(nonlocalized_event_name, target_id)
    update_score_factor(event_candidate, "skymap_score", skymap_score)
    if skymap_score < 1e-2:
        update_event_candidate_score(event_candidate, 0)
        return 

    # run the point source checker
    ps_score = point_source_association(target_id)
    update_score_factor(event_candidate, "ps_score", ps_score)
    if ps_score == 0:
        update_event_candidate_score(event_candidate, 0)
        return
    
    # run the minor planet checker
    target = Target.objects.filter(id=target_id)[0]
    phot = ReducedDatum.objects.filter(
        target_id=target_id,
        data_type="photometry",
        value__magnitude__isnull=False
    )
    if phot.exists():
        latest_det = phot.latest()
        date = Time(latest_det.timestamp).mjd
        t = Transient(target.ra, target.dec)
        mpc_match = t.minor_planet_match(date)
    else:
        logger.warn("This candidate has no photometry, skipping MPC!")
        mpc_match = None

    if mpc_match is not None:
        # update the score factor information
        update_score_factor(event_candidate, "mpc_match_name", mpc_match.match_name)
        update_score_factor(event_candidate, "mpc_match_sep", mpc_match.distance)
        update_score_factor(event_candidate, "mpc_match_date", latest_det.timestamp)

        # update the event candidate overall score to 0
        update_event_candidate_score(event_candidate, 0)

        return
    
    mpc_score = 1
    
    # do the Pcc analysis and find a host
    host_df = host_association(
        target_id,
        radius = 5*60
    )
    if len(host_df) != 0:
        # then run the distance comparison for each of these hosts
        host_df = host_distance_match(
            host_df,
            target_id,
            nonlocalized_event_name
        )

        # choose the maximum score out of the top 10 best hosts
        host_score = host_df.dist_norm_joint_prob.max()
        update_score_factor(event_candidate, "host_distance_score", host_score)

    else:
        # if no hosts are found we don't want to bias the final score if the host
        # is just too far
        host_score = 1

    # Photometry scoring
    allphot = _get_post_disc_phot(target_id=target_id, nonlocalized_event=nonlocalized_event)
    phot_score, lum, max_time, decay_rate, _, _ = _score_phot(
        allphot=allphot,
        target = target,
        nonlocalized_event = nonlocalized_event,
        filt = ["g", "r", "i", "o", "c"] # use the common optical filters
    )
    if lum is not None:
        update_score_factor(event_candidate, "phot_peak_lum", lum.value)
    if max_time is not None:
        update_score_factor(event_candidate, "phot_peak_time", max_time)
    if decay_rate is not None:
        update_score_factor(event_candidate, "phot_decay_rate", decay_rate)

    # check for *reliable* predetections
    prephot = _get_pre_disc_phot(target.id, nonlocalized_event)
    predet_score = 1
    if len(prephot):
        n_predets, _ = get_predetection_stats(
            prephot.mjd.values,
            prephot.magerr.values,
            window_size=5, # +/-5 day window size
            det_snr_thresh=PREDETECTION_SNR_THRESHOLD
        )
        if any(v >= PARAM_RANGES["max_predets"] for v in n_predets):
            predet_score = PHOT_SCORE_MIN
            update_score_factor(event_candidate, "predetection_score", predet_score)

    # compute the overall score
    overall_score = skymap_score*ps_score*mpc_score*host_score*phot_score*predet_score
    update_event_candidate_score(event_candidate, int(overall_score*100))

