import logging
from typing import Optional
from astropy.time import Time, TimeDelta
from astropy import units as u
import numpy as np
import pandas as pd
from scipy.stats import norm

from .scoring import (
    update_score_factor,
    delete_score_factor,
    host_distance_match,
    get_distance_score,
    skymap_association,
    clean_host_df,
    _localization_from_name,
)
from .vet_basic import vet_basic
from .vet_phot import (
    _get_post_disc_phot,
    _get_pre_disc_phot,
    PHOT_SCORE_MIN,
)

from trove_targets.models import Target
from tom_nonlocalizedevents.models import (
    EventCandidate,
    NonLocalizedEvent,
    EventSequence,
)

logger = logging.getLogger(__name__)

PARAM_RANGES = dict(
    t_pre=0,  # baseline = all photometry before the GW trigger (dt < 0)
    t_post=400,  # flare window: 0-400 days after the GW trigger -- the upper edge
    # of the "long-delay" flare population of Darc et al. 2025 (PhRvD 112, 063019),
    # as reported in Vieira et al. 2026 (arXiv:2603.17009) Appendix A; see module
    # docstring for the short-delay (<=~50 day) vs. long-delay (~50-400 day) split
    min_baseline_pts=1,  # minimum pre-trigger points in a filter to compute a
    # baseline at all. Deliberately not raised to demand a "robust" baseline (e.g.
    # 5+): TROVE photometry is often sparse, and KN scoring already commits to
    # giving a reasonable score off as little as 1-2 points rather than refusing
    # to score at all -- see fit_agn_baseline's docstring for how the same
    # median+MAD formula degrades gracefully down to n=1 without a separate
    # code path for "not enough points."
    flare_sigma_thresh=5.0,  # reference significance for "confident flare" in
    # flare_confidence_score, matches PREDETECTION_SNR_THRESHOLD's 5-sigma
    # convention elsewhere in vet_phot.py
    flare_score_center_frac=0.5,  # sigmoid midpoint, as a fraction of flare_sigma_thresh
    flare_score_width_frac=0.25,  # sigmoid transition width, as a fraction of flare_sigma_thresh
    agn_boost_multiplier=5.0,
)


def flare_confidence_score(
    significance: float,
    thresh: float,
    floor: float = PHOT_SCORE_MIN,
    center_frac: float = 0.5,
    width_frac: float = 0.25,
) -> float:
    """
    Map a brightening significance (in sigma, as returned by `detect_flare`) onto a
    continuous [floor, 1.0] confidence score via a normal-CDF sigmoid, instead of a
    hard pass/fail cut at `thresh`.

    The sigmoid is centered at `center_frac * thresh` with transition width
    `width_frac * thresh`, so with the defaults a significance of 0 sits near
    `floor`, `thresh` itself sits near (but not at) the ceiling, and significances
    well above `thresh` saturate at 1.0 -- deliberately similar to the old hard-cut
    behavior at exactly `thresh`, but without the discontinuity.
    """
    center = center_frac * thresh
    width = max(width_frac * thresh, 1e-6)
    raw = norm.cdf(significance, loc=center, scale=width)
    return float(np.clip(floor + (1.0 - floor) * raw, floor, 1.0))


def fit_agn_baseline(prephot: Optional[pd.DataFrame], min_baseline_pts: int = 1) -> dict:
    """
    Characterize the pre-merger "typical variability envelope" of the candidate host
    AGN, per filter, without assuming any particular variability model.

    Uses the robust median magnitude and 1.4826*MAD (a robust estimator of the
    standard deviation) of the pre-merger photometry in each filter. This is
    deliberately model-agnostic: it says nothing about *why* an AGN varies (DRW, PSD,
    etc.), only how much it has historically varied, which is all that's needed to
    flag a later excursion as unusual.

    Degrades gracefully with sparse photometry rather than refusing to score, the
    same choice TROVE's KN scoring already makes (a kilonova can be scored off a
    single detection): with few points the MAD estimate itself shrinks toward 0
    (at n=1 it's exactly 0, at n=2 both points are equidistant from their median so
    it's still 0), but it's floored at the median measurement error a few lines
    below, so the "baseline" for a thinly-sampled filter is effectively just its
    most recent pre-merger point plus its own reported uncertainty -- a plain
    two-point brightening comparison, not a claim of having characterized the AGN's
    long-term variability. `min_baseline_pts` exists to cut this off entirely (0
    points = no baseline for that filter at all), not to gate "how many points
    until this is trustworthy" -- there's no such threshold built into the formula
    itself, only progressively wider uncertainty as points get scarcer.

    Parameters
    ----------
    prephot : pd.DataFrame or None
        Pre-merger photometry, as returned by `vet_phot._get_pre_disc_phot`. Expected
        columns: mag, magerr, filter, upperlimit.
    min_baseline_pts : int
        Minimum number of detections required in a filter before it gets a baseline
        entry at all. Low by default (see module docstring) -- this is a floor
        against zero data, not a robustness gate.

    Returns
    -------
    dict mapping filter -> dict(mag=<median mag>, std=<robust scatter, floored at
    measurement error>, n=<n points>)
    Filters with fewer than `min_baseline_pts` points are simply absent from the
    returned dict.
    """
    baseline = {}
    if prephot is None or not len(prephot):
        return baseline

    phot = prephot[~prephot.upperlimit]
    for filt, group in phot.groupby("filter"):
        mags = group.mag.to_numpy(dtype=float)
        if len(mags) < min_baseline_pts:
            continue
        median_mag = float(np.median(mags))
        robust_std = 1.4826 * float(np.median(np.abs(mags - median_mag)))
        # floor the scatter at the median measurement error so a baseline that
        # happens to be tightly time-sampled (MAD -> 0) doesn't make every later
        # point look artificially significant
        median_err = float(np.median(group.magerr.to_numpy(dtype=float)))
        robust_std = max(robust_std, median_err)
        baseline[filt] = dict(mag=median_mag, std=robust_std, n=int(len(mags)))
    return baseline


def _flare_significance_series(
    postphot: Optional[pd.DataFrame], baseline: dict
) -> Optional[pd.DataFrame]:
    """
    Shared helper for `detect_flare` and `estimate_flare_extent`: attach a
    brightening-significance column (in sigma, baseline vs. observed mag) to every
    detection in `postphot` whose filter has a fitted `baseline`. Returns None if
    nothing qualifies.
    """
    if postphot is None or not len(postphot) or not baseline:
        return None

    phot = postphot[~postphot.upperlimit]
    phot = phot[phot["filter"].isin(baseline.keys())]
    if not len(phot):
        return None

    significance = [
        (baseline[row["filter"]]["mag"] - row.mag)
        / np.sqrt(baseline[row["filter"]]["std"] ** 2 + row.magerr**2)
        for _, row in phot.iterrows()
    ]
    return phot.assign(significance=significance)


def detect_flare(
    postphot: Optional[pd.DataFrame],
    baseline: dict,
    sigma_thresh: float = 5.0,
):
    """
    Look for a significant brightening excursion above the AGN baseline within the
    post-merger photometry.

    Only brightening (flux excess relative to baseline) counts as a candidate flare,
    matching the physical picture of an accretion-episode re-brightening; a dimming
    excursion is not flagged.

    Parameters
    ----------
    postphot : pd.DataFrame or None
        Post-merger photometry in the scoring window, as returned by
        `vet_phot._get_post_disc_phot`.
    baseline : dict
        Output of `fit_agn_baseline`.
    sigma_thresh : float
        Not used to filter here -- the caller compares the returned significance to
        this threshold. Kept as an argument for symmetry / future use.

    Returns
    -------
    (max_significance, best_row): the largest brightening significance found and its
    corresponding photometry row (a pandas Series), or (np.nan, None) if nothing in
    postphot has a filter with a fitted baseline.
    """
    phot = _flare_significance_series(postphot, baseline)
    if phot is None:
        return np.nan, None
    idx = phot.significance.idxmax()
    return float(phot.significance.loc[idx]), phot.loc[idx]


def estimate_flare_extent(
    postphot: Optional[pd.DataFrame],
    baseline: dict,
    extent_sigma_thresh: float = 3.0,
):
    """
    Estimate a candidate flare's delay-from-merger and duration, for comparing
    against the timing envelopes of published BBH-flare emission models
    (`flare_shape_score`).

    `postphot` must carry a `dt` column (days since the GW trigger), as produced by
    `vet_phot._get_post_disc_phot`.

    Returns
    -------
    (delay_days, duration_days) : delay_days is `dt` of the single most significant
    brightening point (same point `detect_flare` would report). duration_days is the
    span between the earliest and latest points *anywhere* in postphot with
    significance >= `extent_sigma_thresh` -- deliberately looser than
    `detect_flare`'s usual 5-sigma detection threshold, since this is meant to
    bracket the whole elevated episode rather than find its single most significant
    point. duration_days is None (rather than 0) when fewer than two such points
    exist, since ground-based survey cadence is usually too sparse to trust a
    duration estimate from a single point -- `flare_shape_score` treats that as
    "unconstrained", not "instantaneous". Both are None if no flare is detectable at
    all (mirrors `detect_flare`'s (nan, None) for "no data", but None here since
    delay/duration aren't naturally NaN-typed floats read back out of ScoreFactor).
    """
    phot = _flare_significance_series(postphot, baseline)
    if phot is None:
        return None, None

    idx = phot.significance.idxmax()
    delay_days = float(phot.dt.loc[idx])

    elevated = phot[phot.significance >= extent_sigma_thresh]
    if len(elevated) < 2:
        return delay_days, None
    duration_days = float(elevated.dt.max() - elevated.dt.min())
    return delay_days, duration_days


# (delay_lo, delay_hi, duration_lo, duration_hi) envelopes, in observed-frame days
# since the GW trigger, for the three BBH-in-AGN-disk emission mechanisms Darc et al.
# 2025 (PhRvD 112, 063019) fit against real long-term photometry of a GW/AGN-flare
# candidate (S231206cc). Bounds are deliberately generous envelopes around each
# paper's own quoted numbers, not sharp physical limits -- ground-based survey
# cadence isn't good enough to trust more precision than "roughly consistent with
# this channel":
#   mck19  -- McKernan et al. 2019 (ApJL 884, L50): ram-pressure-stripped Hill
#             sphere. Delay ranges from <3 days (kick velocity v_k >~ 500 km/s) to
#             ~300 days (v_k <~ 100 km/s); duration ~1-100 days.
#   jrr_i  -- Rodriguez-Ramirez et al. 2025 (PhRvD 111, 083020): jet-cocoon thermal
#             diffusion. Needs v_k >~ 200 km/s to form an efficient jet at all; delay
#             ~50-100+ days, duration ~20-100+ days (both open-ended upward, capped
#             here at a generous but finite value).
#   tgw24  -- Tagawa et al. 2024 (ApJ 966, 21): jet breakout + shock cooling. Delay
#             favors <~50 days for close-in mergers (<0.005 pc, though those are
#             usually too short to detect) out to 40-300 days for ~1 pc mergers;
#             duration ~10-200+ days.
FLARE_SHAPE_MODELS = dict(
    mck19=dict(delay=(0.0, 300.0), duration=(1.0, 100.0)),
    jrr_i=dict(delay=(50.0, 150.0), duration=(20.0, 150.0)),
    tgw24=dict(delay=(0.0, 300.0), duration=(10.0, 250.0)),
)


def _box_edge_score(x: float, lo: float, hi: float, margin: float, floor: float) -> float:
    """1.0 inside [lo, hi]; falls off smoothly outside via margin/(margin+excess)."""
    if lo <= x <= hi:
        return 1.0
    excess = (lo - x) if x < lo else (x - hi)
    return float(np.clip(margin / (margin + excess), floor, 1.0))


def flare_shape_scores_by_model(
    delay_days: float,
    duration_days: Optional[float],
    models: dict = FLARE_SHAPE_MODELS,
    floor: float = PHOT_SCORE_MIN,
) -> dict:
    """
    Score how consistent an observed flare's timing is with *each* published
    BBH-in-AGN-disk emission model in `models` individually, rather than
    collapsing straight to a single aggregate -- TROVE has no way to know a
    priori which mechanism (if any) applies to a given candidate, since that
    depends on kick velocity, SMBH mass, and merger location within the disk, so
    the per-model breakdown is worth keeping around to see (e.g. on the
    candidate page) which specific published picture, if any, a candidate
    actually resembles.

    Each model's fit is `delay_score * duration_score`, each of which is 1.0 inside
    that model's envelope and falls off smoothly (not a hard cut) outside it, via
    `_box_edge_score` with the box's own width as the falloff scale.

    `duration_days` may be None (too little post-merger photometry above the
    extent-detection threshold in `estimate_flare_extent` to bracket a span) -- in
    that case only delay is checked, since a single data point can't rule a model's
    duration range in or out.

    Returns
    -------
    dict mapping model name (e.g. "mck19") -> score.
    """
    scores = {}
    for name, model in models.items():
        delay_lo, delay_hi = model["delay"]
        score = _box_edge_score(delay_days, delay_lo, delay_hi, delay_hi - delay_lo, floor)
        if duration_days is not None:
            dur_lo, dur_hi = model["duration"]
            score *= _box_edge_score(duration_days, dur_lo, dur_hi, dur_hi - dur_lo, floor)
        scores[name] = score
    return scores


def flare_shape_score(
    delay_days: float,
    duration_days: Optional[float],
    models: dict = FLARE_SHAPE_MODELS,
    floor: float = PHOT_SCORE_MIN,
) -> float:
    """
    Score how consistent an observed flare's timing is with *any one* of the
    published BBH-in-AGN-disk emission models in `models`, rather than a single
    one-size-fits-all cut. Takes the best-fitting model's score (see
    `flare_shape_scores_by_model`), not a penalized combination across all three.
    """
    scores = flare_shape_scores_by_model(delay_days, duration_days, models, floor)
    return max(scores.values(), default=floor)


def vet_bbh(
    target_id: int,
    nonlocalized_event_name: Optional[str] = None,
    param_ranges: dict = PARAM_RANGES,
):
    logger.info("Running BBH vetting (AGN-flare vetting)")

    # get the correct EventCandidate object for this target_id and nonlocalized event
    nonlocalized_event = NonLocalizedEvent.objects.get(event_id=nonlocalized_event_name)
    event_candidate = EventCandidate.objects.get(
        nonlocalizedevent_id=nonlocalized_event.id, target_id=target_id
    )
    target = Target.objects.get(id=target_id)

    ## check skymap association
    if np.isfinite(param_ranges["t_post"]):
        gw_disc_date = (
            EventSequence.objects.filter(  # GW discovery time
                nonlocalizedevent_id=nonlocalized_event.id
            )
            .last()
            .details["time"]
        )
        max_time = Time(gw_disc_date) + TimeDelta(param_ranges["t_post"] * u.day)
    else:  # just use current time
        max_time = Time.now()
    skymap_score = skymap_association(
        nonlocalized_event_name, target_id, max_time=max_time
    )
    update_score_factor(event_candidate, "skymap_score", skymap_score)

    # the skymap and distance scores are only valid for one localization, so
    # record which one produced them (matches vet_kn.py / vet_super_kn.py)
    localization = _localization_from_name(nonlocalized_event_name, max_time=max_time)
    update_score_factor(event_candidate, "localization_id", localization.id)

    ## get dataframes of potential hosts / AGN
    host_df, agn_df, keep_vetting = vet_basic(event_candidate.target.id)
    if not keep_vetting:
        # PS or MPC association already zeroed this candidate's score and the
        # host/AGN associations were not (re)performed -- same as vet_kn.py
        return

    # some cleanup before distance scoring
    host_df = clean_host_df(host_df)

    ## distance scoring
    if target.redshift is not None and not np.isnan(target.redshift):
        # use target redshift, so no need to compute distance scores for galaxies
        host_score, host_name = get_distance_score(
            host_df, target_id, nonlocalized_event_name
        )
        update_score_factor(event_candidate, "host_distance_score", host_score)

    elif len(host_df) != 0:
        # then run the distance comparison for each of these hosts
        host_df = host_distance_match(host_df, target_id, nonlocalized_event_name)

        host_score, host_name, host_catalog = get_distance_score(
            host_df, target_id, nonlocalized_event_name
        )
        update_score_factor(event_candidate, "host_distance_score", host_score)
        update_score_factor(event_candidate, "host_name", host_name)
        update_score_factor(event_candidate, "host_catalog", host_catalog)

    else:
        # if no target redshift is known and no hosts are found, we don't want
        # to bias the final score (host may just be too far / undetected)
        delete_score_factor(event_candidate, "host_distance_score")
        delete_score_factor(event_candidate, "host_name")
        delete_score_factor(event_candidate, "host_catalog")

    ## AGN-catalog boost: association can only help, never hurt, since a missing
    ## Milliquas/RomaBzcat match may just mean that AGN hasn't been ingested yet
    if len(agn_df) != 0:
        agn_score = param_ranges["agn_boost_multiplier"]
    else:
        agn_score = 1.0
    update_score_factor(event_candidate, "agn_score", agn_score)

    ## photometric flare scoring
    prephot = _get_pre_disc_phot(
        target_id=target.id,
        nonlocalized_event=nonlocalized_event,
        t_pre=param_ranges["t_pre"],
    )
    baseline = fit_agn_baseline(prephot, min_baseline_pts=param_ranges["min_baseline_pts"])

    postphot = _get_post_disc_phot(
        target_id=target_id,
        nonlocalized_event=nonlocalized_event,
        t_post=param_ranges["t_post"],
        t_pre=param_ranges["t_pre"],
    )
    max_significance, _flare_row = detect_flare(
        postphot, baseline, sigma_thresh=param_ranges["flare_sigma_thresh"]
    )

    if baseline and postphot is not None and len(postphot) and np.isfinite(max_significance):
        agn_flare_score = flare_confidence_score(
            max_significance,
            param_ranges["flare_sigma_thresh"],
            floor=PHOT_SCORE_MIN,
            center_frac=param_ranges["flare_score_center_frac"],
            width_frac=param_ranges["flare_score_width_frac"],
        )
        update_score_factor(event_candidate, "agn_flare_score", agn_flare_score)

        # model-based layer: is the flare's timing consistent with a *specific*
        # published emission model's envelope? Not model-agnostic (see module
        # docstring point 4), so it is always computed and stored here but kept out
        # of the default score product -- scoring/util.get_event_candidate_scores'
        # flare_shape_toggle decides whether it's included, at read time, the same
        # way agn_toggle decides whether agn_score is. That -- not a vet-time
        # PARAM_RANGES flag -- is what lets a user flip it live without a re-vet.
        # Every individual model's score is stored too (flare_shape_score_<name>),
        # not just the aggregate max -- these are display-only (not in
        # scoring/util.SUBSCORE_NAMES, so they never enter the score product) and
        # let the candidate page show which specific published picture, if any,
        # a candidate resembles.
        flare_shape_model_keys = [f"flare_shape_score_{name}" for name in FLARE_SHAPE_MODELS]
        delay_days, duration_days = estimate_flare_extent(postphot, baseline)
        if delay_days is not None:
            model_scores = flare_shape_scores_by_model(delay_days, duration_days)
            for name, score in model_scores.items():
                update_score_factor(event_candidate, f"flare_shape_score_{name}", score)
            update_score_factor(
                event_candidate, "flare_shape_score", max(model_scores.values())
            )
        else:
            delete_score_factor(event_candidate, "flare_shape_score")
            for key in flare_shape_model_keys:
                delete_score_factor(event_candidate, key)
    else:
        # not enough baseline and/or post-merger photometry to judge either way --
        # don't bias the score
        delete_score_factor(event_candidate, "agn_flare_score")
        delete_score_factor(event_candidate, "flare_shape_score")
        for name in FLARE_SHAPE_MODELS:
            delete_score_factor(event_candidate, f"flare_shape_score_{name}")
