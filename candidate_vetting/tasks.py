"""
Asynchronous tasks for (1) querying public services that takes a long time,
(2) vetting all candidates, (3) associating targets with NLEs
"""

import logging

from django_tasks import task
from django.conf import settings

from .public_catalogs.phot_catalogs import ATLAS_Forced_Phot
from .vet import run_mpc

from custom_code.healpix_utils import get_target_ids_in_prob_credible_region
from trove_targets.models import Target

logger = logging.getLogger(__name__)


## tasks
@task(queue_name="atlas_fphot", priority=settings.PRIORITY_MID)
def async_atlas_query(target_id: int, *args, **kwargs) -> None:
    t = Target.objects.get(id=target_id)
    ATLAS_Forced_Phot("atlas").query(t, token=settings.ATLAS_API_KEY, *args, **kwargs)


@task(queue_name="mpc", priority=settings.PRIORITY_MID)
def async_mpc(target_id: int, *args, **kwargs) -> None:
    run_mpc(target_id, *args, **kwargs)


@task(queue_name="vet_all", priority=settings.PRIORITY_HIGH)
def async_vet(
    target_ids: list,
    nle_event_id: str,
    vetting_mode: str,
    *args, **kwargs
) -> None:
    from .config import (
        FORM_CHOICE_FUNC_MAP,
    )  # import within function to avoid circular import error
    if vetting_mode == "basic":
        for ti in target_ids:
            FORM_CHOICE_FUNC_MAP[vetting_mode](target_id=ti)
    else:
        for ti in target_ids:
            FORM_CHOICE_FUNC_MAP[vetting_mode](
                target_id=ti, nonlocalized_event_name=nle_event_id
            )

@task(queue_name="associate_targets", priority=settings.PRIORITY_HIGH)
def async_associate_targets(
    target_ids: list,
    nle_event_id: str,
    first_det_tmin: float,
    first_det_tmax: float,
    snr_min: float,
    *args, **kwargs
) -> None:
    pass
    
    
## functions which enqueue tasks
def vet_all_async(eventcandidates, nle, vetting_mode) -> None:
    """
    Asychronously vet according to vetting mode, wraps async_vet for a list of
    eventcandidates
    """
    for ec in eventcandidates:
        async_vet.enqueue(
            target_ids=[ec.target_id],
            nle_event_id=nle.event_id,
            vetting_mode=vetting_mode,
        )

def associate_targets_with_nle(
        nle,
        first_det_tmin,
        first_det_tmax,
        snr_min
) -> None:
    """
    Asychronously attempt to associate targets with an NLE, if they pass
    certain criteria
    """
    # get NLE sequence
    seq = nle.sequences.last()

    # get targets within the probability region
    logger.info("Finding targets in the "+
                f"{settings.SKYMAP_PROB_CONTOURS*100:.0f}% localization "+
                f"region of {nle.event_id}")
    tids = get_target_ids_in_prob_credible_region(
        seq,
        prob=settings.SKYMAP_PROB_CONTOUR)
    targets = [Target(id=tid) for tid in tids]
    for targ in targets:
        print(targ)

    # for targ in targets:
    #     async_associate_targets.enqueue(
    #         target_ids=[targ.id],
    #         nle_event_id=nle.event_id,
    #         first_det_tmin=first_det_tmin,
    #         first_det_tmax=first_det_tmax,
    #         snr_min=snr_min
    #     )
