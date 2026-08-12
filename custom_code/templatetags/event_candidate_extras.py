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

register = template.Library()


@register.simple_tag
def get_agn_toggle():
    """Current value of the site-wide, cache-backed agn_toggle flag."""
    return cache.get("agn_toggle", True)

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
    request = context['request']
    nle_id = request.GET.get('nonlocalizedevent')
    cooldown_cache_key = settings.VETTING_COOLDOWN_KEY+"_"+nle_id

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
        sf_set = sorted(sf_set, key=lambda sf: order.index(sf.key))
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
            
            if te.value in (None, np.nan, "nan", "None"):
                value = te.value
            else:
                value = fmter(float(te.value))
            
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
            
            if score_factor.value in (None, np.nan, "nan"):
                value = score_factor.value
            else:
                value = (
                    fmter(score_factor.value)
                    if label in ("Host Galaxy used for Distance Scoring", "Host Galaxy Source Catalog")
                    else fmter(float(score_factor.value))
                )
            
            event_card["details"].append(
                {
                    "label": label,
                    "value": value, 
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
                    html += f'                <div class="detail-row">\n'
                    html += f'                  <span class="detail-label">{detail["label"]}</span>\n'
                    html += f'                  <span class="detail-value">{detail["value"]}</span>\n'
                    html += f'                </div>\n'

                # then the photometry scores too
                for key, subscore in ec_subscores[em_transient_type].items():
                    label, fmter = keymap[key+"_score"]
                    html += f'                <div class="detail-row">\n'
                    html += f'                  <span class="detail-label">{label}</span>\n'
                    html += f'                  <span class="detail-value">{fmter(subscore)}</span>\n'
                    html += f'                </div>\n'
                html += f'              </div>\n'
                
                # then the score details (max lum., etc.)
                html += f'              <div class="score-card-content">\n'
                for detail in score_details:
                    if "Score" in detail["label"]: continue 
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
