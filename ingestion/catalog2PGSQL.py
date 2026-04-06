import argparse
import logging

from ingest_extras import *
import catalog_config

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def parse_and_insert(dbctxt: DBctxt, catalog_path: str, catalog_type: str):
    match(catalog_config.Catalogs[catalog_type]):
        case catalog_config.Catalogs.DESIDR1:
            datain = catalog_config.DESIDR1Config(dbctxt, catalog_path)
            datain.insert_all()
        case catalog_config.Catalogs.FERMILPSC:
            datain = catalog_config.BasicAstropyConfig(dbctxt, catalog_path)
            datain.insert_all()
        case catalog_config.Catalogs.FERMI3FHL:
            datain = catalog_config.BasicAstropyConfig(dbctxt, catalog_path)
            datain.insert_all()
        case catalog_config.Catalogs.NEDLVS:
            datain = catalog_config.BasicAstropyConfig(dbctxt, catalog_path)
            datain.insert_all()
        case catalog_config.Catalogs.TWOMASS:
            datain = catalog_config.TwoMASSConfig(dbctxt, catalog_path)
            datain.insert_all()
        case catalog_config.Catalogs.ZTFVARSTAR:
            datain = catalog_config.ZTFVarStarConfig(dbctxt, catalog_path)
            datain.insert_all()


# ##############################################################################
# Run as standalone application
# ##############################################################################
if __name__ == "__main__":
    logger.info(f"started {__file__}.")

    # ##########################################################################
    # Accept command line args
    # ##########################################################################
    parser = argparse.ArgumentParser(
        prog="Catalog2PGSQL",
        description="Parse catalog file and insert records into a Postgres DB")
    
    parser.add_argument('--pghost',               help='Host that a PGSQL server runs on. Can be \"localhost\", a domain name, or IP address.')
    parser.add_argument('--pgport', default=5432, help='Port number the PGSQL server listens on. Default is 5432.')
    parser.add_argument('--pguser',               help='User name necessary to access PGSQL server. This is configured by the server.')
    parser.add_argument('--pgpasswd',             help='User password to access PGSQL server. This is confiugred by the server.')
    parser.add_argument('--pgdb',                 help='Name of the PG database to target.')
    parser.add_argument('--sqltable',             help='Name of table within the PG database to target.')
    parser.add_argument('--catalog_type',         help='Catalogue-specific configs.')
    parser.add_argument('--catalog_path',         help='Astropy-parseable data catalog file.')

    args = parser.parse_args()

    datatrovedb = DBctxt(args.pghost, args.pgport, args.pguser, args.pgpasswd, args.pgdb, args.sqltable)

    parse_and_insert(datatrovedb, args.catalog_path, args.catalog_type)
    
    logger.info(f"{__file__} done.")
