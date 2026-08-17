"""TROVE-side helpers for the ``KilonovaScorer`` package.

The scorer itself is a dependency (``kilonova_scorer`` in requirements.txt) and
nothing in here reimplements any of it. This package holds only the two things
the package genuinely cannot supply, both of which are about **getting TROVE's
data into the shape the scorer expects**:

* :mod:`.grid_db` — reading a simulation grid out of Postgres (``spanda_db``).
  ``KilonovaScorer`` has no grid store at all; it takes ``data_sim`` as a frame
  and leaves persistence to the caller. ``load_grid_db`` returns exactly that
  frame: ``sample_id`` / ``time`` / ``absolute_magnitude`` / ``filter_mapped``.

* :data:`FILTER_LOOKUP` — TROVE's filter names mapped onto the scorer's
  canonical bands. TROVE ingests from ATLAS, ZTF, Pan-STARRS and TNS, which all
  spell the same bandpass differently; the package assumes the caller has
  already normalised.

Everything else — the PPD, ``P_tail``, the ABC survival chain, the cumulative
logit combination — comes from ``KilonovaScorer.core2`` and is not duplicated.
"""
from __future__ import annotations

from dataclasses import dataclass

#: TROVE filter name -> the scorer's canonical band, for ``mode="canonical"``.
#: Only needed when an observation must be compared against simulations in a
#: *different* survey's bandpass; ``mode="survey"`` (the default) matches a
#: bandpass to itself and never consults this.
FILTER_LOOKUP = {
    "lsstg": "g-band", "g-ztf": "g-band", "ztfg": "g-band", "g-p1": "g-band", "g": "g-band",
    "lsstr": "r-band", "r-ztf": "r-band", "ztfr": "r-band", "r-p1": "r-band", "r": "r-band",
    "lssti": "i-band", "i-ztf": "i-band", "ztfi": "i-band", "i-p1": "i-band", "i": "i-band",
    "lsstz": "z-band", "z-ztf": "z-band", "ztfz": "z-band", "z-p1": "z-band", "z": "z-band",
}


#: Telescope/source name -> the grid's bandpass family. Matched as a prefix on
#: the upper-cased source, so "ATLAS", "ATLAS-STH (TNS)" and "ATLAS-CHL (TNS)"
#: all resolve to the same instrument. Order matters: the first match wins, so
#: longer, more specific prefixes come first.
_TELESCOPE_FAMILY = (
    ("ATLAS", "atlas"),
    ("P48", "ztf"),        # P48 is ZTF's telescope; TNS reports it by mount
    ("ZTF", "ztf"),
    ("PS1", "ps1::"),
    ("PS2", "ps1::"),      # same filter set; the grid carries only ps1::
    ("GOTO", "goto"),
    ("RUBIN", "lsst"),
    ("LSST", "lsst"),
)

#: Filter aliases, normalised before the family prefix is applied. ATLAS calls
#: its two bands cyan/orange in TNS reports and c/o in its own stream.
_FILTER_ALIAS = {"cyan": "c", "orange": "o"}

#: Filters with a real bandpass in the grid, for a source we cannot identify.
#: Falling back to the Sloan system is the standard convention for a generic
#: ugriz measurement and is what the grid's sdss* bands are for.
_GENERIC_BANDS = {"u": "sdssu", "g": "sdssg", "r": "sdssr", "i": "sdssi", "z": "sdssz"}


def survey_band(telescope, filter_name) -> str | None:
    """``(telescope, filter)`` -> the grid's bandpass id, or ``None`` if unmodelled.

    TROVE and the simulation grid speak different vocabularies. TROVE stores
    what the broker reported -- ``ATLAS``/``o``, ``P48 (TNS)``/``g``,
    ``PS1 (TNS)``/``w`` -- while the grid is keyed on bandpass ids like
    ``atlaso``, ``ztfg``, ``ps1::w``. Nothing matches on the filter alone, and
    the filter alone is not even sufficient: a bare ``g`` is a different
    bandpass on ZTF, Pan-STARRS and Rubin, and in ``survey`` mode an
    observation is compared against simulations **through its own bandpass**,
    so picking the wrong one silently scores against the wrong population.

    Returns ``None`` for anything with no counterpart in the grid -- ``BG``,
    ``Clear``, ``wide``, and GOTO's ``L`` on a non-GOTO mount. Those are
    dropped rather than guessed: an unmodelled filter scored against an
    arbitrary bandpass is worse than one not scored at all.
    """
    if filter_name is None:
        return None
    f = str(filter_name).strip()
    f = _FILTER_ALIAS.get(f.lower(), f)
    scope = str(telescope or "").strip().upper()

    family = next((fam for pre, fam in _TELESCOPE_FAMILY if scope.startswith(pre)), None)

    if family == "atlas":
        # ATLAS has exactly two bands. `wide` is its unfiltered mode, which the
        # grid does not model.
        return f"atlas{f}" if f in ("c", "o") else None
    if family == "ztf":
        return f"ztf{f}" if f in ("g", "r", "i") else None
    if family == "ps1::":
        return f"ps1::{f}" if f in ("g", "r", "i", "z", "y", "w") else None
    if family == "lsst":
        return f"lsst{f}" if f in ("u", "g", "r", "i", "z", "y") else None
    if family == "goto":
        # GOTO's L is its wide "clear" filter, modelled in the grid as gotol.
        return f"goto{f.lower()}" if f.lower() in ("b", "g", "l", "r") else None

    return _GENERIC_BANDS.get(f)


@dataclass(frozen=True)
class GridRef:
    """A grid in the database, standing where a :class:`~pathlib.Path` would.

    Frozen so it is hashable: candidates get bucketed into a dict keyed on
    their grid so one load serves many of them. ``name`` mirrors ``Path.name``
    for log lines.
    """

    name: str
    distance_mpc: float
    backend: str = "postgres"
    #: Last epoch the grid covers, in days. A 10-day and a 30-day grid at the
    #: same distance are NOT interchangeable, so selection needs this.
    t_max: float = float("nan")

    def __str__(self) -> str:
        return self.name


from .grid_db import (  # noqa: E402
    available_grids_db,
    grid_axis,
    grid_bands,
    grid_dsn,
    grid_exists,
    grid_store_ready,
    load_grid_db,
)

__all__ = [
    "FILTER_LOOKUP",
    "GridRef",
    "available_grids_db",
    "grid_axis",
    "grid_bands",
    "grid_dsn",
    "grid_exists",
    "grid_store_ready",
    "load_grid_db",
    "survey_band",
]
