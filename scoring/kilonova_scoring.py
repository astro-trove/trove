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
import os
from collections import OrderedDict
from typing import Optional

import numpy as np
import pandas as pd
from pandas.api.types import union_categoricals

logger = logging.getLogger(__name__)

#: The reference rung, and the grid this scoring was built and validated
#: against. Callers that need every candidate compared against ONE population
#: pin it explicitly -- the diagnostics do.
#:
#: Scoring never falls back to it. Routing a candidate here because its own rung
#: could not be determined would compare it against a population at the wrong
#: redshift, which changes the SHAPE of the simulated magnitude distribution per
#: band and epoch and cannot be undone downstream -- a wrong answer, delivered
#: silently, and indistinguishable on the page from a right one.
DEFAULT_GRID = "simulations_two_component_kilonova_model_259Mpc_30d"

#: Rungs the scorer may route candidates to, in Mpc; None uses every rung in the
#: store. Distances above :data:`MAX_DISTANCE_MPC` are refused outright rather
#: than routed to the top rung, so this ladder only ever covers 0-1000 Mpc.
#:
#: This is the full ladder: 25 Mpc steps to 250, then 100 Mpc steps to 1000.
#:
#: It can be cut down, and diagnostics/reports/RUNG_LADDER.md measures what that
#: would cost. Scoring 20 real S251112cm candidates against all 19 rungs -- 380
#: scorings -- found the ladder barely moves the answer: median spread 0.000 dex
#: in log10(score) across the whole range, Spearman 1.0000, zero ABC verdict
#: flips. The saving would be grid I/O rather than disk: with the full ladder one
#: S251112cm pass needed 89 grid reads for 147 candidates, because consecutive
#: candidates land on different rungs and the band cache is keyed by
#: (grid, band). A single rung needs ~12, one per band.
#:
#: Reasons that reduction has NOT been made here:
#:
#: * The near field is untested. The measured sample spans 49-997 Mpc, so
#:   anything below 49 Mpc is extrapolated -- and that is exactly where a real
#:   nearby kilonova would land, AT2017gfo being at 43 Mpc.
#: * The cost side is not timed per ladder. Section 5 measures storage, which is
#:   linear in rung count, and the read counts above; no wall-clock for any
#:   particular reduced ladder follows from that.
#: * Every reduced ladder in section 4 preserved the ranking exactly, which is
#:   what TROVE is for -- so the measurement does not discriminate between them
#:   either, and picking one is a judgement rather than a result.
#:
#: To trim it, list the rungs to keep; the nearest available rung to each is
#: used, so a store missing one degrades to its neighbour rather than losing it.
RUNG_LADDER_MPC = (
    25.0, 50.0, 75.0, 100.0, 125.0, 150.0, 175.0, 200.0, 225.0, 250.0,
    300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0, 1000.0,
)

# 202/456 candidates for S251112cm are greater than this
MAX_DISTANCE_MPC = 1000.0

# Current simulation grids only simulated the KN up to 30 days
# Useful because there are quite a few TROVE candidates who only have photometry after 10d
DT_MIN, DT_MAX = 0.0, 30.0

DEFAULT_MAGERR = 2.5 / (3.0 * np.log(10.0))


#: Loaded grid slices, keyed by (grid, ONE band, min_time, max_time). Bounded
#: LRU, budgeted in bytes.
#:
#: `vet_all_async` enqueues ONE TASK PER CANDIDATE, and `vet_bns` calls
#: `score_candidate` without a `grid_df`, so without this every candidate reads
#: the grid out of Postgres again -- 10-20 s each, against ~3 s of actual
#: scoring. A worker is a long-lived process, so caching at module level means
#: the first candidate to want a band pays the read and the rest do not.
#:
#: KEYED PER BAND, NOT PER BAND SET. The first version keyed on the candidate's
#: whole band tuple, which is a *combination*: ~12 bands appear across a GW
#: follow-up list, so the key space was 2**12 while the cache held 4 entries.
#: Measured on S251112cm, that was 125 Postgres reads for 160 candidates and 121
#: evictions -- the same rung re-read three times in a row for ('sdssg',), then
#: ('atlaso','sdssr'), then ('atlasc','atlaso','sdssg'). Keying per band
#: collapses the space to the bands that actually exist, which is what lets
#: ordering candidates by distance group the loads the way it was meant to. The
#: per-candidate frame is then assembled from cached slices: a memory copy
#: instead of a query.
#:
#: Budgeted in bytes rather than entries because a band slice is ~100 MB and the
#: WSL OOM killer is global -- an entry count cannot bound what actually matters.
_GRID_CACHE: "OrderedDict[tuple, Optional[pd.DataFrame]]" = OrderedDict()
GRID_CACHE_MAX_BYTES = int(os.environ.get("TROVE_GRID_CACHE_BYTES") or 1_500_000_000)
_GRID_CACHE_BYTES = 0


#: (name, distance_mpc) for every grid that can actually be scored against,
#: nearest-distance lookup table for `grid_for_distance`. Cached because it is a
#: two-query inventory of a handful of rows and a worker scores hundreds of
#: candidates; `refresh=True` re-reads it after a new rung is ingested.
_GRID_INVENTORY: "list[tuple[str, float]] | None" = None


def _grid_inventory(refresh: bool = False):
    """Grids in the store that span the scored window, sorted by distance.

    Filtered on ``t_max``, not just presence: a 10-day rung is a perfectly valid
    grid that simply cannot answer for epochs 10-30 d, and routing a candidate
    to one would silently drop two thirds of the window. `available_grids_db`
    already inner-joins against the lightcurve table, so a half-ingested rung
    with only an axis row is excluded before it gets here.
    """
    global _GRID_INVENTORY
    if _GRID_INVENTORY is not None and not refresh:
        return _GRID_INVENTORY

    from scoring.KilonovaScorerHelpers import available_grids_db

    inv = []
    try:
        df = available_grids_db()
    except Exception as exc:  # noqa: BLE001 - surfaced as unscoreable below
        # Deliberately not cached: a transient database hiccup should not pin
        # the whole worker into a failed state for its lifetime.
        logger.error("could not read the grid inventory (%s: %s)",
                     type(exc).__name__, exc)
        raise KilonovaScoreUnavailable(
            f"grid inventory unavailable ({type(exc).__name__}: {exc})"
        ) from exc

    for row in df.itertuples():
        ref = row.path
        t_max = getattr(ref, "t_max", float("nan"))
        if not np.isfinite(t_max) or t_max + 1e-6 < DT_MAX:
            logger.debug("grid %s spans only %.1f d, skipping", ref.name, t_max)
            continue
        inv.append((ref.name, float(row.distance_mpc)))
    inv.sort(key=lambda t: t[1])
    inv = _restrict_to_ladder(inv)
    _GRID_INVENTORY = inv
    logger.info("grid inventory: %d rung(s) spanning %.0f-%.0f Mpc",
                len(inv), inv[0][1] if inv else float("nan"),
                inv[-1][1] if inv else float("nan"))
    return _GRID_INVENTORY


def _restrict_to_ladder(inv):
    """Reduce the store's rungs to the ones :data:`RUNG_LADDER_MPC` asks for.

    Nearest available rung to each ladder distance, rather than an exact match,
    so a store missing one of them degrades to its neighbour instead of losing a
    rung silently. Filtering here rather than deleting grids keeps the store
    intact -- the extra rungs cost only disk, and dropping them is not something
    a scoring change should decide.
    """
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


def grid_for_distance(dist_mpc: float, refresh: bool = False) -> str:
    """Name of the rung whose distance is closest to ``dist_mpc``.

    Nearest in linear distance rather than in distance modulus. The ladder is
    deliberately denser where it matters -- 25 Mpc steps below 250, 100 Mpc
    steps above -- so linear nearest already keeps the fractional distance error
    roughly flat across the range, and it is the rule that is obvious when
    reading a candidate's grid assignment back off the page.
    """
    inv = _grid_inventory(refresh=refresh)
    if not inv:
        raise KilonovaScoreUnavailable(
            "no simulation grid in the store spans the scored window"
        )
    name, _ = min(inv, key=lambda t: abs(t[1] - float(dist_mpc)))
    return name


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

    from scoring.KilonovaScorerHelpers import load_grid_db

    try:
        df = load_grid_db(grid, bands=[band], min_time=min_time,
                          max_time=max_time, mode="survey")
    except ValueError as exc:
        # "returned no rows" and "no usable rows after filtering" both mean the
        # band is simply absent. Every other ValueError from the loader -- an
        # empty epoch window, a lightcurve width that disagrees with the axis --
        # means the store is broken, and must not be swallowed into a band that
        # silently disappears from the score.
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

    # `band` and `filter_mapped` are categoricals whose categories differ from
    # slice to slice, and a plain concat would collapse them to object dtype --
    # eight bytes of pointer per row across ~20M rows, and a slower groupby
    # inside the scorer. union_categoricals remaps the codes instead.
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
    """The distance uncertainty as a single non-negative number.

    ``get_eventcandidate_default_distance`` hands back whatever the host-galaxy
    JSON stored in ``DistErr``, and some catalogs record an ASYMMETRIC error as
    a two-element ``[minus, plus]`` pair instead of a scalar -- e.g. AT2025aeag,
    229.9 Mpc with ``[76.3, 94.6]``. ``np.isfinite`` on a pair returns an array
    and ``not`` on an array raises, which took out 31 of 456 S251112cm
    candidates with "truth value of an array is ambiguous" before this existed.

    The package propagates the distance error by sampling ONE Gaussian sigma
    (``compute_abs_mag_samples``), so an asymmetric pair has to be symmetrised
    before it can be used at all. We take the LARGER side: it is the
    conservative choice, widening the distance posterior rather than narrowing
    it, so a candidate is never rejected because we understated how poorly its
    distance is known.

    Scalar behaviour is unchanged from the check this replaces: non-finite or
    negative still becomes 0.0, which the caller reads as "no usable error".
    """
    arr = np.asarray(dist_err, dtype=float).ravel()
    arr = arr[np.isfinite(arr)]
    if arr.size == 0 or (arr < 0).any():
        return 0.0
    return float(arr.max())


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


def _abc_survivors(results: pd.DataFrame) -> Optional[int]:
    """Simulations consistent with EVERY epoch, in every band, or None if unknown.

    This is the survival set S_t of Darc & Kilpatrick (2026), section 3.5,
    carried to its end::

        S_t = S_{t-1} INTERSECT {i : M_rep,i(t) in R(M_obs(t), k_ABC sigma_obs(t))}

    ACROSS BANDS, not per band -- the paper is explicit that the set shrinks
    "as each subsequent observation is incorporated across all available
    photometric bands". ``sample_id`` labels the same physical simulation in
    every band, which is what makes the cross-band intersection meaningful:
    a model that fits g but not r is not a model that fits the candidate.

    The package computes the chain PER BAND instead (``overlap_chain`` is
    called once inside each band's loop and the results are keyed by band in
    ``overlap_summary_by_band``), so nothing it returns implements the paper's
    S_t. That is why this is computed here from ``consistent_ids`` rather than
    read off ``running_survivors_n``.

    Monotone by construction, so the first epoch admitting nothing settles the
    answer and the loop stops there.
    """
    if "consistent_ids" not in results.columns:
        return None
    survivors = None
    for ids in results["consistent_ids"]:
        current = set(ids) if ids is not None else set()
        survivors = current if survivors is None else (survivors & current)
        if not survivors:
            return 0
    return None if survivors is None else len(survivors)


def _cumulative_factor(results: pd.DataFrame) -> float:
    """The package's per-epoch output -> one factor in [0, 1].

    ``binned_stats_cumulative_ptail`` returns a running mean whose LAST bin is
    the cumulative score, and the ABC survival chain floors it to 0 when no
    simulation is consistent with every epoch (see the comment below). Two
    further results need converting rather than passing on:

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

    # THE ABC FLOOR -- Darc & Kilpatrick (2026) section 3.5, "Penalization and
    # Flagging", checked before the P_tail aggregation:
    #
    #   "In cases where |S_t| = 0 at any epoch t, the candidate is flagged as
    #    temporally inconsistent with the kilonova model grid. Even if a high
    #    P_near,KNe or P_tail,KNe score is recorded for a subsequent isolated
    #    observation, the cumulative kilonova score is penalized and set to
    #    zero."
    #
    # So the diagnostic is inert -- it never shades the score up or down --
    # except for this one hard floor, and the floor has to be applied here
    # because the aggregation cannot express it. `binned_stats_cumulative_ptail`
    # combines P_tail in logit space with inverse-variance weights, and the
    # delta-method variance s/(p(1-p)) diverges as p -> 0, so the epochs that
    # reject a candidate carry almost no weight. Measured on AT2025adro: five of
    # eleven epochs admitted ZERO simulations and together held 0.36% of the
    # weight while three permissive epochs held 95%, producing 0.12 for a
    # candidate the diagnostic had already excluded outright.
    #
    # k_ABC is the package's `overlap_k`, left at its default of 2.0. The
    # paper's fiducial 1.5 is calibrated to its N=1e5 grid; Appendix A gives
    # k_ABC,min = 2.0 for an N=1e4 grid and tells users to calibrate to their
    # own grid size, and our rungs carry 1e4 samples per band.
    #
    # Checked FIRST, so a candidate the diagnostic rejects returns 0 even when
    # the aggregation would have failed -- `ivw_stats_logit` raises on some
    # degenerate inputs, and "no simulation fits" is an answer, not an error.
    survivors = _abc_survivors(results)
    if survivors == 0:
        logger.info("ABC chain empty -- no simulation is consistent with every "
                    "epoch; flooring the score to 0")
        return 0.0

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
    grid: Optional[str] = None,
    grid_df: Optional[pd.DataFrame] = None,
    candidate_name: Optional[str] = None,
    n_kde_sim: Optional[int] = None,
) -> float:
    """KilonovaSCORER's photometry factor for one candidate, in [0, 1].

    ``grid`` defaults to the rung nearest the candidate's own distance. A grid
    is distance-specific -- redshift sets both the k-correction and the time
    dilation, so it changes the SHAPE of the magnitude distribution per band and
    epoch, and the distance modulus cannot correct for a mismatch. Passing a
    name pins the choice, which is what the diagnostics do when they need every
    candidate compared against one population.

    ``grid_df`` lets a caller scoring a whole event load the grid once and
    reuse it -- reading it per candidate would dominate the run. It overrides
    ``grid`` entirely, so a caller supplying a frame is responsible for it being
    the right rung.
    """
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
            f"distance {dist_mpc:,.0f} Mpc is beyond the {MAX_DISTANCE_MPC:,.0f} Mpc "
            f"limit of the simulation ladder"
        )

    # Nearest rung, unless the caller pinned one or supplied a frame.
    if grid_df is None and grid is None:
        grid = grid_for_distance(dist_mpc)
        logger.debug("target %s at %.0f Mpc -> grid %s", target_id, dist_mpc, grid)
    elif grid is None:
        # Only a label from here on: `grid_df` overrides the name entirely, so
        # naming a real rung would misreport which population was used.
        grid = "caller-supplied grid"

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
