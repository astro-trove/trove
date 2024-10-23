import logging
from kne_cand_vetting.catalogs import tns_query
from kne_cand_vetting.mpc import is_minor_planet
from tom_targets.models import Target
from astropy.cosmology import FlatLambdaCDM
from astropy.time import Time
from django.conf import settings
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
import dramatiq

from .hooks import update_or_create_target_extra

DB_CONNECT = "postgresql+psycopg2://{USER}:{PASSWORD}@{HOST}:{PORT}/{NAME}".format(**settings.DATABASES['default'])
COSMOLOGY = FlatLambdaCDM(H0=70., Om0=0.3)

logger = logging.getLogger(__name__)


@dramatiq.actor
def target_run_mpc(target_pk, _verbose=False):

    target = Target.objects.get(pk=target_pk)

    phot = target.reduceddatum_set.filter(data_type="photometry")
    if not phot.exists():
        logger.warning("No photometry for this object! Can not check if it is a minor planet!")
        return

    # we unfortunately have to iterate over all of the photometry points
    # this is because the phot.earliest() method will include upperlimits
    # and there is no way to easily filter out upperlimits
    first_det = None
    for item in phot.order_by("timestamp").iterator():
        if "limit" in item.value:
            continue # this is an upperlimit
        first_det = item
        break
    
    if first_det is None:
        logger.warning("All of the photometry was upperlimits! Can not check if it is a minor planet!")
        return

    first_time = first_det.timestamp
    
    # then we can select the first detection and correct for times not in UTC
    utc_offset = first_time.utcoffset()
    if utc_offset is not None:
        utc = first_time - utc_offset
    else:
        utc = first_time
        
    first_mjd = Time(utc, format="datetime")

    # then check if it is an asteroid!
    if is_minor_planet(target.ra, target.dec, first_mjd):
        new_class_str = "Minor Planet/Asteroid"
        classification = target.extra_fields.get("Classification", None)
        if classification and new_class_str not in classification:
            new_class = f'{new_class_str} (TNS: {classification})'
        else:
            new_class = new_class_str

        update_or_create_target_extra(target, 'Classification', new_class)

        logger.info("Found this candidate to be a minor planet!")
    else:
        logger.info("Not a minor planet!")
        
    logger.info("MPC Check Complete!")
