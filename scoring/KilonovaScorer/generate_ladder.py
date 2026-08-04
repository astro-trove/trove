"""
Generate the kilonova simulation grid ladder.

Run with the `kn-sim` environment (redback + bilby + lalsuite), which is kept
separate from `t-env` so redback's pins cannot disturb the TROVE Django stack:

    /home/sopanda25/miniconda3/envs/kn-sim/bin/python \
        scoring/KilonovaScorer/generate_ladder.py

Resumable: any rung whose Parquet already exists is skipped, so an interrupted
run can simply be restarted.

Bands
-----
Simulated in the REAL survey bandpasses TROVE observes in, not in a canonical
g/r/i/z that everything gets approximated onto. 87% of TROVE's candidate
photometry is ATLAS cyan/orange, whose wide filters straddle SDSS bands --
mapping them onto g/r was a real systematic. Simulating `atlasc`/`atlaso`
directly removes it, and likewise for GOTO L, Pan-STARRS w and Gaia G.

The infrared bands are included deliberately: the lanthanide-rich ejecta
component dominates a kilonova from ~2 days onward, which is where a kilonova
separates from a supernova. AT2017gfo -- the event this method is calibrated
against -- was tracked in JHK for exactly that reason. A kilonova grid without
IR discards the most discriminating part of the spectrum.

Rungs
-----
Three, at 150 / 400 / 800 Mpc, rather than reusing one grid (the original was at
259 Mpc) for every event.

**Why a grid is distance-specific at all.** The natural objection is that the
scorer works in *absolute* magnitude, so distance should factor out entirely:
subtract the distance modulus mu and be done. That is true of mu itself. It is
not true of the other two things redshift does to a light curve:

* *K-correction.* A filter has a fixed observer-frame bandpass, so at redshift z
  it samples rest-frame wavelength lambda/(1+z). Between 150 Mpc (z = 0.033) and
  800 Mpc (z = 0.17) the rest-frame wavelength sampled by a fixed filter shifts
  by ~12%. For most transients that is a mild correction; for a kilonova it is
  not, because the lanthanide-rich component produces a steep red continuum that
  moves across a filter edge as z grows.
* *Time dilation.* An observation at t days post-merger samples the model at
  t/(1+z) in the rest frame. At 3 days observed, that is 2.90 d at 150 Mpc but
  2.56 d at 800 Mpc. Kilonovae fade ~4 mag over 10 days, so a 10% shift in epoch
  is a real magnitude shift, and it is largest where the light curve is steepest.

Both are *band- and epoch-dependent*, so they change the SHAPE of the absolute
magnitude distribution in each (band, time-bin) cell, not just its location. A
shape change cannot be undone afterwards by subtracting a constant -- which is
exactly what reusing a single-distance grid implicitly assumes.

The three constraints below then set how many rungs that shape change requires.

**Simulation.** The score consumes P_tail = 2*min(F, 1-F) where F is the CDF of
simulated absolute magnitudes in a (band, time-bin). The error from using a
grid at the wrong distance is therefore bounded exactly, with no distributional
assumption::

    |dP_tail| <= 2 * sup_x |F_1(x) - F_2(x)| = 2 * KS(D_1, D_2)

Measured on *paired* grids (same prior draws at every distance -- see the
RANDOM_SEED note in simulation.py; without pairing this measurement is swamped
by a sampling-noise floor of ~sqrt(2/N))::

    dP_tail ~ 1.94 * |z - z_grid|

and with identical parameters, 100 -> 200 Mpc shifts magnitudes by 0.040 mag
median.

**Observability.** A kilonova peaks near M_AB ~ -16, so it is detectable to
~200 Mpc at ZTF/ATLAS depth, ~316 Mpc deep, ~501 Mpc for targeted follow-up and
~1000 Mpc for DECam-class. That caps the range a grid must cover: beyond it a
detected candidate is necessarily too luminous to be a kilonova and is rejected
on absolute magnitude whichever grid is used.

**Real data.** 1395 of TROVE's 3369 candidates (41%) lie inside 1000 Mpc,
across 26 events; candidate-weighted median 197 Mpc, p95 866 Mpc.

The deciding comparison is against the uncertainty already present in every
candidate's absolute magnitude, sigma_mu = (5/ln10) * sigma_D/D. Over 788 real
candidates that is 0.26 mag median -- but p25 is 0.057 mag, so a quarter have
host redshifts good enough that a mis-distanced grid would be their dominant
error. Hence::

    rungs   worst dP_tail   median dP_tail
      1         0.186           0.120
      2         0.149           0.029
      3         0.140           0.008

One rung's median error equals the whole RNG scatter and exceeds sigma_mu for a
quarter of candidates; three rungs cut it 15x. Beyond three, the worst case
stops improving (it is set by the ends of the range, not the spacing).

`grids.grid_for_distance()` picks the nearest rung from whatever is on disk, so
rungs can be added later without touching anything else.
"""

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scoring.KilonovaScorer.grids import GRID_DIR  # noqa: E402

# NB: `simulation` is imported inside main(), not here. It pulls in redback ->
# bilby -> lalsuite, which only exists in the `kn-sim` environment. BANDS below
# is plain data that the scoring side legitimately needs (to check which
# bandpasses a grid should contain), and importing it must not require the
# simulation stack.

#: Luminosity distances in Mpc. See the "Rungs" note above.
DISTANCES = [150, 400, 800]

#: Matches the existing 259 Mpc grid so every rung is constructed identically.
#: The CDF convergence test showed 5000 already puts grid sampling error
#: (dP_tail ~ 0.03) well below the scorer's own RNG scatter (~0.12), so this can
#: be halved if storage becomes a concern.
N_SIM = 10000

MODEL = "two_component_kilonova_model"

#: Every bandpass TROVE has photometry in, plus IR headroom. Names are sncosmo
#: bandpass ids (redback resolves them through sncosmo).
BANDS = (
    # ATLAS -- 87% of TROVE's candidate photometry
    "atlasc", "atlaso",
    # ZTF
    "ztfg", "ztfr", "ztfi",
    # SDSS-like (generic g/r/i/z from assorted telescopes)
    "sdssu", "sdssg", "sdssr", "sdssi", "sdssz",
    # Rubin / LSST
    "lsstu", "lsstg", "lsstr", "lssti", "lsstz", "lssty",
    # Pan-STARRS, including the wide w filter
    "ps1::g", "ps1::r", "ps1::i", "ps1::z", "ps1::y", "ps1::w",
    # Gaia
    "gaia::g",
    # GOTO, including the wide L filter
    "gotob", "gotog", "gotol", "gotor",
    # Johnson / Cousins
    "bessellux", "bessellb", "bessellv", "bessellr", "besselli",
    # Near-infrared -- where a kilonova separates from a supernova
    "2massj", "2massh", "2massks",
    "f110w", "f125w", "f160w",
)


def main() -> int:
    import scoring.KilonovaScorer.simulation as sim  # heavy: redback/bilby/lalsuite

    outdir = Path(GRID_DIR)
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"ladder: {DISTANCES} Mpc, N={N_SIM}, {len(BANDS)} bands -> {outdir}", flush=True)

    t_all = time.time()
    failures = []
    for i, d in enumerate(DISTANCES, 1):
        target = outdir / f"simulations_{MODEL}_{float(d):.0f}Mpc.parquet"
        if target.exists():
            print(f"[{i}/{len(DISTANCES)}] {d} Mpc: exists, skipping", flush=True)
            continue
        print(f"\n=== [{i}/{len(DISTANCES)}] {d} Mpc ===", flush=True)
        t0 = time.time()
        try:
            # simulate_kilonova streams straight to disk and returns df=None
            # when saving, so the row count comes from the Parquet footer.
            _, path = sim.simulate_kilonova(
                N_SIM=N_SIM, DL_Mpc=float(d), MODEL_NAME=MODEL, FILTER_BANDS=BANDS,
            )
            import pyarrow.parquet as pq

            n_rows = pq.ParquetFile(path).metadata.num_rows
            print(
                f"OK {d} Mpc: {n_rows:,} rows, {path.stat().st_size/1e6:.0f} MB, "
                f"{time.time()-t0:.0f}s",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 - one bad rung must not stop the ladder
            print(f"FAILED {d} Mpc: {type(exc).__name__}: {exc}", flush=True)
            failures.append(d)

    print(f"\nLADDER_DONE in {(time.time()-t_all)/60:.1f} min", flush=True)
    if failures:
        print(f"FAILED RUNGS: {failures}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
