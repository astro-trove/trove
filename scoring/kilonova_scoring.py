"""
Score TROVE candidates with KilonovaSCORER.

This is the bridge between TROVE's database and the vendored
:mod:`scoring.KilonovaScorer` package: it pulls a candidate's photometry,
converts it to absolute magnitudes, matches it against a simulated kilonova
grid, and returns a single cumulative score with its diagnostics.

Typical use::

    from scoring.kilonova_scoring import score_event, load_simulation_grid
    from scoring.KilonovaScorer.grids import grid_for_distance

    grid = load_simulation_grid(grid_for_distance(300))   # nearest grid to 300 Mpc
    scores = score_event("S251112cm", grid=grid)          # DataFrame, one row per candidate
    scores.sort_values("score", ascending=False).head(10)

or one candidate at a time, keeping the full diagnostics::

    from scoring.kilonova_scoring import score_candidate

    result = score_candidate("S251112cm", "SN2025adgq", grid=grid)
    result.score, result.score_err        # cumulative P_tail_KNe and its error
    result.per_observation                # per-epoch metrics
    result.binned                         # per-time-bin running score
    result.survivors                      # ABC survivor counts per band

The scorer needs a **simulation grid**, which this repo does not ship -- it is
generated with :mod:`scoring.KilonovaScorer.simulation` (which pulls in redback
/ bilby / lal, and is deliberately *not* imported by the package ``__init__``,
so nothing here drags that stack in). See ``simulation_grids`` below and
scoring/README_kilonova_scoring.md.

Distance handling
-----------------
Absolute magnitudes are derived from the candidate's own distance
(:func:`scoring.candidate_photometry.get_candidate_distance`), which falls back
through target redshift -> best host-galaxy redshift -> the GW skymap posterior
at the candidate's healpix. Because the simulated grid is generated at a
particular luminosity distance (the redshift enters the model), grids are
selected per distance bin -- see :func:`select_grid_for_distance`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .KilonovaScorer.core import binned_stats_cumulative_ptail, kilonovascorer_v3
from .KilonovaScorer.utils import compute_abs_mag_samples
from .candidate_photometry import (
    DEFAULT_MAGERR,
    get_candidate_distance,
    get_candidates,
    get_event,
    get_event_photometry,
    to_scorer_frame,
)

logger = logging.getLogger(__name__)

#: Bands the scorer models. Anything else is dropped upstream by
#: :func:`~scoring.candidate_photometry.to_scorer_frame`.
BAND_LIST: Tuple[str, ...] = ("g-band", "r-band", "i-band", "z-band")

#: Columns ``kilonovascorer_v3`` requires on the observation frame.
DATA_OBS_COLUMNS = (
    "filter_mapped",
    "time_after_gw",
    "absolute_magnitude",
    "absolute_magnitude_error",
    "is_limit",
)

#: Columns ``kilonovascorer_v3`` requires on the simulation grid.
DATA_SIM_COLUMNS = ("filter_mapped", "time", "absolute_magnitude", "sample_id")


# ---------------------------------------------------------------------------
# Simulation grids
#
# Grid loading, the distance ladder and the artifact filtering all live in
# KilonovaScorer.grids, next to the data. These are thin aliases so callers of
# this module do not need to reach into the package.
# ---------------------------------------------------------------------------
from .KilonovaScorer.grids import GRID_DIR  # noqa: E402
from .KilonovaScorer.grids import available_grids as simulation_grids  # noqa: E402
from .KilonovaScorer.grids import clear_cache as clear_grid_cache  # noqa: E402
from .KilonovaScorer.grids import grid_distance_mpc  # noqa: E402
from .KilonovaScorer.grids import grid_for_distance as select_grid_for_distance  # noqa: E402
from .KilonovaScorer.grids import grid_name  # noqa: E402
from .KilonovaScorer.grids import load_grid as load_simulation_grid  # noqa: E402
from .KilonovaScorer.grids import resolve_grid  # noqa: E402


# ---------------------------------------------------------------------------
# Observation frame
# ---------------------------------------------------------------------------
def build_data_obs(
    phot: pd.DataFrame,
    dist_mpc: float,
    dist_err_mpc: float,
    map_wide_bands: bool = False,
    n_samples: int = 5000,
    mode: str = "survey",
    random_state=42,
) -> pd.DataFrame:
    """Turn a TROVE photometry frame into the scorer's ``data_obs``.

    In-memory equivalent of KilonovaSCORER's ``load_observations``: same
    columns, same Monte-Carlo distance-modulus treatment
    (``compute_abs_mag_samples``), without the CSV round-trip.

    ``phot`` is the long frame from
    :func:`~scoring.candidate_photometry.get_event_photometry`, already
    restricted to one candidate. Pre-merger points and upper limits must
    already be removed -- :func:`score_candidate` does that.
    """
    scorer = to_scorer_frame(phot, map_wide_bands=map_wide_bands, mode=mode)
    if scorer.empty:
        return scorer

    # Non-detections carry no photometric error -- there is no measurement to
    # have one. They still need a sigma: it is the depth uncertainty of the
    # limit, used both to convolve the PPD and to soften the ABC acceptance
    # edge. DEFAULT_MAGERR is the module's existing 3-sigma convention,
    # 2.5/(3 ln 10) ~ 0.36 mag, i.e. the error a source detected exactly at the
    # limit would have had. Surveys differ on whether a quoted limit is 3- or
    # 5-sigma; 3-sigma is the conservative reading (wider sigma, more tolerant
    # test) and matches what _parse_datum already assumes for a detection with
    # a missing error.
    mag_err = scorer["e_magnitude"].to_numpy(dtype=float).copy()
    is_limit = scorer["is_limit"].to_numpy(dtype=bool)
    mag_err[is_limit & ~np.isfinite(mag_err)] = DEFAULT_MAGERR

    abs_mag, abs_err = compute_abs_mag_samples(
        scorer["magnitude"].to_numpy(),
        mag_err,
        dist_mpc=dist_mpc,
        dist_err_mpc=dist_err_mpc,
        n_samples=n_samples,
        random_state=random_state,
    )
    out = scorer.copy()
    out["absolute_magnitude"] = abs_mag
    out["absolute_magnitude_error"] = abs_err

    # A NaN absolute magnitude means the distance draw failed; the scorer would
    # skip these silently, so drop them here where it can be reported.
    bad = out["absolute_magnitude"].isna() | ~(out["absolute_magnitude_error"] > 0)
    if bad.any():
        logger.info("Dropping %d observation(s) with no usable absolute magnitude", int(bad.sum()))
        out = out[~bad]
    return out


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass
class KilonovaScore:
    """Outcome of scoring one candidate.

    ``score`` is the cumulative P_tail_KNe after the last time bin -- the
    headline number, in [0, 1], higher meaning more consistent with the
    simulated kilonova population. It is ``None`` when the candidate could not
    be scored, with ``skip_reason`` saying why.
    """

    event_id: str
    target_name: str
    target_id: Optional[int] = None
    score: Optional[float] = None
    score_err: Optional[float] = None
    dist_mpc: float = np.nan
    dist_err_mpc: float = np.nan
    n_obs_supplied: int = 0
    n_obs_scored: int = 0
    bands: Tuple[str, ...] = ()
    final_survivors: Dict[str, int] = field(default_factory=dict)
    grid: Optional[str] = None
    skip_reason: Optional[str] = None
    score_note: Optional[str] = None
    per_observation: Optional[pd.DataFrame] = None
    binned: Optional[pd.DataFrame] = None
    survivors: Optional[pd.DataFrame] = None
    #: Joint cross-band ABC survival chain (paper eq. 18) and the epoch, if
    #: any, at which |S_t| hit zero. See :func:`abc_survival_chain`.
    abc_chain: List[Dict[str, Any]] = field(default_factory=list)
    abc_collapse_time: Optional[float] = None

    @property
    def scored(self) -> bool:
        return self.score is not None and np.isfinite(self.score)

    def as_row(self) -> Dict[str, Any]:
        """Flat summary suitable for a results table (drops the frames)."""
        return {
            "event_id": self.event_id,
            "target_name": self.target_name,
            "target_id": self.target_id,
            "score": self.score,
            "score_err": self.score_err,
            "dist_mpc": self.dist_mpc,
            "dist_err_mpc": self.dist_err_mpc,
            "n_obs_supplied": self.n_obs_supplied,
            "n_obs_scored": self.n_obs_scored,
            "n_bands": len(self.bands),
            "bands": ",".join(self.bands),
            "final_survivors": (
                min(self.final_survivors.values()) if self.final_survivors else np.nan
            ),
            "grid": self.grid,
            "abc_collapse_time": self.abc_collapse_time,
            "skip_reason": self.skip_reason,
            "score_note": self.score_note,
        }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def abc_survival_chain(per_obs: pd.DataFrame) -> Tuple[List[Dict[str, Any]], Optional[float]]:
    """The paper's sequential ABC survival filter, intersected across all bands.

    Implements eq. 18 of Darc & Kilpatrick (2026)::

        S_t = S_{t-1} INTERSECT {i : M_rep,i(t) in ROPE(M_obs(t), k_ABC*sigma_obs)}

    ``kilonovascorer_v3`` already runs ``overlap_chain`` and reports
    ``final_n_survivors``, but it does so **per band**: each band gets its own
    independent chain. The paper is explicit that the filter is joint -- "As
    each subsequent observation is incorporated *across all available
    photometric bands*, only simulations that satisfy all previous ROPE
    criteria are retained" (Section 3.5). A simulation must therefore explain
    the candidate in *every* band at *every* epoch, and per-band chains are
    strictly more permissive: a simulation that fails in g but survives in r
    is kept by the per-band version and discarded by the paper's.

    Rebuilding the chain here rather than in the vendored scorer keeps this a
    boundary decision in the integration layer, the same way
    ``zero_on_total_rejection`` is (see ``score_candidate``).

    ``sample_id`` labels a parameter draw and is shared across bands, so
    intersecting id sets from different bands is meaningful.

    Returns ``(chain, collapse_time)`` -- one entry per epoch in time order,
    and the time of the first epoch where the running intersection is empty
    (``None`` if it never collapses).
    """
    if per_obs.empty or not {"consistent_ids", "obs_time"} <= set(per_obs.columns):
        return [], None

    # One step per epoch, pooling every band observed at that epoch: the filter
    # advances in time, not in (time, band) pairs.
    chain: List[Dict[str, Any]] = []
    survivors: Optional[set] = None
    collapse_time: Optional[float] = None

    for t_obs, group in per_obs.groupby("obs_time", sort=True):
        # |A_t| is an INTERSECTION over the bands observed at this epoch, not a
        # union: eq. 18 retains a simulation only if it satisfies the ROPE
        # criterion of every observation, so one that matches in r but misses
        # in g has not explained the epoch.
        accepted: Optional[set] = None
        for ids in group["consistent_ids"]:
            s = set(ids) if ids is not None else set()
            accepted = s if accepted is None else (accepted & s)
        accepted = accepted or set()
        survivors = set(accepted) if survivors is None else (survivors & accepted)

        chain.append({
            "time": float(t_obs),
            # |A_t| -- accepted at this epoch alone, the denominator of the
            # paper's relative survival fraction f_surv (eq. 19)
            "n_accepted": len(accepted),
            "n_survivors": len(survivors),          # |S_t|
        })
        if not survivors and collapse_time is None:
            collapse_time = float(t_obs)

    return chain, collapse_time


def _cumulative_score(per_obs: pd.DataFrame, bin_size: float) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """Run the logit-space cumulative aggregation, guarding its edge cases.

    ``binned_stats_cumulative_ptail`` assumes a non-empty metric frame and at
    least one surviving bin; both assumptions fail routinely on sparse TROVE
    light curves (a single epoch, or every bin dropped by its internal
    ``dropna``). Returns ``(binned_df, error_reason)``.
    """
    if per_obs.empty:
        return None, "no epochs scored"

    # Non-detections inform the ABC survival filter but never this average.
    #
    # A limit's score is Pr(M_rep > M_lim), which is ~1 whenever the limit is
    # shallower than the simulated population -- i.e. precisely when the
    # observation carries NO information. Averaged in, "we did not look deep
    # enough to see anything" reads as "maximally consistent with a kilonova",
    # and it dominates: with limits aggregated, two S251112cm candidates that
    # were never detected at all scored 0.948 and 0.944, above the 0.804 of the
    # best candidate with real detections. The framework has no way to express
    # "neutral" -- 1-F is high for an uninformative limit and there is no
    # weighting that fixes that, because the value itself, not its weight, is
    # what is wrong (measured IVW weights were an unremarkable 0.5-2.8).
    #
    # The real information in a non-detection is exclusionary: a DEEP limit
    # rules out simulations bright enough to have been seen. That is exactly
    # what the ABC kernel does with it in compute_consistent_ids_anyhit, and a
    # shallow limit correctly excludes nothing there. So limits can shrink the
    # survivor set and trigger the collapse penalty, but can never raise the
    # score -- which is the honest reading of a non-detection.
    if "is_limit" in per_obs.columns:
        detections = per_obs[~per_obs["is_limit"].astype(bool)]
        if detections.empty:
            return None, "only non-detections in the grid's time window"
        per_obs = detections

    # NOTE: this used to short-circuit when every epoch had p_tail_std == 0,
    # because the aggregation divided by zero and returned NaN. `ivw_stats_logit`
    # now floors the std (IMPROVEMENTS.md §1c), so an all-zero-spread candidate
    # aggregates normally and comes out at ~0 -- which is the correct answer for
    # a candidate lying wholly outside the simulated population, not a failure.
    # Short-circuiting here would hide the fix.
    try:
        binned = binned_stats_cumulative_ptail(per_obs, bin_size=bin_size)
    except Exception as exc:  # noqa: BLE001
        # Deliberately broad. `ivw_stats_logit` returns Series with different
        # key sets on different paths, so what groupby().apply() produces
        # depends on how pandas aligns them -- with a single bin, or with every
        # bin taking the short return, the result has no "mean" column at all
        # and indexing it raises KeyError (IMPROVEMENTS.md #1b, #15). An
        # unscoreable candidate must not take down a whole event's run.
        return None, f"cumulative aggregation failed: {type(exc).__name__}: {exc}"

    if binned is None or binned.empty:
        return None, "all time bins dropped during aggregation"
    if not np.isfinite(binned["running_mean"].iloc[-1]):
        return binned, "cumulative score is not finite"
    return binned, None


def score_candidate(
    event_id: str,
    target_name: str,
    grid: Optional[pd.DataFrame] = None,
    grid_path=None,
    phot: Optional[pd.DataFrame] = None,
    dist: Optional[Tuple[float, float]] = None,
    dt_min: float = 0.0,
    dt_max: Optional[float] = None,
    snr_min: Optional[float] = None,
    map_wide_bands: bool = False,
    mode: str = "survey",
    time_bin_width: float = 0.2,
    band_list: Optional[Sequence[str]] = None,
    keep_frames: bool = True,
    zero_on_total_rejection: bool = True,
    abc_penalty: bool = True,
    random_state=42,
    **scorer_kwargs,
) -> KilonovaScore:
    """Score one candidate of one GW event.

    Parameters
    ----------
    event_id, target_name
        Superevent name (``'S251112cm'``) and the candidate's target name.
    grid
        A grid already loaded with :func:`load_simulation_grid`. Pass this when
        scoring many candidates so the file is read once.
    grid_path
        Explicit grid file. If neither ``grid`` nor ``grid_path`` is given, one
        is chosen from the grid directory by the candidate's distance.
    phot
        Pre-fetched photometry for this candidate (from
        :func:`~scoring.candidate_photometry.get_event_photometry`). Avoids a
        second database round-trip when scoring a whole event.
    dist
        ``(dist_mpc, dist_err_mpc)`` override. Defaults to the candidate's own
        distance from TROVE's scoring.
    dt_min, dt_max, snr_min
        Photometry cuts, in days since the trigger and S/N. ``dt_min=0`` (the
        default) drops pre-merger data, as KilonovaSCORER's own loader does.
    map_wide_bands
        Fold unfiltered/very wide bands (Clear, GOTO L, BlackGem q, ATLAS wide)
        onto their nearest SDSS band instead of dropping them.
    keep_frames
        Attach the per-observation / binned / survivor frames to the result.
        Set False when scoring in bulk to keep memory flat.
    zero_on_total_rejection
        Report a score of 0 when every epoch falls outside the simulated
        population, instead of "unscoreable". See the comment in the body --
        this is the one place this module departs from raw upstream behaviour.

    Remaining keyword arguments are passed to ``kilonovascorer_v3``
    (``n_kde_sim``, ``min_sim_points``, ``overlap_k``).
    """
    result = KilonovaScore(event_id=event_id, target_name=target_name)

    # --- photometry --------------------------------------------------------
    if phot is None:
        phot = get_event_photometry(
            event_id,
            target_names=[target_name],
            dt_min=dt_min,
            dt_max=dt_max,
            include_limits=True,
            snr_min=snr_min,
        )
    phot = phot[phot["target_name"] == target_name]
    result.n_obs_supplied = len(phot)
    if phot.empty:
        result.skip_reason = "no photometry"
        return result
    result.target_id = int(phot["target_id"].iloc[0])

    # --- distance ----------------------------------------------------------
    if dist is None:
        dist_mpc, dist_err_mpc = get_candidate_distance(result.target_id, event_id)
    else:
        dist_mpc, dist_err_mpc = dist
    result.dist_mpc, result.dist_err_mpc = dist_mpc, dist_err_mpc
    if not np.isfinite(dist_mpc) or dist_mpc <= 0:
        result.skip_reason = f"no usable distance ({dist_mpc})"
        return result
    if not np.isfinite(dist_err_mpc) or dist_err_mpc <= 0:
        result.skip_reason = f"no usable distance error ({dist_err_mpc})"
        return result

    # --- observation frame -------------------------------------------------
    # Built BEFORE the grid so the grid can be read for just the bands this
    # candidate was actually observed in. A ladder rung is 380M rows and needs
    # ~15 GB materialised in full -- enough to OOM the machine -- while a single
    # band is 1/38 of that. Candidates are rarely observed in more than a few
    # bands, so the band-scoped read is both the cheap and the correct one.
    data_obs = build_data_obs(
        phot, dist_mpc, dist_err_mpc, map_wide_bands=map_wide_bands, mode=mode,
        random_state=random_state,
    )
    if data_obs.empty:
        result.skip_reason = "no observations in a modelled band"
        return result
    result.bands = tuple(sorted(data_obs["filter_mapped"].unique()))

    # --- grid --------------------------------------------------------------
    if grid is None:
        path = resolve_grid(grid_path) if grid_path else select_grid_for_distance(dist_mpc)
        # The filter applies to the grid's own `band` column. In survey mode the
        # observation's filter_mapped IS that band id; in canonical mode it is
        # 'g-band'/'r-band'/..., so it has to be inverted through FILTER_LOOKUP
        # first or the filter would match nothing and the load would come back
        # empty.
        if mode == "survey":
            grid_bands = list(result.bands)
        else:
            from .KilonovaScorer.core import FILTER_LOOKUP

            grid_bands = [k for k, v in FILTER_LOOKUP.items() if v in result.bands]
        # Same time-axis trim as score_event_by_distance: nothing past the last
        # observed epoch (plus one bin, since the last bin ends at t+width) can
        # affect the score, so it is not worth reading.
        obs_tmax = float(data_obs["time_after_gw"].max())
        grid = load_simulation_grid(
            path, bands=grid_bands or None, mode=mode,
            max_time=obs_tmax + time_bin_width if np.isfinite(obs_tmax) else None,
        )
        result.grid = grid_name(path)
    else:
        result.grid = getattr(grid, "attrs", {}).get("name", result.grid)

    # --- score -------------------------------------------------------------
    # Score only bands present on BOTH sides. In survey mode this is the set
    # of real bandpasses the candidate was observed in and the grid simulates;
    # a fixed g/r/i/z list would silently skip atlaso, gotol, ztfg and the rest.
    if band_list is None:
        obs_bands = set(data_obs["filter_mapped"].dropna().unique())
        sim_bands = set(grid["filter_mapped"].dropna().unique())
        bands = tuple(sorted(obs_bands & sim_bands))
        missing = sorted(obs_bands - sim_bands)
        if missing:
            logger.info(
                "%s: %d observation(s) in band(s) the grid does not simulate: %s",
                target_name, int(data_obs["filter_mapped"].isin(missing).sum()), missing,
            )
        if not bands:
            result.skip_reason = (
                f"no band overlap between observations {sorted(obs_bands)} "
                f"and grid {sorted(sim_bands)[:8]}"
            )
            return result
    else:
        bands = tuple(band_list)

    per_obs, summary = kilonovascorer_v3(
        data_obs=data_obs,
        data_sim=grid,
        candidate_name=target_name,
        time_bin_width=time_bin_width,
        band_list=bands,
        random_state=random_state,
        **scorer_kwargs,
    )
    result.n_obs_scored = len(per_obs)
    if per_obs.empty:
        result.skip_reason = "no epoch fell inside the grid's time coverage"
        return result

    if not summary.empty:
        result.final_survivors = {
            band: int(summary[band]["final_n_survivors"]) for band in summary.columns
        }

    # ------------------------------------------------------------------
    # Categorical rejection is an ANSWER, not a failure.
    #
    # When every scored epoch has p_tail_mean == 0, the candidate lies wholly
    # outside the simulated kilonova population at every epoch -- the strongest
    # possible evidence against the hypothesis, and the two-sided tail
    # probability genuinely is 0. But `ivw_stats_logit` filters on
    # `p_tail_mean > 0` and drops those epochs, so the cumulative aggregation
    # has nothing left and returns no score (IMPROVEMENTS.md #1).
    #
    # Reporting that as "unscoreable" would make the most confidently rejected
    # candidates indistinguishable from ones with missing data. They score 0.
    # This is a boundary decision in the integration layer only -- the scorer's
    # own arithmetic is untouched. Set `zero_on_total_rejection=False` to get
    # the raw upstream behaviour back.
    # ------------------------------------------------------------------
    all_rejected = bool((per_obs["p_tail_mean"] <= 0).all())

    binned, reason = _cumulative_score(per_obs, bin_size=time_bin_width)
    if reason:
        result.skip_reason = reason
    if binned is not None and not binned.empty:
        last = binned.iloc[-1]
        if np.isfinite(last["running_mean"]):
            result.score = float(last["running_mean"])
            result.score_err = float(last["running_std"])

    if result.score is None and all_rejected and zero_on_total_rejection:
        result.score = 0.0
        result.score_err = 0.0
        result.skip_reason = None
        result.score_note = (
            f"all {len(per_obs)} epoch(s) fall outside the simulated population "
            "(p_tail = 0): categorical rejection, not missing data"
        )

    # ------------------------------------------------------------------
    # ABC hard penalisation (paper Section 3.5).
    #
    # "In cases where |S_t| = 0 at any epoch t, the candidate is flagged as
    # temporally inconsistent with the kilonova model grid. Even if a high
    # P_near or P_tail score is recorded for a subsequent isolated
    # observation, the cumulative kilonova score is penalized and set to
    # zero."
    #
    # This is the mechanism that separates a kilonova from an impostor whose
    # individual epochs look fine -- it is what collapses SN 2025ulz at
    # t ~ 6 d (paper Figure 5) and every simulated supernova class by 3-4 d
    # (Figure 7). Without it the ranking is the paper's "Base (Uncorrected)"
    # curve, which is not what the validation plots report.
    #
    # It runs last, and deliberately overrides a finite score: a candidate
    # that no single simulation can explain across all its epochs is rejected
    # however plausible any one epoch looked in isolation.
    # ------------------------------------------------------------------
    chain, collapse_time = abc_survival_chain(per_obs)
    result.abc_chain = chain
    result.abc_collapse_time = collapse_time
    if abc_penalty and collapse_time is not None:
        prior = result.score
        result.score = 0.0
        result.score_err = 0.0
        result.skip_reason = None
        was = f" (uncorrected {prior:.4g})" if prior is not None else ""
        result.score_note = (
            f"ABC survival collapsed at t = {collapse_time:.2f} d: no simulated "
            f"kilonova is consistent with every epoch{was}"
        )

    if keep_frames:
        result.per_observation = per_obs
        result.binned = binned
        result.survivors = summary
    return result


def score_event(
    event_id: str,
    grid: Optional[pd.DataFrame] = None,
    grid_path=None,
    viable_only: bool = False,
    target_names: Optional[Iterable[str]] = None,
    min_obs: int = 1,
    min_bands: int = 1,
    dt_min: float = 0.0,
    dt_max: Optional[float] = None,
    snr_min: Optional[float] = None,
    map_wide_bands: bool = False,
    mode: str = "survey",
    keep_frames: bool = False,
    progress: bool = False,
    **kwargs,
) -> pd.DataFrame:
    """Score every candidate of a GW event and return a ranked table.

    Photometry for all candidates is fetched in one query and split per
    candidate, so this costs the same two database round-trips as extracting
    the photometry alone.

    ``min_obs`` / ``min_bands`` are a floor on how much photometry a candidate
    must have, defaulting to **one point in one band** -- the scorer's whole
    point is that it works in the sparse regime GW follow-up actually produces.
    P_tail_KNe is evaluated per observation against the simulated population,
    so a single detection already yields a score; more observations sharpen it
    through the sequential update rather than being required for it. Raise
    these only to deliberately exclude thin light curves. Candidates below the
    floor still appear in the output with a ``skip_reason``, so nothing
    silently disappears. Sorted by score, highest first, unscored last.
    """
    event = get_event(event_id)
    candidates = list(get_candidates(event, viable_only=viable_only, target_names=target_names))
    if not candidates:
        logger.warning("No candidates for %s", event_id)
        return pd.DataFrame()

    phot = get_event_photometry(
        event_id,
        viable_only=viable_only,
        target_names=target_names,
        dt_min=dt_min,
        dt_max=dt_max,
        # Non-detections are kept and scored one-sided as Pr(M_rep > M_lim);
        # see predictive_tail_kde(is_limit=True). For many candidates they are
        # the only photometry inside the grid's 0-10 d window: of the 30
        # S251112cm candidates previously skipped as "no photometry", 98% of
        # their 2,738 points were limits and not one detection fell in window.
        include_limits=True,
        snr_min=snr_min,
    )

    by_target = dict(tuple(phot.groupby("target_name"))) if not phot.empty else {}
    results: List[KilonovaScore] = []

    for i, cand in enumerate(candidates, start=1):
        name = cand.target.name
        if progress:
            logger.info("[%d/%d] %s", i, len(candidates), name)

        lc = by_target.get(name)
        if lc is None or lc.empty:
            results.append(
                KilonovaScore(event_id, name, target_id=cand.target_id, skip_reason="no photometry")
            )
            continue
        if len(lc) < min_obs or lc["filter"].nunique() < min_bands:
            results.append(
                KilonovaScore(
                    event_id, name, target_id=cand.target_id,
                    n_obs_supplied=len(lc),
                    skip_reason=f"too sparse ({len(lc)} pts, {lc['filter'].nunique()} bands)",
                )
            )
            continue

        try:
            results.append(
                score_candidate(
                    event_id, name, grid=grid, grid_path=grid_path, phot=lc,
                    dt_min=dt_min, dt_max=dt_max, snr_min=snr_min,
                    map_wide_bands=map_wide_bands, mode=mode,
                    keep_frames=keep_frames, **kwargs,
                )
            )
        except Exception as exc:  # noqa: BLE001 - one bad candidate must not stop the run
            logger.exception("Scoring failed for %s", name)
            results.append(
                KilonovaScore(
                    event_id, name, target_id=cand.target_id,
                    skip_reason=f"error: {type(exc).__name__}: {exc}",
                )
            )

    table = pd.DataFrame([r.as_row() for r in results])
    if keep_frames:
        table.attrs["results"] = {r.target_name: r for r in results}
    return table.sort_values("score", ascending=False, na_position="last").reset_index(drop=True)


def score_event_by_distance(
    event_id: str,
    grid_path=None,
    viable_only: bool = False,
    target_names: Optional[Iterable[str]] = None,
    min_obs: int = 1,
    min_bands: int = 1,
    dt_min: float = 0.0,
    dt_max: Optional[float] = None,
    snr_min: Optional[float] = None,
    map_wide_bands: bool = False,
    mode: str = "survey",
    time_bin_width: float = 0.2,
    max_bands_per_load: int = 6,
    random_state=42,
    keep_frames: bool = False,
    progress: bool = False,
    **kwargs,
) -> pd.DataFrame:
    """Score every candidate of a GW event, grouped by simulation grid.

    Same contract as :func:`score_event` -- one row per candidate, unscoreable
    ones carrying a ``skip_reason`` rather than vanishing -- but ordered so each
    grid is read **once**.

    ``score_event`` scores candidates in name order and lets ``score_candidate``
    pull a grid per candidate, scoped to that candidate's bands. Those loads are
    memoised on ``(path, bands)``, so the cache only helps candidates whose band
    sets match exactly. Measured on ``S251112cm``: 269 scoreable candidates over
    **101 distinct band sets** across 3 rungs, i.e. 101 loads of ~8-50 s each,
    and a memo that grows all run because nothing is ever evicted -- which is
    how a bulk run reaches an OOM.

    Here candidates are bucketed by the rung their own distance selects, the
    buckets are walked in distance order, and each rung is read once for the
    *union* of the bands its candidates need, then handed to every candidate in
    the bucket. Three loads instead of 101, and the previous rung is dropped
    before the next is read, so peak memory is one grid rather than an
    accumulating cache.

    Distances are resolved once, up front, and passed in -- ``score_candidate``
    would otherwise re-derive each one, which is a database round trip per
    candidate.

    Candidates below the ``min_obs``/``min_bands`` floor never reach any of
    this: they are filtered before the distance pass, so they cost one
    dictionary lookup each. That floor defaults to a single point in a single
    band, so in practice only candidates with no usable photometry at all are
    dropped here.
    """
    from .KilonovaScorer.grids import clear_cache

    event = get_event(event_id)
    candidates = list(get_candidates(event, viable_only=viable_only, target_names=target_names))
    if not candidates:
        logger.warning("No candidates for %s", event_id)
        return pd.DataFrame()

    phot = get_event_photometry(
        event_id,
        viable_only=viable_only,
        target_names=target_names,
        dt_min=dt_min,
        dt_max=dt_max,
        # Non-detections are kept and scored one-sided as Pr(M_rep > M_lim);
        # see predictive_tail_kde(is_limit=True). For many candidates they are
        # the only photometry inside the grid's 0-10 d window: of the 30
        # S251112cm candidates previously skipped as "no photometry", 98% of
        # their 2,738 points were limits and not one detection fell in window.
        include_limits=True,
        snr_min=snr_min,
    )
    by_target = dict(tuple(phot.groupby("target_name"))) if not phot.empty else {}

    results: List[KilonovaScore] = []
    scoreable: List[Tuple[Any, pd.DataFrame]] = []

    # --- cheap rejections first, before any distance or grid work ----------
    for cand in candidates:
        name = cand.target.name
        lc = by_target.get(name)
        if lc is None or lc.empty:
            results.append(
                KilonovaScore(event_id, name, target_id=cand.target_id, skip_reason="no photometry")
            )
            continue
        if len(lc) < min_obs or lc["filter"].nunique() < min_bands:
            results.append(
                KilonovaScore(
                    event_id, name, target_id=cand.target_id,
                    n_obs_supplied=len(lc),
                    skip_reason=f"too sparse ({len(lc)} pts, {lc['filter'].nunique()} bands)",
                )
            )
            continue
        scoreable.append((cand, lc))

    logger.info(
        "%s: %d candidates, %d scoreable after the sparsity cut",
        event_id, len(candidates), len(scoreable),
    )
    if not scoreable:
        return _results_table(results, keep_frames)

    # --- distances, once ---------------------------------------------------
    buckets: Dict[Any, List[Tuple[Any, pd.DataFrame, float, float]]] = {}
    for cand, lc in scoreable:
        name = cand.target.name
        try:
            dist_mpc, dist_err_mpc = get_candidate_distance(cand.target_id, event_id)
        except Exception as exc:  # noqa: BLE001
            results.append(
                KilonovaScore(event_id, name, target_id=cand.target_id,
                              skip_reason=f"distance lookup failed: {type(exc).__name__}: {exc}")
            )
            continue
        if not np.isfinite(dist_mpc) or dist_mpc <= 0:
            results.append(
                KilonovaScore(event_id, name, target_id=cand.target_id, dist_mpc=dist_mpc,
                              n_obs_supplied=len(lc), skip_reason=f"no usable distance ({dist_mpc})")
            )
            continue
        if not np.isfinite(dist_err_mpc) or dist_err_mpc <= 0:
            results.append(
                KilonovaScore(event_id, name, target_id=cand.target_id, dist_mpc=dist_mpc,
                              dist_err_mpc=dist_err_mpc, n_obs_supplied=len(lc),
                              skip_reason=f"no usable distance error ({dist_err_mpc})")
            )
            continue

        try:
            path = resolve_grid(grid_path) if grid_path else select_grid_for_distance(dist_mpc)
        except Exception as exc:  # noqa: BLE001 - no grid at all, or none close enough
            results.append(
                KilonovaScore(event_id, name, target_id=cand.target_id, dist_mpc=dist_mpc,
                              dist_err_mpc=dist_err_mpc, n_obs_supplied=len(lc),
                              skip_reason=f"no usable grid: {exc}")
            )
            continue
        buckets.setdefault(path, []).append((cand, lc, dist_mpc, dist_err_mpc))

    # --- grid loads, nearest rung first ------------------------------------
    ordered = sorted(buckets, key=lambda p: grid_distance_mpc(p))
    for rung_i, path in enumerate(ordered, start=1):
        members = buckets[path]

        # the bands -- and the last epoch -- each candidate at this rung needs
        bands_by_member = []
        for member in members:
            frame = to_scorer_frame(member[1], map_wide_bands=map_wide_bands, mode=mode)
            bands = (
                frozenset(frame["filter_mapped"].dropna().unique())
                if not frame.empty
                else frozenset()
            )
            tmax = (
                float(frame["time_after_gw"].max())
                if not frame.empty and "time_after_gw" in frame.columns
                else None
            )
            bands_by_member.append((member, bands, tmax))

        no_band = [m for m, b, _ in bands_by_member if not b]
        for cand, lc, d, derr in no_band:
            results.append(
                KilonovaScore(event_id, cand.target.name, target_id=cand.target_id,
                              dist_mpc=d, dist_err_mpc=derr, n_obs_supplied=len(lc),
                              skip_reason="no observations in a modelled band")
            )
        with_band = [(m, b, t) for m, b, t in bands_by_member if b]
        if not with_band:
            continue

        for chunk_i, (chunk, chunk_bands, chunk_tmax) in enumerate(
            _chunk_by_bands(with_band, max_bands_per_load), start=1
        ):
            if mode != "survey":
                from .KilonovaScorer.core import FILTER_LOOKUP

                grid_bands = [k for k, v in FILTER_LOOKUP.items() if v in chunk_bands]
            else:
                grid_bands = sorted(chunk_bands)

            # Read only the part of the time axis these candidates can reach.
            # The scorer bins observations into [t, t+time_bin_width) windows and
            # never looks past the last one, so simulation rows beyond that are
            # decompressed, converted and binned for nothing. A grid spans 0-10 d
            # at 0.01 d resolution, so a chunk whose last epoch is 3 d reads ~30%
            # of the rows -- which is what keeps a load that cannot be split by
            # band (a single candidate observed in more than max_bands_per_load
            # bands forces its own band count) inside the memory budget.
            load_max_time = (
                chunk_tmax + time_bin_width if chunk_tmax is not None and np.isfinite(chunk_tmax)
                else None
            )
            logger.info(
                "[rung %d/%d load %d] %s: %d candidates, %d band(s), t<=%s",
                rung_i, len(ordered), chunk_i, grid_name(path), len(chunk), len(grid_bands),
                f"{load_max_time:.2f} d" if load_max_time is not None else "all",
            )
            try:
                grid = load_simulation_grid(
                    path, bands=grid_bands, mode=mode, max_time=load_max_time
                )
            except Exception as exc:  # noqa: BLE001 - one bad load must not stop the event
                logger.exception("Loading %s failed", grid_name(path))
                for cand, lc, d, derr in chunk:
                    results.append(
                        KilonovaScore(event_id, cand.target.name, target_id=cand.target_id,
                                      dist_mpc=d, dist_err_mpc=derr, n_obs_supplied=len(lc),
                                      skip_reason=f"grid load failed: {type(exc).__name__}: {exc}")
                    )
                continue

            for i, (cand, lc, dist_mpc, dist_err_mpc) in enumerate(chunk, start=1):
                name = cand.target.name
                if progress:
                    logger.info("  [%d/%d] %s", i, len(chunk), name)
                try:
                    results.append(
                        score_candidate(
                            event_id, name, grid=grid, phot=lc,
                            dist=(dist_mpc, dist_err_mpc),
                            dt_min=dt_min, dt_max=dt_max, snr_min=snr_min,
                            map_wide_bands=map_wide_bands, mode=mode,
                            time_bin_width=time_bin_width,
                            random_state=random_state,
                            keep_frames=keep_frames, **kwargs,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - one bad candidate must not stop the run
                    logger.exception("Scoring failed for %s", name)
                    results.append(
                        KilonovaScore(event_id, name, target_id=cand.target_id,
                                      skip_reason=f"error: {type(exc).__name__}: {exc}")
                    )

            # drop this grid before reading the next, so peak memory is one load
            del grid
            clear_cache()

    return _results_table(results, keep_frames)


def _chunk_by_bands(with_band, max_bands: int):
    """Group candidates so each grid load covers at most ``max_bands`` bands.

    The union of bands over a whole rung is what a single load would have to
    read, and it does not stay small: on ``S251112cm`` every rung's union is
    12-13 bands, which measured **12.9 GB of peak RSS** on a 15 GB machine --
    pyarrow's transient peak during the read is well above the final frame, so
    the union is not something to hand to the reader unbounded.

    Candidates are sorted by band set so ones needing the same bands land in the
    same load, then packed greedily until adding the next would exceed
    ``max_bands``. Identical band sets therefore still share a load -- which is
    the case that matters, since 87% of TROVE photometry is ATLAS c/o -- while
    the tail of odd single-band candidates is spread over a few small loads
    instead of forcing one enormous one.

    ``max_bands`` at or above the rung's union size collapses to exactly one
    load, i.e. the unbounded behaviour, so this only ever costs extra reads
    where an unbounded read would not have fit.

    ``max_bands`` is a target, not a guarantee: a *single* candidate observed in
    more bands than that still needs all of them in one grid to be scored, so it
    forms an oversized load of its own. The caller bounds those with ``max_time``
    instead.

    Parameters
    ----------
    with_band : sequence of (member, bands, tmax)
        ``tmax`` is the candidate's last epoch in days after the GW, or None.

    Returns
    -------
    list of (members, bands, tmax)
        ``tmax`` is the latest epoch over the chunk -- the point past which no
        candidate in it can use a simulation row -- or None if unknown for any
        member, in which case the caller must read the whole time axis.
    """
    chunks = []
    current, current_bands, current_tmax = [], set(), 0.0
    for member, bands, tmax in sorted(with_band, key=lambda mb: sorted(mb[1])):
        if current and len(current_bands | bands) > max_bands:
            chunks.append((current, current_bands, current_tmax))
            current, current_bands, current_tmax = [], set(), 0.0
        current.append(member)
        current_bands |= bands
        # One unknown epoch poisons the chunk: without it there is no safe
        # ceiling, so the whole axis has to be read.
        if current_tmax is not None:
            current_tmax = None if tmax is None else max(current_tmax, tmax)
    if current:
        chunks.append((current, current_bands, current_tmax))
    return chunks


def _results_table(results: List[KilonovaScore], keep_frames: bool) -> pd.DataFrame:
    """Flatten scoring results into the ranked table both entry points return."""
    table = pd.DataFrame([r.as_row() for r in results])
    if keep_frames:
        table.attrs["results"] = {r.target_name: r for r in results}
    if table.empty:
        return table
    return table.sort_values("score", ascending=False, na_position="last").reset_index(drop=True)
