"""
Per-event selection of which photometry scoring method the candidate list uses.

TROVE ranks a candidate by multiplying a set of subscores together (see
:func:`scoring.util.get_event_candidate_scores`). One of those factors is the
*photometry* score. There are now two ways to compute it:

``trove``
    The original: check the fitted peak luminosity, peak time and decay rate
    against the allowed parameter ranges for the transient class
    (``vet_bns.PARAM_RANGES`` and friends). The factor is 1 if every value is
    in range and :data:`scoring.vet_phot.PHOT_SCORE_MIN` if any is not.

``kilonova``
    KilonovaSCORER's cumulative P_tail_KNe in [0, 1] -- how consistent the
    light curve is with a grid of simulated kilonovae. Computed by
    :mod:`scoring.kilonova_scoring`, which is far too slow to run inline, so it
    is run once per event by :func:`scoring.tasks.async_kilonova_score` and
    stored as a ``ScoreFactor`` row keyed :data:`KILONOVA_SCORE_KEY`.

    It replaces the photometry factor of the plain **KN** score only. An SSM
    event also gets a KN-in-SN and a super-KN score, and those keep the TROVE
    check: the grids are populations of bare kilonovae, so P_tail_KNe would
    penalise the very excess flux that defines those two classes. This is
    enforced by :data:`KILONOVA_TRANSIENT` below, in the scoring loop -- *not*
    by restricting when the method can be chosen, so the toggle is free to be
    on for any event.

The choice, and the parameters the KilonovaSCORER run was configured with, live
in the Django cache rather than the database, and are keyed **per non-localized
event**. Unlike ``agn_toggle``, which is a cheap site-wide display flag, a
KilonovaSCORER run costs minutes to hours of grid loading and scoring for one
event's candidates. Making the choice site-wide would mean selecting it on one
GW event silently changed how every other event ranked -- while only the event
you were looking at actually had ``kilonova_score`` rows, so every other event
would fall back to TROVE anyway. The setting therefore belongs to the event
whose candidates were scored.

Pass ``nle_id`` to every accessor. ``None`` means "no event in context" -- the
unfiltered candidate list -- which always uses TROVE, since a cross-event list
has no single run behind it.

The file-based cache backend TROVE uses is shared across processes, so the
worker sees what the web request wrote.

Nothing here imports pandas or the scorer -- it is safe to import from
templates, settings-time code and tasks.
"""

from django.core.cache import cache

#: The two methods, as stored in the cache and posted by the form.
TROVE = "trove"
KILONOVA = "kilonova"

PHOT_METHOD_CHOICES = [
    (TROVE, "TROVE photometry scoring"),
    (KILONOVA, "KilonovaSCORER"),
]
PHOT_METHOD_LABELS = dict(PHOT_METHOD_CHOICES)

#: The only entry of :data:`scoring.util.TRANSIENTS` whose photometry factor
#: KilonovaSCORER may supply. Lives here rather than in ``util`` so that it can
#: be read without pulling in astropy and the ORM.
KILONOVA_TRANSIENT = "KN"

#: Cache key prefixes. Each is suffixed with the non-localized event id, so one
#: event's method and run parameters cannot leak into another's. ``None``
#: timeouts below mean "never expire" -- these are settings, not derived data,
#: and must outlive the 5-minute scored-candidate cache.
PHOT_METHOD_CACHE_PREFIX = "phot_scoring_method"
KILONOVA_PARAMS_CACHE_PREFIX = "kilonova_scoring_params"
KILONOVA_STATUS_CACHE_PREFIX = "kilonova_scoring_status"


def _key(prefix: str, nle_id) -> str:
    """Cache key for one event's setting."""
    return f"{prefix}_{nle_id}"

#: Version counter mixed into every scored-candidate cache key. Bumping it
#: invalidates all of them at once, which is the only way to expire a whole
#: family of keys on a backend with no pattern deletion (FileBasedCache).
SCORED_CACHE_VERSION_KEY = "event_candidates_scored_version"

#: ``ScoreFactor`` keys written by the KilonovaSCORER run.
KILONOVA_SCORE_KEY = "kilonova_score"
KILONOVA_SKIP_KEY = "kilonova_skip_reason"

#: The parameters every KilonovaSCORER run uses. Keys match the keyword
#: arguments of :func:`scoring.kilonova_scoring.score_event` (which forwards the
#: last four on to ``kilonovascorer_v3``), so the dict can be splatted straight
#: in.
#:
#: **This is the only place these are set.** The UI offers the choice of
#: scoring method and nothing else: the numbers below are the paper's fiducial
#: values plus automatic per-candidate grid selection, and a vetter picking
#: between them in a modal was fourteen ways to get a worse answer than the
#: default. Change a value here to change it for every run.
DEFAULT_KILONOVA_PARAMS = {
    "grid_path": "",        # "" means pick the nearest grid per candidate distance
    "max_grid_offset": 0.5,
    "grid_offset_action": "score",
    "dt_min": 0.0,
    # The grids simulate 0-10 days (simulation.py: TIME = linspace(0, 10, 1000)),
    # so an epoch past 10 days falls outside every time bin the scorer has and is
    # dropped after being fetched and converted. Cutting here instead.
    "dt_max": 10.0,
    "snr_min": None,
    "min_obs": 3,
    "min_bands": 1,
    "map_wide_bands": False,
    "mode": "survey",
    "time_bin_width": 0.2,
    "k_near": 1.5,
    "n_kde_sim": 50000,
    "min_sim_points": 20,
    "overlap_k": 2.0,
    # Seeds every Monte-Carlo draw, per candidate, so a re-run reproduces the
    # same scores and a candidate's score does not depend on what was scored
    # before it. Set to None for the scorer's original non-deterministic
    # behaviour.
    "random_state": 42,
}

#: The valid values of ``grid_offset_action`` above -- what to do with a
#: candidate whose distance is further than ``max_grid_offset`` (as a fraction
#: of its own distance) from every rung of the grid ladder. Read by
#: :func:`scoring.tasks.async_kilonova_score`; no longer rendered as form
#: choices, kept as the enumeration and its rationale.
#:
#: See ``KilonovaScorer/generate_ladder.py`` for why a grid is distance-specific
#: at all: the k-correction and time dilation change the SHAPE of the magnitude
#: distribution per band and epoch, so a badly matched rung cannot be corrected
#: for after the fact.
GRID_OFFSET_ACTIONS = [
    ("score", "Score against the nearest rung anyway"),
    ("skip", "Leave the candidate unscored, recording the offset"),
    ("generate", "Generate a new rung at the candidate's distance, then score"),
]

#: Keys forwarded to :func:`scoring.kilonova_scoring.score_event` -- an explicit
#: allowlist, NOT "everything else". ``score_event`` takes ``**kwargs`` and
#: hands them to ``kilonovascorer_v3``, so a stray key here does not raise at
#: the boundary; it travels all the way down and blows up mid-run, after the
#: multi-GB grid load. Anything that is a TROVE-side policy rather than a
#: scorer argument (grid selection, the offset guard) must stay out.
_SCORE_EVENT_KEYS = (
    # score_event's own arguments
    "dt_min",
    "dt_max",
    "snr_min",
    "min_obs",
    "min_bands",
    "map_wide_bands",
    "mode",
    # passed through score_candidate
    "time_bin_width",
    "random_state",
    # passed through to kilonovascorer_v3
    "k_near",
    "n_kde_sim",
    "min_sim_points",
    "overlap_k",
)


def get_phot_method(nle_id=None) -> str:
    """The photometry scoring method this event's candidate list ranks with.

    ``nle_id=None`` is the unfiltered, cross-event candidate list, which always
    uses TROVE: no single KilonovaSCORER run stands behind a mixed list.
    """
    if nle_id is None:
        return TROVE
    method = cache.get(_key(PHOT_METHOD_CACHE_PREFIX, nle_id), TROVE)
    return method if method in PHOT_METHOD_LABELS else TROVE


def get_phot_method_label(nle_id=None) -> str:
    return PHOT_METHOD_LABELS[get_phot_method(nle_id)]


def get_kilonova_params(nle_id=None) -> dict:
    """The parameters this event's last KilonovaSCORER run was configured with.

    Always a complete dict: stored values are layered over the defaults, so a
    parameter added to :data:`DEFAULT_KILONOVA_PARAMS` after someone last saved
    the form does not come back missing.
    """
    params = dict(DEFAULT_KILONOVA_PARAMS)
    if nle_id is not None:
        params.update(cache.get(_key(KILONOVA_PARAMS_CACHE_PREFIX, nle_id)) or {})
    return params


def score_event_kwargs(params: dict | None = None) -> dict:
    """The subset of the stored parameters ``score_event`` actually accepts.

    Grid selection (``grid_path``) and the offset guard are TROVE-side policy
    applied by the task before scoring starts, so they are filtered out here --
    see :data:`_SCORE_EVENT_KEYS` for why this is an allowlist.
    """
    params = params if params is not None else dict(DEFAULT_KILONOVA_PARAMS)
    return {k: v for k, v in params.items() if k in _SCORE_EVENT_KEYS}


def set_phot_method(method: str, nle_id, params: dict | None = None) -> None:
    """Persist the method (and, for KilonovaSCORER, its parameters) for one event."""
    if method not in PHOT_METHOD_LABELS:
        raise ValueError(f"Unknown photometry scoring method: {method!r}")
    if nle_id is None:
        raise ValueError("A photometry scoring method belongs to a non-localized event")
    cache.set(_key(PHOT_METHOD_CACHE_PREFIX, nle_id), method, None)
    if params is not None:
        cache.set(_key(KILONOVA_PARAMS_CACHE_PREFIX, nle_id), dict(params), None)
    invalidate_scored_candidates()


def get_kilonova_status(nle_id=None) -> dict | None:
    """Progress/outcome of this event's most recent KilonovaSCORER run.

    Written by :func:`scoring.tasks.async_kilonova_score`. Keys: ``state``
    (``running`` / ``done`` / ``error``), ``event_id``, ``message``, and
    ``scored`` / ``total`` once a run has finished. ``None`` if this event has
    never been run.
    """
    if nle_id is None:
        return None
    return cache.get(_key(KILONOVA_STATUS_CACHE_PREFIX, nle_id))


def set_kilonova_status(nle_id, **status) -> None:
    """Record progress for one event's run. Runs on other events are untouched."""
    if nle_id is None:
        return
    cache.set(_key(KILONOVA_STATUS_CACHE_PREFIX, nle_id), status, None)


def scored_candidates_cache_key(filter_key: str, agn_toggle, phot_method: str | None = None) -> str:
    """Cache key for a scored, sorted candidate list.

    The photometry method is part of the key for the same reason ``agn_toggle``
    is: it changes every score in the list. The version prefix is what lets
    :func:`invalidate_scored_candidates` drop them all.

    ``filter_key`` already encodes which event is being viewed, so the caller
    passes the method it resolved for that event; ``None`` falls back to TROVE,
    which is what a cross-event list uses.
    """
    if phot_method is None:
        phot_method = TROVE
    version = cache.get(SCORED_CACHE_VERSION_KEY, 0)
    return f"event_candidates_scored_v{version}_{filter_key}_{agn_toggle}_{phot_method}"


def invalidate_scored_candidates() -> None:
    """Expire every cached scored candidate list.

    Called when the scoring inputs change -- the method, its parameters, or a
    finished KilonovaSCORER run that wrote new ``ScoreFactor`` rows.
    """
    try:
        cache.incr(SCORED_CACHE_VERSION_KEY)
    except ValueError:  # key not set yet
        cache.set(SCORED_CACHE_VERSION_KEY, 1, None)
