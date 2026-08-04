"""
Asynchronous tasks for (1) querying public services that takes a long time,
(2) vetting all candidates, (3) associating targets with NLEs
"""

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
import numpy as np

from django_tasks import task
from django.conf import settings

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
    
    
def _grid_offset_report(event_id: str, target_ids: dict, max_frac_offset: float):
    """Which candidates sit too far from every rung of the grid ladder.

    Returns ``(offenders, wanted_distances)``: a ``{target_name: (dist_mpc,
    offset)}`` mapping, and the distinct distances (rounded, so nearby
    candidates share one rung) a new grid would be needed at.

    A grid is generated at one luminosity distance, and the k-correction and
    time dilation at that redshift change the *shape* of the simulated
    magnitude distribution per band and epoch -- see the derivation in
    ``KilonovaScorer/generate_ladder.py``. Scoring a 3 Gpc candidate against an
    800 Mpc rung is therefore not a small error that washes out; it is the
    wrong distribution.
    """
    from .KilonovaScorer.grids import available_grids
    from .candidate_photometry import get_candidate_distance

    grids = available_grids()
    rungs = (
        [d for d in grids["distance_mpc"].tolist() if d == d] if len(grids) else []
    )
    if not rungs:
        return {}, []

    offenders = {}
    for name, target_id in target_ids.items():
        try:
            dist_mpc, _ = get_candidate_distance(target_id, event_id)
        except Exception:  # noqa: BLE001 - a distance failure is score_candidate's to report
            continue
        if not np.isfinite(dist_mpc) or dist_mpc <= 0:
            continue
        offset = min(abs(rung - dist_mpc) for rung in rungs) / dist_mpc
        if offset > max_frac_offset:
            offenders[name] = (float(dist_mpc), float(offset))

    # one rung per 100 Mpc bucket: candidates 20 Mpc apart do not each deserve
    # their own 30-minute, 4 GB grid
    wanted = sorted({round(d / 100.0) * 100.0 for d, _ in offenders.values()})
    return offenders, [d for d in wanted if d > 0]


@task(queue_name="kilonova_grids", priority=settings.PRIORITY_LOW)
def async_generate_grid_rung(distance_mpc: float, nle_id: int | None = None) -> None:
    """Generate one simulation-grid rung, then re-score the event if given one.

    Shells out to the ``kn-sim`` interpreter: grid generation needs redback /
    bilby / lalsuite, which are deliberately absent from the environment this
    worker runs in. ~30 minutes and ~4 GB of disk per rung.
    """
    import subprocess

    from .phot_method import get_kilonova_params, set_kilonova_status

    interpreter = getattr(settings, "KN_SIM_PYTHON", "")
    if not interpreter or not os.path.exists(interpreter):
        logger.error(
            "Cannot generate a grid rung: KN_SIM_PYTHON is unset or missing (%r). "
            "Set it in settings_local.py to the kn-sim environment's python.",
            interpreter,
        )
        set_kilonova_status(
            state="error",
            nle_id=nle_id,
            message=(
                "Cannot generate a simulation grid: KN_SIM_PYTHON is not "
                "configured. Set it in settings_local.py."
            ),
        )
        return

    script = os.path.join(os.path.dirname(__file__), "KilonovaScorer", "generate_rung.py")
    logger.info("Generating a %.0f Mpc grid rung via %s", distance_mpc, interpreter)
    set_kilonova_status(
        state="running",
        nle_id=nle_id,
        message=(
            f"Generating a {distance_mpc:.0f} Mpc simulation grid "
            "(~30 min, ~4 GB) before scoring."
        ),
    )

    # generation is memory-hungry in its own right, so it queues behind any
    # scoring run rather than adding to it
    with _heavy_job_lock(f"grid rung {distance_mpc:.0f} Mpc"):
        proc = subprocess.run(
            [interpreter, "-u", script, "--distance", str(float(distance_mpc))],
            capture_output=True,
            text=True,
        )
    if proc.returncode != 0:
        logger.error(
            "Grid rung at %.0f Mpc failed (exit %d):\n%s",
            distance_mpc, proc.returncode, proc.stderr[-2000:],
        )
        set_kilonova_status(
            state="error",
            nle_id=nle_id,
            message=f"Generating the {distance_mpc:.0f} Mpc grid failed: {proc.stderr.strip()[-300:]}",
        )
        return

    logger.info("Grid rung at %.0f Mpc done:\n%s", distance_mpc, proc.stdout[-1000:])

    # the ladder changed, so cached grid inventories are stale
    from .KilonovaScorer.grids import clear_cache

    clear_cache()

    if nle_id is not None:
        # now that the rung exists, score the event against it
        async_kilonova_score.enqueue(nle_id=nle_id, params=get_kilonova_params(nle_id))


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
    # score_event_by_distance, not score_event: it groups candidates by the grid
    # rung their distance selects, so each multi-GB grid is read once for the
    # whole event instead of once per distinct band set (101 loads on S251112cm)
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

    # --- grid ladder coverage ---------------------------------------------
    # Only meaningful when the grid is chosen per candidate; an explicitly
    # chosen grid is the user overriding this decision on purpose.
    skip_names = {}
    if not grid_path:
        action = params.get("grid_offset_action", "score")
        max_offset = params.get("max_grid_offset")
        if action != "score" and max_offset is not None:
            offenders, wanted = _grid_offset_report(
                nle.event_id,
                {name: ec.target_id for name, ec in candidates.items()},
                float(max_offset),
            )
            if offenders:
                logger.info(
                    "KilonovaSCORER: %d candidate(s) further than %.0f%% from every "
                    "rung; nearest-rung distances wanted: %s",
                    len(offenders), float(max_offset) * 100, wanted,
                )
            if offenders and action == "generate":
                # generate the missing rungs, then re-enter scoring; stop here
                # rather than scoring half the event against the wrong grid
                for distance in wanted:
                    async_generate_grid_rung.enqueue(
                        distance_mpc=distance,
                        # only the last one needs to re-trigger scoring, but
                        # enqueueing per rung keeps this restartable
                        nle_id=nle.id if distance == wanted[-1] else None,
                    )
                set_kilonova_status(
                    state="running",
                    nle_id=nle.id,
                    event_id=nle.event_id,
                    message=(
                        f"{len(offenders)} candidate(s) of {nle.event_id} are further "
                        f"than {float(max_offset):.0%} from every simulation grid. "
                        f"Generating {len(wanted)} new rung(s) at "
                        f"{', '.join(f'{d:.0f}' for d in wanted)} Mpc "
                        "(~30 min each) before scoring."
                    ),
                )
                return
            skip_names = {
                name: (
                    f"no simulation grid within {float(max_offset):.0%} of "
                    f"{dist:.0f} Mpc (nearest is {offset:.0%} off)"
                )
                for name, (dist, offset) in offenders.items()
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

    n_scored = 0
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
        if row.target_name in skip_names:
            # too far from every rung to be scored honestly, and the user asked
            # for those to be left alone rather than scored anyway
            score = None
        if score is not None and np.isfinite(score):
            ScoreFactor.objects.update_or_create(
                event_candidate=ec,
                key=KILONOVA_SCORE_KEY,
                defaults=dict(value=f"{float(score):.6g}"),
            )
            ScoreFactor.objects.filter(
                event_candidate=ec, key=KILONOVA_SKIP_KEY
            ).delete()
            n_scored += 1
        else:
            # a stale score from a previous run with different parameters would
            # be worse than none at all
            ScoreFactor.objects.filter(
                event_candidate=ec, key=KILONOVA_SCORE_KEY
            ).delete()
            reason = skip_names.get(row.target_name) or getattr(row, "skip_reason", None)
            ScoreFactor.objects.update_or_create(
                event_candidate=ec,
                key=KILONOVA_SKIP_KEY,
                # ScoreFactor.value is a CharField(max_length=200)
                defaults=dict(value=str(reason)[:200]),
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