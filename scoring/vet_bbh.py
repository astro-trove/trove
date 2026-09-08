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
    t_pre=0, # consider using t_pre < 0
    t_post=400, 
    min_baseline_pts=2,  # smallest n whose MAD carries any scatter information:
    # at n=1 the MAD is identically 0 and the "baseline" collapses to a single
    # point plus its own reported error, which inflates flare significance for
    # sparsely sampled filters. Kept low (not 5+) because TROVE photometry is
    # often sparse and KN scoring likewise scores off very few points.
    flare_sigma_thresh=5.0, 
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
    # Maps significance to score using normal CDF
    center = center_frac * thresh
    width = max(width_frac * thresh, 1e-6)
    raw = norm.cdf(significance, loc=center, scale=width)
    return float(np.clip(floor + (1.0 - floor) * raw, floor, 1.0))


def fit_agn_baseline(prephot: Optional[pd.DataFrame], min_baseline_pts: int = 2) -> dict:
    """
    Per-filter pre-merger baseline: median mag + robust (MAD-based) scatter, floored
    at measurement error.

    Deliberately model-agnostic -- no AGN variability model (DRW, CARMA, PSD) is fit,
    since this only needs to know how much the source has historically varied, not
    why. The MAD is floored at the median measurement error so a tightly sampled
    filter can't make every later point look significant.

    Returns dict mapping filter -> dict(mag, std, n); filters below
    `min_baseline_pts` real detections are absent.
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
        # If scatter is less than detector noise, then just use detector noise as the scatter
        median_err = float(np.median(group.magerr.to_numpy(dtype=float)))
        robust_std = max(robust_std, median_err)
        baseline[filt] = dict(mag=median_mag, std=robust_std, n=int(len(mags)))
    return baseline


def _flare_significance_series(
    postphot: Optional[pd.DataFrame], baseline: dict
) -> Optional[pd.DataFrame]:
    """
    Shared by `detect_flare`/`estimate_flare_extent`: attach a brightening-
    significance column (sigma, baseline vs. observed mag) to every detection in
    `postphot` whose filter has a fitted baseline. None if nothing qualifies.
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
    Largest brightening significance in `postphot` against `baseline`, and its row.
    `sigma_thresh` isn't used to filter here; the caller compares against it.

    Returns (max_significance, best_row), or (np.nan, None) if nothing qualifies.
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
    Delay-from-merger and duration of a candidate flare, for `flare_shape_score`.
    `postphot` must carry a `dt` column (days since the GW trigger).

    delay_days is `dt` of the most significant point (same as `detect_flare`).
    duration_days spans the earliest-to-latest points anywhere in postphot at or
    above the looser `extent_sigma_thresh`, or None if fewer than two such points
    exist. Both None if no flare is detectable at all.
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


# (delay_lo, delay_hi), (duration_lo, duration_hi) envelopes, observed-frame days
# since the GW trigger, for three published BBH-in-AGN-disk emission mechanisms.
# Each box is a generous envelope around that paper's own quoted numbers, not a sharp
# physical limit -- survey cadence can't justify more precision than "roughly
# consistent with this channel":
#   mck19 -- McKernan et al. 2019 (ApJL 884, L50), ram-pressure-stripped Hill sphere:
#            delay <3 d (kick >~500 km/s) to ~300 d (<~100 km/s), duration ~1-100 d.
#   jrr_i -- Rodriguez-Ramirez et al. 2025 (PhRvD 111, 083020), jet-cocoon thermal
#            diffusion: needs kick >~200 km/s, delay ~50-100+ d, duration ~20-100+ d
#            (both open-ended upward, capped here at a generous finite value).
#   tgw24 -- Tagawa et al. 2024 (ApJ 966, 21), jet breakout + shock cooling: delay
#            <~50 d for close-in mergers out to 40-300 d at ~1 pc, duration ~10-200+ d.
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
    Score `(delay_days, duration_days)` against each model in `models`
    independently rather than collapsing straight to one aggregate: TROVE can't know
    a priori which mechanism (if any) applies to a candidate, so the breakdown of
    which published picture it resembles is worth showing on the candidate page.
    Each score is `delay_score * duration_score`, 1.0 inside the model's envelope
    and falling off smoothly outside it via `_box_edge_score`. `duration_days=None`
    skips the duration check (delay-only).

    Returns dict mapping model name -> score.
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

        # model-dependent layer, always computed/stored; scoring/util.py's
        # flare_shape_toggle decides at read time whether it joins the score
        # product, the same way agn_toggle does for agn_score -- that's what lets
        # a user flip it live without a re-vet. Per-model scores are display-only
        # (not in SUBSCORE_NAMES), so they never enter the score product.
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
