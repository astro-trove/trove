"""Adapter: score a TROVE candidate with the ``KilonovaScorer`` package.

This is deliberately thin. Everything statistical -- the noise-convolved PPD,
``P_tail``, the ABC survival chain, the cumulative logit combination -- belongs
to ``KilonovaScorer.core2`` and is called, never reimplemented. What lives here
is only the TROVE-specific ingestion the package cannot do for itself:

1. pull the candidate's photometry (``vet_phot._get_post_disc_phot``, the same
   reader ``vet_bns`` uses, so both scorers see identical data),
2. attach the candidate's distance and convert to absolute magnitude,
3. hand the package a ``data_obs`` frame and a grid frame,
4. reduce its per-epoch output to the single factor TROVE's score multiplies.

Read-only throughout: no ``ScoreFactor`` is written here and no photometry is
fetched from a broker.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

#: Grid to score against. One grid exists today; a candidate at any distance is
#: compared against it, with distance entering through the distance modulus
#: rather than through grid choice.
DEFAULT_GRID = "simulations_two_component_kilonova_model_259Mpc_30d"

#: Epoch window, in days after the trigger. 0 because a kilonova does not exist
#: before the merger; 30 because that is the grid's span -- an epoch past it
#: has nothing to be compared against.
DT_MIN, DT_MAX = 0.0, 30.0

#: A measurement with no usable error. 2.5/(3 ln10) ~ 0.36 mag is the
#: error a source detected exactly at a 3-sigma limit would have had.
DEFAULT_MAGERR = 2.5 / (3.0 * np.log(10.0))


#: Loaded grid frames, keyed by (grid, bands, min_time, max_time). Bounded LRU.
#:
#: `vet_all_async` enqueues ONE TASK PER CANDIDATE, and `vet_bns` calls
#: `score_candidate` without a `grid_df`, so without this every candidate reads
#: the grid out of Postgres again -- 25-60 s each, against ~3 s of actual
#: scoring. A worker is a long-lived process, so caching at module level means
#: the first candidate of a band set pays the read and the rest do not.
#:
#: Bounded because a band slice is ~100 MB: an unbounded cache would grow to the
#: whole grid (~1.6 GB) inside one worker. Candidates repeat band sets heavily
#: (ATLAS c/o covers most of a GW follow-up list), so a small cache still hits
#: nearly always.
_GRID_CACHE: "OrderedDict[tuple, pd.DataFrame]" = OrderedDict()
GRID_CACHE_MAX = 4


def _load_grid_cached(grid: str, bands: tuple, min_time: float, max_time: float):
    key = (grid, tuple(sorted(bands)), min_time, max_time)
    hit = _GRID_CACHE.get(key)
    if hit is not None:
        _GRID_CACHE.move_to_end(key)
        return hit

    from scoring.KilonovaScorerHelpers import load_grid_db

    df = load_grid_db(grid, bands=list(key[1]), min_time=min_time,
                      max_time=max_time, mode="survey")
    _GRID_CACHE[key] = df
    while len(_GRID_CACHE) > GRID_CACHE_MAX:
        dropped, _ = _GRID_CACHE.popitem(last=False)
        logger.info("Grid cache full -- evicted %s", dropped[1])
    return df


class KilonovaScoreUnavailable(RuntimeError):
    """Raised when the candidate cannot be scored at all (no grid, no distance,
    no usable photometry). Distinct from *scoring to zero*, which is a result."""


def build_data_obs(phot: pd.DataFrame, dist_mpc: float, dist_err_mpc: float) -> pd.DataFrame:
    """TROVE photometry -> the package's ``data_obs`` frame.

    Required columns are ``filter_mapped`` / ``time_after_gw`` /
    ``absolute_magnitude`` / ``absolute_magnitude_error``. The distance modulus
    is applied by the package's own ``compute_abs_mag_samples``, which
    propagates the distance uncertainty by sampling rather than in quadrature.

    NOTHING IS FILTERED HERE ON QUALITY GROUNDS. Every cut below exists because
    the package or the grid physically cannot consume the point, never because
    the point looks weak:

    * **Upper limits.** ``data_obs`` has no is-limit column and
      ``kilonovascorer_v3`` treats every row as a detection, so a limit passed
      through would be scored as if the source had been *measured* at its
      limiting magnitude -- wrong, and wrong in the direction that flatters the
      candidate. The package cannot represent a limit; that is its constraint.
    * **Epochs outside 0-30 d.** The grid's time axis ends at 30 d, so a later
      epoch has nothing to be compared against.
    * **Filters with no bandpass in the grid.** In survey mode an observation is
      compared through its own bandpass; if the grid does not model it there is
      no population to score against.

    There is deliberately no S/N threshold. An earlier version dropped
    detections below S/N 5 to match ``vet_bns``'s ``phot_score_snr_min``, which
    meant a candidate whose only detections were marginal came back "no
    detections in 0-30 d" -- reported as *unscoreable* when it was merely
    faint. A low-S/N point carries a large ``magerr``, the PPD convolves it in,
    and the scorer already weights it down accordingly. Discarding it threw away
    real information and misattributed the loss to the package.
    """
    from KilonovaScorer.utils import compute_abs_mag_samples

    det = phot[~phot["upperlimit"].astype(bool)].copy()
    det = det[(det["dt"] >= DT_MIN) & (det["dt"] <= DT_MAX)]
    if det.empty:
        return det.assign(filter_mapped=None, time_after_gw=None,
                          absolute_magnitude=None, absolute_magnitude_error=None)

    magerr = pd.to_numeric(det["magerr"], errors="coerce").to_numpy(float)
    magerr = np.where(np.isfinite(magerr) & (magerr > 0), magerr, DEFAULT_MAGERR)

    abs_mag, abs_err = compute_abs_mag_samples(
        pd.to_numeric(det["mag"], errors="coerce").to_numpy(float),
        magerr, dist_mpc=float(dist_mpc), dist_err_mpc=float(dist_err_mpc),
    )
    # TROVE's filter strings are NOT the grid's bandpass ids -- see
    # `survey_band`. Mapping needs the telescope too, because a bare "g" is a
    # different bandpass on ZTF, Pan-STARRS and Rubin.
    from scoring.KilonovaScorerHelpers import survey_band

    scope = det["telescope"].astype(str) if "telescope" in det.columns else ""
    mapped = [survey_band(t, f) for t, f in
              zip(scope if len(scope) else [None] * len(det), det["filter"].astype(str))]
    out = pd.DataFrame({
        # survey mode: an observation is compared against simulations through
        # its OWN bandpass, so the grid's band ids are the matching key.
        "filter_mapped": mapped,
        "time_after_gw": det["dt"].to_numpy(float),
        "absolute_magnitude": np.asarray(abs_mag, dtype=float),
        "absolute_magnitude_error": np.asarray(abs_err, dtype=float),
    })
    # A NaN absolute magnitude means the distance draw failed; the package would
    # skip these silently, so drop them here where it can be reported.
    unmapped = out["filter_mapped"].isna()
    if unmapped.any():
        logger.info("Dropping %d observation(s) in filters the grid does not model: %s",
                    int(unmapped.sum()),
                    sorted(set(det["filter"].astype(str)[unmapped.to_numpy()])))
        out = out[~unmapped]
    if out.empty:
        return out
    bad = ~np.isfinite(out["absolute_magnitude"]) | ~(out["absolute_magnitude_error"] > 0)
    if bad.any():
        logger.info("Dropping %d observation(s) with no usable absolute magnitude",
                    int(bad.sum()))
        out = out[~bad]
    return out


def _cumulative_factor(results: pd.DataFrame) -> float:
    """The package's per-epoch output -> one factor in [0, 1].

    ``binned_stats_cumulative_ptail`` returns a running mean whose LAST bin is
    the cumulative score. Two results need converting rather than passing on:

    * **NaN.** When the ABC chain empties, every ``p_tail_std`` is 0 and the
      package's inverse-variance update divides by zero, so the running mean
      comes back NaN (``KilonovaScorer/utils.py`` line ~250). A NaN factor
      multiplies TROVE's whole score to NaN -- strictly worse than the
      rejection it is meant to represent. Total rejection is a score of 0.
    * **Out of range.** Clipped defensively; a factor is multiplied into the
      total score and must not be able to inflate it.
    """
    from KilonovaScorer.core2 import binned_stats_cumulative_ptail

    if results is None or not len(results):
        raise KilonovaScoreUnavailable("scorer returned no per-epoch rows")
    try:
        cum = binned_stats_cumulative_ptail(results)
    except (KeyError, ValueError) as exc:
        # `binned_stats_cumulative_ptail` does groupby(...).apply(ivw_stats_logit)
        # and then reads a "mean" column off the result. On some degenerate
        # inputs -- seen on real candidates -- `ivw_stats_logit` raises
        # "truth value of an array ... is ambiguous" inside the apply, the
        # frame comes back without that column, and the read dies with
        # KeyError('mean'). That is a defect in the package, not something to
        # paper over with a score: the candidate is unscoreable, so say so and
        # let the caller fall back rather than inventing a number.
        raise KilonovaScoreUnavailable(
            f"scorer could not combine the epochs ({type(exc).__name__}: {exc})"
        ) from exc
    if not len(cum) or "running_mean" not in cum.columns:
        raise KilonovaScoreUnavailable("no cumulative score in the scorer output")

    value = float(cum["running_mean"].iloc[-1])
    if not np.isfinite(value):
        survivors = int(results["running_survivors_n"].iloc[-1]) \
            if "running_survivors_n" in results.columns else -1
        logger.info("Cumulative score is NaN with %d ABC survivor(s) -- "
                    "reading total rejection as 0.0", survivors)
        return 0.0
    return float(np.clip(value, 0.0, 1.0))


def score_candidate(
    target_id: int,
    nonlocalized_event,
    grid: str = DEFAULT_GRID,
    grid_df: Optional[pd.DataFrame] = None,
    candidate_name: Optional[str] = None,
    n_kde_sim: Optional[int] = None,
) -> float:
    """KilonovaSCORER's photometry factor for one candidate, in [0, 1].

    ``grid_df`` lets a caller scoring a whole event load the grid once and
    reuse it -- reading it per candidate would dominate the run.
    """
    from scoring.scoring import get_eventcandidate_default_distance
    from scoring.vet_phot import _get_post_disc_phot
    from KilonovaScorer.core2 import kilonovascorer_v3

    event_id = getattr(nonlocalized_event, "event_id", nonlocalized_event)

    dist_mpc, dist_err_mpc = get_eventcandidate_default_distance(target_id, event_id)
    if not np.isfinite(dist_mpc) or dist_mpc <= 0:
        raise KilonovaScoreUnavailable(f"no usable distance for target {target_id}")
    if not np.isfinite(dist_err_mpc) or dist_err_mpc < 0:
        dist_err_mpc = 0.0

    phot = _get_post_disc_phot(target_id=target_id,
                               nonlocalized_event=nonlocalized_event,
                               t_post=DT_MAX)
    if phot is None or not len(phot):
        raise KilonovaScoreUnavailable(f"no photometry for target {target_id}")

    data_obs = build_data_obs(phot, dist_mpc, dist_err_mpc)
    if not len(data_obs):
        raise KilonovaScoreUnavailable(
            f"no detections in {DT_MIN:g}-{DT_MAX:g} d for target {target_id}")

    bands = tuple(sorted(set(data_obs["filter_mapped"])))
    if grid_df is None:
        grid_df = _load_grid_cached(grid, bands, DT_MIN, DT_MAX)
    # Only the bands this candidate actually has; a band with no simulations
    # would make the package iterate over an empty frame.
    usable = tuple(b for b in bands if (grid_df["filter_mapped"] == b).any())
    if not usable:
        raise KilonovaScoreUnavailable(
            f"none of the candidate's bands {bands} are in grid {grid}")

    # `n_kde_sim` is the number of Monte-Carlo draws from the noise-convolved
    # PPD per observation (package default 50,000). Left at the package default
    # unless a caller overrides it -- lowering it trades score precision for
    # speed, and the scorer is already unseeded, so it compounds existing noise.
    extra = {} if n_kde_sim is None else {"n_kde_sim": int(n_kde_sim)}
    results, _summary = kilonovascorer_v3(
        data_obs[data_obs["filter_mapped"].isin(usable)],
        grid_df,
        **extra,
        candidate_name=candidate_name or str(target_id),
        band_list=usable,
    )
    return _cumulative_factor(results)
