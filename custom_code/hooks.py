import logging
from kne_cand_vetting.catalogs import static_cats_query
from tom_targets.models import TargetExtra

logger = logging.getLogger(__name__)


def target_post_save(target, created):
    """This hook runs following update of a target."""
    logger.info('Target post save hook: %s created: %s', target, created)

    qprob, qso, qoffset = static_cats_query([target.ra], [target.dec])
    TargetExtra(target=target, key='qprob', value=qprob).save()
    TargetExtra(target=target, key='qso', value=qso).save()
    TargetExtra(target=target, key='qoffset', value=qoffset).save()
