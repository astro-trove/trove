"""The scoring-controls module: toggles that change how candidates are ranked.

Drop it into any page with::

    {% load scoring_controls %}
    {% scoring_controls %}

and it renders the AGN-score toggle, the photometry-scoring toggle
(:mod:`scoring.phot_method`), and the status line of a running KilonovaSCORER
job. Nothing has to be added to the view's context: the tags read the current
state themselves, from the cache, and pick the non-localized event out of
``?nonlocalizedevent=`` on the request. That is what makes this pluggable -- a
page that wants these controls adds one line, not a block of
``get_context_data``.

Pass ``nle_id`` explicitly on a page whose URL does not carry the event::

    {% scoring_controls nle_id=nonlocalizedevent.id %}

Where the surrounding layout needs the progress line somewhere other than
directly under the buttons, render the two halves separately::

    {% scoring_controls show_status=False %}
    ...
    {% scoring_status %}

The toggles send you back to the page you clicked from (see
``TogglePhotScoringMethodView`` and ``ToggleAgnCacheView``), so the module works
off the candidate list as well as on it.
"""

from django import template
from django.core.cache import cache

from scoring.phot_method import KILONOVA, get_kilonova_status, get_phot_method

register = template.Library()


def _scoring_state(context, nle_id=None):
    """Everything the partials render, read fresh rather than taken on trust.

    Deliberately does not fall back to values the including page may happen to
    have in context under these names: a control that renders one state and
    toggles another is worse than no control.
    """
    request = context.get("request")
    if nle_id is None and request is not None:
        nle_id = request.GET.get("nonlocalizedevent")
    # "" from a blank query parameter is not an event
    nle_id = nle_id or None

    return {
        "request": request,
        "nle_id": nle_id,
        # site-wide display flag, unlike the per-event photometry method
        "agn_toggle": cache.get("agn_toggle", True),
        "phot_method_is_kilonova": get_phot_method(nle_id) == KILONOVA,
        "kilonova_status": get_kilonova_status(nle_id),
    }


@register.inclusion_tag("scoring/partials/scoring_controls.html", takes_context=True)
def scoring_controls(context, nle_id=None, show_status=True):
    """Render the scoring toggles for one non-localized event.

    ``nle_id``
        The event whose ranking the controls change. Defaults to
        ``?nonlocalizedevent=`` on the current request. Without one there is no
        event to scope the photometry method to -- it is stored per event -- so
        only the site-wide AGN toggle is shown.
    ``show_status``
        Whether to follow the buttons with the KilonovaSCORER progress line.
        Pass ``False`` and use :func:`scoring_status` to place it yourself.
    """
    state = _scoring_state(context, nle_id)
    state["show_status"] = show_status
    return state


@register.inclusion_tag("scoring/partials/scoring_status.html", takes_context=True)
def scoring_status(context, nle_id=None):
    """Render just the KilonovaSCORER progress line for one event.

    Renders nothing unless the event is actually on KilonovaSCORER and a run has
    reported something.
    """
    return _scoring_state(context, nle_id)
