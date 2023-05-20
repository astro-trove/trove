from django import template
from tom_nonlocalizedevents.models import NonLocalizedEvent
from astropy.coordinates import get_sun, get_moon
from astropy.time import Time
from astroplan import moon_illumination
import numpy as np


register = template.Library()
h = w = 5. ** 0.5 / 2.  # half-height and half-width of a 5 deg2 square
CSS_FOOTPRINT = np.array([[-w, -h], [-w, h], [w, h], [w, -h], [-w, -h]])


@register.inclusion_tag('tom_nonlocalizedevents/partials/skymap.html')
def skymap(localization):
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

    # CSS fields
    fields = localization.css_field_credible_regions.filter(group__isnull=False)
    if fields.exists():
        groups = list(fields.order_by('group').values_list('group', flat=True).distinct())
        vertices = []
        for g in groups:
            centers = np.array(fields.filter(group=g).values_list('css_field__ra', 'css_field__dec'))
            cos_dec = np.cos(np.deg2rad(centers[:, ::-1]))
            cos_dec[:, 1] = 1.  # take cosine of dec and divide the RAs by it
            vertices.append((centers[:, np.newaxis] + CSS_FOOTPRINT / cos_dec[:, np.newaxis]).tolist())
        extras['css_fields'] = vertices
    else:
        extras['css_fields'] = []

    # GW skymap
    contour = localization.credible_region_contours.filter(probability=0.9)
    if contour.exists():
        extras['credible_region'] = contour.last().pixels
    else:
        extras['credible_region'] = []

    return extras
