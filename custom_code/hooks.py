import logging
from requests import Response
from kne_cand_vetting.catalogs import static_cats_query
from kne_cand_vetting.galaxy_matching import galaxy_search
from kne_cand_vetting.survey_phot import TNS_get, query_ZTFpubphot
from tom_targets.models import TargetExtra, TargetName
from tom_dataproducts.models import ReducedDatum
from .templatetags.target_extras import split_name
import json
import numpy as np
from astropy.cosmology import FlatLambdaCDM
from astropy.time import Time, TimezoneInfo
from astropy.coordinates import SkyCoord
from astroquery.ipac.irsa.irsa_dust import IrsaDust
from healpix_alchemy.constants import HPX
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
        rd, created = ReducedDatum.objects.get_or_create(
            timestamp=jd.to_datetime(timezone=TimezoneInfo()),
            value=value,
            source_name='ZTF',
            data_type='photometry',
            target=target)
        if created:  # do this afterward, in case there are duplicate candidates with distinct ZIDs
            rd.source_location = candidate['zid']
            rd.save()


def update_or_create_target_extra(target, key, value):
    """
    Check if a ``TargetExtra`` with the given key exists for a given target. If it exists, update the value. If it does
    not exist, create it with the input value.
    """
    te, created = TargetExtra.objects.get_or_create(target=target, key=key)
    te.value = value
    te.save()


def target_post_save(target, created, tns_time_limit:int=5):
    """This hook runs following update of a target."""
    logger.info('Target post save hook: %s created: %s', target, created)

    messages = []
    tns_query_status = None
    if created:
        coord = SkyCoord(target.ra, target.dec, unit='deg')
        target.galactic_lng = coord.galactic.l.deg
        target.galactic_lat = coord.galactic.b.deg
        target.save()

        if target.extra_fields.get('MW E(B-V)') is None:
            try:
                mwebv = IrsaDust.get_query_table(coord, section='ebv')['ext SandF ref'][0]
            except Exception as e:
                logger.error(f'Error querying IRSA dust for {target.name}')
            else:
                update_or_create_target_extra(target, 'MW E(B-V)', mwebv)
                messages.append(f'MW E(B-V) set to {mwebv:.4f}')


        update_or_create_target_extra(target=target, key='healpix', value=HPX.skycoord_to_healpix(coord))

        qso, qoffset, asassn, asassnoffset, tns_results, gaia, gaiaoffset, gaiaclass, ps1prob, ps1, ps1offset = \
            static_cats_query([target.ra], [target.dec], db_connect=DB_CONNECT)
            
        if tns_results:
            for iau_name, redshift, classification, internal_names in tns_results:
                # choose the name that already matches, if more than one
                # and make sure we ignore TNS classification prefixes that can change
                basename = split_name(iau_name)['basename']
                if basename == split_name(target.name)['basename']:
                    break

            # now query the real TNS by name for even more recent updates
            get_obj = [("objname", basename), ("objid", ""), ("photometry", "1"), ("spectra", "0")]
            response, time_to_wait = TNS_get(get_obj,
                                             settings.BROKERS['TNS']['bot_id'],
                                             settings.BROKERS['TNS']['bot_name'],
                                             settings.BROKERS['TNS']['api_key'],
                                             timelimit=tns_time_limit)
            if response is not None and response.status_code == 200:
                tns_reply = response.json()['data']

                # update the coordinates if needed; round to same number of sig figs as CSV files to avoid infinite loop
                tns_ra = float(f'{tns_reply["radeg"]:.14g}')
                tns_dec = float(f'{tns_reply["decdeg"]:.14g}')
                if target.ra != tns_ra or target.dec != tns_dec:
                    target.ra = tns_ra
                    target.dec = tns_dec
                    target.save()
                    messages.append(f'Updated coordinates to {target.ra:.6f}, {target.dec:.6f} based on TNS')

                # ingest any photometry
                n_new_phot = 0
                for candidate in tns_reply.get('photometry', []):
                    jd = Time(candidate['jd'], format='jd', scale='utc')
                    value = {'filter': candidate['filters']['name']}
                    if candidate['flux']:  # detection
                        value['magnitude'] = float(candidate['flux'])
                    else:
                        value['limit'] = float(candidate['limflux'])
                    if candidate['fluxerr']:  # not empty or zero
                        value['error'] = float(candidate['fluxerr'])
                    rd, created = ReducedDatum.objects.get_or_create(
                        timestamp=jd.to_datetime(timezone=TimezoneInfo()),
                        value=value,
                        source_name=candidate['telescope']['name'] + ' (TNS)',
                        data_type='photometry',
                        target=target)
                    n_new_phot += created
                if n_new_phot:
                    messages.append(f'Added {n_new_phot:d} photometry points from the TNS')

                # if query is successful, use these up-to-date versions instead of what's in the local copy
                iau_name = tns_reply['name_prefix'] + tns_reply['objname']
                if tns_reply['redshift']:
                    redshift = float(tns_reply['redshift'])
                classification = tns_reply['object_type']['name']
                internal_names = tns_reply['internal_names']

            else:
                if isinstance(response, Response):
                    tns_query_status = f"""
TNS Request responded with code {response.status_code}: {response.reason}
"""
                else:
                    tns_query_status = f'We ran out of API calls to the TNS with {time_to_wait}s left! This exceeded the {tns_time_limit}s limit!'
                    
                logger.info(tns_query_status)

            # update the target details from the TNS query, if successful, or from the local copy in the database
            if target.name != iau_name:
                target.name = iau_name
                target.save()
                messages.append(f"Found a match in the TNS: {target.name}")
            if classification and target.extra_fields.get('Classification') != classification:
                update_or_create_target_extra(target, 'Classification', classification)
                messages.append(f"Classification set to {classification}")
            if redshift is not None and np.isfinite(redshift) and target.extra_fields.get('Redshift') != redshift:
                update_or_create_target_extra(target, 'Redshift', redshift)
                messages.append(f"Redshift set to {redshift}")
            for alias in internal_names.split(','):
                if (alias and alias.replace(' ', '') != target.name.replace(' ', '')
                        and not TargetName.objects.filter(name=alias).exists()):
                    tn = TargetName.objects.create(target=target, name=alias)
                    messages.append(f'Added alias {tn.name} from TNS')
                
        update_or_create_target_extra(target=target, key='QSO Match', value=qso[0])
        if qso[0] != 'None':
            update_or_create_target_extra(target=target, key='QSO Offset', value=qoffset[0])

        update_or_create_target_extra(target=target, key='ASASSN Match', value=asassn[0])
        if asassn[0] != 'None':
            update_or_create_target_extra(target=target, key='ASASSN Offset', value=asassnoffset[0])

        update_or_create_target_extra(target=target, key='Gaia Match', value=gaia[0])
        if gaia[0] != 'None':
            update_or_create_target_extra(target=target, key='Gaia VS Offset', value=gaiaoffset[0])
            update_or_create_target_extra(target=target, key='Gaia VS Class', value=gaiaclass[0])

        update_or_create_target_extra(target=target, key='PS1 match', value=ps1[0])
        if ps1[0] != 'None' and ps1[0] != 'Multiple matches' and ps1[0] != 'Galaxy match':
            update_or_create_target_extra(target=target, key='PS1 Star Prob.', value=ps1prob[0])
            update_or_create_target_extra(target=target, key='PS1 Offset', value=ps1offset[0])

        matches, hostdict = galaxy_search(target.ra, target.dec, db_connect=DB_CONNECT)
        update_or_create_target_extra(target=target, key='Host Galaxies', value=json.dumps(hostdict))

        if hostdict and target.distance is None:
            dist = hostdict[0].get('Dist', np.nan)
            if np.isfinite(dist):
                target.distance = dist
            disterr = hostdict[0].get('DistErr', np.nan)
            if np.all(np.isfinite(disterr)):
                target.distance_err = np.mean(disterr)
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
                if not TargetName.objects.filter(name=candidate['oid']).exists():
                    tn = TargetName.objects.create(target=target, name=candidate['oid'])
                    messages.append(f'Added alias {tn.name} from ZTF')
        process_reduced_ztf_data(target, newztfphot)

    redshift = target.targetextra_set.filter(key='Redshift')
    if redshift.exists() and redshift.first().float_value >= 0.02 and target.distance is None:
        messages.append(f'Updating distance of {target.name} based on redshift')
        target.distance = COSMOLOGY.luminosity_distance(redshift.first().float_value).to('Mpc').value
        target.save()

    for message in messages:
        logger.info(message)

    return messages, tns_query_status
