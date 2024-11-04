import logging
from kne_cand_vetting.mpc import minor_planet_match
from tom_targets.models import Target
from astropy.time import Time
import dramatiq

from .hooks import update_or_create_target_extra

logger = logging.getLogger(__name__)


@dramatiq.actor
def target_run_mpc(target_pk, _verbose=False):

    target = Target.objects.get(pk=target_pk)

    # get the time of the latest detection from the photometry set
    phot = target.reduceddatum_set.filter(data_type="photometry", value__magnitude__isnull=False)
    if not phot.exists():
        logger.warning(f"No detections of {target.name}! Can not check if it is a minor planet!")
        return
    latest_det = Time(phot.latest().timestamp).mjd
    
    # then check if it is an asteroid!
    match = minor_planet_match(target.ra, target.dec, latest_det)
    if match is not None:
        name, sep = match
        update_or_create_target_extra(target, 'Minor Planet Match', name)
        update_or_create_target_extra(target, 'Minor Planet Offset', sep)
        logger.info(f'{target.name} is {sep:.1f}" from minor planet {name}')
    else:
        update_or_create_target_extra(target, 'Minor Planet Match', 'None')
        logger.info(f"{target.name} is not a minor planet!")
        
    logger.info(f"MPC Check for {target.name} Complete!")
