"""
Site-wide selection of which photometry scoring method candidate lists use.

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

The choice, and the parameters runs are configured with, live in the Django
cache rather than the database, and are **site-wide** -- one setting for the
whole site, exactly like ``agn_toggle``. It is a display preference: which of
two numbers already available at render time becomes the photometry factor.
Flipping it recomputes nothing and destroys nothing, so it is cheap to flip
back and forth (see :func:`scoring.util.get_event_candidate_scores`, which
computes the TROVE factor unconditionally and reads the KilonovaSCORER one from
a ``ScoreFactor`` row).

What is *not* site-wide is the expensive part. A KilonovaSCORER **run** costs
minutes to hours of grid loading and scoring, and belongs to one event: it is
launched per event from that event's Vet All, and its status
(:func:`get_kilonova_status`) stays keyed by ``nle_id``. So the method says
"rank with KilonovaSCORER where a score exists"; the run is what makes scores
exist, one event at a time.

The consequence is worth stating plainly, because it is visible in the UI: an
event with no finished run has no ``kilonova_score`` rows, so every one of its
candidates falls back to the TROVE photometry check no matter what this setting
says. :func:`get_scoring_settings` reports that, so a list is never silently
labelled with a method it is not actually using.

The file-based cache backend TROVE uses is shared across processes, so the
worker sees what the web request wrote.

Nothing here imports pandas or the scorer -- it is safe to import from
templates, settings-time code and tasks.
"""

from django.core.cache import cache

#: The two methods, as stored in the cache and posted by the form.
TROVE = "trove"
KILONOVA = "kilonova"

#: Display names, and the set of valid values -- membership here is what
#: validates a method.
PHOT_METHOD_LABELS = {
    TROVE: "TROVE photometry scoring",
    KILONOVA: "KilonovaSCORER",
}

#: The only entry of :data:`scoring.util.TRANSIENTS` whose photometry factor
#: KilonovaSCORER may supply. Lives here rather than in ``util`` so that it can
#: be read without pulling in astropy and the ORM.
KILONOVA_TRANSIENT = "KN"

#: The site-wide setting. ``None`` timeouts on the writes below mean "never
#: expire" -- this is a setting, not derived data, and must outlive the
#: 5-minute scored-candidate cache.
PHOT_METHOD_CACHE_KEY = "phot_scoring_method"

#: Run status is the one thing here that stays **per event**: a run scores one
#: event's candidates, so its progress and outcome belong to that event. The
#: key is this prefix plus the non-localized event id.
KILONOVA_STATUS_CACHE_PREFIX = "kilonova_scoring_status"

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
#: scoring method and nothing else. The numbers below are the paper's
#: recommended values, plus automatic per-candidate grid selection; letting a
#: vetter change them in a form mostly gave worse results than the defaults.
#: Change a value here to change it for every run.
DEFAULT_KILONOVA_PARAMS = {
    "grid_path": "",        # "" means pick the nearest grid per candidate distance
    "max_grid_offset": 0.5,
    # What to do with a candidate further than max_grid_offset (as a fraction of
    # its own distance) from every rung of the ladder. Read by
    # scoring.tasks.async_kilonova_score, which compares against these literals:
    #   "score"    -- score against the nearest rung anyway
    #   "skip"     -- leave it unscored, recording the offset as the skip reason
    #   "generate" -- generate a rung at its distance first (~30 min each)
    # A grid is distance-specific because the k-correction and time dilation
    # change the SHAPE of the magnitude distribution per band and epoch, so a
    # badly matched rung cannot be corrected for after the fact -- see
    # KilonovaScorer/generate_ladder.py.
    "grid_offset_action": "score",
    "dt_min": 0.0,
    # The grids simulate 0-10 days (simulation.py: TIME = linspace(0, 10, 1000)),
    # so an epoch past 10 days falls outside every time bin the scorer has and is
    # dropped after being fetched and converted. Cutting here instead.
    "dt_max": 10.0,
    "snr_min": None,
    # One detection in one band is enough. P_tail_KNe is evaluated per
    # observation against the simulated population, so a single point already
    # produces a score; further observations sharpen it through the sequential
    # update rather than being a precondition for it. Scoring sparse light
    # curves is the method's purpose -- GW follow-up rarely produces dense ones
    # -- so a higher floor here just discards candidates it was built to rank.
    # (Was 3, which skipped 199 of 457 candidates of S251112cm.)
    "min_obs": 1,
    "min_bands": 1,
    "map_wide_bands": False,
    "mode": "survey",
    # How many bands one grid read may cover -- a MEMORY control with a brutal
    # non-linear cost in TIME. Do not lower it without measuring both.
    #
    # Measured on S251112cm (445 candidates, 13 distinct bands), packing
    # candidates by band set:
    #
    #     max_bands_per_load    loads    band-reads   peak RSS   load time
    #              3             125         503       3.08 GB      34 s
    #              6              11          66       3.80 GB      60 s
    #             12               2          22       > 5.5 GB (aborted)
    #
    # Dropping 3 -> the memory saving is 0.7 GB, because the peak is dominated
    # by pyarrow's scan of the whole 3.84 GB file, NOT by the band count -- a
    # ONE-band load also peaks at 3.08 GB. The cost is 125 loads instead of 11,
    # i.e. ~71 minutes of pure I/O instead of ~11. Packing fragments badly at 3
    # because the common bands recur in nearly every chunk: atlasc was re-read
    # 117 times, atlaso 109 times.
    #
    # 6 is the measured sweet spot under ~6.5 GB of usable RAM. 12-13 bands
    # exceeds it, which matches the 12.9 GB figure in _chunk_by_bands' own
    # docstring; on WSL that is not a clean failure, the VM's OOM killer picks
    # an arbitrary victim and has taken the VS Code server with it.
    #
    # Band-partitioning the parquet would remove the trade-off entirely: the
    # fixed ~19 s full-file scan per load is what makes extra loads expensive.
    "max_bands_per_load": 6,
    "time_bin_width": 0.2,
    # Monte-Carlo draws from the noise-convolved PPD, per observation. This is
    # the single biggest cost in the scorer -- kde.resample() measured 10.65 ms
    # at 50000 against 2.26 ms at 10000, and it dominates per-observation time.
    #
    # The accuracy cost is negligible: the MC standard error on F_hat is
    # sqrt(F(1-F)/n), i.e. 0.0022 at 50000 and 0.0050 at 10000, against a
    # characteristic P_tail uncertainty of ~0.1 (paper Appendix A). The
    # reference notebook ran at 5000. Combined with the searchsorted rewrite in
    # predictive_tail_kde this took a full observation from 17.7 ms to 3.8 ms.
    "n_kde_sim": 10000,
    "min_sim_points": 20,
    # k_ABC: the ROPE half-width of the ABC acceptance kernel (paper eq. 18).
    # This one really does change the score: since the ABC penalty was added,
    # it decides which candidates get zeroed, not just what gets reported.
    #
    # Must be calibrated to the grid size: as the grid shrinks, fewer draws
    # satisfy the ROPE and the accepted set collapses spuriously. Paper
    # Appendix A measures the minimum viable threshold on AT 2017gfo as
    # k_ABC,min = 1.5 for the gold-standard 10^5 set, and 2.0 / 2.5 / 2.0 for
    # the high (10^4) / medium (10^3) / low (10^2) sets. Our grids are 10^4
    # (generate_ladder.N_SIM), so 2.0 is the calibrated value. Dropping to a
    # 10^3 grid to save space would require raising this to 2.5.
    "overlap_k": 2.0,
    # Seeds every Monte-Carlo draw, per candidate, so a re-run reproduces the
    # same scores and a candidate's score does not depend on what was scored
    # before it. Set to None for the scorer's original non-deterministic
    # behaviour.
    "random_state": 42,
}

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
    # score_event_by_distance only -- the memory guard on grid reads
    "max_bands_per_load",
    # passed through score_candidate
    "time_bin_width",
    "random_state",
    # passed through to kilonovascorer_v3
    "n_kde_sim",
    "min_sim_points",
    "overlap_k",
)


def get_phot_method() -> str:
    """The photometry scoring method candidate lists rank with, site-wide."""
    method = cache.get(PHOT_METHOD_CACHE_KEY, TROVE)
    return method if method in PHOT_METHOD_LABELS else TROVE


def get_kilonova_params() -> dict:
    """The parameters KilonovaSCORER runs are configured with.

    A fresh copy of :data:`DEFAULT_KILONOVA_PARAMS` -- there is nowhere else for
    these to come from, since the UI exposes the method and nothing else. It
    stays a function rather than callers touching the constant directly so that
    a caller mutating the returned dict cannot corrupt every later run.
    """
    return dict(DEFAULT_KILONOVA_PARAMS)


def score_event_kwargs(params: dict | None = None) -> dict:
    """The subset of the stored parameters ``score_event`` actually accepts.

    Grid selection (``grid_path``) and the offset guard are TROVE-side policy
    applied by the task before scoring starts, so they are filtered out here --
    see :data:`_SCORE_EVENT_KEYS` for why this is an allowlist.
    """
    params = params if params is not None else dict(DEFAULT_KILONOVA_PARAMS)
    return {k: v for k, v in params.items() if k in _SCORE_EVENT_KEYS}


def set_phot_method(method: str) -> None:
    """Persist the photometry scoring method, site-wide."""
    if method not in PHOT_METHOD_LABELS:
        raise ValueError(f"Unknown photometry scoring method: {method!r}")
    cache.set(PHOT_METHOD_CACHE_KEY, method, None)
    invalidate_scored_candidates()


def get_kilonova_status(nle_id=None) -> dict | None:
    """Progress/outcome of one event's most recent KilonovaSCORER run.

    Written by :func:`scoring.tasks.async_kilonova_score`. Keys: ``state``
    (``running`` / ``done`` / ``error``), ``event_id``, ``message``, and
    ``scored`` / ``total`` / ``finished_at`` / ``params`` once a run has
    finished. ``None`` if this event has never been run.
    """
    if nle_id is None:
        return None
    return cache.get(f"{KILONOVA_STATUS_CACHE_PREFIX}_{nle_id}")


#: How long a ``running`` status may survive without being superseded. A
#: finished run's status is a result and never expires, but ``running`` is a
#: claim about a live process, and nothing guarantees the process outlives it:
#: an OOM-killed worker, a deleted task, or a restart leaves the claim behind
#: with no writer left to correct it. That is not hypothetical -- deleting an
#: unclaimed task left one event reporting "Scoring candidates..." indefinitely,
#: long after there was anything to score. Six hours is well past any real run.
RUNNING_STATUS_TTL = 6 * 60 * 60


def set_kilonova_status(nle_id, **status) -> None:
    """Record progress for one event's run. Runs on other events are untouched."""
    if nle_id is None:
        return
    timeout = RUNNING_STATUS_TTL if status.get("state") == "running" else None
    cache.set(f"{KILONOVA_STATUS_CACHE_PREFIX}_{nle_id}", status, timeout)


def get_scoring_settings(nle_id=None) -> dict:
    """The settings behind the scores a candidate list is currently displaying.

    Answers "what produced these numbers?", which is not the same question as
    "what is selected?" -- the two come apart in a way that matters:

    * the method and the AGN toggle are applied at **render** time, so they
      describe the list you are looking at right now;
    * ``kilonova_score`` rows come from a **past run** on this event, so the
      list can be set to KilonovaSCORER while showing scores produced before
      the run finished -- or before any run happened at all, in which case
      every candidate silently falls back to the TROVE check.

    ``scores_are_kilonova`` answers "is this list really using KilonovaSCORER":
    true only when the method is selected *and* a run has finished.

    Cache-only, no database access, so this stays importable from templates and
    tasks. Callers that want per-candidate coverage counts (how many rows
    actually have a score) add them on top -- see the ``scoring_settings``
    template tag.
    """
    method = get_phot_method()
    status = get_kilonova_status(nle_id)
    finished = bool(status and status.get("state") == "done")

    return {
        "nle_id": nle_id,
        "phot_method": method,
        "phot_method_label": PHOT_METHOD_LABELS[method],
        "agn_toggle": cache.get("agn_toggle", True),
        "kilonova_selected": method == KILONOVA,
        "kilonova_status": status,
        "kilonova_finished": finished,
        "scores_are_kilonova": method == KILONOVA and finished,
        # The parameters only mean anything when the displayed scores came from
        # a run; showing defaults next to TROVE scores would imply they were
        # used to produce them.
        "params": (status or {}).get("params") if finished else None,
    }


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
