"""
The "pipeline" to vet candidate counterparts to nonlocalized events based on
their resemblance to kilonovae-in-supernovae.
"""

import logging
from typing import Optional
from astropy.time import Time, TimeDelta
from astropy import units as u
import numpy as np

from .scoring import (
    update_score_factor,
    delete_score_factor,
    clean_host_df,
    host_distance_match,
    get_distance_score,
    skymap_association,
    _localization_from_name,
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
from tom_nonlocalizedevents.models import (
    EventCandidate,
    NonLocalizedEvent,
    EventSequence,
)

logger = logging.getLogger(__name__)

PARAM_RANGES = dict(
    lum_max=[5e41 * u.erg / u.s, 1e44 * u.erg / u.s],
    peak_time=[0, 35],
    decay_rate=[-0.1, 2.0],
    max_predets=3,
    t_pre=-1.0,
    t_post=np.inf,
    max_decay_fit_time=100,
    phot_score_snr_min=5,
)


def vet_kn_in_sn(
    target_id: int,
    nonlocalized_event_name: Optional[str] = None,
    param_ranges: dict = PARAM_RANGES,
):
    logger.info("Running KN-in-SN vetting")

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

    # record which localization these NLE-dependent scores were computed
    # against. Both the skymap score above and the distance score below are
    # only meaningful for one particular skymap -- a later one moves the
    # credible region and the distance at this target's healpix -- and until
    # now nothing stored alongside the scores said which skymap that was. See
    # the TODO in custom_code.alertstream_handlers about acting on this.
    localization = _localization_from_name(nonlocalized_event_name, max_time=max_time)
    update_score_factor(event_candidate, "localization_id", localization.id)
    if skymap_score < 1e-2:
        return

    ## get dataframes of potential hosts / AGN
    host_df, agn_df, keep_vetting = vet_basic(event_candidate.target.id)
    if not keep_vetting:
        # a point source or minor planet match already zeroes this candidate's
        # score, so the slower checks below cannot change its ranking
        return

    # some cleanup
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

        # choose the maximum score
        host_score, host_name, host_catalog = get_distance_score(
            host_df, target_id, nonlocalized_event_name
        )
        update_score_factor(event_candidate, "host_distance_score", host_score)
        update_score_factor(event_candidate, "host_name", host_name)
        update_score_factor(event_candidate, "host_catalog", host_catalog)

    else:
        # if no target redshift is known and no hosts are found, we don't want
        # to bias the final score (host may just be too far)
        host_score = 1

        # and we should also clear out any existing scores / host names for it
        delete_score_factor(event_candidate, "host_distance_score")
        delete_score_factor(event_candidate, "host_name")
        delete_score_factor(event_candidate, "host_catalog")

    ## AGN scoring
    if len(agn_df) != 0:
        agn_assoc_score = 0.1  # association with an AGN is bad
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