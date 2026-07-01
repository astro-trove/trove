"""
Ingest a single IGWN gravitational-wave alert from local files or directly from GraceDB.

See https://github.com/astro-trove/trove/issues/5 — JSON alert packets alone cannot carry
skymap bytes; attach the FITS file before calling the same path as the live alert stream.

Two data sources are supported:
  * local files: an IGWN alert JSON plus a companion skymap FITS, and
  * GraceDB: the latest VOEvent XML for a superevent (e.g. S190814bv) plus its GW_SKYMAP,
    fetched from https://gracedb.ligo.org and reformatted into the same alert packet.
"""
from __future__ import annotations

import copy
import gzip
import json
import logging
import re
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from typing import Any

import healpy as hp
import requests
from astropy.table import Table
from hop.io import Metadata
from hop.models import JSONBlob
from ligo.skymap.bayestar import derasterize
from ligo.skymap.io import write_sky_map

from custom_code.alertstream_handlers import handle_message_and_send_alerts

logger = logging.getLogger(__name__)

_GZIP_MAGIC = b"\x1f\x8b"

GRACEDB_BASE_URL = "https://gracedb.ligo.org"
# Matches a versioned GraceDB VOEvent filename, e.g. "S190814bv-5-Update.xml"
# (deliberately excludes the ",N" file-revision aliases).
_VOEVENT_NAME_RE = re.compile(r"-(\d+)-[A-Za-z]+\.xml$")

# GraceDB superevent search queries (same syntax as the web UI).
#  * Production excludes MDC/Test events.
#  * GCN_PRELIM_SENT marks superevents for which a *significant* public GCN alert was
#    issued; it spans O3 and O4 (unlike the O4-only SIGNIF_LOCKED label), so it is the
#    cross-era way to select "significant=True" events.
GRACEDB_PRODUCTION_QUERY = "category: Production"
GRACEDB_SIGNIFICANT_QUERY = "category: Production label: GCN_PRELIM_SENT"


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


# --------------------------------------------------------------------------------------
# GraceDB source
# --------------------------------------------------------------------------------------


def _voe_localname(tag: str) -> str:
    """Strip any XML namespace from an element tag."""
    return tag.rsplit("}", 1)[-1]


def _voe_float(value: Any) -> float | None:
    """Best-effort float conversion for VOEvent Param values."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _voe_iso_utc(value: str | None) -> str | None:
    """Normalize a VOEvent ISO timestamp to a UTC string ending in 'Z'."""
    if not value:
        return None
    value = value.strip()
    if value.endswith("Z") or "+" in value:
        return value
    return value + "Z"


def latest_voevent_filename(file_names, superevent_id: str) -> str:
    """
    Return the highest-numbered VOEvent XML filename for a superevent.

    GraceDB names VOEvents ``<superevent_id>-<N>-<Type>.xml`` (e.g. ``S190814bv-5-Update.xml``);
    the largest ``N`` is the most recent. ``,N`` revision aliases are ignored.
    """
    prefix = f"{superevent_id}-"
    best = None  # (version, name)
    for name in file_names:
        if not name.startswith(prefix):
            continue
        match = _VOEVENT_NAME_RE.search(name)
        if not match:
            continue
        version = int(match.group(1))
        if best is None or version > best[0]:
            best = (version, name)
    if best is None:
        raise ValueError(
            f"No VOEvent XML (e.g. {superevent_id}-N-Update.xml) found for {superevent_id}"
        )
    return best[1]


def parse_voevent_xml(xml_bytes: bytes) -> tuple[dict[str, Any], str | None]:
    """
    Parse a GraceDB VOEvent into an IGWN-style alert dict and its skymap URL.

    Returns ``(alert, skymap_url)`` where ``alert`` matches the packet consumed by
    ``handle_igwn_message`` (minus the skymap bytes, which are attached separately).
    """
    root = ET.fromstring(xml_bytes)

    what = next((el for el in root.iter() if _voe_localname(el.tag) == "What"), None)
    params: dict[str, str] = {}
    groups: dict[str, dict[str, str]] = {}
    if what is not None:
        for child in what:
            ln = _voe_localname(child.tag)
            if ln == "Param":
                params[child.get("name")] = child.get("value")
            elif ln == "Group":
                # The semantic identity of an LVC group is its ``type`` (e.g. GW_SKYMAP,
                # Classification, Properties); ``name`` is more specific (e.g. the skymap
                # pipeline "LALInference.v1"), so prefer ``type`` for lookups.
                gname = child.get("type") or child.get("name")
                groups[gname] = {
                    p.get("name"): p.get("value")
                    for p in child
                    if _voe_localname(p.tag) == "Param"
                }

    who = next((el for el in root.iter() if _voe_localname(el.tag) == "Who"), None)
    time_created = None
    if who is not None:
        date_el = next((el for el in who.iter() if _voe_localname(el.tag) == "Date"), None)
        if date_el is not None:
            time_created = _voe_iso_utc(date_el.text)

    iso_el = next((el for el in root.iter() if _voe_localname(el.tag) == "ISOTime"), None)
    event_time = _voe_iso_utc(iso_el.text) if iso_el is not None else None

    superevent_id = params.get("GraceID")
    alert_type = (params.get("AlertType") or "").upper() or None

    instruments_raw = params.get("Instruments") or ""
    instruments = [i for i in (s.strip() for s in instruments_raw.split(",")) if i]

    significant = params.get("Significant")
    if significant is not None:
        significant = significant.strip().lower() in ("1", "true")

    classification = {k: _voe_float(v) for k, v in groups.get("Classification", {}).items()}
    properties = {k: _voe_float(v) for k, v in groups.get("Properties", {}).items()}
    skymap_url = groups.get("GW_SKYMAP", {}).get("skymap_fits") or params.get("skymap_fits")

    gracedb_url = params.get("EventPage")
    if not gracedb_url and superevent_id:
        gracedb_url = f"{GRACEDB_BASE_URL}/superevents/{superevent_id}/view/"

    event = None
    if alert_type != "RETRACTION":
        event = {
            "time": event_time,
            "far": _voe_float(params.get("FAR")),
            "significant": significant,
            "instruments": instruments,
            "group": params.get("Group"),
            "pipeline": params.get("Pipeline"),
            "search": params.get("Search"),
            "properties": properties,
            "classification": classification,
            "duration": _voe_float(params.get("Duration")),
            "central_frequency": _voe_float(params.get("CentralFreq")),
        }

    alert = {
        "alert_type": alert_type,
        "time_created": time_created,
        "superevent_id": superevent_id,
        "urls": {"gracedb": gracedb_url} if gracedb_url else {},
        "event": event,
        "external_coinc": None,
    }
    return alert, skymap_url


def fetch_gracedb_alert(
    superevent_id: str,
    *,
    base_url: str = GRACEDB_BASE_URL,
    session: Any = None,
    timeout: float = 60.0,
) -> tuple[dict[str, Any], bytes | None, str]:
    """
    Fetch the latest VOEvent + GW_SKYMAP for a superevent from GraceDB.

    Returns ``(alert, skymap_bytes, voevent_filename)``. ``skymap_bytes`` is the raw
    (uncompressed) FITS for the GW_SKYMAP, or ``None`` (e.g. for a retraction with no map).
    """
    sess = session or requests.Session()
    base = base_url.rstrip("/")

    files_url = f"{base}/api/superevents/{superevent_id}/files/"
    resp = sess.get(files_url, timeout=timeout)
    resp.raise_for_status()
    files = resp.json()

    voevent_name = latest_voevent_filename(files.keys(), superevent_id)
    voevent_url = files.get(voevent_name) or (
        f"{base}/api/superevents/{superevent_id}/files/{voevent_name}"
    )
    logger.info("Using GraceDB VOEvent %s for %s", voevent_name, superevent_id)
    xml_resp = sess.get(voevent_url, timeout=timeout)
    xml_resp.raise_for_status()

    alert, skymap_url = parse_voevent_xml(xml_resp.content)

    skymap_bytes = None
    if skymap_url:
        full_url = skymap_url if skymap_url.startswith("http") else f"{base}{skymap_url}"
        logger.info("Downloading GraceDB skymap %s", full_url)
        map_resp = sess.get(full_url, timeout=timeout)
        map_resp.raise_for_status()
        skymap_bytes = decompress_fits_bytes(map_resp.content)

    return alert, skymap_bytes, voevent_name


def iter_gracedb_superevents(
    query: str = GRACEDB_SIGNIFICANT_QUERY,
    *,
    base_url: str = GRACEDB_BASE_URL,
    session: Any = None,
    limit: int | None = None,
    page_size: int = 100,
    timeout: float = 60.0,
):
    """
    Yield superevent records matching a GraceDB search query, following pagination.

    Each yielded item is the raw superevent dict from ``/api/superevents/`` (keys include
    ``superevent_id``, ``category``, ``far``, ``labels``, ...). Pagination is done with
    explicit ``start``/``count`` so the ``query`` filter is preserved across pages.
    """
    sess = session or requests.Session()
    url = f"{base_url.rstrip('/')}/api/superevents/"
    start = 0
    total = None
    yielded = 0
    while True:
        params = {"count": page_size, "start": start}
        if query:
            params["query"] = query
        resp = sess.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if total is None:
            total = data.get("numRows", 0)
        batch = data.get("superevents", [])
        if not batch:
            break
        for superevent in batch:
            yield superevent
            yielded += 1
            if limit is not None and yielded >= limit:
                return
        start += len(batch)
        if total is not None and start >= total:
            break


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
    Classic flat ``bayestar.fits.gz`` files (NESTED + PROB, full-resolution) are converted
    with :func:`ligo.skymap.bayestar.derasterize`, the canonical inverse of the rasterization
    used to build flat BAYESTAR maps. It merges identical-valued sibling pixels back into
    coarse tiles, so the result is an *adaptive* multi-resolution grid (a manageable number of
    tiles, not one per full-resolution pixel) with ``PROBDENSITY`` correctly expressed in 1/sr.
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

    # derasterize / reconstruct_nested assume NESTED ordering; reorder a RING map if needed.
    ordering = table.meta.get("ORDERING", "NESTED")
    if isinstance(ordering, (list, tuple)):  # duplicate FITS cards come back as a list
        ordering = ordering[0] if ordering else "NESTED"
    ordering = str(ordering).upper()
    if ordering == "RING":
        for name in table.colnames:
            table[name] = hp.reorder(table[name], r2n=True)
        table.meta["ORDERING"] = "NESTED"
    elif ordering not in ("NESTED", "NUNIQ"):
        raise ValueError(f"Unsupported ORDERING {ordering!r} for skymap conversion")

    logger.info(
        "Converting flat BAYESTAR skymap (%d pixels) to adaptive multi-order UNIQ/PROBDENSITY",
        len(table),
    )

    converted = derasterize(table)
    logger.info("Adaptive multi-order skymap has %d tiles", len(converted))

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
