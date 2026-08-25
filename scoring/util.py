"""
Some common functions used in multiple places throughout the app
"""

from collections import OrderedDict
from datetime import timedelta
import math
import logging
from astropy.units import Quantity
from django.db import DatabaseError
from django.db.models import Count, FloatField, Max, Min, Q
from django.db.models.functions import Cast
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django_tasks import ResultStatus
from django_tasks.backends.database.models import DBTaskResult
from tom_nonlocalizedevents.models import (
    EventCandidate,
    EventLocalization,
    NonLocalizedEvent,
)
from trove_targets.models import Target
from tom_targets.models import TargetExtra

from custom_code.templatetags.nonlocalizedevent_extras import get_most_likely_class

from candidate_vetting.vet import localization_sequence_from_name

from .vet_phot import PHOT_SCORE_MIN
from .vet_bns import PARAM_RANGES as KN_PARAM_RANGES
from .vet_kn_in_sn import PARAM_RANGES as KN_IN_SN_PARAM_RANGES
from .vet_super_kn import PARAM_RANGES as SUPER_KN_PARAM_RANGES
from .models import ScoreFactor
from .tasks import VET_ALL_QUEUE_NAME, async_vet

import time

logger = logging.getLogger(__name__)

# map imported parameter ranges to transients
TRANSIENTS = ["KN", "KN-in-SN", "super-KN"]

#: The one transient type KilonovaSCORER models. Its simulation grid is a
#: two-component kilonova population, so its factor is only meaningful for
#: "KN" -- "KN-in-SN" and "super-KN" ask a different question of the same
#: photometry and keep TROVE's own factor.
KILONOVA_SCORER_TRANSIENT = "KN"
DICT_TRANSIENTS_PARAM_RANGES = {
    "KN": KN_PARAM_RANGES,
    "KN-in-SN": KN_IN_SN_PARAM_RANGES,
    "super-KN": SUPER_KN_PARAM_RANGES,
}


# default subscore names
#: ScoreFactor key holding KilonovaSCORER's photometry factor, written by
#: `vet_bns` when the site-wide `phot_method` toggle is on KilonovaSCORER.
#: Listed in SUBSCORE_NAMES so it is fetched, and excluded from the generic
#: product below so it is never multiplied in as just another subscore -- it
#: REPLACES the TROVE photometry factor rather than joining it.
KILONOVA_SCORE_KEY = "kilonova_score"

#: Why KilonovaSCORER could not score a candidate, written by `vet_bns` in place
#: of a score. Its PRESENCE is what distinguishes "the scorer ran and failed"
#: from "this candidate has not been vetted yet" -- the two look identical if
#: you only check for a missing score, and only the first is worth flagging.
KILONOVA_SKIP_REASON_KEY = "kilonova_skip_reason"

SUBSCORE_NAMES = [
    "kilonova_score",
    "skymap_score",
    "host_distance_score",
    "ps_score",
    "agn_score",
    "predetection_score",
    "phot_peak_lum",
    "phot_peak_time",
    "phot_decay_rate",
]

# some of the keys in ScoreFactor are really just calculated values
# where the score depends on the type of non-localized event, so we need to
# convert these to scores
VAL_NOT_SCORE_KEYS = {
    "phot_peak_lum": "lum_max",
    "phot_peak_time": "peak_time",
    "phot_decay_rate": "decay_rate",
}

# these should now be stored in a TargetExtra object so the score needs to be
# accessed differently
TARGETEXTRA_KEYS = [
    "ps_score",
    "mpc_match_name",
    "mpc_match_sep",
    "mpc_match_date",
]
MPC_KEYS = [
    "mpc_match_name",
    "mpc_match_sep",
    "mpc_match_date",
]


def _check_phot_val(val, param_ranges, param_range_key):
    val_max = max(param_ranges[param_range_key])
    val_min = min(param_ranges[param_range_key])
    if isinstance(val_min, Quantity):
        val_min = val_min.value
    if isinstance(val_max, Quantity):
        val_max = val_max.value

    if val < val_min or val > val_max:
        # multiply photometry score by PHOT_SCORE_MIN
        return PHOT_SCORE_MIN
    return 1


def get_event_candidate_scores(
        event_candidates,
        dict_transients_param_ranges=DICT_TRANSIENTS_PARAM_RANGES,
        subscore_names=SUBSCORE_NAMES,
        agn_toggle=True,
        include_subscores=False,
        phot_method=None,
):
    """Get the event candidate scores for everything in subscore_names

    event_candidates should be a django queryset of EventCandidate objects

    ``phot_method`` selects which photometry factor the score uses. ``None``
    reads the site-wide toggle. On ``kilonova`` a candidate with a stored
    ``kilonova_score`` uses it INSTEAD of the product of TROVE's peak-luminosity
    / peak-time / decay-rate checks; a candidate without one falls back to the
    TROVE factor, because the toggle deliberately does not rescore and a
    candidate that has never been vetted under KilonovaSCORER has nothing else
    to show. Each candidate is marked with ``ec.phot_source`` so the page can
    say which it is displaying rather than leaving the two indistinguishable.
    """
    from scoring.phot_method import PHOT_METHOD_KILONOVA, get_phot_method

    if phot_method is None:
        phot_method = get_phot_method()
    use_kilonova = phot_method == PHOT_METHOD_KILONOVA

    val_not_score_keys = VAL_NOT_SCORE_KEYS
    exclude_keys = (set(val_not_score_keys.keys()) | set(TARGETEXTRA_KEYS)
                    | {KILONOVA_SCORE_KEY})
    
    if not agn_toggle:
        exclude_keys.add('agn_score')

    # only evaluate this once since it is time consuming
    event_candidates_list = list(event_candidates)

    # which transient types to consider?
    ### TODO: Right now, just does KN unless SSM; change this for BBH events
    try:
        nle_eventseq = localization_sequence_from_name(
            event_candidates_list[0].nonlocalizedevent.event_id
        )
        most_likely_class = get_most_likely_class(nle_eventseq.details)
    except IndexError:
        return []
    
    if most_likely_class == "SSM":
        transients = TRANSIENTS
    elif most_likely_class in {"BNS", "NSBH", "SGRB"}:
        transients = ["KN"]
    elif most_likely_class == "LGRB":
        transients = ["KN", "SN"] # SN is not yet implemented as a vetting mode
    elif most_likely_class == "FXT":
        transients = ["KN", "SN", "TDE"] # SN and TDE are not yet implemented as a vetting mode
    else:
        return []
        

    # Batch load all related data at once
    target_ids = [ec.target_id for ec in event_candidates_list]

    # Prefetch TargetExtra for all targets at once
    target_extras_by_id = {}
    for te in TargetExtra.objects.filter(target_id__in=target_ids):
        if te.target_id not in target_extras_by_id:
            target_extras_by_id[te.target_id] = {}
        target_extras_by_id[te.target_id][te.key] = te.value

    # Prefetch all ScoreFactor objects at once
    score_factors = ScoreFactor.objects.filter(
        event_candidate__in=event_candidates_list, key__in=subscore_names
    ).annotate(value_float=Cast("value", FloatField()))

    # Group score factors by event candidate
    score_factors_by_ec = {}
    for sf in score_factors:
        ec_id = sf.event_candidate_id
        if ec_id not in score_factors_by_ec:
            score_factors_by_ec[ec_id] = {}
        score_factors_by_ec[ec_id][sf.key] = sf.value_float

    ecs_out = []
    for ec in event_candidates_list:
        # set ec.score to be a dictionary mapping transient : score
        ec.score = {}

        if include_subscores:
            ec.subscores = {}
        
        # get all 'subscores' (sometimes actually calculated values)
        # for object; need to re-do this per transient because of step
        # below where we exclude certain scores from the queryset
        sf_dict = score_factors_by_ec.get(ec.id, {})

        # Extract values that need special handling
        val_dict = {
            subscore_key: sf_dict[subscore_key]
            for subscore_key, param_range_key in val_not_score_keys.items()
            if subscore_key in sf_dict
        }

        # now get all the scores stored in TargetExtra objects
        te = target_extras_by_id.get(ec.target_id, {})
        ps_score = 1
        if "ps_score" in te:
            ps_score = float(te["ps_score"])

        mpc_score = 1
        if "mpc_match_name" in te:
            mpc_score = int(te["mpc_match_name"] == str(None))

        # remove keys we don't want and calculate a base subscore
        # need to add "agn" to exclude keys if button is selected
        # AGN enabled should be a global state of the website
        subscore_no_phot = (
            math.prod([sf_dict[key] for key in sf_dict if key not in exclude_keys])
            * ps_score
            * mpc_score
        )

        # add things to the subscores dict, if requested by the user
        if include_subscores:
            ec.subscores["ps_score"] = ps_score
            ec.subscores["mpc_score"] = mpc_score
            for key in sf_dict:
                if key in exclude_keys: continue
                ec.subscores[key] = sf_dict[key]
                
        # now for EM transient/model specific scores
        for transient in transients:
            # allowed parameter ranges for given transient
            if transient not in dict_transients_param_ranges:
                continue # this is fine, some transient scoring algorithms aren't implemented yet
            param_ranges = dict_transients_param_ranges[transient]

            # compute the photometry score
            phot_subscores = {
                subscore_key: _check_phot_val(
                    val_dict[subscore_key], param_ranges, param_range_key
                )
                for subscore_key, param_range_key in val_not_score_keys.items()
                if subscore_key in val_dict
            }

            if include_subscores:
                ec.subscores[transient] = phot_subscores

            # ONLY "KN" may use KilonovaSCORER. The three transient types differ
            # solely in the `param_ranges` above -- `subscore_no_phot` is shared
            # -- so substituting the same `kn` into all of them made all three
            # scores numerically identical and silently discarded the
            # "KN-in-SN" / "super-KN" acceptance windows, which is the whole
            # content of those two columns.
            kn = sf_dict.get(KILONOVA_SCORE_KEY)
            if (use_kilonova and transient == KILONOVA_SCORER_TRANSIENT
                    and kn is not None and math.isfinite(kn)):
                # KilonovaSCORER's factor stands in for the whole TROVE
                # photometry product -- not multiplied with it, which would
                # apply the photometry twice.
                phot_score = kn
                phot_source = "kilonova"
            else:
                phot_score = math.prod(list(phot_subscores.values()))
                phot_source = "trove"

            # Recorded from the "KN" pass only. This drives the yellow-row
            # highlight, the "scored only" filter and the blue "no scores yet"
            # notice, all of which are about the KilonovaSCORER column; taking
            # it from whichever transient happened to be last would report
            # "trove" for every candidate as soon as more than one type is
            # scored.
            if transient == KILONOVA_SCORER_TRANSIENT:
                ec.phot_source = phot_source

            # save the score to a temporary field (dictionary) in the
            # EventCandidate object
            ec.score[transient] = (
                subscore_no_phot * phot_score
            )  # multiply the subscores
        ecs_out.append(ec)

    print("Finished computing the scores, sorting and returning...", time.time())

    # sort by kilonova score, for now
    ## TODO: generalize this
    return sorted(ecs_out, reverse=True, key=lambda x: x.score["KN"])


def get_target_score(target_id):

    if target_id is None:
        return "Target ID is None!"

    target = Target.objects.get(id=target_id)

    out = {}
    for event_candidate in target.eventcandidate_set.all():
        nonlocalized_name = NonLocalizedEvent.objects.get(
            id=event_candidate.nonlocalizedevent_id
        ).event_id

        out[nonlocalized_name] = event_candidate.priority

    return out


VET_ALL_FINISHED_NOTICE_PERIOD = 3600  # 1 hour in seconds


def _latest_run(tasks, latest):
    """
    Narrow an event's vetting tasks to those of its most recent run.

    Runs are told apart by the `run_started` stamp `vet_all_async` puts on every
    task it enqueues. Rows predating that stamp fall back on timing: a run
    enqueues all of its tasks within seconds, so anything appreciably older than
    the newest task belongs to an earlier run.
    """
    stamp = (latest.args_kwargs.get("kwargs") or {}).get("run_started")
    if stamp:
        return tasks.filter(args_kwargs__kwargs__run_started=stamp)
    return tasks.filter(
        enqueued_at__gte=latest.enqueued_at - timedelta(minutes=2))


def get_vet_all_progress(nonlocalizedevent_id):
    """
    Summarize how far the most recent "Vet All" run for an event has got.

    "Vet All" enqueues one task per candidate, so the queue *is* the progress
    bar: every task still waiting or running is a candidate whose score has not
    been updated yet. Returns None when there is nothing worth telling the user
    about (no run on record, and nothing left in the queue).
    """
    if not nonlocalizedevent_id:
        return None

    try:
        nle = NonLocalizedEvent.objects.get(id=nonlocalizedevent_id)
    except (NonLocalizedEvent.DoesNotExist, ValueError):
        return None

    # Pin this to the one task that "Vet All" enqueues, not just to its queue.
    # Production queues are shared and outlive any one feature, so a second kind
    # of task landing on this one later must not start counting as candidates.
    tasks = DBTaskResult.objects.filter(
        queue_name=VET_ALL_QUEUE_NAME,
        task_path=async_vet.module_path,
        args_kwargs__kwargs__nle_event_id=nle.event_id,
    )
    pending_statuses = [ResultStatus.NEW, ResultStatus.RUNNING]

    try:
        latest = tasks.order_by("-enqueued_at").first()
        if latest is None:
            return None

        # What is still outstanding is counted across every run, not just the
        # newest. If a second run starts before the first has drained, the
        # leftovers of the first are still rewriting scores, and the page would
        # otherwise call itself up to date while they did.
        pending = tasks.filter(status__in=pending_statuses).count()
        run = _latest_run(tasks, latest).aggregate(
            total=Count("id"),
            pending=Count("id", filter=Q(status__in=pending_statuses)),
            failed=Count("id", filter=Q(status=ResultStatus.FAILED)),
            last_finished=Max("finished_at"),
            first_enqueued=Min("enqueued_at"),
        )
    except DatabaseError:
        # the progress notice is never worth taking the candidate list down for
        logger.exception("Could not read Vet All progress for %s", nle.event_id)
        return None

    if not run["total"] and not pending:
        return None

    last_finished = run["last_finished"]
    run_kwargs = latest.args_kwargs.get("kwargs") or {}
    started = (parse_datetime(run_kwargs["run_started"])
               if run_kwargs.get("run_started") else run["first_enqueued"])

    # a finished run is only news for as long as the button stays on cooldown;
    # after that, stop reporting it
    if not pending:
        if last_finished is None:
            return None
        if timezone.now() - last_finished > timedelta(
            seconds=VET_ALL_FINISHED_NOTICE_PERIOD
        ):
            return None

    total = run["total"]
    done = max(total - run["pending"], 0)

    return {
        "running": bool(pending),
        "pending": pending,
        "done": done,
        "total": total,
        "failed": run["failed"],
        "percent": int(round(100 * done / total)) if total else None,
        "started": started,
        "finished": last_finished if not pending else None,
        "vetting_mode": run_kwargs.get("vetting_mode"),
        "username": run_kwargs.get("started_by"),
    }


def get_last_vetting(target_id, nonlocalizedevent_id=None):
    """
    When was this candidate last vetted, and did it work?

    Read off the task queue, which records one row per candidate per run. Note
    the ``target_ids__0`` lookup: ``vet_all_async`` enqueues exactly one target
    per task, so the first element is the whole list. A task carrying several
    targets would be missed, which nothing currently creates.
    """
    if target_id is None:
        return None

    tasks = DBTaskResult.objects.filter(
        queue_name=VET_ALL_QUEUE_NAME,
        task_path=async_vet.module_path,
        args_kwargs__kwargs__target_ids__0=int(target_id),
    )
    if nonlocalizedevent_id:
        try:
            nle = NonLocalizedEvent.objects.get(id=nonlocalizedevent_id)
        except (NonLocalizedEvent.DoesNotExist, ValueError):
            return None
        tasks = tasks.filter(args_kwargs__kwargs__nle_event_id=nle.event_id)

    try:
        latest = tasks.order_by("-enqueued_at").first()
        if latest is None:
            return None
        finished = (tasks.filter(finished_at__isnull=False)
                    .order_by("-finished_at").first())
    except DatabaseError:
        logger.exception("Could not read last vetting for target %s", target_id)
        return None

    def describe(task):
        if task is None:
            return None
        kwargs = task.args_kwargs.get("kwargs", {})
        return {
            "finished": task.finished_at,
            "enqueued": task.enqueued_at,
            "status": task.status,
            "succeeded": task.status == ResultStatus.SUCCEEDED,
            "vetting_mode": kwargs.get("vetting_mode"),
            "event_id": kwargs.get("nle_event_id"),
        }

    return {
        # a queued or running task means the score on screen is about to change
        "in_progress": latest.status in (ResultStatus.NEW, ResultStatus.RUNNING),
        "last": describe(finished),
        "queued_at": latest.enqueued_at if latest.status == ResultStatus.NEW else None,
    }


def get_last_vet_all_run(nonlocalizedevent_id):
    """
    Summarize the most recent "Vet All" run for an event, however long ago.

    Distinct from `get_vet_all_progress`, which is a transient notice about a
    run happening now. This is the standing record of when the scores on the
    page were last refreshed.
    """
    if not nonlocalizedevent_id:
        return None

    try:
        nle = NonLocalizedEvent.objects.get(id=nonlocalizedevent_id)
    except (NonLocalizedEvent.DoesNotExist, ValueError):
        return None

    tasks = DBTaskResult.objects.filter(
        queue_name=VET_ALL_QUEUE_NAME,
        task_path=async_vet.module_path,
        args_kwargs__kwargs__nle_event_id=nle.event_id,
    )
    try:
        latest = tasks.order_by("-enqueued_at").first()
        if latest is None:
            return None
        counts = _latest_run(tasks, latest).aggregate(
            total=Count("id"),
            succeeded=Count("id", filter=Q(status=ResultStatus.SUCCEEDED)),
            failed=Count("id", filter=Q(status=ResultStatus.FAILED)),
            pending=Count(
                "id",
                filter=Q(status__in=[ResultStatus.NEW, ResultStatus.RUNNING]),
            ),
            finished=Max("finished_at"),
            first_enqueued=Min("enqueued_at"),
        )
    except DatabaseError:
        logger.exception("Could not read last Vet All run for %s", nle.event_id)
        return None

    run_kwargs = latest.args_kwargs.get("kwargs") or {}
    return {
        "finished": counts["finished"],
        "started": (parse_datetime(run_kwargs["run_started"])
                    if run_kwargs.get("run_started") else counts["first_enqueued"]),
        "vetting_mode": run_kwargs.get("vetting_mode"),
        "username": run_kwargs.get("started_by"),
        "total": counts["total"],
        "succeeded": counts["succeeded"],
        "failed": counts["failed"],
        "running": bool(counts["pending"]),
    }
