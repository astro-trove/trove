"""
Generate ONE simulation-grid rung at an arbitrary luminosity distance.

``generate_ladder.py`` builds the fixed 150 / 400 / 800 Mpc ladder. This builds
a single rung wherever you ask for one, for the case the ladder does not cover:
a candidate far enough from every existing rung that scoring it against the
nearest one is not defensible (see the k-correction / time-dilation argument in
``generate_ladder.py`` -- both change the *shape* of the magnitude distribution
per band and epoch, which no after-the-fact correction can undo).

Everything except the distance is imported from ``generate_ladder``, so a rung
made here is constructed identically to the ladder's own and the two are
directly comparable. Do not duplicate N_SIM / MODEL / BANDS here.

Run with the `kn-sim` environment (redback + bilby + lalsuite), which is kept
separate from `t-env` so redback's pins cannot disturb the TROVE Django stack::

    /home/sopanda25/miniconda3/envs/kn-sim/bin/python \
        scoring/KilonovaScorer/generate_rung.py --distance 1200

This is why :func:`scoring.tasks.async_generate_grid_rung` shells out to a
separate interpreter instead of importing anything here: the Django worker runs
in `t-env`, where ``import redback`` does not resolve.

Costs, measured from ``grids/ladder.log``: ~30 min and ~4 GB of disk per rung,
380M rows at N_SIM=10000 across 38 bands.

Exits 0 on success (including "already exists"), non-zero on failure. The last
line of stdout on success is ``RUNG_OK <path>`` so a caller can parse it.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scoring.KilonovaScorer.generate_ladder import BANDS, MODEL, N_SIM  # noqa: E402
from scoring.KilonovaScorer.grids import GRID_DIR  # noqa: E402


def rung_path(distance_mpc: float, outdir=None) -> Path:
    """Where a rung at this distance lives.

    Same ``simulations_{model}_{distance:.0f}Mpc.parquet`` naming the ladder
    uses, which is what lets :func:`KilonovaScorer.grids.grid_for_distance`
    read the distance back off the filename.
    """
    outdir = Path(outdir) if outdir else Path(GRID_DIR)
    return outdir / f"simulations_{MODEL}_{float(distance_mpc):.0f}Mpc.parquet"


def generate_rung(distance_mpc: float, outdir=None, overwrite: bool = False) -> Path:
    """Simulate one rung and return its path, skipping an existing one."""
    outdir = Path(outdir) if outdir else Path(GRID_DIR)
    outdir.mkdir(parents=True, exist_ok=True)
    target = rung_path(distance_mpc, outdir)

    if target.exists() and not overwrite:
        print(f"{distance_mpc:.0f} Mpc: exists, skipping", flush=True)
        return target

    import scoring.KilonovaScorer.simulation as sim  # heavy: redback/bilby/lalsuite

    print(
        f"rung: {distance_mpc:.0f} Mpc, N={N_SIM}, {len(BANDS)} bands -> {outdir}",
        flush=True,
    )
    t0 = time.time()
    # simulate_kilonova streams to disk and returns df=None when saving
    _, path = sim.simulate_kilonova(
        N_SIM=N_SIM,
        DL_Mpc=float(distance_mpc),
        MODEL_NAME=MODEL,
        FILTER_BANDS=BANDS,
        outdir=str(outdir),
    )
    path = Path(path)
    print(
        f"OK {distance_mpc:.0f} Mpc: {path.stat().st_size / 1e6:.0f} MB, "
        f"{time.time() - t0:.0f}s",
        flush=True,
    )
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distance", type=float, required=True, help="luminosity distance, Mpc")
    parser.add_argument("--outdir", default=None, help="defaults to GRID_DIR")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    if not (args.distance > 0):
        print(f"distance must be positive, got {args.distance}", file=sys.stderr)
        return 2

    try:
        path = generate_rung(args.distance, outdir=args.outdir, overwrite=args.overwrite)
    except Exception as exc:  # noqa: BLE001 - the caller only sees the exit code and stderr
        print(f"FAILED {args.distance:.0f} Mpc: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"RUNG_OK {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
