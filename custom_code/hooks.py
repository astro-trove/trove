import logging
from kne_cand_vetting.catalogs import static_cats_query
from kne_cand_vetting.galaxy_matching import galaxy_search
from kne_cand_vetting.survey_phot import query_ZTFpubphot
from tom_targets.models import TargetExtra
from tom_dataproducts.models import ReducedDatum
import json
from astropy.time import Time, TimezoneInfo

logger = logging.getLogger(__name__)

filters = {1: 'g', 2: 'r', 3: 'i'}

def target_post_save(target, created):
    """This hook runs following update of a target."""
    logger.info('Target post save hook: %s created: %s', target, created)

    if created:
        qprob, qso, qoffset, asassnprob, asassn, asassnoffset = static_cats_query([target.ra], [target.dec])
        TargetExtra(target=target, key='QSO Match', value=qso[0]).save()
        if qso[0] != 'None':
            TargetExtra(target=target, key='QSO Prob.', value=qprob[0]).save()
            TargetExtra(target=target, key='QSO Offset', value=qoffset[0]).save()
        TargetExtra(target=target, key='ASASSN Match', value=asassn[0]).save()
        if asassn[0] != 'None':
            TargetExtra(target=target, key='ASASSN Prob.', value=asassnprob[0]).save()
            TargetExtra(target=target, key='ASASSN Offset', value=asassnoffset[0]).save()

        matches, hostdict = galaxy_search([target.ra], [target.dec])
        TargetExtra(target=target, key='Host Galaxies', value=json.dumps(hostdict)).save()

        ztfphot = query_ZTFpubphot(target.ra,target.dec)
        for candidate in ztfphot:
            if all([key in candidate['candidate'] for key in ['jd', 'magpsf', 'fid', 'sigmapsf']]):
                nondetection = False
            elif all(key in candidate['candidate'] for key in ['jd', 'diffmaglim', 'fid']):
                nondetection = True
            else:
                continue
            jd = Time(candidate['candidate']['jd'], format='jd', scale='utc')
            jd.to_datetime(timezone=TimezoneInfo())
            value = {
                'filter': filters[candidate['candidate']['fid']]
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
                data_type='photometry',
                target=target)
