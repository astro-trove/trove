from django import template
from tom_nonlocalizedevents.models import NonLocalizedEvent
import math

register = template.Library()

SI_PREFIXES = ['', 'k', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y', 'R', 'Q']


@register.filter
def format_inverse_far(far):
    inv_far = 3.168808781402895e-08 / far  # 1/Hz to yr
    if inv_far > 1.:
        log1000 = math.log10(inv_far) / 3.
        i = min(int(log1000), 10)
        inv_far *= 1000. ** -i
        unit = SI_PREFIXES[i] + 'yr'
    else:  # convert to days
        inv_far *= 365.25
        unit = 'd'
    return f'{inv_far:.0f} {unit}' if inv_far > 10. else f'{inv_far:.1f} {unit}'


@register.filter
def format_distance(localization):
    if localization is None or not localization.distance_mean:
        return ''
    dist_mean = localization.distance_mean
    dist_std = localization.distance_std
    if localization.distance_mean < 1000.:
        unit = 'Mpc'
    else:
        dist_mean /= 1000.
        dist_std /= 1000.
        unit = 'Gpc'
    return f'{dist_mean:.0f} ± {dist_std:.0f} {unit}' if dist_mean > 10. else f'{dist_mean:.1f} ± {dist_std:.1f} {unit}'


@register.filter
def get_most_likely_class(details):
    if details['group'] == 'CBC':
        classification = details['classification']
        return max(classification, key=classification.get)
    else:  # burst
        return details['group']


@register.filter
def percentformat(value, d=0):
    try:
        return f'{value:.{d}%}'
    except ValueError:
        return value


@register.filter
def millisecondformat(value, d=0):
    try:
        return f'{value * 1000.:.{d}f} ms'
    except ValueError:
        return value


@register.filter
def truncate(string, length=5):
    if len(string) > length:
        return string[:length-1] + '.'
    else:
        return string


@register.inclusion_tag('tom_nonlocalizedevents/partials/nonlocalizedevent_details.html', takes_context=True)
def nonlocalizedevent_details(context, localization=None):
    if localization is None:
        event_id = context['request'].GET.get('localization_event')
        if event_id is None:
            return
        nle = NonLocalizedEvent.objects.get(event_id=event_id)
        sequence = nle.sequences.last()
    elif localization.external_coincidences.exists():
        sequence = localization.external_coincidences.last().sequences.last()
    else:
        sequence = localization.sequences.last()
    return {'sequence': sequence}
