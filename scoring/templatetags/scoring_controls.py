"""The scoring-controls module: toggles that change how candidates are ranked.

Drop it into any page with::

    {% load scoring_controls %}
    {% scoring_controls %}

and it renders the AGN-score toggle and the photometry-scoring toggle
(:mod:`scoring.phot_method`). Run progress is deliberately NOT shown here: the
event page already reports it once at the top, and a second live banner under
the toggle was duplicate noise. Nothing has to be added to the view's context: the tags read the current
state themselves, from the cache, and pick the non-localized event out of
``?nonlocalizedevent=`` on the request. That is what lets it drop into any
page -- a page that wants these controls adds one line, not a block of
``get_context_data``.

Pass ``nle_id`` explicitly on a page whose URL does not carry the event::

    {% scoring_controls nle_id=nonlocalizedevent.id %}

The toggles send you back to the page you clicked from (see
``TogglePhotScoringMethodView`` and ``ToggleAgnCacheView``), so the module works
off the candidate list as well as on it.
"""

from django import template
from django.core.cache import cache

from scoring.phot_method import (
    KILONOVA,
    KILONOVA_SCORE_KEY,
    KILONOVA_SKIP_KEY,
    get_kilonova_status,
    get_phot_method,
    get_scoring_settings,
)

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
        "phot_method_is_kilonova": get_phot_method() == KILONOVA,
        "kilonova_status": get_kilonova_status(nle_id),
    }


@register.inclusion_tag("scoring/partials/scoring_controls.html", takes_context=True)
def scoring_controls(context, nle_id=None):
    """Render the scoring toggles for one non-localized event.

    ``nle_id``
        The event whose ranking the controls change. Defaults to
        ``?nonlocalizedevent=`` on the current request. Without one there is no
        event to scope the photometry method to -- it is stored per event -- so
        only the site-wide AGN toggle is shown.
    """
    return _scoring_state(context, nle_id)


#: Parameters worth showing in the scoring-settings panel, in display order, as
#: (key, label, suffix). The full dict is ~16 entries, most of which are
#: plumbing (grid_path, dt_min, snr_min); these are the ones that actually move
#: a score, so the panel stays readable.
_SHOWN_PARAMS = (
    ("time_bin_width", "time bin", " d"),
    ("overlap_k", "k_ABC", ""),
    ("n_kde_sim", "KDE samples", ""),
    ("min_obs", "min epochs", ""),
    ("dt_max", "max epoch", " d"),
    ("random_state", "seed", ""),
)


@register.inclusion_tag("scoring/partials/scoring_settings.html", takes_context=True)
def scoring_settings(context, nle_id=None):
    """Render "what settings produced the scores on this page".

    Goes at the foot of a candidate list. Reports the settings applied at render
    time (method, AGN toggle) separately from the run that produced the stored
    ``kilonova_score`` rows, because those can disagree -- see
    :func:`scoring.phot_method.get_scoring_settings`.

    The candidate counts are done here rather than in ``phot_method`` so that
    module stays free of the ORM: two aggregate queries, no per-row work.
    """
    request = context.get("request")
    if nle_id is None and request is not None:
        nle_id = request.GET.get("nonlocalizedevent")
    nle_id = nle_id or None

    prov = dict(get_scoring_settings(nle_id))
    prov["request"] = request

    # How many candidates of this event actually carry a KilonovaSCORER score.
    # Without this the panel could claim the list is KilonovaSCORER-ranked while
    # most rows silently fall back to the TROVE check.
    if nle_id and prov["kilonova_selected"]:
        from scoring.models import ScoreFactor

        base = ScoreFactor.objects.filter(event_candidate__nonlocalizedevent_id=nle_id)
        n_scored = base.filter(key=KILONOVA_SCORE_KEY).count()
        n_skipped = base.filter(key=KILONOVA_SKIP_KEY).count()
        prov["n_scored"] = n_scored
        prov["n_skipped"] = n_skipped
        prov["n_fallback"] = n_skipped
        prov["has_coverage"] = n_scored > 0

    params = prov.get("params") or {}
    prov["shown_params"] = [
        {"label": label, "value": f"{params[key]}{suffix}"}
        for key, label, suffix in _SHOWN_PARAMS
        if key in params
    ]
    return prov
