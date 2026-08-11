"""grid_db.py -- the Postgres backend for simulation grids.

The Parquet backend in :mod:`.grids` reads a grid off disk. This one reads the
same grid out of two Postgres tables:

    kn_grid_axis        one row per grid: the shared time axis, the distance
    kn_grid_lightcurve  one row per (grid, band, sample_id): n_time magnitudes

:func:`load_grid_db` returns exactly what :func:`.grids.load_grid` returns --
same columns, same dtypes, same filters applied -- so the scorer cannot tell
which backend produced its grid. Choose one with ``TROVE_GRID_BACKEND``;
Parquet remains the default.

This is a **standalone local database**, addressed by DSN, not one of Django's
``DATABASES`` entries -- so there is no migration, nothing to apply to TROVE's
own database, and no Django import anywhere in this module.
:func:`ensure_schema` creates the two tables on demand. A rung is ~2 GB of pure
simulation output: it is not TROVE data, it does not belong in TROVE's
migration history, and keeping it separate means the grid store can be dropped
and rebuilt without touching anything else.

    export TROVE_GRID_DSN='postgresql://bench@127.0.0.1:55432/gridbench'
    export TROVE_GRID_BACKEND=postgres

Why one row per *lightcurve*
----------------------------
Measured in ``DB.md``, and it is the whole design. A rung holds 380M
magnitudes, but it is not relational data: every lightcurve is evaluated on the
same ``np.linspace(0, 10, 1000)``, so ``time`` cycles through 1,000 values
380,000 times, ``sample_id`` repeats 1,000 times and ``band`` 10,000,000 times.
Postgres adds a 23-byte tuple header per row and does not compress columns, so
the natural (sample_id, band, time, magnitude) table measures 25 GB per rung and
4.5 s per read -- worse than Parquet on every axis.

Collapsing each lightcurve into one row removes 1,000 tuple headers, 1,000
visibility checks and 1,000 protocol messages per lightcurve; the three key
columns disappear because position in the array *is* the epoch; and at 4 KB the
value clears the TOAST threshold and gets compressed. Same data, same query:
253.8 MB -> 20.9 MB, 4.5 s -> 0.039 s.

What that buys over Parquet
---------------------------
Memory, mostly. A one-band read is 3.05 s / 847 MB peak here against 8.5 s /
2,840 MB from the Parquet file, because the rung's 400 row groups each contain
every band -- so a band filter prunes nothing and the reader decompresses the
whole 3.6 GB either way, discarding 2.39 GB of it. Postgres finds 10,000 rows
by index and stops. On this machine a 2.8 GB spike is ~43% of usable RAM and
the WSL OOM killer takes the VM down with the process, so the peak is not an
abstract number.

Above about three bands per load Parquet catches up on wall clock (its cost is
fixed; this one scales with the ask) but still loses on memory.
"""

from __future__ import annotations

import io
import logging
import os
import struct
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

#: Connection string for the grid database. No default on purpose: grids are
#: gigabytes, and a backend that silently connected to TROVE's own database
#: would put them somewhere nobody asked for. Set it explicitly, e.g.
#:
#:     export TROVE_GRID_DSN='postgresql://bench@127.0.0.1:55432/gridbench'
GRID_DSN = os.environ.get("TROVE_GRID_DSN")

AXIS_TABLE = "kn_grid_axis"
LIGHTCURVE_TABLE = "kn_grid_lightcurve"

#: Magnitudes are stored as float32 little-endian. Fixed by the ingest, relied
#: on by the read; changing one without the other silently returns garbage.
DTYPE = np.dtype("<f4")
ITEMSIZE = DTYPE.itemsize

#: Header of PostgreSQL's binary COPY stream: signature, flags, extension area.
_COPY_SIGNATURE = b"PGCOPY\n\xff\r\n\x00"
_COPY_HEADER = _COPY_SIGNATURE + struct.pack(">ii", 0, 0)
_COPY_TRAILER = struct.pack(">h", -1)


# ---------------------------------------------------------------------------
# connection
# ---------------------------------------------------------------------------


#: ``(dsn, connection)`` for the current process, reused. A scoring run issues
#: a handful of large reads, so pooling buys nothing, but reconnecting per load
#: over an SSH tunnel would cost five round trips each time. Kept as a tuple
#: because psycopg2's connection is a C type that takes no extra attributes.
_CONN: Optional[Tuple[str, object]] = None


def _connection(dsn: Optional[str] = None):
    """Cached psycopg2 connection to the grid database.

    ``psycopg2`` rather than Django: this database is not in ``DATABASES``, and
    keeping the import out means :mod:`.grid_db` -- like the rest of
    KilonovaScorer -- can be used from a plain script with no framework set up.
    """
    global _CONN

    dsn = dsn or GRID_DSN
    if not dsn:
        raise RuntimeError(
            "No grid database configured. Set TROVE_GRID_DSN, e.g.\n"
            "    export TROVE_GRID_DSN='postgresql://bench@127.0.0.1:55432/gridbench'\n"
            "or keep the Parquet backend with TROVE_GRID_BACKEND=parquet (the default)."
        )

    if _CONN is not None and _CONN[0] == dsn and not _CONN[1].closed:
        return _CONN[1]

    import psycopg2

    conn = psycopg2.connect(dsn)
    conn.autocommit = True  # bulk COPY, no transaction to hold open
    _CONN = (dsn, conn)
    return conn


def close_connection() -> None:
    """Drop the cached connection (tests, and after a fork)."""
    global _CONN

    if _CONN is not None and not _CONN[1].closed:
        _CONN[1].close()
    _CONN = None


SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {AXIS_TABLE} (
    grid         text PRIMARY KEY,
    time_axis    bytea NOT NULL,
    distance_mpc double precision NOT NULL,
    n_samples    integer NOT NULL,
    n_time       integer NOT NULL
);

CREATE TABLE IF NOT EXISTS {LIGHTCURVE_TABLE} (
    grid       text     NOT NULL,
    band       text     NOT NULL,
    sample_id  integer  NOT NULL,
    absmag     bytea    NOT NULL,
    PRIMARY KEY (grid, band, sample_id)
);
"""

# LZ4 beats pglz on both ratio and detoast speed for 4 KB float blocks, but it
# is a build option and a missing method is an error rather than a fallback --
# the conda-forge Postgres 16 used for the benchmark did not have it. Catch it
# so a server without LZ4 quietly keeps pglz.
_COMPRESSION_SQL = f"""
DO $$
BEGIN
    ALTER TABLE {LIGHTCURVE_TABLE} ALTER COLUMN absmag SET COMPRESSION lz4;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'lz4 unavailable; {LIGHTCURVE_TABLE}.absmag keeps pglz';
END
$$;
"""


def ensure_schema(dsn: Optional[str] = None) -> None:
    """Create the grid tables if they are not there. Safe to call repeatedly."""
    conn = _connection(dsn)
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        cur.execute(_COMPRESSION_SQL)


def _copy_out(conn, sql: str, params: Sequence = ()) -> bytes:
    """Run ``sql`` as a binary ``COPY ... TO STDOUT`` and return the raw stream.

    Binary rather than the default text format because ``psycopg2`` renders
    ``bytea`` as hex, two wire bytes per stored byte. Binary halved the transfer
    *and* cut a three-band read from 19.9 s to 11.7 s on a local socket where
    bandwidth is free -- the saving is hex decoding, not the wire.
    """
    with conn.cursor() as cur:
        stmt = cur.mogrify(sql, params).decode() if params else sql
        buf = io.BytesIO()
        try:
            cur.copy_expert(f"COPY ({stmt}) TO STDOUT (FORMAT binary)", buf)
        except AttributeError as exc:  # psycopg3 renamed the whole COPY API
            raise RuntimeError(
                "The grid backend needs psycopg2's copy_expert(); this connection "
                "does not have it (psycopg3?). Port _copy_out/_copy_in to "
                "cursor.copy() before using psycopg3."
            ) from exc
        return buf.getvalue()


def _iter_copy_rows(blob: bytes) -> Iterator[List[Optional[memoryview]]]:
    """Yield one list of raw field buffers per row of a binary COPY stream.

    Fields come back as ``memoryview`` slices of ``blob`` rather than copies, so
    a 40 MB response is walked without duplicating it.
    """
    mv = memoryview(blob)
    if mv[:11].tobytes() != _COPY_SIGNATURE:
        raise ValueError("not a PostgreSQL binary COPY stream")
    pos = 11
    _flags, ext_len = struct.unpack_from(">ii", mv, pos)
    pos += 8 + ext_len

    while True:
        (n_fields,) = struct.unpack_from(">h", mv, pos)
        pos += 2
        if n_fields == -1:  # trailer
            return
        row: List[Optional[memoryview]] = []
        for _ in range(n_fields):
            (length,) = struct.unpack_from(">i", mv, pos)
            pos += 4
            if length == -1:
                row.append(None)
            else:
                row.append(mv[pos : pos + length])
                pos += length
        yield row


def _copy_in(conn, table: str, columns: Sequence[str], payload: bytes) -> None:
    """Load a binary COPY payload built by :func:`_encode_rows`."""
    with conn.cursor() as cur:
        cur.copy_expert(
            f"COPY {table} ({', '.join(columns)}) FROM STDIN (FORMAT binary)",
            io.BytesIO(payload),
        )


def _encode_rows(rows: Iterable[Sequence]) -> bytes:
    """Encode rows of ``(text | int | bytes)`` fields as a binary COPY stream."""
    out = io.BytesIO()
    out.write(_COPY_HEADER)
    for row in rows:
        out.write(struct.pack(">h", len(row)))
        for value in row:
            if value is None:
                out.write(struct.pack(">i", -1))
            elif isinstance(value, (bytes, bytearray, memoryview)):
                data = bytes(value)
                out.write(struct.pack(">i", len(data)))
                out.write(data)
            elif isinstance(value, str):
                data = value.encode("utf-8")
                out.write(struct.pack(">i", len(data)))
                out.write(data)
            elif isinstance(value, (int, np.integer)):
                out.write(struct.pack(">i", 4))
                out.write(struct.pack(">i", int(value)))
            elif isinstance(value, (float, np.floating)):
                out.write(struct.pack(">i", 8))
                out.write(struct.pack(">d", float(value)))
            else:
                raise TypeError(f"cannot encode {type(value).__name__} for binary COPY")
    out.write(_COPY_TRAILER)
    return out.getvalue()


# ---------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------


def grid_store_ready(dsn: Optional[str] = None) -> bool:
    """True if the grid tables exist and hold at least one grid."""
    try:
        conn = _connection(dsn)
        with conn.cursor() as cur:
            cur.execute(f"SELECT to_regclass('{AXIS_TABLE}') IS NOT NULL")
            if not cur.fetchone()[0]:
                return False
            cur.execute(f"SELECT count(*) FROM {AXIS_TABLE}")
            return cur.fetchone()[0] > 0
    except Exception as exc:  # noqa: BLE001 - a missing/unreachable store is an answer
        logger.debug("Grid store not available: %s", exc)
        return False


def available_grids_db(dsn: Optional[str] = None) -> pd.DataFrame:
    """Inventory of grids in the database, sorted by distance.

    Columns mirror :func:`.grids.available_grids` -- ``path`` (a
    :class:`~.grids.GridRef` rather than a filesystem path), ``distance_mpc``
    and ``size_mb`` -- so callers that only read those keep working.
    """
    from .grids import GridRef

    conn = _connection(dsn)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT a.grid, a.distance_mpc, a.n_samples, a.n_time,
                   COALESCE(SUM(pg_column_size(l.absmag)), 0)
              FROM {AXIS_TABLE} a
              LEFT JOIN {LIGHTCURVE_TABLE} l ON l.grid = a.grid
             GROUP BY a.grid, a.distance_mpc, a.n_samples, a.n_time
             ORDER BY a.distance_mpc
            """
        )
        rows = cur.fetchall()

    return pd.DataFrame(
        [
            {
                "path": GridRef(name=name, distance_mpc=float(dist), backend="postgres"),
                "distance_mpc": float(dist),
                "size_mb": float(nbytes) / 1e6,
                "n_samples": int(n_samples),
                "n_time": int(n_time),
            }
            for name, dist, n_samples, n_time, nbytes in rows
        ],
        columns=["path", "distance_mpc", "size_mb", "n_samples", "n_time"],
    )


def grid_axis(grid: str, dsn: Optional[str] = None) -> Tuple[np.ndarray, float, int]:
    """``(time_axis, distance_mpc, n_samples)`` for one grid."""
    conn = _connection(dsn)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT time_axis, distance_mpc, n_samples FROM {AXIS_TABLE} WHERE grid = %s",
            [grid],
        )
        row = cur.fetchone()
    if row is None:
        raise FileNotFoundError(
            f"No grid named {grid!r} in {AXIS_TABLE}. Ingest one with "
            f"`manage.py ingest_kn_grid <parquet>`, or list what is there with "
            f"`manage.py ingest_kn_grid --list`."
        )
    axis_blob, distance, n_samples = row
    axis = np.frombuffer(bytes(axis_blob), dtype=DTYPE)
    return axis, float(distance), int(n_samples)


def grid_bands(grid: str, dsn: Optional[str] = None) -> List[str]:
    """Band names present for ``grid``."""
    conn = _connection(dsn)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT band FROM {LIGHTCURVE_TABLE} WHERE grid = %s ORDER BY band",
            [grid],
        )
        return [r[0] for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


def load_grid_db(
    grid: str,
    bands: Optional[Sequence[str]] = None,
    min_time: float = 0.0,
    max_time: Optional[float] = None,
    max_abs_mag: float = 0.0,
    add_filter_mapped: bool = True,
    mode: str = "survey",
    dsn: Optional[str] = None,
) -> pd.DataFrame:
    """Load a grid out of Postgres as the long frame the scorer consumes.

    Returns ``sample_id`` / ``band`` / ``time`` / ``absolute_magnitude`` (plus
    ``filter_mapped``), with ``time <= min_time`` and
    ``absolute_magnitude > max_abs_mag`` dropped -- identical to
    :func:`.grids.load_grid`, whose docstring explains why those two cuts exist.

    The time window is applied **in the database**, as a ``substring`` of the
    magnitude array: the epochs are ordered, so a cut is a byte range, and a
    load that only needs 0-4 d of a 0-10 d grid transfers 40% of the bytes
    rather than filtering after the fact.
    """
    axis, distance, _n_samples = grid_axis(grid, dsn)
    n_time = axis.size

    # Epoch window -> column range. side='right' on min_time keeps the cut
    # exclusive (t <= min_time is dropped) and inclusive on max_time, matching
    # the Parquet reader's `time <= max_time` pushdown exactly.
    lo = int(np.searchsorted(axis, np.float32(min_time), side="right"))
    hi = n_time if max_time is None else int(
        np.searchsorted(axis, np.float32(max_time), side="right")
    )
    if hi <= lo:
        raise ValueError(
            f"Grid {grid} has no epochs in ({min_time}, {max_time}]; its axis spans "
            f"[{axis[0]:.3f}, {axis[-1]:.3f}] d"
        )
    axis_sel = np.ascontiguousarray(axis[lo:hi])
    n_sel = hi - lo

    # bytea substring is 1-based and counts bytes, not elements.
    sql = (
        f"SELECT band, sample_id, substring(absmag FROM {lo * ITEMSIZE + 1} "
        f"FOR {n_sel * ITEMSIZE}) FROM {LIGHTCURVE_TABLE} WHERE grid = %s"
    )
    params: List = [grid]
    if bands:
        sql += " AND band = ANY(%s)"
        params.append(list(bands))
    sql += " ORDER BY band, sample_id"

    conn = _connection(dsn)
    blob = _copy_out(conn, sql, params)

    band_names: List[str] = []
    band_codes: List[int] = []
    sample_ids: List[int] = []
    mags: List[np.ndarray] = []
    code_of: dict = {}

    for band_buf, sid_buf, mag_buf in _iter_copy_rows(blob):
        name = bytes(band_buf).decode("utf-8")
        code = code_of.get(name)
        if code is None:
            code = len(band_names)
            code_of[name] = code
            band_names.append(name)
        band_codes.append(code)
        # int4 arrives big-endian; the magnitudes are our own little-endian blob.
        sample_ids.append(struct.unpack(">i", sid_buf)[0])
        mags.append(np.frombuffer(mag_buf, dtype=DTYPE))

    del blob
    if not mags:
        raise ValueError(
            f"Grid {grid} returned no rows for bands={list(bands or [])}, max_time={max_time}"
        )

    n_lc = len(mags)
    widths = {m.size for m in mags}
    if widths != {n_sel}:
        raise ValueError(
            f"Grid {grid} has lightcurves of {sorted(widths)} epochs where the axis "
            f"slice is {n_sel}; the store is inconsistent with {AXIS_TABLE}."
        )

    # Expand the (lightcurve x epoch) block into the scorer's long format. The
    # magnitudes are the only per-element data; time and sample_id are a tile
    # and a repeat of the axis, which is exactly the redundancy the row-per-
    # lightcurve schema exists to avoid storing.
    absolute_magnitude = np.concatenate(mags)
    del mags
    time = np.tile(axis_sel, n_lc)
    sample_id = np.repeat(np.asarray(sample_ids, dtype=np.int32), n_sel)
    codes = np.repeat(np.asarray(band_codes, dtype=np.int32), n_sel)

    # Drop the artifacts the Parquet path drops: flux underflow at late times
    # comes back as an absurd (positive) magnitude and distorts the KDE.
    keep = np.isfinite(absolute_magnitude) & (absolute_magnitude <= np.float32(max_abs_mag))
    if not keep.all():
        n_drop = int((~keep).sum())
        logger.info(
            "Grid %s: dropping %d artifact row(s) with M>%g", grid, n_drop, max_abs_mag
        )
        # One array at a time, freeing as we go: both copies of a 10M-row
        # column exist only for the length of each statement.
        absolute_magnitude = absolute_magnitude[keep]
        time = time[keep]
        sample_id = sample_id[keep]
        codes = codes[keep]
    del keep

    band = pd.Categorical.from_codes(codes, categories=band_names)
    del codes

    df = pd.DataFrame(
        {
            "sample_id": sample_id,
            "band": band,
            "time": time,
            "absolute_magnitude": absolute_magnitude,
        },
        copy=False,
    )

    if add_filter_mapped:
        if mode == "survey":
            # The grid's own bandpass ids ARE the matching key: an observation
            # through atlaso is scored against simulations through atlaso.
            df["filter_mapped"] = df["band"]
        else:
            from .core import FILTER_LOOKUP

            mapping = {c: FILTER_LOOKUP.get(str(c).lower().strip()) for c in band_names}
            df["filter_mapped"] = df["band"].map(mapping).astype("category")
            unmapped = df["filter_mapped"].isna()
            if unmapped.any():
                logger.info(
                    "Grid %s: dropping %d row(s) in unmapped bands", grid, int(unmapped.sum())
                )
                df = df[~unmapped]
                df["filter_mapped"] = df["filter_mapped"].cat.remove_unused_categories()

    if df.empty:
        raise ValueError(f"Grid {grid} has no usable rows after filtering")

    df = df.reset_index(drop=True)
    df.attrs["name"] = grid
    df.attrs["distance_mpc"] = distance

    logger.info(
        "Loaded %s from %s: %d rows, %d lightcurves, t=[%.3f, %.2f] d, "
        "M=[%.2f, %.2f], D_L=%.0f Mpc, %.0f MB in memory",
        grid, LIGHTCURVE_TABLE, len(df), n_lc,
        df["time"].min(), df["time"].max(),
        df["absolute_magnitude"].min(), df["absolute_magnitude"].max(),
        distance, df.memory_usage(deep=True).sum() / 1e6,
    )
    return df


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


def _parquet_bands(path) -> List[str]:
    """Distinct band names in a Parquet grid.

    Only called when the caller did not name the bands, and it is not cheap: a
    rung's ``band`` column is 380M values, and every row group holds every band
    so there is nothing to prune. Uniquing inside Arrow one batch at a time
    keeps it to seconds and one batch of memory -- materialising each row group
    as a Python list instead takes minutes, which is worth stating because it
    is the obvious way to write this.
    """
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    if "band" not in pq.ParquetFile(path).schema_arrow.names:
        raise ValueError(f"{path} has no 'band' column")

    seen = set()
    for batch in _scan(path, ["band"]).to_batches():
        if batch.num_rows:
            seen.update(pc.unique(batch.column("band")).to_pylist())
    return sorted(seen)


def _scan(path, columns: Sequence[str], band: Optional[str] = None):
    """Stream a parquet file one small batch at a time.

    The readahead settings are not incidental. Every row group in a rung
    contains every band, so a band filter prunes nothing and each prefetched
    batch is a fully decompressed row group; at pyarrow's defaults (16 batches
    x 4 fragments in flight) a one-band read peaks at 3.3 GB. One batch, one
    fragment, no threads is what bounds it.
    """
    import pyarrow.dataset as pds
    import pyarrow.compute as pc

    dataset = pds.dataset(path, format="parquet")
    return dataset.scanner(
        columns=list(columns),
        filter=(pc.field("band") == band) if band is not None else None,
        batch_size=250_000,
        batch_readahead=1,
        fragment_readahead=1,
        use_threads=False,
    )


def _nearest_index(values: np.ndarray, axis: np.ndarray) -> np.ndarray:
    """Index into ``axis`` of the nearest entry to each of ``values``.

    Float equality is not safe here -- the axis is stored as float32 and the
    file's ``time`` column may be float64 -- so this snaps to the nearest epoch
    and the caller checks the residual.
    """
    idx = np.searchsorted(axis, values)
    idx = np.clip(idx, 1, axis.size - 1)
    left = axis[idx - 1]
    right = axis[idx]
    return np.where(np.abs(values - left) <= np.abs(right - values), idx - 1, idx)


def ingest_parquet(
    path,
    grid: Optional[str] = None,
    bands: Optional[Sequence[str]] = None,
    distance_mpc: Optional[float] = None,
    replace: bool = False,
    dsn: Optional[str] = None,
) -> dict:
    """Load a Parquet rung into the grid tables, one band at a time.

    Memory is bounded by the (n_samples x n_time) block for a single band --
    40 MB at the standard 10,000 x 1,000 -- plus one parquet batch and the COPY
    payload. That is the point of filling a preallocated array from a streaming
    scan rather than materialising the band as a frame: read as a frame, one
    band of a rung peaks near 2.7 GB.

    Returns a summary dict; does not commit if a band fails, so a partial rung
    is never left behind.
    """
    from pathlib import Path

    from .grids import grid_distance_mpc

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    grid = grid or path.stem

    # Discovering the band list is a whole extra pass over the file, so it only
    # happens when the caller did not say which bands they wanted. Naming them
    # is what makes ingesting one band to try it out cheap.
    if bands:
        todo = list(bands)
    else:
        todo = _parquet_bands(path)
        logger.info("%s holds %d band(s): %s", path.name, len(todo), ", ".join(todo))

    if distance_mpc is None:
        distance_mpc = grid_distance_mpc(path)
    if not np.isfinite(distance_mpc):
        raise ValueError(
            f"Could not determine the distance of {path.name}. Pass --distance-mpc."
        )

    # --- axis and sample ids, from the first band --------------------------
    logger.info("Reading the time axis and sample ids from %s of %s", todo[0], path.name)
    times: List[np.ndarray] = []
    sids: List[np.ndarray] = []
    for batch in _scan(path, ["sample_id", "time"], todo[0]).to_batches():
        if batch.num_rows:
            times.append(batch.column("time").to_numpy(zero_copy_only=False))
            sids.append(batch.column("sample_id").to_numpy(zero_copy_only=False))
    if not times:
        raise ValueError(
            f"{path.name} has no rows in band {todo[0]!r}. Check the spelling against "
            f"`manage.py ingest_kn_grid --list`, or omit --bands to ingest every band."
        )
    axis = np.unique(np.concatenate(times)).astype(DTYPE)
    sample_ids = np.unique(np.concatenate(sids)).astype(np.int32)
    del times, sids
    n_time, n_samples = axis.size, sample_ids.size
    logger.info(
        "%s: %d samples x %d epochs, t=[%.4f, %.4f] d, D_L=%.0f Mpc",
        grid, n_samples, n_time, axis[0], axis[-1], distance_mpc,
    )

    # Hoisted: the epoch snap compares against this on every batch of every
    # band, and re-widening a 1,000-element axis each time is pure overhead.
    axis_f8 = axis.astype(np.float64)

    conn = _connection(dsn)
    written = {}
    with conn.cursor() as cur:
        if replace:
            cur.execute(f"DELETE FROM {LIGHTCURVE_TABLE} WHERE grid = %s", [grid])
            logger.info("Replacing grid %s: deleted %d existing row(s)", grid, cur.rowcount)
        cur.execute(
            f"""
            INSERT INTO {AXIS_TABLE} (grid, time_axis, distance_mpc, n_samples, n_time)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (grid) DO UPDATE SET
                time_axis = EXCLUDED.time_axis,
                distance_mpc = EXCLUDED.distance_mpc,
                n_samples = EXCLUDED.n_samples,
                n_time = EXCLUDED.n_time
            """,
            [grid, axis.tobytes(), float(distance_mpc), n_samples, n_time],
        )

    for band in todo:
        block = np.full((n_samples, n_time), np.nan, dtype=DTYPE)
        n_rows = 0
        worst = 0.0
        for batch in _scan(path, ["sample_id", "time", "absolute_magnitude"], band).to_batches():
            if not batch.num_rows:
                continue
            b_sid = batch.column("sample_id").to_numpy(zero_copy_only=False)
            b_time = batch.column("time").to_numpy(zero_copy_only=False)
            b_mag = batch.column("absolute_magnitude").to_numpy(zero_copy_only=False)
            col = _nearest_index(b_time.astype(np.float64), axis_f8)
            worst = max(worst, float(np.abs(b_time - axis[col]).max()))
            # sample_ids is sorted and unique, so its position IS the row index.
            # A dict lookup per row would be 10M Python-level operations per
            # band; searchsorted is one vectorised call per batch.
            row = np.searchsorted(sample_ids, b_sid)
            block[row, col] = b_mag
            n_rows += batch.num_rows

        if not n_rows:
            raise ValueError(f"{path.name} has no rows in band {band!r}")

        spacing = float(np.min(np.diff(axis))) if n_time > 1 else np.inf
        if worst > spacing / 2:
            raise ValueError(
                f"{grid}/{band}: an epoch is {worst:.3g} d from the nearest axis point "
                f"(spacing {spacing:.3g} d). The lightcurves do not share one time axis, "
                f"which this schema requires."
            )
        holes = int(np.isnan(block).sum())
        if holes:
            logger.warning(
                "%s/%s: %d of %d (sample, epoch) cells are missing from the file and "
                "will be stored as NaN", grid, band, holes, block.size,
            )

        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {LIGHTCURVE_TABLE} WHERE grid = %s AND band = %s", [grid, band]
            )
        _copy_in(
            conn,
            LIGHTCURVE_TABLE,
            ("grid", "band", "sample_id", "absmag"),
            _encode_rows(
                (grid, band, int(sid), block[i].tobytes())
                for i, sid in enumerate(sample_ids)
            ),
        )
        written[band] = n_samples
        logger.info(
            "%s/%s: %d lightcurves from %d row(s), %.0f MB payload",
            grid, band, n_samples, n_rows, block.nbytes / 1e6,
        )
        del block

    return {
        "grid": grid,
        "distance_mpc": float(distance_mpc),
        "n_samples": n_samples,
        "n_time": n_time,
        "bands": written,
    }


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------


def verify_band(
    path,
    grid: str,
    band: str,
    n_check: int = 20,
    dsn: Optional[str] = None,
) -> dict:
    """Compare ``n_check`` lightcurves in the store against the Parquet source.

    The two paths should agree **exactly**, not approximately: both cast the
    file's magnitudes to float32 once and nothing else touches the numbers, so
    any difference is a bug in the ingest rather than rounding.

    Deliberately samples a handful of lightcurves rather than a whole band. A
    full-band comparison would hold the Parquet read's ~2.8 GB peak and the
    database frame at the same time, which is most of this machine's RAM; a
    per-``sample_id`` pushdown reads a few MB and catches an indexing or
    endianness error just as well.
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.dataset as pds

    from pathlib import Path

    axis, _distance, _n = grid_axis(grid, dsn)

    conn = _connection(dsn)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT sample_id FROM {LIGHTCURVE_TABLE} WHERE grid = %s AND band = %s "
            f"ORDER BY sample_id LIMIT %s",
            [grid, band, n_check],
        )
        sample_ids = [r[0] for r in cur.fetchall()]
    if not sample_ids:
        raise ValueError(f"No rows for {grid}/{band} in {LIGHTCURVE_TABLE}")

    # --- database side -----------------------------------------------------
    blob = _copy_out(
        conn,
        f"SELECT sample_id, absmag FROM {LIGHTCURVE_TABLE} "
        f"WHERE grid = %s AND band = %s AND sample_id = ANY(%s) ORDER BY sample_id",
        [grid, band, sample_ids],
    )
    from_db = {
        struct.unpack(">i", sid)[0]: np.frombuffer(mag, dtype=DTYPE)
        for sid, mag in _iter_copy_rows(blob)
    }

    # --- parquet side ------------------------------------------------------
    scanner = pds.dataset(Path(path), format="parquet").scanner(
        columns=["sample_id", "time", "absolute_magnitude"],
        filter=(pc.field("band") == band)
        & pc.is_in(pc.field("sample_id"), value_set=pa.array(sample_ids)),
        batch_size=250_000,
        batch_readahead=1,
        fragment_readahead=1,
        use_threads=False,
    )
    from_file: dict = {}
    for batch in scanner.to_batches():
        if not batch.num_rows:
            continue
        sid = batch.column("sample_id").to_numpy(zero_copy_only=False)
        tim = batch.column("time").to_numpy(zero_copy_only=False)
        mag = batch.column("absolute_magnitude").to_numpy(zero_copy_only=False)
        for s in np.unique(sid):
            sel = sid == s
            order = np.argsort(tim[sel], kind="stable")
            prev = from_file.get(int(s))
            cur_vals = mag[sel][order].astype(DTYPE)
            from_file[int(s)] = cur_vals if prev is None else np.concatenate([prev, cur_vals])

    mismatched, missing, worst = [], [], 0.0
    for sid in sample_ids:
        a, b = from_db.get(sid), from_file.get(sid)
        if b is None:
            missing.append(sid)
            continue
        if a.size != b.size:
            mismatched.append((sid, f"{a.size} epochs in the store, {b.size} in the file"))
            continue
        both = np.isfinite(a) & np.isfinite(b)
        if not np.array_equal(np.isfinite(a), np.isfinite(b)):
            mismatched.append((sid, "NaN pattern differs"))
            continue
        if both.any():
            delta = float(np.abs(a[both] - b[both]).max())
            worst = max(worst, delta)
            if delta > 0:
                mismatched.append((sid, f"max |delta| = {delta:.6g}"))

    return {
        "grid": grid,
        "band": band,
        "checked": len(sample_ids),
        "n_time": int(axis.size),
        "missing_from_file": missing,
        "mismatched": mismatched,
        "max_abs_delta": worst,
        "ok": not mismatched and not missing,
    }
