"""
Some functions for accessing the EventCandidate table inside a django template
"""
from functools import partial
from urllib.parse import urlparse
from django import template
from django.template.defaultfilters import linebreaks
from django.utils.safestring import mark_safe
from tom_nonlocalizedevents.models import EventCandidate, NonLocalizedEvent
from trove_targets.models import Target

register = template.Library()

#@register.inclusion_tag('tom_targets/partials/target_data.html', takes_context=True)
@register.simple_tag
def get_candidate_event_score(target_id):

    if target_id is None:
        return "Target ID is None!"

    target = Target.objects.get(id=target_id)

    out = {}
    for event_candidate in target.eventcandidate_set.all():
        nonlocalized_name = NonLocalizedEvent.objects.get(
            id = event_candidate.nonlocalizedevent_id
        ).event_id
        
        out[nonlocalized_name] = event_candidate.priority
    
    return out



@register.simple_tag
def display_score_details(target_id):

    if target_id is None:
        return "Target ID is None!"

    target = Target.objects.get(id=target_id)
    
    score_details = []
    for event_candidate in target.eventcandidate_set.all():
        score_details.append(event_candidate.scorefactor_set.all())

    res = {}
    keymap = dict(
        skymap_score = ("2D Localization Score", _float_format),
        ps_score = ("Point Source Score (1 or 0)", _bool_format),
        host_distance_score = ("3D Association Score", _float_format),
        phot_peak_lum = ("Maximum Luminosity", partial(_sci_format, unit="erg/s")),
        phot_peak_time = ("Time of Maximum Light Curve", partial(_float_format, unit="days")),
        phot_decay_rate = ("Light Curve Slope (positive is brightening)", partial(_float_format, unit="mag/day"))
    )
    for queryset in score_details:
        for score_factor in queryset:
            nle = score_factor.event_candidate.nonlocalizedevent
            if nle not in res:
                res[nle] = ""
            if score_factor.key in keymap:
                label, fmter = keymap[score_factor.key]
            else:
                label = score_factor.key
                fmter = _float_format
            res[nle] += f"&emsp;{label}: {fmter(float(score_factor.value))}\n"

    out = ""
    for key, s in res.items():
        out += f"<h6>{key}</h6>"
        out += s
        out += "\n\n"
            
    return mark_safe(linebreaks(out))

def _float_format(flt, unit=""):
    return f"{flt:.2f} {unit}"

def _sci_format(flt, unit=""):
    prefactor, power = f"{flt:.2e}".split("e")
    if power[0] == "+":
        power = power[1:]
    return f"{prefactor} x 10<sup>{power}</sup> {unit}"

def _bool_format(flt):
    return int(flt)
