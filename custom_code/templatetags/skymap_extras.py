from django import template
from tom_nonlocalizedevents.models import NonLocalizedEvent
from astropy.coordinates import get_sun, get_moon
from astropy.time import Time, TimezoneInfo
from astroplan import moon_illumination
import numpy as np


register = template.Library()
h = w = 5. ** 0.5 / 2.  # half-height and half-width of a 5 deg2 square
CSS_FOOTPRINT = np.array([[-w, -h], [-w, h], [w, h], [w, -h], [-w, -h]])


def centers_to_vertices(centers, footprint):
    """Calculate the vertices for a pointing from its center and footprint"""
    cos_dec = np.cos(np.deg2rad(centers[:, ::-1]))
    cos_dec[:, 1] = 1.  # take cosine of dec and divide the RAs by it
    return (centers[:, np.newaxis] + footprint / cos_dec[:, np.newaxis]).tolist()


@register.inclusion_tag('tom_nonlocalizedevents/partials/skymap.html')
def skymap(localization, survey_candidates=None, survey_observations=None):
    # sun, moon, and candidates
    now = Time.now()
    current_sun_pos = get_sun(now)
    current_moon_pos = get_moon(now)
    current_moon_exclusion = 3. + 42. * moon_illumination(now)
    extras = {
        'current_sun_ra': current_sun_pos.ra.deg,
        'current_sun_dec': current_sun_pos.dec.deg,
        'current_moon_ra': current_moon_pos.ra.deg,
        'current_moon_dec': current_moon_pos.dec.deg,
        'current_moon_exclusion': current_moon_exclusion,
        'candidates': localization.nonlocalizedevent.candidates.all(),
    }

    # potential survey fields
    fields = localization.surveyfieldcredibleregions.filter(group__isnull=False)
    if fields.exists():
        groups = list(fields.order_by('group').values_list('group', flat=True).distinct())
        vertices = []
        for g in groups:
            centers = np.array(fields.filter(group=g).values_list('survey_field__ra', 'survey_field__dec'))
            cos_dec = np.cos(np.deg2rad(centers[:, ::-1]))
            cos_dec[:, 1] = 1.  # take cosine of dec and divide the RAs by it
            vertices.append((centers[:, np.newaxis] + CSS_FOOTPRINT / cos_dec[:, np.newaxis]).tolist())
        extras['survey_fields'] = vertices
    else:
        extras['survey_fields'] = []

    # observed survey fields candidates
    if survey_observations is not None and survey_observations.exists():
        centers = np.unique(survey_observations.values_list('survey_field__ra', 'survey_field__dec'), axis=0)
        extras['survey_observations'] = centers_to_vertices(centers, CSS_FOOTPRINT)
    elif survey_candidates is not None and survey_candidates.exists():
        centers = np.unique(survey_candidates.values_list('observation_record__survey_field__ra',
                                                          'observation_record__survey_field__dec'), axis=0)
        extras['survey_candidates'] = survey_candidates
        extras['survey_observations'] = centers_to_vertices(centers, CSS_FOOTPRINT)
    else:
        extras['survey_observations'] = []

    # GW skymap
    contour = localization.credible_region_contours.filter(probability=0.9)
    if contour.exists():
        extras['credible_region'] = contour.last().pixels
    else:
        extras['credible_region'] = []

    return extras


@register.inclusion_tag('tom_nonlocalizedevents/partials/skymap.html', takes_context=True)
def skymap_event_id(context, survey_candidates=None, survey_observations=None):
    event_id = context['request'].GET.get('localization_event')
    if event_id is None:
        return
    nle = NonLocalizedEvent.objects.get(event_id=event_id)
    seq = nle.sequences.last()
    return skymap(seq.localization, survey_candidates, survey_observations)


@register.filter
def time_after_event(time, event_id, unit='hour', precision=1):
    nle = NonLocalizedEvent.objects.get(event_id=event_id)
    seq = nle.sequences.last()
    dt = Time(time) - Time(seq.details['time'])
    return dt.to(unit).to_string(precision=precision)
