import logging
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import transaction
from django.db.models import Min
from tom_targets.models import TargetExtra
from tom_nonlocalizedevents.models import NonLocalizedEvent, EventCandidate
from tom_dataproducts.models import ReducedDatum

from scoring.vet_kn import vet_kn
from scoring.vet_kn_in_sn import vet_kn_in_sn
from scoring.vet_super_kn import vet_super_kn
from scoring.vet_basic import vet_basic
from scoring.vet_bbh import vet_bbh, AGN_FLARE_HORIZON_DAYS

from custom_code.healpix_utils import create_candidates_from_targets
from custom_code.templatetags.nonlocalizedevent_extras import get_most_likely_class
from trove_targets.models import Target
from astropy.time import Time, TimezoneInfo
from astropy.coordinates import SkyCoord
from astroquery.ipac.irsa.irsa_dust import IrsaDust
from django.conf import settings

logger = logging.getLogger(__name__)
new_format = logging.Formatter("[%(asctime)s] %(levelname)s : s%(message)s")
for handler in logger.handlers:
    handler.setFormatter(new_format)

_target_hook_options = ContextVar('target_hook_options', default=None)
    
def process_reduced_ztf_data(target, candidates):
    """Ingest data from the ZTF JSON format into ``ReducedDatum`` objects. Mostly copied from tom_base v2.13.0."""
    for candidate in candidates:
        if all(
            [
                key in candidate["candidate"]
                for key in ["jd", "magpsf", "fid", "sigmapsf"]
            ]
        ):
            nondetection = False
        elif all(key in candidate["candidate"] for key in ["jd", "diffmaglim", "fid"]):
            nondetection = True
        else:
            continue
        jd = Time(candidate["candidate"]["jd"], format="jd", scale="utc")
        jd.to_datetime(timezone=TimezoneInfo())
        value = {"filter": {1: "g", 2: "r", 3: "i"}[candidate["candidate"]["fid"]]}
        if nondetection:
            value["limit"] = candidate["candidate"]["diffmaglim"]
        else:
            value["magnitude"] = candidate["candidate"]["magpsf"]
            value["error"] = candidate["candidate"]["sigmapsf"]
        rd, created = ReducedDatum.objects.get_or_create(
            timestamp=jd.to_datetime(timezone=TimezoneInfo()),
            value=value,
            source_name="ZTF",
            data_type="photometry",
            target=target,
        )
        if created:  # do this afterward, in case there are duplicate candidates with distinct ZIDs
            rd.source_location = candidate["zid"]
            rd.save()


def update_or_create_target_extra(target, key, value):
    """
    Check if a ``TargetExtra`` with the given key exists for a given target. If it exists, update the value. If it does
    not exist, create it with the input value.
    """
    te, created = TargetExtra.objects.get_or_create(target=target, key=key)
    te.value = value
    te.save()


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

    if get_most_likely_class(candidate.nonlocalizedevent.sequences.last().details) == "BBH":
        vet_bbh(target_id, event_id, basic_results=_basic())
    else:
        vet_kn(target_id, event_id, basic_results=_basic())
        vet_kn_in_sn(target_id, event_id, basic_results=_basic())
        vet_super_kn(target_id, event_id, basic_results=_basic())


def associate_nle_with_target(
    target: Target, lookback_days_nle, first_det_min, first_det_max
):
    # automatically associate with nonlocalized events
    new_candidates = []
    first_det = (
        target.reduceddatum_set.filter(
            data_type="photometry", value__magnitude__isnull=False
        )
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
    targets = ReducedDatum.objects.filter(
        data_type="photometry",
        value__magnitude__isnull=False
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

@contextmanager
def target_hook_options(**opts):
    token = _target_hook_options.set(opts)
    try:
        yield
    finally:
        _target_hook_options.reset(token)

def target_post_save(
    target,
    created=True,
    **kwargs,
):
    """This hook runs following update of a target."""
    # work with kwargs + post save hook options stored in the target object itself
    # to figure out which options to use
    kwargs_defaults = dict(
        lookback_days_nle=7,
        first_det_min=-1,
        first_det_max=10,
        skip_vet_if_no_new_phot=False,
        known_associated_nle_id=None,
        skip_vetting=False
    )
    
    # pack these possible options into a single dict such that
    # 1. kwargs_defaults first
    # 2. then overwrite with the options passed via a context manager
    #    (stored in the target object)
    # 3. then finally overwrite with any explicitly passed in kwargs by the user
    opts = {**kwargs_defaults, **(_target_hook_options.get() or {}), **kwargs}

    # unpack the options dictionary into variables
    skip_vetting = opts.pop("skip_vetting")
    lookback_days_nle = opts.pop("lookback_days_nle")
    first_det_min = opts.pop("first_det_min")
    first_det_max = opts.pop("first_det_max")
    known_associated_nle_id = opts.pop("known_associated_nle_id")
    
    # then we can continue with the normal vetting
    messages = []
    tns_query_status = None
    logger.info("Target post save hook: %s created: %s vetting %s", target, created, not skip_vetting)
    if created and not skip_vetting:
        if target.extra_fields.get("MW E(B-V)") is None:
            coord = SkyCoord(target.ra, target.dec, unit="deg")
            try:
                mwebv = IrsaDust.get_query_table(
                    coord, section="ebv"
                )["ext SandF ref"][0]
            except Exception as e:
                logger.error(f"Error querying IRSA dust for {target.name}")
            else:
                update_or_create_target_extra(target, "MW E(B-V)", mwebv)
                messages.append(f"MW E(B-V) set to {mwebv:.4f}")

        # do the "basic" vetting (PS, MPC, Host association)
        # a new target needs its host / AGN tables for the target page even if
        # its point source or MPC score has already zeroed it, same as a user
        # vetting one target from the UI. setdefault rather than a keyword
        # because callers forward arbitrary kwargs into this hook
        opts.setdefault("stop_on_zero", False)
        basic_results = vet_basic(target.id, **opts)

        # the skymap queries in create_candidates_from_targets go through a separate
        # SQLAlchemy connection that can't see this target until Target.save's
        # transaction commits, so defer the NLE association and vetting until then
        # (on_commit runs immediately if we aren't inside a transaction)
        def _associate_and_vet():
            # given a known associated NLE we can associate that
            if known_associated_nle_id:
                nle = NonLocalizedEvent.objects.get(event_id=known_associated_nle_id)
                known_candidates = create_candidates_from_targets(
                    nle.sequences.last(),
                    target_ids=[target.id]
                )
                if len(known_candidates):
                    logger.info(f'Created a new EventCandidate from {target} and {nle}')

            # first, check for any existing candidates associated with this target
            ecs = EventCandidate.objects.filter(target=target)
            if ecs.exists():
                for cand in ecs:
                    # still vet this as a "new" candidate since the target recently had new
                    # info added and saved
                    vet_new_candidate(cand, basic_results=basic_results)

            # then check if this target is associated with any NLEs
            new_candidates = associate_nle_with_target(
                target,
                lookback_days_nle=lookback_days_nle,
                first_det_min=first_det_min,
                first_det_max=first_det_max,
            )

            if len(new_candidates):
                for cand in new_candidates:
                    vet_new_candidate(cand, basic_results=basic_results)

        transaction.on_commit(_associate_and_vet)

    for message in messages:
        logger.info(message)

    return messages, tns_query_status
