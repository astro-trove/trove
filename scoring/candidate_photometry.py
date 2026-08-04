"""
Photometry for the candidate counterparts of a gravitational-wave event.

Walks NonLocalizedEvent -> EventCandidate -> Target -> ReducedDatum and returns
a tidy long-format light-curve table, plus helpers to reshape it into the form
KilonovaSCORER's ``load_observations`` expects.

This is the data layer for :mod:`scoring.kilonova_scoring`. It is a plain
importable module -- Django must already be configured, which it is for
anything running inside the app (views, management commands, tasks). For
standalone/CLI use see ``scripts/get_gw_photometry.py``, which bootstraps
Django and then imports from here.

See scripts/README_gw_photometry.md for how the data model fits together and
for the caveats baked into the filter and distance handling.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

from astropy.time import Time
from tom_dataproducts.models import ReducedDatum
from tom_nonlocalizedevents.models import (
    EventCandidate,
    NonLocalizedEvent,
)

# Reused from the vetting code so filter names and S/N are defined identically
# here and in the scores TROVE already reports.
from custom_code.templatetags.photometry_extras import error_to_snr
from scoring.vet_phot import standardize_filter_names

#: ``ReducedDatum.data_type`` value that flags a row as photometry (as opposed
#: to 'spectroscopy'). Matches ``settings.DATA_PRODUCT_TYPES['photometry'][0]``.
PHOTOMETRY_DATA_TYPE = "photometry"

#: 3-sigma magnitude uncertainty. Used to replace a reported error of exactly 0,
#: which is what several brokers write when they do not report an error at all.
DEFAULT_MAGERR = 2.5 / (3 * np.log(10))

#: Map a TROVE filter name (lower-cased, after
#: :func:`~scoring.vet_phot.standardize_filter_names` strips the survey suffix)
#: onto the canonical band names KilonovaSCORER models: g/r/i/z-band. This is
#: the ``FILTER_LOOKUP`` the KilonovaSCORER notebook expects you to supply.
#:
#: Bands with no g/r/i/z counterpart (u, y, JWST/Roman filters, 'clear', ...)
#: map to NaN and are dropped by :func:`to_scorer_frame` -- KilonovaSCORER has
#: no model for them.
#: Keys are matched against the *raw* filter string by longest '-'-prefix (see
#: :func:`match_band`), so 'orange-ATLAS' hits 'orange' and 'BG-q-BlackGem'
#: hits 'BG-q'. Matching on the raw string rather than the standardized one
#: matters: ``standardize_filter_names('BG-q-BlackGem')`` returns 'BG', the
#: *telescope* prefix, which throws away which BlackGem band it was.
#:
#: The lookup is CASE-SENSITIVE, unlike the ``.str.lower().map(...)`` in the
#: KilonovaSCORER notebook: TROVE filter names distinguish 'g' (SDSS) from 'G'
#: (Gaia), 'r' from 'R' (Cousins), and 'i' from 'I', so lower-casing first would
#: silently merge different bandpasses.
FILTER_LOOKUP = {
    # exact
    "g": "g-band",
    "r": "r-band",
    "i": "i-band",
    "z": "z-band",
    "BG-g": "g-band",  # BlackGem g
    "BG-i": "i-band",  # BlackGem i
    # APPROXIMATE -- non-SDSS bands with no one-to-one counterpart, mapped to
    # their nearest neighbour by effective wavelength so the (large) ATLAS and
    # Johnson-Cousins samples are not thrown away. ATLAS cyan spans g+r and
    # ATLAS orange spans r+i, so those two in particular are a real systematic,
    # not a relabeling. Delete these entries for a photometrically clean sample.
    "c": "g-band",       # ATLAS cyan
    "cyan": "g-band",    # ...same filter, spelled out by the TNS ingestor
    "o": "r-band",       # ATLAS orange
    "orange": "r-band",  # ...same filter, spelled out by the TNS ingestor
    "V": "g-band",       # Johnson V (547nm; nearly equidistant from g and r)
    "R": "r-band",       # Cousins R
    "I": "i-band",       # Cousins I
    "G": "r-band",       # Gaia G
    "w": "r-band",       # Pan-STARRS w
}

#: Unfiltered or very wide bands, where assigning an SDSS band is a judgement
#: call rather than an approximation: these span two or more SDSS bands and
#: their zero-points depend on how each pipeline calibrated them. Merged into
#: :data:`FILTER_LOOKUP` only when you opt in (``map_wide_bands=True`` /
#: ``--map-wide-bands``); otherwise these points are dropped. In the production
#: database they are ~1.5% of candidate photometry.
WIDE_BAND_LOOKUP = {
    "Clear": "r-band",  # unfiltered, mostly SAGUARO pipeline
    "L": "g-band",      # GOTO L, 400-700nm
    "BG-q": "g-band",   # BlackGem q, 440-720nm
    "wide": "r-band",   # ATLAS wide
}

# ---------------------------------------------------------------------------
# Per-survey bandpass matching
#
# The canonical g/r/i/z mapping above forces every observation through an
# approximation: ATLAS cyan/orange straddle SDSS bands, GOTO L and PS1 w are
# wide, Gaia G is very wide. ATLAS alone is 87% of TROVE's candidate photometry,
# so that approximation was the single largest uncontrolled systematic in the
# score.
#
# The simulation grid is now generated in the REAL survey bandpasses (sncosmo
# ids), so an observation can be compared against a simulation through the same
# filter and the approximation disappears. Matching needs the source as well as
# the filter name, because 'g' alone is ambiguous -- ZTF g, SDSS g and LSST g
# are different bandpasses.
# ---------------------------------------------------------------------------

#: Map a ``ReducedDatum.source_name`` onto a photometric system. Checked in
#: order; first match wins.
_SOURCE_SYSTEM_PATTERNS: tuple = (
    ("atlas", "atlas"),
    ("ztf", "ztf"),
    ("p48", "ztf"),          # P48 is ZTF's telescope, as reported via TNS
    ("pan-starrs", "ps1"),
    ("ps1", "ps1"),
    ("ps2", "ps1"),
    ("ps ", "ps1"),
    ("rubin", "lsst"),
    ("lsst", "lsst"),
    ("goto", "goto"),
    ("gaia", "gaia"),
    ("2mass", "2mass"),
    ("wfc3", "hst"),
    ("hubble", "hst"),
    ("hst", "hst"),
)

#: (system, filter) -> sncosmo bandpass id.
_SYSTEM_BANDS: dict = {
    "atlas": {"c": "atlasc", "cyan": "atlasc", "o": "atlaso", "orange": "atlaso"},
    "ztf": {"g": "ztfg", "r": "ztfr", "i": "ztfi"},
    "ps1": {"g": "ps1::g", "r": "ps1::r", "i": "ps1::i", "z": "ps1::z",
            "y": "ps1::y", "w": "ps1::w"},
    "lsst": {"u": "lsstu", "g": "lsstg", "r": "lsstr", "i": "lssti",
             "z": "lsstz", "y": "lssty"},
    "goto": {"L": "gotol", "B": "gotob", "G": "gotog", "R": "gotor",
             "g": "gotog", "r": "gotor", "b": "gotob", "l": "gotol"},
    "gaia": {"G": "gaia::g"},
    "2mass": {"J": "2massj", "H": "2massh", "K": "2massks", "Ks": "2massks"},
    "hst": {"F110W": "f110w", "F125W": "f125w", "F160W": "f160w"},
}

#: Fallback when the source does not identify a system. Filters that name their
#: instrument unambiguously (ATLAS c/o, Gaia G, GOTO L, PS1 w) resolve exactly;
#: bare SDSS-style letters default to the SDSS bandpasses, which is the closest
#: thing to a generic optical system.
_DEFAULT_BANDS: dict = {
    "u": "sdssu", "g": "sdssg", "r": "sdssr", "i": "sdssi", "z": "sdssz",
    "U": "bessellux", "B": "bessellb", "V": "bessellv", "R": "bessellr",
    "I": "besselli",
    "c": "atlasc", "cyan": "atlasc", "o": "atlaso", "orange": "atlaso",
    "G": "gaia::g", "L": "gotol", "w": "ps1::w", "y": "ps1::y",
    "J": "2massj", "H": "2massh", "K": "2massks", "Ks": "2massks",
    "F110W": "f110w", "F125W": "f125w", "F160W": "f160w",
}

#: Wide/unfiltered bands with no true bandpass counterpart. Opt-in via
#: ``map_wide_bands`` -- assigning them is a judgement call, not a lookup.
#: ATLAS 'wide' (TDO) is a broader version of the ATLAS filters; BlackGem q
#: (440-720nm) and SAGUARO 'Clear' are closest to the wide PS1 w.
_WIDE_BANDPASSES: dict = {
    "wide": "atlaso",
    "BG-q": "ps1::w",
    "BG-g": "sdssg",
    "BG-i": "sdssi",
    "Clear": "ps1::w",
}


def photometric_system(source: Optional[str]) -> Optional[str]:
    """Photometric system implied by a ``ReducedDatum.source_name``."""
    if not source:
        return None
    low = str(source).lower()
    for needle, system in _SOURCE_SYSTEM_PATTERNS:
        if needle in low:
            return system
    return None


def match_bandpass(
    filter_raw: str,
    source: Optional[str] = None,
    map_wide_bands: bool = False,
) -> Optional[str]:
    """sncosmo bandpass id for a TROVE filter, or None if there is no match.

    Resolution order:

    1. the survey's own table, when ``source`` identifies one (so ZTF 'g'
       becomes ``ztfg`` rather than a generic SDSS g);
    2. the filter-only default table, which resolves instrument-specific names
       (ATLAS ``o``/``orange``, Gaia ``G``, GOTO ``L``, PS1 ``w``) exactly and
       falls back to SDSS/Bessell for bare letters;
    3. optionally the wide/unfiltered table.

    Matching uses the same longest-``-``-prefix rule as :func:`match_band`, so
    'g-ZTF', 'orange-ATLAS' and 'BG-q-BlackGem' all resolve.
    """
    lookup = dict(_DEFAULT_BANDS)
    system = photometric_system(source)
    if system:
        lookup.update(_SYSTEM_BANDS.get(system, {}))
    if map_wide_bands:
        lookup.update(_WIDE_BANDPASSES)
    return match_band(filter_raw, lookup)


#: Columns KilonovaSCORER's ``load_observations`` reads off a CSV, plus the two
#: it derives (``time_after_gw``) or that you must supply (``filter_mapped``).
SCORER_COLUMNS = [
    "time",             # MJD
    "magnitude",
    "e_magnitude",
    "band",             # raw-ish filter name
    "filter_mapped",    # canonical band, required before scoring
    "time_after_gw",    # days since the GW trigger (load_observations recomputes)
]

#: Column order of the returned DataFrame.
COLUMNS = [
    "event_id",
    "candidate_id",
    "target_id",
    "target_name",
    "ra",
    "dec",
    "mwebv",
    "mjd",
    "dt",
    "filter",
    "filter_raw",
    "mag",
    "magerr",
    "snr",
    "upperlimit",
    "source",
    "telescope",
    "reduceddatum_id",
]


# ---------------------------------------------------------------------------
# Event / candidate lookup
# ---------------------------------------------------------------------------
def get_event(event_id: str) -> NonLocalizedEvent:
    """Look up a ``NonLocalizedEvent`` by its GraceDB-style superevent name.

    ``event_id`` is the human-readable name (``'S250818k'``, ``'GW170817'``),
    which is what the TROVE UI shows -- *not* the database primary key.
    """
    return NonLocalizedEvent.objects.get(event_id=event_id)


def get_event_t0_mjd(event: NonLocalizedEvent) -> float:
    """Return the GW trigger time in MJD.

    Each alert TROVE receives for a superevent is stored as an ``EventSequence``
    (preliminary -> initial -> update -> ...), numbered by ``sequence_id``, with
    the raw alert payload in ``details``. Every ingestor writes the trigger time
    to ``details['time']`` as an ISO-8601 string, so we walk the sequences from
    newest to oldest and take the first usable one. Retraction notices can carry
    a ``details`` payload without a time, hence the loop rather than ``.last()``.
    """
    for seq in event.sequences.order_by("-sequence_id"):
        t = (seq.details or {}).get("time")
        if t:
            return Time(t).mjd
    raise ValueError(
        f"No EventSequence for {event.event_id} has a trigger time in details['time']"
    )


def get_candidates(
    event: NonLocalizedEvent,
    viable_only: bool = False,
    target_names: Optional[Iterable[str]] = None,
):
    """Return the ``EventCandidate`` queryset for this event.

    An ``EventCandidate`` is the many-to-many join between a ``Target`` (a
    transient) and a ``NonLocalizedEvent`` (the GW trigger). Candidates are
    created automatically for targets landing inside the localization's credible
    region (``custom_code.healpix_utils.create_candidates_from_targets``) and
    manually through the web UI.

    ``viable_only=True`` drops candidates a human has already ruled out
    (``EventCandidate.viable``); it defaults to False so the full sample --
    including the rejects -- is available for training/validating a classifier.
    """
    qs = EventCandidate.objects.filter(nonlocalizedevent=event).select_related("target")
    if viable_only:
        qs = qs.filter(viable=True)
    if target_names is not None:
        qs = qs.filter(target__name__in=list(target_names))
    return qs.order_by("target__name")


# ---------------------------------------------------------------------------
# Photometry extraction
# ---------------------------------------------------------------------------
def _datum_value(datum: ReducedDatum) -> dict:
    """``ReducedDatum.value`` as a dict.

    It is a ``JSONField``, but a few older ingestion paths stored a JSON string
    into it, so decode defensively.
    """
    value = datum.value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _parse_datum(datum: ReducedDatum) -> Optional[dict]:
    """Turn one photometry ``ReducedDatum`` into a row dict, or None to skip it.

    The JSON payload is not schema-enforced by the database. In practice a
    photometry point is one of:

    * a detection --  ``{'magnitude': float, 'error': float, 'filter': str}``
    * a non-detection -- ``{'limit': float, 'filter': str}``

    with an optional ``'telescope'`` key. Anything without a filter, or with
    neither ``magnitude`` nor ``limit``, is not usable as a light-curve point.
    """
    value = _datum_value(datum)

    filt = value.get("filter")
    if filt is None or datum.timestamp is None:
        return None

    if "magnitude" in value:
        mag = value["magnitude"]
        magerr = value.get("error") or DEFAULT_MAGERR  # 0/None -> 3-sigma default
        upperlimit = False
    elif "limit" in value:
        mag = value["limit"]
        magerr = np.nan
        upperlimit = True
    else:
        return None

    if mag is None:
        return None

    return dict(
        mjd=Time(datum.timestamp).mjd,
        filter_raw=filt,
        mag=float(mag),
        magerr=float(magerr) if magerr is not None else np.nan,
        upperlimit=upperlimit,
        # `source_name` is the broker/survey that delivered the point (ZTF, ATLAS,
        # TNS, SAGUARO pipeline, ...); value['telescope'] is the reporting
        # telescope when the source bothered to tell us.
        source=datum.source_name or "unknown",
        telescope=value.get("telescope", ""),
        reduceddatum_id=datum.id,
    )


def get_event_photometry(
    event_id: str,
    viable_only: bool = False,
    target_names: Optional[Iterable[str]] = None,
    dt_min: Optional[float] = None,
    dt_max: Optional[float] = None,
    include_limits: bool = True,
    snr_min: Optional[float] = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """All photometry for all candidates of one GW event, as a long DataFrame.

    Parameters
    ----------
    event_id
        Superevent name, e.g. ``'S250818k'``.
    viable_only
        Only include candidates still flagged viable (default: include all).
    target_names
        Restrict to these target names instead of all candidates.
    dt_min, dt_max
        Keep only points with ``dt_min <= dt <= dt_max``, where ``dt`` is days
        relative to the GW trigger (negative = before the trigger). ``None``
        means unbounded on that side.
    include_limits
        Keep non-detections (upper limits). They matter for constraining the
        rise, but most light-curve fitters want them dropped -- set False to do
        that here.
    snr_min
        Drop detections below this S/N. Upper limits are never cut by this.
    refresh
        **Writes to the database.** Before reading, query TNS for new photometry
        and enqueue an asynchronous ATLAS forced-photometry job per candidate
        (``scoring.vet_phot.find_public_phot``). The ATLAS job runs in the
        background worker, so its results will *not* be in the returned frame --
        re-run without ``refresh`` a few minutes later to pick them up.

    Returns
    -------
    pandas.DataFrame
        One row per photometric measurement, sorted by target then time, with
        the columns listed in :data:`COLUMNS`. Empty (but correctly typed) if
        the event has no candidates or none of them have photometry.
    """
    event = get_event(event_id)
    t0_mjd = get_event_t0_mjd(event)
    candidates = list(get_candidates(event, viable_only=viable_only, target_names=target_names))

    if not candidates:
        logger.warning("No candidates found for %s", event_id)
        return pd.DataFrame(columns=COLUMNS)

    if refresh:
        from scoring.vet_phot import find_public_phot

        for cand in candidates:
            logger.info("Querying public photometry services for %s", cand.target.name)
            find_public_phot(cand.target)

    # Metadata is per-target; look it up once instead of per photometry point.
    by_target = {cand.target_id: cand for cand in candidates}

    # One query for every candidate's photometry rather than one per target.
    datums = ReducedDatum.objects.filter(
        target_id__in=list(by_target),
        data_type=PHOTOMETRY_DATA_TYPE,
    ).order_by("target_id", "timestamp")

    rows = []
    for datum in datums.iterator():
        row = _parse_datum(datum)
        if row is None:
            continue
        cand = by_target[datum.target_id]
        target = cand.target
        row.update(
            event_id=event.event_id,
            candidate_id=cand.id,
            target_id=target.id,
            target_name=target.name,
            ra=target.ra,
            dec=target.dec,
            mwebv=target.mwebv,  # Milky Way E(B-V), for extinction correction
        )
        rows.append(row)

    if not rows:
        logger.warning("Found %d candidates for %s but no photometry", len(candidates), event_id)
        return pd.DataFrame(columns=COLUMNS)

    df = pd.DataFrame(rows)

    # Derived columns.
    # 'g-ZTF' / 'r.ATLAS' / 'V ' all collapse to a bare band name, same rule the
    # vetting code uses, so filters are comparable across surveys.
    df["filter"] = standardize_filter_names(df["filter_raw"].tolist())
    df["dt"] = df["mjd"] - t0_mjd  # days since the GW trigger
    df["snr"] = error_to_snr(df["magerr"])  # 2.5 / ln(10) / magerr; NaN for limits

    # Cuts.
    if dt_min is not None:
        df = df[df["dt"] >= dt_min]
    if dt_max is not None:
        df = df[df["dt"] <= dt_max]
    if not include_limits:
        df = df[~df["upperlimit"]]
    if snr_min is not None:
        df = df[df["upperlimit"] | (df["snr"] >= snr_min)]

    df = df.sort_values(["target_name", "mjd"]).reset_index(drop=True)
    return df[COLUMNS]


def get_target_photometry(event_id: str, target_name: str, **kwargs) -> pd.DataFrame:
    """Convenience wrapper: photometry for a single candidate of an event."""
    return get_event_photometry(event_id, target_names=[target_name], **kwargs)


# ---------------------------------------------------------------------------
# KilonovaSCORER handoff
#
# KilonovaSCORER loads observations with
#     load_observations(file_path, merger_mjd, dist_mpc, dist_err_mpc)
# where the CSV needs columns time (MJD), magnitude, e_magnitude, band; it adds
# time_after_gw = time - merger_mjd and Monte-Carlos absolute_magnitude from
# (dist_mpc, dist_err_mpc). It also requires a `filter_mapped` column of
# canonical band names before scoring. The functions below produce exactly that
# per candidate, plus a manifest carrying the three scalar arguments.
# ---------------------------------------------------------------------------
def _scalar_distance(value, what: str, target_id: int) -> float:
    """Coerce a distance / distance error to a single float.

    The host-galaxy branch of ``get_eventcandidate_default_distance`` returns
    whatever the catalog JSON held, and some z-independent host distances carry
    an **asymmetric** error as a two-element ``[upper, lower]`` list. Since
    KilonovaSCORER samples a symmetric Gaussian distance modulus it needs one
    number, so the two sides are averaged. Averaging rather than taking the
    larger side keeps the absolute magnitudes from being needlessly inflated;
    if you would rather be conservative, use ``max`` here.
    """
    if isinstance(value, (list, tuple, np.ndarray)):
        vals = [float(v) for v in np.ravel(value) if v is not None and np.isfinite(float(v))]
        if not vals:
            return np.nan
        out = float(np.mean(vals))
        logger.info(
            "target %s: asymmetric %s %s -> using the mean, %.3f",
            target_id, what, list(value), out,
        )
        return out
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def get_candidate_distance(target_id: int, event_id: str) -> tuple[float, float]:
    """Luminosity distance and its 1-sigma error, in Mpc, for one candidate.

    Thin wrapper around
    :func:`scoring.scoring.get_eventcandidate_default_distance`, which is the
    same distance TROVE's own scoring uses. It falls back through, in order:
    the target's own redshift, the most trustworthy associated host galaxy's
    redshift, and finally the GW skymap's distance posterior at the candidate's
    healpix. Feed these straight into ``load_observations`` as ``dist_mpc`` and
    ``dist_err_mpc``.

    Both values are coerced to plain floats -- see :func:`_scalar_distance`.
    """
    from scoring.scoring import get_eventcandidate_default_distance

    try:
        dist, dist_err = get_eventcandidate_default_distance(target_id, event_id)
    except AttributeError as exc:
        # get_eventcandidate_default_distance indexes host_df.z_type without
        # checking the column exists (scoring/scoring.py). Targets whose stored
        # "Host Galaxies" JSON predates that field raise here -- 17 of 121
        # candidates on S250818k. An unusable distance is a skippable candidate,
        # not a crash, so report it as a missing distance.
        logger.warning(
            "target %s: host-galaxy table is missing a column "
            "(%s) -- treating the distance as unknown", target_id, exc,
        )
        return np.nan, np.nan
    except Exception as exc:  # noqa: BLE001
        logger.warning("target %s: distance lookup failed: %r", target_id, exc)
        return np.nan, np.nan
    dist = _scalar_distance(dist, "distance", target_id)
    dist_err = _scalar_distance(dist_err, "distance error", target_id)

    # A handful of host-galaxy catalog rows carry a negative Dist (a sentinel
    # that survived the z-based filtering upstream). A distance modulus is
    # undefined there, so return NaN rather than handing KilonovaSCORER a value
    # that would silently produce garbage absolute magnitudes.
    if not dist > 0:
        logger.warning("target %s: non-positive distance %s -> NaN", target_id, dist)
        dist = np.nan
    if dist_err < 0:
        logger.warning("target %s: negative distance error %s -> NaN", target_id, dist_err)
        dist_err = np.nan

    return dist, dist_err


def match_band(filter_raw: str, lookup: dict) -> Optional[str]:
    """Canonical band name for a raw TROVE filter string, or None.

    Filter strings in the database are ``<band>[-<instrument>[-<telescope>]]``
    with a fair bit of drift: 'g-ZTF', 'orange-ATLAS', 'BG-q-BlackGem',
    'Clear-', 'y-P1'. We take the longest '-'-delimited prefix present in
    ``lookup``, so the band survives however much survey cruft is appended, and
    a two-token band name like 'BG-q' still beats its own first token 'BG'.
    Falls back to the standardized name to catch '.'/' '-delimited variants.
    """
    key = (filter_raw or "").strip()
    tokens = key.split("-")
    for i in range(len(tokens), 0, -1):
        band = "-".join(tokens[:i]).strip()
        if band in lookup:
            return lookup[band]
    return lookup.get(standardize_filter_names([key])[0])


def to_scorer_frame(
    df: pd.DataFrame,
    filter_lookup: Optional[dict] = None,
    drop_unmapped: bool = True,
    map_wide_bands: bool = False,
    mode: str = "survey",
) -> pd.DataFrame:
    """Rename/derive the columns KilonovaSCORER expects.

    Takes the frame from :func:`get_event_photometry` and returns one with
    :data:`SCORER_COLUMNS` (plus ``target_name``/``target_id`` so it can still
    be grouped per candidate).
    ``mode`` selects how a filter becomes a comparable band:

    ``'survey'`` (default)
        Each observation keeps its own bandpass -- ATLAS orange stays
        ``atlaso``, ZTF g stays ``ztfg`` -- and is scored against simulations
        through the *same* filter. Requires a grid generated in those
        bandpasses (see ``KilonovaScorer/generate_ladder.py``).
    ``'canonical'``
        The older behaviour: everything is approximated onto ``g-band`` /
        ``r-band`` / ``i-band`` / ``z-band`` via :data:`FILTER_LOOKUP`. Needed
        for grids that only carry LSST bands, but it folds a real
        photometric-system error into 87% of TROVE's photometry.

    Rows whose filter has no match are dropped when ``drop_unmapped``.
    ``map_wide_bands=True`` additionally folds in the wide/unfiltered bands
    (ATLAS wide, BlackGem, SAGUARO Clear) whose assignment is a judgement call.

    Note this does *not* drop pre-merger points or upper limits -- do that with
    ``dt_min=0, include_limits=False`` in :func:`get_event_photometry`, which is
    what :func:`write_scorer_inputs` does.
    """
    if mode not in ("survey", "canonical"):
        raise ValueError(f"mode must be 'survey' or 'canonical', got {mode!r}")

    if mode == "survey":
        # Exact: each observation keeps its own bandpass, matched against a
        # simulation through the same filter. No cross-system approximation.
        mapped = [
            match_bandpass(f, s, map_wide_bands=map_wide_bands)
            for f, s in zip(df["filter_raw"], df["source"])
        ]
    else:
        lookup = dict(FILTER_LOOKUP if filter_lookup is None else filter_lookup)
        if map_wide_bands:
            lookup.update(WIDE_BAND_LOOKUP)
        # mapped from filter_raw, not the standardized name -- see match_band
        mapped = [match_band(f, lookup) for f in df["filter_raw"]]

    out = pd.DataFrame(
        {
            "target_name": df["target_name"],
            "target_id": df["target_id"],
            "time": df["mjd"],
            "magnitude": df["mag"],
            "e_magnitude": df["magerr"],
            "band": df["filter"],
            "filter_mapped": mapped,
            "time_after_gw": df["dt"],
        }
    )
    if drop_unmapped:
        unmapped = out["filter_mapped"].isna()
        if unmapped.any():
            logger.warning(
                "Dropping %d point(s) in unmappable filters: %s",
                int(unmapped.sum()),
                sorted(out.loc[unmapped, "band"].unique()),
            )
        out = out[~unmapped]
    return out[["target_name", "target_id"] + SCORER_COLUMNS].reset_index(drop=True)


def write_scorer_inputs(
    event_id: str,
    outdir: str,
    viable_only: bool = False,
    target_names: Optional[Iterable[str]] = None,
    dt_min: float = 0.0,
    dt_max: Optional[float] = None,
    snr_min: Optional[float] = None,
    map_wide_bands: bool = False,
    refresh: bool = False,
) -> pd.DataFrame:
    """Write one KilonovaSCORER-ready CSV per candidate, plus ``manifest.csv``.

    Upper limits and (by default) pre-merger points are dropped, matching what
    KilonovaSCORER's own JSON loader does.

    ``manifest.csv`` has one row per candidate with the arguments to feed
    ``load_observations``: ``file_path``, ``merger_mjd``, ``dist_mpc``,
    ``dist_err_mpc`` -- plus ``n_points``/``n_bands`` so you can skip candidates
    with too sparse a light curve.

    Returns the manifest as a DataFrame.
    """
    out_path = Path(outdir)
    out_path.mkdir(parents=True, exist_ok=True)

    df = get_event_photometry(
        event_id,
        viable_only=viable_only,
        target_names=target_names,
        dt_min=dt_min,
        dt_max=dt_max,
        include_limits=False,  # KilonovaSCORER scores detections only
        snr_min=snr_min,
        refresh=refresh,
    )
    if df.empty:
        logger.warning("Nothing to write for %s", event_id)
        return pd.DataFrame()

    merger_mjd = get_event_t0_mjd(get_event(event_id))
    scorer = to_scorer_frame(df, map_wide_bands=map_wide_bands)
    if scorer.empty:
        logger.warning("No photometry in a mappable filter for %s", event_id)
        return pd.DataFrame()

    rows = []
    for (target_name, target_id), lc in scorer.groupby(["target_name", "target_id"]):
        # Slashes and spaces show up in TNS/broker names often enough to matter.
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(target_name))
        csv_path = out_path / f"{safe}.csv"
        lc[SCORER_COLUMNS].to_csv(csv_path, index=False)

        try:
            dist, dist_err = get_candidate_distance(target_id, event_id)
        except Exception as exc:  # noqa: BLE001 - one bad target shouldn't kill the run
            logger.warning("No distance for %s: %s", target_name, exc)
            dist, dist_err = np.nan, np.nan

        rows.append(
            dict(
                target_name=target_name,
                target_id=target_id,
                file_path=str(csv_path),
                merger_mjd=merger_mjd,
                dist_mpc=dist,
                dist_err_mpc=dist_err,
                n_points=len(lc),
                n_bands=lc["filter_mapped"].nunique(),
            )
        )

    manifest = pd.DataFrame(rows).sort_values("target_name").reset_index(drop=True)
    manifest.to_csv(out_path / "manifest.csv", index=False)
    return manifest


