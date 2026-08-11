# KilonovaSCORER — timings

Measured numbers for everything that costs wall-clock time: grid generation,
grid loading, scoring, and the database round-trips around them. Where a figure
is an estimate rather than a measurement it says so.

All measurements on the development machine unless noted:
WSL2, 8.7 GB RAM (~6.5 GB usable), 7 cores, Postgres reached over an SSH tunnel
to `datatrove.as.arizona.edu`. **The tunnel matters** — several costs below are
network latency, not computation, and will differ on the production host.

---

## 1. The headline: where time actually goes

Two things dominate, and neither is the statistics:

| stage | cost | notes |
|---|---|---|
| Grid generation (one rung) | **52 min** | one-off, per distance |
| Grid **loading** during a run | **~22 s per load** | repeated; ~40 loads in a full event |
| Per-observation scoring | **3.8 ms** | after the optimisations in §3 |
| Photometry fetch (per candidate) | **851 ms** | drops to ~113 ms when batched |
| Distance resolution (per candidate) | **186 ms** | one DB round trip each |

The lesson learned the hard way: **on sparse light curves the per-observation
maths is a rounding error.** A candidate with 7 points spends ~27 ms in the
scorer and ~1 s in the database. Optimising the inner loop 4.7× (§3) saved
under a second across 30 candidates. Attack I/O and per-candidate overhead
first; see §5 for what is still on the table.

---

## 2. Grid generation

`generate_rung.py --distance 259`, 10,000 samples x 38 bands x 1000 epochs.

| | |
|---|---|
| Wall clock | **52.2 min** (3133 s) |
| Output | 380,000,000 rows, 3.84 GB parquet |
| Throughput | ~3.2 draws/s across 6 worker processes |
| Peak RSS | ~1.5 GB per worker |

Notes:

* The docstring's "~30 min per rung" was optimistic; 52 min is the measured
  figure for 38 bands. An earlier 4-band grid is where 30 min came from.
* Parallel efficiency is poor — 3.2 draws/s on 6 cores is barely better than
  serial, because `redback` evaluates one band at a time inside each draw.
* Results are streamed to parquet in chunks of `chunk_size` samples, so memory
  stays flat. Collecting the whole grid in a list first needs ~40 GB and does
  not fit.
* `_prewarm_bandpasses()` is required before the `Pool` starts. Without it,
  six workers race to download the same uncached sncosmo bandpass, one reads a
  half-written file, and the run dies ~40 min in with
  `ValueError: zero-size array to reduction operation maximum`. That failure
  cost two full generation attempts before the cause was found.

### Storage

| | parquet | Postgres (estimated) |
|---|---|---|
| Bytes/row | 16.7 | ~142 (14 cols + 24 B tuple header) |
| One rung | **3.84 GB** | ~54 GB + 12–20 GB index |
| Three-rung ladder | ~11 GB | ~160–220 GB |

Only `magnitude` and `absolute_magnitude` carry real entropy; the 10 parameter
columns are constant per `sample_id` and compress to ~0 in parquet, while a row
store would pay 8 bytes each on every row. Keep grids as files.

---

## 3. Per-observation scoring

Measured with a 10,000-point simulation slice, `sigma_obs = 0.15`, `n_obs = 100`.

### Before

| component | cost | share |
|---|---|---|
| `kde.resample(50000)` | 10,650 µs | 60% |
| `n_obs=100` uncertainty (broadcast) | 5,475 µs | 31% |
| noise convolution | 1,106 µs | 6% |
| `p_tail` point estimate | 38 µs | <1% |
| `p_near_KNe` | 479 µs | (removed, §4) |
| **total** | **17.73 ms** | |

### After

| change | cost/obs | speedup |
|---|---|---|
| baseline | 17.73 ms | 1.00x |
| + `searchsorted` | 15.12 ms | 1.17x |
| + `n_kde_sim` = 20,000 | 6.52 ms | 2.72x |
| + `n_kde_sim` = 10,000 | **3.78 ms** | **4.69x** |

**`searchsorted`** replaces the `(n_obs, n_sim)` boolean broadcast — 5,000,000
elements per observation — with one sort plus 100 binary searches.
`searchsorted(side="right")` counts `y <= M`, so it is *exactly* the old
`.mean(axis=1)`: verified bit-identical, `max abs diff = 0.0`. Free.

**`n_kde_sim` 50,000 → 10,000** is the big one, because the resample dominates.
The accuracy cost is negligible — MC standard error on `F_hat` is
`sqrt(F(1-F)/n)`:

| n_kde_sim | resample | SE(F_hat) |
|---|---|---|
| 50,000 | 10,651 µs | 0.0022 |
| 20,000 | 4,570 µs | 0.0035 |
| 10,000 | 2,256 µs | 0.0050 |
| 5,000 | 1,333 µs | 0.0071 |

Against a characteristic `P_tail` uncertainty of ~0.1 (paper Appendix A), 0.005
is noise. The reference notebook ran at 5,000. Scores agreed across all four
settings within run-to-run spread (0.6186 / 0.6165 / 0.6144 / 0.6222, spread
±0.013).

### The closed form is *slower* — do not implement ISSUE #6 as written

IMPROVEMENTS.md §6 argues the analytic Gaussian-mixture CDF is "better on every
axis at once". Measured, for the path that actually runs:

| | cost |
|---|---|
| analytic (10k sims x 100 M_obs draws) | **56,739 µs** |
| Monte Carlo 50k + sort/searchsorted | **19,551 µs** |

The analytic form is **2.9x slower**, because the `n_obs = 100` uncertainty
resampling turns it into 1,000,000 `ndtr` evaluations. It would win for a
*point* estimate alone (10,000 `ndtr` ≈ 0.6 ms vs 10.6 ms to resample), so the
claim is only true if the uncertainty is dropped — and it cannot be, because
`p_tail_std` is the inverse-variance weight in the paper's eqs. 10–14. Agreement
was good (mean F 0.6946 analytic vs 0.6915 MC), so this is a performance
verdict, not a correctness one.

---

## 4. Work removed

| removed | saving | why |
|---|---|---|
| `p_near_KNe` | 479 µs/obs (~14%) | Paper §3.4.2 says it is never aggregated; TROVE discarded it entirely — it lived only on `per_observation`, and the production task runs `score_event(keep_frames=False)`, which builds the table from `as_row()` alone. Computed and thrown away. |
| CSV→parquet converter | — | 131 lines, dead since `simulation.py` writes parquet natively |
| `plotting.py` | — | 605 lines, imported by nothing |

**Not removable:** the `n_obs = 100` uncertainty. It looks like the obvious
target at 31% of per-observation time, but `p_tail_std` is the inverse-variance
weight in the logit aggregation (eqs. 10–14) and `p_tail_mean` — not the point
estimate — is what feeds the score. Dropping it does not skip a report, it
replaces the paper's estimator with an unweighted one.

---

## 5. I/O and per-candidate overhead — the remaining wins

### Database (over the SSH tunnel)

| call | cost |
|---|---|
| `get_event_photometry`, one candidate at a time | **851 ms** |
| `get_event_photometry`, 6 candidates in one query | 677 ms total (**113 ms** each) |
| `get_candidate_distance` | **186 ms** per candidate |

Batching is ~7.5x cheaper per candidate. `score_event_by_distance` already does
this — it fetches photometry once per event and resolves distances up front.
**Any ad-hoc harness that loops `score_candidate` re-queries per candidate and
will mis-measure the pipeline by ~1 s/candidate.** This mistake was made while
benchmarking; do not repeat it.

### Grid loading

A load is a full scan of the parquet: **~22 s**, and a full-event run did ~40 of
them (~15 min of a 40 min run). The tail is worst — once the well-populated band
combinations are done, the remaining candidates have singleton band sets, so
each pays a full scan to score one target. Observed packing over one event:
`19, 7, 3, 1, 1, 1, 2, 1, 1, 2, 1, 1, 3, 3, 14` candidates per load.

**Band-partitioning the parquet is the biggest outstanding win** and touches no
maths. `atlaso` + `atlasc` are 85% of TROVE's photometry, so with hive-style
`band=atlaso/…` a typical load touches 2 of 38 partitions instead of scanning
3.84 GB. It also relieves the memory ceiling in §6.

### Other candidates, not yet measured

* **Cache `x_star` per (band, time-bin).** The PPD draw depends only on the grid
  slice; only the noise convolution needs `sigma_obs`. Currently redrawn per
  observation even though the *KDE object* is cached. Caveat: observations
  sharing a bin would share draws, a small behaviour change.
* **Per-candidate grid regrouping.** `kilonovascorer_v3` builds `sim_groups`
  inside each call, over the whole in-memory grid (18.6M rows for two bands).
  Suspected to dominate per-candidate time, but not yet isolated.
* **The 10^3 grid.** Paper Appendix A: ~100x faster scoring with residuals
  within the `P_tail` uncertainty. Requires `k_ABC` = 2.5 instead of 2.0.

---

## 6. Memory (a time cost when it goes wrong)

`_chunk_by_bands` measured **12.9 GB peak RSS** for a 12–13 band load — pyarrow's
transient peak during the read is far above the final frame, roughly 1 GB per
band. The library default of `max_bands_per_load = 6` therefore needs ~6 GB
free.

| bands per load | peak RSS |
|---|---|
| 3 | 4.09 GB |
| 6 (old default) | ~6 GB (projected) |
| 12–13 | 12.9 GB |

`DEFAULT_KILONOVA_PARAMS` now sets **3**. On WSL an overrun is not a clean
failure: the VM's OOM killer picks an arbitrary victim, which has included the
VS Code server. Trade-off: fewer bands per load means more loads, and each load
is a full scan (§5) — raise toward 6 on a larger production host.

A loaded two-band grid is 18.6M rows / **261 MB** resident after the `t<=0` and
`M>0` artifact cuts drop 1.35M rows.

---

## 7. Full-event runs

**S251112cm, 457 candidates, 226 scored / 231 skipped** — `max_bands_per_load=3`,
`n_kde_sim=50000`, before the §3 optimisations and before upper-limit support:

| | |
|---|---|
| Wall clock | **40.0 min** |
| Peak RSS | 4.09 GB |
| Grid loads | 40 (~15 min) |
| Scoring | ~3 s/candidate (~6 min) |

Estimated split: ~15 min grid reads, ~6 min scoring, remainder database and
per-candidate setup.

### The 2-hour run that was stopped, and what it exposed

A `Vet All` run with `min_obs=1`, upper limits, and `max_bands_per_load=3` was
killed at ~11 min, on track for **~2 hours**. It was not a regression in the
statistics — it exposed two structural problems, both now fixed.

**(a) `max_bands_per_load=3` produced 125 loads instead of 11** (§6). ~71 min of
I/O for a 0.7 GB memory saving. Reverted to 6.

**(b) Per-candidate work that did not depend on the candidate.** Scoring ran at
**4.9 s per (candidate, band)** for units averaging 2.9 observations — about
11 ms of real statistics, so **over 99% was overhead**:

| operation | cost | frequency |
|---|---|---|
| `grid[grid.filter_mapped == band]` | 0.48 s | per candidate, per band |
| `digitize` into time bins | 0.26 s | per candidate, per band |
| `groupby("time_bin")` | 1.27 s | per candidate, per band |
| `compute_consistent_ids_anyhit` re-scanning the band | ~0.3-0.5 s | **per observation** |

The last one was ISSUE #13, flagged in the source as "likely the dominant
runtime in v3" and never fixed.

**Fixes:** `_band_time_index` computes the time-sorted band view once per
(grid, band); `_bin_slice` replaces digitize + groupby with two binary searches
(a uniform bin is a contiguous range in time-sorted order — exactly equivalent);
and the already-selected bin is passed to `compute_consistent_ids_anyhit`.

| | per candidate |
|---|---|
| before | ~9.8 s (4.9 s x 2 bands) |
| after | **1.76 s** |

**A warning worth recording.** The band cache was first stored in
`DataFrame.attrs`. pandas deep-copies `attrs` inside `__finalize__`, so every
subsequent slice copied the multi-million-row cached frames: **13.1 s of 17.7 s**
went to `deepcopy`, making the "optimisation" slower than what it replaced.
Never put large objects in `.attrs`. The cache now lives in a module-level dict
keyed by a weak reference to the grid.

**Not bit-identical.** `_band_time_index` sorts by time, and `kde.resample`
draws seeded indices *into the dataset*, so a different row order yields
different samples. `AT2025adhh` moved 0.3311 → 0.3287 (0.7%), well inside the
±0.013 run-to-run MC spread measured in §3. Results remain reproducible going
forward; they just do not match runs from before this change.

### Measured: a full clean run

`S251112cm`, Vet All from the web page, `db_worker --queue-name
kilonova_scoring`, `min_obs=1 n_kde_sim=10000 max_bands_per_load=6`.

| | |
|---|---|
| Wall clock | **1,746 s = 29 min 6 s** |
| Candidates in the event | 457 |
| Reached a grid load | 443 |
| Scored | **423** |
| Grid loads | **11** |
| Per candidate, overall | **3.94 s** (1,746 / 443) |
| Worker RSS | 2.0–2.5 GB, flat throughout |

Against ~2 h before this section's changes, so roughly a **4× speedup**, and
the projection above (~25 min) was 17% optimistic.

The 11 loads all hit the *same* 259 Mpc rung. That is `_chunk_by_bands` doing
its job — candidates are packed into groups whose band union stays under
`max_bands_per_load` — but each group pays a fresh 3.6 GB scan, because
`clear_cache()` drops the grid after every chunk to bound peak memory. Loads
6–11 alone re-read the file six times to serve 297 candidates.

> The 3.94 s/candidate here is **not** comparable to the 1.76 s in §5. That
> figure is scoring only; this one includes the 11 grid loads and the
> per-candidate photometry and distance queries over the SSH tunnel. The split
> between the two cannot be recovered from the log, which carries no
> timestamps — add them before trying to attribute the difference.

### The same run on the Postgres grid backend

Same event, same task, same params except the backend and
`max_bands_per_load`. See `DB.md` for the store itself.

| | Parquet | Postgres |
|---|---|---|
| `max_bands_per_load` | 6 | 10 |
| Wall clock | 1,746 s | **983 s (16 min 23 s)** |
| Grid loads | 11 | **3** |
| Per candidate | 3.94 s | **2.22 s** |
| Peak worker RSS | 2.5 GB | 2.66 GB |
| Scored | 423 / 457 | 423 / 457 |

**1.78× faster, 763 s saved, and all 423 scores are bit-identical** — the
backend changes where the numbers come from, not what they are.

Nearly all of the saving is I/O. Loads went from 11 × ~60 s to 3 × ~10 s, about
630 s; the remaining ~130 s is unattributed, plausibly less GC and page-cache
pressure from not decompressing 3.6 GB eleven times. The log has no timestamps,
so that last part is inference, not measurement.

**Raising `max_bands_per_load` is no longer the lever it was.** Measured load
cost on Postgres, full time axis:

| bands/load | load | peak RSS |
|---|---|---|
| 6 | 4.8 s | 1.64 GB |
| 8 | 6.0 s | 2.13 GB |
| 10 | 9.2 s | 2.62 GB |
| 15 (all) | 12.0 s | 3.84 GB |

Under Parquet a load cost ~60 s whatever it read, so few large loads was the
only sane policy. Here cost scales with what is asked for, so 11 small loads
would have cost ~53 s and one big one ~12 s — a ~40 s difference in a ~1,000 s
run. 10 was chosen over 15 because the scorer's per-band sorted index cache
adds ~1.7 GB on top of the load peak, and 3.84 + 1.7 GB exceeds what was free.

**The bottleneck is now unambiguously the scorer, not I/O.** ~950 s of the
983 s is Monte-Carlo work on 443 candidates, single-threaded on a 7-core
machine. Candidates within a load are independent, so a process pool over the
candidate loop is the next real win — bigger than anything left in storage.

---

## 8. How to measure

* **Full run:** `scoring/KilonovaScorer/grids/*.log` for generation; the
  `db_worker` log for scoring. `get_kilonova_status(nle_id)` records
  `finished_at`, `scored`/`total` and the parameters used.
* **Per-observation:** benchmark `predictive_tail_kde` directly against a
  synthetic slice — do not go through `score_candidate`, which is dominated by
  database and setup cost.
* **Never benchmark by looping `score_candidate` per candidate** (§5).
* Peak RSS: sample `ps -o rss=` on the worker; the run itself does not record it.
