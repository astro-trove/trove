"""
The "pipeline" to vet candidate counterparts to nonlocalized events based on
their resemblance to super-kilonovae.
"""

import logging
from typing import Optional
from astropy.time import Time, TimeDelta
from astropy import units as u
import numpy as np

from .scoring import (
    update_score_factor,
    delete_score_factor,
    host_distance_match,
    get_distance_score,
    skymap_association,
)
from .vet_basic import vet_basic
from .vet_phot import (
    _get_post_disc_phot,
    _score_phot,
    _get_pre_disc_phot,
    get_predetection_stats,
    PHOT_SCORE_MIN,
    PREDETECTION_SNR_THRESHOLD,
)

from trove_targets.models import Target
from tom_dataproducts.models import ReducedDatum
from tom_nonlocalizedevents.models import (
    EventCandidate,
    NonLocalizedEvent,
    EventSequence,
)

logger = logging.getLogger(__name__)

PARAM_RANGES = dict(
    lum_max=[1e41 * u.erg / u.s, 1e43 * u.erg / u.s],
    peak_time=[10, 70],
    decay_rate=[-np.inf, np.inf],
    max_predets=3,
    t_pre=-1.0,
    t_post=np.inf,
    max_decay_fit_time=100,
    phot_score_snr_min=5,
)


def vet_super_kn(
    target_id: int,
    nonlocalized_event_name: Optional[str] = None,
    param_ranges: dict = PARAM_RANGES,
):
    print(target_id)
    logger.info("Running super-KN vetting")

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
    if skymap_score < 1e-2:
        return

    ## compute the basic scores, return dataframes of potential hosts / AGN
    host_df, agn_df = vet_basic(event_candidate.target.id)

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

        # choose the maximum score
        host_score, host_name = get_distance_score(
            host_df, target_id, nonlocalized_event_name
        )
        update_score_factor(event_candidate, "host_distance_score", host_score)
        update_score_factor(event_candidate, "host_name", host_name)

    else:
        # if no target redshift is known and no hosts are found, we don't want
        # to bias the final score (host may just be too far)
        host_score = 1

        # and we should also clear out any existing scores / host names for it
        delete_score_factor(event_candidate, "host_distance_score")
        delete_score_factor(event_candidate, "host_name")

    ## AGN scoring
    if len(agn_df) != 0:
        agn_assoc_score = 0  # association with an AGN is bad
    else:
        agn_assoc_score = 1
    agn_score = agn_assoc_score  # don't bother with 3D AGN scoring, for now
    update_score_factor(event_candidate, "agn_score", agn_score)

    ## photometry scoring
    allphot = _get_post_disc_phot(
        target_id=target_id,
        nonlocalized_event=nonlocalized_event,
        t_post=param_ranges["t_post"],
    )
    phot_score, lum, max_time, decay_rate, _, _ = _score_phot(
        allphot=allphot,
        target=target,
        nonlocalized_event=nonlocalized_event,
        param_ranges=param_ranges,
        filt=[
            "g",
            "r",
            "i",
            "z",
            "F129",
            "F158",
            "o",
            "c",
        ],  # common optical filters + some Roman filters + ATLAS o,c
    )
    if lum is not None:
        update_score_factor(event_candidate, "phot_peak_lum", lum.value)
    else:
        delete_score_factor(event_candidate, "phot_peak_lum")

    if max_time is not None:
        update_score_factor(event_candidate, "phot_peak_time", max_time)
    else:
        delete_score_factor(event_candidate, "phot_peak_time")

    if decay_rate is not None:
        update_score_factor(event_candidate, "phot_decay_rate", decay_rate)
    else:
        delete_score_factor(event_candidate, "phot_decay_rate")

    # check for *reliable* predetections before time t_pre
    prephot = _get_pre_disc_phot(
        target_id=target.id,
        nonlocalized_event=nonlocalized_event,
        t_pre=param_ranges["t_pre"],
    )
    predet_score = 1
    if prephot is not None and len(prephot):
        try:
            n_predets, _ = get_predetection_stats(
                prephot.mjd.values,
                prephot.magerr.values,
                window_size=5,  # +/-5 day window size
                det_snr_thresh=PREDETECTION_SNR_THRESHOLD,
            )
        except ValueError:
            n_predets = [
                0
            ]  # this ValueError only happens when there aren't any predets
        if any(v >= param_ranges["max_predets"] for v in n_predets):
            predet_score = PHOT_SCORE_MIN
            update_score_factor(event_candidate, "predetection_score", predet_score)
        else:
            delete_score_factor(event_candidate, "predetection_score")
