"""
Generate ONE simulation-grid rung at a given luminosity distance.

This is the only grid generator. It used to be the exception to
``generate_ladder.py``, which built a fixed 150 / 400 / 800 Mpc ladder that the
scorer then selected from per candidate; the ladder was removed on 2026-08-12
and there is now a single 259 Mpc grid, pinned by name in
``DEFAULT_KILONOVA_PARAMS``. The sizing argument for why a grid is
distance-specific at all -- k-correction and time dilation change the *shape* of
the magnitude distribution per band and epoch, so no after-the-fact correction
undoes a distance mismatch -- is preserved in IMPROVEMENTS.md section 18, along
with the measurement of how many rungs it would take.

Run with the grid-generation environment (`KN_SIM_PYTHON` in
settings_local.py; see KilonovaScorer/README.md) (redback + bilby + lalsuite), which is kept
separate from `t-env` so redback's pins cannot disturb the TROVE Django stack::

    "$KN_SIM_PYTHON" \
        scoring/KilonovaScorer/generate_rung.py --distance 1200

Costs, measured from ``grids/ladder.log``: ~55 min and ~1.5 GB in the store per
rung, 380,000 lightcurves at N_SIM=10000 across 38 bands.

Exits 0 on success (including "already exists"), non-zero on failure. The last
line of stdout on success is ``RUNG_OK <path>`` so a caller can parse it.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

#: Samples per rung. The CDF convergence test showed 5000 already puts grid
#: sampling error (dP_tail ~ 0.03) well below the scorer's own RNG scatter
#: (~0.12), so this can be halved if storage becomes a concern. Referenced by
#: phot_method's k_ABC note, which is calibrated against this value.
N_SIM = 10000

MODEL = "two_component_kilonova_model"

#: Every bandpass TROVE has photometry in, plus IR headroom. Names are sncosmo
#: bandpass ids (redback resolves them through sncosmo).
#:
#: Simulated in the REAL survey bandpasses TROVE observes in, not a canonical
#: g/r/i/z that everything gets approximated onto. 87% of TROVE's candidate
#: photometry is ATLAS cyan/orange, whose wide filters straddle SDSS bands --
#: mapping them onto g/r was a real systematic. Simulating ``atlasc``/``atlaso``
#: directly removes it, and likewise for GOTO L, Pan-STARRS w and Gaia G.
#:
#: The infrared bands are deliberate: the lanthanide-rich ejecta component
#: dominates from ~2 days onward, which is where a kilonova separates from a
#: supernova. AT2017gfo -- the event this method is calibrated against -- was
#: tracked in JHK for exactly that reason. A kilonova grid without IR discards
#: the most discriminating part of the spectrum.
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


def rung_name(distance_mpc: float) -> str:
    """Key a rung at this distance is stored under.

    The distance is also a column in ``kn_grid_axis``, which is where anything
    needing it should read it -- this name is for humans.
    """
    return f"simulations_{MODEL}_{float(distance_mpc):.0f}Mpc"


def generate_rung(distance_mpc: float, overwrite: bool = False, dsn=None) -> str:
    """Simulate one rung into the grid store and return its name."""
    grid = rung_name(distance_mpc)

    from scoring.KilonovaScorer import grid_db

    if grid_db.grid_exists(grid, dsn=dsn) and not overwrite:
        print(f"{distance_mpc:.0f} Mpc: '{grid}' already in the store, skipping",
              flush=True)
        return grid

    import scoring.KilonovaScorer.simulation as sim  # heavy: redback/bilby/lalsuite

    print(f"rung: {distance_mpc:.0f} Mpc, N={N_SIM}, {len(BANDS)} bands -> {grid}",
          flush=True)
    t0 = time.time()
    summary, name = sim.simulate_kilonova(
        N_SIM=N_SIM,
        DL_Mpc=float(distance_mpc),
        MODEL_NAME=MODEL,
        FILTER_BANDS=BANDS,
        grid_name=grid,
        replace=True,
        dsn=dsn,
    )
    print(f"OK {distance_mpc:.0f} Mpc: {summary['n_samples']:,} samples x "
          f"{len(summary['bands'])} bands, {time.time() - t0:.0f}s", flush=True)
    return name


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distance", type=float, required=True, help="luminosity distance, Mpc")
    parser.add_argument("--dsn", default=None,
                        help="grid database (default: $TROVE_GRID_DSN)")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    if not (args.distance > 0):
        print(f"distance must be positive, got {args.distance}", file=sys.stderr)
        return 2

    try:
        path = generate_rung(args.distance, overwrite=args.overwrite, dsn=args.dsn)
    except Exception as exc:  # noqa: BLE001 - the caller only sees the exit code and stderr
        print(f"FAILED {args.distance:.0f} Mpc: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"RUNG_OK {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
