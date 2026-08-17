"""
Some functions for accessing the EventCandidate table inside a django template
"""

import numpy as np
from collections import OrderedDict
from functools import partial
from django import template
from django.core.cache import cache
from django.template.defaultfilters import linebreaks
from django.conf import settings
from django.utils.safestring import mark_safe
from trove_targets.models import Target
from tom_targets.models import TargetExtra
from scoring.util import (
    get_event_candidate_scores as _get_event_candidate_scores,
    get_target_score as _get_target_score,
    TARGETEXTRA_KEYS,
)
from scoring.phot_method import (
    get_phot_method as _get_phot_method,
    phot_method_label as _phot_method_label,
)

register = template.Library()


@register.simple_tag
def get_agn_toggle():
    """Current value of the site-wide, cache-backed agn_toggle flag."""
    return cache.get("agn_toggle", True)


@register.simple_tag
def get_phot_method():
    """Which photometry scorer Vet All will use: ``trove`` or ``kilonova``.

    Site-wide and cache-backed, exactly like ``agn_toggle``. Unlike the AGN
    flag, flipping this does NOT rescore anything -- the stored factors are not
    recomputed and no vetting is triggered. It only changes which scorer the
    NEXT Vet All run uses, so the button is cheap to press and cannot cost a
    user a long re-vet by accident.
    """
    return _get_phot_method()


@register.simple_tag
def get_phot_method_label():
    """``TROVE`` or ``KilonovaSCORER`` — what the toggle button displays."""
    return _phot_method_label()

@register.simple_tag
def get_event_candidate_scores(*args, **kwargs):
    """A wrapper on the imported _get_event_candidate_scores, but registered as a tag"""
    return _get_event_candidate_scores(*args, **kwargs)

@register.simple_tag
def get_target_score(*args, **kwargs):
    """A wrapper on the imported _get_target_score, but registered as a tag"""
    return _get_target_score(*args, **kwargs)

@register.simple_tag(takes_context=True)
def vet_all_is_allowed(context):
    """Is the Vet All button enabled, or is this event within its cooldown?

    True when nothing has run recently. ``VetAllView.form_valid`` sets
    ``VETTING_COOLDOWN_KEY_<nle_id>`` for ``VETTING_COOLDOWN_PERIOD`` (1 hour)
    when the button is used, so the presence of that key IS the cooldown.

    This used to compute the key and then fall off the end of the function,
    returning None. None is falsy, so the template took every event to be on
    cooldown permanently and the button was greyed out for good -- with no
    cooldown actually set anywhere.
    """
    request = context['request']
    nle_id = request.GET.get('nonlocalizedevent')
    if not nle_id:
        # No event in scope, so nothing to rate-limit. Returning True also
        # avoids `KEY + "_" + None`, which raises TypeError and would take the
        # whole page down rather than just disabling a button.
        return True
    cooldown_cache_key = settings.VETTING_COOLDOWN_KEY + "_" + str(nle_id)
    return not cache.get(cooldown_cache_key)

@register.simple_tag(takes_context=True)
def display_score_details(context, target_id):
    request = context['request']
    nle_requested = request.GET.get('nonlocalizedevent')
    
    if target_id is None:
        return "Target ID is None!"

    target = Target.objects.get(id=target_id)

    keymap = OrderedDict(
        ps_score=("Point Source Association?", _bool_format_yesno),
        mpc_score=("Minor Planet Score Association?", _bool_format_yesno),
        mpc_match_name=("MPC Match Name", _str_format),
        mpc_match_date=("MPC Match Date", _str_format),
        mpc_match_sep=('MPC Match Separation', partial(_float_format, unit='"')),
        skymap_score=("Localization Score", _float_format),
        host_distance_score=("Distance Score", _float_format),
        host_name=("Host Galaxy used for Distance Scoring", _str_int_format),
        host_catalog=("Host Galaxy Source Catalog", _str_format),
        agn_score=("AGN Score (0.1 or 1.0)", partial(_float_format, precision=1)),
        phot_peak_lum=("Maximum Luminosity", partial(_sci_format, unit="erg/s")),
        phot_peak_time=(
            "Time of Maximum Light Curve",
            partial(_float_format, unit="days"),
        ),
        phot_decay_rate=(
            "Light Curve Slope (positive is brightening)",
            partial(_float_format, unit="mag/day"),
        ),
        phot_peak_lum_score=("Score from Maximum Luminosity", partial(_float_format, precision=1)),
        phot_peak_time_score=(
            "Score from Time of Maximum Light Curve",
            partial(_float_format, precision=1),
        ),
        phot_decay_rate_score=(
            "Score from Light Curve Slope",
            partial(_float_format, precision=1),
        ),
        kilonova_score=(
            "KilonovaSCORER Photometry Score",
            partial(_float_format, precision=4),
        ),
        kilonova_skip_reason=(
            "KilonovaSCORER: could not score",
            _str_format,
        ),
    )
    order = list(keymap.keys())

    # basic scores/details
    basic_score_details = []
    te = TargetExtra.objects.filter(target_id=target_id)
    basic_score_details.append(te.filter(key="ps_score"))
    for event_candidate in target.eventcandidate_set.all():
        sf_set = event_candidate.scorefactor_set.filter(key="mpc_score")
        basic_score_details.append(sf_set)
    te_set = te.filter(key__in=TARGETEXTRA_KEYS).exclude(key__in=["ps_score"])
    basic_score_details.append(te_set)

    # NLE-specific scores/details
    score_details = []
    for event_candidate in target.eventcandidate_set.all():
        sf_set = event_candidate.scorefactor_set.exclude(
            key__in=TARGETEXTRA_KEYS
            + ["mpc_score", "predetection_score"]
        ).all()
        # `order.index` raises ValueError on a key that is not in `keymap`,
        # which took the whole target page down whenever a new ScoreFactor key
        # appeared -- `kilonova_score` did exactly that. Unknown keys sort to
        # the end and are rendered with their raw key as the label instead.
        sf_set = sorted(
            sf_set,
            key=lambda sf: order.index(sf.key) if sf.key in order else len(order),
        )
        score_details.append(sf_set)

    # Build structured data instead of strings
    cards = []

    # Basic Score Details Card
    basic_card = {
        "title": "Basic Scores (Not Event-Specific)",
        "details": []
    }
    for queryset in basic_score_details:
        for te in queryset:
            if te.key in keymap:
                label, fmter = keymap[te.key]
            else:
                label = te.key
                fmter = _float_format
            
            value = _safe_format(te.value, fmter)
            
            basic_card["details"].append({
                "label": label,
                "value": value
            })
    
    cards.append(basic_card)

    # Event Cards
    for queryset in score_details:
        event_name = None
        event_card = None
        
        for score_factor in queryset:
            print(score_factor.key)
            ec = score_factor.event_candidate
            nle = ec.nonlocalizedevent
            
            # Create new card if we encounter a new event
            if event_name != str(nle):
                if event_card:
                    cards.append(event_card)

                event_name = nle.event_id
                event_card = {
                    "title": event_name,
                    "ec": ec,
                    "details": []
                }
            
            if score_factor.key in keymap:
                label, fmter = keymap[score_factor.key]
            else:
                label = score_factor.key
                fmter = _float_format
            
            numeric = label not in (
                "Host Galaxy used for Distance Scoring",
                "Host Galaxy Source Catalog",
            )
            value = _safe_format(score_factor.value, fmter, numeric=numeric)
            
            # KilonovaSCORER scores only the KN model, so its score and its
            # "could not score" reason belong in the KN subtab alone. The detail
            # loops below run once per transient subtab, so without this tag the
            # same row is repeated under KN-in-SN and super-KN, where it is not
            # just redundant but wrong -- it reads as a verdict on models the
            # scorer never evaluated.
            event_card["details"].append(
                {
                    "label": label,
                    "value": value,
                    "only": "KN" if score_factor.key.startswith("kilonova") else None,
            })
        
        if event_card:
            cards.append(event_card)

    # Render cards as HTML

    # Separate basic card from event cards
    basic_card = cards[0]  # First card is always "Basic Score Details"
    event_cards = cards[1:]  # Rest are event cards

    # Render basic card
    html = '<div class="score-details-wrapper">\n'
    html += f'  <div class="score-card">\n'
    html += f'    <div class="score-card-header">{basic_card["title"]}</div>\n'
    html += f'    <div class="score-card-content-basic">\n'
    for detail in basic_card["details"]:
        html += f'      <div class="detail-row">\n'
        html += f'        <span class="detail-label">{detail["label"]}</span>\n'
        html += f'        <span class="detail-value">{detail["value"]}</span>\n'
        html += f'      </div>\n'
    html += f'    </div>\n'
    html += f'  </div>\n'

    # Render event tabs and cards
    if event_cards:
        html += '  <div class="event-tabs-container">\n'
        html += '    <div class="event-tabs">\n'
        for idx, card in enumerate(event_cards):
            ec = card["ec"]
            if nle_requested is None:
                active_class = 'active' if idx == 0 else ''
            else:
                active_class = 'active' if nle_requested==ec.nonlocalizedevent.event_id else ''
                
            html += f'      <button class="event-tab {active_class}" data-tab="{idx}">{card["title"]}</button>\n'
        html += '    </div>\n'
        
        html += '    <div class="event-cards">\n'
        for idx, card in enumerate(event_cards):
            ec = card.pop("ec")
            ec_score_details = _get_event_candidate_scores(
                [ec],
                include_subscores=True
            )[0]
            ec_scores = ec_score_details.score
            ec_subscores = ec_score_details.subscores

            score_details = card["details"]

            # setup the "event" card tab content
            display_class = 'active' if idx == 0 else 'hidden'
            html += f'      <div class="event-card {display_class}" data-tab-content="{idx}">\n'    

            # add "subtabs" for the different types of scores
            html += '        <div class="event-subtabs-container">\n'
            html += '          <div class="event-subtabs">\n'
            label_idx_map = {}
            for jdx, (label, score) in enumerate(ec_scores.items()):
                active_class = 'active' if jdx == 0 else ''
                html += f'            <button class="event-subtab {active_class}" data-subtab="{idx}-{jdx}">{label} = {score:.2f}</button>\n'
                label_idx_map[label] = f"{idx}-{jdx}"
                
            html += '          </div>\n'

            html += '          <div class="event-cards">\n'
            for kdx, (em_transient_score_label, idxlabel) in enumerate(label_idx_map.items()): 
                em_transient_type = em_transient_score_label.split(" ")[0]

                active_subclass = ""
                if not kdx:
                    active_subclass = "active"
                
                html += f'            <div class="event-subtab-content {active_subclass}" data-subtab-content="{idxlabel}">\n'
                # then add the content with the score details
                # first the more general scores (2D, Distance, AGN, etc.) that don't change
                # per transient model 
                html += f'              <div class="score-card-content-filled">\n'
                for detail in score_details:
                    if "Score" not in detail["label"]: continue
                    if detail.get("only") and detail["only"] != em_transient_type:
                        continue
                    html += f'                <div class="detail-row">\n'
                    html += f'                  <span class="detail-label">{detail["label"]}</span>\n'
                    html += f'                  <span class="detail-value">{detail["value"]}</span>\n'
                    html += f'                </div>\n'

                # then the photometry scores too. Both lookups are guarded:
                # a transient with no subscores, or a subscore whose "<key>_score"
                # has no keymap entry, is a missing label -- not a reason to 500
                # the page and lose every other score on it.
                for key, subscore in ec_subscores.get(em_transient_type, {}).items():
                    label, fmter = keymap.get(key + "_score", (key, _float_format))
                    html += f'                <div class="detail-row">\n'
                    html += f'                  <span class="detail-label">{label}</span>\n'
                    html += f'                  <span class="detail-value">{fmter(subscore)}</span>\n'
                    html += f'                </div>\n'
                html += f'              </div>\n'
                
                # then the score details (max lum., etc.)
                html += f'              <div class="score-card-content">\n'
                for detail in score_details:
                    if "Score" in detail["label"]: continue
                    if detail.get("only") and detail["only"] != em_transient_type:
                        continue
                    html += f'                <div class="detail-row">\n'
                    html += f'                  <span class="detail-label">{detail["label"]}</span>\n'
                    html += f'                  <span class="detail-value">{detail["value"]}</span>\n'
                    html += f'                </div>\n'
                html += f'              </div>\n'
                html += f'            </div>\n'
            html += '          </div>\n'
            html += '        </div>\n'
            html += '      </div>\n'
        html += '    </div>\n'
        html += '  </div>\n'
    html += '</div>\n'
    return mark_safe(html)


def _safe_format(raw, fmter, numeric=True):
    """Render a ScoreFactor value, never raising.

    Two things reach here that the old inline formatting could not survive: a
    value that is text rather than a number (``kilonova_skip_reason`` is a
    sentence), and a key with no entry in ``keymap``, whose fallback formatter
    is numeric. Either one raised ValueError and returned a 500 for the whole
    target page -- losing every other score on it to make one cell unrenderable.
    Falling back to the raw string keeps the page up and still shows the value.
    """
    if raw is None or (isinstance(raw, str) and raw.strip() in ("", "nan", "None")):
        return raw
    try:
        if numeric:
            return fmter(float(raw))
        return fmter(raw)
    except (TypeError, ValueError):
        return str(raw)


def _float_format(flt, unit="", precision=2):
    return f"{flt:.{precision}f} {unit}"


def _sci_format(flt, unit=""):
    prefactor, power = f"{flt:.2e}".split("e")
    if power[0] == "+":
        power = power[1:]
    return f"{prefactor} x 10<sup>{power}</sup> {unit}"


def _bool_format(flt):
    return int(flt) 

def _bool_format_yesno(flt):
    # yes, this order is correct because a score of 0 means an association!
    return "No" if bool(flt) else "Yes"

def _str_int_format(s):
    try:
        return str(int(s))
    except ValueError:
        return str(s)

    
def _str_format(s):
    return str(s)
