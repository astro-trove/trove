"""
Asynchronous tasks for (1) querying public services that takes a long time,
(2) vetting all candidates, (3) associating targets with NLEs
"""

import logging
import time
from datetime import datetime, timedelta
import numpy as np

from django_tasks import task
from django.conf import settings
from django.utils import timezone

from candidate_vetting.public_catalogs.phot_catalogs import ATLAS_Forced_Phot
from candidate_vetting.vet import run_mpc

from custom_code.healpix_utils import (
    get_target_ids_in_prob_credible_region,
    create_candidates_from_targets,
    )
from trove_targets.models import Target

from tom_nonlocalizedevents.models import NonLocalizedEvent

logger = logging.getLogger(__name__)

from scoring.phot_method import PHOT_METHOD_KILONOVA, get_phot_method

#: Queue the per-candidate "Vet All" tasks are enqueued on. The candidate list
#: counts the tasks still sitting on it to show how far a run has got, so the
#: name is shared rather than repeated.
VET_ALL_QUEUE_NAME = "vet_all"


## tasks
@task(queue_name="atlas_fphot", priority=settings.PRIORITY_MID)
def async_atlas_query(target_id: int, *args, **kwargs) -> None:
    t = Target.objects.get(id=target_id)
    ATLAS_Forced_Phot("atlas").query(t, token=settings.ATLAS_API_KEY, *args, **kwargs)


@task(queue_name="mpc", priority=settings.PRIORITY_MID)
def async_mpc(target_id: int, *args, **kwargs) -> None:
    run_mpc(target_id, *args, **kwargs)


@task(queue_name=VET_ALL_QUEUE_NAME, priority=settings.PRIORITY_HIGH)
def async_vet(
    target_ids: list,
    nle_event_id: str,
    vetting_mode: str,
    phot_method: str = None,
    *args, **kwargs
) -> None:
    """
    `phot_method` is the scorer the user had selected when they pressed the
    button, captured then and carried here.

    It is NOT read from the toggle inside this task. The toggle lives in
    `cache`, which is a per-process FileBasedCache, and workers run in their own
    containers -- so a worker reading it would get the default rather than the
    user's choice, and would quietly score with the other scorer while the page
    reported this one. Passing it makes the task say which scorer it ran, and a
    toggle flipped mid-run cannot change the answer halfway through the event.

    `run_started` and `started_by` arrive in **kwargs for the same reason and
    are likewise not inputs to the vetting: they let the candidate list tell one
    run from the next and report progress against it.
    """
    from .config import (
        FORM_CHOICE_FUNC_MAP,
    )  # import within function to avoid circular import error

    # Only the KN pipeline has a choice of photometry scorer; the others take no
    # such argument and must not be handed one.
    extra = {"phot_method": phot_method} if vetting_mode == "KN" and phot_method else {}

    if vetting_mode == "basic":
        for ti in target_ids:
            FORM_CHOICE_FUNC_MAP[vetting_mode](target_id=ti)
    else:
        for ti in target_ids:
            FORM_CHOICE_FUNC_MAP[vetting_mode](
                target_id=ti, nonlocalized_event_name=nle_event_id, **extra
            )

@task(queue_name="associate_targets", priority=settings.PRIORITY_HIGH)
def async_associate_targets_nle(
    target_ids: list,
    nle_id: str,
    first_det_tmin: float,
    first_det_tmax: float,
    snr_min: float,
    *args, **kwargs
) -> None:

    nle = NonLocalizedEvent.objects.filter(id=nle_id)[0]
    seq = nle.sequences.last()
    try:
        nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f")
    for ti in target_ids:
        target = Target.objects.filter(id=ti)[0]
        logger.info(f"\n{target.name}")

        # if excluding based on first detection's SNR...
        # ...is first detection >= SNR minimum?
        if snr_min > 0:
            first_det = target.reduceddatum_set.filter(
                data_type="photometry",
                value__magnitude__isnull=False,
                value__error__isnull=False,
                value__error__lte=2.5/np.log(10)/snr_min).order_by("timestamp").first()
            if first_det:
                logger.info(f"First non-limit, SNR >= {snr_min} detection: {first_det.timestamp}")
            else:
                logger.info(f"No SNR >= {snr_min} detections, skipping")
                return
        else:
            first_det = target.reduceddatum_set.filter(
                data_type="photometry",
                value__magnitude__isnull=False,
                value__error__isnull=False).order_by("timestamp").first()

        # is first detection within prescribed time window?
        if not(
            first_det.timestamp > nle_time + timedelta(days=first_det_tmin)  and
            first_det.timestamp < nle_time + timedelta(days=first_det_tmax)
            ):
            logger.info("First detection is outside of "+
                        f"{nle_time + timedelta(days=first_det_tmin)} to "+
                        f"{nle_time + timedelta(days=first_det_tmax)} "+
                        "time window")
            return
        else:
            logger.info("First detection is within "+
                        f"{nle_time + timedelta(days=first_det_tmin)} to "+
                        f"{nle_time + timedelta(days=first_det_tmax)} "+
                        "time window")
            # attempt to create the eventcandidate
            new_cand = create_candidates_from_targets(
                seq,
                prob=settings.SKYMAP_PROB_CONTOUR,
                target_ids=[target.id])
            if len(new_cand):
                logger.info("New eventcandidate created")
            else:
                logger.info("Eventcandidate already exists")
    
    
## functions which enqueue tasks
def vet_all_async(eventcandidates, nle, vetting_mode, phot_method=None,
                  started_by=None) -> None:
    """
    Asychronously vet according to vetting mode, wraps async_vet for a list of
    eventcandidates

    Under KilonovaSCORER the candidates are enqueued in DISTANCE ORDER, because
    the scorer now picks the simulation rung nearest each candidate's own
    distance. The grid cache holds a handful of frames and a rung costs 25-60 s
    to read out of Postgres, so an arbitrary ordering evicts and re-reads the
    same rungs over and over -- with 19 rungs and a 4-entry cache that is
    potentially hundreds of reads across an event. Sorted, each rung is read
    once and every candidate at that distance is scored behind it.

    The sort costs one distance lookup per candidate here, which the scoring
    task then repeats. That duplication is deliberate: it is seconds against
    the tens of minutes of grid reads it removes, and it keeps `async_vet`
    self-contained rather than having to trust a distance passed in from
    outside.

    Every task of one run also carries the same `run_started` stamp, which is
    what lets the candidate list tell one run from the next and report progress
    against it. It rides on the task rows rather than in a cache deliberately:
    a Vet All is a shared action whose results everyone sees, and the cache is
    per-process, so it would show different users different things.
    """
    ecs = list(eventcandidates)
    run_started = timezone.now().isoformat()

    # The caller (the vetting form) picks the scorer. The site-wide toggle is
    # only a fallback for callers that do not, and is read here in the web
    # process -- never in the worker, which cannot see it.
    phot_method = phot_method or get_phot_method()

    if vetting_mode == "KN" and phot_method == PHOT_METHOD_KILONOVA:
        from scoring.scoring import get_eventcandidate_default_distance

        def _dist(ec):
            try:
                d, _ = get_eventcandidate_default_distance(ec.target_id, nle.event_id)
                return float(d) if np.isfinite(d) and d > 0 else float("inf")
            except Exception:  # noqa: BLE001 - unknown distance sorts last
                # These fail in the task too, so their position is irrelevant to
                # cache behaviour; last keeps them out of the grouped runs.
                return float("inf")

        t0 = time.time()
        ecs.sort(key=_dist)
        logger.info("ordered %d candidate(s) by distance for grid grouping in %.1fs",
                    len(ecs), time.time() - t0)

    for ec in ecs:
        async_vet.enqueue(
            target_ids=[ec.target_id],
            nle_event_id=nle.event_id,
            vetting_mode=vetting_mode,
            phot_method=phot_method,
            run_started=run_started,
            started_by=started_by,
        )

def associate_targets_with_nle_async(
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
    try:
        nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f")

    # get targets within the localization region
    logger.info("Getting targets in the "+
                f"{settings.SKYMAP_PROB_CONTOUR*100:.0f}% localization "+
                f"region of {nle.event_id}")
    tids = get_target_ids_in_prob_credible_region(
        seq,
        prob=settings.SKYMAP_PROB_CONTOUR,
        tdelta=first_det_tmin)
    tids_ls = list(tids)
    tids_ls = [tid[0] for tid in tids_ls]
    targets = Target.objects.filter(id__in=tids_ls,
                                    created__gte=nle_time+timedelta(first_det_tmin)).order_by("name")
    logger.info(f"Found {len(targets)} targets")

    # associate, asyncronously!
    async_associate_targets_nle.enqueue(
        target_ids=[target.id for target in targets],
        nle_id=nle.id,
        first_det_tmin=first_det_tmin,
        first_det_tmax=first_det_tmax,
        snr_min=snr_min
    )