import logging
from datetime import datetime, timedelta, timezone
import numpy as np
from tom_targets.models import TargetExtra
from tom_nonlocalizedevents.models import NonLocalizedEvent
from tom_dataproducts.models import ReducedDatum

from candidate_vetting.vet_phot import find_public_phot
from candidate_vetting.vet_bns import vet_bns
from candidate_vetting.vet_kn_in_sn import vet_kn_in_sn
from candidate_vetting.vet_super_kn import vet_super_kn
from candidate_vetting.vet_basic import vet_basic

from candidate_vetting.public_catalogs.phot_catalogs import TNS_Phot
from custom_code.healpix_utils import create_candidates_from_targets
from custom_code.templatetags.skymap_extras import get_preferred_localization
from trove_targets.models import Target
from astropy.cosmology import FlatLambdaCDM
from astropy.time import Time, TimezoneInfo
from astropy.coordinates import SkyCoord
from astroquery.ipac.irsa.irsa_dust import IrsaDust
from django.conf import settings

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

def get_active_nonlocalizedevents(t0=None, lookback_days=3., test=False):
    """
    Returns a queryset containing "active" NonLocalizedEvents, significant events that happened less than
    `lookback_days` before `t0` and have not been retracted. Use `test=True` to query mock events instead of real ones.
    """
    if t0 is None:
        t0 = datetime.now(tz=timezone.utc)
    lookback_window_nle = (t0 - timedelta(days=lookback_days)).isoformat()
    active_nles = NonLocalizedEvent.objects.filter(sequences__details__time__gte=lookback_window_nle, state='ACTIVE')
    active_nles = active_nles.exclude(sequences__details__significant=False)
    if test:
        active_nles = active_nles.filter(event_id__startswith='MS')
    else:
        active_nles = active_nles.exclude(event_id__startswith='MS')
    return active_nles.distinct()
    
def associate_nle_with_target(target:Target, lookback_days_nle, lookback_days_obs):
    # automatically associate with nonlocalized events
    new_candidates = []
    for nle in get_active_nonlocalizedevents(lookback_days=lookback_days_nle):
        seq = nle.sequences.last()
        localization = get_preferred_localization(nle)
        nle_time = datetime.strptime(seq.details['time'], '%Y-%m-%dT%H:%M:%S.%f%z')
        target_ids = []
        first_det = target.reduceddatum_set.filter(data_type='photometry', value__magnitude__isnull=False
                                                   ).order_by('timestamp').first()
        if first_det and nle_time < first_det.timestamp < nle_time + timedelta(days=lookback_days_obs):
            target_ids.append(target.id)
        
        new_candidates += create_candidates_from_targets(seq, target_ids=target_ids)

    return new_candidates
    
def target_post_save(target, created=True, lookback_days_nle=7, lookback_days_obs=7):
    """This hook runs following update of a target."""
    logger.info('Target post save hook: %s created: %s', target, created)

    messages = []
    tns_query_status = None
    if created:
        if target.extra_fields.get('MW E(B-V)') is None:
            coord = SkyCoord(target.ra, target.dec, unit='deg')
            try:
                mwebv = IrsaDust.get_query_table(coord, section='ebv')['ext SandF ref'][0]
            except Exception as e:
                logger.error(f'Error querying IRSA dust for {target.name}')
            else:
                update_or_create_target_extra(target, 'MW E(B-V)', mwebv)
                messages.append(f'MW E(B-V) set to {mwebv:.4f}')

        # do the "basic" vetting (PS, MPC, Host association)
        vet_basic(target.id)
        
        # then check if this target is associated with any NLEs
        new_candidates = associate_nle_with_target(
            target,
            lookback_days_nle=lookback_days_nle,
            lookback_days_obs=lookback_days_obs
        )
        
        # TODO: add a check for the type of non-localized event
        #       For now we are just always all types of vetting
        if len(new_candidates):
            for cand in new_candidates:
                vet_bns(cand.target.id, cand.nonlocalizedevent.event_id)
                vet_kn_in_sn(cand.target.id, cand.nonlocalizedevent.event_id)
                vet_super_kn(cand.target.id, cand.nonlocalizedevent.event_id)
        else:
            messages.append("Could not run vetting on this target because there are no non-localized events associated with it!")
            
    redshift = target.targetextra_set.filter(key='Redshift')
    if redshift.exists() and redshift.first().float_value >= 0.02 and target.distance is None:
        messages.append(f'Updating distance of {target.name} based on redshift')
        target.distance = settings.COSMO.luminosity_distance(target.redshift).to('Mpc').value
        target.save()

    for message in messages:
        logger.info(message)

    return messages, tns_query_status
