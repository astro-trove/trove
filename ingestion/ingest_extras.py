import logging

import psycopg2

logger = logging.getLogger(__name__)

class DBctxt():
    def __init__(self, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, sql_table):
        self.POSTGRES_HOST = POSTGRES_HOST
        self.POSTGRES_PORT = POSTGRES_PORT
        self.POSTGRES_USER = POSTGRES_USER
        self.POSTGRES_PASSWORD = POSTGRES_PASSWORD
        self.POSTGRES_DB = POSTGRES_DB
        self.sql_table = sql_table

def execute_statement(dbctxt: DBctxt, SQL_statement: str):
    logger.debug("Executing SQL statement")
    with psycopg2.connect(host=dbctxt.POSTGRES_HOST, port=dbctxt.POSTGRES_PORT, dbname=dbctxt.POSTGRES_DB, user=dbctxt.POSTGRES_USER, password=dbctxt.POSTGRES_PASSWORD) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(SQL_statement)
                conn.commit()
            except Exception as e: raise
            finally:
                logger.debug("Done.")

def q3c_index_table(dbctxt: DBctxt, ra: str, dec: str):
    SQL_statements = [f"CREATE INDEX ON {dbctxt.sql_table} (q3c_ang2ipix({ra}, {dec}));", \
                      f"CLUSTER {dbctxt.sql_table}_q3c_ang2ipix_idx ON {dbctxt.sql_table};"] # create index creates index named <tablename>_q3c_ang2ipix_idx
    
    execute_statement(dbctxt, SQL_statements[0])
    execute_statement(dbctxt, SQL_statements[1])

