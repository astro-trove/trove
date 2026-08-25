"""grid_db.py -- the Postgres backend for simulation grids.

Grids live in two Postgres tables, written straight from the simulator and read
back here. There is no file format and no import step:

    kn_grid_axis        one row per grid: the shared time axis, the distance
    kn_grid_lightcurve  one row per (grid, band, sample_id): n_time magnitudes

:func:`load_grid_db` returns what :func:`.grids.load_grid` hands the scorer --
the same columns, dtypes and filters the Parquet reader used to produce, which
is why removing that reader changed nothing downstream.

This is a **standalone local database**, addressed by DSN, not one of Django's
``DATABASES`` entries -- so there is no migration, nothing to apply to TROVE's
own database, and no Django import at module level (:func:`grid_dsn`
imports ``django.conf`` lazily, and works fine without it).
:func:`ensure_schema` creates the two tables on demand. A rung is ~2 GB of pure
simulation output: it is not TROVE data, it does not belong in TROVE's
migration history, and keeping it separate means the grid store can be dropped
and rebuilt without touching anything else.

Which database is read comes from :func:`grid_dsn` -- ``TROVE_GRID_DSN`` in
``trove_tom/settings_local.py`` for a deployment, or the environment variable of
the same name to override it for a single process::

    TROVE_GRID_DSN = 'postgresql://trove:PW@localhost:5432/catalogs'  # settings
    export TROVE_GRID_DSN='postgresql://trove@127.0.0.1:5433/kn_grids'  # one-off

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

def grid_dsn() -> Optional[str]:
    """Connection string for the grid database, or None if none is configured.

    Two sources, environment first::

        TROVE_GRID_DSN=postgresql://trove@127.0.0.1:5433/kn_grids   # per process
        TROVE_GRID_DSN = "postgresql://trove:...@localhost:5432/catalogs"
                                                    # trove_tom/settings_local.py

    **Settings is the one to use in production.** A queue worker is a separate
    process that does not inherit the shell which enqueued its task, so an
    environment variable has to be injected into every unit separately and is
    silently absent if one is missed -- and a worker without a DSN dies on its
    first task. ``settings_local.py`` is read by every process that boots
    Django, is gitignored, and is already where the other credentials live.

    The environment still wins when set, so a one-off run can point at a
    different store (a local copy, a new rung) without editing settings. That
    is the safe precedence: forgetting the override falls back to the
    production value rather than to nothing.

    Django is imported lazily and its absence is not an error -- this module is
    deliberately usable from a plain script with no framework configured, which
    is how ``simulation.py`` writes grids in the ``kn-sim`` environment.

    Resolved per call, not once at import: a module-level constant captured the
    environment as it was when the first import happened, which made the value
    depend on import order and impossible to override afterwards.

    No default anywhere. A grid is gigabytes, and a backend that quietly
    connected to TROVE's own database would look for grids somewhere nobody
    asked for -- and, worse, could be handed write traffic there.
    """
    dsn = os.environ.get("TROVE_GRID_DSN")
    if dsn:
        return dsn
    try:
        from django.conf import settings

        return getattr(settings, "TROVE_GRID_DSN", None) or None
    except Exception:  # noqa: BLE001 - Django absent or not configured
        return None

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

    dsn = dsn or grid_dsn()
    if not dsn:
        raise RuntimeError(
            "No grid database configured. Set TROVE_GRID_DSN either in\n"
            "  trove_tom/settings_local.py  (preferred -- every process that boots\n"
            "  Django picks it up, including queue workers), e.g.\n"
            "      TROVE_GRID_DSN = 'postgresql://trove:PW@localhost:5432/catalogs'\n"
            "or in the environment, which overrides it for one process:\n"
            "      export TROVE_GRID_DSN='postgresql://trove@127.0.0.1:5433/kn_grids'\n"
            "It is the only place grids live; there is no file-based fallback."
        )

    if _CONN is not None and _CONN[0] == dsn and not _CONN[1].closed:
        return _CONN[1]

    import psycopg2

    # Timeouts and keepalives, because the grid store is now reachable over a
    # link that can die mid-run. Without them a black-holed TCP connection
    # blocks forever on read: a dropped SSH tunnel once hung a scoring run for
    # 26 minutes before anyone noticed. Fail fast instead -- a clear error is
    # recoverable, a silent hang is not.
    #
    # Passed as keyword arguments, which psycopg2 merges into either DSN form
    # (URI or key=value). Anything the caller already set in the DSN is left
    # alone.
    defaults = {
        "connect_timeout": 15,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    }
    conn = psycopg2.connect(dsn, **{k: v for k, v in defaults.items() if k not in dsn})
    conn.autocommit = True  # bulk COPY, no transaction to hold open
    _CONN = (dsn, conn)
    return conn


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
    :class:`.GridRef` rather than a filesystem path), ``distance_mpc``
    and ``size_mb`` -- so callers that only read those keep working.

    An inner join, deliberately: a grid is only "available" if it actually has
    lightcurves. An axis row on its own is a half-finished or abandoned ingest,
    and listing it lets a caller route candidates to a grid that cannot be read
    -- which is not a clean failure but one ``FileNotFoundError`` per candidate,
    deep inside the scoring loop. Seen for real: a stray axis row at the same
    distance as the good grid took roughly half an event's candidates with it.
    """
    from . import GridRef

    conn = _connection(dsn)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT a.grid, a.distance_mpc, a.n_samples, a.n_time, count(l.*)
              FROM {AXIS_TABLE} a
              JOIN {LIGHTCURVE_TABLE} l ON l.grid = a.grid
             GROUP BY a.grid, a.distance_mpc, a.n_samples, a.n_time
            HAVING count(l.*) > 0
             ORDER BY a.distance_mpc
            """
        )
        rows = cur.fetchall()

    # The last epoch of each grid, so a caller can tell a 10-day rung from a
    # 30-day one. Decoding costs nothing here -- the axis is 4 KB and there are
    # a handful of grids -- and without it selection has no way to know that
    # two rungs at the same distance are not interchangeable.
    spans = {}
    with conn.cursor() as cur:
        cur.execute(f"SELECT grid, time_axis FROM {AXIS_TABLE}")
        for name, blob in cur.fetchall():
            axis = np.frombuffer(bytes(blob), dtype=DTYPE)
            spans[name] = (float(axis[0]), float(axis[-1])) if axis.size else (np.nan, np.nan)

    return pd.DataFrame(
        [
            {
                "path": GridRef(
                    name=name,
                    distance_mpc=float(dist),
                    backend="postgres",
                    t_max=spans.get(name, (np.nan, np.nan))[1],
                ),
                "distance_mpc": float(dist),
                # Computed, not measured. `SUM(pg_column_size(absmag))` is exact
                # but walks every lightcurve row and resolves each TOAST pointer
                # -- 3.15 s against the 380,000-row rung in `catalogs`, for a
                # column nothing but display reads. Every blob in this layout is
                # exactly n_time float32s, so the arithmetic is the same number.
                "size_mb": float(n_lightcurves) * int(n_time) * DTYPE.itemsize / 1e6,
                "n_samples": int(n_samples),
                "n_time": int(n_time),
                "t_min": spans.get(name, (np.nan, np.nan))[0],
                "t_max": spans.get(name, (np.nan, np.nan))[1],
            }
            for name, dist, n_samples, n_time, n_lightcurves in rows
        ],
        columns=["path", "distance_mpc", "size_mb", "n_samples", "n_time", "t_min", "t_max"],
    )


#: Axis rows already fetched, keyed by ``(dsn, grid)``. An axis is written once
#: by the ingest and never updated, so it cannot go stale under a running score.
_AXIS_CACHE: dict = {}


def grid_axis(grid: str, dsn: Optional[str] = None) -> Tuple[np.ndarray, float, int]:
    """``(time_axis, distance_mpc, n_samples)`` for one grid.

    Memoised. This is four kilobytes, but ``grids.resolve_grid`` calls it to turn
    a pinned grid name into a ``GridRef`` and that happens once per *candidate*:
    445 round trips at the 78.8 ms latency of the tunnel to ``catalogs`` measured
    34 s, a quarter of the run, to fetch the same row 445 times.
    """
    key = (dsn or grid_dsn(), grid)
    hit = _AXIS_CACHE.get(key)
    if hit is not None:
        axis, distance, n_samples = hit
        # a copy per call: np.frombuffer gives a read-only view, but a cached
        # array handed to many callers is a mutation waiting to happen
        return axis.copy(), distance, n_samples

    conn = _connection(dsn)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT time_axis, distance_mpc, n_samples FROM {AXIS_TABLE} WHERE grid = %s",
            [grid],
        )
        row = cur.fetchone()
    if row is None:
        raise FileNotFoundError(
            f"No grid named {grid!r} in {AXIS_TABLE}. Build one with\n"
            f"    KilonovaScorer/generate_rung.py --distance <Mpc>\n"
            f"or list what is there with `KilonovaScorer.grids.available_grids()`."
        )
    axis_blob, distance, n_samples = row
    axis = np.frombuffer(bytes(axis_blob), dtype=DTYPE)
    _AXIS_CACHE[key] = (axis, float(distance), int(n_samples))
    return axis.copy(), float(distance), int(n_samples)


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
            from . import FILTER_LOOKUP

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
# writing a grid straight from the simulator
#
# These replace the Parquet round-trip. The simulator used to stream row groups
# to a file which a separate `ingest_kn_grid` pass then folded into this schema;
# now it calls `begin_grid` once and `write_lightcurves` per chunk, and there is
# no intermediate artifact at all.
#
# The chunked write works because a lightcurve is the unit of both the
# simulation and the schema: a worker evaluates one sample across the WHOLE time
# axis, so every row it produces is final. A chunk of 500 samples is 500
# complete rows per band, and the primary key (grid, band, sample_id) is
# satisfied on arrival. Nothing has to be accumulated and no row is ever
# updated, which is what keeps peak memory at one chunk (~76 MB at 500 x 38 x
# 1,000) instead of the 1.5 GB a whole-grid buffer would need.
# ---------------------------------------------------------------------------


def begin_grid(
    grid: str,
    time_axis: np.ndarray,
    distance_mpc: float,
    n_samples: int,
    replace: bool = True,
    dsn: Optional[str] = None,
) -> None:
    """Register ``grid`` and clear any previous contents.

    Call once before the first :func:`write_lightcurves`. With
    ``replace=True`` (the default) every existing lightcurve for this grid is
    deleted, so a re-run never mixes rows from two simulations -- the danger
    being that a shorter re-run would otherwise leave the tail of the old one
    behind, with no way to tell from the data which sample came from where.
    """
    axis = np.asarray(time_axis, dtype=DTYPE)
    conn = _connection(dsn)
    with conn.cursor() as cur:
        if replace:
            cur.execute(f"DELETE FROM {LIGHTCURVE_TABLE} WHERE grid = %s", [grid])
            if cur.rowcount:
                logger.info("Replacing grid %s: deleted %d row(s)", grid, cur.rowcount)
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
            [grid, axis.tobytes(), float(distance_mpc), int(n_samples), int(axis.size)],
        )
    # the one moment an axis can change; drop it wholesale rather than guess
    # which DSN key this connection was opened under
    _AXIS_CACHE.clear()


def write_lightcurves(
    grid: str,
    band: str,
    sample_ids: Sequence[int],
    block: np.ndarray,
    dsn: Optional[str] = None,
) -> int:
    """Append one chunk of complete lightcurves for a single band.

    ``block`` is ``(len(sample_ids), n_time)``; row *i* is the full lightcurve
    of ``sample_ids[i]``. Cast to float32 here rather than trusting the caller,
    because the read path does a bare ``np.frombuffer(..., dtype=DTYPE)`` and a
    float64 block would come back as garbage of half the length rather than as
    an error.
    """
    block = np.ascontiguousarray(block, dtype=DTYPE)
    if block.ndim != 2 or block.shape[0] != len(sample_ids):
        raise ValueError(
            f"block shape {block.shape} does not match {len(sample_ids)} sample id(s)"
        )
    conn = _connection(dsn)
    _copy_in(
        conn,
        LIGHTCURVE_TABLE,
        ("grid", "band", "sample_id", "absmag"),
        _encode_rows(
            (grid, band, int(sid), block[i].tobytes())
            for i, sid in enumerate(sample_ids)
        ),
    )
    return len(sample_ids)


def grid_exists(grid: str, dsn: Optional[str] = None) -> bool:
    """Whether ``grid`` is registered and has at least one lightcurve."""
    try:
        conn = _connection(dsn)
    except RuntimeError:
        return False
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT EXISTS (SELECT 1 FROM {LIGHTCURVE_TABLE} WHERE grid = %s)", [grid]
        )
        return bool(cur.fetchone()[0])


def drop_grid(grid: str, dsn: Optional[str] = None) -> int:
    """Remove a grid entirely. Returns the number of lightcurves deleted."""
    conn = _connection(dsn)
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {LIGHTCURVE_TABLE} WHERE grid = %s", [grid])
        n = cur.rowcount
        cur.execute(f"DELETE FROM {AXIS_TABLE} WHERE grid = %s", [grid])
    _AXIS_CACHE.clear()
    return n
