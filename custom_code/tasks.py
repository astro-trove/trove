import logging
from kne_cand_vetting.mpc import minor_planet_match
from tom_dataproducts.models import ReducedDatum
from astropy.time import Time
from django_tasks import task

from .hooks import update_or_create_target_extra

logger = logging.getLogger(__name__)


@task(queue_name="mpc")
def target_run_mpc(latest_det_id, _verbose=False):
    """check if a given photometric detection is a minor planet"""
    latest_det = ReducedDatum.objects.get(id=latest_det_id)
    match = minor_planet_match(latest_det.target.ra, latest_det.target.dec, Time(latest_det.timestamp).mjd)
    if match is not None:
        name, sep = match
        update_or_create_target_extra(latest_det.target, 'Minor Planet Match', name)
        update_or_create_target_extra(latest_det.target, 'Minor Planet Date', latest_det.timestamp)
        update_or_create_target_extra(latest_det.target, 'Minor Planet Offset', sep)
        logger.info(f'{latest_det.target.name} is {sep:.1f}" from minor planet {name}')
    else:
        update_or_create_target_extra(latest_det.target, 'Minor Planet Match', 'None')
        update_or_create_target_extra(latest_det.target, 'Minor Planet Date', latest_det.timestamp)
        logger.info(f"{latest_det.target.name} is not a minor planet!")
