
from __future__ import annotations

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


#: Only the pure band-name mapping lives here. The models and the loader are
#: deliberately NOT re-exported: Django imports this package while building the
#: app registry, before models are ready, so a model import at this level would
#: raise AppRegistryNotReady. Import them from `.models` / `.grid_db` directly.
__all__ = ["survey_band"]
