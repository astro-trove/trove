# KilonovaSCORER — Method & Implementation Review

Detailed notes on identified issues and proposed fixes. This document began as a
specification with **no code changed**; that is no longer true for every entry. Issues
carrying **Status: FIXED** have been implemented in the vendored source — see the "As
implemented" subsection of each for exactly what changed, how it was verified, and how to
revert it. Everything else remains a specification only.

| Fixed | When | Files touched |
|---|---|---|
| #1 (a, b, c) | 2026-08-04 | `utils.py` (`ivw_stats_logit`, `calculate_sequential_score_logit`), `core.py` (`binned_stats_cumulative_ptail`), `scoring/kilonova_scoring.py` (`_cumulative_score` guard) |

Findings are ordered by (scientific impact × confidence) / effort. Each entry gives the
location, the mechanism, why it matters, one or more candidate fixes, and an effort/risk note.

| # | Issue | Impact | Effort | Status |
|---|---|---|---|---|
| 1 | Zero-score bins silently dropped from cumulative score | **High** — biases score upward | Low | **FIXED 2026-08-04** |
| 2 | Distance systematic treated as independent per-epoch error | **High** — score overconfident | Medium | Confirmed by reading |
| 3 | No calibration validation (N=1 real positive) | **High** — paper-critical | Medium | Confirmed |
| 4 | Colour information never used | High | Low–Medium | Confirmed |
| 5 | Hard ABC intersection is outlier-fragile | Medium–High | Medium | Confirmed |
| 6 | MC estimation of an analytically-tractable quantity | Medium | Low | Confirmed |
| 7 | Time binning instead of interpolation | Medium | Medium | Confirmed |
| 8 | `sigma_obs` used twice in `p_tail_std` | Medium | Low | Confirmed |
| 9 | Upper limits discarded | Medium | Low–Medium | Confirmed |
| 10 | Silently skipped epochs (`min_sim_points`) | Medium | Low | Confirmed |
| 11 | Unseeded RNG | Medium | Low | Confirmed |
| 12 | `anyhit` depends on grid time resolution | Medium | — | Confirmed |
| 13 | v3's ABC helper defeats its own pre-grouping | Low (perf) | Low | Confirmed |
| 14 | Single-model grid | Medium | High | Confirmed |
| 15 | Duplicate scorers, no tests | Low | Medium | Confirmed |
| 16 | Empirical confirmation of #1 / #11 against real data | — | — | **Measured** |
| 17 | `host_df.z_type` unguarded — 14% of candidates get no distance | Medium | Low | **Measured** |
| 18 | Single-distance grid | Medium | Medium | **Measured** |
| 19 | Detection selection bias — score penalises real kilonovae beyond ~150 Mpc | **High** | Medium | **Measured** |

---

## 1. Zero-score bins are silently dropped from the cumulative score

> **Status: FIXED, 2026-08-04.** The diagnosis below stands as written; jump to
> [As implemented](#as-implemented-2026-08-04) for what was actually changed, the
> before/after numbers, and how to revert. The rest of this section is preserved as the
> original diagnosis so the reasoning behind the change stays readable.

**Location:** [`ivw_stats_logit`](src/KilonovaScorer/utils.py#L128) lines 160–165;
[`binned_stats_cumulative_ptail`](src/KilonovaScorer/core2.py#L478) line 526 (and the
equivalent [core.py:359](src/KilonovaScorer/core.py#L359)).

### Mechanism

The zero-handling block added in commit `c01b82e` reads:

```python
valid = (group["p_tail_std"] > 0) & (group["p_tail_mean"] > 0)
group = group[valid]
if group.empty:
    return pd.Series({"mean": 0.0, "std": 0.0})
```

Two separate problems follow from this.

**1a. Per-observation filtering.** The `p_tail_mean > 0` condition removes individual
observations with a zero tail probability from the inverse-variance weighted mean. But
`p_tail_mean = 0` is the *strongest possible evidence against* the kilonova hypothesis — the
observation sits entirely outside the simulated population. Discarding it means a bin
containing one perfectly consistent point and one wildly inconsistent point scores exactly
the same as a bin containing only the consistent point.

Note that the `eps = 1e-4` clipping at line 176 already exists precisely to keep zeros away
from the logit singularity:

```python
p_clipped = np.clip(p, eps, 1.0 - eps)
```

So the `> 0` filter is both redundant and actively harmful — `eps` handles the numerics, and
the filter throws away information the clipping would have retained.

**1b. All-zero bins.** When *every* observation in a bin is filtered out, the early return
produces a Series with keys `{"mean", "std"}` — **missing the `"count"` key** that all other
return paths include (lines 174 and 187). Under `groupby(...).apply(...)`, pandas aligns
the returned Series into a DataFrame using the union of index labels, so this row gets
`count = NaN`. The very next statement is:

```python
binned_stats = binned_stats.dropna()   # core2.py:526
```

`dropna()` defaults to `how="any"`, so **the entire bin row is removed** on the strength of
the missing `count` field alone.

The net effect: a time bin in which the candidate is *categorically inconsistent with every
simulation* contributes nothing at all to the running score. The cumulative score is
therefore biased upward — systematically, and worst exactly for the objects the tool should
be rejecting most confidently.

**1c. The alternative branch is also broken.** If the row were *not* dropped (different
pandas version, or `dropna` removed), `std = 0.0` propagates into
[`calculate_sequential_score_logit`](src/KilonovaScorer/utils.py#L194):

```python
z_std = stds / (means_clipped * (1.0 - means_clipped))   # → 0.0
...
new_prec = 1.0 / z_std[i] ** 2                           # → inf
updated_z = (current_z * current_prec + z[i] * new_prec) / updated_prec
#           (finite            + ±inf)      / inf        → NaN
```

The guard at line 244 only tests `np.isfinite(z_std[i])`, and `0.0` is finite, so it passes.
Every subsequent bin then inherits NaN. If the zero bin happens to be the first one,
`current_prec = 1.0 / z_std[0] ** 2` at line 239 is `inf` from the start and the whole
running score is NaN.

So the behaviour depends on a pandas implementation detail, and both outcomes are wrong:
either silently discard the evidence, or NaN-poison the score.

### Why it matters

This is the difference between "we found no evidence against a kilonova" and "we found
strong evidence against a kilonova and ignored it." For a tool whose purpose is real-time
vetting, suppressing the reject signal is the most consequential failure mode available.

### Proposed fix

Remove the special-casing entirely and let `eps` do its job:

```python
def ivw_stats_logit(group, eps=1e-4):
    p = group["p_tail_mean"].to_numpy()
    s = group["p_tail_std"].to_numpy()

    # Keep p == 0 rows: they are real evidence. Only require a usable variance.
    mask = np.isfinite(p) & np.isfinite(s) & (s > 0)
    p, s = p[mask], s[mask]

    if len(p) == 0:
        # Consistent schema on EVERY return path, so groupby.apply cannot
        # introduce a spurious NaN column that dropna() then acts on.
        return pd.Series({"mean": np.nan, "std": np.nan, "count": 0})

    p_clipped = np.clip(p, eps, 1.0 - eps)
    ...
```

Three accompanying changes:

- **Every return path must carry the same keys.** This is what makes `dropna()` safe.
- **Replace the blanket `dropna()`** in both `binned_stats_cumulative_ptail` implementations
  with an explicit `dropna(subset=["mean", "std"])`, so only genuinely unusable bins are
  removed and the reason is visible in the code.
- **Handle `s == 0` deliberately.** A zero reported std means "this bin is infinitely
  informative," which is never true. Either floor it (`s = np.maximum(s, s_floor)` with
  `s_floor` set by the MC resolution, e.g. `1/sqrt(n_obs)`) or drop the row explicitly. It
  should not reach the precision accumulator.

Additionally, guard the sequential updater against non-finite precision:

```python
if not np.isfinite(z[i]) or not np.isfinite(z_std[i]) or z_std[i] <= 0:
    running_score[i] = running_score[i - 1]
    running_error[i] = running_error[i - 1]
    continue
```

and apply the same check when initialising from bin 0 (line 239) rather than assuming it
is valid.

**Effort:** Low — a few lines in [utils.py](src/KilonovaScorer/utils.py) plus one line in
each `binned_stats_cumulative_ptail`.
**Risk:** Changes published scores. Any existing numbers (including the AT2017gfo and
AT2025ulz results) will need regenerating, and the change should be characterised before/after.
**Confirm first:** Run a synthetic `metric_df` with one all-zero bin through
`binned_stats_cumulative_ptail` on the pinned pandas version to establish which of the two
branches (1b drop vs 1c NaN) is actually taken. This was not executed during this review.

### As implemented (2026-08-04)

All three sub-issues were fixed **together**, because they are not independent: fixing 1b
alone makes the schema consistent, which stops `dropna()` deleting all-zero bins, which
then starts delivering `std = 0.0` rows into `calculate_sequential_score_logit` — which is
precisely what 1c NaN-poisons on. Fixing 1b without 1c converts a crash into a silent NaN.

**Change 1 — `ivw_stats_logit` (utils.py).** The `valid` filter and the two-key early
return are gone. The function now:

```python
p = group["p_tail_mean"].to_numpy(dtype=float)
s = group["p_tail_std"].to_numpy(dtype=float)

mask = np.isfinite(p) & np.isfinite(s) & (s >= 0)   # was: (s > 0) & (p > 0)
p, s = p[mask], s[mask]

if len(p) == 0:
    return pd.Series({"mean": np.nan, "std": np.nan, "count": 0})

s = np.maximum(s, s_floor)                          # 1c, see below
p_clipped = np.clip(p, eps, 1.0 - eps)              # eps handles p == 0, as always intended
```

Every return path now carries `{mean, std, count}`. `p == 0` and `s == 0` are both kept —
they are evidence, not missing data.

**Change 2 — the `s == 0` floor.** New module constant `P_TAIL_STD_FLOOR` (default `None`
→ use `eps`), overridable per call via `ivw_stats_logit(..., s_floor=...)`. This is a
**scientific lever, not a numerical detail**: it sets how hard a categorically-rejected bin
can pull the cumulative score down. Tying it to `eps` is the self-consistent choice —
claiming to know a probability better than the resolution it is stored at is not
defensible. It gives a rejected bin `z_std ≈ 1`, hence unit weight, against ≈ 25 for a
well-measured bin at `p = 0.5 ± 0.05`. The `1/sqrt(n_obs) = 0.1` alternative proposed above
is *looser* and weights rejections roughly 10× less; it was not adopted, but the constant
exists so the choice can be revisited without touching the arithmetic.

**Change 3 — `calculate_sequential_score_logit` (utils.py).** Guard extended to
`z_std > 0` (not merely finite — `0.0` is finite and passed the old check), and applied
when **initialising** as well as when updating. Initialising unconditionally from bin 0 was
its own bug: one bad first bin NaN'd the entire running score. It now initialises from the
first *valid* bin, reports NaN for bins before it rather than a fabricated value, and
returns all-NaN if no bin is valid instead of raising `IndexError` on `z[0]`.

**Change 4 — `binned_stats_cumulative_ptail` (core.py).** Bare `.dropna()` →
`.dropna(subset=["mean", "std"])`, plus an early return when nothing survives (otherwise
the empty frame reaches `z[0]`). Narrowing to the two columns the running score consumes
means a future schema addition cannot silently start deleting bins again.

**Change 5 — `scoring/kilonova_scoring.py`.** `_cumulative_score` short-circuited with
`"no epoch has a usable p_tail_std"` when every epoch had `p_tail_std == 0`. That guard
existed only to dodge the divide-by-zero and would now mask the fix, so it was removed —
such a candidate aggregates normally and comes out at ≈ 0.

### Verification

Synthetic cases (`binned_stats_cumulative_ptail`, pandas 3.0.3):

| case | before | after |
|---|---|---|
| mixed all-zero + non-zero bins | `KeyError: 'mean'` | scores, `1.0e-4` |
| two good bins (p = 0.5) | 0.5 | 0.5 (unchanged) |
| same + **one** all-zero bin | 0.5 — bin deleted | **0.455** — bin now counts against |
| every epoch `p_tail = 0` | unscoreable | `1.0e-4` |
| bad *first* bin | all-NaN | NaN for that bin, then normal |
| no valid bins | `IndexError` | all-NaN |

Real data — `AT2024aeuy` (S251112cm), 8 epochs at M ≈ −18 against a grid spanning −17.2 to
0, i.e. brighter than every simulated kilonova. Before: `cumulative aggregation failed:
KeyError: 'mean'`. After: **0.0001 ± 0.0000** — correctly reported as near-total rejection
rather than as a failure indistinguishable from missing data.

Event-level impact on S251112cm is recorded in §16.

### Reverting

Restore `valid = (group["p_tail_std"] > 0) & (group["p_tail_mean"] > 0)` and its
two-key early return in `ivw_stats_logit` to get the old upward-biased behaviour back.
**Do not revert changes 3 and 4** — those only ever converted a crash into a correct
result, and the guards are cheap. Note that reverting change 1 without also restoring
change 5's guard in `kilonova_scoring.py` will reintroduce NaN scores.

---

## 2. The distance systematic is treated as independent per-epoch noise

**Location:** [`compute_abs_mag_samples`](src/KilonovaScorer/utils.py#L28) lines 94–117;
[`calculate_sequential_score_logit`](src/KilonovaScorer/utils.py#L194) lines 243–259.

### Mechanism

`compute_abs_mag_samples` gets the physics right. It draws **one** shared set of distance
samples across all rows (line 94), with the docstring correctly noting that "distance
uncertainty is a global systematic, not an independent draw per row":

```python
D_samples = np.random.normal(dist_mpc, dist_err_mpc, n_samples) * 1e6
mu_samples = 5.0 * np.log10(D_samples) - 5.0    # shared across all observations
```

But then, per row:

```python
abs_mag_mean[i] = np.mean(abs_samples)
abs_mag_std[i]  = np.std(abs_samples)
```

Collapsing to a per-row `(mean, std)` **destroys the correlation structure**. The returned
`absolute_magnitude_error` is a marginal standard deviation with no record that a large
fraction of it is common to every row.

Downstream, the sequential updater accumulates precision additively:

```python
new_prec     = 1.0 / z_std[i] ** 2
updated_prec = current_prec + new_prec
```

This is the correct formula for **independent** measurements. Here it is applied to
observations sharing a dominant common-mode error.

### Why it matters

At the fiducial 259 ± 62 Mpc, the distance modulus uncertainty is roughly

```
σ_μ ≈ (5 / ln 10) × (62 / 259) ≈ 0.52 mag
```

which is typically larger than the photometric error on a well-measured point. That 0.52 mag
shifts **every** absolute magnitude in the same direction by the same amount. It does not
average down with more epochs — it is an irreducible floor.

The current implementation lets the running error shrink as ≈1/√N without limit. The
consequence is that **the cumulative score becomes more overconfident the more data you
collect** — the opposite of the desired behaviour, and most severe for the well-observed
candidates where the score carries the most weight.

There is a second-order effect too: a coherent ±0.5 mag shift moves the observed magnitudes
bodily up or down relative to the simulated population, which changes `P_tail` in a
correlated way across all epochs. Marginalising this correctly can move the central value,
not just the error bar.

### Proposed fix

**Option A — analytic covariance (cheaper, approximate).** Have `compute_abs_mag_samples`
additionally return the decomposition:

```python
sigma_phot[i]  = photometric-only contribution   (per-row, independent)
sigma_mu       = distance modulus contribution   (scalar, shared by all rows)
```

Then combine with a covariance matrix rather than a diagonal:

```
C = diag(σ²_phot) + σ²_μ · J        (J = all-ones matrix)
```

and use generalised least squares in logit space instead of `Σ 1/σ²`. This preserves the
existing architecture and correctly floors the cumulative error at the systematic level.
The approximation is that the distance→logit-score mapping is locally linear.

**Option B — outer marginalisation (correct, more compute).** Move the distance draw outside
the scorer entirely:

```python
for m in range(M):                      # M ≈ 100–500
    mu_m = draw_distance_modulus()
    obs_m = apparent_mag - mu_m         # coherent shift, all rows together
    score_m = kilonovascorer(obs_m, ...)   # full pipeline at fixed distance
report distribution over {score_m}      # median + credible interval
```

This is exact, requires no changes to the scoring internals, and is trivially parallel. It
also yields a directly useful diagnostic: the spread of the score across distance
realisations tells you how much of the total uncertainty is distance-driven. For a GW event
with a poorly constrained distance posterior, that is a headline number.

If the GW skymap provides a full 3D distance posterior along the candidate's line of sight,
Option B lets you sample from it directly rather than assuming a Gaussian — a strict
improvement over `np.random.normal(dist_mpc, dist_err_mpc, ...)`.

**Recommendation:** Option B. It is more honest, needs no changes to `predictive_tail_kde*`
or the ABC machinery, and produces a better figure. Option A is the fallback if the M-fold
cost is prohibitive in a real-time broker context (though the analytic PPD of issue #6 makes
each pass much cheaper).

**Effort:** Medium (Option B is mostly harness code + a plotting change).
**Risk:** Widens all reported error bars. This is a correction, not a regression, but it
should be presented as such.

---

## 3. No calibration validation — the method rests on N = 1

**Location:** methodology, not a specific file. Current validation is AT2017gfo in
`Getting_Started_v0.ipynb`.

### Mechanism

`P_tail` is presented as a two-sided tail probability. The defining property of such a
statistic is that **under the null hypothesis it is uniformly distributed on (0, 1)**.
Nothing in the repository tests this. If the KDE bandwidth, the noise convolution, the
`min_sim_points` cut, or the binning introduces bias, there is currently no way to detect it,
and no way to interpret a score of, say, 0.3 in absolute terms.

Validation on a single real object (AT2017gfo) demonstrates that the pipeline runs and gives
a sensible answer once. It cannot establish calibration.

### Why it matters

This is the most valuable single addition available, because it requires **no negative class,
no labels, and no new physics** — only the simulation grid that already exists. It converts
"validated on the one known kilonova" into a quantitative calibration curve over thousands
of objects.

### Proposed fix

**3a. Null calibration (self-consistency).**

```
1. Hold out N ≈ 2000 sample_ids from the grid. Score against the REMAINING grid only —
   the held-out sims must not appear in their own reference population, or the test is
   circular and will look better than it is.
2. For each held-out sim, build a synthetic candidate:
     - sample a realistic epoch set and band set
     - inject photometric noise matched to real LSST/ZTF depth vs. magnitude
     - apply preprocess_lsst_like() to impose real cadence
     - apply a distance modulus and its uncertainty
3. Score each synthetic candidate through the full pipeline.
4. Plot the CDF of the final cumulative score.
   PERFECT CALIBRATION → uniform on (0,1) → a straight diagonal.
```

Any deviation is a direct, quantitative measurement of miscalibration. Repeat the test
sliced by number of epochs, by band coverage, and by SNR to find *where* the method is
well-behaved — which is exactly the information a user needs to know when to trust a score.

**3b. Cross-model bias.** Repeat 3a with held-out sims drawn from a *different* kilonova
model (POSSIS/Bulla, Kasen, or another `redback` model) scored against the
`two_component_kilonova_model` grid. The departure from uniformity now measures
**model-dependent bias** — the honest quantification of the tool's largest systematic, and
the direct answer to the obvious reviewer objection.

**3c. Descriptive contaminant behaviour.** Score SN Ia / Ibc / IIP light curves (SNANA or
PLAsTiCC templates) through the pipeline and report where their scores land. This is
*descriptive only* — no classifier is trained, no labels are used in fitting. It
characterises the separation the score achieves and, critically, shows which contaminants
the score cannot distinguish. That is a limitation worth stating explicitly rather than
leaving for a reader to discover.

**3d. Power curve.** From 3a and 3c together, report completeness vs. contamination as a
function of the score threshold and the number of epochs available. This turns the score
into something operationally usable: "at 2 epochs in 2 bands, a threshold of X retains Y% of
kilonovae and rejects Z% of SNe."

### Implementation notes

This is a new module (suggested: `validation.py`) plus a driver notebook. It needs no changes
to the scoring code, though it will be **much** cheaper to run once issue #6 (analytic PPD)
is done — thousands of synthetic candidates × the current Monte Carlo is expensive, while the
closed form makes it nearly free.

Fix issue #1 before running any of this, or the calibration test will measure the bug rather
than the method.

**Effort:** Medium.
**Risk:** None to existing code — purely additive.

---

## 4. Colour information is never used

**Location:** [`kilonovascorer_v1`](src/KilonovaScorer/core.py#L511) line 543 /
[`kilonovascorer_v3`](src/KilonovaScorer/core2.py#L542) line 599 — the `for band in band_list`
loop, and the per-band `overlap_chain` call at [core2.py:724](src/KilonovaScorer/core2.py#L724).

### Mechanism

The entire scorer runs inside a loop over bands. Each band gets its own time bins, its own
KDE, its own `P_tail`, and its own independent ABC survivor chain. The results are
concatenated at the end but never combined. Nothing in the pipeline ever evaluates the
**joint** behaviour across filters.

### Why it matters

Rapid reddening is the primary photometric signature distinguishing a kilonova from a young
supernova: g−i evolving by more than a magnitude within a few days, driven by the
lanthanide-rich ejecta component. It is the single most discriminating feature available in
the first 72 hours.

The current design cannot see it. A candidate can be individually consistent with the
kilonova population in g *and* individually consistent in i, while having a g−i colour that
**no simulation in the grid produces**. That object passes with a high score.

This is a marginal-vs-joint distribution problem: consistency with every marginal does not
imply consistency with the joint, and here the joint is where the physics lives.

### Proposed fix

**4a. Cross-band survivor intersection (cheap, high value).** The `sample_id` bookkeeping
that already exists solves this almost for free. Instead of running `overlap_chain`
per band, accumulate the consistent-ID sets across *all* bands and *all* epochs, then
intersect once:

```python
# current (per band, inside the band loop):
chain = overlap_chain(band_ids_lists, band_times)

# proposed (after the band loop, across everything):
all_ids_lists = [...]   # every (band, epoch) consistent-ID set
all_times     = [...]   # corresponding times, bands tracked alongside
global_chain  = overlap_chain(all_ids_lists, all_times)
```

A surviving `sample_id` must now be consistent with every observation in every filter — which
is precisely a colour constraint, since one simulated light curve has to explain g and i
*simultaneously*. This requires no new statistics and reuses `overlap_chain` unmodified.

Keep the per-band chains as well; they remain useful for diagnosing *which* band drives a
rejection. Report both.

Two things to watch:
- Survivor counts will fall much faster, so `overlap_k` will likely need retuning.
- Ordering by time across bands mixes filters within a bin; `overlap_chain` sorts by time
  (line 425), so simultaneous multi-band points need a deterministic tiebreak.

**4b. Direct colour scoring.** Where two bands have observations within a matching time bin,
form the observed colour and score it against the simulated colour distribution using the
existing machinery:

```
(g − i)_obs   with   σ² = σ²_g + σ²_i    (photometric only — the distance modulus
                                          cancels exactly in a colour, which is a
                                          significant advantage given issue #2)
```

The simulated colour distribution comes from the grid by joining on `sample_id` within the
bin. `P_tail_colour` then slots into the same aggregation as the magnitude scores.

Colours are the natural place to start addressing issue #2, since the distance systematic
cancels identically.

**4c. Colour evolution rate.** The strongest signature is d(g−i)/dt, not the colour itself.
With two epochs in two bands this is measurable and can be scored the same way. Worth
considering once 4a/4b are in place.

**Effort:** 4a is Low (restructure the accumulation, keep both chains). 4b is Medium. 4c is
Medium.
**Risk:** 4a substantially changes survivor counts and the collapse-time diagnostic; expect
to retune `overlap_k` and regenerate published figures.

---

## 5. The hard ABC intersection is fragile to single outliers

**Location:** [`compute_consistent_ids_anyhit`](src/KilonovaScorer/core2.py#L353) and
[`overlap_chain`](src/KilonovaScorer/core2.py#L397) (duplicated at
[core.py:278](src/KilonovaScorer/core.py#L278) and [core.py:294](src/KilonovaScorer/core.py#L294)).

### Mechanism

Acceptance is a hard binary cut:

```python
inside = np.abs(sim_bin["absolute_magnitude"].to_numpy() - M_obs) <= rope_half_width
```

and survivors are a monotone running intersection:

```python
survivors &= sets[i + 1]
```

Once a `sample_id` is excluded at any epoch it can never return, by construction.

### Why it matters

The collapse of `|S_t|` to zero is interpreted in the documentation as "strong evidence
against the kilonova hypothesis." But it is *equally* the signature of:

- one bad photometric point (cosmic ray, subtraction residual, contaminating source),
- one underestimated error bar,
- a marginally miscalibrated zero-point in a single filter,
- an epoch falling near a bin edge where the `anyhit` criterion behaves oddly (see #12).

Nothing in the output distinguishes "no kilonova model explains this sequence" from "one
point out of twelve is bad." Since real alert-stream photometry reliably contains occasional
bad points, this is not a hypothetical failure mode — and the diagnostic is currently
load-bearing in the interpretation.

There is a second issue: the hard cut discards the *degree* of consistency. A simulation
passing at 0.1σ and one passing at 1.49σ (with `overlap_k = 1.5`) are treated identically,
while one at 1.51σ is eliminated permanently. Most of the information in the comparison is
being thrown away at the threshold.

### Proposed fix

Replace set intersection with accumulated log-likelihood per `sample_id` — the standard move
from ABC-rejection to importance sampling:

```python
# per sample_id j, accumulated over ALL observations (all bands, all epochs)
log_w[j] += -0.5 * (M_obs - m_j(t_obs))**2 / (sigma_obs**2 + sigma_sys**2)
```

where `m_j(t_obs)` is simulation *j* interpolated to the exact observation time (see #7) and
`sigma_sys` is an optional model-error floor absorbing radiative-transfer inaccuracy.

This yields, from the same grid and roughly the same compute:

- **Effective sample size** `ESS = (Σw)² / Σw²`, playing the role of the survivor count but
  degrading smoothly rather than collapsing. One bad point down-weights; it does not
  annihilate.
- **Importance weights over simulations**, which are a principled upgrade to
  `plot_survivor_param_kde_grid` — currently an unweighted KDE over the survivor set, which
  is a crude approximation to the same object. The weighted version is closer to a genuine
  posterior over ejecta parameters.
- **A model-evidence-like quantity** `log Σ w` for comparing hypotheses, which is the natural
  route to the likelihood-ratio extension (see #14) without needing labels.
- **Outlier robustness** by swapping the Gaussian kernel for a Student-t, or by mixing in a
  small uniform outlier component:
  ```
  L = (1 − f_out) · N(M_obs | m_j, σ) + f_out · U(mag_range)
  ```
  with `f_out ≈ 0.01–0.05`. A single catastrophic point then costs a bounded amount of
  likelihood instead of an infinite amount.

Keep the existing hard-cut survivor chain alongside it, at least initially — it is the
published diagnostic, it is easy to explain, and the two can be compared directly on the same
candidates to demonstrate the improvement.

**Effort:** Medium.
**Risk:** Introduces `sigma_sys` and possibly `f_out` as new hyperparameters requiring
justification. Both are defensible but must be stated and their sensitivity shown.

---

## 6. The Monte Carlo estimates a quantity available in closed form

**Location:** [`predictive_tail_kde_python`](src/KilonovaScorer/core.py#L207) lines 248–258;
[`predictive_tail_kde`](src/KilonovaScorer/core2.py) around lines 320–338.

### Mechanism

```python
kde     = gaussian_kde(sim_values)
x_star  = kde.resample(n_sim)[0]
y_dist  = x_star + np.random.normal(0, sigma, size=n_sim)
f_hat   = np.mean(y_dist <= x0)
prob_near = np.mean(np.abs(y_dist - x0) <= k * sigma)
```

A Gaussian KDE with bandwidth *h* over points `{m_i}` is a Gaussian mixture:

```
p(x) = (1/N) Σ_i N(x | m_i, h²)
```

Convolving with independent Gaussian observational noise gives another Gaussian mixture in
closed form:

```
p(y) = (1/N) Σ_i N(y | m_i, h² + σ²)
```

so both quantities the function computes are exact, not sampled:

```
F(x₀)  = (1/N) Σ_i Φ( (x₀ − m_i) / s )                       with  s = √(h² + σ²)
P_near = (1/N) Σ_i [ Φ((x₀ + kσ − m_i)/s) − Φ((x₀ − kσ − m_i)/s) ]
```

`h` is available from scipy as `kde.factor * np.std(sim_values, ddof=1)` (Scott's rule by
default); confirm the exact convention against the scipy version in use, since
`covariance_factor` semantics have varied.

### Why it matters

The closed form is better on every axis simultaneously:

- **Deterministic** — eliminates the reproducibility problem (#11) at the source for these
  metrics, rather than papering over it with a seed.
- **Exact** — removes MC noise entirely. The notebook runs at `n_kde_sim = 5000` for speed,
  giving a standard error on `F_hat` of order `√(F(1−F)/5000) ≈ 0.007`, which is then
  amplified by the logit transform near the boundaries where `dz/dp = 1/(p(1−p))` is large.
  This is a real contributor to score instability.
- **Faster** — one vectorised `Φ` evaluation over N sim points versus drawing and comparing
  50 000 samples. The sim count per bin is typically far below `n_kde_sim`.
- **Removes a hyperparameter** — `n_kde_sim` disappears, along with the accuracy/speed
  tradeoff it forces.

This also makes the calibration study (#3) computationally feasible, since it needs thousands
of full pipeline runs.

### Proposed fix

```python
from scipy.special import ndtr   # vectorised standard-normal CDF

def predictive_tail_analytic(sim_values, M_obs, sigma_obs, k=1.5, bandwidth=None):
    m = np.asarray(sim_values, dtype=float)
    m = m[np.isfinite(m)]
    if m.size == 0:
        raise ValueError("sim_values array cannot be empty.")
    if not (sigma_obs > 0):
        raise ValueError("sigma_obs must be positive.")

    if bandwidth is None:
        # Scott's rule, matching gaussian_kde's default; verify against scipy version
        bandwidth = m.std(ddof=1) * m.size ** (-1.0 / 5.0)

    s = np.hypot(bandwidth, sigma_obs)          # sqrt(h^2 + sigma^2)

    F_hat  = float(ndtr((M_obs - m) / s).mean())
    p_tail = 2.0 * min(F_hat, 1.0 - F_hat)
    p_near = float((ndtr((M_obs + k * sigma_obs - m) / s)
                    - ndtr((M_obs - k * sigma_obs - m) / s)).mean())
    return {"F_hat": F_hat, "p_tail": p_tail, "p_near": p_near}
```

The `p_tail_std` term needs separate treatment — see #8, where the correct definition is in
question anyway. Once resolved it is also analytic, or a cheap 1-D quadrature over the
`M_obs` distribution.

**Validation:** before switching, run both implementations over a few hundred real bins and
confirm agreement within MC error. This is a good candidate for the first unit test (#15).

**Caveat worth noting:** a fixed-bandwidth Gaussian KDE over-smooths genuinely multimodal
distributions, and the simulated magnitude distribution in a bin can be bimodal where blue
and red ejecta components alternate in dominance. The analytic form does not fix this — it
reproduces the KDE exactly, including its smoothing. Worth a separate look at whether
Scott's rule is appropriate here, e.g. by comparing against a cross-validated bandwidth or
plotting a few bins against their histograms.

**Effort:** Low.
**Risk:** Low, given the equivalence test above.

---

## 7. Time binning instead of interpolation

**Location:** [core.py:552–556](src/KilonovaScorer/core.py#L552),
[core2.py:615–629](src/KilonovaScorer/core2.py#L615).

### Mechanism

Simulations are assigned to discrete time bins via `np.digitize`, and every simulated point
falling in the bin containing an observation is pooled into a single distribution.

But each `sample_id` is a **smooth, densely sampled light curve** — the default grid is 1000
time steps over 10 days. Binning discards this structure. With `time_bin_width = 0.2 d`, each
simulation contributes roughly 20 points to a bin, and those 20 points are treated as 20
independent draws from the population rather than as one curve sampled 20 times.

This inflates the apparent width of the PPD by the amount each light curve moves within the
bin — a purely numerical artefact that is largest during the fastest photometric evolution,
which is exactly the early-time regime the tool targets.

### Why it matters

Binning introduces three hyperparameters and one bias:

- `time_bin_width` — an arbitrary resolution/statistics tradeoff
- `min_sim_points` — needed only because bins can be underpopulated (#10)
- the grid's own `ntime` — a hidden dependency (#12)
- the width inflation described above

Interpolating to the exact observation time removes all four.

### Proposed fix

Pre-pivot the grid to a wide array once at load time, then interpolate:

```python
# once, at load: shape (n_samples, n_times) per band
M = pivot(data_sim, index="sample_id", columns="time", values="absolute_magnitude")

# per observation: exact, no binning
m_at_t = np.array([np.interp(t_obs, times, M[j]) for j in range(n_samples)])
# or fully vectorised via searchsorted + linear weights
```

`m_at_t` then feeds directly into the analytic PPD (#6) and the log-likelihood accumulation
(#5). Every simulation contributes exactly one value per observation, which is the correct
weighting.

Kilonova light curves are smooth on the 0.01 d grid spacing, so linear interpolation is
ample; cubic is available if wanted.

**Additional benefit:** this makes the wide-array representation natural, which vectorises
the whole scorer over `sample_id` and removes the per-observation pandas filtering that
currently dominates the runtime (#13).

**Effort:** Medium — touches the core loop in both scorers and the grid loading path.
**Risk:** Changes all scores somewhat (they will be slightly *less* dispersed, the width
inflation being removed). Memory: a 100k-sample × 1000-time float64 array per band is ~800 MB,
so use float32 and/or subset the time range at load. Worth benchmarking before committing.

---

## 8. `sigma_obs` enters the `p_tail_std` calculation twice

**Location:** [core.py:251–268](src/KilonovaScorer/core.py#L251),
[core2.py:336](src/KilonovaScorer/core2.py#L336).

### Mechanism

```python
y_dist = x_star + np.random.normal(0, sigma, size=n_sim)      # (1) noise in the PPD
...
x0_samples = np.random.normal(x0, sigma, 100)                 # (2) noise on the observation
f_hat_samples = (y_dist <= x0_samples[:, np.newaxis]).mean(axis=1)
p_tail_samples = 2 * np.minimum(f_hat_samples, 1 - f_hat_samples)
```

The **same** `sigma` is used at both (1) and (2). Step (1) is the noise-convolved prior
predictive distribution: the observational error is already folded into the reference
distribution, which is the correct construction and matches the documented method. Step (2)
then perturbs the observation by that same error again.

### Why it matters

There is a defensible reading — (2) estimates the sampling uncertainty *of the statistic*
given that `M_obs` is itself a noisy realisation — but the two uses are not independent, and
combining them the way the code does has a systematic consequence:

`p_tail` is a concave function of `x0` near the distribution's centre and convex in the tails.
Averaging over `x0_samples` therefore does not return `p_tail(x0)`. In the tails, where
`P_tail` is small, the averaging pulls `p_tail_mean` **upward** relative to `p_tail`, because
draws scattered toward the centre gain more than draws scattered further out lose.

Since `p_tail_mean` (not `p_tail`) is what feeds the cumulative score via `ivw_stats_logit`,
this bias propagates directly into the headline number — and it acts in the same direction as
issue #1, toward more kilonova-consistent scores.

The magnitude is not currently characterised anywhere.

### Proposed fix

The first step is to decide what `p_tail_std` is *meant* to represent, and to state it
explicitly in the documentation. Three coherent options:

**A. It represents MC estimation error only.** Then `y_dist` keeps `sigma`, and `p_tail_std`
should come from the sampling error of `F_hat` — analytically `√(F(1−F)/n_sim)`, or via
bootstrap. Step (2) is removed. Under the analytic form (#6) this error is zero and
`p_tail_std` becomes meaningless, which means the IVW weights need rethinking — arguably
they should be driven by `sigma_obs` and the local PPD density instead.

**B. It represents sensitivity of the statistic to the observation.** Then `y_dist` should
**not** include the noise convolution (compare `M_obs` against the raw population), and the
spread over `x0_samples` carries the observational error exactly once. This is the cleanest
reading and is close to a standard predictive p-value.

**C. Keep the current construction deliberately.** Defensible if documented as a conservative
smoothing, but the concavity bias must be quantified — e.g. by plotting
`p_tail_mean − p_tail` against `p_tail` over real bins — and the result stated.

**Recommendation:** B is the most defensible statistically and is a small change. Whichever is
chosen, the calibration test in #3 will measure the consequences directly, which is the
principled way to settle it — run 3a under each variant and see which yields a uniform null.

**Effort:** Low to change; the decision is the hard part.
**Risk:** Shifts all `p_tail_mean` values and hence every cumulative score.

---

## 9. Upper limits and non-detections are discarded

**Location:** `parse_json_photometry` in both [core.py](src/KilonovaScorer/core.py#L53) and
[core2.py](src/KilonovaScorer/core2.py) — entries flagged `upper_limit` are dropped, as are
pre-merger points.

### Mechanism

Only detections survive ingestion. Every non-detection is thrown away.

### Why it matters

In GW follow-up, non-detections are often the most constraining data available:

- A **deep non-detection hours before** the first detection bounds the rise time and the
  explosion epoch, strongly constraining ejecta mass and velocity.
- A **non-detection in one band with a detection in another** at the same epoch is a hard
  colour limit — directly relevant to the reddening signature (#4) and frequently the
  cleanest kilonova indicator available on night one.
- **Late non-detections** constrain the decline rate, which separates kilonovae from slower
  supernovae.

For a tool explicitly designed for the sparse early-time regime, discarding these means
discarding a large fraction of the total information — sometimes the majority of it on the
first night.

Note that the pre-merger cut is separately worth revisiting: a pre-merger *non-detection* is
physically meaningful (the source was not there), even though a pre-merger *detection* would
argue against association.

### Proposed fix

Upper limits fit the existing framework naturally, since they are one-sided versions of the
same comparison. For a limiting magnitude `M_lim` (fainter = larger):

**In the PPD framework:** the consistency statement is that the source was fainter than the
limit,

```
P_consistent = Pr(Y > M_lim) = 1 − F(M_lim)
```

computable with exactly the analytic machinery of #6, using the limit's own uncertainty (or a
nominal 0.1–0.2 mag for the depth estimate).

**In the survivor / likelihood framework (#5):** for a hard cut, retain `sample_id` where
`m_j(t) > M_lim`. For the soft version, use a censored-Gaussian likelihood:

```
log L_j = log Φ( (m_j(t) − M_lim) / σ_lim )
```

which smoothly penalises simulations predicting a detectable source where none was seen.

**Ingestion changes required:**
- Preserve `upper_limit` entries through `parse_json_photometry` with a boolean flag rather
  than dropping them.
- Convert limits to absolute magnitudes through the same distance-modulus path.
- Add an `is_limit` column and branch on it in the scorer.
- Decide and document the limit convention (3σ vs 5σ) — surveys differ, and getting this
  wrong biases the constraint systematically.

**Effort:** Low–Medium. The ingestion change is small; the scoring branch is a modest addition
to whichever likelihood formulation is adopted.
**Risk:** Low, and additive — candidates with no limits are unaffected.

---

## 10. Silently skipped epochs

**Location:** [core.py:583–584](src/KilonovaScorer/core.py#L583),
[core2.py:656–661](src/KilonovaScorer/core2.py#L656).

### Mechanism

```python
if len(sim_bin) < min_sim_points:
    logger.debug(...)      # v3 only; v1 has no message at all
    continue
```

The observation is dropped from scoring with no record in the returned DataFrame.

### Why it matters

Under-populated bins occur preferentially at the **temporal edges of the grid** — the earliest
and latest epochs. Early epochs are the highest-value observations for kilonova
identification, so the failure mode is biased toward discarding the most informative data.

Because the skip is invisible in the output, a user cannot distinguish:

- "this epoch scored well" from
- "this epoch was never scored"

They see a cumulative score computed over an unknown subset of their data. Two candidates
with visibly different data volumes can produce identical-looking outputs. There is currently
no field in `metric_df` recording how many observations were supplied versus scored.

The `logger.debug` in v3 is not a substitute — it is off by default and does not reach the
returned data structure.

### Proposed fix

**Short term** (independent of any other change):

- Emit a row for every input observation, with `scored = False` and a `skip_reason` field
  (`"insufficient_sims"`, `"invalid_sigma"`, `"no_sim_in_band"`, ...).
- Add a returned summary: `n_obs_supplied`, `n_obs_scored`, per band.
- Surface the scored fraction on the diagnostic plots so it cannot be missed.
- Promote the message from `debug` to `warning` when more than some fraction of observations
  are skipped.

**Long term:** issue #7 removes the problem at its root. With interpolation there is no bin
occupancy, so `min_sim_points` becomes unnecessary — every observation inside the grid's time
span is scorable. The only remaining skip condition is an observation outside the grid's
temporal coverage, which should be reported explicitly as such.

**Effort:** Low.
**Risk:** None — purely additive to the output schema. (Confirm downstream plotting filters
on `scored` before consuming rows.)

---

## 11. Unseeded RNG throughout — **FIXED**

> **Resolved.** `kilonovascorer_v3`, `predictive_tail_kde` and
> `compute_abs_mag_samples` now take a seed (`random_state`, default `42`;
> `predictive_tail_kde` takes the `rng` itself), and TROVE threads it from
> `scoring.phot_method.DEFAULT_KILONOVA_PARAMS` so a re-run reproduces its
> scores. `default_rng` as proposed below, so the caller's global stream is
> untouched — verified. Two details beyond the sketch:
>
> - `kde.resample()` is a **third** RNG source alongside the two
>   `np.random.normal` calls, and is seeded via `seed=rng`. Missing it would
>   have left results non-deterministic despite everything else.
> - The Generator is created **per `kilonovascorer_v3` call**, i.e. per
>   candidate, not once globally. Draws still differ between epochs, but a
>   candidate's score no longer depends on how many candidates preceded it —
>   without which, batching or reordering a run silently changes every score.
>
> Pass `random_state=None` for the original non-deterministic behaviour.

**Location:** [core.py:253](src/KilonovaScorer/core.py#L253),
[core.py:262](src/KilonovaScorer/core.py#L262), [core2.py:336](src/KilonovaScorer/core2.py#L336),
[utils.py:94](src/KilonovaScorer/utils.py#L94), [utils.py:113](src/KilonovaScorer/utils.py#L113).

### Mechanism

Every stochastic call uses the global `np.random` state with no seed. Scores are not
reproducible run to run, and the variation is largest at the low `n_kde_sim = 5000` the
notebook uses for speed.

### Why it matters

Results in a paper must be reproducible from the stated inputs. Beyond that, unseeded
stochasticity makes it impossible to tell whether a change in score between two runs reflects
a code change or MC noise — which will actively obstruct the work in #1, #6 and #8, all of
which require comparing before/after scores.

### Proposed fix

Thread an explicit generator rather than seeding the global state:

```python
def kilonovascorer_v3(..., random_state=None):
    rng = np.random.default_rng(random_state)
    ...
    # pass rng down to predictive_tail_kde and compute_abs_mag_samples
```

Using `default_rng` rather than `np.random.seed` avoids clobbering the caller's global state —
important if the scorer is embedded in a broker or a larger pipeline.

Issue #6 removes the need for an RNG in the `P_tail` / `P_near` path entirely, which is the
better fix where it applies. Seeding is still needed for `compute_abs_mag_samples`, and for
the distance marginalisation of #2.

**Effort:** Low.
**Risk:** None.

---

## 12. The `anyhit` criterion depends on the grid's time resolution

**Location:** [`compute_consistent_ids_anyhit`](src/KilonovaScorer/core2.py#L353).

### Mechanism

A `sample_id` is retained if **any** of its points in the bin falls inside the ROPE. With the
default grid (1000 steps over 10 days) and `time_bin_width = 0.2 d`, each simulation
contributes roughly 20 points per bin, sampled across a window during which the light curve
may move substantially.

The effective acceptance tolerance is therefore not `overlap_k · σ_obs`, but

```
overlap_k · σ_obs  +  (magnitude swept by the light curve within the bin)
```

which varies with the local decline rate — largest during fast early evolution, precisely
where discrimination matters most.

### Why it matters

This creates a **hidden dependency on a grid-generation parameter that has nothing to do with
the physics**. Regenerating the grid with `ntime = 2000` instead of 1000 gives each simulation
twice as many chances to land inside the ROPE, so survivor counts rise and the collapse time
shifts — with no change to the model, the priors, or the data.

Any published survivor count or collapse time is therefore conditional on an undocumented
numerical choice. The docstring describes the criterion as "conservative," which is accurate,
but the *degree* of conservatism is uncontrolled and epoch-dependent.

### Proposed fix

Interpolation (#7) resolves this completely: each simulation yields exactly one magnitude at
exactly the observation time, so the tolerance is `overlap_k · σ_obs` and nothing else.

If interpolation is deferred, the interim mitigation is to replace `anyhit` with the value at
the bin-centre nearest neighbour, or the bin median per `sample_id`:

```python
per_sample = sim_bin.groupby("sample_id")["absolute_magnitude"].median()
inside = (per_sample - M_obs).abs() <= overlap_k * sigma_obs
```

This at least makes the criterion one-value-per-simulation and removes the sampling-density
dependence, though it still smears over the bin width.

Either way, this should be documented as a known systematic until fixed, and the sensitivity
demonstrated by rerunning a candidate against grids of differing `ntime`.

**Effort:** Low as an interim fix; subsumed by #7.
**Risk:** Changes survivor counts and hence published figures.

---

## 13. v3's ABC helper defeats its own pre-grouping optimisation

**Location:** [core2.py:626–629](src/KilonovaScorer/core2.py#L626) versus
[core2.py:386–388](src/KilonovaScorer/core2.py#L386).

### Mechanism

`kilonovascorer_v3` pre-groups the simulations for O(1) bin lookup, as advertised:

```python
sim_groups: Dict[int, pd.DataFrame] = {k: v for k, v in sim_band.groupby("time_bin")}
...
sim_bin = sim_groups.get(bin_idx, pd.DataFrame())     # O(1) — good
```

But it then calls `compute_consistent_ids_anyhit(sim_band=sim_band, bin_idx=bin_idx, ...)`,
passing the **full band DataFrame**, and that function performs its own scan:

```python
sim_bin = sim_band.loc[sim_band["time_bin"] == bin_idx, [...]]    # O(N_band) — every call
```

So each observation triggers a full boolean scan over every simulation row in the band —
potentially tens of millions of rows for a 100k-sample grid — despite the grouping already
being available. The KDE cache above it works as intended; the ABC path does not benefit.

### Why it matters

Purely a performance issue — the results are correct. But it is likely the dominant cost in
`v3`, which undercuts the refactor's stated purpose, and it directly limits the feasibility of
the calibration study in #3, which needs thousands of full runs.

### Proposed fix

Pass the already-grouped frame:

```python
consistent_ids = compute_consistent_ids_anyhit(
    sim_bin=sim_bin,          # the pre-grouped frame from sim_groups
    M_obs=M_obs,
    sigma_obs=sigma_obs,
    overlap_k=overlap_k,
)
```

with the helper's signature changed to accept a pre-filtered bin rather than
`(sim_band, bin_idx)`. Since the function is exported at package level, either keep a
backward-compatible wrapper or bump the API version deliberately.

Issue #7's array representation removes this concern entirely.

**Effort:** Low.
**Risk:** Low — a signature change on an exported symbol; verify no other callers.

---

## 14. Single-model grid

**Location:** [simulating_kne_pop.py](simulating_kne_pop.py), `simulation.py`. The grid is
generated from `two_component_kilonova_model` at a fixed 259 Mpc over LSST g/r/i/z.

### Mechanism

Every score is implicitly conditional on one radiative-transfer model and one set of priors.
The output is presented as `P(data | kilonova)` but is really
`P(data | two_component_kilonova_model, these priors)`.

### Why it matters

This is the most predictable objection to the method, and there is currently no quantitative
answer to it. Systematic differences between kilonova models — POSSIS, Kasen, Bulla, the
two-component analytic model — are substantial, particularly in the near-infrared and at
early times, and are plausibly comparable to the differences the score is being asked to
detect.

### Proposed fix

**14a. Model-marginalised score.** Generate grids from 2–3 independent models and report
either the envelope of scores or an explicitly marginalised value:

```
P_tail_marginal = Σ_m  w_m · P_tail(data | model m)
```

with `w_m` uniform absent a reason to prefer one model. The spread across models is then a
directly quantified systematic rather than an unstated assumption — turning the weakest point
of the method into a stated error budget.

**14b. Likelihood ratio against alternative transient classes.** The same machinery, applied
to a SN Ia grid, a SN Ibc grid, and a SN IIP grid, gives:

```
R = P_tail(data | KNe) / P_tail(data | SN Ia)
```

This is the discriminative capability that a supervised classifier would provide, but
obtained without labels, without a training set, and without the domain-shift and calibration
problems that come with one. It preserves full interpretability and uncertainty propagation.

The `p_tail_KNe` / `p_near_KNe` naming already adopted in `core2.py` suggests this was
anticipated in the original design; the naming is ready for it.

Strictly, `P_tail` is a tail probability rather than a likelihood, so a ratio of two
`P_tail` values is not a Bayes factor. The likelihood-accumulation formulation of #5 gives
the quantity that *can* be ratioed properly (`log Σ w` per model class), which is another
argument for doing #5 first. If the `P_tail` ratio is used as a heuristic ranking statistic,
that should be stated plainly rather than presented as a Bayes factor.

**Effort:** High — grid generation is expensive and needs the heavier
`redback`/`bilby`/`astropy` environment. Storage for multiple 100k-sample grids is
non-trivial.
**Risk:** Low scientifically; mainly a compute and data-management cost.

---

## 15. Duplicate scorers, dead code, and no tests

**Location:** package-wide. See §9 of `DOCUMENTATION.md` for the full inventory.

### Mechanism

- `kilonovascorer_v1` ([core.py](src/KilonovaScorer/core.py#L511)) and `kilonovascorer_v3`
  ([core2.py](src/KilonovaScorer/core2.py#L542)) coexist with **different output column
  names** (`p_tail`/`prob_near` vs `p_tail_KNe`/`p_near_KNe`). There is no v2.
- `__init__.py` does `from .core import ...` followed by `from .core2 import *`, so core2's
  definitions silently win for the six names defined in both. Which implementation runs
  depends on import order.
- Plotting is written against v1's column names only.
- Large commented-out blocks in `utils.py` (lines 292–409), `core.py` (lines ~385–503), and
  `simulation.py`.
- No tests, no CI, `status = beta`.

### Why it matters

Every fix in this document has to be applied twice, to two implementations that differ in
subtle ways — `k_near` alone defaults to 1.0 in v1 and 1.5 in v3, so the two produce different
`P_near` values for identical input. The duplication roughly doubles the work and creates a
standing risk of the two drifting further apart, and the shadowing means a reader cannot tell
from the import which code path executes.

More importantly: **issues #1, #6, #7 and #8 all change reported scores.** Without tests there
is no way to distinguish an intended change from a regression, and no way to verify the
analytic/MC equivalence check proposed in #6.

### Proposed fix

Sequencing matters here — this should come **before** the substantive changes, not after:

1. **Choose one scorer.** v3 is the better base (KDE cache, pre-grouping, better docstrings,
   paper-aligned naming). Delete v1 or keep it as a thin deprecated alias that renames columns.
2. **Standardise column names** on the `_KNe` convention and update `plotting.py` accordingly.
   This currently requires a manual rename to use v3 with the plots at all.
3. **Fix `__init__.py`** to import explicitly from one module. Remove the wildcard import so
   the resolution is visible rather than order-dependent.
4. **Delete the commented-out blocks.** They are in git history if needed.
5. **Add a minimal pytest suite** before touching any scoring code:
   - a small synthetic grid with a known analytic answer
   - `P_tail ≈ 1` for an observation at the population median
   - `P_tail ≈ 0` for an observation 10σ away
   - MC vs analytic agreement within MC error (gates #6)
   - `ivw_stats_logit` returns a consistent schema on **all** paths, including all-zero and
     all-invalid inputs (gates #1)
   - survivor count is monotonically non-increasing
   - a determinism check: same seed → identical output (gates #11)
6. **Pin pandas** or at minimum test against 2.x. `groupby(...).apply(...)` returning a Series
   is deprecation-sensitive, and issue #1's behaviour depends on how pandas aligns
   inconsistent Series — this must not be left to chance.

**Effort:** Medium.
**Risk:** None to the method; unblocks everything else safely.

---

## 16. Empirical confirmation from the TROVE integration

**Added 2026-08-03.** The original review was source inspection with nothing executed. The
scorer has now been run against real data — TROVE's `trove_main` database and the 10,000-sample
`simulations_IR_two_component_kilonova_model` grid at 259 Mpc — which resolves two questions
the review left open and turns up one new failure.

### #1's failure branch: neither of the two predicted outcomes

The review flagged that it could not tell whether a zero-score bin is *dropped* by the bare
`dropna()` (branch 1b) or *propagates NaN* through the precision accumulator (branch 1c), and
asked for a targeted test. Executed: **it is a third outcome, and it raises.**

`ivw_stats_logit`'s early return carries `{"mean", "std"}` while its other paths carry
`{"mean", "std", "count"}`. When *some* bins take the early return and others do not,
`groupby(...).apply(...)` cannot align the mismatched index sets into columns, so it returns a
**MultiIndex Series** — `(time_bin, key)` pairs — rather than a DataFrame. `reset_index()` then
produces columns `['time_bin', 'level_1', 0]`, and the next line

```python
binned_stats["mean"].values
```

raises `KeyError: 'mean'`. The whole candidate fails, not just the bin.

Observed on **4 of 121** candidates for S250818k — all of them objects with enough photometry to
score, so this is not a rare-edge-case cost. The mixed-schema condition needs only one time bin
in which every epoch has `p_tail_mean == 0`, which is common for supernovae: they sit outside
the kilonova population at some epochs and inside it at others.

This does not change the prescribed fix (make every return path carry identical keys), but it
raises its priority: the current behaviour is a hard failure, not a silent bias, and it means
the `dropna()` narrowing in branch 1b must be done *together* with the 1c guard, since a
consistent schema will start delivering `std = 0.0` rows into `calculate_sequential_score_logit`.

### #1's other half: categorical rejection returns no score at all

A candidate lying wholly outside the simulated population gets `p_tail = 0` at *every* epoch.
`ivw_stats_logit`'s `p_tail_mean > 0` filter then removes everything, and the candidate comes
back **unscoreable** — indistinguishable, to a caller, from one with no photometry.

Confirmed on `SN2025adgq` (S251112cm): absolute magnitude ≈ −18 against a grid spanning
−17.2 to 0, so it is brighter than every simulated kilonova. 14 epochs, all `p_tail = 0`,
0 ABC survivors — the most confident rejection the method can produce, reported as a failure.

The correct value is 0: the two-sided tail probability of an observation outside the population
genuinely is zero. TROVE's integration layer (`scoring/kilonova_scoring.py`) special-cases this
and reports 0.0 with a note, but the fix belongs upstream in `ivw_stats_logit`.

### #11 quantified: run-to-run sigma is 15% of the score

The review noted scores are not reproducible but did not measure it. Five identical runs of the
same candidate (`SN2025uso`, S250818k, `n_kde_sim = 50000` — ten times the notebook's setting):

```
0.221, 0.248, 0.310, 0.329, 0.221     sigma = 0.041 = 15% of the mean
```

The spread (0.221–0.329) is **as large as the reported `score_err`** (~0.07). Two candidates
whose true scores differ by less than ~0.1 cannot be reliably ranked, which is the core use case.
Note this is at 50k samples; the notebook's 5000 will be worse. This makes #6 (closed form,
which removes the RNG from the P_tail path entirely) the highest-value fix in the document, not
merely "nearly free".

---

## 17. `host_df.z_type` — unguarded column access in the distance lookup

**Location:** [scoring/scoring.py:346](../scoring.py#L346) (TROVE-side, not the scorer, but it
gates every score).

### Mechanism

`get_eventcandidate_default_distance` filters the host-galaxy table and then does:

```python
userz_distance_hosts = host_df[host_df.z_type == "user spec-z"]
```

An *empty* `host_df` is handled two lines earlier. A *non-empty* one whose stored
`"Host Galaxies"` JSON has no `z_type` key raises `AttributeError: 'DataFrame' object has no
attribute 'z_type'`. Same pattern at lines 225/235/245/255 in the sibling function.

### Why it matters

**17 of 121** candidates for S250818k fail here, so they get no distance, hence no absolute
magnitude, hence no score. Because `vet_phot._score_phot` calls the same function, TROVE's
existing photometric vetting is likely losing the same targets.

### Proposed fix

Guard the column, and treat a missing `z_type` as an unranked photo-z rather than an error:

```python
if "z_type" not in host_df.columns:
    host_df["z_type"] = "photo-z"
```

Decide deliberately whether that default is right — it determines which host wins the
ranking — and check whether these records should be backfilled instead.

---

## 18. Single-distance grid

Related to #14 (single-model grid), but a distinct axis and, for GW follow-up, a more pressing one.

### Mechanism

A grid is generated at one luminosity distance: redshift enters the model through time dilation
`(1+z)` and the K-correction, so a grid is only strictly valid near the distance it was made at.
The available grid is at 259 Mpc (z = 0.056).

### Why it matters

Across the 98 GW events in TROVE that have both candidates and a localization distance, the
median event is at **1902 Mpc**, ranging from 40 Mpc to 7.5 Gpc. Nothing in the scorer records
which distance a grid is valid for, or warns when it is used far outside that range.

The saving grace is that this matters over a much narrower range than the event distribution
suggests — see the sizing analysis below.

### Proposed fix

Tag grids with their distance (now done: `KilonovaScorer/grids.py` reads it from the grid's own
`redshift` column and selects the nearest rung, with an optional `max_frac_offset` guard), and
generate a small ladder covering the range where a kilonova is detectable at all. Before
building it, measure whether the distribution actually moves: generate two ~200-sample grids at
the ends of the intended range and compare per-(band, time-bin) medians against the per-bin
sigma. If the shift is well below the sigma, one grid covers the range and the ladder is
unnecessary.

---

*Section 16-18 added 2026-08-03 from executing the scorer against TROVE data. #1's failure
branch and #11's magnitude are now measured rather than inferred; the caveat in the original
closing note is resolved.*

---

## 19. Detection selection bias — the score penalises real kilonovae at distance

**Location:** [core.py `predictive_tail_kde`](core.py) — the reference population, not the
arithmetic.

**Impact: HIGH | Effort: MEDIUM.** Added 2026-08-04 from measurement, not inspection.

### Mechanism

`P_tail = 2*min(F, 1-F)` asks where an observation sits within the **full** simulated
population. But an observation only exists because it was **detected**, and detection requires

    M_obs < m_lim - mu(D)

The simulated population is not subject to that cut. The prior spans `mej` from 1e-4 to 0.1
Msun, so it contains a large majority of kilonovae that are far too faint to ever be seen —
at t = 3 d in r-band the faintest simulation is M ~ 0, and 5% of the population is fainter
than M = -5.4. Those draws are physically legitimate; they are simply invisible.

So the score compares a *detected* object against a population dominated by *undetectable*
ones. A real kilonova is therefore forced into the bright tail of the reference distribution,
and `2*min(F, 1-F)` reads a bright tail as inconsistency.

**The controlling variable is `M_lim = m_lim - mu`, not distance.** An earlier version of
this entry said the bias "grows with distance". That is wrong, and Darc & Kilpatrick (2026)
Section 4.4 refutes it directly: they score LSST ToO simulations at 259 Mpc *with* per-band
limiting magnitudes and an SNR > 3 cut, and kilonovae keep a median cumulative score of 0.52
(BNS) / 0.68 (NSBH). No collapse.

The reason is that depth and distance enter only through their difference:

| survey | m_lim (SNR>3) | D (Mpc) | M_lim | regime |
|:-------|--------------:|--------:|------:|:-------|
| LSST ToO (paper Sec 4.4) | 25.6 | 259 | -11.5 | cut nearly inert -> **calibrated** |
| ATLAS (TROVE, measured)  | 20.6 | 259 | -16.5 | cut deep in population -> **biased** |
| ATLAS (TROVE, measured)  | 20.6 | 400 | -17.4 | brighter than the grid maximum (-17.2) |

The paper's `M_lim = -11.5` at 259 Mpc is *identical* to the 40 Mpc / `m_lim = 21.5` case in
the self-consistency table below, and the scores agree (0.52 vs 0.507) across a 6.5x
difference in distance. Two independent measurements at matched `M_lim` landing on the same
number is strong evidence that `M_lim` is the right variable.

So the paper's validation is correct **for the regime it tested** — Rubin/LSST follow-up.
TROVE's stream is 87% ATLAS at ~4.9 magnitudes shallower, which is where the effect lives.
This makes the issue more specific to TROVE, not less real.

Fraction of the grid that is detectable at `m_lim = 21.5` (10,000-sample grid, r-band). Read
the rows as `M_lim` values, not as distances:

| D (Mpc) | t=0.5 d | t=1 d | t=3 d | t=7 d |
|--------:|--------:|------:|------:|------:|
|      40 |   98.6% | 94.9% | 54.1% | 25.4% |
|     150 |   77.1% | 72.1% | 10.6% |  0.0% |
|     300 |   20.4% | 15.4% |  0.0% |  0.0% |
|     400 |    3.2% |  1.3% |  0.0% |  0.0% |

### Why it matters

Self-consistency test: take simulations that would be **detected** at a given distance — these
are genuine kilonovae by construction — treat each as an observation, and score it against the
full population exactly as the scorer does. A calibrated method returns `P_tail` roughly
uniform on (0,1), median ~0.5.

| D (Mpc) | t (d) | band | median P_tail | fraction < 0.1 |
|--------:|------:|:-----|--------------:|---------------:|
|      40 |   0.5 | r    |     0.507     |          8.7%  |
|      40 |   3.0 | r    |     0.541     |          9.2%  |
|     150 |   1.0 | r    |     0.639     |          6.9%  |
|     150 |   3.0 | r    |   **0.106**   |      **47.3%** |
|     300 |   0.5 | r    |   **0.204**   |      **24.5%** |
|     300 |   1.0 | r    |   **0.154**   |      **32.5%** |

At 40 Mpc the method is well calibrated — median 0.51, exactly as it should be. Beyond
~150 Mpc it is not: at 300 Mpc a genuine kilonova receives a median `P_tail` of 0.15-0.20 and
**a third of real kilonovae score below 0.1**, which reads as rejection. At 150 Mpc by 3 days,
**nearly half** do.

The bias is systematic, directional, and grows with both distance and time since merger — i.e.
it is worst exactly where GW follow-up operates and where late-time colour would otherwise be
most diagnostic. Unlike the RNG scatter of #11 it does not average out, and unlike #1 it
affects the objects the tool exists to find rather than the ones it should reject.

This also explains the calibration that #3 asks for without ever running it: the null test
would have surfaced this immediately at any distance beyond the local universe.

### Why the published validation did not catch it

Not because the detection cut was omitted — it was applied. Section 4.4 filters the
simulated transients to LSST per-band limiting magnitudes with SNR > 3 before scoring. And
since the repository contains **no limiting-magnitude handling anywhere** (verified by
reading it: `preprocess_lsst_like` is a cadence downsampler, ≤1 observation per night per
band, with no depth threshold; the only mention of limits is a comment that non-detections
are ignored), that cut was applied to the *test objects* while the reference PPD stayed
untruncated.

So their experiment contained exactly the asymmetry described above — detection-limited
observations scored against an undetection-limited reference — and was harmless, because at
`M_lim = -11.5` the truncation removes almost nothing. The asymmetry is only damaging when
the cut lands deep in the population.

The real-data cases are likewise uninformative here, for a different reason. AT 2017gfo sits
at `M_lim ~ -11.5` (40 Mpc at wide-field depth), the calibrated regime. SN 2025ulz is a
contaminant whose correct answer is a low score, which the bias also produces. And no
spectroscopically confirmed kilonova exists beyond AT 2017gfo, so no real-data test can
reach the shallow-survey / large-distance corner at all. That is why the simulation
self-consistency test is the right instrument: it probes that corner without needing a
distant kilonova to exist.

### Corroboration from the paper's own k-correction test

Section 4.3 independently supports the distance-ladder argument (`generate_ladder.py`):

> "the shift in effective rest-frame wavelength, lambda_rest = lambda_obs/(1+z), leads to
> non-negligible band mismatches, and proper K-corrections, rest-frame filter mapping, and
> time-dilation corrections should be applied"

They measured the effect on GRB160821B (z = 0.1616, ~800 Mpc) and found score changes of
order **0.1-0.2**, judged it tolerable, and did not correct for it. That is the same
magnitude as the one-rung error derived independently in `generate_ladder.py` (worst 0.186,
median 0.120). The ladder implements the correction the paper says should be applied, by
evaluating the model at the candidate's own redshift rather than remapping bands after the
fact.

**Scope of the damage.** Ranking *within* an event largely survives: at 389 Mpc a SN Ia
(M ~ -19) is brighter than the grid's most luminous simulation (-17.2) and is rejected on
absolute magnitude regardless, while a kilonova at M ~ -16 keeps a suppressed but non-zero
score. What does not survive is absolute interpretation (a fixed "score > x" threshold is
not transportable across distance) and cross-event pooling (a 150 Mpc event and an 800 Mpc
event are scored on different scales). Until this is fixed, use the score to order
candidates within one event, not to compare across events or against a fixed cut.

### Proposed fix

Condition the reference population on detectability — a truncated prior predictive:

    F_det(M_obs) = Pr(M_rep <= M_obs | M_rep < M_lim)

so both the observation and the reference are subject to the same selection. Concretely, in
`predictive_tail_kde`, restrict `sim_values` to `sim_values < M_lim` before fitting the KDE
(and renormalise), where `M_lim = m_lim - mu` for that observation.

### Where `m_lim` comes from — measured, not assumed

    M_lim = m_lim - mu,    mu = 5*log10(D_Mpc * 1e6) - 5

Measured over 4,362 detections and 3,599 upper limits across the six largest events
(S251112cm, S240514c, S240807h, S230529ay, S240716b, S240618ah):

| source | availability | verdict |
|:-------|:-------------|:--------|
| survey upper limits, matched per observation | 1.8% within 0.5 d, 13.5% within 3 d | **not viable per-observation** |
| depth implied by the detection's own S/N | 96.1% of detections | **primary** |
| per-facility default depth | always | fallback for the remaining 3.9% |

Upper limits are *abundant* — 45% of all photometry — but they are not co-located with
detections: only 1.8% of detections have a limit on the same target in the same filter
within half a day. Limits constrain epochs where nothing was seen, which is precisely
where detections are not. So #9 is still worth doing, but it does **not** supply the
per-observation threshold this fix needs. Its real value here is calibrating the
per-facility fallback: the ATLAS limits have median depth 20.59 (o) / 20.69 (c).

The S/N route is available almost everywhere, since `magerr` is always populated:

    sigma_m = (2.5/ln10) * sigma_F/F  ~  1.0857/SNR   =>   SNR ~ 1.0857/magerr
    m_lim = m + 2.5*log10(SNR / sigma_thresh)

Median implied SNR is 8.1 (p5 3.5, p95 36.2), giving a median implied depth of 20.18 —
consistent with the 20.59 the ATLAS limits report directly, which is a useful independent
check on the whole approach.

**Two traps in the S/N route.**

*The placeholder.* `DEFAULT_MAGERR = 2.5/(3*ln10) = 0.362` is substituted when a datum
carries no error, and corresponds to SNR = 3 exactly. Fed through the formula with
`sigma_thresh = 5` it yields a limit *brighter* than the detection. It affects 3.9% of
detections and must be excluded explicitly, not just handled by the degeneracy guard —
these observations have no measured uncertainty at all, so they cannot support the S/N
route in any form.

*`sigma_thresh` is not 5.* With `sigma_thresh = 5`, **17.3%** of real detections give
`m_lim < m` — the object is fainter than its own detection limit, which is impossible.
The cause is that surveys report detections below 5 sigma: the S/N distribution runs down
to 3.5 at p5. The threshold is a property of each pipeline, so take it from the data —
the low percentile of a facility's own S/N distribution is a direct estimate. With
`sigma_thresh = 3` the median depth becomes `m + 1.08` and the degenerate cases vanish by
construction, since a detected object necessarily has `SNR >= sigma_thresh`.

Do **not** "fix" the degeneracy by clamping `m_lim = max(m_lim, m)`. That sets the
truncation exactly at the observation, making `F_det(M_obs) = 1` and `P_tail = 0` — it
would silently convert every low-S/N detection into a hard rejection.

**Data-quality prerequisite.** `telescope` is blank on 2,959 of 4,362 detections (68%),
so a per-facility fallback cannot key on it as it stands. The facility is usually
recoverable from `filter_raw`, which carries suffixes like `orange-ATLAS`, `r-ZTF`,
`g-ZTF`. That mapping has to exist before the fallback or the `sigma_thresh` estimate can
be per-facility.

Guard the degenerate case: if the truncation leaves too few simulations (as at 300 Mpc,
t >= 3 d, where *nothing* is detectable), the honest output is "the grid cannot say", not a
score — the observation is outside the regime the model population covers at all.

Note the interaction with #2: truncating changes the width of the reference distribution, so
the `p_tail_std` weighting must be recomputed, not carried over.

### Sample-size consequence

Truncation is why the grid is generated untruncated and large. Detectability is a property of
a *(sample, distance, band, epoch, depth)* cell, not of a draw — the same light curve is inside
the reference population at 40 Mpc and outside it at 400 Mpc — so there is no subset that could
have been skipped at generation time. Filtering during generation would also bake one `m_lim`
and one distance into the file, reintroducing exactly the coupling the distance ladder removes,
in a form invisible to anything reading the grid later.

The cost is that samples are spent unevenly. Survivors of the 10,000-sample grid after the cut
at `m_lim = 21.5` (r-band):

| D (Mpc) | t=0.5 d | t=1 d |
|--------:|--------:|------:|
|     150 |  ~7,700 | ~7,200 |
|     300 |  ~2,040 | ~1,540 |
|     400 |    ~320 |  ~130 |

~130 samples is marginal for a KDE, and the resulting noise is not captured by `p_tail_std`
(#2), which measures the spread of the reference population rather than the uncertainty in
having estimated it. So the fix needs a minimum-survivor threshold alongside the empty-set
guard above — below it, report "insufficient reference population" rather than a score.

If the far rungs prove too thin in practice, two remedies, in order of preference:

* raise `N_SIM` for the distant rungs only — the rungs are independent files, so this is
  additive and requires no regeneration of the near ones;
* importance-sample the prior toward the detectable region and carry the weights into a
  weighted KDE. Better per CPU-hour, but it makes every downstream consumer weight-aware.

Neither is worth doing before the truncated scorer exists and the survivor counts can be
measured rather than predicted.

### Validation

The self-consistency test above is the acceptance criterion, and it is cheap: after the fix,
median `P_tail` for detectable simulations should be ~0.5 at **every** distance, not just at
40 Mpc. Keep it as a regression test (#15 asks for a test suite; this is the highest-value
entry in it).


---

## 20. Library defaults do not reproduce the paper, and k_ABC is grid-size dependent

**Location:** [core.py `kilonovascorer_v3`](core.py) signature.

**Impact: MEDIUM | Effort: LOW.** Found by reading the upstream repo against the paper.

The tuning constants used to produce the published results are passed explicitly in the
authors' notebook and differ from the library defaults we inherit:

| parameter | paper / notebook | our v3 default | upstream v1 default |
|:----------|-----------------:|---------------:|--------------------:|
| `k_near`     | 3.0 | **1.5** | 1.0 |
| `overlap_k` (= k_ABC) | 1.5 | **2.0** | 2.0 |
| `n_kde_sim`  | 5000 | 50000 | 50000 |

`overlap_k` is the ABC acceptance parameter: `rope_half_width = overlap_k * sigma_obs` in
`compute_consistent_ids_anyhit`. Note also that our vendored `core.py` is upstream's
`core2.py` (v3), while the paper's numbers come from v1 — so our scores are not directly
comparable to published values even at matched parameters.

**`k_ABC` must be calibrated to the grid size.** Appendix A of the paper gives the minimum
viable threshold as a function of `N_sim`: 1.5 at `N = 1e5`, **2.0 at `N = 1e4`**, 2.5 at
`N = 1e3`. Below it, too few draws satisfy the ROPE, the survivor set collapses to zero, and
the cumulative score is *hard-penalised to zero* — indistinguishable from a confident
rejection. Our rungs are `N = 1e4`, so `overlap_k = 2.0` is exactly the minimum with no
margin. It is currently correct by inheritance rather than by choice; it should be set
explicitly, with a comment tying it to `N_sim`, and 2.5 considered for safety.

This is a plausible contributor to the total-rejection cases currently absorbed by
`zero_on_total_rejection` in `kilonova_scoring.score_candidate` — worth re-checking once the
parameter is set deliberately.

Appendix A also validates the grid size: `N = 1e4` reproduces the `N = 1e5` gold standard to
within the `P_tail` uncertainty (|delta| <~ 0.1), and `N = 1e3` is ~100x faster with no
significant loss. That is useful headroom for the far-rung survivor-count problem in #19.

---

## Suggested sequencing

Ordered so that each stage makes the next one safe or cheap.

**Stage 0 — foundations.**
#15 (consolidate + tests) and #11 (seeding). Nothing else should be attempted before there is
a way to detect regressions and reproduce a run.

**Stage 1 — correctness.**
#1 (zero-score handling) first, since it is a bug with a clear direction of bias, then #8
(decide the `p_tail_std` definition) and #10 (report skipped epochs). All three are cheap and
change what the numbers mean.

**Stage 2 — cheap wins.**
#6 (analytic PPD) and #13 (pre-grouping). Both are low-risk, both make everything downstream
substantially faster, and #6 makes the pipeline deterministic — a prerequisite for meaningful
before/after comparison in later stages.

**Stage 3 — validation.**
#3 (calibration study). Do this once the pipeline is fast, deterministic, and free of the
Stage 1 bugs. Everything after this point should be re-validated against it, and it becomes
the objective test for the design choices left open in #8.

**Stage 4 — method improvements.**
#2 (distance systematic) and #4a (cross-band survivors) are the highest-value scientific
changes. Then #7 (interpolation), which also resolves #12 and removes `min_sim_points`
entirely. Then #5 (soft likelihood) and #9 (upper limits), which build naturally on #7.

**Stage 5 — scope extension.**
#14 (multi-model grids, likelihood ratios). Largest effort, best done once the core method is
stable and calibrated.

---

## Highest-value items, if only a few are attempted

- **#1** — a bug that biases the score toward accepting kilonovae, in the code path that
  handles the strongest disconfirming evidence. Cheapest fix here with the largest correctness
  payoff. Confirm the branch with a test first.
- **#2** — the cumulative score's error bars are wrong, and get worse with more data.
- **#3** — the strongest possible addition for a paper, requiring no negative class and no
  new physics.
- **#6** — nearly free, makes the tool deterministic and much faster, and is a prerequisite
  for #3 being practical. **Promoted on measurement (§16): run-to-run sigma is 15% of the score,
  as large as the quoted error bar, so candidate ranking is currently not reproducible. This is
  arguably now the first thing to fix.**
- **#17** — one guarded column access; without it 14% of candidates on a real event get no
  distance and therefore no score, and TROVE's existing vetting loses the same targets.
- **#19** — measured, systematic, and pointed the wrong way: beyond ~150 Mpc the score
  penalises genuine kilonovae, with a third of them scoring below 0.1 at 300 Mpc. Every
  other item on this list makes the score noisier or subtly biased; this one makes it wrong
  about the objects the tool exists to find.

---

*Review based on source inspection of `main` at commit `98e53fc`. No code modified.*

*Update 2026-08-03: the scorer has since been executed against real data — see §16. Issue #1's
failure branch is no longer inferred: it is neither of the two predicted outcomes but a third,
a `KeyError` raised from a MultiIndex Series, and it hard-fails the candidate. #11's magnitude
is now measured at 15% run-to-run sigma. Two further issues (§17, §18) were found in the same
pass.*
