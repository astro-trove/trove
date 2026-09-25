"""Per-viewer display preferences, held in the session.

These used to live in the global cache, where one viewer's choice changed the
page for everyone and, worse, mixed into a cache key shared across users. They
are preferences about how to *show* scores, so they belong to the viewer.
"""

#: session key for the AGN score toggle
AGN_TOGGLE_KEY = "agn_toggle"

#: AGN scores count towards the total unless the viewer turns them off
AGN_TOGGLE_DEFAULT = True


def get_agn_toggle(request=None) -> bool:
    """Whether this viewer wants AGN scores counted.

    ``request`` is optional for the same reason as
    :func:`scoring.phot_method.get_phot_method`: a queued task has no session.
    """
    if request is None or not hasattr(request, "session"):
        return AGN_TOGGLE_DEFAULT
    return bool(request.session.get(AGN_TOGGLE_KEY, AGN_TOGGLE_DEFAULT))


def set_agn_toggle(request, value: bool) -> bool:
    """Set this viewer's AGN toggle. Returns what was stored."""
    request.session[AGN_TOGGLE_KEY] = bool(value)
    return bool(value)
