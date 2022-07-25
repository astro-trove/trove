import logging
from kne_cand_vetting.catalogs import static_cats_query
from kne_cand_vetting.galaxy_matching import galaxy_search
from tom_targets.models import TargetExtra
import json

logger = logging.getLogger(__name__)


def target_post_save(target, created):
    """This hook runs following update of a target."""
    logger.info('Target post save hook: %s created: %s', target, created)

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
