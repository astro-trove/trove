import argparse
import importlib
import logging
import os
import sys

from   astropy.table import Table
import psycopg

logger = logging.getLogger(__name__)


def get_SQL_type(dtype: str, conversion_table: dict) -> str:
        return conversion_table[dtype]


def get_relational_schema(data_table: Table, conversion_table: dict) -> str:
    new_schema = "( "

    # ap_types = set() # used in devel to get src data types (in the fits file, not implementation)
    for col_index in range(len(data_table.colnames)):
        colname = data_table.colnames[col_index]
        np_dtype = str(type(data_table[colname][0]))
        ap_dtype = data_table[data_table.colnames[col_index]].dtype.str
        converted = get_SQL_type(ap_dtype, conversion_table)
        
        # ap_types.add(ap_dtype) # used in devel to get src data types (in the fits file, not implementation)
        new_schema += f"\n{colname.ljust(20)}\t{converted},"

    new_schema = new_schema[:-1] # get rid of the trailing comma bc PGSQL syntax won't ignore it
    new_schema += ")"
    # print(ap_types) # used in devel to get src data types (in the fits file, not implementation)
    return new_schema


def get_SQL_values(data_table: Table, rows, conversion_table: dict) -> list[str]:
    all_values = []
    
    cols = range(len(data_table.columns))

    for row_index in rows:
        values = "( "
        for col_index in cols:
            value = str(data_table[row_index][col_index])
            valtype = get_SQL_type(data_table[data_table.colnames[col_index]].dtype.str, conversion_table)

            # #####################################################################
            # TODO: make a more comprehensive, flexible fun. for cleaning
            # #####################################################################
            if value == "--":
                    value = "null"

            if valtype == "text":
                value = value.replace('"', '"""') # SQL escapes a quote with another quote
                value = value.replace("'", "''")
                values += f"\'{value}\', "
            else:
                values += f"{value}, "

        values = values[:-2] # strip trailing comma bc PGSQL syntax won't accept it
        values += " )"

        all_values.append(values)

    return all_values


def create_SQL_table(POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_TABLE, 
                     table_schema: str):
    # #####################################################################
    # TODO: drop table & create new, edit in place?
    # #####################################################################
    SQL_statement = f"CREATE TABLE {POSTGRES_TABLE} {table_schema};"
    logger.debug(SQL_statement)
    with psycopg.connect(host=POSTGRES_HOST, port=POSTGRES_PORT, dbname=POSTGRES_DB, user=POSTGRES_USER, password=POSTGRES_PASSWORD) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(SQL_statement)
                conn.commit()
            except psycopg.errors.DuplicateTable:
                logger.debug(f"Table {POSTGRES_TABLE} already exists. Attemtping to continue with existing schema...")
            except Exception as e: raise
            finally:
                logger.debug("continuing.")


def insert_values(POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_TABLE, 
                  data_table, conversion_table: dict, rows: list[str]):
    stringified_chunk = ""
    SQL_statement = ""

    chunked_vals = get_SQL_values(data_table, rows, conversion_table)

    for vals in chunked_vals:
        stringified_chunk += f"{vals}, "

    stringified_chunk = stringified_chunk[:-2] # remove trailing ", "
    
    SQL_statement = f"INSERT INTO {POSTGRES_TABLE} VALUES {stringified_chunk};"

    # connect to db & insert
    with psycopg.connect(host=POSTGRES_HOST, user=POSTGRES_USER, port=POSTGRES_PORT, password=POSTGRES_PASSWORD, dbname=POSTGRES_DB) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(SQL_statement)
                conn.commit()
            except Exception as e:
                logger.debug(e)


def q3c_index_table(POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_TABLE):
    SQL_statement = f"CREATE INDEX ON {POSTGRES_TABLE} (q3c_ang2ipix(ra, dec)); "
    with psycopg.connect(host=POSTGRES_HOST, user=POSTGRES_USER, port=POSTGRES_PORT, password=POSTGRES_PASSWORD, dbname=POSTGRES_DB) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(SQL_statement)
                conn.commit()
            except Exception as e:
                logger.debug(e) 


def parse_and_insert(POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_TABLE, POSTGRES_USER, POSTGRES_PASSWORD, 
                     source_data: Table, conversion_table: dict, processing: callable, chunksize: int):

    # #####################################################################
    # Read source data
    # #####################################################################
    logger.debug("reading file into astropy table...")
    table = Table.read(source_data)

    # TODO: do this in chunks to save mem
    table = processing(table) # mod data, colnames, etc. defined in config

    logger.debug("done.")

    # #####################################################################
    # Define new table schema
    # #####################################################################
    logger.debug("stringifying new table schema...")
    new_schema = get_relational_schema(table, conversion_table)
    logger.debug("done.")

    # #####################################################################
    # Create new table in DB
    # #####################################################################
    logger.debug("requesting pgsql server create new table...")
    create_SQL_table(POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_TABLE, new_schema)
    logger.debug("done.")

    # #####################################################################
    # Insert records for each chunk
    # #####################################################################
    for chunk in range((len(table) // chunksize) + 1):
        logger.info(f"inserting chunk {chunk + 1} of {(len(table) // chunksize) + 1}")
        
        rows = range(chunk * chunksize, min(chunk * chunksize + chunksize, len(table)))
        
        insert_values(POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_TABLE, table, 
                      conversion_table, rows)
    logger.info(f"Done inserting values.")

    # #####################################################################
    # Index table using Q3C
    # #####################################################################
    logger.debug(f"Performing Q3C indexing...")
    q3c_index_table(POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_TABLE)
    logger.debug(f"done.")


# ##########################################################################################################################################
# Run as standalone application
# ##########################################################################################################################################
if __name__ == "__main__":
    """
    # example bash script:

    #! /usr/bin/bash

    micromamba activate /var/www/saguaro_tom/TroveEnv/

    python /var/www/saguaro_tom/custom_code/management/ingest.py /data/catalogs/NEDLVS_20250602.fits
    <POSTGRES_HOST> <POSTGRES_PORT> <POSTGRES_DB> <POSTGRES_TABLE> <POSTGRES_USER> <POSTGRES_PASSWORD> # do not enter passwords in CLI! (store in files w/ access controls, like this ex. bash script)
    /var/www/saguaro_tom/custom_code/management/ingest.py /data/catalogs/NEDLVS_config.py
    100000

    """

    logging.basicConfig(level=logging.DEBUG)
    logger.info(f"started {__file__}.")        

    # #####################################################################
    # Accept command line args
    # #####################################################################
    parser = argparse.ArgumentParser(
        prog="Catalog2PGSQL",
        description="Parse catalog file and insert records into a Postgres DB")
    
    parser.add_argument('--catalog_file',                   help='Astropy-parseable data catalog file.')
    parser.add_argument('--pghost',                         help='Host that a PGSQL server runs on. Can be \"localhost\", a domain name, or IP address.')
    parser.add_argument('--pgport', default=5432,           help='Port number the PGSQL server listens on. Default is 5432.')
    parser.add_argument('--pgdb',                           help='Name of the PG database to target.')
    parser.add_argument('--pgtable',                        help='Name of table within the PG database to target.')
    parser.add_argument('--pguser',                         help='User name necessary to access PGSQL server. This is configured by the server.')
    parser.add_argument('--pgpasswd',                       help='User password to access PGSQL server. This is confiugred by the server.')
    parser.add_argument('--config',                         help='Catalogue-specific configs.')
    parser.add_argument('--chunksize', type=int, default=1, help='Count of how many `VALUES` to `INSERT` per SQL statement.')

    args = parser.parse_args()

    # #####################################################################
    # Dynamically import config file
    # #####################################################################
    # TODO: ugly way of dynamically importing
    sys.path.insert(0, os.path.dirname(args.config))
    config = importlib.import_module(os.path.basename(args.config).replace(".py", ""))
    sys.path = sys.path[1:] # cleanup. remove BY INDEX, not value!

    # #####################################################################
    # Do everything
    # #####################################################################
    parse_and_insert(args.pghost, args.pgport, args.pgdb, args.pgtable, args.pguser, args.pgpasswd, args.catalog_file, 
                     config.conversion_table, config.processing, args.chunksize)
    
    logger.info(f"{__file__} done.")

