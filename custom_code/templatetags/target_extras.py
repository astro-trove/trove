from django import template
from django.template.defaultfilters import stringfilter
from astropy.coordinates import SkyCoord
import json
import re

register = template.Library()

TNS_PREFIXES = ["AT", "SN", "TDE", "FRB"]


@register.filter
def ecliptic_lng(target):
    sc = SkyCoord(target.ra, target.dec, unit='deg')
    return sc.barycentrictrueecliptic.lon.deg


@register.filter
def ecliptic_lat(target):
    sc = SkyCoord(target.ra, target.dec, unit='deg')
    return sc.barycentrictrueecliptic.lat.deg


@register.filter
@stringfilter
def split_name(name):
    """Splits the name into a prefix, consisting of no digits, and a basename, which starts with its first digit"""
    res = re.match('(?P<prefix>\D*)(?P<basename>.*)', name)
    name_info = res.groupdict()
    if name_info['prefix'] == 'FRB':
        name_info['tns_objname'] = name
    elif name_info['prefix'] in TNS_PREFIXES:
        name_info['tns_objname'] = name_info['basename']
    else:
        name_info['tns_objname'] = None
    return name_info


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
