"""
Some common functions used in multiple places throughout the app
"""

from collections import namedtuple
from datetime import timedelta
import json
import math
import logging
from astropy import units as u
from astropy.units import Quantity
from django.db import DatabaseError
from django.db.models import Count, FloatField, Max, Min, Q
from django.db.models.functions import Cast
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django_tasks import ResultStatus
from django_tasks.backends.database.models import DBTaskResult
from tom_nonlocalizedevents.models import NonLocalizedEvent
from trove_targets.models import Target
from tom_targets.models import TargetExtra

from custom_code.templatetags.nonlocalizedevent_extras import get_most_likely_class

from candidate_vetting.vet import localization_sequence_from_name

from .scoring import classification_score, cosmo, mpc_score_from_match
from .vet_phot import PHOT_SCORE_MIN
from .vet_kn import PARAM_RANGES as KN_PARAM_RANGES
from .vet_kn_in_sn import PARAM_RANGES as KN_IN_SN_PARAM_RANGES
from .vet_super_kn import PARAM_RANGES as SUPER_KN_PARAM_RANGES
from .vet_bbh import PARAM_RANGES as AGN_FLARE_PARAM_RANGES
from .models import ScoreFactor
from .tasks import async_vet

import time

logger = logging.getLogger(__name__)

# map imported parameter ranges to transients. Also doubles as the priority order
# used below to pick a single canonical sort key for a candidate list -- "AGN-flare"
# has to be here too, or a BBH event (transients = ["AGN-flare"]) falls through the
# loop below and its candidates come back unsorted.
TRANSIENTS = ["KN", "KN-in-SN", "super-KN", "SN", "TDE", "AGN-flare"]

DICT_TRANSIENTS_PARAM_RANGES = {
    "KN": KN_PARAM_RANGES,
    "KN-in-SN": KN_IN_SN_PARAM_RANGES,
    "super-KN": SUPER_KN_PARAM_RANGES,
    "AGN-flare": AGN_FLARE_PARAM_RANGES,
}


# ScoreFactor key holding KilonovaSCORER's photometry factor, written by
# `vet_kn` when the viewer's `phot_method` toggle is on KilonovaSCORER
KILONOVA_SCORE_KEY = "kilonova_score"

# why KilonovaSCORER could not score a candidate, written by `vet_kn` in place of score
KILONOVA_SKIP_REASON_KEY = "kilonova_skip_reason"

# session key for the AGN toggle, which decides whether `agn_score` counts
# towards the total in `get_event_candidate_scores` below. Per viewer.
AGN_TOGGLE_KEY = "agn_toggle"
AGN_TOGGLE_DEFAULT = True

# default subscore names
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
    "agn_flare_score",
    "nuclear_offset_score",
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


# the AGN toggle governs only these; BBH AGN-flare scoring always uses
# its AGN association
AGN_TOGGLE_TRANSIENTS = {"KN", "KN-in-SN", "super-KN"}

PS_WAIVED_TRANSIENTS = {"AGN-flare"}

KN_STYLE_CLASSES = {"SSM", "Terrestrial", "BNS", "NSBH", "SGRB", "LGRB", "FXT"}

def ps_counts_toward(transient, agn_score):
    """Whether ps_score enters `transient`'s score."""
    if transient not in PS_WAIVED_TRANSIENTS:
        return True
    matched = AGN_FLARE_PARAM_RANGES["agn_match_score"]
    return not (agn_score is not None and agn_score >= matched)


def agn_counts_toward(transient, agn_toggle):
    """Whether agn_score enters `transient`'s score."""
    return bool(agn_toggle) or transient not in AGN_TOGGLE_TRANSIENTS


def _phot_score_key(metric_key):
    """The key a photometry metric's subscore is stored under."""
    return f"{metric_key}_score"


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


def kilonova_scores_exist(nonlocalizedevent_id=None, target_id=None):
    """Whether any KilonovaSCORER score is stored for this event and/or target."""
    qs = ScoreFactor.objects.filter(key=KILONOVA_SCORE_KEY)
    if nonlocalizedevent_id is not None:
        qs = qs.filter(event_candidate__nonlocalizedevent_id=nonlocalizedevent_id)
    if target_id is not None:
        qs = qs.filter(event_candidate__target_id=target_id)
    return qs.exists()


def most_likely_class_for_event(nonlocalizedevent_name):
    """Best-guess EM-transient classification for an NLE (event_id string), or
    None if it can't be determined (e.g. no localization sequence yet)."""
    try:
        nle_eventseq = localization_sequence_from_name(nonlocalizedevent_name)
        return get_most_likely_class(nle_eventseq.details)
    except IndexError:
        return None


def get_no_score_message(most_likely_class):
    """Message for event classes with no scoring yet, else None. Takes the
    class from most_likely_class_for_event."""
    if most_likely_class in KN_STYLE_CLASSES | {"BBH"}:
        return None

    return f"Scoring is not yet implemented for events of class {most_likely_class or 'unknown'}."


def get_agn_toggle(request=None) -> bool:
    """Whether this viewer wants AGN scores counted. No session, no request:
    a queued task gets the default."""
    if request is None or not hasattr(request, "session"):
        return AGN_TOGGLE_DEFAULT
    return bool(request.session.get(AGN_TOGGLE_KEY, AGN_TOGGLE_DEFAULT))


def set_agn_toggle(request, value: bool) -> bool:
    """Set this viewer's AGN toggle. Returns what was stored."""
    request.session[AGN_TOGGLE_KEY] = bool(value)
    return bool(value)


def get_event_candidate_scores(
        event_candidates,
        dict_transients_param_ranges=DICT_TRANSIENTS_PARAM_RANGES,
        subscore_names=SUBSCORE_NAMES,
        agn_toggle=True,
        include_subscores=False,
        phot_method=None,
):
    """
    `phot_method` selects which photometry factor the score uses (`None`
    falls back to the default, since there may be no session to read.) `agn_toggle` drops agn_score from the
    kilonova-style scores only (`AGN_TOGGLE_TRANSIENTS`).
    """
    from scoring.phot_method import PHOT_METHOD_KILONOVA, get_phot_method

    if phot_method is None:
        phot_method = get_phot_method()
    use_kilonova = phot_method == PHOT_METHOD_KILONOVA

    val_not_score_keys = VAL_NOT_SCORE_KEYS

    # exclude keys thathave special cases like
    # 1. keys that describe a derived metric rather than a score directly
    # 2. keys that are in the target extra instead of scorefactor table
    # 3. the KilonovaScorer key, since it only gets applied to KN
    # 4. the agn and classification scores, which change behavior based on the EM
    #    transient type that is expected
    exclude_keys = (set(val_not_score_keys.keys()) | set(TARGETEXTRA_KEYS)
                    | {KILONOVA_SCORE_KEY} | {"agn_score", "classification_score"})

    # only evaluate this once since it is time consuming
    event_candidates_list = list(event_candidates)

    # which transient types to consider?
    try:
        nle_eventseq = localization_sequence_from_name(
            event_candidates_list[0].nonlocalizedevent.event_id
        )
        most_likely_class = get_most_likely_class(nle_eventseq.details)
    except IndexError:
        return []

    if most_likely_class in {"SSM", "Terrestrial"}:
        transients = ["KN", "KN-in-SN", "super-KN"]
    elif most_likely_class in {"BNS", "NSBH", "SGRB"}:
        transients = ["KN"]
    elif most_likely_class == "LGRB":
        transients = ["KN", "SN"] # SN is not yet implemented as a vetting mode
    elif most_likely_class == "FXT":
        transients = ["KN", "SN", "TDE"] # SN and TDE are not yet implemented as a vetting mode
    elif most_likely_class == "BBH":
        transients = ["AGN-flare"]
    else:
        transients = []

    # Batch load all related data at once
    target_ids = [ec.target_id for ec in event_candidates_list]

    # Prefetch TargetExtra for all targets at once
    target_extras_by_id = {}
    for te in TargetExtra.objects.filter(target_id__in=target_ids):
        if te.target_id not in target_extras_by_id:
            target_extras_by_id[te.target_id] = {}
        target_extras_by_id[te.target_id][te.key] = te.value

    # Prefetch all ScoreFactor objects at once.
    score_factors = ScoreFactor.objects.filter(
        event_candidate__in=event_candidates_list,
        key__in=list(subscore_names),
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
            _phot_score_key(subscore_key): sf_dict[subscore_key]
            for subscore_key, param_range_key in val_not_score_keys.items()
            if subscore_key in sf_dict
        }

        # now get all the scores stored in TargetExtra objects
        te = target_extras_by_id.get(ec.target_id, {})
        ps_score = 1
        if "ps_score" in te:
            ps_score = float(te["ps_score"])

        mpc_score = mpc_score_from_match(te.get("mpc_match_name"))

        # removed ps_score because if it is a vet_bbh() call, then ps_score of 0
        # might just be because AGN is in the point source catalogue
        subscore_no_phot = (
            math.prod([sf_dict[key] for key in sf_dict
                       if key not in exclude_keys])
            * mpc_score
        )
        agn_score = sf_dict.get("agn_score")

        # add things to the subscores dict, if requested by the user
        if include_subscores:
            ec.subscores["ps_score"] = ps_score
            ec.subscores["mpc_score"] = mpc_score
            for key in sf_dict:
                if key in exclude_keys: continue
                if key == "agn_score" and not any(agn_counts_toward(t, agn_toggle) for t in transients):
                    continue
                ec.subscores[key] = sf_dict[key]
                
        # now for EM transient/model specific scores
        for transient in transients:
            # allowed parameter ranges for given transient
            if transient not in dict_transients_param_ranges:
                continue # this is fine, some transient scoring algorithms aren't implemented yet
            param_ranges = dict_transients_param_ranges[transient]

            # handle the agn and PS scores, whose behaviour changes based on the
            # expected transient type
            agn_factor = (agn_score if agn_score is not None
                          and agn_counts_toward(transient, agn_toggle) else 1)
            ps_factor = ps_score if ps_counts_toward(transient, agn_score) else 1
            
            # handle the classification score, which changes based on the expected
            # EM transient
            class_score = classification_score(ec.target, transient)
            
            # val_dict is keyed with the "_score" suffix the display labels use
            phot_subscores = {
                _phot_score_key(subscore_key): _check_phot_val(
                    val_dict[_phot_score_key(subscore_key)], param_ranges, param_range_key
                )
                for subscore_key, param_range_key in val_not_score_keys.items()
                if _phot_score_key(subscore_key) in val_dict
                and param_range_key in param_ranges
            }

            other_subscores = dict(
                classification_score = class_score
            )
            
            if include_subscores:
                ec.subscores[transient] = phot_subscores | other_subscores

            # ONLY "KN" can use KilonovaSCORER
            kn = sf_dict.get(KILONOVA_SCORE_KEY)
            kn_available = kn is not None and math.isfinite(kn)
            if use_kilonova and transient == "KN" and kn_available:
                # KilonovaSCORER's factor stands in for the whole TROVE
                # photometry product -- not multiplied with it, which would
                # apply the photometry twice.
                phot_score = kn
                phot_source = "kilonova"
            else:
                phot_score = math.prod(list(phot_subscores.values()))
                phot_source = "trove"

            if transient == "KN":
                ec.phot_source = phot_source
                ec.kilonova_score = kn if kn_available else None

            # save the score to a temporary field (dictionary) in the
            # EventCandidate object
            ec.score[transient] = min(
                1.0, max(0.0,
                         subscore_no_phot * agn_factor * ps_factor * class_score * phot_score)
            )
        ecs_out.append(ec)

    logger.info(f"Finished computing the scores, sorting and returning... time.time = {time.time()}")

    for key in TRANSIENTS:
        if key in transients:
            return sorted(ecs_out, reverse=True, key=lambda x: x.score.get(key, 0))
    return ecs_out


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


#: the distance a candidate was scored against, and where it came from
HostDistance = namedtuple("HostDistance", "distance neg_err pos_err source")


def host_distances(event_candidates):
    """The distance each candidate was scored against, in Mpc, by candidate id.

    Three batched queries whatever the number of candidates, so a whole list
    costs about what one row used to. Vetting records the galaxy it picked in
    `host_name`/`host_catalog`, so the distance is read back out of that
    galaxy's row rather than stored per candidate. Candidates scored off the
    target's own redshift have no host, so theirs comes from that redshift.
    """
    candidates = list(event_candidates)
    if not candidates:
        return {}

    recorded = {}
    for score_factor in ScoreFactor.objects.filter(
        event_candidate_id__in=[c.id for c in candidates],
        key__in=("host_name", "host_catalog"),
    ).only("event_candidate_id", "key", "value"):
        recorded.setdefault(score_factor.event_candidate_id, {})[
            score_factor.key] = score_factor.value

    hosted = {c.id: recorded[c.id] for c in candidates
              if _recorded_host_name(recorded.get(c.id))}

    galaxies = {}
    if hosted:
        rows = TargetExtra.objects.filter(
            target_id__in={c.target_id for c in candidates if c.id in hosted},
            key="Host Galaxies",
        ).values_list("target_id", "value")
        for target_id, value in rows:
            galaxies[target_id] = _host_galaxy_rows(value)

    # the remainder were scored off the target's own redshift, if it has one
    redshifts = dict(
        Target.objects.filter(
            id__in={c.target_id for c in candidates if c.id not in hosted}
        ).values_list("id", "redshift")
    )

    distances = {}
    for candidate in candidates:
        if candidate.id in hosted:
            found = _recorded_host_distance(
                galaxies.get(candidate.target_id), hosted[candidate.id])
        else:
            found = _redshift_distance(redshifts.get(candidate.target_id))
        if found is not None:
            distances[candidate.id] = found
    return distances


def _recorded_host_name(recorded):
    """The host id vetting recorded, or None where it recorded no host."""
    if not recorded:
        return None
    name = recorded.get("host_name")
    return None if name in (None, "", "None", "nan") else name


def _host_galaxy_rows(value):
    """The galaxy rows of a "Host Galaxies" TargetExtra, always as a list."""
    try:
        rows = json.loads(value)
    except (TypeError, ValueError):
        return []
    return rows if isinstance(rows, list) else [rows]


def _recorded_host_distance(galaxies, recorded):
    """The distance of the galaxy vetting recorded, matched out of its table."""
    if not galaxies:
        return None
    name = recorded.get("host_name")
    matches = [g for g in galaxies if str(g.get("ID")) == str(name)]
    if not matches:
        # ids above 2**53 are recorded having been through a float, so the
        # stored name can be a rounded copy of the one in this table
        matches = [g for g in galaxies if _same_id_through_float(g.get("ID"), name)]
    # ids repeat across catalogs, so the catalog breaks the tie where we have it
    catalog = recorded.get("host_catalog")
    if len(matches) > 1 and catalog:
        matches = [g for g in matches if str(g.get("Source")) == str(catalog)]
    # the same galaxy is sometimes ingested twice; rows that agree on the
    # distance are not a real ambiguity, only conflicting ones are
    found = {_finite(g.get("Dist")) for g in matches}
    if len(found) != 1:
        return None
    distance = found.pop()
    if distance is None:
        return None
    galaxy = matches[0]
    neg_err, pos_err = _distance_bounds(galaxy.get("DistErr"))
    return HostDistance(distance, neg_err, pos_err,
                        f"{name} ({galaxy.get('Source')})")


def _same_id_through_float(table_id, recorded):
    """Whether two galaxy ids agree once both are put through a float."""
    try:
        return float(str(table_id).strip("'")) == float(str(recorded))
    except (TypeError, ValueError):
        return False


def _distance_bounds(err):
    """A galaxy's distance error, which the host table stores either as a
    [low, high] pair or as one symmetric number."""
    if isinstance(err, (list, tuple)) and len(err) == 2:
        return _finite(err[0]), _finite(err[1])
    symmetric = _finite(err)
    return symmetric, symmetric


def _finite(value):
    """A float, or None where it is missing or not a finite number."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) or math.isinf(value) else value


def _redshift_distance(redshift):
    """The luminosity distance of a target's own redshift, as vetting takes it.

    Redshifts are stored as NaN rather than NULL where unknown, and a
    non-positive one is not a distance, so both come back as no distance.
    """
    redshift = _finite(redshift)
    if redshift is None or redshift <= 0:
        return None
    err = cosmo.luminosity_distance(1e-3).to(u.Mpc).value
    return HostDistance(
        cosmo.luminosity_distance(redshift).to(u.Mpc).value,
        err, err, "target redshift")


def _latest_run(tasks, latest):
    """
    Get running vetting tasks for the most recent "Vet All" or "Vet Selected"
    run for some event.
    """
    stamp = (latest.args_kwargs.get("kwargs") or {}).get("run_started")
    if stamp:
        return tasks.filter(args_kwargs__kwargs__run_started=stamp)
    return tasks.filter(
        enqueued_at__gte=latest.enqueued_at - timedelta(minutes=2))


def get_vet_multi_progress(nonlocalizedevent_id):
    """
    Get the progress for the most recent "Vet All" or "Vet Selected" run.
    """
    if not nonlocalizedevent_id:
        return None

    try:
        nle = NonLocalizedEvent.objects.get(id=nonlocalizedevent_id)
    except (NonLocalizedEvent.DoesNotExist, ValueError):
        return None

    # get tasks for given NLE
    tasks = DBTaskResult.objects.filter(
        queue_name="vet_multi",
        task_path=async_vet.module_path,
        args_kwargs__kwargs__nle_event_id=nle.event_id,
    )
    pending_statuses = [ResultStatus.NEW, ResultStatus.RUNNING]

    try:
        latest = tasks.order_by("-enqueued_at").first()
        if latest is None:
            return None

        # ONE queryset behind every number below, scoped to the latest run.
        # These used to be split: the running flag counted every pending task
        # for the EVENT while the totals counted only the latest run, so a task
        # left pending by some earlier run read as "still running" next to
        # "88 of 88 scored".
        run = _latest_run(tasks, latest).aggregate(
            total=Count("id"),
            pending=Count("id", filter=Q(status__in=pending_statuses)),
            failed=Count("id", filter=Q(status=ResultStatus.FAILED)),
            last_finished=Max("finished_at"),
            first_enqueued=Min("enqueued_at"),
        )
    except DatabaseError:
        # the progress notice is never worth taking the candidate list down for
        logger.exception("Could not read Vet All / Vet Selected progress for %s", 
                         nle.event_id)
        return None

    pending = run["pending"]
    if not run["total"]:
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
            seconds=3600 # 1 hour
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
    """When was this candidate last vetted?"""
    if target_id is None:
        return None

    tasks = DBTaskResult.objects.filter(
        queue_name="vet_multi",
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


def get_last_vet_multi_run(nonlocalizedevent_id):
    """
    Summarize the most recent "Vet All" or "Vet Selected" run. Distinct from
    `get_vet_multi_progress`, describes an ongoing run.
    """
    if not nonlocalizedevent_id:
        return None

    try:
        nle = NonLocalizedEvent.objects.get(id=nonlocalizedevent_id)
    except (NonLocalizedEvent.DoesNotExist, ValueError):
        return None

    tasks = DBTaskResult.objects.filter(
        queue_name="vet_multi",
        task_path=async_vet.module_path,
        args_kwargs__kwargs__nle_event_id=nle.event_id,
    )
    try:
        latest = tasks.order_by("-enqueued_at").first()
        if latest is None:
            return None
        latest_tasks = _latest_run(tasks, latest)
        counts = latest_tasks.aggregate(
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
        logger.info(counts["failed"])
        if counts["failed"]: # if any failed, record names
            latest_tasks_failed = latest_tasks.filter(status="FAILED")
            logger.info(latest_tasks_failed)
            targets_failed = [Target.objects.get(
                id=task.args_kwargs["kwargs"]["target_ids"][0]) for
                task in latest_tasks_failed]
        else:
            targets_failed = []
    except DatabaseError:
        logger.exception("Could not read last Vet All / Vet Selected run for %s",
                         nle.event_id)
        return None

    run_kwargs = latest.args_kwargs.get("kwargs") or {}
    logger.info(f"{latest}")
    logger.info(f"{run_kwargs}")

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
        "targets_failed":targets_failed,
    }
