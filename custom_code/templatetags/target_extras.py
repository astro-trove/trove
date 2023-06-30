from django import template
from astropy.coordinates import SkyCoord
import json

register = template.Library()


@register.filter
def ecliptic_lng(target):
    sc = SkyCoord(target.ra, target.dec, unit='deg')
    return sc.barycentrictrueecliptic.lon.deg


@register.filter
def ecliptic_lat(target):
    sc = SkyCoord(target.ra, target.dec, unit='deg')
    return sc.barycentrictrueecliptic.lat.deg


@register.inclusion_tag('tom_targets/partials/aladin_custom.html')
def aladin_custom(target):
    """
    Displays Aladin skyview of the given target along with basic finder chart annotations and circles around potential
    host galaxies. The resulting image is downloadable. This templatetag only works for sidereal targets.
    """
    target_extra = target.targetextra_set.filter(key='Host Galaxies')
    if target_extra.exists():
        galaxies = json.loads(target_extra.first().value)
    else:
        galaxies = []
    return {'target': target, 'galaxy_ras': [g['RA'] for g in galaxies], 'galaxy_decs': [g['Dec'] for g in galaxies]}
