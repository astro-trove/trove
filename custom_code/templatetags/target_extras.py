from django import template
from astropy.coordinates import SkyCoord

register = template.Library()


@register.filter
def ecliptic_lng(target):
    sc = SkyCoord(target.ra, target.dec, unit='deg')
    return sc.barycentrictrueecliptic.lon.deg


@register.filter
def ecliptic_lat(target):
    sc = SkyCoord(target.ra, target.dec, unit='deg')
    return sc.barycentrictrueecliptic.lat.deg
