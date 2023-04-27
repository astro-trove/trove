import logging
from kne_cand_vetting.catalogs import static_cats_query
from kne_cand_vetting.galaxy_matching import galaxy_search
from kne_cand_vetting.survey_phot import query_ZTFpubphot
from tom_targets.models import TargetExtra
from tom_dataproducts.models import ReducedDatum
import json
import numpy as np
from astropy.cosmology import FlatLambdaCDM
from astropy.time import Time, TimezoneInfo
from astropy.coordinates import SkyCoord
from django.conf import settings

DB_CONNECT = "postgresql+psycopg2://{USER}:{PASSWORD}@{HOST}:{PORT}/{NAME}".format(**settings.DATABASES['default'])
COSMOLOGY = FlatLambdaCDM(H0=70., Om0=0.3)

logger = logging.getLogger(__name__)


def process_reduced_ztf_data(target, candidates):
    """Ingest data from the ZTF JSON format into ``ReducedDatum`` objects. Mostly copied from tom_base v2.13.0."""
    for candidate in candidates:
        if all([key in candidate['candidate'] for key in ['jd', 'magpsf', 'fid', 'sigmapsf']]):
            nondetection = False
        elif all(key in candidate['candidate'] for key in ['jd', 'diffmaglim', 'fid']):
            nondetection = True
        else:
            continue
        jd = Time(candidate['candidate']['jd'], format='jd', scale='utc')
        jd.to_datetime(timezone=TimezoneInfo())
        value = {
            'filter': {1: 'g', 2: 'r', 3: 'i'}[candidate['candidate']['fid']]
        }
        if nondetection:
            value['limit'] = candidate['candidate']['diffmaglim']
        else:
            value['magnitude'] = candidate['candidate']['magpsf']
            value['error'] = candidate['candidate']['sigmapsf']
        rd, _ = ReducedDatum.objects.get_or_create(
            timestamp=jd.to_datetime(timezone=TimezoneInfo()),
            value=value,
            source_name='ZTF',
            source_location=alert['zid'],
            data_type='photometry',
            target=target)


def update_or_create_target_extra(target, key, value):
    """
    Check if a ``TargetExtra`` with the given key exists for a given target. If it exists, update the value. If it does
    not exist, create it with the input value.
    """
    te, created = TargetExtra.objects.get_or_create(target=target, key=key)
    te.value = value
    te.save()


def target_post_save(target, created):
    """This hook runs following update of a target."""
    logger.info('Target post save hook: %s created: %s', target, created)

    messages = []
    if created:
        coord = SkyCoord(target.ra, target.dec, unit='deg')
        target.galactic_lng = coord.galactic.l.deg
        target.galactic_lat = coord.galactic.b.deg
        target.save()

        qprob, qso, qoffset, asassnprob, asassn, asassnoffset, tns_results, gaiaprob, gaias, gaiaoffset, gaiaclass = \
            static_cats_query([target.ra], [target.dec], db_connect=DB_CONNECT)

        if tns_results[0] is not None:
            iau_name, redshift, classification = tns_results[0]
            if target.name != iau_name:
                target.name = iau_name
                target.save()
                messages.append(f"Found a match in the TNS: {target.name}")
            if classification is not None and target.extra_fields.get('Classification') != classification:
                update_or_create_target_extra(target, 'Classification', classification)
                messages.append(f"Classification set to {classification}")
            if redshift is not None and target.extra_fields.get('Redshift') != redshift:
                update_or_create_target_extra(target, 'Redshift', redshift)
                messages.append(f"Redshift set to {redshift}")

        update_or_create_target_extra(target=target, key='QSO Match', value=qso[0])
        if qso[0] != 'None':
            update_or_create_target_extra(target=target, key='QSO Prob.', value=qprob[0])
            update_or_create_target_extra(target=target, key='QSO Offset', value=qoffset[0])

        update_or_create_target_extra(target=target, key='ASASSN Match', value=asassn[0])
        if asassn[0] != 'None':
            update_or_create_target_extra(target=target, key='ASASSN Prob.', value=asassnprob[0])
            update_or_create_target_extra(target=target, key='ASASSN Offset', value=asassnoffset[0])

        update_or_create_target_extra(target=target, key='Gaia Match', value=asassn[0])
        if asassn[0] != 'None':
            update_or_create_target_extra(target=target, key='Gaia VS Prob.', value=gaiaprob[0])
            update_or_create_target_extra(target=target, key='Gaia VS Offset', value=gaiaoffset[0])
            update_or_create_target_extra(target=target, key='Gaia VS Class', value=gaiaclass[0])

        matches, hostdict = galaxy_search(target.ra, target.dec, db_connect=DB_CONNECT)
        update_or_create_target_extra(target=target, key='Host Galaxies', value=json.dumps(hostdict))

        if hostdict:
            dist = hostdict[0].get('Dist', np.nan)
            if np.isfinite(dist):
                target.distance = dist
            disterr = hostdict[0].get('DistErr', np.nan)
            if np.isfinite(disterr):
                target.distance_err = disterr
            target.save()

        ztfphot = query_ZTFpubphot(target.ra, target.dec, db_connect=DB_CONNECT)
        newztfphot = []
        if ztfphot:
            olddatetimes = [rd.timestamp for rd in target.reduceddatum_set.all()]
            for candidate in ztfphot:
                jd = Time(candidate['candidate']['jd'], format='jd', scale='utc')
                newdatetime = jd.to_datetime(timezone=TimezoneInfo())
                if newdatetime not in olddatetimes:
                    logger.info('New ZTF point at {0}.'.format(newdatetime))
                    newztfphot.append(candidate)
        process_reduced_ztf_data(target, newztfphot)

    redshift = target.targetextra_set.filter(key='Redshift')
    if redshift.exists() and redshift.first().float_value >= 0.02 and target.distance is None:
        messages.append(f'Updating distance of {target.name} based on redshift')
        target.distance = COSMOLOGY.luminosity_distance(redshift.first().float_value).to('Mpc').value
        target.save()

    for message in messages:
        logger.info(message)

    return messages
