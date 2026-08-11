"""Load KilonovaSCORER simulation grids into the local Postgres grid store.

The store is a standalone database addressed by ``TROVE_GRID_DSN`` -- not one
of Django's ``DATABASES`` -- so nothing here touches TROVE's own tables and
there is no migration to apply. See ``KilonovaScorer/DB.md`` for the schema and
the measurements behind it.

    export TROVE_GRID_DSN='postgresql://bench@127.0.0.1:55432/gridbench'

    # what is in the store
    ./manage.py ingest_kn_grid --list

    # load one band first and check it round-trips before committing to a rung
    ./manage.py ingest_kn_grid <rung>.parquet --bands ztfr
    ./manage.py ingest_kn_grid <rung>.parquet --verify ztfr

    # the whole rung (~12 min, ~2.1 GB stored)
    ./manage.py ingest_kn_grid <rung>.parquet

Then point scoring at it with ``TROVE_GRID_BACKEND=postgres``.
"""

import logging
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Ingest a Parquet simulation grid into the local Postgres grid store."

    def add_arguments(self, parser):
        parser.add_argument(
            "parquet", nargs="?", default=None,
            help="Grid file to ingest. Omit with --list or --drop.",
        )
        parser.add_argument(
            "--dsn", default=None,
            help="Grid database connection string (default: $TROVE_GRID_DSN).",
        )
        parser.add_argument(
            "--grid-name", default=None,
            help="Name to store the grid under (default: the file's stem).",
        )
        parser.add_argument(
            "--bands", default=None,
            help="Comma-separated bands to ingest (default: every band in the file). "
                 "Ingesting a subset is how you try one band before the whole rung.",
        )
        parser.add_argument(
            "--distance-mpc", type=float, default=None,
            help="Override the luminosity distance instead of reading it from the file.",
        )
        parser.add_argument(
            "--replace", action="store_true",
            help="Delete every existing row for this grid first, rather than only the "
                 "bands being written.",
        )
        parser.add_argument(
            "--list", action="store_true", dest="do_list",
            help="Show what the store holds and exit.",
        )
        parser.add_argument(
            "--verify", metavar="BAND", default=None,
            help="Compare stored lightcurves in BAND against the Parquet file and exit. "
                 "Requires the parquet argument.",
        )
        parser.add_argument(
            "--verify-count", type=int, default=20,
            help="How many lightcurves --verify compares (default: 20).",
        )
        parser.add_argument(
            "--drop", metavar="GRID", default=None,
            help="Delete GRID from the store and exit.",
        )

    def handle(self, *args, **opts):
        # Imported here rather than at module scope: the grid backend pulls in
        # psycopg2 and pyarrow, and `manage.py help` should not pay for that.
        from scoring.KilonovaScorer import grid_db

        dsn = opts["dsn"]

        if opts["do_list"]:
            return self._list(grid_db, dsn)
        if opts["drop"]:
            return self._drop(grid_db, dsn, opts["drop"])

        if not opts["parquet"]:
            raise CommandError("Give a parquet file to ingest, or use --list / --drop.")
        path = Path(opts["parquet"]).expanduser()
        if not path.exists():
            raise CommandError(f"No such file: {path}")
        grid = opts["grid_name"] or path.stem

        if opts["verify"]:
            return self._verify(grid_db, dsn, path, grid, opts["verify"], opts["verify_count"])

        grid_db.ensure_schema(dsn)
        bands = [b.strip() for b in opts["bands"].split(",")] if opts["bands"] else None

        self.stdout.write(f"Ingesting {path.name} as {grid!r}")
        summary = grid_db.ingest_parquet(
            path,
            grid=grid,
            bands=bands,
            distance_mpc=opts["distance_mpc"],
            replace=opts["replace"],
            dsn=dsn,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"{summary['grid']}: {len(summary['bands'])} band(s), "
                f"{summary['n_samples']} samples x {summary['n_time']} epochs, "
                f"D_L={summary['distance_mpc']:.0f} Mpc"
            )
        )
        self.stdout.write(
            "Verify a band before relying on it:  "
            f"./manage.py ingest_kn_grid {path} --verify <band>"
        )

    # -- subcommands --------------------------------------------------------

    def _list(self, grid_db, dsn):
        if not grid_db.grid_store_ready(dsn):
            self.stdout.write(
                self.style.WARNING(
                    "The grid store is empty or unreachable. Check TROVE_GRID_DSN, then "
                    "ingest a rung with `manage.py ingest_kn_grid <parquet>`."
                )
            )
            return
        grids = grid_db.available_grids_db(dsn)
        self.stdout.write(f"{'grid':<58} {'D_L/Mpc':>9} {'size/MB':>9} {'samples':>8} {'epochs':>7}")
        for row in grids.itertuples():
            self.stdout.write(
                f"{row.path.name:<58} {row.distance_mpc:>9.0f} {row.size_mb:>9.0f} "
                f"{row.n_samples:>8d} {row.n_time:>7d}"
            )
            bands = grid_db.grid_bands(row.path.name, dsn)
            self.stdout.write(f"    {len(bands)} band(s): {', '.join(bands)}")

    def _drop(self, grid_db, dsn, grid):
        conn = grid_db._connection(dsn)
        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {grid_db.LIGHTCURVE_TABLE} WHERE grid = %s", [grid]
            )
            n = cur.rowcount
            cur.execute(f"DELETE FROM {grid_db.AXIS_TABLE} WHERE grid = %s", [grid])
        self.stdout.write(self.style.SUCCESS(f"Dropped {grid}: {n} lightcurve(s)"))

    def _verify(self, grid_db, dsn, path, grid, band, n_check):
        report = grid_db.verify_band(path, grid, band, n_check=n_check, dsn=dsn)
        self.stdout.write(
            f"{report['grid']}/{report['band']}: compared {report['checked']} lightcurve(s) "
            f"of {report['n_time']} epochs against {path.name}"
        )
        if report["missing_from_file"]:
            self.stdout.write(
                self.style.ERROR(
                    f"  {len(report['missing_from_file'])} sample_id(s) in the store are not "
                    f"in the file: {report['missing_from_file'][:10]}"
                )
            )
        for sid, why in report["mismatched"][:10]:
            self.stdout.write(self.style.ERROR(f"  sample {sid}: {why}"))
        if report["ok"]:
            self.stdout.write(
                self.style.SUCCESS("  exact match -- every magnitude is bit-identical")
            )
        else:
            raise CommandError(
                f"{len(report['mismatched'])} mismatch(es), max |delta| "
                f"{report['max_abs_delta']:.6g}"
            )
