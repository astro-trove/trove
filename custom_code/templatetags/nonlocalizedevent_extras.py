from django import template
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
