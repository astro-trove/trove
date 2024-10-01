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
    
    discovery_mjd = target.extra_fields.get("DiscoveryDate", None)
    if discovery_mjd is None:
        logger.info("Querying TNS for the discovery date")

        # try to pull the discovery date from TNS
        try:
            engine = create_engine(DB_CONNECT)
            get_session = sessionmaker(bind=engine)
            session = get_session()
        except Exception as _e2:
            if _verbose:
                print(f"{_e2}")
            raise Exception(f"Failed to connect to database")

        coord = tuple(zip([target.ra], [target.dec]))
        radius = 2 / 3600 # arcseconds to degrees
        tns_results = tns_query(session, coord, radius)
        for iau_name, redshift, classification, internal_names, discoverydate in tns_results:
            if target.name[2:] == iau_name[2:]:  # choose the name that already matches, if more than one
                break
        
        discovery_mjd = Time(discoverydate.isoformat(), format='isot').mjd

    if is_minor_planet(target.ra, target.dec, discovery_mjd):
        if classification:
            new_class = f'Minor Planet/Asteroid (TNS: {classification})'
        else:
            new_class = 'Minor Planet/Asteroid'
            update_or_create_target_extra(target, 'Classification', new_class)

        logger.info("Found this candidate to be a minor planet!")
    else:
        logger.info("Not a minor planet!")
        
    logger.info("MPC Check Complete!")
