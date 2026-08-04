# Scoring GW candidates with KilonovaSCORER

Two modules, both importable anywhere Django is configured (views, management
commands, tasks, notebooks, `manage.py shell`):

| module | job |
|---|---|
| `scoring.candidate_photometry` | pull every photometric point TROVE holds for the candidates of a GW event |
| `scoring.kilonova_scoring` | convert that to absolute magnitudes, match it against a simulation grid, return a score |

The scorer itself is the vendored `scoring.KilonovaScorer` package
([upstream](https://github.com/phelipedarc/KilonovaSCORER)).

## TL;DR

```python
from scoring.kilonova_scoring import load_simulation_grid, score_event, score_candidate

grid = load_simulation_grid("path/to/simulations_..._259Mpc.csv")

# rank every candidate of an event
scores = score_event("S251112cm", grid=grid)
scores.head(10)          # score, score_err, n_obs_scored, dist_mpc, skip_reason, ...

# one candidate, keeping the diagnostics
res = score_candidate("S251112cm", "SN2025adgq", grid=grid)
res.score, res.score_err   # cumulative P_tail_KNe in [0, 1]; higher = more KN-like
res.per_observation        # per-epoch P_tail_KNe / P_near_KNe / ABC survivors
res.binned                 # per-time-bin running score
res.survivors              # ABC survivor counts per band
```

Photometry alone, without scoring:

```python
from scoring.candidate_photometry import get_event_photometry

df = get_event_photometry("S251112cm", dt_min=0, dt_max=30, include_limits=False)
```

**Prerequisites:** a working `trove_tom/settings_local.py` pointed at a
reachable TROVE database, and a simulation grid (see below). Nothing here
writes to the database unless you pass `refresh=True`.

> Photometry extraction verified against `trove_main` on 2026-08-03: event
> `S251112cm`, 428 candidates, 219k photometry points. **End-to-end scoring is
> not yet verified** -- see "Status" at the bottom.

## The simulation grid

`kilonovascorer_v3` scores an observation by asking where it falls in the
distribution of *simulated* kilonova absolute magnitudes at the same time and
band. That grid is a separate artifact this repo does not ship.

Generate one with `scoring.KilonovaScorer.simulation.simulate_kilonova`, which
pulls in redback / bilby / lal. That stack is deliberately **not** imported by
`scoring/KilonovaScorer/__init__.py`, so importing the scorer -- or anything in
`scoring.kilonova_scoring` -- does not drag it in. Import it explicitly, only
when generating:

```python
import scoring.KilonovaScorer.simulation as sim
sim.simulate_kilonova(N_SIM=1000, MODEL_NAME="two_component_kilonova_model")
```

### The `kn-sim` environment

redback pins numpy / pandas / astropy tightly enough to disturb the Django
stack, so grid generation lives in its **own conda environment** and the worker
shells out to it rather than importing anything. `async_generate_grid_rung`
reads that interpreter's path from the `KN_SIM_PYTHON` setting — declare it in
`settings_local.py`; leave it blank to disable on-demand rung generation.

```bash
conda create -n kn-sim python=3.11
conda activate kn-sim
pip install redback lalsuite pyarrow tqdm
```

`lalsuite` is **not** a base redback dependency — it sits behind redback's `all`
extra — but `simulation.py` does `import lal` directly, so installing redback
alone leaves grid generation failing at import. `pyarrow` (the grid's on-disk
format) and `tqdm` are likewise not pulled in. Everything else — bilby, sncosmo,
extinction, afterglowpy, numpy, pandas, scipy, astropy, matplotlib — arrives as
a redback dependency.

Check the environment before trusting a long run:

```bash
"$KN_SIM_PYTHON" -c "import redback, bilby, lal, pyarrow, tqdm; print('kn-sim OK')"
```

Versions the 150 / 400 / 800 Mpc ladder was generated with. A grid is only
reproducible against the stack that built it, so record these before
regenerating (see IMPROVEMENTS.md §11 — the ladder had to be rebuilt once
already because seeding was not reaching bilby's own RNG):

| package | version | | package | version |
|:--------|:--------|-|:--------|:--------|
| python | 3.11.15 | | astropy | 8.0.1 |
| redback | 1.18.0 | | numpy | 2.4.6 |
| bilby | 2.8.1 | | pandas | 3.0.5 |
| lalsuite | 7.26.15 | | scipy | 1.17.1 |
| pyarrow | 25.0.0 | | tqdm | 4.70.0 |

`load_simulation_grid()` normalizes whatever it is handed:

- maps redback band names (`lsstg`, ...) to canonical `g-band`/`r-band`/... via
  KilonovaSCORER's own `FILTER_LOOKUP`, dropping bands the scorer does not
  model (2MASS J/H/Ks, JWST F356W/F444W, ...);
- **drops `time <= 0`**, where the models are undefined and return absurd
  magnitudes (the IR grid has absolute magnitudes around **+11** at t=0);
- **drops `absolute_magnitude > 0`**, flux-underflow artifacts that appear at
  late times in some simulations. Left in, both classes of row distort the KDE
  the entire score is built on;
- reads only the four columns the scorer needs, as float32 -- a grid is easily
  several GB as CSV, and a bare `read_csv` of one is an out-of-memory risk;
- records the grid's own luminosity distance from its `redshift` column in
  `grid.attrs["distance_mpc"]`.

Because the grid is generated at one luminosity distance, use one grid per
distance bin. `select_grid_for_distance(dist_mpc)` picks the nearest from
`KILONOVA_GRID_DIR` (default `data/kilonova_grids/`, gitignored), reading the
distance from either a `..._259Mpc.csv` filename or the file's own `redshift`
column.

## Scoring API

`score_candidate(event_id, target_name, grid=..., ...)` returns a
`KilonovaScore` dataclass:

| field | meaning |
|---|---|
| `score`, `score_err` | cumulative P_tail_KNe after the last time bin, and its error. `None` if unscoreable |
| `scored` | whether a finite score came out |
| `skip_reason` | why not, when it did not: no photometry, no usable distance, too sparse, no epoch inside the grid's time coverage, ... |
| `dist_mpc`, `dist_err_mpc` | distance used for the absolute magnitudes |
| `n_obs_supplied` / `n_obs_scored` | epochs handed to the scorer vs epochs it actually scored (they differ -- the scorer silently skips epochs whose time bin holds too few simulations) |
| `bands`, `final_survivors` | bands scored, and ABC survivors per band |
| `per_observation`, `binned`, `survivors` | the full frames, when `keep_frames=True` |

`score_event(event_id, grid=...)` scores every candidate and returns a table
sorted by score. Candidates that cannot be scored still appear, with their
`skip_reason` -- nothing disappears silently. Photometry for all candidates is
fetched in **one** query and split per candidate, so scoring a whole event
costs the same two database round-trips as extracting the photometry.

---

## 1. Where the data lives

There is no "photometry for a GW event" table. You walk four models:

```
NonLocalizedEvent            the superevent, e.g. S250818k   (event_id)
  │
  ├── EventSequence          one row per alert (preliminary → initial → update…)
  │     └── details["time"]  ← the GW trigger time, ISO-8601
  │
  └── EventCandidate         join table: this Target is a candidate for this event
        └── Target           the transient (trove_targets.models.Target)
              └── ReducedDatum   one row per photometric point (data_type="photometry")
```

| Model | App | Notes |
|---|---|---|
| `NonLocalizedEvent` | `tom_nonlocalizedevents` | Look up by `event_id` (the GraceDB name), **not** by primary key. |
| `EventSequence` | `tom_nonlocalizedevents` | Ordered by `sequence_id`. `details` is the raw alert payload; every TROVE ingestor writes the trigger time to `details["time"]`. |
| `EventCandidate` | `tom_nonlocalizedevents` | FK to `Target` and `NonLocalizedEvent`. Has `viable` / `viability_reason` — the human "ruled out" flag. |
| `Target` | `trove_targets` | Subclass of the TOM `BaseTarget` adding `redshift`, `mwebv`, `healpix`, `classification`. |
| `ReducedDatum` | `tom_dataproducts` | `target`, `timestamp`, `source_name`, `data_type`, and the `value` JSON blob. |

### How candidates get attached to an event

Two paths, both producing `EventCandidate` rows:

- **Automatic** — `custom_code.healpix_utils.create_candidates_from_targets()`
  creates a candidate for every target inside the localization's credible
  region (driven by `manage.py associate_targets_with_nle`, and by the
  alert-stream handlers as new targets arrive).
- **Manual** — the "create candidate" buttons in the web UI
  (`custom_code.views.EventCandidateCreateView`).

So `EventCandidate.objects.filter(nonlocalizedevent=nle)` **is** the candidate
list you see on the event page. The script defaults to *all* candidates,
including ones marked non-viable, since for training/validating a classifier
you usually want the rejects too. Pass `viable_only=True` to match the UI's
default view.

### The photometry itself

Photometry is `ReducedDatum` rows with `data_type="photometry"`, linked to the
`Target`, **not** to the event. That's why there is no direct event→photometry
query: you go through the candidates.

`source_name` records who delivered the point — `ZTF`, `ATLAS`, `TNS`,
`SAGUARO pipeline`, `MARS`, … `value` is a **JSON blob with no schema
enforcement**. In practice:

```jsonc
{"magnitude": 19.2, "error": 0.11, "filter": "g-ZTF", "telescope": "P48"}  // detection
{"limit": 20.5, "filter": "o"}                                            // non-detection
```

Gotchas the script handles, all of which are real in the production database:

- `error` is sometimes `0` or missing when a broker doesn't report one. Left
  alone this breaks any fit that weights by uncertainty, so it's replaced with
  `2.5 / (3·ln10) ≈ 0.362` mag (a 3σ detection), the same substitution
  `scoring/vet_phot.py` makes.
- Some rows carry a filter but neither `magnitude` nor `limit`, or vice versa.
  These aren't light-curve points and are skipped.
- A few older ingestion paths stored `value` as a JSON *string* rather than an
  object, so it's decoded defensively.
- Filter names carry a survey suffix (`g-ZTF`, `r.ATLAS`, `V `). These are
  collapsed to the bare band with `scoring.vet_phot.standardize_filter_names`,
  the same rule the vetting code uses, so filters are comparable across
  surveys. **Case is significant**: `g` is SDSS g, `G` is Gaia G.
- The same physical filter appears under several spellings. ATLAS cyan/orange
  arrive as `c`/`o` from the ATLAS forced-photometry path but as
  `cyan`/`orange`/`cyan-ATLAS`/`orange-ATLAS` from the TNS ingestor — 1.6k
  points in `trove_main` that a naive `c`/`o`-only mapping silently discards.
- `standardize_filter_names` takes the first `-`-delimited token, which is
  wrong for BlackGem: `BG-q-BlackGem` → `BG`, the *telescope* prefix, losing
  which band it was. The `filter` column inherits this; `filter_raw` does not,
  which is why band mapping keys off `filter_raw` (see below).

## 2. What the script returns

`get_event_photometry(event_id, ...)` → one row per measurement, all candidates
stacked:

| column | meaning |
|---|---|
| `event_id`, `candidate_id`, `target_id`, `target_name` | identity/join keys |
| `ra`, `dec` | degrees |
| `mwebv` | Milky Way E(B−V) at the target, for extinction correction |
| `mjd` | observation time, MJD |
| `dt` | **days since the GW trigger** (negative = pre-merger) |
| `filter` | standardized band (`g`, `r`, `o`, `F150W`, …) |
| `filter_raw` | as stored, e.g. `g-ZTF` |
| `mag`, `magerr` | magnitude; for an upper limit `mag` is the limit and `magerr` is NaN |
| `snr` | `2.5 / ln(10) / magerr`, NaN for limits |
| `upperlimit` | True for non-detections |
| `source` | `ReducedDatum.source_name` (broker/survey) |
| `telescope` | `value["telescope"]` when reported |
| `reduceddatum_id` | primary key, to trace a row back to the DB |

Filtering arguments: `viable_only`, `target_names`, `dt_min`, `dt_max`,
`include_limits`, `snr_min`.

The whole thing is **two queries** (candidates, then one `target_id__in` query
for all photometry) rather than one per candidate, so it stays fast on events
with hundreds of candidates.

### `refresh=True` writes to the database

`refresh=True` calls `scoring.vet_phot.find_public_phot()` for each candidate
before reading. That **queries TNS and saves any new photometry**, and
**enqueues an asynchronous ATLAS forced-photometry job**. The ATLAS results
land later via the background worker, so they will *not* be in the frame you
just got back — re-run without `refresh=True` a few minutes later to pick them up.
Leave it off unless you actually want to trigger those queries.

## 3. Handoff to KilonovaSCORER

KilonovaSCORER loads observations with

```python
load_observations(file_path, merger_mjd, dist_mpc, dist_err_mpc)
```

and needs a CSV with `time` (MJD), `magnitude`, `e_magnitude`, `band`; it adds
`time_after_gw = time - merger_mjd` and Monte-Carlos `absolute_magnitude` /
`absolute_magnitude_error` from the distance and its error. Before scoring you
must also supply a `filter_mapped` column of canonical band names.

`write_scorer_inputs()` produces exactly that:

```
DIR/
  AT2025xyz.csv     time, magnitude, e_magnitude, band, filter_mapped, time_after_gw
  AT2025abc.csv
  manifest.csv      target_name, target_id, file_path, merger_mjd,
                    dist_mpc, dist_err_mpc, n_points, n_bands
```

so a full run is:

```python
import pandas as pd
manifest = pd.read_csv("kns_inputs/manifest.csv")
for row in manifest.itertuples():
    data_obs = load_observations(row.file_path, row.merger_mjd,
                                 row.dist_mpc, row.dist_err_mpc)
    ...
```

Column mapping (TROVE → KilonovaSCORER):

| TROVE | KilonovaSCORER |
|---|---|
| `mjd` | `time` |
| `mag` | `magnitude` |
| `magerr` | `e_magnitude` |
| `filter` | `band` |
| `FILTER_LOOKUP[filter]` | `filter_mapped` |
| `dt` | `time_after_gw` |
| `get_candidate_distance()` | `dist_mpc`, `dist_err_mpc` |
| `EventSequence.details["time"]` | `merger_mjd` |

Three decisions baked into the export, all overridable:

1. **Upper limits and pre-merger points are dropped** (`include_limits=False`,
   `dt_min=0`), matching what KilonovaSCORER's own JSON loader does. They are
   still available from `get_event_photometry` if you want them for plotting or
   for constraining the rise.
2. **Filters with no g/r/i/z counterpart are dropped** — u, y, J, B, JWST/HST
   and Roman filters, `Other`. KilonovaSCORER has no model for them. The count
   and band names are logged.
3. **Some filter mappings are approximations** (see the next section).

### Band mapping

`match_band()` resolves the **raw** filter string by longest `-`-delimited
prefix, so the band survives however much survey cruft is appended
(`orange-ATLAS` → `orange`) and a two-token band still beats its own first
token (`BG-q-BlackGem` → `BG-q`, not `BG`). Matching the raw string rather than
the standardized one is what keeps the BlackGem bands distinguishable.

Measured against all 219k candidate photometry points in `trove_main`:

| | dropped as unmappable |
|---|---|
| default | 3,278 / 219,198 (1.50%) |
| `map_wide_bands=True` | 61 / 219,198 (0.03%) |

Three tiers, by how defensible the assignment is:

- **Exact** — `g`, `r`, `i`, `z`, `BG-g`, `BG-i`.
- **Approximate** (on by default) — nearest SDSS neighbour by effective
  wavelength: ATLAS `c`/`cyan` → g, `o`/`orange` → r, Johnson `V` → g, Cousins
  `R`/`I`, Gaia `G` → r, Pan-STARRS `w` → r. ATLAS alone is **87%** of all
  candidate photometry and its two bands straddle SDSS bands (cyan ≈ g+r,
  orange ≈ r+i), so this is a real systematic, not a relabeling. Delete those
  entries from `FILTER_LOOKUP` for a photometrically clean sample.
- **Judgement call** (`map_wide_bands=True`, off by default) — unfiltered or very
  wide bands whose zero-point depends on each pipeline's calibration:
  `Clear` (SAGUARO) → r, GOTO `L` → g, BlackGem `BG-q` → g, ATLAS `wide` → r.
  Worth ~1.5% of points; review before trusting these in a fit.

### Distances

`get_candidate_distance()` wraps
`scoring.scoring.get_eventcandidate_default_distance()`, i.e. the same distance
TROVE's own scoring uses. It falls back in this order:

1. the target's own redshift, converted with the project cosmology;
2. the best associated host galaxy's redshift (spec-z preferred over photo-z,
   catalogs ranked, `-99`/`-999`/`-9999` sentinel values filtered out);
3. the GW skymap's distance posterior at the candidate's healpix.

Only case 3 is really "the event's distance" — for 1 and 2 the distance is the
*candidate's*, which is what you want for an absolute magnitude, but it means
`dist_mpc` is not homogeneous across the candidate list. `manifest.csv` keeps
`target_id` so you can re-derive it however you prefer.

Three things the real data does that you should know about:

- **Asymmetric errors.** The host-galaxy branch returns whatever the catalog
  JSON held, and z-independent distances often carry `[upper, lower]` as a
  two-element list. KilonovaSCORER samples a symmetric Gaussian, so the two
  sides are **averaged** (`_scalar_distance`); switch to `max` there if you
  prefer to be conservative. This is common — 8 of 428 candidates for
  `S251112cm`, and it would otherwise write a Python list into the CSV.
- **Negative distances.** A few host-catalog rows carry a negative `Dist`
  sentinel that survived the upstream z-filtering (2 of 428 for `S251112cm`).
  A distance modulus is undefined there, so these become `NaN` with a warning
  rather than silently poisoning the absolute magnitudes. **Filter on
  `dist_mpc.notna()` before scoring.**
- **Distances far outside the GW volume.** 98 of 428 candidates for
  `S251112cm` sit beyond 2000 Mpc (max ~10.5 Gpc). That is not a bug — most
  candidates are background supernovae whose own host redshift is used — but
  it means absolute magnitudes are only meaningful once you have decided the
  candidate is plausibly at the event distance.

## 4. Known limitations

- The trigger time comes from the highest-`sequence_id` alert that has a
  `details["time"]`. For a superevent whose sky position/time was revised this
  is the latest value, which is what you want; but if a retraction is the most
  recent sequence, the loop falls back to the previous one.
- No de-duplication: if the same detection reaches TROVE from two brokers (a
  TNS report and a ZTF alert of the same point, say) you get two rows. This is
  real and visible in production — e.g. `AT2024aeuy` carries `L-GOTO` and `L`
  at an identical MJD and magnitude from two different `ReducedDatum` rows.
  Group on `mjd`/`filter` if that matters for your fit.
- Light curves are sparse. For `S251112cm`, the median candidate has 4 usable
  post-merger points within 30 days, and only 253 of 428 reach ≥3 points in
  ≥2 bands. Use `n_points`/`n_bands` in the manifest to skip the rest.
- No extinction correction is applied. `mwebv` is passed through so you can do
  it downstream.
- Photometry is attached to a `Target`, not to a `(Target, event)` pair, so the
  frame contains that target's *entire* light curve, including data from long
  before or after the event. That's deliberate — pre-merger history is exactly
  what the predetection vetting uses — but constrain it with `dt_min`/`dt_max`
  if you only want the post-merger window.

## Status

- **Photometry extraction** — verified end-to-end against `trove_main`
  (2026-08-03) across six GW events from 2019–2025, including `S251112cm`
  (456 candidates, 56k points) and `GW190814`.
- **Scoring** — the modules import and compile, but `score_candidate` /
  `score_event` have **not been run against a real grid yet**: the only grid on
  disk was still downloading (>2.4 GB and growing) when this was written. Run
  one candidate first and check `skip_reason` before trusting a bulk run.
- **Known upstream issues** — `scoring/KilonovaScorer/IMPROVEMENTS.md` documents
  15 open issues in the scorer, several of which change reported scores
  (notably #1, zero-score bins silently dropped, and #2, the distance
  systematic treated as independent noise so the score gets *more* confident
  the more epochs you add). `scoring.kilonova_scoring` does not modify the
  scorer's behaviour — it guards the crash-level edge cases at the boundary
  (empty metric frames, all-zero `p_tail_std`, non-finite running scores) and
  reports them as `skip_reason` rather than returning a NaN score.
