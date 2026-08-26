from __future__ import annotations

import logging
from typing import List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from django.db.models import Exists, OuterRef

from .models import GRID_DTYPE, KnGridAxis, KnGridLightcurve

logger = logging.getLogger(__name__)

DTYPE = np.dtype(GRID_DTYPE)

_MAX_ABS_MAG = 0.0

# ---------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------
def scoreable_grids() -> List[Tuple[str, float, float]]:
    # Just queries a small KnGridAxis which is only 19 rows, and 
    # primarily contains meta-data and the time axis, which is much smaller
    # loading the full light curves

    # Checks whether the grid has an associated KnGridLightcurve
    has_lightcurves = KnGridLightcurve.objects.filter(grid=OuterRef("grid"))
    out = []
    for axis in (KnGridAxis.objects
                 .annotate(has_lc=Exists(has_lightcurves))
                 .filter(has_lc=True)
                 .only("grid", "distance_mpc", "time_axis")):
        epochs = axis.epochs
        out.append((axis.grid, float(axis.distance_mpc),
                    float(epochs[-1]) if epochs.size else float("nan")))
    return out

# Although this is relatively small, it is run per candidate, so it is useful to cache
# Saves 2 minutes from the full run
_AXIS_CACHE: dict = {}

def grid_axis(grid: str) -> Tuple[np.ndarray, float]:
    hit = _AXIS_CACHE.get(grid)
    if hit is not None:
        axis, distance = hit
        return axis.copy(), distance

    row = (
        KnGridAxis.objects.filter(grid=grid)
        .values_list("time_axis", "distance_mpc")
        .first()
    )
    if row is None:
        raise FileNotFoundError(
            f"No grid named {grid!r} in {KnGridAxis._meta.db_table}. "
            "Build one with\n"
            f"    KilonovaSCORER_generator/generate_rung.py --distance <Mpc>\n"
            f"or list what is there with `scoreable_grids()`."
        )
    axis_blob, distance = row
    axis = np.frombuffer(bytes(axis_blob), dtype=DTYPE)
    _AXIS_CACHE[grid] = (axis, float(distance))
    return axis.copy(), float(distance)


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


def _lightcurve_query(grid: str, bands: Optional[Sequence[str]]):
    qs = KnGridLightcurve.objects.filter(grid=grid)
    if bands:
        qs = qs.filter(band__in=list(bands))
    return (
        qs.order_by("band", "sample_id")
        .values_list("band", "sample_id", "absmag")
    )


def _fetch_lightcurves(
    grid: str, bands: Optional[Sequence[str]], lo: int, hi: int, n_time: int
) -> Tuple[List[str], List[int], List[np.ndarray]]:
    band_of: List[str] = []
    sample_of: List[int] = []
    mags: List[np.ndarray] = []

    for band, sample_id, blob in _lightcurve_query(grid, bands).iterator():
        full = np.frombuffer(blob, dtype=DTYPE)
        if full.size != n_time:
            raise ValueError(
                f"Grid {grid} has a lightcurve of {full.size} epochs where its "
                f"axis is {n_time}; the store is inconsistent with "
                f"{KnGridAxis._meta.db_table}."
            )
        band_of.append(band)
        sample_of.append(int(sample_id))
        mags.append(full[lo:hi])

    return band_of, sample_of, mags


def load_grid_db(
    grid: str,
    bands: Optional[Sequence[str]] = None,
    min_time: float = 0.0,
    max_time: Optional[float] = None,
) -> pd.DataFrame:
    axis, distance = grid_axis(grid)

    lo = int(np.searchsorted(axis, np.float32(min_time), side="right"))
    hi = axis.size if max_time is None else int(
        np.searchsorted(axis, np.float32(max_time), side="right")
    )
    if hi <= lo:
        raise ValueError(
            f"Grid {grid} has no epochs in ({min_time}, {max_time}]; its axis "
            f"spans [{axis[0]:.3f}, {axis[-1]:.3f}] d"
        )

    band_of, sample_ids, mags = _fetch_lightcurves(grid, bands, lo, hi, axis.size)
    if not mags:
        raise ValueError(
            f"Grid {grid} returned no rows for bands={list(bands or [])}, "
            f"max_time={max_time}"
        )

    n_lc, n_sel = len(mags), hi - lo
    absolute_magnitude = np.concatenate(mags)
    del mags
    time = np.tile(axis[lo:hi], n_lc)
    sample_id = np.repeat(np.asarray(sample_ids, dtype=np.int32), n_sel)

    lc_codes, band_names = pd.factorize(np.asarray(band_of))
    codes = np.repeat(lc_codes.astype(np.int32), n_sel)

    keep = np.isfinite(absolute_magnitude) & (absolute_magnitude <= np.float32(_MAX_ABS_MAG))
    if not keep.any():
        raise ValueError(f"Grid {grid} has no usable rows after filtering")
    if not keep.all():
        logger.info("Grid %s: dropping %d artifact row(s) with M>%g",
                    grid, int((~keep).sum()), _MAX_ABS_MAG)
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
            "filter_mapped": band,
        },
        copy=False,
    )
    df.attrs["name"] = grid
    df.attrs["distance_mpc"] = distance

    logger.info(
        "Loaded %s: %d rows, %d lightcurves, t=[%.3f, %.2f] d, "
        "M=[%.2f, %.2f], D_L=%.0f Mpc, %.0f MB in memory",
        grid, len(df), n_lc,
        df["time"].min(), df["time"].max(),
        df["absolute_magnitude"].min(), df["absolute_magnitude"].max(),
        distance, df.memory_usage(index=True, deep=False).sum() / 1e6,
    )
    return df
