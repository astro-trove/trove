"""
Asynchronous tasks for (1) querying public services that takes a long time,
(2) vetting all candidates, (3) associating targets with NLEs
"""

import logging
import time
from contextlib import contextmanager
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


#: Advisory lock file serialising work that loads a simulation grid. Deliberately
#: a well-known path rather than a private one: any out-of-process tooling that
#: also loads a grid should take it too, so a run started from the web UI and one
#: started from a shell cannot collide. (Local diagnostic harnesses do this from
#: bash with flock on the same path.)
HEAVY_JOB_LOCK = "/tmp/trove_heavy_job.lock"


@contextmanager
def _heavy_job_lock(what: str = "job"):
    """Serialise work that loads a simulation grid.

    A band-scoped grid load peaks at 9-13 GB, and the WSL VM has ~15 GB. Two
    overlapping loads exhaust it and the OOM reaper takes an arbitrary victim --
    which has included the VS Code server. The scheduled jobs
    (``run_job*.sh``) take this same file, so a run started from the web UI and
    one started by the Task Scheduler cannot collide.

    Blocks rather than failing: the point is that the work still happens, just
    not at the same time as other heavy work.
    """
    import fcntl

    with open(HEAVY_JOB_LOCK, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logger.info("%s: waiting for the heavy-job lock (%s)", what, HEAVY_JOB_LOCK)
            fcntl.flock(handle, fcntl.LOCK_EX)
            logger.info("%s: acquired the heavy-job lock", what)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


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
    
    
@task(queue_name="kilonova_scoring", priority=settings.PRIORITY_HIGH)
def async_kilonova_score(nle_id: int, params: dict) -> None:
    """Re-score every candidate of an NLE with KilonovaSCORER.

    Runs :func:`scoring.kilonova_scoring.score_event` and stores each result as
    a ``ScoreFactor`` row, so the score survives the 5-minute scored-candidate
    cache and is available to the candidate list, the target page and the API.
    Candidates that could not be scored get their ``skip_reason`` recorded
    instead of a score -- nothing disappears silently.

    This is a task rather than an inline call because a whole event is minutes
    to hours of work: a simulation grid is several GB, and every epoch of every
    candidate is scored against a KDE built from it.
    """
    # imported here, not at module level: this pulls in pandas, pyarrow and the
    # vendored scorer, which nothing else in the task module needs
    # score_event_by_distance, not score_event: it groups candidates by the bands
    # they were observed in, so the multi-GB grid is read once per band group for
    # the whole event instead of once per candidate (101 loads on S251112cm)
    from .kilonova_scoring import score_event_by_distance as score_event
    from .models import ScoreFactor
    from .phot_method import (
        KILONOVA_SCORE_KEY,
        KILONOVA_SKIP_KEY,
        invalidate_scored_candidates,
        score_event_kwargs,
        set_kilonova_status,
    )
    from tom_nonlocalizedevents.models import EventCandidate

    nle = NonLocalizedEvent.objects.get(id=nle_id)
    params = params or {}
    grid_path = params.get("grid_path") or None
    kwargs = score_event_kwargs(params)

    # map target name -> candidate once; score_event returns one row per
    # candidate of this event, keyed by target name
    candidates = {
        ec.target.name: ec
        for ec in EventCandidate.objects.filter(
            nonlocalizedevent_id=nle.id
        ).select_related("target")
    }

    set_kilonova_status(
        state="running",
        nle_id=nle.id,
        event_id=nle.event_id,
        message=f"Scoring candidates of {nle.event_id} with KilonovaSCORER...",
    )
    logger.info("KilonovaSCORER: scoring %s with %s", nle.event_id, kwargs)

    try:
        # holds the lock for the whole run: releasing between rungs would let a
        # scheduled job load its grid while this one still holds two
        with _heavy_job_lock(f"KilonovaSCORER {nle.event_id}"):
            table = score_event(
                nle.event_id,
                grid_path=grid_path,
                keep_frames=False,
                progress=True,
                **kwargs,
            )
    except Exception as exc:  # noqa: BLE001 - surface the failure in the UI
        logger.exception("KilonovaSCORER run failed for %s", nle.event_id)
        set_kilonova_status(
            state="error",
            nle_id=nle.id,
            event_id=nle.event_id,
            message=f"{type(exc).__name__}: {exc}",
        )
        return

    t_persist = time.perf_counter()
    scored_rows, skipped_rows = [], []
    for row in table.itertuples():
        ec = candidates.get(row.target_name)
        if ec is None:
            continue
        # an all-unscoreable event leaves `score` as an object column, so the
        # value is None rather than NaN and cannot go straight to np.isfinite
        try:
            score = float(getattr(row, "score", None))
        except (TypeError, ValueError):
            score = None
        if score is not None and np.isfinite(score):
            scored_rows.append(
                ScoreFactor(
                    event_candidate=ec,
                    key=KILONOVA_SCORE_KEY,
                    value=f"{float(score):.6g}",
                )
            )
        else:
            reason = getattr(row, "skip_reason", None)
            skipped_rows.append(
                ScoreFactor(
                    event_candidate=ec,
                    key=KILONOVA_SKIP_KEY,
                    # ScoreFactor.value is a CharField(max_length=200)
                    value=str(reason)[:200],
                )
            )
    n_scored = len(scored_rows)

    # Same invariant the per-row version enforced: a candidate holds a score or
    # a skip reason, never both. A stale score from a previous run with
    # different parameters is worse than none at all.
    ScoreFactor.objects.filter(
        event_candidate_id__in=[f.event_candidate_id for f in scored_rows],
        key=KILONOVA_SKIP_KEY,
    ).delete()
    ScoreFactor.objects.filter(
        event_candidate_id__in=[f.event_candidate_id for f in skipped_rows],
        key=KILONOVA_SCORE_KEY,
    ).delete()
    # ON CONFLICT against the (event_candidate, key) unique_together, so this is
    # an upsert: existing rows have their value replaced, new ones are inserted.
    for batch in (scored_rows, skipped_rows):
        if batch:
            ScoreFactor.objects.bulk_create(
                batch,
                update_conflicts=True,
                unique_fields=["event_candidate", "key"],
                update_fields=["value"],
                batch_size=500,
            )
    logger.info(
        "KilonovaSCORER: persisted %d score(s) and %d skip(s) in %.1f s",
        len(scored_rows), len(skipped_rows), time.perf_counter() - t_persist,
    )

    logger.info(
        "KilonovaSCORER: scored %d of %d candidates of %s",
        n_scored, len(table), nle.event_id,
    )
    set_kilonova_status(
        state="done",
        nle_id=nle.id,
        event_id=nle.event_id,
        scored=n_scored,
        total=int(len(table)),
        message=f"Scored {n_scored} of {len(table)} candidates of {nle.event_id}.",
        # Recorded for the scoring-settings panel at the foot of the candidate list. Recorded
        # here, at the moment the scores are written, rather than read back from
        # DEFAULT_KILONOVA_PARAMS at render time: the point is to say what
        # produced *these* numbers, and the defaults may have been edited since.
        finished_at=timezone.now().isoformat(),
        params=dict(params),
        grid_path=grid_path or "",
    )

    # the scores just changed, so every cached ranking for them is stale
    invalidate_scored_candidates()


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

def kilonova_score_all_async(nle, params: dict) -> None:
    """Enqueue a KilonovaSCORER re-scoring run for every candidate of ``nle``.

    One task for the whole event, not one per candidate: the simulation grid is
    several GB and is cached in the worker process between candidates, so
    splitting the run would mean re-reading it for each one.
    """
    async_kilonova_score.enqueue(nle_id=nle.id, params=params)


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