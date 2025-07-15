from django import template
from django.db.models import Max
from tom_nonlocalizedevents.models import NonLocalizedEvent
from custom_code.templatetags.skymap_extras import get_preferred_localization
import math

register = template.Library()

SI_PREFIXES = ['', 'k', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y', 'R', 'Q']


@register.filter
def format_inverse_far(far):
    if not far:
        return ''
    inv_far = 3.168808781402895e-08 / far  # 1/Hz to yr
    if inv_far > 1.:
        log1000 = math.log10(inv_far) / 3.
        i = int(log1000)
        if i < len(SI_PREFIXES):
            inv_far *= 1000. ** -i
            unit = SI_PREFIXES[i] + 'yr'
        else:
            unit = 'yr'
    else:  # convert to days
        inv_far *= 365.25
        unit = 'd'
    if inv_far >= 1000.:
        return f'{inv_far:.0e} {unit}'
    elif inv_far > 10.:
        return f'{inv_far:.0f} {unit}'
    else:
        return f'{inv_far:.1f} {unit}'


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
def format_area(area):
    unit = 'deg²'
    if area < 1.:
        area *= 3600.
        unit = 'arcmin²'
    if area < 1.:
        area *= 3600.
        unit = 'arcsec²'
    if area >= 10.:
        return f'{area:.0f} {unit}'
    else:
        return f'{area:.1f} {unit}'


@register.filter
def get_most_likely_class(details):
    if not details:
        return
    elif details['search'] == 'SSM':
        return details['search']
    elif details['group'] == 'CBC':
        classification = details['classification']
        return max(classification, key=classification.get)
    else:  # burst
        return details['group']


@register.filter
def percentformat(value, d=0):
    try:
        return f'{float(value):.{d}%}'
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


@register.filter
def sort_localizations(localizations):
    return localizations.annotate(Max('sequences__sequence_id')).order_by('sequences__sequence_id__max')


@register.inclusion_tag('tom_nonlocalizedevents/partials/nonlocalizedevent_details.html', takes_context=True)
def nonlocalizedevent_details(context, localization=None):
    if localization is None:
        event_id = context['request'].GET.get('localization_event')
        if event_id is None:
            return
        nle = NonLocalizedEvent.objects.get(event_id=event_id)
        sequence = nle.sequences.last()
        localization = get_preferred_localization(nle)
    elif localization.external_coincidences.exists():
        sequence = localization.external_coincidences.last().sequences.last()
    else:
        sequence = localization.sequences.last()

    if sequence.nonlocalizedevent.event_type == NonLocalizedEvent.NonLocalizedEventType.GRAVITATIONAL_WAVE:
        if sequence.details['group'] == 'CBC':
            details_to_display = [
                [
                    ('Event Type', f'{sequence.nonlocalizedevent.event_type} {sequence.details["group"]}'),
                    ('Instrument', '+'.join(sequence.details['instruments'])),
                    ('50% Area', format_area(localization.area_50)),
                    ('90% Area', format_area(localization.area_90)),
                ],
                [
                    ('1/FAR', format_inverse_far(sequence.details['far'])),
                    ('Distance', format_distance(localization)),
                ] +
                [(prop, f'{prob:.0%}') for prop, prob in sequence.details['properties'].items()],
                [(classification, f'{prob:.0%}') for classification, prob in sequence.details['classification'].items()],
            ]
        elif sequence.details['group'] == 'Burst':
            details_to_display = [
                [
                    ('Event Type', f'{sequence.nonlocalizedevent.event_type} {sequence.details["group"]}'),
                    ('Instrument', '+'.join(sequence.details['instruments'])),
                    ('50% Area', format_area(localization.area_50)),
                    ('90% Area', format_area(localization.area_90)),
                ],
                [
                    ('1/FAR', format_inverse_far(sequence.details['far'])),
                    ('Duration', millisecondformat(sequence.details['duration'])),
                    ('Frequency', f'{sequence.details["central_frequency"]:.0f} Hz'),
                ]
            ]
        else:
            details_to_display = []
    elif sequence.nonlocalizedevent.event_type == NonLocalizedEvent.NonLocalizedEventType.GAMMA_RAY_BURST:
        details_to_display = [
            [
                ('Event Type', sequence.nonlocalizedevent.event_type),
                ('Instrument', sequence.details['notice_type'].split()[0]),
                ('50% Area', format_area(localization.area_50)),
                ('90% Area', format_area(localization.area_90)),
            ],
            [
                ('Significance', sequence.details['data_signif'].replace(' [sigma]', 'σ')),
                ('Interval', millisecondformat(float(sequence.details['data_interval'].split()[0]))),
                ('Energy', '[' + sequence.details['e_range'].replace(' -', ',').replace(']', '').replace(' [', '] ')),
            ]
        ]
    elif sequence.nonlocalizedevent.event_type == NonLocalizedEvent.NonLocalizedEventType.UNKNOWN:  # Einstein probe
        details_to_display = [
            [
                ('Event Type', 'X-ray Transient'),
                ('Instrument', sequence.details['instrument']),
                ('50% Area', format_area(localization.area_50)),
                ('90% Area', format_area(localization.area_90)),
            ],
            [
                ('Image S/N', sequence.details['image_snr']),
                ('Count Rate', f'{sequence.details["net_count_rate"]} s⁻¹'),
                ('Energy', f'{sequence.details["image_energy_range"]} keV'),
            ]
        ]
    return {'details': details_to_display}
