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

### The process pool

`_score_chunk` in `kilonova_scoring.py` spreads each load's candidates over
forked workers. `fork`, not `spawn`: the grid is 1-2 GB and its band index
another ~1 GB, so children inherit it copy-on-write through a module global
(`_WORKER_GRID`) rather than receiving a pickled copy each. `prewarm_band_indexes`
builds every band's sorted view *before* the fork for the same reason — built
lazily afterwards, each child holds its own private copy.

Watch out for a measurement trap here. Per-process RSS shows ~2.1 GB **per
worker** and sums to more than the machine has; that is shared pages counted
repeatedly. `MemAvailable` is the only number that means anything, and any
memory guard has to read it rather than per-process RSS.

**Results are identical to serial** — 14 of 14 candidates, exact. Every
candidate is seeded by `random_state` independently of what was scored before
it, and `imap` yields in submission order, so the results table is unchanged.

Full event, corrected grid, Postgres backend, 4 workers, `max_bands_per_load=6`:

| | |
|---|---|
| Wall clock | **1,216 s (20 min 16 s)** |
| Scored | 425 / 457 |
| Grid loads | 11 |
| Memory floor | 1.94 GB available |
| Errors | 0 |

Phase split, and the reason this is **not** a clean speedup number:

| phase | time |
|---|---|
| serial distance pass (445 lookups over the SSH tunnel) | ~570 s |
| pooled scoring, 445 candidates | ~350 s |
| grid loads + persistence | ~300 s |

Scoring ran at ~0.8 s/candidate against ~2.2 s serial, i.e. **~2.5-3x on the
work the pool actually covers**. But the run came in *slower* than the 983 s
serial baseline, because the distance pass took ~9.5 min against a much faster
tunnel earlier in the day. The tunnel was restarted mid-session; end-to-end
totals from before and after it are not comparable. Do not quote a
pool-vs-serial ratio from these two runs.

### Two memory bugs the pool exposed

Both pre-dated the pool and both bit harder with it.

* **The band index cache outlived its grid.** `_BAND_INDEX_CACHE` only evicted
  when `_band_time_index` was next called with a *different* grid, so for the
  whole of the next `load_grid` the process held the new grid **and** the
  previous one's ~1 GB of sorted copies. Fixed with `clear_band_indexes()`,
  called next to `clear_cache()` when a grid is dropped.
* **Worker private memory crept ~2.5 GB over 149 candidates.** The scorer
  allocates and frees large transient arrays per candidate and glibc does not
  return them to the OS. Fixed with `maxtasksperchild=25`; re-forking is cheap
  and re-shares the parent's grid, so the creep resets.

### The band/worker trade-off inverted

Under Parquet, memory was spent on few large loads because a load cost ~60 s
regardless. Under Postgres a 6-band load costs 4.8 s, so loads are cheap and
memory is the scarce resource — spend it on **workers**, not bands.
`max_bands_per_load=6` with 4 workers holds a 1.94 GB floor; `10` with 4
workers tripped the guard twice.

### The tunnel, not the CPU

The round trip to the database host through the SSH tunnel measures **78.7 ms**
(50 × `SELECT 1`). Against the 1,216 s run that reframes everything:

| phase | time | what it was |
|---|---|---|
| distance pass | ~570 s | 445 lookups x ~16 round trips |
| persistence | ~300 s | 457 candidates x ~8 statements |
| pooled scoring | ~350 s | actual computation |

**~71% of the run was waiting on the network**, and the part that had just been
parallelised was the smallest of the three. Both latency phases have since been
fixed.

### Persistence: ~300 s -> 0.44 s

`ScoreFactor.objects.update_or_create` per candidate is a SELECT plus an
INSERT/UPDATE inside savepoints, and the loop also issued a `.delete()` --
about 3,800 statements for 457 candidates. Replaced with two bulk deletes and
two `bulk_create(update_conflicts=True)` upserts against the
`(event_candidate, key)` unique constraint.

Measured on the real 457 rows: **0.44 s, values byte-identical, no duplicate
rows**, and the same invariant holds (a candidate carries a score or a skip
reason, never both).

### Distance pass: ~570 s -> 19.4 s

`get_candidate_distances` (in `candidate_photometry.py`) resolves a whole
event at once. The win is not a cleverer algorithm, it is not repeating work:

* every `Target` in one query instead of one each,
* every "Host Galaxies" `TargetExtra` in one query,
* **the event's localization once**, where `_distance_at_healpix` previously
  re-queried `NonLocalizedEvent`, re-queried *every* `EventLocalization` of the
  event and re-sorted them, once per candidate.

Crucially this is a **refactor, not a reimplementation**:
`get_eventcandidate_default_distance` gained optional `target` / `host_json` /
`localization` keywords, so there is still exactly one copy of the fallback
logic. Omit them and it queries as before.

Validated on all 457 candidates of `S251112cm`: **457/457 identical**,
including NaN-for-NaN. 19.4 s batched against 109.0 s looped in the same
process -- and 109 s is itself a warm-cache figure, since the batched pass ran
first; the same work took ~570 s cold in the full run.

> `host_json=None` means "not prefetched" and `""` means "this candidate has no
> host row". Collapsing the two would send prefetched-but-empty candidates back
> to the database and quietly undo the saving.

### Diagnostic: n_kde_sim 10,000 vs 5,000 -- not recommended

Fixed 40-candidate subset, 4 workers, everything else equal:

| | 10,000 | 5,000 |
|---|---|---|
| scoring | 90.6 s | 73.4 s (-19%) |
| wall | 259.8 s | 234.6 s (-9.7%) |

**-19%, not the ~50% predicted.** That prediction came from the old profile
where `kde.resample` dominated; the searchsorted rewrite already removed most
of it, so halving the draws now buys proportionally less.

The cost is real: of 37 scored, 33 were unchanged (almost all ABC-penalised
zeros), and **every candidate with a non-trivial score moved by 15-37% in
relative terms** -- `AT2025adhh` 0.3200 -> 0.2716, which is ~3.7x the ±0.013
run-to-run MC spread at 10,000. Top-10 set and order survived on this subset.

6.6% of wall clock for that much movement on exactly the candidates a vetter
reads is a bad trade. Keep 10,000.

### Diagnostic: BLAS thread pinning -- no effect

`OMP_NUM_THREADS=1` / `OPENBLAS_NUM_THREADS=1` / `MKL_NUM_THREADS=1`, on the
theory that 4 workers x 8 BLAS threads were oversubscribing 8 cores.

Microbenchmark of the hot call, `gaussian_kde.resample(10000)`, minimum of 40
reps, two independent trials:

```
default   min 1.73 ms / 1.70 ms
pinned    min 1.71 ms / 1.70 ms
```

**No difference.** The premise was wrong: a 1-D `gaussian_kde` does no
meaningful BLAS work -- a 1x1 Cholesky and RNG -- so there was never any thread
contention to remove. Scores are identical either way (40/40), so it is safe,
just pointless.

### Measured: the optimised full run

`S251112cm`, all of the above, 6 workers, `max_bands_per_load=6`, corrected
10-day grid pinned via `grid_path`, warmed tunnel:

| phase | first run of the day | optimised |
|---|---|---|
| distance pass | ~570 s | **10.1 s** |
| grid loads (11) | ~660 s | **45.4 s** |
| scoring, 445 candidates | ~1,100 s | **206.2 s** |
| persistence | ~300 s | **1.2 s** |
| **total** | **1,746 s** | **272.9 s** |

**6.4x, and all 457 rows byte-identical** to the previous run -- verified
against a snapshot, values and keys both.

Two things about the headline. The often-quoted "~2 hours" was never a fair
baseline: it was a stopped run at `max_bands_per_load=3`, a configuration that
re-read the parquet 125 times instead of 11. And the 272.9 s run used 6 workers
rather than the default 4, plus a pinned `grid_path` that skips per-candidate
grid selection.

The striking part is how little of the original time was ever computation. The
Monte-Carlo scoring is ~206 s and always was; the rest was re-reading a file
that could not be filtered, ~7,000 network round trips, and seven idle cores.

> Wall-clock on this machine drifts by ±40% between identical runs -- the same
> configuration measured 272.9 s and later 396.8 s, the difference being the
> grid database's page cache. Compare phases within a run, or paired runs
> back to back; never two totals from different hours.

### The 30-day rung: two different questions

A second rung was generated spanning 0-30 d at the same 259 Mpc and the same
10,000 x 38 x 1,000 shape -- so 0.03 d per epoch instead of 0.01 d.

**As a drop-in speed change (`dt_max=10` on both), it is not worth it.**
136.0 s against 272.9 s, but the entire saving is in scoring (67.6 s vs
206.2 s) because the 0-10 d window holds ~333 epochs instead of 1,000. Grid
loads are unchanged, since the `substring` pushdown already trims to the
observed window either way. Scores move: 363 of 425 identical, but
`AT2025aebp` shifted 0.5567 -> 0.3535 and the top-20 lost a member. Coarser,
not better.

**As an extended window (`dt_max=30`), it is a different proposition
entirely** -- see IMPROVEMENTS.md section 21. Runtime is essentially free:

| | 10-day grid, dt_max=10 | 30-day grid, dt_max=30 |
|---|---|---|
| wall clock | 396.8 s | 430.4 s |
| grid loads | 11 | 15 |
| observations scored | 3,867 | **8,653 (2.24x)** |
| candidates scored | 425 | **429** |

Both measured back to back, so the totals are comparable to each other but not
to the 272.9 s above (cold page cache after switching grids).

### How not to measure this

Three end-to-end attempts at the pinning question were all confounded, and the
methodology is worth recording because the same trap is waiting for the next
person.

The distance pass was used as a control -- it should be unaffected by BLAS
threads, so a change in it flags bad conditions. It flagged every time
(117.3 / 117.4 s clean, then 141.8 / 154.1 / 141.2 s), including once because
*this session* started a 457-candidate validation alongside the benchmark.

But the control was also **the wrong resource**: it measures database and
network contention, while the thing under test is CPU-bound. One run had a 20%
worse control and 9% *better* scoring. A 4-minute end-to-end run on a loaded
8-core laptop cannot resolve a ~5% CPU effect. Use a microbenchmark of the hot
call and take the **minimum** of many reps -- minima are robust to contention
in a way means and single runs are not.

### Reading the grid from `catalogs` on the production host

The 30-day rung was cloned into `catalogs` on datatrove, and the same event was
scored twice with everything but the grid store held fixed (457 candidates,
`dt_max=10`, 6 workers, 6 bands per load, the grid pinned by name). Scores were
compared afterwards: **457/457 identical**, so the clone is faithful and the
remote store is a drop-in.

| phase              | local store | `catalogs` (tunnelled) | delta  |
| ------------------ | ----------: | ---------------------: | -----: |
| distance pass      |      14.0 s |                 12.7 s |   ~0   |
| 11 grid loads      |      26.7 s |             **150.7 s** | **+124** |
| scoring (CPU)      |      68.5 s |                 77.1 s |   +8.6 |
| persistence        |       1.2 s |                  1.2 s |   ~0   |
| **wall**           | **123.3 s** |            **261.3 s** | **2.1x** |

Scoring is the control: it is pure CPU and lands within a few percent either
way, so the gap is entirely transfer. The tunnel moves grid bytes at ~16 MB/s
against ~100 MB/s from the local store once its page cache is warm.

**Do not size this from a single cold band read.** Doing exactly that gave
7.4 MB/s tunnelled against 9.7 MB/s local -- a 1.3x penalty -- and produced an
estimate of "co-location saves ~15 s". The real figure is 124 s. A cold read is
dominated by detoasting 4 KB float blocks, which happens on the server and hides
the network; the second read of the same band does not.

So co-location on the database host is worth roughly half of this run, not a
rounding error. What it cannot help is the CPU: 77 s of scoring stays 77 s.

### Two per-candidate round trips that hid behind a pinned grid

Both were invisible until the store moved off the local socket, and both were
the same shape -- a query re-answered once per candidate whose answer cannot
change during a run.

* `grid_for_distance` opens by calling `available_grids`, which was uncached.
  Against `catalogs` the inventory query took 3.15 s, so selecting a grid for
  457 candidates cost ~24 minutes -- to pick the only grid in the store. Most of
  that 3.15 s was `SUM(pg_column_size(absmag))` walking 380,000 TOAST pointers
  to fill a `size_mb` column nothing but display reads; it is now computed as
  `n_lightcurves * n_time * 4`, which for this layout is the same number.
* `resolve_grid` turns a pinned grid name into a `GridRef` by fetching the axis
  row, once per candidate: 445 round trips at 78.8 ms = 34 s, a quarter of the
  run. `grid_db.grid_axis` is now memoised, and the distance pass fell from
  48.1 s to 12.7 s -- level with the local store, which is what it should have
  been all along, since both configurations query the same `trove_test` through
  the same tunnel.

The second one is the useful lesson: **the distance pass was 3.4x slower with no
plausible mechanism**, because the grid store is not supposed to be involved in
it at all. An unexplained gap in a phase that should not have moved is a bug,
not noise.

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
