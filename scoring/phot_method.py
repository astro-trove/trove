from __future__ import annotations

# session key. Per viewer, matching `agn_toggle` in scoring.util.
PHOT_METHOD_KEY = "phot_method"

PHOT_METHOD_METRICS = "metrics"
PHOT_METHOD_KILONOVASCORER = "kilonovascorer"

# TROVE's own check stays the default: it needs no simulation grid, so it can
# never fail for want of one
PHOT_METHOD_DEFAULT = PHOT_METHOD_METRICS

PHOT_METHOD_CHOICES = (PHOT_METHOD_METRICS, PHOT_METHOD_KILONOVASCORER)

# the one vetting mode KilonovaSCORER can score: its grid is a two-component
# kilonova population, so the choice is meaningless for the other modes, which
# fit the light curve and have no second scorer to pick between
KILONOVASCORER_VETTING_MODE = "KN"

# what the toggle shows for each value
PHOT_METHOD_LABELS = {
    PHOT_METHOD_METRICS: "Light curve metrics",
    PHOT_METHOD_KILONOVASCORER: "KilonovaSCORER",
}


def get_phot_method(request=None) -> str:
    """The viewer's selected method, always one of :data:`PHOT_METHOD_CHOICES`."""
    if request is None or not hasattr(request, "session"):
        return PHOT_METHOD_DEFAULT
    value = request.session.get(PHOT_METHOD_KEY, PHOT_METHOD_DEFAULT)
    return value if value in PHOT_METHOD_CHOICES else PHOT_METHOD_DEFAULT


def set_phot_method(request, method: str) -> str:
    """Set this viewer's method. Returns what was stored.

    Held in the session, so it persists for the browser session rather than
    reverting mid-use the way a timed cache entry would.
    """
    if method not in PHOT_METHOD_CHOICES:
        raise ValueError(
            f"unknown photometry method {method!r}; "
            f"expected one of {PHOT_METHOD_CHOICES}"
        )
    request.session[PHOT_METHOD_KEY] = method
    return method


def toggle_phot_method(request) -> str:
    """Flip between the two methods and return the new value."""
    current = get_phot_method(request)
    return set_phot_method(
        request,
        PHOT_METHOD_KILONOVASCORER if current == PHOT_METHOD_METRICS else PHOT_METHOD_METRICS,
    )


def phot_method_label(method: str | None = None, request=None) -> str:
    """Human-readable name, for the toggle button."""
    return PHOT_METHOD_LABELS.get(
        method or get_phot_method(request), PHOT_METHOD_DEFAULT
    )
