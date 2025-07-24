import argparse
import logging 

from   astropy.table import Table
import psycopg

logger = logging.getLogger(__name__)


def get_SQL_type(NEDLVS_dtype: str) -> str:
        conversion_table = {
            '|S19':     "text",
            '|S1':      "text",
            '|S6':      "text",
            '>f8':      "float8",
            '|S30':     "text",
            '|b1':      "bool",
            '>f4':      "float4",
            '|S4':      "text",
            '|S5':      "text"
        }

        return conversion_table[NEDLVS_dtype]

def get_relational_schema(NEDLVS_table: Table) -> str:
    new_schema = "( "

    # ap_types = set() # used in devel to get src data types (in the fits file, not implementation)
    for col_index in range(len(NEDLVS_table.colnames)):
        colname = NEDLVS_table.colnames[col_index]
        np_dtype = str(type(NEDLVS_table[colname][0]))
        ap_dtype = NEDLVS_table[NEDLVS_table.colnames[col_index]].dtype.str
        converted = get_SQL_type(ap_dtype)
        
        # ap_types.add(ap_dtype) # used in devel to get src data types (in the fits file, not implementation)
        new_schema += f"\n{colname.ljust(20)}\t{converted},"

    new_schema = new_schema[:-1] # get rid of the trailing comma bc PGSQL syntax won't ignore it
    new_schema += ")"
    # print(ap_types) # used in devel to get src data types (in the fits file, not implementation)
    return new_schema

def get_SQL_values(NEDLVS_table: Table, rows) -> list[str]:
    # String will look like "INSERT INTO {TABLENAME} VALUES ('<pad string with single quote>', <comma separate everything>);"
    all_values = []
    
    cols = range(len(NEDLVS_table.columns))

    for row_index in rows:
        values = "( "
        for col_index in cols:
            value = str(NEDLVS_table[row_index][col_index])
            valtype = get_SQL_type(NEDLVS_table[NEDLVS_table.colnames[col_index]].dtype.str)

            if value == "--":
                    value = "null"

            if valtype == "text":
                value = value.replace('"', '"""')
                value = value.replace("'", "''")
                values += f"\'{value}\', "
            else:
                values += f"{value}, "

        values = values[:-2] # strip trailing comma bc PGSQL syntax won't accept it
        values += " )"

        all_values.append(values)

    return all_values

def create_SQL_table(POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_TABLE, table_schema):
    # #####################################################################
    # Create the new table using the new schema
    # #####################################################################
    # TODO: overwrite records, overwrite schema, append? This needs a better fallback.
    SQL_statement = f"CREATE TABLE {POSTGRES_TABLE} {table_schema};"
    logger.debug(SQL_statement)
    with psycopg.connect(host=POSTGRES_HOST, user=POSTGRES_USER, port=POSTGRES_PORT, password=POSTGRES_PASSWORD, dbname=POSTGRES_DB) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(SQL_statement)
                conn.commit()
            except psycopg.errors.DuplicateTable:
                logger.debug(f"Table {POSTGRES_TABLE} already exists. Attemtping to continue with existing schema...")
            except Exception as e:
                logger.debug(e)
            finally:
                logger.debug("continuing.")

def insert_values(POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_TABLE, aptable, rows):
    stringified_chunk = ""

    chunked_vals = get_SQL_values(aptable, rows)

    for vals in chunked_vals:
        stringified_chunk += f"{vals}, "

    stringified_chunk = stringified_chunk[:-2] # remove trailing ", "
    
    # make insert statement
    SQL_statement = f"INSERT INTO {POSTGRES_TABLE} VALUES {stringified_chunk};"

    # connect to db & insert
    with psycopg.connect(host=POSTGRES_HOST, user=POSTGRES_USER, port=POSTGRES_PORT, password=POSTGRES_PASSWORD, dbname=POSTGRES_DB) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(SQL_statement)
                conn.commit()
            except Exception as e:
                logger.debug(e)

def parse_and_insert(POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_TABLE, POSTGRES_USER, POSTGRES_PASSWORD, source_data, chunksize):
    
    table = Table.read(source_data)

    new_schema = get_relational_schema(table)

    create_SQL_table(POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_TABLE, new_schema)

    # #####################################################################
    # Insert records for each chunk
    # #####################################################################
    for chunk in range((len(table) // chunksize) + 1):
        logger.info(f"chunk {chunk} of {(len(table) // chunksize) + 1}")
        
        rows = range(chunk * chunksize, min(chunk * chunksize + chunksize, len(table)))
        
        insert_values(POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_TABLE, table, rows)


# #####################################################################
# Run as standalone application
# #####################################################################
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info(f"started {__file__}.")        

    # #####################################################################
    # Accept command line args
    # #####################################################################
    parser = argparse.ArgumentParser(
        prog="NEDLVS2PGSQL",
        description="Parse FITS file of NEDLVS2 data and insert records \
            into a Postgres DB")
    
    parser.add_argument('--nedlvs_file',                    help='FITS file of NASA/IPAC Extragalactic Database (NED) Local Volume Sample (LVS)')
    parser.add_argument('--pghost',                         help='Host that a PGSQL server runs on. Can be \"localhost\", a domain name, or IP address.')
    parser.add_argument('--pgport', default=5432,           help='Port number the PGSQL listens on. Default is 5432.')
    parser.add_argument('--pgdb',                           help='Name of the PG database to target.')
    parser.add_argument('--pgtable',                        help='Name of table within the PG database to target.')
    parser.add_argument('--pguser',                         help='User name necessary to access PGSQL server. This is configured by the server.')
    parser.add_argument('--pgpasswd',                       help='User password to access PGSQL server. This is confiugred by the server.')
    parser.add_argument('--chunksize', type=int, default=1, help='Count of howe many `VALUES` to INSERT per SQL statement.')

    args = parser.parse_args()

    # #####################################################################
    # Do everything
    # #####################################################################
    parse_and_insert(args.pghost, args.pgport, args.pgdb, args.pgtable, args.pguser, args.pgpasswd, args.nedlvs_file, args.chunksize)
    
    logger.info(f"{__file__} done.")

