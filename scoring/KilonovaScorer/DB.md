# KilonovaSCORER — Postgres vs Parquet for simulation grids

Whether the simulation grids should live in the database or stay as Parquet
files on disk, measured rather than argued. Covers on-disk size, ingest cost,
load wall-clock, **peak memory**, and bytes on the wire for a remote server.

Measured on the development machine: WSL2, 8.7 GB RAM (~6.5 GB usable), 7
cores. The benchmark database is a throwaway **Postgres 16.14** installed in a
scratch directory with its own data dir on port 55432, `shared_buffers=1GB`,
client on the same host over a Unix socket. Production is **Postgres 14.23**.
The production database was never written to.

Where a figure is projected rather than measured it says so.

---

## 0. Verdict

**Move the grids into Postgres**, stored one row per *lightcurve* with the
magnitudes as a `bytea`, read back through binary `COPY`. Against the Parquet
file as it exists today this is:

| | Parquet (today's file) | Postgres (row per lightcurve) |
|---|---|---|
| Per rung on disk | 3.6 GB | **2.1 GB** (projected from 3 measured bands) |
| One-band load | 8.5 s | **3.05 s** |
| One-band peak RSS | 2,840 MB | **847 MB** |
| Three-band load | **8.3 s** | 11.7 s |
| Three-band peak RSS | 2,846 MB | **1,825 MB** |

The memory column is the one that decides it. A 2.8 GB spike is ~43% of usable
RAM on this machine, and the WSL OOM killer takes the whole VM — editor
included — down with the offending process. Postgres never materialises what it
does not return.

The one thing that must not be done is the obvious thing: **a row per
observation**. That schema is what makes the database look impossible (25 GB
per rung, 4.5 s reads), and §2 is a record of why.

---

## 1. The structural finding

Everything else follows from this. A grid is generated as
`TIME = np.linspace(0, 10, 1000)` for every draw, so — verified against the
real 259 Mpc rung, not assumed:

```
lightcurves: 3800   rows per lightcurve: [1000]
first axis: n=1000  min=0.0000 max=10.0000  uniform=True
every lightcurve shares that same axis: True
distinct time values in whole grid: 1000
```

A rung is therefore not relational data at all. It is a dense 3-D array

```
(10,000 samples) x (38 bands) x (1,000 epochs)  of float32 magnitudes
```

with 380,000,000 useful numbers in it and nothing else. In the current Parquet
layout — and in any row-per-observation table — `sample_id` is stored 1,000
times per lightcurve, `band` 10,000,000 times per band, and `time` cycles
through the same 1,000 values 380,000 times. That is ~11 bytes of redundant key
per 8 bytes of payload.

Columnar compression hides this in Parquet (RLE and dictionary encoding crush
the three key columns to nearly nothing). Postgres has no such mechanism, which
is why the naive translation is a catastrophe and the array translation is not.

---

## 2. Round 1 — row per observation, and why it failed

The straightforward relational schema, 380M rows per rung:

```sql
CREATE TABLE grid_pg (
    sample_id          integer,   -- repeats 1000x, once per epoch
    band_id            smallint,  -- repeats 10,000,000x per band
    "time"             real,      -- 1000 distinct values in the whole grid
    absolute_magnitude real       -- the only column carrying information
);
CREATE INDEX grid_pg_bt ON grid_pg (band_id, "time");
CLUSTER grid_pg USING grid_pg_bt;
```

Measured on a **1% grid** (the first 100 `sample_id`s carved out of the real
259 Mpc rung: 3.8M rows, 38 bands, 38.6 MB as Parquet). Multiply by 100 for a
rung.

### Storage

| Layout | Bytes/row | 1% grid | Projected per rung |
|---|---|---|---|
| Actual payload (int4+int2+real+real) | 14 | 53 MB | 5.3 GB |
| Parquet, narrowed + sorted | **2.8** | **10.8 MB** | **1.1 GB** |
| Parquet, 4 scoring columns, zstd | 5.1 | 19.5 MB | 2.0 GB |
| Parquet, 14 columns, zstd (as generated) | 10.2 | 38.6 MB | 3.9 GB |
| PG heap only | 44 | 168.6 MB | 17 GB |
| PG heap + btree(band_id, time) | 67 | 253.8 MB | **25 GB** |
| PG naive: 14 cols, float8/text | 142 | 540.9 MB | 54 GB |

Two multipliers stack. Postgres adds a **23-byte tuple header** plus alignment
padding and a 4-byte line pointer to every row (14 → 44 bytes), and it **does
not compress columns** — TOAST only engages above ~2 KB and a 4-byte `real`
never qualifies, so all that redundancy is stored in full 380M times over.

Ingest was 13.0 s per 1% (`COPY` 4.4 s + index build and `CLUSTER` 8.6 s), or
~22 min per rung against ~50 s to write the Parquet file.

### Reads (1% grid, best of 3; cold = page cache evicted, server restarted)

| Query | Parquet (narrow) | PG `COPY` | PG `read_sql` |
|---|---|---|---|
| all 38 bands, cold | **0.104 s** | 4.55 s | 7.35 s |
| all 38 bands, warm | **0.056 s** | 4.54 s | 7.20 s |
| 1 band, cold | **0.146 s** | 0.261 s | 0.301 s |
| 3 bands + t≤4 d, cold | **0.084 s** | 0.295 s | 0.571 s |
| 3 bands + t≤4 d, warm | **0.076 s** | 0.147 s | 0.207 s |

Cold and warm are nearly identical for Postgres, which is the diagnosis: the
bulk read was never I/O-bound, it was CPU-bound in tuple materialisation.
`EXPLAIN (ANALYZE, BUFFERS)` on the full scan with everything in shared buffers:

```
Seq Scan on grid_pg  (actual time=0.040..689.537 rows=3479555 loops=1)
  Buffers: shared hit=20541
Execution Time: 859.546 ms
```

859 ms server-side alone, against 56 ms for the whole Parquet read. Per row,
Postgres does a visibility check, deforms the tuple attribute by attribute,
evaluates the filter, then frames the survivor into its own protocol message —
work proportional to *rows*, where Parquet's is proportional to *bytes*. No
index, disk or cache tuning closes that gap.

---

## 3. Round 2 — array layouts

Five ways to store the same magnitudes, all measured on the same 1% grid. The
shared time axis lives once in its own one-row table for B/C/D.

| | Table shape | Rows (1% / per rung) |
|---|---|---|
| **A** | `(sample_id, band_id, time, absolute_magnitude)` | 3.8M / 380M |
| **B** | `(band_id, sample_id, absmag real[])` — PG array of 1000 | 3,800 / 380k |
| **C** | `(band_id, sample_id, absmag bytea)` — float32 LE, 4 KB | 3,800 / 380k |
| **D** | `(band_id, band, n_samples, n_time, sample_ids, absmag bytea)` — one (sample × time) block per band | 38 / 38 |
| **E** | `(name, data bytea)` — the Parquet file itself as one blob | 1 / 1 |

### Storage and ingest (1% grid)

| Layout | Size | Ingest |
|---|---|---|
| Parquet narrow (reference file) | 10.8 MB | 0.48 s |
| E parquet-as-blob | 11.3 MB | 0.63 s |
| B `real[]` per lightcurve | 11.7 MB | 1.42 s |
| D `bytea` per band | 15.8 MB | 0.38 s |
| C `bytea` per lightcurve | 20.9 MB | 0.35 s |
| *(A, for comparison)* | *253.8 MB* | *13.0 s* |

Sizes include the primary-key index. All used `pglz`: the conda-forge Postgres
16 build lacks LZ4 (`compression method lz4 not supported`). Production's
Debian build almost certainly has it, which should shrink C and D further and
speed detoasting.

### Reads (1% grid, cold / warm, seconds)

| Layout | all 38 bands | 1 band | 3 bands + t≤4 d |
|---|---|---|---|
| Parquet narrow file | **0.143 / 0.062** | 0.070 / 0.046 | **0.066 / 0.035** |
| C `bytea` per lightcurve | 1.128 / 1.056 | **0.039 / 0.025** | 0.077 / 0.050 |
| D `bytea` per band | 1.537 / 1.453 | 0.057 / 0.030 | 0.124 / 0.072 |
| B `real[]` | 2.342 / 2.037 | 0.068 / 0.045 | 0.167 / 0.122 |
| E parquet-as-blob | 0.305 / 0.190 | 0.253 / 0.208 | 0.202 / 0.174 |

**C wins the queries scoring actually issues.** Against layout A the same
database, same data, same query goes from 253.8 MB to 20.9 MB and from 4.5 s to
0.039 s. Collapsing 1,000 magnitudes into one row removes 1,000 tuple headers,
1,000 visibility checks and 1,000 protocol messages per lightcurve; the three
key columns vanish because position in the array *is* the epoch; and at 4 KB
the value finally clears the TOAST threshold and gets compressed.

B is slower than C because psycopg2 parses PG's array text representation
element by element. D transfers less but forces whole-band granularity. E is a
reasonable middle road — the DB as the distribution mechanism, Parquet as the
format — but no filter can be pushed into a blob, so every query pays for the
whole file.

---

## 4. Full scale — layout C against the real rung

Three real bands (`atlaso`, `ztfg`, `ztfr`) ingested at the rung's true 10,000
samples, so both sides of the comparison are measured on identical data rather
than extrapolated.

Ingest, one band at a time (a single band is 10M rows and peaks near 2.7 GB in
pyarrow; two at once does not fit):

```
atlaso   10,000 lightcurves x 1000 epochs  read 15.1s  COPY 1.9s  peak RSS 2558 MB
ztfg     10,000 lightcurves x 1000 epochs  read 20.6s  COPY 2.1s  peak RSS 2733 MB
ztfr     10,000 lightcurves x 1000 epochs  read 16.0s  COPY 1.0s  peak RSS 2733 MB

lc_full: 30,000 rows, 165 MB for 3 bands  ->  2,091 MB projected for all 38
ingest total 57 s for 3 bands             ->  ~12 min projected per rung
```

### The headline table

Each read returns the same four-column frame the scorer consumes, filtered
identically (`time > 0`, `absolute_magnitude < 0`). Peak RSS measured as
`ru_maxrss` in a fresh interpreter per case, so transient buffers freed before
the frame is returned are still counted. Baseline (interpreter + imports) is
~110 MB in every case.

| Query | Backend | Load | Peak RSS | Frame | Wire |
|---|---|---|---|---|---|
| **1 band** `ztfr`, 9,384,824 rows | Parquet, file as-is | 8.2–9.2 s | 2,697–2,840 MB | 339 MB | — |
| | PG layout C, text | 3.22 s | 840 MB | 197 MB | 80.3 MB |
| | PG layout C, `COPY BINARY` | **3.05 s** | **847 MB** | 197 MB | **40.3 MB** |
| **3 bands** t≤4 d, 11,871,302 rows | Parquet, file as-is | **8.3–8.5 s** | 2,846 MB | 437 MB | — |
| | PG layout C, text | 19.9 s | 1,991 MB | 249 MB | 240.9 MB |
| | PG layout C, `COPY BINARY` | 11.7 s | **1,825 MB** | 249 MB | 120.8 MB |

The two backends scale differently, and that is the whole result:

* **Parquet's cost is nearly fixed** — ~8.4 s and ~2.8 GB whether you ask for
  one band or three, because the band filter has to touch the whole 3.6 GB file
  either way. The rung has 400 row groups of ~17 MB and *every* one contains
  *every* band, so predicate pushdown cannot prune them.
* **Postgres scales with the ask** — ~10,000 rows, ~3.5 s and ~40 MB per band.

Crossover sits just under three bands. Below it Postgres wins on both axes;
above it Postgres still wins on memory.

### Memory

Of Parquet's 2.84 GB peak, **2.39 GB is transient** — row groups decompressed
in full and then mostly discarded by the filter, to deliver a 339 MB frame.
Postgres finds 10,000 rows by index, detoasts 40 MB of magnitudes, and stops.

Note the frames are not dtype-identical: Parquet returns the source file's
`float64`/`int64`/string dtypes (339 MB) while layout C returns `float32`
(197 MB), because float32 is what is stored. Narrowing the Parquet file would
close part of that gap — see §7.

> First measurement of `real_pq_1band` was 21.9 s; it settles at 8.2–9.2 s on
> repeat. The first figure was a cold-disk outlier, not the steady state.

---

## 5. Remote access

Everything above ran over a local Unix socket, where transfer is free. That is
not the situation from a development machine: today Postgres is reached over an
SSH tunnel to `datatrove.as.arizona.edu`, and per TIMINGS.md several existing
costs are tunnel latency rather than computation.

Bytes were counted through a relay proxy and remote wall-clock modelled as
`t_local + bytes/bandwidth + roundtrips × RTT`. All five round trips are
connection setup; the payload streams. The model ignores TCP slow-start, so it
flatters large transfers — i.e. it understates rather than overstates the case
for Postgres.

| Path (3 bands, t≤4 d) | Wire | LAN 1 Gbps/1 ms | VPN 100 Mbps/20 ms | Home 50 Mbps/60 ms |
|---|---|---|---|---|
| PG layout C, text | 240.9 MB | 21.8 s | 39.3 s | 58.7 s |
| PG layout C, `COPY BINARY` | 120.8 MB | 12.7 s | 21.5 s | 31.3 s |
| Parquet, file already local | 0 | **8.3 s** | **8.3 s** | **8.3 s** |
| *One-off: sync today's rung* | *3.6 GB* | *29 s* | *4.8 min* | *9.6 min* |
| *One-off: sync a narrowed rung* | *1.1 GB* | *8.8 s* | *1.5 min* | *2.9 min* |

On a VPN link the two strategies break even at **about seven scoring loads**
(88 s of sync against 13 s saved per load). TIMINGS.md records ~40 grid loads
in a full event, so for a *remote client* running whole events, a locally
synced Parquet file is still cheaper.

**This only matters if the scoring worker is remote from the database.**
`kilonova_scoring` is a `django_tasks` queue worker; if it runs on the
datatrove host there is no link and the local-socket numbers in §4 stand
unmodified. That is the question that actually settles the design — worth
confirming before committing.

### Free win: binary transport

psycopg2 returns `bytea` as hex text, two wire bytes per stored byte.
`COPY … TO STDOUT (FORMAT binary)` halves the transfer **and** cut the
three-band read from 19.9 s to 11.7 s (−41%) even on a local socket, where
transfer is supposedly free — the saving is hex decoding, not bandwidth.

---

## 6. Recommended schema

```sql
-- one row per lightcurve; position in the array IS the epoch
CREATE TABLE kn_grid_lightcurve (
    grid       text     NOT NULL,   -- e.g. 'two_component_259Mpc'
    band       text     NOT NULL,
    sample_id  integer  NOT NULL,
    absmag     bytea    NOT NULL,   -- 1000 x float32 LE = 4 KB
    PRIMARY KEY (grid, band, sample_id)
);
ALTER TABLE kn_grid_lightcurve ALTER COLUMN absmag SET COMPRESSION lz4;

-- the axis every lightcurve shares, stored exactly once per grid
CREATE TABLE kn_grid_axis (
    grid         text PRIMARY KEY,
    time_axis    bytea NOT NULL,    -- 1000 x float32 LE
    distance_mpc double precision NOT NULL,
    n_samples    integer NOT NULL,
    n_time       integer NOT NULL
);
```

`distance_mpc` on the axis table replaces the filename-parsing and
`redshift`-sampling that `grids.grid_for_distance()` does today — the ladder
becomes `SELECT grid FROM kn_grid_axis ORDER BY abs(distance_mpc - %s) LIMIT 1`.

Read path, per band set:

```sql
COPY (SELECT band, sample_id, absmag
        FROM kn_grid_lightcurve
       WHERE grid = %s AND band = ANY(%s)
       ORDER BY band, sample_id) TO STDOUT (FORMAT binary)
```

then `np.frombuffer(blob, np.float32)` per row, `np.tile` the axis, and apply
the `time`/`max_abs_mag` cuts client-side exactly as `load_grid` does now.

Three conditions on the migration:

1. **Row per lightcurve, never row per observation.** §2 is the record of what
   the latter costs.
2. **Binary `COPY` out, LZ4 on the column.** Both are free and both are large.
3. **Keep the in-process grid cache in `grids.py`.** Nothing here changes the
   fact that a run wants the same band set repeatedly; the cache is what turns
   a 3–12 s load into a once-per-worker cost, and it is what makes the remote
   case tolerable at all. The existing `GRID_CACHE_BYTES` budget and LRU
   eviction carry over unchanged.

---

## 7. What this does not settle

* **Parquet is not at its ceiling either.** Narrowing dtypes and sorting by
  `(band, time)` made the 1% file 1.8× smaller (19.5 → 10.8 MB) and its
  band-filtered read 2.6× faster (0.121 → 0.047 s). Projected to a rung that is
  ~1.1 GB instead of 3.6 GB. The §4 comparison is against *today's* file, which
  is the honest baseline for a decision made today, but a fair fight would
  restructure both. The pruning advantage should grow at full scale (a sorted
  file touches ~11 row groups per band instead of 400) — **not verified at full
  scale**; rewriting the rung sorted needs ~5 GB in one pass, which is at the
  WSL ceiling, so it wants a band-partitioned streaming rewrite.
* **Postgres storage for all 38 bands is projected**, linearly from 165 MB
  measured on 3 bands. Layouts B/D/E and the all-38-band read were measured at
  1% only.
* **`pglz`, not LZ4** — the test server could not do LZ4. Production numbers
  should be slightly better than these, not worse.
* **The network figures are a model**, not a throttled measurement: real byte
  counts and round trips, arithmetic bandwidth and latency.
* **Peak RSS on the Postgres path is not optimised.** 1,825 MB for three bands
  is dominated by holding the raw response and the frame simultaneously;
  streaming into a preallocated array would cut it further. A server-side
  cursor was tried and did not help (430.8 MB vs 425.6 MB at 1%) because
  psycopg2 still accumulates each chunk.

---

## 8. Reproducing

Scripts live in the session scratchpad and are **not** checked in; the schema in
§6 and the `EXPLAIN` output in §2 are enough to rebuild the important parts.

| Script | What it does |
|---|---|
| `make_mini.py` | carves the first 100 `sample_id`s out of the real rung into a 1% grid |
| `bench.py` | round 1: row-per-observation storage, ingest, cold/warm reads |
| `bench2.py` | round 2: layouts B–E at 1% |
| `mem_case.py` | one load path per fresh interpreter, reports `ru_maxrss` |
| `net_case.py` / `net_proxy.py` | counting TCP relay; wire bytes and round trips |
| `full_scale.py` | ingests 3 real bands at 10,000 samples into layout C |
| `full_read.py` | full-scale reads, text vs `COPY BINARY`, memory and wire |

Cold measurements evict the page cache with `posix_fadvise(POSIX_FADV_DONTNEED)`
— no root needed — and restart Postgres to empty `shared_buffers`.

---

## 9. Implementation

Built 2026-08-11 against this document. Layout C, binary `COPY`, in-process
cache retained — the three conditions in §6.

| File | What it holds |
|---|---|
| `KilonovaScorer/grid_db.py` | the backend: DSN connection, `ensure_schema`, binary `COPY` in and out, `ingest_parquet`, `verify_band` |
| `KilonovaScorer/grids.py` | `GridRef`, `resolve_grid`, `grid_name`, and `TROVE_GRID_BACKEND` dispatch inside `available_grids` / `grid_for_distance` / `load_grid` / `grid_distance_mpc` |
| `scoring/management/commands/ingest_kn_grid.py` | `ingest_kn_grid <parquet>`, `--list`, `--verify BAND`, `--drop GRID` |
| `scoring/kilonova_scoring.py` | the four call sites that assumed a filesystem `Path` now go through `resolve_grid()` / `grid_name()` |

A grid is identified by a `Path` under Parquet and a `GridRef` under Postgres.
Both have a `.name` and both hash, which is all the scoring loop needs — it
buckets candidates into a dict keyed on their grid and sorts those keys by
distance. `load_grid` returns the same columns and dtypes either way, so
nothing downstream can tell which backend served it.

### Configuration

```bash
export TROVE_GRID_DSN='postgresql://bench@127.0.0.1:55432/gridbench'
export TROVE_GRID_BACKEND=postgres     # default is parquet
```

**Parquet stays the default.** A grid store is empty until something ingests a
rung, so a deployment that flipped the backend before loading data would lose
scoring outright rather than degrade.

### Deliberately not done

* **No Django model, no migration.** The store is a standalone database reached
  by DSN, not an entry in `DATABASES`. A rung is ~2 GB of pure simulation
  output: it is not TROVE data, it has no business in TROVE's migration
  history, and keeping it separate means the store can be dropped and rebuilt
  without touching anything else. Moving it into TROVE's database later is a
  `CREATE TABLE` — the DDL is `grid_db.SCHEMA_SQL`.
* **The schema is raw SQL**, because the ORM cannot express a composite primary
  key and would silently produce a different table from the one measured here.

### Measured on the implemented path

Three real bands at the rung's full 10,000 samples, promoted into the schema
from the `lc_full` benchmark table. Local socket, warm `shared_buffers`, the
store at 160 MB.

| Query | Rows | Load | Peak RSS | Frame |
|---|---|---|---|---|
| 1 band `ztfr`, full axis | 9,384,824 | **0.97 s** | **417 MB** | 131 MB |
| 3 bands, t≤4 d | 11,871,302 | **1.20 s** | **593 MB** | 166 MB |

Both row counts are identical to the Parquet reads in §4 — the same 9,384,824
and 11,871,302 — which is the check that matters: the two backends return the
same rows after the same `time > 0` and `M < 0` cuts.

Faster and leaner than §4's layout C figures (3.05 s / 847 MB and 11.7 s /
1,825 MB) for two reasons, neither of them a contradiction of §4:

* **Narrower frame.** `sample_id` is built as int32 and `band` as a categorical
  rather than coming back as int64 and text, so the three-band frame is 166 MB
  where §4 measured 249 MB.
* **The time window is pushed into the query** as `substring(absmag ...)`. The
  epochs are ordered, so a time cut is a byte range: a load needing 0–4 d of a
  0–10 d grid transfers 40% of the bytes instead of all of them and filtering
  afterwards. §4 measured the filter applied client-side.

> Both figures are warm. §4's cold/warm columns showed the difference is small
> for Postgres — the bulk read is CPU-bound in tuple materialisation, not I/O —
> but these specific numbers are not cold measurements.

### What is still open

* **Only 3 of 38 bands have ever been in the store.** The 2.1 GB and ~12 min
  per rung in §4 remain projections; no full rung has been ingested.
* **`ingest_parquet` scans the whole file once per band**, because every row
  group contains every band and the filter prunes nothing. That is the same
  property that makes Parquet's *read* cost fixed, and it makes a 38-band
  ingest 38 full scans. Band-partitioning the Parquet file at generation time
  (§7) would fix the ingest and the Parquet read together.
* **The remote question from §5 is still unanswered.** These numbers are a
  local socket. If the `kilonova_scoring` worker runs on the datatrove host
  they stand as they are; if it runs remotely, §5's wire costs apply.

---

*Benchmarked 2026-08-11 against
`simulations_two_component_kilonova_model_259Mpc.parquet` (380,000,000 rows,
3.6 GB).*
