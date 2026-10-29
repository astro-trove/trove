import logging
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import transaction
from django.db.models import Min
from tom_targets.models import TargetExtra
from tom_nonlocalizedevents.models import NonLocalizedEvent, EventCandidate
from tom_dataproducts.models import PhotometryReducedDatum

from scoring.em_models.util import MODELS_BY_NAME
from scoring.unified_scorer import score, vet_basic
from scoring.util import get_possible_em_transients

from trove_nonlocalizedevents.healpix_utils import create_candidates_from_targets
from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import get_most_likely_class
from trove_targets.models import Target
from astropy.time import Time, TimezoneInfo
from astropy.coordinates import SkyCoord
from astroquery.ipac.irsa.irsa_dust import IrsaDust
from django.conf import settings

logger = logging.getLogger(__name__)
new_format = logging.Formatter("[%(asctime)s] %(levelname)s : s%(message)s")
for handler in logger.handlers:
    handler.setFormatter(new_format)

# an AGN flare can appear long after a BBH merger, so BBH events stay open this long
AGN_FLARE_HORIZON_DAYS = MODELS_BY_NAME["AGN-flare"].detection_horizon[1]

_target_hook_options = ContextVar('target_hook_options', default=None)

def get_active_nonlocalizedevents(t0=None, lookback_days=3.0, test=False):
    """
    Returns a queryset containing "active" NonLocalizedEvents, significant events that happened less than
    `lookback_days` before `t0` and have not been retracted. Use `test=True` to query mock events instead of real ones.
    """
    if t0 is None:
        t0 = datetime.now(tz=timezone.utc)
    lookback_window_nle = (t0 - timedelta(days=lookback_days)).isoformat()
    active_nles = NonLocalizedEvent.objects.filter(
        sequences__details__time__gte=lookback_window_nle, state="ACTIVE"
    )
    active_nles = active_nles.exclude(sequences__details__significant=False)
    if test:
        active_nles = active_nles.filter(event_id__startswith="MS")
    else:
        active_nles = active_nles.exclude(event_id__startswith="MS")
    return active_nles.distinct()


def first_detection_window_days(nle_class, first_det_min, first_det_max):
    """(min, max) days after an event that a target's first detection may fall.
    A BBH merger's AGN flare can appear long after it, so BBH events stay open
    to AGN_FLARE_HORIZON_DAYS."""
    if nle_class == "BBH":
        return first_det_min, max(first_det_max, AGN_FLARE_HORIZON_DAYS)
    return first_det_min, first_det_max


def vet_new_candidate(candidate, basic_results=None):
    """Vet a newly associated candidate for its event's class: an AGN flare for
    a BBH event, every kilonova-style mode for anything else.

    `basic_results` is the output of an earlier `vet_basic` call on this target,
    passed through so each vetting mode doesn't rerun it."""
    target_id, event_id = candidate.target.id, candidate.nonlocalizedevent.event_id

    def _basic():
        # fresh copies so one vetting mode can't modify another's dataframes
        if basic_results is None:
            return None
        host_df, agn_df, keep_vetting = basic_results
        return host_df.copy(), agn_df.copy(), keep_vetting

    for em_model in get_possible_em_transients(event_id):
        if em_model not in MODELS_BY_NAME:
            continue  # no scoring model implemented for this transient yet
        score(
            target_id=target_id,
            nonlocalized_event_name=event_id,
            em_model_name=em_model,
            basic_results=_basic()
        )

def associate_nle_with_target(
    target: Target, lookback_days_nle, first_det_min, first_det_max
):
    # automatically associate with nonlocalized events
    new_candidates = []
    first_det = (
        target.photometryreduceddatum_set.filter(brightness__isnull=False)
        .order_by("timestamp")
        .first()
    )
    if first_det is None:
        return new_candidates

    # BBH events stay open for the AGN-flare horizon, every other class for lookback_days_nle
    recent_nle_ids = set(
        get_active_nonlocalizedevents(lookback_days=lookback_days_nle).values_list("id", flat=True)
    )
    lookback_days = max(lookback_days_nle, AGN_FLARE_HORIZON_DAYS)
    for nle in get_active_nonlocalizedevents(lookback_days=lookback_days):
        seq = nle.sequences.last()
        nle_class = get_most_likely_class(seq.details)
        if nle.id not in recent_nle_ids and nle_class != "BBH":
            continue
        try:
            nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f%z")
        except ValueError:
            nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f")
        det_min, det_max = first_detection_window_days(nle_class, first_det_min, first_det_max)
        if (
            nle_time + timedelta(days=det_min) < first_det.timestamp
            and first_det.timestamp < nle_time + timedelta(days=det_max)
        ):
            new_candidates += create_candidates_from_targets(seq, target_ids=[target.id])

    return new_candidates

def associate_targets_with_nle(
    nle:NonLocalizedEvent, first_det_min, first_det_max   
):
    # get info on the NLE
    seq = nle.sequences.last()
    try:
        nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f")

    # query for relevant targets
    targets = PhotometryReducedDatum.objects.filter(
        brightness__isnull=False
    ).values(
        "target_id"
    ).annotate(
        min_timestamp=Min("timestamp")
    ).filter(
        min_timestamp__gt = nle_time + timedelta(days=first_det_min),
        min_timestamp__lt = nle_time + timedelta(days=first_det_max)
    ).values_list(
        "target_id",
        flat=True
    )

    # then create candidates from these targets and return them
    return create_candidates_from_targets(seq, target_ids=list(targets)) 

