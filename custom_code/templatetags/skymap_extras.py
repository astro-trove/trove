from django import template
from tom_nonlocalizedevents.models import NonLocalizedEvent
from astropy.coordinates import get_body
from astropy.time import Time
from datetime import timedelta
from astroplan import moon_illumination


register = template.Library()


@register.inclusion_tag('tom_nonlocalizedevents/partials/skymap.html', takes_context=True)
def skymap(context, localization):
    # sun, moon, and candidates
    now = context['request'].GET.get('grouping_now')
    if now:
        now = Time(now)
    else:
        now = Time.now()
    current_sun_pos = get_body('sun', now)
    current_moon_pos = get_body('moon', now)
    current_moon_exclusion = 3. + 42. * moon_illumination(now)
    extras = {
        'current_sun_ra': current_sun_pos.ra.deg,
        'current_sun_dec': current_sun_pos.dec.deg,
        'current_moon_ra': current_moon_pos.ra.deg,
        'current_moon_dec': current_moon_pos.dec.deg,
        'current_moon_exclusion': current_moon_exclusion,
        'candidates': localization.nonlocalizedevent.candidates.all(),
    }

    # GW skymap
    contour = localization.credible_region_contours.filter(probability=0.9)
    if contour.exists():
        extras['credible_region'] = contour.last().pixels
    else:
        extras['credible_region'] = []

    return extras


def get_preferred_localization(nle):
    seq = nle.sequences.last()
    if seq is not None:
        return seq.localization if seq.external_coincidence is None else seq.external_coincidence.localization


@register.inclusion_tag('tom_nonlocalizedevents/partials/skymap.html', takes_context=True)
def skymap_event_id(context):
    event_id = context['request'].GET.get('localization_event')
    if event_id is None:
        return
    nle = NonLocalizedEvent.objects.get(event_id=event_id)
    localization = get_preferred_localization(nle)
    return skymap(context, localization)


@register.filter
def time_after_event(time, event_id, unit='hour', precision=1):
    nle = NonLocalizedEvent.objects.get(event_id=event_id)
    seq = nle.sequences.last()
    dt = Time(time) - Time(seq.details['time'])
    return dt.to(unit).to_string(precision=precision)


@register.filter
def secondslater(time, seconds):
    return time + timedelta(seconds=seconds)
