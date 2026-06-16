"""
Ingest a single IGWN gravitational-wave alert from local JSON + FITS skymap files.

See https://github.com/astro-trove/trove/issues/5 — JSON alert packets alone cannot carry
skymap bytes; attach the FITS file before calling the same path as the live alert stream.
"""
from __future__ import annotations

import copy
import gzip
import json
import logging
from io import BytesIO
from pathlib import Path
from typing import Any

import astropy_healpix as ah
import healpy as hp
import numpy as np
from astropy.table import Table
from hop.io import Metadata
from hop.models import JSONBlob
from ligo.skymap import moc
from ligo.skymap.io import write_sky_map

from custom_code.alertstream_handlers import handle_message_and_send_alerts

logger = logging.getLogger(__name__)

_GZIP_MAGIC = b"\x1f\x8b"


def decompress_fits_bytes(skymap_bytes: bytes) -> bytes:
    """
    Return raw FITS bytes suitable for ``Table.read(..., format='fits')``.

    IGWN Kafka alerts carry **uncompressed** FITS. Local ``*.fits.gz`` files (e.g.
    ``bayestar.fits.gz``) must be gunzipped first; passing gzip bytes to Astropy raises
    ``IORegistryError: Format could not be identified``.
    """
    if skymap_bytes[:2] == _GZIP_MAGIC:
        return gzip.decompress(skymap_bytes)
    return skymap_bytes


def read_skymap_table(skymap_bytes: bytes) -> Table:
    """Parse skymap bytes (gzip or raw FITS) into an Astropy table."""
    fits_bytes = decompress_fits_bytes(skymap_bytes)
    return Table.read(BytesIO(fits_bytes), format="fits")


def load_alert_dict(alert_json_path: str | Path) -> dict[str, Any]:
    """Load an IGWN alert JSON object from disk."""
    path = Path(alert_json_path)
    with path.open(encoding="utf-8") as handle:
        alert = json.load(handle)
    if not isinstance(alert, dict):
        raise ValueError(f"Expected a JSON object in {path}, got {type(alert).__name__}")
    return alert


def load_skymap_bytes(skymap_fits_path: str | Path) -> bytes:
    """
    Read a skymap file and return **uncompressed** FITS bytes (same as IGWN alerts).

    Accepts ``.fits``, ``.fits.gz``, and other paths; gzip is detected by magic bytes.
    """
    raw = Path(skymap_fits_path).read_bytes()
    return decompress_fits_bytes(raw)


def attach_skymap_to_alert(
    alert: dict[str, Any],
    skymap_bytes: bytes,
    *,
    combined: bool = False,
) -> dict[str, Any]:
    """
    Return a copy of ``alert`` with skymap bytes attached.

    By default sets ``alert['event']['skymap']``. When ``combined=True``, sets
    ``alert['external_coinc']['combined_skymap']`` (creating ``external_coinc`` if needed).
    """
    alert = copy.deepcopy(alert)
    if combined:
        external = alert.setdefault("external_coinc", {})
        external["combined_skymap"] = skymap_bytes
    else:
        event = alert.setdefault("event", {})
        event["skymap"] = skymap_bytes
    return alert


def ensure_multiorder_skymap_bytes(skymap_bytes: bytes) -> bytes:
    """
    Ensure skymap bytes are in multi-order HEALPix (UNIQ + PROBDENSITY) form.

    ``create_localization_for_skymap`` expects GraceDB-style ``*.multiorder.fits`` tables.
    Classic ``bayestar.fits.gz`` files (NESTED + PROB, full-resolution) are converted to
    multi-order UNIQ indexing in memory (same requirement as GraceDB ``bayestar.multiorder.fits``).
    """
    fits_bytes = decompress_fits_bytes(skymap_bytes)
    table = Table.read(BytesIO(fits_bytes), format="fits")

    if "UNIQ" in table.colnames and "PROBDENSITY" in table.colnames:
        return fits_bytes

    if "PROB" not in table.colnames:
        raise ValueError(
            "Skymap must contain UNIQ/PROBDENSITY (multi-order) or PROB (classic bayestar); "
            f"found columns: {table.colnames}"
        )

    ordering = (table.meta.get("ORDERING") or "").upper()
    if ordering not in ("NESTED", "NUNIQ", ""):
        raise ValueError(f"Unsupported ORDERING {ordering!r} for skymap conversion")

    logger.info(
        "Converting classic BAYESTAR skymap (NSIDE=%s, %d pixels) to multi-order UNIQ/PROBDENSITY",
        table.meta.get("NSIDE"),
        len(table),
    )

    nside = int(table.meta["NSIDE"])
    order = ah.nside_to_level(nside)
    npix = len(table)
    ipix = np.arange(npix, dtype=np.int64)
    uniq = moc.nest2uniq(order, ipix)
    probdensity = table["PROB"] / hp.nside2pixarea(nside)

    columns = [uniq, probdensity]
    names = ["UNIQ", "PROBDENSITY"]
    for extra in ("DISTMU", "DISTSIGMA", "DISTNORM"):
        if extra in table.colnames:
            columns.append(table[extra])
            names.append(extra)

    converted = Table(columns, names=names)
    converted.meta.update(table.meta)

    buffer = BytesIO()
    write_sky_map(buffer, converted, nest=True)
    buffer.seek(0)
    return buffer.read()


def build_hop_message(alert: dict[str, Any]) -> JSONBlob:
    """Wrap an IGWN alert dict in a HOP ``JSONBlob`` (``content[0]`` is the alert)."""
    return JSONBlob(content=[alert])


def ingest_local_igwn_alert(
    alert: dict[str, Any],
    metadata: Metadata | None = None,
):
    """
    Ingest one alert through the same handler as the IGWN Kafka stream.

    Returns ``(NonLocalizedEvent, EventSequence)`` or ``(None, None)`` for skipped test alerts.
    """
    message = build_hop_message(alert)
    return handle_message_and_send_alerts(message, metadata)


def upload_local_nle(
    alert_json_path: str | Path,
    skymap_fits_path: str | Path,
    metadata: Metadata | None = None,
    *,
    convert_skymap: bool = True,
):
    """
    Load a local IGWN alert JSON + skymap FITS and ingest one NonLocalizedEvent.

    Parameters
    ----------
    alert_json_path:
        Path to the GraceDB / IGWN update JSON (without embedded skymap bytes).
    skymap_fits_path:
        Path to the companion skymap (e.g. ``bayestar.fits.gz`` or ``*.multiorder.fits``).
    metadata:
        Optional HOP metadata (unused by ``handle_igwn_message`` today; pass ``None``).
    convert_skymap:
        When True (default), convert classic bayestar FITS to multi-order format if needed.

    Returns
    -------
    tuple
        ``(NonLocalizedEvent, EventSequence)`` or ``(None, None)`` when the alert is skipped.
    """
    alert = load_alert_dict(alert_json_path)
    skymap_bytes = load_skymap_bytes(skymap_fits_path)
    if convert_skymap:
        skymap_bytes = ensure_multiorder_skymap_bytes(skymap_bytes)
    alert = attach_skymap_to_alert(alert, skymap_bytes)
    return ingest_local_igwn_alert(alert, metadata)
