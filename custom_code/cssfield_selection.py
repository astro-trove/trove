import numpy as np
import healpy
from astroplan import moon_illumination
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_moon, get_sun, spherical_to_cartesian
from astropy.time import Time
from astropy import units as u
import logging

logger = logging.getLogger(__name__)


def project_footprint(footprint, ra, dec, pos_angle=None):
    if pos_angle is None:
        pos_angle = 0.0

    footprint_zero_center_ra = np.asarray([pt[0] for pt in footprint])
    footprint_zero_center_dec = np.asarray([pt[1] for pt in footprint])
    footprint_zero_center_uvec = ra_dec_to_uvec(footprint_zero_center_ra, footprint_zero_center_dec)
    footprint_zero_center_x, footprint_zero_center_y, footprint_zero_center_z = footprint_zero_center_uvec
    proj_footprint = []
    for idx in range(footprint_zero_center_x.shape[0]):
        vec = np.asarray([footprint_zero_center_x[idx], footprint_zero_center_y[idx], footprint_zero_center_z[idx]])
        new_vec = vec @ x_rot(-pos_angle) @ y_rot(dec) @ z_rot(-ra)
        new_x, new_y, new_z = new_vec.flat
        pt_ra, pt_dec = uvec_to_ra_dec(new_x, new_y, new_z)
        proj_footprint.append([pt_ra, pt_dec])
    return proj_footprint


def footprint(h, w):
    scale = 1  # for degrees
    vertices = []
    half_h = round(0.5 * float(h) * scale, 4)
    half_w = round(0.5 * float(w) * scale, 4)
    vertices.append([-half_w, half_h])
    vertices.append([half_w, half_h])
    vertices.append([half_w, -half_h])
    vertices.append([-half_w, -half_h])
    vertices.append([-half_w, half_h])
    return vertices


CSS_FOOTPRINT = footprint(5. ** 0.5, 5. ** 0.5)


def ra_dec_to_uvec(ra, dec):
    phi = np.deg2rad(90 - dec)
    theta = np.deg2rad(ra)
    x = np.cos(theta) * np.sin(phi)
    y = np.sin(theta) * np.sin(phi)
    z = np.cos(phi)
    return x, y, z


def uvec_to_ra_dec(x, y, z):
    r = np.sqrt(x ** 2 + y ** 2 + z ** 2)
    x /= r
    y /= r
    z /= r
    theta = np.arctan2(y, x)
    phi = np.arccos(z)
    dec = 90 - np.rad2deg(phi)
    if theta < 0:
        ra = 360 + np.rad2deg(theta)
    else:
        ra = np.rad2deg(theta)
    return ra, dec


def x_rot(theta_deg):
    theta = np.deg2rad(theta_deg)
    return np.matrix([
        [1, 0, 0],
        [0, np.cos(theta), -np.sin(theta)],
        [0, np.sin(theta), np.cos(theta)]
    ])


def y_rot(theta_deg):
    theta = np.deg2rad(theta_deg)
    return np.matrix([
        [np.cos(theta), 0, np.sin(theta)],
        [0, 1, 0],
        [-np.sin(theta), 0, np.cos(theta)]
    ])


def z_rot(theta_deg):
    theta = np.deg2rad(theta_deg)
    return np.matrix([
        [np.cos(theta), -np.sin(theta), 0],
        [np.sin(theta), np.cos(theta), 0],
        [0, 0, 1]
    ])


def get_prob_radec(probs, nside, fp, css_field_credible_regions):
    for cr in css_field_credible_regions:
        pointing_footprint = project_footprint(fp, cr.css_field.ra, cr.css_field.dec)
        ras_poly = [x[0] for x in pointing_footprint][:-1]
        decs_poly = [x[1] for x in pointing_footprint][:-1]
        xyzpoly = spherical_to_cartesian(1, np.deg2rad(decs_poly), np.deg2rad(ras_poly))
        qp = healpy.query_polygon(nside, np.array(xyzpoly).T)
        prob = 0
        for ind in qp:
            prob += probs[ind]
        cr.probability_contained = prob
        cr.save()


CSS_LOCATION = EarthLocation(lat=32.4433333333 * u.deg, lon=-110.788888889 * u.deg, height=2790 * u.m)


def observable_tonight(target):
    radec = SkyCoord(target.ra, target.dec, unit=u.deg)
    time = Time.now() + np.linspace(0., 1 * u.day, 48)
    frame = AltAz(obstime=time, location=CSS_LOCATION)

    altaz = radec.transform_to(frame)
    target_up = (altaz.secz <= 1.75) & (altaz.secz >= 1.)

    sun_altaz = get_sun(time).transform_to(frame)
    sun_down = sun_altaz.alt <= -12. * u.deg
    now_or_sunset = np.flatnonzero(sun_down).min()
    sunrise = np.flatnonzero(~sun_down[now_or_sunset:]).min() + now_or_sunset

    moon_altaz = get_moon(time).transform_to(frame)
    moon_down = moon_altaz.alt <= 0.
    moon_far = moon_altaz.separation(altaz) > (3. + 42. * moon_illumination(time)) * u.deg

    observable = target_up & (moon_far | moon_down)
    return observable[now_or_sunset:sunrise].any()


def rank_css_fields(queryset, n_select=12, n_groups=3):
    queryset.update(group=None, rank_in_group=None)  # erase any previous ranking
    fields_remaining = list(queryset.order_by('-probability_contained'))
    for g in range(n_groups):
        adjacent_remaining = set(fields_remaining)
        for r in range(n_select):
            for i, cr in enumerate(fields_remaining):
                if cr in adjacent_remaining:
                    cr = fields_remaining.pop(i)
                    if observable_tonight(cr.css_field):
                        cr.group = g + 1
                        cr.rank_in_group = r + 1
                        cr.save()
                        break
            else:
                logger.warning('No adjacent fields to select')
                break
            adjacent_remaining |= set(cr.css_field.adjacent.all()) & set(fields_remaining)
