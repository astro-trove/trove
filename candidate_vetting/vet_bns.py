"""
The "pipeline" to vet BNS nonlocalized events
"""
import logging
from typing import Optional
from astropy.time import Time

from .vet import (
    skymap_association,
    point_source_association,
    host_association,
    host_distance_match,
    update_score_factor,
    update_event_candidate_score
)
from trove_mpc import Transient

from trove_targets.models import Target
from tom_dataproducts.models import ReducedDatum
from tom_nonlocalizedevents.models import EventCandidate, NonLocalizedEvent

logger = logging.getLogger(__name__)

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
        radius = 5*60,
        nkeep = 10
    )
    if len(host_df) != 0:
        # then run the distance comparison for each of these hosts
        host_df = host_distance_match(
            host_df,
            target_id,
            nonlocalized_event_name
        )

        # choose the most likely host and take the score
        host_score = host_df.iloc[0].dist_norm_joint_prob
        update_score_factor(event_candidate, "host_distance_score", host_score)

    else:
        # if no hosts are found we don't want to bias the final score if the host
        # is just too far
        host_score = 1

    # check against forced photometry
    # TO DO!!!

    # compute the overall score
    overall_score = skymap_score*ps_score*mpc_score*host_score
    update_event_candidate_score(event_candidate, int(overall_score*100))

