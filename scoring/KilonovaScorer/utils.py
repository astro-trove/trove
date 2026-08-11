"""
utils.py — KilonovaScorer utility functions.

Implements:
  - Apparent-to-absolute magnitude conversion via Monte Carlo sampling,
    supporting both scalar and array inputs.
  - Logit-space inverse-variance weighted aggregation (ivw_stats_logit).
  - Sequential logit-space score updating (calculate_sequential_score_logit).
"""

from scipy.stats import gaussian_kde

import logging
from typing import Optional

import numpy as np
import pandas as pd
from scipy.special import expit, logit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Apparent → absolute magnitude conversion
# ---------------------------------------------------------------------------

def compute_abs_mag_samples(
    app_mag,
    app_mag_err,
    dist_mpc: float,
    dist_err_mpc: float,
    n_samples: int = 5000,
    random_state=None,
):
    """
    Convert apparent magnitude(s) to absolute magnitude via Monte Carlo sampling.

    Supports both scalar and array inputs for ``app_mag`` / ``app_mag_err``,
    so it can be called once on an entire DataFrame column (vectorised) or on
    a single observation.

    The distance modulus is sampled from N(dist_mpc, dist_err_mpc); apparent
    magnitudes are sampled from N(app_mag, app_mag_err).  Non-physical
    (negative) distance draws are rejected before computation.

    Parameters
    ----------
    app_mag : float or array-like
        Apparent magnitude(s).
    app_mag_err : float or array-like
        Uncertainty on apparent magnitude(s).  Non-finite values are treated
        as zero (no photometric uncertainty).
    dist_mpc : float
        Luminosity distance in Mpc.
    dist_err_mpc : float
        Uncertainty on the luminosity distance in Mpc.
    n_samples : int
        Number of Monte Carlo draws per observation.

    Returns
    -------
    abs_mag_mean : float or np.ndarray
        Mean absolute magnitude(s).  np.nan for invalid inputs.
    abs_mag_std : float or np.ndarray
        Standard deviation of absolute magnitude(s).  np.nan for invalid inputs.

    Notes
    -----
    When called with arrays, one independent set of ``n_samples`` distance
    draws is shared across all rows (same distance realisation), while
    apparent-magnitude noise is drawn independently per row.  This correctly
    reflects that the distance uncertainty is a global systematic.
    """
    scalar_input = np.ndim(app_mag) == 0

    app_mag = np.atleast_1d(np.asarray(app_mag, dtype=float))
    app_mag_err = np.atleast_1d(np.asarray(app_mag_err, dtype=float))

    # Treat non-finite errors as zero (conservative: no photometric noise)
    app_mag_err = np.where(np.isfinite(app_mag_err), app_mag_err, 0.0)
    app_mag_err = np.clip(app_mag_err, 0.0, None)

    n_obs = len(app_mag)
    abs_mag_mean = np.full(n_obs, np.nan)
    abs_mag_std  = np.full(n_obs, np.nan)

    # Global distance validation
    if not np.isfinite(dist_mpc) or dist_mpc <= 0:
        logger.warning("Invalid distance dist_mpc=%.3f — returning NaN.", dist_mpc)
        return (float("nan"), float("nan")) if scalar_input else (abs_mag_mean, abs_mag_std)

    # Sample distance modulus (shared across all observations — distance is a
    # global systematic, not an independent draw per row)
    # ISSUE #11 — FIXED.  `random_state` seeds a local Generator, so scores are
    # reproducible run-to-run.  default_rng rather than np.random.seed, so the
    # caller's global state is not clobbered when the scorer is embedded in a
    # broker or larger pipeline.  random_state=None keeps the old
    # non-deterministic behaviour.
    #
    # ISSUE #2 — The distance draw is correctly SHARED across all rows here
    # (a global systematic, as the docstring states).  The problem is that
    # this correlation is destroyed a few lines below -- see the ISSUE #2
    # block at the abs_mag_mean/abs_mag_std assignment.
    rng = np.random.default_rng(random_state)
    D_samples = rng.normal(dist_mpc, dist_err_mpc, n_samples) * 1e6  # parsecs
    D_samples = D_samples[D_samples > 0]

    if len(D_samples) < 10:
        logger.warning(
            "Fewer than 10 valid distance samples drawn — dist_mpc=%.3f, "
            "dist_err_mpc=%.3f.  Returning NaN.",
            dist_mpc, dist_err_mpc,
        )
        return (float("nan"), float("nan")) if scalar_input else (abs_mag_mean, abs_mag_std)

    n_valid = len(D_samples)
    mu_samples = 5.0 * np.log10(D_samples) - 5.0  # shape (n_valid,)

    for i in range(n_obs):
        if not np.isfinite(app_mag[i]):
            continue  # leave as NaN

        # Independent apparent-magnitude noise per observation
        app_samples = rng.normal(app_mag[i], app_mag_err[i], n_valid)
        abs_samples = app_samples - mu_samples

        # =================================================================
        # ISSUE #2 — The distance systematic is collapsed into a per-row
        # marginal std and thereafter treated as independent noise.
        # See IMPROVEMENTS.md §2.   Impact: HIGH | Effort: MEDIUM
        # -----------------------------------------------------------------
        # The physics above is right: ONE set of distance draws is shared
        # across every row, because distance is a global systematic.  But
        # collapsing to (mean, std) here DESTROYS THAT CORRELATION.  The
        # returned absolute_magnitude_error is a marginal standard
        # deviation carrying no record that a large part of it is common to
        # all rows.
        #
        # Downstream, calculate_sequential_score_logit() accumulates
        # precision additively (updated_prec = current_prec + new_prec),
        # which is correct ONLY for independent measurements.
        #
        # At the fiducial 259 +/- 62 Mpc:
        #     sigma_mu ~ (5/ln10) * (62/259) ~ 0.52 mag
        # typically LARGER than the photometric error, and it shifts every
        # absolute magnitude in the SAME direction by the SAME amount.  It
        # does not average down with more epochs -- it is an irreducible
        # floor.  The current code lets the running error shrink as 1/sqrt(N)
        # without limit, so THE CUMULATIVE SCORE BECOMES MORE OVERCONFIDENT
        # THE MORE DATA YOU COLLECT -- the opposite of desired behaviour,
        # and worst for the best-observed candidates.
        #
        # Second-order: a coherent +/-0.5 mag shift moves all observed mags
        # bodily relative to the simulated population, changing P_tail in a
        # correlated way.  Marginalising properly can move the CENTRAL
        # VALUE, not just the error bar.
        #
        # FIX (Option A, cheaper, approximate) -- return the decomposition
        #   sigma_phot[i]  (per-row, independent)
        #   sigma_mu       (scalar, shared)
        # and combine downstream with C = diag(sigma_phot^2) + sigma_mu^2 * J
        # via generalised least squares in logit space instead of Sum(1/s^2).
        # Assumes the distance->logit-score map is locally linear.
        #
        # FIX (Option B, exact, RECOMMENDED) -- move the distance draw
        # OUTSIDE the scorer entirely:
        #     for m in range(M):                  # M ~ 100-500
        #         mu_m   = draw_distance_modulus()
        #         obs_m  = apparent_mag - mu_m    # coherent shift, all rows
        #         score_m = kilonovascorer(obs_m, ...)
        #     report the distribution over {score_m}
        # Needs no change to the scoring internals, is trivially parallel,
        # and the spread across realisations directly quantifies how much of
        # the total uncertainty is distance-driven.  If the GW skymap gives
        # a 3D distance posterior along the candidate's line of sight,
        # sample from it rather than assuming the Gaussian used above.
        # =================================================================
        abs_mag_mean[i] = np.mean(abs_samples)
        abs_mag_std[i]  = np.std(abs_samples)

    if scalar_input:
        return float(abs_mag_mean[0]), float(abs_mag_std[0])
    return abs_mag_mean, abs_mag_std


# ---------------------------------------------------------------------------
# Logit-space inverse-variance weighted aggregation
# ---------------------------------------------------------------------------

#: Floor applied to ``p_tail_std`` before it becomes an inverse-variance weight.
#:
#: A bin in which every epoch has ``p_tail = 0`` reports a standard deviation of
#: exactly 0, which claims infinite information and sends the precision
#: accumulator to ``inf`` (and the running score to NaN). Something must bound
#: it, and *what* it is bounded at is a real scientific lever, not a numerical
#: detail: it sets how decisively a categorically-rejected bin can pull the
#: cumulative score down.
#:
#: The default ties the floor to ``eps``, the clip already applied to
#: ``p_tail_mean`` before the logit -- claiming to know a probability better than
#: the resolution it is stored at is not defensible. That gives a rejected bin
#: ``z_std ~ 1`` and hence unit weight, against ~25 for a well-measured bin at
#: p = 0.5 +/- 0.05. A *smaller* floor makes rejections dominate; ``None`` here
#: means "use eps". IMPROVEMENTS.md §1 suggests ``1/sqrt(n_obs)`` as an
#: alternative, which is looser and weights rejections less.
P_TAIL_STD_FLOOR: Optional[float] = None


def ivw_stats_logit(
    group: pd.DataFrame, eps: float = 1e-4, s_floor: Optional[float] = None
) -> pd.Series:
    """
    Inverse-variance weighted mean and uncertainty in logit space.

    Statistically appropriate for bounded P_tail_KNe scores in (0, 1):
    aggregating directly in probability space biases the mean toward extreme
    values with small absolute uncertainties.  Operating in logit space
    stabilises variances near the boundaries (see paper Section 2).

    The uncertainty on each logit-transformed score is propagated via the
    delta method::

        sigma_z = sigma_p / (p * (1 - p))

    and the result is transformed back via the inverse logit (expit).

    Parameters
    ----------
    group : pd.DataFrame
        Subset of the metrics DataFrame for a single time bin.  Must contain
        ``p_tail_mean`` and ``p_tail_std`` columns.
    eps : float
        Clamping value to keep scores away from 0 and 1 before logit
        transform (prevents infinite logit values).

    Returns
    -------
    pd.Series
        ``mean``  – inverse-variance weighted mean (probability space).
        ``std``   – propagated uncertainty (probability space).
        ``count`` – number of valid scores used.
    """
    # =====================================================================
    # ISSUE #1 — FIXED (was: zero-score bins silently dropped / hard crash).
    # See IMPROVEMENTS.md §1 and §16. All three sub-issues had to be fixed
    # together, because fixing 1b alone starts delivering std = 0.0 rows into
    # calculate_sequential_score_logit, which is exactly what 1c crashes on.
    #
    # (1a) The `p_tail_mean > 0` filter is GONE. p_tail == 0 is the strongest
    #      evidence AGAINST the kilonova hypothesis, not missing data;
    #      discarding it biased the score upward precisely for the candidates
    #      that should be rejected most confidently. The `eps` clip below is
    #      what keeps zeros away from the logit singularity -- that was always
    #      its job, so the filter was redundant as well as destructive.
    #
    # (1b) EVERY return path now carries the same keys {mean, std, count}.
    #      With mismatched key sets, groupby(...).apply(...) cannot align the
    #      Series into columns and returns a MultiIndex Series instead, so the
    #      caller's `binned_stats["mean"]` raised KeyError and took the whole
    #      candidate down -- 68 of 225 scoreable candidates on S251112cm.
    #
    # (1c) s == 0 is floored rather than allowed through (see
    #      P_TAIL_STD_FLOOR). A zero reported std claims infinite information;
    #      it made the precision accumulator inf and the running score NaN.
    #
    # Reverting: restore the `& (group["p_tail_mean"] > 0)` filter to get the
    # old upward-biased behaviour back. The schema consistency should NOT be
    # reverted -- it only ever caused a crash.
    # =====================================================================
    p = group["p_tail_mean"].to_numpy(dtype=float)
    s = group["p_tail_std"].to_numpy(dtype=float)

    # Only non-finite values are unusable. A finite p of 0.0, or a finite std
    # of 0.0, is information -- see (1a) and (1c) above.
    mask = np.isfinite(p) & np.isfinite(s) & (s >= 0)
    p, s = p[mask], s[mask]

    if len(p) == 0:
        return pd.Series({"mean": np.nan, "std": np.nan, "count": 0})

    if s_floor is None:
        s_floor = eps if P_TAIL_STD_FLOOR is None else P_TAIL_STD_FLOOR
    s = np.maximum(s, s_floor)

    p_clipped = np.clip(p, eps, 1.0 - eps)
    z     = logit(p_clipped)
    z_std = s / (p_clipped * (1.0 - p_clipped))    # delta method
    weights = 1.0 / z_std ** 2

    z_mean     = np.sum(weights * z) / np.sum(weights)
    z_std_comb = np.sqrt(1.0 / np.sum(weights))

    mean = float(expit(z_mean))
    std  = float(mean * (1.0 - mean) * z_std_comb)  # delta method back-transform

    return pd.Series({"mean": mean, "std": std, "count": len(p)})


# ---------------------------------------------------------------------------
# Sequential logit-space cumulative score update
# ---------------------------------------------------------------------------

def calculate_sequential_score_logit(
    means: np.ndarray,
    stds: np.ndarray,
    eps: float = 1e-4,
):
    """
    Sequentially update the cumulative P_tail_KNe score in logit space.

    Implements the sequential inverse-variance weighted update from the paper::

        z_new = (z_prev / sigma_prev^2 + z_i / sigma_i^2)
                / (1/sigma_prev^2 + 1/sigma_i^2)

        sigma_new^2 = (1/sigma_prev^2 + 1/sigma_i^2)^{-1}

    all in logit space, with results transformed back via expit.  NaN bins
    are carried forward from the previous step without updating, so a missing
    time bin does not reset or corrupt the running score.

    Parameters
    ----------
    means : np.ndarray
        Per-bin inverse-variance weighted P_tail_KNe means (probability space).
    stds : np.ndarray
        Per-bin inverse-variance weighted P_tail_KNe standard deviations.
    eps : float
        Clamping value before logit transform.

    Returns
    -------
    running_score : np.ndarray
        Cumulative P_tail_KNe score at each time bin (probability space).
    running_error : np.ndarray
        Propagated uncertainty on the cumulative score (probability space).
    """
    n = len(means)
    running_score = np.zeros(n)
    running_error = np.zeros(n)

    means_clipped = np.clip(means, eps, 1.0 - eps)
    z     = logit(means_clipped)
    z_std = stds / (means_clipped * (1.0 - means_clipped))  # delta method

    # =====================================================================
    # ISSUE #1c — FIXED. See IMPROVEMENTS.md §1.
    # ---------------------------------------------------------------------
    # A z_std of 0.0 gives new_prec = 1/0**2 = inf and updated_z = NaN, and
    # every subsequent bin inherits the NaN. np.isfinite() does NOT catch it,
    # because 0.0 is finite -- so the guard tests z_std > 0 explicitly, and is
    # applied when INITIALISING as well as when updating. Initialising from
    # bin 0 unconditionally was its own bug: one bad first bin made the entire
    # running score NaN.
    #
    # ivw_stats_logit now floors the std, so this should not trigger from that
    # path -- but this function is exported and callable directly, so it stays
    # defensive.
    # =====================================================================
    valid = np.isfinite(z) & np.isfinite(z_std) & (z_std > 0)
    if not valid.any():
        return np.full(n, np.nan), np.full(n, np.nan)

    # Initialise from the first VALID bin, not blindly from bin 0. Bins before
    # it have no running score to report yet, so they carry NaN rather than a
    # fabricated one.
    first = int(np.argmax(valid))
    running_score[:first] = np.nan
    running_error[:first] = np.nan
    current_z    = z[first]
    current_prec = 1.0 / z_std[first] ** 2
    running_score[first] = float(means_clipped[first])
    running_error[first] = float(stds[first])

    for i in range(first + 1, n):
        if not valid[i]:
            # Carry forward without update
            running_score[i] = running_score[i - 1]
            running_error[i] = running_error[i - 1]
            continue

        new_prec     = 1.0 / z_std[i] ** 2
        updated_prec = current_prec + new_prec
        updated_z    = (current_z * current_prec + z[i] * new_prec) / updated_prec

        current_z    = updated_z
        current_prec = updated_prec

        score_i = float(expit(updated_z))
        running_score[i] = score_i
        running_error[i] = score_i * (1.0 - score_i) * np.sqrt(1.0 / updated_prec)

    return running_score, running_error