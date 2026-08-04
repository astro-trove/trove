"""
core2.py — KilonovaScorer core pipeline (single scorer).

This is the sole scoring module.  The former core.py (kilonovascorer_v1) was
removed during the ISSUE #15 consolidation (IMPROVEMENTS.md §15): v3 is the
retained base (KDE cache, pre-grouping, paper-aligned _KNe column naming), and
__init__.py now imports explicitly from this module only -- no wildcard, no
import-order-dependent shadowing.  plotting.py and the notebook use the _KNe
column names.

ISSUE #15 residual (not yet done): add a minimal pytest suite (synthetic grid
with known answers -- P_tail ~ 1 at the population median, ~ 0 at 10 sigma;
MC-vs-analytic agreement gating #6; ivw_stats_logit schema consistency gating
#1; monotone survivor counts; same seed -> identical output gating #11), delete
the remaining commented-out blocks in utils.py / simulation.py (they are in git
history), and pin pandas (or at least test against 2.x).  Issues #1, #6, #7, #8
change reported scores -- add the tests BEFORE applying them so a regression is
distinguishable from an intended change.

Implements:
  - JSON / CSV photometry loading with absolute magnitude computation
  - LSST-like cadence downsampling
  - P_tail_KNe and P_near_KNe scoring via noise-convolved KDE (predictive_tail_kde)
  - ABC sequential survival diagnostic (overlap_chain)
  - Logit-space inverse-variance weighted cumulative scoring
"""

# ---------------------------------------------------------------------------
# Standard library
# ---------------------------------------------------------------------------
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Third-party
# ---------------------------------------------------------------------------
import numpy as np
import pandas as pd
from scipy.stats import gaussian_kde

# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------
from .utils import *  # noqa: F401,F403  (decorators and helpers)

logger = logging.getLogger(__name__)

FILTER_LOOKUP = {
    'lsstg': 'g-band', 'g-ztf': 'g-band', 'ztfg': 'g-band', 'g-p1': 'g-band', 'g': 'g-band',
    'lsstr': 'r-band', 'r-ztf': 'r-band', 'ztfr': 'r-band', 'r-p1': 'r-band', 'r': 'r-band',
    'lssti': 'i-band', 'i-ztf': 'i-band', 'ztfi': 'i-band', 'i-p1': 'i-band', 'i': 'i-band',
    'lsstz': 'z-band', 'z-ztf': 'z-band', 'ztfz': 'z-band', 'z-p1': 'z-band', 'z': 'z-band'
}

# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------

def arcade_progress_bar(current: int, total: int, bar_length: int = 30) -> None:
    """Print an arcade-style progress bar to stdout."""
    percent = current / total
    filled = int(bar_length * percent)
    bar = "█" * filled + "-" * (bar_length - filled)
    sys.stdout.write(f"\r[ {bar} ] {percent * 100:6.2f}% ⬛")
    sys.stdout.flush()
    if current == total:
        sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# Photometry loading
# ---------------------------------------------------------------------------

def parse_json_photometry(file_path: Path, merger_mjd: float) -> pd.DataFrame:
    """
    Extract photometry from a JSON file following the standard schema.

    Returns a DataFrame with raw band names ready for FILTER_LOOKUP mapping.
    Pre-merger timestamps and upper limits are excluded.

    Parameters
    ----------
    file_path : Path
        Path to the JSON photometry file.
    merger_mjd : float
        MJD of the GW merger event; observations before this are discarded.

    Returns
    -------
    pd.DataFrame
        Columns: time, time_after_gw, magnitude, e_magnitude, band,
        instrument, telescope.  Empty DataFrame on parse failure.
    """
    try:
        with open(file_path, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        logger.error("Failed to decode JSON from %s", file_path)
        return pd.DataFrame()

    if "photometry" not in data:
        logger.warning("No 'photometry' key found in %s", file_path)
        return pd.DataFrame()

    records = []
    for entry in data["photometry"]:
        # 1. Validate timestamp
        t = entry.get("timestamp")
        if t is None or t < merger_mjd:
            continue

        # 2. Extract nested magnitude / filter
        val = entry.get("value", {})
        app_mag = val.get("magnitude")
        app_err = val.get("error", 0)
        raw_filter = val.get("filter")

        # =================================================================
        # ISSUE #9 — Upper limits and non-detections are DISCARDED.
        # See IMPROVEMENTS.md §9.   Impact: MED | Effort: LOW-MEDIUM
        # -----------------------------------------------------------------
        # Non-detections are often the most constraining data in GW
        # follow-up: a deep limit hours before first detection bounds the
        # rise time; a non-detection in one band alongside a detection in
        # another is a hard colour limit (see ISSUE #4).  Dropping them
        # discards much of the information available on night one --
        # precisely the regime this tool targets.
        #
        # FIX: keep them with an `is_limit` flag, convert through the same
        # distance-modulus path, and branch in the scorer:
        #   PPD framework:        P_consistent = Pr(Y > M_lim) = 1 - F(M_lim)
        #   Likelihood (#5):      log L_j = log Phi((m_j(t) - M_lim)/sigma_lim)
        # Document the limit convention (3-sigma vs 5-sigma) -- surveys
        # differ, and the wrong assumption biases the constraint.
        # =================================================================
        # 3. Quality control — skip upper limits and missing data
        if app_mag is None or raw_filter is None or val.get("upper_limit", False):
            continue

        # 4. Append standardised record
        # "band" kept as raw string (e.g. 'ztfg') for downstream FILTER_LOOKUP.
        records.append({
            "time": t,
            "time_after_gw": t - merger_mjd,
            "magnitude": float(app_mag),
            "e_magnitude": float(app_err),
            "band": str(raw_filter).lower().strip(),
            "instrument": entry.get("instrument", "unknown"),
            "telescope": entry.get("telescope", "unknown"),
        })

    return pd.DataFrame(records)


def load_observations(
    file_path,
    merger_mjd: float,
    dist_mpc: float,
    dist_err_mpc: float,
) -> pd.DataFrame:
    """
    Load and standardise photometric observations, then compute absolute magnitudes.

    Supports .csv, .parquet and .json input files.  Absolute magnitudes are
    derived via ``compute_abs_mag_samples`` (from utils), which is expected to
    accept array inputs for vectorised computation.

    Parameters
    ----------
    file_path : str or Path
        Path to the photometry file (.csv, .parquet or .json).
    merger_mjd : float
        MJD of the GW merger event.
    dist_mpc : float
        Luminosity distance in Mpc.
    dist_err_mpc : float
        Uncertainty on the luminosity distance in Mpc.

    Returns
    -------
    pd.DataFrame
        Standardised DataFrame including ``absolute_magnitude`` and
        ``absolute_magnitude_error`` columns.
    """
    path = Path(file_path)
    logger.info("Loading observations from %s", path)

    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path)
        df["time_after_gw"] = df["time"] - merger_mjd
    elif suffix in (".parquet", ".pq"):
        df = pd.read_parquet(path)
        df["time_after_gw"] = df["time"] - merger_mjd
    elif suffix == ".json":
        df = parse_json_photometry(path, merger_mjd)
    else:
        raise ValueError(
            f"Unsupported file format: {path.suffix} (expected .csv, .parquet or .json)"
        )

    if df.empty:
        logger.warning("No valid observations loaded from %s", path)
        return df

    # Vectorised absolute-magnitude computation — compute_abs_mag_samples must
    # accept 1-D arrays and return (abs_mag_array, abs_err_array).
    abs_mag, abs_err = compute_abs_mag_samples(  # noqa: F821 (from utils.*)
        df["magnitude"].to_numpy(),
        df["e_magnitude"].to_numpy(),
        dist_mpc=dist_mpc,
        dist_err_mpc=dist_err_mpc,
    )
    df["absolute_magnitude"] = abs_mag
    df["absolute_magnitude_error"] = abs_err

    return df


# ---------------------------------------------------------------------------
# LSST-like cadence downsampling
# ---------------------------------------------------------------------------

def preprocess_lsst_like(
    data_obs,
    bands=("g-band", "z-band"),
    time_col="time_after_gw",
    band_col="filter_mapped",
    strategy="earliest",
):
    """
    Downsample high-cadence observational data to mimic a standard LSST-like
    survey cadence (typically one observation per band per night).

    Primarily used to make over-sampled events (like AT2017gfo) comparable to
    standard kilonova candidates by reducing data density.

    Parameters
    ----------
    data_obs : pandas.DataFrame
        Raw observational data containing timestamps and filter bands.
    bands : tuple of str, default=("g-band", "z-band")
        The filters to retain in the processed dataset.
    time_col : str, default="time_after_gw"
        Column name for time since the merger event (days).
    band_col : str, default="filter_mapped"
        Column name identifying the filter/band for each observation.
    strategy : {'earliest', 'snr', 'random'}, default="earliest"
        Rule for selecting a single point when multiple observations fall on
        the same night:
        - "earliest": smallest timestamp.
        - "snr":      highest signal-to-noise ratio (1 / e_magnitude).
        - "random":   random draw (seed 42 for reproducibility).

    Returns
    -------
    pandas.DataFrame
        Downsampled data conforming to the chosen strategy, sorted by time.
    """
    df = data_obs.copy()

    # 1. Keep only desired bands
    df = df[df[band_col].isin(bands)].copy()

    # 2. Define LSST-style observing day
    df["day"] = np.floor(df[time_col]).astype(int)

    # 3. Sort for deterministic selection
    df = df.sort_values(time_col)

    # 4. Select at most one obs per (day, band)
    if strategy == "earliest":
        df_lsst = df.groupby(["day", band_col], as_index=False).first()

    elif strategy == "snr":
        df["snr"] = 1.0 / df["e_magnitude"]
        df_lsst = (
            df.sort_values("snr", ascending=False)
              .groupby(["day", band_col], as_index=False)
              .first()
        )
        df_lsst = df_lsst.drop(columns="snr")

    elif strategy == "random":
        df_lsst = df.groupby(["day", band_col], as_index=False).sample(
            n=1, random_state=42
        )

    else:
        raise ValueError("strategy must be 'earliest', 'snr', or 'random'")

    # 5. Final cleanup
    return df_lsst.sort_values(time_col).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Core scoring: P_tail_KNe and P_near_KNe
# ---------------------------------------------------------------------------

def predictive_tail_kde(
    sim_values: np.ndarray,
    M_obs: float,
    sigma_obs: float,
    k: float = 1.5,
    n_sim: int = 50000,
    n_obs: int = 100,
    kde: Optional[gaussian_kde] = None,
    rng: Optional[np.random.Generator] = None,
) -> Dict[str, float]:
    """
    Compute P_tail_KNe and P_near_KNe from the noise-convolved prior predictive
    distribution (PPD) via KDE.

    Implements the two-sided tail-area probability (paper eq. 2)::

        F(M_obs) = Pr(M_rep <= M_obs)
        P_tail_KNe = 2 * min(F(M_obs), 1 - F(M_obs))

    and the ROPE-based local consistency score (paper eq. 4)::

        P_near_KNe = Pr(M_rep in [M_obs - k*sigma_obs, M_obs + k*sigma_obs])

    Both are evaluated on the noise-convolved PPD:
        Y = X* + epsilon,  X* ~ KDE(sim_values),  epsilon ~ N(0, sigma_obs)

    Uncertainty on P_tail_KNe is propagated by sampling N_obs realisations of
    M_obs from N(M_obs, sigma_obs), as described in the paper.  The broadcast
    comparison (N_obs, 1) vs (n_sim,) -> (N_obs, n_sim) is fully vectorised.

    P_near_KNe is a *local*, per-observation score and is intentionally not
    aggregated across bands or epochs (paper Section 2).  Only P_tail_KNe
    (via p_tail_mean / p_tail_std) feeds into the cumulative logit-space score.

    A pre-fitted KDE can be supplied via ``kde`` to avoid redundant fitting when
    multiple observations share the same simulation time bin.

    Parameters
    ----------
    sim_values : np.ndarray
        Simulated absolute magnitudes from the PPD for the relevant time bin.
    M_obs : float
        Observed absolute magnitude (paper notation: M_obs).
    sigma_obs : float
        Observational uncertainty on M_obs (paper notation: sigma_obs).
    k : float
        ROPE half-width factor.  Paper fiducial value: 1.5.
    n_sim : int
        Number of Monte Carlo draws for the noise-convolved PPD.
    n_obs : int
        Number of M_obs realisations for P_tail_KNe uncertainty estimation.
        Paper value: N_obs = 100.
    kde : gaussian_kde or None
        Pre-fitted KDE object.  If None, a new KDE is fitted to ``sim_values``.

    Returns
    -------
    dict with keys:
        F_hat        – empirical CDF F(M_obs) under the noise-convolved PPD.
        p_tail_KNe   – two-sided tail probability at M_obs (point estimate).
        p_tail_mean  – mean P_tail_KNe over n_obs M_obs uncertainty samples.
        p_tail_std   – std  P_tail_KNe over n_obs M_obs uncertainty samples.
        p_near_KNe   – ROPE-based local consistency score P_near_KNe.

    Raises
    ------
    ValueError
        If ``sim_values`` is empty or ``sigma_obs`` is non-positive.
    """
    sim_values = np.asarray(sim_values)
    if sim_values.size == 0:
        raise ValueError("sim_values cannot be empty.")
    if sigma_obs <= 0:
        raise ValueError("sigma_obs must be positive.")

    # =====================================================================
    # ISSUE #6 — This Monte Carlo estimates a quantity available in CLOSED
    # FORM.  See IMPROVEMENTS.md §6.   Impact: MED | Effort: LOW
    # ---------------------------------------------------------------------
    # A Gaussian KDE with bandwidth h over points {m_i} is a Gaussian
    # mixture:            p(x) = (1/N) Sum_i N(x | m_i, h^2)
    # Convolving with independent Gaussian noise gives another mixture:
    #                     p(y) = (1/N) Sum_i N(y | m_i, h^2 + sigma_obs^2)
    # so BOTH quantities computed below are exact, not sampled:
    #
    #     s          = sqrt(h^2 + sigma_obs^2)
    #     F(M_obs)   = (1/N) Sum_i Phi( (M_obs - m_i) / s )
    #     P_near     = (1/N) Sum_i [ Phi((M_obs + k*sig - m_i)/s)
    #                              - Phi((M_obs - k*sig - m_i)/s) ]
    #
    # h is available as `kde.factor * np.std(sim_values, ddof=1)` (Scott's
    # rule by default) -- confirm the convention against the scipy version
    # in use, as covariance_factor semantics have varied.
    #
    # The closed form is better on EVERY axis at once:
    #   - Deterministic: removes ISSUE #11 at the source for these metrics
    #     rather than papering over it with a seed.
    #   - Exact: the notebook runs at n_kde_sim = 5000, giving a standard
    #     error on F_hat of ~sqrt(F(1-F)/5000) ~ 0.007, which the logit
    #     transform then amplifies near the boundaries where
    #     dz/dp = 1/(p(1-p)) is large.  A real contributor to score jitter.
    #   - Faster: one vectorised Phi over N sim points vs. drawing and
    #     comparing n_kde_sim samples.  Sim count per bin is usually well
    #     below n_kde_sim.
    #   - Removes the n_kde_sim hyperparameter and its accuracy/speed
    #     tradeoff.
    # This also makes the ISSUE #3 calibration study computationally
    # feasible, since it needs thousands of full pipeline runs.
    #
    # FIX: replace steps 1-3 with, using scipy.special.ndtr:
    #     s      = np.hypot(bandwidth, sigma_obs)
    #     F_hat  = float(ndtr((M_obs - m) / s).mean())
    #     p_tail = 2.0 * min(F_hat, 1.0 - F_hat)
    #     p_near = float((ndtr((M_obs + k*sigma_obs - m)/s)
    #                     - ndtr((M_obs - k*sigma_obs - m)/s)).mean())
    # Validate by running both over a few hundred real bins and confirming
    # agreement within MC error (good first unit test -- see ISSUE #15).
    #
    # NOTE: the `kde` cache parameter (and the kde_cache dict in
    # kilonovascorer_v3) exists purely to amortise gaussian_kde fitting.
    # Under the analytic form only the BANDWIDTH is needed, so the cache
    # can be reduced to a per-bin float -- simpler and cheaper than caching
    # KDE objects.
    #
    # CAVEAT: a fixed-bandwidth Gaussian KDE over-smooths genuinely
    # multimodal distributions, and the simulated magnitude distribution in
    # a bin CAN be bimodal where blue/red ejecta components alternate in
    # dominance.  The analytic form reproduces the KDE exactly, INCLUDING
    # its smoothing -- it does not fix this.  Worth separately checking
    # whether Scott's rule is appropriate here (compare a few bins against
    # their histograms, or against a cross-validated bandwidth).
    #
    # ISSUE #11 — FIXED.  Every draw below comes from `rng`, so a seeded caller
    # gets reproducible scores.  Note kde.resample() is a THIRD source of
    # randomness beyond the two np.random.normal calls, and is seeded here too;
    # missing it would leave the result non-deterministic despite the rest.
    # Still superseded by the analytic fix, which needs no RNG at all.
    if rng is None:
        rng = np.random.default_rng()


    # =====================================================================
    # 1. Build noise-convolved PPD: Y = X* + epsilon, X* ~ KDE, epsilon ~ N(0, sigma_obs)

    # KDE - Kernel Density Estimation
    # KDE creates a probability distribution function from a bunch of samples of the sim_values
    # by creating something that is smooth/differentiable
    if kde is None:
        kde = gaussian_kde(sim_values)
    # Samples n_sim amount of samples from the generated PDF of the sim_values
    # This is an array of n_sim amount of magnitude values sampled from the distribution from sim_values
    x_star = kde.resample(n_sim, seed=rng)[0]
    # Adds some normally distributed noise with sigma_obs to the distribution
    y_dist = x_star + rng.normal(0, sigma_obs, size=n_sim)

    # 2. P_tail_KNe — two-sided tail probability at M_obs (paper eq. 2)
    # y_dist <= M_obs is a n_sim long boolean array with 1s and 0s
    # np.mean() determines the ratio of sampled y greater than M_obs
    # exactly a monte carlo estimate of F, the precise integral
    F_hat = float(np.mean(y_dist <= M_obs))
    p_tail_KNe = 2.0 * min(F_hat, 1.0 - F_hat)

    # 3. P_near_KNe — fraction of noise-convolved PPD draws within the ROPE
    #    (paper eq. 4; k=1.5 fiducial).  Not aggregated across epochs.
    # Similarly, a monte carlo estimate of the number of y in the ROPE of M_obs
    p_near_KNe = float(np.mean(np.abs(y_dist - M_obs) <= k * sigma_obs))

    # =====================================================================
    # ISSUE #8 — sigma_obs enters the p_tail_std calculation TWICE.
    # See IMPROVEMENTS.md §8.   Impact: MED | Effort: LOW (decision is hard)
    # ---------------------------------------------------------------------
    # Step 1 already convolved the observational error into the reference
    # distribution:
    #     y_dist = x_star + np.random.normal(0, sigma_obs, ...)
    # which is the documented noise-convolved PPD and is correct.  The line
    # below then perturbs the observation by THE SAME sigma_obs again.
    #
    # There is a defensible reading -- this estimates the sampling
    # uncertainty OF THE STATISTIC given that M_obs is itself a noisy
    # realisation -- but the two uses are not independent, and the
    # combination has a systematic consequence:
    #
    #   p_tail is concave in M_obs near the distribution centre and convex
    #   in the tails.  Averaging over M_obs_samples therefore does NOT
    #   return p_tail(M_obs).  In the tails, where P_tail is small, the
    #   averaging pulls p_tail_mean UPWARD, because draws scattered toward
    #   the centre gain more than draws scattered further out lose.
    #
    # Since p_tail_mean (not p_tail_KNe) is what feeds the cumulative score
    # via ivw_stats_logit, this bias propagates straight into the headline
    # number -- and it acts in the SAME DIRECTION as ISSUE #1, toward more
    # kilonova-consistent scores.  The magnitude is not characterised
    # anywhere in the repo.
    #
    # FIX: first decide what p_tail_std is MEANT to represent and state it
    # in the docs.  Three coherent options:
    #   A. MC estimation error only -> keep the convolution in y_dist, drop
    #      this resampling, and use sqrt(F(1-F)/n_sim) or a bootstrap.
    #      Note under the ISSUE #6 analytic form this error is zero, so the
    #      IVW weights would need rethinking (arguably driven by sigma_obs
    #      and the local PPD density instead).
    #   B. Sensitivity of the statistic to the observation -> then y_dist
    #      should NOT include the noise convolution, and the spread over
    #      M_obs_samples carries the observational error exactly once.  This
    #      is the cleanest reading and closest to a standard predictive
    #      p-value.  RECOMMENDED.
    #   C. Keep as-is deliberately, documented as conservative smoothing --
    #      but then quantify the bias, e.g. plot (p_tail_mean - p_tail_KNe)
    #      against p_tail_KNe over real bins, and report it.
    #
    # The ISSUE #3 calibration test settles this empirically: run the null
    # calibration under each variant and see which yields a uniform result.
    # =====================================================================
    # 4. P_tail_KNe uncertainty — sample N_obs realisations of M_obs (paper Section 2)
    #    Broadcast: (n_obs, 1) vs (n_sim,) -> (n_obs, n_sim)
    # Monte Carlo estimates of the uncertainties
    M_obs_samples = rng.normal(M_obs, sigma_obs, n_obs)
    F_hat_samples = (y_dist <= M_obs_samples[:, np.newaxis]).mean(axis=1)
    p_tail_samples = 2.0 * np.minimum(F_hat_samples, 1.0 - F_hat_samples)

    return {
        "F_hat": F_hat,
        "p_tail_KNe": p_tail_KNe,
        "p_tail_mean": float(np.mean(p_tail_samples)),
        "p_tail_std": float(np.std(p_tail_samples)),
        "p_near_KNe": p_near_KNe,
    }


# ---------------------------------------------------------------------------
# ABC diagnostic helpers
# ---------------------------------------------------------------------------

def compute_consistent_ids_anyhit(
    sim_band: pd.DataFrame,
    bin_idx: int,
    M_obs: float,
    sigma_obs: float,
    overlap_k: float = 2.0,
) -> List:
    """
    Return simulation IDs whose predicted magnitude falls within the ROPE at
    the given time bin (conservative "any-hit" criterion).

    The ROPE acceptance kernel is:
        |M_rep - M_obs| <= overlap_k * sigma_obs

    Parameters
    ----------
    sim_band : pd.DataFrame
        Simulation data for a single photometric band, with ``time_bin``,
        ``sample_id``, and ``absolute_magnitude`` columns.
    bin_idx : int
        Time-bin index to filter on.
    M_obs : float
        Observed absolute magnitude.
    sigma_obs : float
        Observational uncertainty.
    overlap_k : float
        ROPE half-width multiplier (sigma units).

    Returns
    -------
    list
        Unique sample IDs consistent with the ROPE at this epoch.
    """
    # =====================================================================
    # ISSUE #13 — This scan DEFEATS v3's own pre-grouping optimisation.
    # See IMPROVEMENTS.md §13.   Impact: LOW (perf only) | Effort: LOW
    # ---------------------------------------------------------------------
    # kilonovascorer_v3 builds `sim_groups` for O(1) bin lookup and uses it
    # for the KDE path -- but then passes the FULL band DataFrame to this
    # function, which performs its own boolean scan over every simulation
    # row in the band on EVERY observation.  For a 100k-sample grid that is
    # tens of millions of rows per observation.  Results are correct; the
    # cost is not.  This is likely the dominant runtime in v3, undercutting
    # the refactor's stated purpose, and it directly limits the ISSUE #3
    # calibration study, which needs thousands of full runs.
    #
    # FIX: change the signature to accept the pre-filtered bin and pass
    # sim_groups[bin_idx] from the caller:
    #     def compute_consistent_ids_anyhit(sim_bin, M_obs, sigma_obs, overlap_k=2.0)
    # This symbol is exported at package level, so either keep a
    # backward-compatible wrapper or bump the API version deliberately, and
    # verify no other callers.  ISSUE #7's array representation removes the
    # concern entirely.
    #
    # ISSUE #12 — the "any-hit" criterion below also depends on the GRID'S
    # TIME RESOLUTION.  See IMPROVEMENTS.md §12.  Impact: MED | Effort: LOW
    # (interim) / subsumed by #7.
    # ---------------------------------------------------------------------
    # A sample_id is retained if ANY of its points in the bin lands inside
    # the ROPE.  With the default grid (1000 steps over 10 days) and
    # time_bin_width = 0.2 d, each simulation contributes ~20 points per
    # bin, sampled across a window in which the light curve may move
    # substantially.  The effective tolerance is therefore not
    # overlap_k * sigma_obs but
    #
    #     overlap_k * sigma_obs + (magnitude swept by the LC within the bin)
    #
    # which varies with the local decline rate -- largest during fast early
    # evolution, precisely where discrimination matters most.
    #
    # This is a HIDDEN DEPENDENCY ON A GRID-GENERATION PARAMETER THAT HAS
    # NOTHING TO DO WITH THE PHYSICS.  Regenerating with ntime = 2000 gives
    # each simulation twice as many chances to land in the ROPE, so
    # survivor counts rise and the collapse time shifts -- with no change to
    # the model, the priors, or the data.  Any published survivor count or
    # collapse time is conditional on this undocumented numerical choice.
    #
    # FIX: interpolation (ISSUE #7) resolves this completely -- one
    # magnitude per simulation at exactly the observation time, tolerance
    # exactly overlap_k * sigma_obs.  Interim mitigation if #7 is deferred,
    # to at least remove the sampling-density dependence:
    #     per_sample = sim_bin.groupby("sample_id")["absolute_magnitude"].median()
    #     inside = (per_sample - M_obs).abs() <= overlap_k * sigma_obs
    # Until fixed, document as a known systematic and demonstrate the
    # sensitivity by rerunning a candidate against grids of differing ntime.
    # =====================================================================
    sim_bin = sim_band.loc[
        sim_band["time_bin"] == bin_idx, ["sample_id", "absolute_magnitude"]
    ]
    if sim_bin.empty:
        return []

    rope_half_width = overlap_k * sigma_obs
    inside = np.abs(sim_bin["absolute_magnitude"].to_numpy() - M_obs) <= rope_half_width
    return sim_bin.loc[inside, "sample_id"].dropna().unique().tolist()


def overlap_chain(ids_lists: List[List], times: List[float]) -> Dict[str, Any]:
    """
    Compute the sequential ABC survival diagnostic across observations.

    For a sequence of per-observation consistent-ID sets S_1, S_2, ..., S_N,
    this function computes:

    - pairwise overlaps: S_i ∩ S_{i+1}
    - running survivors: ⋂_{j<=i} S_j  (the set S_t from the paper)

    The survival count |S_t| is monotonically non-increasing by construction.

    Parameters
    ----------
    ids_lists : list of lists
        Per-observation lists of consistent simulation IDs.
    times : list of float
        Observation timestamps (days after merger), same order as ids_lists.

    Returns
    -------
    dict with keys:
        times               – sorted observation times.
        pairwise            – list of dicts with pairwise overlap info.
        survivors_over_time – list of dicts with cumulative survivors per epoch.
        final_survivors     – sorted IDs surviving all epochs.
        final_n_survivors   – count of final survivors.
    """
    # =====================================================================
    # ISSUE #5 — The hard ABC intersection is fragile to single outliers.
    # See IMPROVEMENTS.md §5.   Impact: MED-HIGH | Effort: MEDIUM
    # ---------------------------------------------------------------------
    # `survivors &= sets[i+1]` below is monotone by construction: once a
    # sample_id is excluded it can never return.  So |S_t| -> 0 is
    # indistinguishable from "one bad photometric point", "one
    # underestimated error bar", or "a miscalibrated zero-point in one
    # filter" -- yet it is interpreted as strong evidence against the
    # kilonova hypothesis.  The hard cut also discards the DEGREE of
    # consistency (0.1 sigma and 1.49 sigma are identical; 1.51 sigma is
    # eliminated forever).
    #
    # FIX: accumulate per-sample_id log-likelihood instead of intersecting
    # sets (ABC-rejection -> importance sampling), giving ESS in place of
    # the survivor count, weighted parameter posteriors, a
    # model-evidence-like log(Sum w), and graceful outlier handling.  Keep
    # this hard-cut chain alongside it initially for comparison.
    #
    # ISSUE #4 — this is invoked PER BAND by the scorer, so it never
    # constrains colour.  Intersecting across bands as well as epochs is
    # the cheapest high-value fix in the whole list; see the ISSUE #4 block
    # in kilonovascorer_v3 below.  Note the argsort below sorts by time
    # only, so simultaneous multi-band points would need a deterministic
    # tiebreak.
    # =====================================================================
    order = np.argsort(times)
    times_sorted = np.asarray(times)[order]
    sets = [set(ids_lists[i]) for i in order]

    if not sets:
        return {
            "times": [],
            "pairwise": [],
            "survivors_over_time": [],
            "final_survivors": [],
            "final_n_survivors": 0,
        }

    # Initialise running intersection from first observation
    survivors = sets[0].copy()
    survivors_over_time = [{
        "t": float(times_sorted[0]),
        "n_survivors": len(survivors),
        "survivor_ids": sorted(survivors),
    }]

    pairwise = []
    for i in range(len(sets) - 1):
        # Pairwise: S_i ∩ S_{i+1}
        inter = sets[i] & sets[i + 1]
        pairwise.append({
            "t_left": float(times_sorted[i]),
            "t_right": float(times_sorted[i + 1]),
            "n_overlap": len(inter),
            "overlap_ids": sorted(inter),
        })

        # Cumulative: S_t = S_{t-1} ∩ S_t
        survivors &= sets[i + 1]
        survivors_over_time.append({
            "t": float(times_sorted[i + 1]),
            "n_survivors": len(survivors),
            "survivor_ids": sorted(survivors),
        })

    return {
        "times": times_sorted.tolist(),
        "pairwise": pairwise,
        "survivors_over_time": survivors_over_time,
        "final_survivors": sorted(survivors),
        "final_n_survivors": len(survivors),
    }


# ---------------------------------------------------------------------------
# Logit-space cumulative P_tail_KNe scoring
# ---------------------------------------------------------------------------

def binned_stats_cumulative_ptail(
    metric_df: pd.DataFrame,
    bin_size: float = 0.2,
) -> pd.DataFrame:
    """
    Aggregate per-observation P_tail_KNe scores into time-binned cumulative scores.

    Within each time bin, individual scores are combined using an
    inverse-variance weighted mean in logit space (see paper Section 2).
    The result is then updated sequentially across bins to produce a running
    cumulative score, also in logit space.

    Logit-space aggregation prevents extreme scores with small absolute
    uncertainties from dominating the weighted mean — a known pathology of
    direct probability-space averaging near the [0, 1] boundaries.

    Parameters
    ----------
    metric_df : pd.DataFrame
        Output of ``kilonovascorer``.  Must contain ``obs_time``,
        ``p_tail_mean``, and ``p_tail_std`` columns.
    bin_size : float
        Width of time bins in days.  Should match the scorer's
        ``time_bin_width`` (default 0.2 d).

    Returns
    -------
    pd.DataFrame
        One row per time bin with columns:
        ``time_bin``, ``time_mid``, ``mean``, ``std``,
        ``running_mean``, ``running_std``.
    """
  #modify to match the bin edges of kilonovaScorer_V3 + bin_size / 2,
  #modidy back to +  bin_size
    bin_edges = np.arange(
        metric_df["obs_time"].min() - bin_size / 2,
        metric_df["obs_time"].max() + bin_size ,
        bin_size,
    )
    metric_df = metric_df.copy()
    metric_df["time_bin"] = pd.cut(metric_df["obs_time"], bins=bin_edges)

    binned_stats = (
        metric_df.groupby("time_bin", observed=True)
        .apply(ivw_stats_logit)  # noqa: F821 (from utils.*)
        .reset_index()
    )
    binned_stats["time_mid"] = binned_stats["time_bin"].apply(lambda x: x.mid)
    # =====================================================================
    # ISSUE #1b — FIXED. See IMPROVEMENTS.md §1.
    # ---------------------------------------------------------------------
    # This was a bare .dropna(), i.e. how="any". ivw_stats_logit's early
    # return omitted the "count" key its other paths carried, so that row got
    # count = NaN and the ENTIRE BIN was removed -- a bin in which the
    # candidate was categorically inconsistent with every simulation
    # contributed nothing, biasing the score upward exactly for the objects
    # that should be rejected most confidently.
    #
    # ivw_stats_logit now returns {mean, std, count} on every path, so the
    # only rows this should drop are genuinely empty bins. Narrowed to the two
    # columns the running score actually consumes, so a future schema addition
    # cannot silently start deleting bins again.
    #
    # ISSUE #15 — groupby(...).apply(...) returning a Series is
    # deprecation-sensitive. With a consistent schema it now reliably yields a
    # DataFrame rather than a MultiIndex Series; still worth a pinned-pandas
    # test.
    # =====================================================================
    binned_stats = binned_stats.dropna(subset=["mean", "std"])
    if binned_stats.empty:
        # Nothing left to accumulate. Returning the empty frame keeps the
        # caller's "no usable bins" path intact; falling through would index
        # z[0] on an empty array.
        return binned_stats

    running_mean, running_err = calculate_sequential_score_logit(  # noqa: F821
        binned_stats["mean"].values,
        binned_stats["std"].values,
    )
    binned_stats["running_mean"] = running_mean
    binned_stats["running_std"] = running_err

    return binned_stats


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------

def kilonovascorer_v3(
    data_obs: pd.DataFrame,
    data_sim: pd.DataFrame,
    candidate_name: str,
    time_bin_width: float = 0.2,
    band_list: Tuple[str, ...] = ("g-band", "r-band", "i-band", "z-band"),
    k_near: float = 1.5,
    n_kde_sim: int = 50000,
    min_sim_points: int = 20,
    overlap_k: float = 2.0,
    random_state=42,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Score a kilonova candidate against a simulation grid.

    For each photometric band and observation, computes:

    - **P_tail_KNe** — two-sided tail probability of M_obs under the
      noise-convolved PPD (with uncertainty via observation sampling).
    - **P_near_KNe** — ROPE-based local consistency score.
    - **ABC survival diagnostic** — sequential intersection of consistent
      simulation IDs across epochs (|S_t| from paper Section 3).

    Parameters
    ----------
    data_obs : pd.DataFrame
        Observational data.  Required columns: ``filter_mapped``,
        ``time_after_gw``, ``absolute_magnitude``,
        ``absolute_magnitude_error``.
    data_sim : pd.DataFrame
        Simulation grid.  Required columns: ``filter_mapped``, ``time``,
        ``absolute_magnitude``, ``sample_id``.
    candidate_name : str
        Human-readable identifier for the transient candidate.
    time_bin_width : float
        Width of time bins used to match observations to simulations (days).
    band_list : tuple of str
        Photometric bands to score.
    k_near : float
        ROPE half-width factor for P_near_KNe (paper fiducial: 1.5).
    n_kde_sim : int
        Monte Carlo samples for the noise-convolved KDE.
    min_sim_points : int
        Minimum number of simulations required in a bin to attempt scoring.
    overlap_k : float
        ROPE half-width factor for the ABC diagnostic (sigma units).
    random_state : int, Generator or None
        Seed for every Monte-Carlo draw in the scoring of THIS candidate
        (ISSUE #11). One Generator is created here and threaded through the
        per-band, per-epoch calls below, so draws still differ between epochs
        while the candidate's score as a whole is reproducible.

        Seeding per call, rather than once globally, is deliberate: it makes a
        candidate's score depend only on its own data, not on how many
        candidates happened to be scored before it. Without that, batching or
        reordering a run silently changes every score in it. ``None`` restores
        the old non-deterministic behaviour.

    Returns
    -------
    results_df : pd.DataFrame
        Per-observation metrics including P_tail_KNe, P_near_KNe, and ABC
        diagnostics.
    summary_df : pd.DataFrame
        Per-band overlap chain summary.
    """
    # One Generator for this candidate, threaded through every per-epoch draw
    # below (ISSUE #11). Created here rather than per epoch so successive epochs
    # still get different draws, and per CALL rather than globally so the score
    # does not depend on how many candidates preceded this one.
    rng = np.random.default_rng(random_state)

    results: List[Dict[str, Any]] = []
    overlap_summary_by_band: Dict[str, Any] = {}

    # =====================================================================
    # ISSUE #4 — Colour information is NEVER USED.  See IMPROVEMENTS.md §4.
    # Impact: HIGH | Effort: LOW (4a)
    # ---------------------------------------------------------------------
    # Everything below runs inside this per-band loop, including the
    # overlap_chain call at the end.  Bands are never combined, so rapid
    # reddening -- the primary signature separating kilonovae from young
    # supernovae, and the most discriminating feature available in the
    # first 72 h -- is invisible to the score.  A candidate consistent in g
    # AND consistent in i can have a g-i colour no simulation produces, and
    # still pass.
    #
    # FIX 4a (cheapest high-value change in the list): accumulate the
    # consistent-ID sets across ALL bands and epochs and call overlap_chain
    # ONCE after this loop, so a surviving sample_id must explain every
    # filter simultaneously.  Keep the per-band chains too, for diagnosing
    # which band drives a rejection.  Expect survivor counts to fall much
    # faster -- retune overlap_k.
    # FIX 4b: score (g-i) directly; the distance modulus cancels exactly in
    # a colour, which also sidesteps ISSUE #2.
    #
    # ISSUE #11 — no random_state parameter.  Add one and thread
    # `rng = np.random.default_rng(random_state)` through
    # predictive_tail_kde and compute_abs_mag_samples.  ISSUE #6 removes
    # the RNG need in the P_tail path entirely.
    # =====================================================================
    for band in band_list:
        # 1. Filter data for this band
        sim_band = data_sim[data_sim["filter_mapped"] == band].copy()
        obs_band = data_obs[data_obs["filter_mapped"] == band].copy()

        if sim_band.empty or obs_band.empty:
            logger.debug("No data for band %s — skipping.", band)
            continue

        # 2. Assign simulation time bins (computed once per band).
        #    Bin edges are chosen so that the first and last observations both
        #    land at the centre of their respective bins:
        #      left edge  = t_first - bin_width/2
        #      right edge = t_last  + bin_width/2
        #    An extra bin_width is added to t_end so np.arange includes the
        #    final right edge.
        # =================================================================
        # ISSUE #7 — Time BINNING instead of INTERPOLATION.
        # See IMPROVEMENTS.md §7.   Impact: MED | Effort: MEDIUM
        # -----------------------------------------------------------------
        # Each sample_id is a smooth curve on 1000 time steps, but binning
        # pools ~20 points per simulation per 0.2 d bin and treats them as
        # independent draws.  This inflates the PPD width by however far
        # each light curve moves within the bin -- a numerical artefact,
        # largest during the fastest evolution, i.e. exactly the early-time
        # regime this tool targets.
        #
        # It also creates three hyperparameters (time_bin_width,
        # min_sim_points, and the grid's own ntime -- ISSUE #12).
        # Interpolating to the exact observation time removes all of them:
        #     M = pivot(data_sim, index="sample_id", columns="time",
        #               values="absolute_magnitude")     # once, at load
        #     m_at_t = np.interp(t_obs, times, M[j])     # per sample_id
        # Every simulation then contributes exactly ONE value per
        # observation, which is the correct weighting, and the wide-array
        # form vectorises the scorer over sample_id (also fixing #13).
        # Watch memory: 100k x 1000 float64 ~ 800 MB per band -- use
        # float32 and/or subset the time range at load.
        # =================================================================
        t_first = obs_band["time_after_gw"].min()
        t_last  = obs_band["time_after_gw"].max()
        t_start = t_first - time_bin_width / 2
        t_end   = t_last  + time_bin_width  
        bins = np.arange(t_start, t_end, time_bin_width)

        # assert bins[0] < t_first < bins[1],  "First observation not centred in first bin."
        # assert bins[-2] < t_last < bins[-1], "Last observation not centred in last bin."

        sim_band["time_bin"] = np.digitize(sim_band["time"], bins)

        # Pre-group simulations by time bin for O(1) lookup per observation
        sim_groups: Dict[int, pd.DataFrame] = {
            k: v for k, v in sim_band.groupby("time_bin")
        }

        # Per-band tracking for ABC overlap chain
        band_times: List[float] = []
        band_ids_lists: List[List] = []
        band_row_indices: List[int] = []

        # KDE cache: fitted once per bin_idx, reused across observations in the same bin
        kde_cache: Dict[int, gaussian_kde] = {}

        # 3. Process observations in chronological order
        obs_band = obs_band.sort_values("time_after_gw")
        total_obs = len(obs_band)

        for count, obs_row in enumerate(obs_band.itertuples(index=False), start=1):
            t_obs = float(obs_row.time_after_gw)
            M_obs = float(obs_row.absolute_magnitude)
            sigma_obs = float(obs_row.absolute_magnitude_error)

            # Skip degenerate observations
            if not (np.isfinite(M_obs) and np.isfinite(sigma_obs) and sigma_obs > 0):
                logger.debug("Skipping invalid observation at t=%.3f d.", t_obs)
                continue

            bin_idx = int(np.digitize(t_obs, bins))
            sim_bin = sim_groups.get(bin_idx, pd.DataFrame())

            # =============================================================
            # ISSUE #10 — Silently skipped epochs.
            # See IMPROVEMENTS.md §10.   Impact: MED | Effort: LOW
            # -------------------------------------------------------------
            # This `continue` drops the observation with no record in the
            # returned DataFrame.  logger.debug is NOT a substitute: it is
            # off by default and never reaches the returned data.
            #
            # Under-populated bins cluster at the TEMPORAL EDGES OF THE
            # GRID -- and early epochs are the highest-value observations
            # for kilonova identification, so the bias runs toward
            # discarding the most informative data.  A user cannot tell
            # "this epoch scored well" from "this epoch was never scored";
            # nothing in metric_df records observations supplied vs scored.
            #
            # FIX (short term): emit a row for every input observation with
            # scored=False and a skip_reason ("insufficient_sims",
            # "invalid_sigma", "no_sim_in_band"); return n_obs_supplied /
            # n_obs_scored per band; surface the scored fraction on the
            # diagnostic plots; promote this to logger.warning when a large
            # fraction is skipped.  Confirm plotting filters on `scored`.
            # FIX (long term): ISSUE #7 removes the cause -- with
            # interpolation there is no bin occupancy and min_sim_points
            # becomes unnecessary.  The only legitimate skip is then
            # "outside the grid's temporal coverage", reported as such.
            # =============================================================
            if len(sim_bin) < min_sim_points:
                logger.debug(
                    "Bin %d has %d simulations (< %d) — skipping.",
                    bin_idx, len(sim_bin), min_sim_points,
                )
                continue

            # 3a. Fit KDE once per bin; reuse if another observation shares the same bin
            if bin_idx not in kde_cache:
                kde_cache[bin_idx] = gaussian_kde(
                    sim_bin["absolute_magnitude"].to_numpy()
                )
            cached_kde = kde_cache[bin_idx]

            # 3b. Compute P_tail_KNe and P_near_KNe (paper eqs. 2 and 4)
            metric = predictive_tail_kde(
                sim_bin["absolute_magnitude"].to_numpy(),
                M_obs=M_obs,
                sigma_obs=sigma_obs,
                k=k_near,
                n_sim=n_kde_sim,
                n_obs=100,      # N_obs = 100 per paper Section 2
                kde=cached_kde,
                rng=rng,
            )

            # ISSUE #13 (IMPROVEMENTS.md §13) — passing the FULL band frame
            # here forces compute_consistent_ids_anyhit to re-scan every
            # simulation row in the band, discarding the sim_groups
            # pre-grouping built above and the O(1) lookup it provides.
            # Likely the dominant runtime in v3.
            # FIX: pass the already-filtered `sim_bin` (from sim_groups)
            # and change the helper's signature to accept a pre-filtered
            # bin instead of (sim_band, bin_idx).
            # 3c. ABC diagnostic — consistent simulation IDs at this epoch
            consistent_ids = compute_consistent_ids_anyhit(
                sim_band=sim_band,
                bin_idx=bin_idx,
                M_obs=M_obs,
                sigma_obs=sigma_obs,
                overlap_k=overlap_k,
            )

            # 3d. Safe time-bin edge lookup
            bin_low = float(bins[bin_idx - 1] if bin_idx > 0 else bins[0])
            bin_high = float(bins[bin_idx] if bin_idx < len(bins) else bins[-1])

            row: Dict[str, Any] = {
                "candidate_name": candidate_name,
                "band": band,
                "obs_time": t_obs,
                "time_bin_low": bin_low,
                "time_bin_high": bin_high,
                "observed_mag": M_obs,
                "observed_mag_err": sigma_obs,
                "p_tail_KNe": metric["p_tail_KNe"],
                "p_tail_mean": metric["p_tail_mean"],
                "p_tail_std": metric["p_tail_std"],
                "p_near_KNe": metric["p_near_KNe"],
                "n_sim_bin": len(sim_bin),
                "n_consistent_lcs": len(consistent_ids),
                "consistent_ids": consistent_ids,
                # ABC overlap fields — populated in post-processing step 4
                "overlap_with_next_n": np.nan,
                "overlap_with_next_ids": [],
                "running_survivors_n": np.nan,
                "running_survivors_ids": [],
            }

            results.append(row)
            band_times.append(t_obs)
            band_ids_lists.append(consistent_ids)
            band_row_indices.append(len(results) - 1)
            arcade_progress_bar(count, total_obs, bar_length=50)

        # 4. Post-processing: compute ABC overlap chain for this band
        if band_ids_lists:
            chain = overlap_chain(band_ids_lists, band_times)
            overlap_summary_by_band[band] = chain

            # Map running survivors back to per-observation rows
            for j, surv in enumerate(chain["survivors_over_time"]):
                idx = band_row_indices[j]
                results[idx]["running_survivors_n"] = int(surv["n_survivors"])
                results[idx]["running_survivors_ids"] = surv["survivor_ids"]

            # Map pairwise overlaps to the left-hand observation of each pair
            for j, pw in enumerate(chain.get("pairwise", [])):
                idx_left = band_row_indices[j]
                results[idx_left]["overlap_with_next_n"] = int(pw["n_overlap"])
                results[idx_left]["overlap_with_next_ids"] = pw["overlap_ids"]

    return pd.DataFrame(results), pd.DataFrame(overlap_summary_by_band)
