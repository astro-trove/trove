import logging
from tom_targets.models import TargetExtra
from tom_dataproducts.models import ReducedDatum
from astropy.cosmology import FlatLambdaCDM
from astropy.time import Time, TimezoneInfo
from astropy.coordinates import SkyCoord
from astroquery.ipac.irsa.irsa_dust import IrsaDust
from healpix_alchemy.constants import HPX

COSMOLOGY = FlatLambdaCDM(H0=70., Om0=0.3)

logger = logging.getLogger(__name__)
new_format = logging.Formatter('[%(asctime)s] %(levelname)s : s%(message)s')
for handler in logger.handlers:
    handler.setFormatter(new_format)

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


def target_post_save(target, created, tns_time_limit: float=5.):
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

        # TODO: add all the vetting code
        #  - cone search on static copy of the TNS
        #  - if TNS match, query live TNS (handling any timeouts) for
        #    photometry, coord/name updates, redshift, spec class, aliases
        #  - cone search for point source matches
        #  - larger cone search for host galaxy matches
        #  - ZTF/LSST photometry & aliases from brokers
        #  - minor planet checker (spawn async process if slow)

    redshift = target.targetextra_set.filter(key='Redshift')
    if redshift.exists() and redshift.first().float_value >= 0.02 and target.distance is None:
        messages.append(f'Updating distance of {target.name} based on redshift')
        target.distance = COSMOLOGY.luminosity_distance(redshift.first().float_value).to('Mpc').value
        target.save()

    for message in messages:
        logger.info(message)

    return messages, tns_query_status
