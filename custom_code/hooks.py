import logging
from kne_cand_vetting.catalogs import static_cats_query
from kne_cand_vetting.galaxy_matching import galaxy_search
from kne_cand_vetting.survey_phot import query_ZTFpubphot
from tom_targets.models import TargetExtra
from tom_alerts.brokers.mars import MARSBroker
import json
from saguaro_tom.settings import DB_USER, DB_PASS, DB_HOST, DB_PORT, DB_NAME

DB_CONNECT = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

logger = logging.getLogger(__name__)


def target_post_save(target, created):
    """This hook runs following update of a target."""
    logger.info('Target post save hook: %s created: %s', target, created)

    if created:
        qprob, qso, qoffset, asassnprob, asassn, asassnoffset = static_cats_query(target.ra, target.dec,
                                                                                  db_connect=DB_CONNECT)
        TargetExtra(target=target, key='QSO Match', value=qso[0]).save()
        if qso[0] != 'None':
            TargetExtra(target=target, key='QSO Prob.', value=qprob[0]).save()
            TargetExtra(target=target, key='QSO Offset', value=qoffset[0]).save()
        TargetExtra(target=target, key='ASASSN Match', value=asassn[0]).save()
        if asassn[0] != 'None':
            TargetExtra(target=target, key='ASASSN Prob.', value=asassnprob[0]).save()
            TargetExtra(target=target, key='ASASSN Offset', value=asassnoffset[0]).save()

        matches, hostdict = galaxy_search(target.ra, target.dec, db_connect=DB_CONNECT)
        TargetExtra(target=target, key='Host Galaxies', value=json.dumps(hostdict)).save()

        ztfphot = query_ZTFpubphot(target.ra, target.dec, db_connect=DB_CONNECT)
        if ztfphot:
            # emulate the MARS alert format
            alert = ztfphot[0]
            alert['lco_id'] = alert.pop('zid')
            if len(ztfphot) > 1:
                alert['prv_candidate'] = ztfphot[1:]

            # process using the built-in MARS broker interface
            mars = MARSBroker()
            mars.name = 'ZTF'
            mars.process_reduced_data(target, alert)
