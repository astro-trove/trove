from __future__ import annotations

import logging
import os
from collections import OrderedDict
from typing import Optional

import numpy as np
import pandas as pd
from pandas.api.types import union_categoricals

logger = logging.getLogger(__name__)

DEFAULT_DISTANCE_MPC = 259.0

# Finer at smaller distances because that is likely where the GW events will
# be most populated
RUNG_LADDER_MPC = (
    25.0, 50.0, 75.0, 100.0, 125.0, 150.0, 175.0, 200.0, 225.0, 250.0,
    300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0, 1000.0,
)

# DIAGNOSTIC: 202/456 candidates for S251112cm are greater than this
MAX_DISTANCE_MPC = 1000.0

DT_MIN, DT_MAX = 0.0, 30.0

DEFAULT_MAGERR = 2.5 / (3.0 * np.log(10.0))

_GRID_CACHE: "OrderedDict[tuple, Optional[pd.DataFrame]]" = OrderedDict()
GRID_CACHE_MAX_BYTES = int(os.environ.get("TROVE_GRID_CACHE_BYTES") or 1_500_000_000)
_GRID_CACHE_BYTES = 0


#: (name, distance_mpc) for every grid that can actually be scored against,
#: nearest-distance lookup table for `grid_for_distance`. Cached because it is a
#: single-query inventory of a handful of rows and a worker scores hundreds of
#: candidates. Held for the life of the process, so a worker must be
#: restarted to pick up a newly ingested rung.
_GRID_INVENTORY: "list[tuple[str, float]] | None" = None

def _grid_inventory():
    global _GRID_INVENTORY
    if _GRID_INVENTORY is not None:
        return _GRID_INVENTORY

    from .grid_db import scoreable_grids

    try:
        rungs = scoreable_grids()
    except Exception as exc:
        logger.error("could not read the grid inventory (%s: %s)",
                     type(exc).__name__, exc)
        raise KilonovaScoreUnavailable(
            f"grid inventory unavailable ({type(exc).__name__}: {exc})"
        ) from exc

    inv = []
    for name, distance, t_max in rungs:
        if not np.isfinite(t_max) or t_max + 1e-6 < DT_MAX:
            logger.debug("grid %s spans only %.1f d, skipping", name, t_max)
            continue
        inv.append((name, distance))
    inv.sort(key=lambda t: t[1])
    inv = _restrict_to_ladder(inv)
    _GRID_INVENTORY = inv
    logger.info("grid inventory: %d rung(s) spanning %.0f-%.0f Mpc",
                len(inv), inv[0][1] if inv else float("nan"),
                inv[-1][1] if inv else float("nan"))
    return _GRID_INVENTORY


def _restrict_to_ladder(inv):
    if not inv or not RUNG_LADDER_MPC:
        return inv
    keep = {}
    for target in RUNG_LADDER_MPC:
        name, dist = min(inv, key=lambda t: abs(t[1] - float(target)))
        keep[name] = dist
    reduced = sorted(keep.items(), key=lambda t: t[1])
    if len(reduced) < len(inv):
        logger.info("restricting %d rung(s) in the store to the %d-rung ladder: %s",
                    len(inv), len(reduced),
                    ", ".join(f"{d:.0f} Mpc" for _, d in reduced))
    return reduced


def grid_for_distance(dist_mpc: float) -> str:
    inv = _grid_inventory()
    if not inv:
        raise KilonovaScoreUnavailable(
            "no simulation grid in the store spans the scored window"
        )
    name, _ = min(inv, key=lambda t: abs(t[1] - float(dist_mpc)))
    return name


def default_grid() -> str:
    return grid_for_distance(DEFAULT_DISTANCE_MPC)


def _frame_bytes(df: Optional[pd.DataFrame]) -> int:
    """Resident size of a cached slice.

    ``deep=False`` is accurate here: every column is a numpy array or a
    categorical, so there are no object pointers hiding a larger payload.
    """
    if df is None:
        return 0
    try:
        return int(df.memory_usage(index=True, deep=False).sum())
    except Exception:  # noqa: BLE001 - a bad size estimate must not fail a score
        return 0


def _load_band_cached(grid: str, band: str, min_time: float, max_time: float):
    """One band of one rung, or None if the rung carries no simulations in it.

    The None is cached as well: a band the grid does not have is a permanent
    fact about the store, and re-asking Postgres once per candidate is the same
    wasted round trip as re-reading a band it does have.
    """
    global _GRID_CACHE_BYTES

    key = (grid, band, min_time, max_time)
    if key in _GRID_CACHE:
        _GRID_CACHE.move_to_end(key)
        return _GRID_CACHE[key]

    from .grid_db import load_grid_db

    try:
        df = load_grid_db(grid, bands=[band], min_time=min_time,
                          max_time=max_time)
    except ValueError as exc:
        if "no rows" not in str(exc):
            raise
        logger.info("Grid %s has no simulations in band %s", grid, band)
        _GRID_CACHE[key] = None
        return None

    _GRID_CACHE[key] = df
    _GRID_CACHE_BYTES += _frame_bytes(df)
    # `> 1` so the slice just loaded is never the one evicted: a budget smaller
    # than a single band would otherwise thrash forever and never make progress.
    while len(_GRID_CACHE) > 1 and _GRID_CACHE_BYTES > GRID_CACHE_MAX_BYTES:
        dropped, victim = _GRID_CACHE.popitem(last=False)
        _GRID_CACHE_BYTES -= _frame_bytes(victim)
        logger.info("Grid cache over budget -- evicted %s / %s", dropped[0], dropped[1])
    return df


def _load_grid_cached(grid: str, bands: tuple, min_time: float, max_time: float):
    """The candidate's bands as one frame, assembled from per-band cache slices."""
    wanted = sorted(set(bands))
    frames = [df for df in (_load_band_cached(grid, b, min_time, max_time)
                            for b in wanted) if df is not None]
    if not frames:
        # Same failure the single-query loader raised, so callers see no change.
        raise ValueError(
            f"Grid {grid} returned no rows for bands={wanted}, max_time={max_time}")
    if len(frames) == 1:
        return frames[0]

    columns = list(frames[0].columns)
    cat_cols = [c for c in ("band", "filter_mapped")
                if isinstance(frames[0][c].dtype, pd.CategoricalDtype)]
    merged = {c: union_categoricals([f[c] for f in frames]) for c in cat_cols}
    out = pd.concat([f.drop(columns=cat_cols) for f in frames],
                    ignore_index=True, copy=False)
    for col, values in merged.items():
        out[col] = values
    out = out[columns]
    out.attrs["name"] = grid
    return out


def _scalar_dist_err(dist_err) -> float:
    arr = np.asarray(dist_err, dtype=float).ravel()
    arr = arr[np.isfinite(arr)]
    if arr.size == 0 or (arr < 0).any():
        return 0.0
    return float(arr.max())


class KilonovaScoreUnavailable(RuntimeError):
    """Raised when the candidate cannot be scored at all (no grid, no distance,
    no usable photometry). Distinct from *scoring to zero*, which is a result."""


def build_data_obs(phot: pd.DataFrame, dist_mpc: float, dist_err_mpc: float) -> pd.DataFrame:
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
    
    from . import survey_band

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
    from KilonovaScorer.core2 import binned_stats_cumulative_ptail

    if results is None or not len(results):
        raise KilonovaScoreUnavailable("scorer returned no per-epoch rows")
    
    if "running_survivors_n" in results.columns and (results["running_survivors_n"] == 0).any():
        logger.info("ABC chain empty -- no simulation is consistent with every "
                    "epoch; flooring the score to 0")
        return 0.0

    try:
        cum = binned_stats_cumulative_ptail(results)
    except (KeyError, ValueError) as exc:
        raise KilonovaScoreUnavailable(
            f"scorer could not combine the epochs ({type(exc).__name__}: {exc})"
        ) from exc
    if not len(cum) or "running_mean" not in cum.columns:
        raise KilonovaScoreUnavailable("no cumulative score in the scorer output")

    value = float(cum["running_mean"].iloc[-1])
    if not np.isfinite(value):
        # The package's inverse-variance update divides by zero when every
        # p_tail_std is 0. A NaN factor would multiply TROVE's whole score to
        # NaN, which is strictly worse than the rejection it represents.
        logger.info("Cumulative score is NaN -- reading total rejection as 0.0")
        return 0.0
    return float(np.clip(value, 0.0, 1.0))


def score_candidate(
    target_id: int,
    nonlocalized_event,
    candidate_name: Optional[str] = None,
) -> float:

    from scoring.scoring import get_eventcandidate_default_distance
    from scoring.vet_phot import _get_post_disc_phot
    from KilonovaScorer.core2 import kilonovascorer_v3

    event_id = getattr(nonlocalized_event, "event_id", nonlocalized_event)

    dist_mpc, dist_err_mpc = get_eventcandidate_default_distance(target_id, event_id)
    if not np.isfinite(dist_mpc) or dist_mpc <= 0:
        raise KilonovaScoreUnavailable(f"no usable distance for target {target_id}")
    dist_err_mpc = _scalar_dist_err(dist_err_mpc)

    # Checked before any photometry is read: the read is the expensive part and
    # the answer cannot change once the distance is known.
    if dist_mpc > MAX_DISTANCE_MPC:
        raise KilonovaScoreUnavailable(
            f"Distance {dist_mpc:,.0f} Mpc is beyond the {MAX_DISTANCE_MPC:,.0f} Mpc "
            f"limit of the simulation ladder"
        )

    grid = grid_for_distance(dist_mpc)
    logger.debug("target %s at %.0f Mpc -> grid %s", target_id, dist_mpc, grid)

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
    grid_df = _load_grid_cached(grid, bands, DT_MIN, DT_MAX)
    # Only the bands this candidate actually has; a band with no simulations
    # would make the package iterate over an empty frame.
    usable = tuple(b for b in bands if (grid_df["filter_mapped"] == b).any())
    if not usable:
        raise KilonovaScoreUnavailable(
            f"none of the candidate's bands {bands} are in grid {grid}")

    results, _summary = kilonovascorer_v3(
        data_obs[data_obs["filter_mapped"].isin(usable)],
        grid_df,
        candidate_name=candidate_name or str(target_id),
        band_list=usable,
    )
    return _cumulative_factor(results)
