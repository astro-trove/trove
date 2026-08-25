"""Which photometry scorer TROVE uses — a site-wide, cache-backed user choice.

TROVE can judge a candidate's photometry two ways:

``trove``
    ``vet_phot._score_phot``: fit the light curve, then range-check peak
    luminosity, peak time and decay rate against ``vet_bns.PARAM_RANGES``. The
    factor is 1 if every fitted value is in range and 0.1 per violation.

``kilonova``
    KilonovaSCORER's cumulative ``P_tail``: compare the light curve against a
    simulated kilonova population and report where the observations fall in it.
    Needs a simulation grid (see :mod:`scoring.KilonovaScorerHelpers`).

The choice lives in the cache rather than the database because it is a display
and run-time preference, not per-candidate data — the same reasoning as
``agn_toggle``, which this deliberately mirrors.

**Flipping it does not rescore anything.** No stored ``ScoreFactor`` row is
touched and no vetting is queued; the next Vet All run simply reads the value
and scores accordingly. That keeps the button cheap: a mis-click costs nothing,
where a rescore-on-toggle would cost minutes of compute per press.
"""
from __future__ import annotations

from django.core.cache import cache

# cache key. Site-wide and unscoped by event, matching ``agn_toggle``.
PHOT_METHOD_KEY = "phot_method"

PHOT_METHOD_TROVE = "trove"
PHOT_METHOD_KILONOVA = "kilonova"

# TROVE's own check stays the default: it needs no simulation grid, so it can
# never fail for want of one
PHOT_METHOD_DEFAULT = PHOT_METHOD_TROVE

PHOT_METHOD_CHOICES = (PHOT_METHOD_TROVE, PHOT_METHOD_KILONOVA)

# what the toggle shows for each value
PHOT_METHOD_LABELS = {
    PHOT_METHOD_TROVE: "Light curve metrics",
    PHOT_METHOD_KILONOVA: "KilonovaSCORER",
}


def get_phot_method() -> str:
    """The currently selected method, always one of :data:`PHOT_METHOD_CHOICES`.

    An unrecognised cached value falls back to the default rather than
    propagating: the cache is not validated on write by Django, and a stale or
    hand-set key must not be able to send an unknown method into the scorer.
    """
    value = cache.get(PHOT_METHOD_KEY, PHOT_METHOD_DEFAULT)
    return value if value in PHOT_METHOD_CHOICES else PHOT_METHOD_DEFAULT


def set_phot_method(method: str) -> str:
    """Set the method. Returns what was stored.

    Cached without a timeout: the choice should persist until someone changes
    it, not silently revert mid-session the way a 5-minute entry would.
    """
    if method not in PHOT_METHOD_CHOICES:
        raise ValueError(
            f"unknown photometry method {method!r}; "
            f"expected one of {PHOT_METHOD_CHOICES}"
        )
    cache.set(PHOT_METHOD_KEY, method, None)
    return method


def toggle_phot_method() -> str:
    """Flip between the two methods and return the new value."""
    current = get_phot_method()
    return set_phot_method(
        PHOT_METHOD_KILONOVA if current == PHOT_METHOD_TROVE else PHOT_METHOD_TROVE
    )


def phot_method_label(method: str | None = None) -> str:
    """Human-readable name, for the toggle button."""
    return PHOT_METHOD_LABELS.get(method or get_phot_method(), PHOT_METHOD_DEFAULT)
