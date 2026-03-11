from enum import Enum
import gzip
import os
import logging

from astropy.table import Table
import fitsio
import numpy as np
import psycopg2

from ingest_extras import *
import numpy2PGSQL

logger = logging.getLogger(__name__)

Catalogs = Enum('Catalogs', 
[
    ('DESIDR1',   'DESI_DR1'),
    ('FERMILPSC', 'Fermi_LPSC'),
    ('FERMI3FHL', 'Fermi_3FHL'),
    ('NEDLVS',    'NEDLVS'),
    ('TWOMASS',   'Two_MASS'),
    ('ZTFVARSTAR','ZTF_varstar')
])

class CatalogConfig():
    def __init__(self, dbctxt: DBctxt, path: str):
        self.dbctxt:            DBctxt = dbctxt
        self.path:              str    = path
        self.relational_schema: str    = None
        self.data                      = None

    # ##########################################################################
    # "private"
    # ##########################################################################
    def _tabularize(self, path: str):
        # ex: return Table.read(path)
        raise NotImplementedError()
    
    def _clean_data(self):
        raise NotImplementedError()
    
    def _relational_schema(self):
        raise NotImplementedError()

    def _create_table(self):
        raise NotImplementedError()

    def _data2SQLValues(self) -> str:
        raise NotImplementedError()

    # ##########################################################################
    # "public"
    # ##########################################################################
    def insert_all(self):
        raise NotImplementedError()


class BasicAstropyConfig(CatalogConfig):
    def __init__(self, dbctxt: DBctxt, path: str, chunk_rows: int = 100000):
        super().__init__(dbctxt, path)

        self.chunk_rows: int = int(chunk_rows)

        BasicAstropyConfig._tabularize(self, path)
        BasicAstropyConfig._clean_data(self)
        BasicAstropyConfig._relational_schema(self)

        self.ra  = "ra"
        self.dec = "dec"


    def _tabularize(self, path):
        self.table = Table.read(path)

    def _clean_data(self):
        pass # no modifications to table are necessary

    def _relational_schema(self):
            self.relational_schema = "( "

            # ap_types = set() # used in devel to get src data types (in the fits file, not implementation)
            for col_index in range(len(self.table.colnames)):
                colname = self.table.colnames[col_index]
                np_dtype = str(type(self.table[colname][0]))
                ap_dtype = self.table[colname].dtype.str
                pg_type = numpy2PGSQL.convert(ap_dtype)
                
                # ap_types.add(ap_dtype) # used in devel to get src data types (in the fits file, not implementation)
                self.relational_schema += f"\n{colname.ljust(20)}\t{pg_type},"
        
            self.relational_schema = self.relational_schema[:-1] # get rid of the trailing comma bc PGSQL syntax won't ignore it
            self.relational_schema += ")"
            return self.relational_schema

    def _create_table(self):
        logger.info(f"Creating new table in database {self.dbctxt.POSTGRES_DB} on host {self.dbctxt.POSTGRES_HOST}.")

        SQL_statement = ""
        
        SQL_statement += f"DROP TABLE IF EXISTS {self.dbctxt.sql_table};\n"
        
        SQL_statement += f"CREATE TABLE {self.dbctxt.sql_table} {self.relational_schema};"
        with psycopg2.connect(host=self.dbctxt.POSTGRES_HOST, port=self.dbctxt.POSTGRES_PORT, dbname=self.dbctxt.POSTGRES_DB, user=self.dbctxt.POSTGRES_USER, password=self.dbctxt.POSTGRES_PASSWORD) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(SQL_statement)
                    conn.commit()
                except psycopg2.errors.DuplicateTable:
                    raise f"Table {self.dbctxt.sql_table} already exists. Attemtping to continue with existing schema..."
                except Exception as e: raise

        logger.info("done creating table.")
                
    def _data2SQLValues(self, rows: range) -> str:
        all_values = []
    
        cols = range(len(self.table.columns))

        for row_index in rows:
            values = "( "
            for col_index in cols:
                value = str(self.table[row_index][col_index])
                valtype = numpy2PGSQL.convert(self.table[self.table.colnames[col_index]].dtype.str)

                # ##############################################################
                # TODO: DRY: delegate to a fun. for cleaning
                # ##############################################################
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
    
    def insert_all(self):
        self._create_table()
        
        for i in range(0, len(self.table), self.chunk_rows):

            stringified_chunk = ""
            SQL_statement = ""
            rows = range(i, min(len(self.table), i + self.chunk_rows))
            
            logger.info(f"Inserting values for rows {rows.start}-{rows.stop} of {len(self.table)}.")
            
            chunked_vals = self._data2SQLValues(rows)

            stringified_chunk = ", ".join(chunked_vals)

            SQL_statement = f"INSERT INTO {self.dbctxt.sql_table} VALUES {stringified_chunk};"

            # connect to db & insert
            with psycopg2.connect(host=self.dbctxt.POSTGRES_HOST, user=self.dbctxt.POSTGRES_USER, port=self.dbctxt.POSTGRES_PORT, password=self.dbctxt.POSTGRES_PASSWORD, dbname=self.dbctxt.POSTGRES_DB) as conn:
                with conn.cursor() as cur:
                    try:
                        cur.execute(SQL_statement)
                        conn.commit()
                    except Exception as e:
                        raise e
            logger.info("done.")

        q3c_index_table(self.dbctxt, self.ra, self.dec)


class DESIDR1Config(BasicAstropyConfig):
    COEFF_COUNT = 10
    COEFF_INDEX = 9
    
    def __init__(self, dbctxt: DBctxt, path: str, chunk_rows: int = 100000):
        super().__init__(dbctxt, path)
        self.ra  = "TARGET_RA"
        self.dec = "TARGET_DEC"

    def _tabularize(self, path):
        logger.debug(f"Opening data file {path}...")
        try:
            self.table = Table(fitsio.read(path, ext=1)) # this will take a while, this is a large file
        except Exception as e:
            logger.debug(e)

        logger.debug("done loading data file.")

    def _clean_data(self):
        oldcol = self.table['COEFF'].data
        self.table.remove_column('COEFF')
        for i in range(DESIDR1Config.COEFF_COUNT):
            self.table.add_column(oldcol[:, i], index=DESIDR1Config.COEFF_INDEX + i, name=f"COEFF_{i:02}")



class TwoMASSConfig(CatalogConfig):
    relational_schema = [
        "ra double precision",
        "decl double precision",
        "err_maj real",
        "err_min real",
        "err_ang smallint",
        "designation character(17)",
        "j_m real",
        "j_cmsig real",
        "j_msigcom real",
        "j_snr real",
        "h_m real",
        "h_cmsig real",
        "h_msigcom real",
        "h_snr real",
        "k_m real",
        "k_cmsig real",
        "k_msigcom real",
        "k_snr real",
        "ph_qual character(3)",
        "rd_flg character(3)",
        "bl_flg character(3)",
        "cc_flg character(3)",
        "ndet character(6)",
        "prox real",
        "pxpa smallint",
        "pxcntr integer",
        "gal_contam smallint",
        "mp_flg smallint",
        "pts_key integer",
        "hemis character(1)",
        "date date",
        "scan smallint",
        "glon real",
        "glat real",
        "x_scan real",
        "jdate double precision",
        "j_psfchi real",
        "h_psfchi real",
        "k_psfchi real",
        "j_m_stdap real",
        "j_msig_stdap real",
        "h_m_stdap real",
        "h_msig_stdap real",
        "k_m_stdap real",
        "k_msig_stdap real",
        "dist_edge_ns integer",
        "dist_edge_ew integer",
        "dist_edge_flg character(2)",
        "dup_src smallint",
        "use_src smallint",
        "a character(1)",
        "dist_opt real",
        "phi_opt smallint",
        "b_m_opt real",
        "vr_m_opt real",
        "nopt_mchs smallint",
        "ext_key integer",
        "scan_key integer",
        "coadd_key integer",
        "coadd smallint"
    ]
    
    def __init__(self, dbctxt: DBctxt, path: str, chunk_rows: int = 100000):
        super().__init__(dbctxt, path)
        self.chunk_rows = chunk_rows
        self.relational_schema = TwoMASSConfig.relational_schema # TODO: redundant
        self.ra  = "ra"
        self.dec = "decl"

    def _tabularize(self, path):
        with gzip.open(path, 'rb') as f:
            file_content = f.read()
            file_content = str(file_content)[2:-1].replace("\\\\N", "NULL")
            file_content = file_content.split("\\n")[:-1] # remove trailing empty line
            
            for rownum, record in enumerate(file_content):
                file_content[rownum] = file_content[rownum].split("|")

            self.data = file_content
                
    def _clean_data(self):
        pass

    def _relational_schema(self):
        pass

    def _create_table(self):
        # #####################################################################
        # TODO: drop table & create new, edit in place?
        # #####################################################################
        logger.info(f"Creating new table in database {self.dbctxt.POSTGRES_DB} on host {self.dbctxt.POSTGRES_HOST}.")

        SQL_statement = ""
        
        SQL_statement += f"DROP TABLE IF EXISTS {self.dbctxt.sql_table};\n"
        
        comma_nl = ",\n"
        SQL_statement += f"CREATE TABLE {self.dbctxt.sql_table} (\n{comma_nl.join(self.relational_schema)});"
        with psycopg2.connect(host=self.dbctxt.POSTGRES_HOST, port=self.dbctxt.POSTGRES_PORT, dbname=self.dbctxt.POSTGRES_DB, user=self.dbctxt.POSTGRES_USER, password=self.dbctxt.POSTGRES_PASSWORD) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(SQL_statement)
                    conn.commit()
                except psycopg2.errors.DuplicateTable:
                    raise f"Table {self.dbctxt.sql_table} already exists. Attemtping to continue with existing schema..."
                except Exception as e: raise

        logger.info("done creating table.")

    def _data2SQLValues(self):
        for rownum, record in enumerate(self.data):
            for elementnum, element in enumerate(record):
                if "character" in self.relational_schema[elementnum]:
                    element = element.replace('"', '"""') # SQL escapes a quote with another quote
                    element = element.replace("'", "''")
                    record[elementnum] = f"\'{element}\'"
                
                if "date" in self.relational_schema[elementnum]:
                    record[elementnum] = f"\'{element}\'"

            comma_space = ", "
            record = f"({comma_space.join(record)})"

            self.data[rownum] = record

    def insert_all(self):
        SQL_statement = ""
        comma_nl = ",\n"

        filenames = sorted(os.listdir(self.path))
        
        self._create_table()

        for index, filename in enumerate(filenames):
            
            if filename[:4] == "psc_" and filename[-3:] == ".gz":
                logger.debug(f"File index: {index}\t filename: {filename}")
                
                self._tabularize(f"{self.path}/{filename}")
                self._clean_data()
                self._data2SQLValues()

                for start_index in range(0, len(self.data), self.chunk_rows):
                    logger.debug(f"{self.path}/{filename} rows {start_index}:{min(len(self.data), start_index + self.chunk_rows)}")
                    SQL_statement = f"INSERT INTO {self.dbctxt.sql_table} VALUES \n {comma_nl.join(self.data[start_index:start_index + self.chunk_rows])};"
                    execute_statement(self.dbctxt, SQL_statement)
                
                logger.debug(f"file {self.path}/{filename} complete.")    
        
        q3c_index_table(self.dbctxt, self.ra, self.dec)


class ZTFVarStarConfig(CatalogConfig):
    relational_schema = [
        "ID         character(22)",
        "SourceID   int8",
        "RAdeg      float8",
        "DEdeg      float8",
        "Per        float8",
        "R21        float8",
        "phi21      float8",
        "T0         float8",
        "gmag       float8",
        "rmag       float8",
        "Per_g      float8",
        "Per_r      float8",
        "Num_g      int4",
        "Num_r      int4",
        "R21_g      float8",
        "R21_r      float8",
        "phi21_g    float8",
        "phi21_r    float8",
        "R2_g       float8",
        "R2_r       float8",
        "Amp_g      float8",
        "Amp_r      float8",
        "logFAP_g   float8",
        "logFAP_r   float8",
        "Type       character(5)",
        "Dmin_g     float8",
        "Dmin_r     float8"
    ]

    first_data_row = 36
    
    def __init__(self, dbctxt: DBctxt, path: str, chunk_rows: int = 100000):
        super().__init__(dbctxt, path)
        self.chunk_rows = chunk_rows
        self.relational_schema = ZTFVarStarConfig.relational_schema # TODO: redundant
        self.ra  = "RAdeg"
        self.dec = "DEdeg"

    def _tabularize(self, path):
        logger.info(f"Reading in data from single catalog file {self.path}...")
        with open(path, 'r') as f:
            file_content = f.read()
            
            file_content = file_content.split("\n")

            file_content = file_content[ZTFVarStarConfig.first_data_row:-1]

            for index, row in enumerate(file_content):
                file_content[index] = " ".join(row.split())

            
            for rownum, record in enumerate(file_content):
                file_content[rownum] = record.split(" ")

            self.data = file_content
        logger.info(f"done creating internal data table.")
                
    def _clean_data(self):
        pass

    def _relational_schema(self):
        pass

    def _create_table(self):
        logger.info(f"Creating new table in database {self.dbctxt.POSTGRES_DB} on host {self.dbctxt.POSTGRES_HOST}.")
        
        SQL_statement = ""
        comma_nl = ",\n"
        
        SQL_statement += f"DROP TABLE IF EXISTS {self.dbctxt.sql_table};\n"
        
        SQL_statement += f"CREATE TABLE {self.dbctxt.sql_table} (\n{comma_nl.join(self.relational_schema)});"
        with psycopg2.connect(host=self.dbctxt.POSTGRES_HOST, port=self.dbctxt.POSTGRES_PORT, dbname=self.dbctxt.POSTGRES_DB, user=self.dbctxt.POSTGRES_USER, password=self.dbctxt.POSTGRES_PASSWORD) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(SQL_statement)
                    conn.commit()
                except psycopg2.errors.DuplicateTable:
                    raise f"Table {self.dbctxt.sql_table} already exists. Attemtping to continue with existing schema..."
                except Exception as e: raise
        
        logger.info("done creating table.")

    def _data2SQLValues(self):
        # comma_space = ", "
        # for rownum, record in enumerate(self.data):
        #     record = f"({comma_space.join(record)})"
        #     self.data[rownum] = record
        for rownum, record in enumerate(self.data):
            for elementnum, element in enumerate(record):
                if "character" in self.relational_schema[elementnum]:
                    element = element.replace('"', '"""') # SQL escapes a quote with another quote
                    element = element.replace("'", "''")
                    record[elementnum] = f"\'{element}\'"

            comma_space = ", "
            record = f"({comma_space.join(record)})"

            self.data[rownum] = record

    def insert_all(self):
        SQL_statement = ""
        comma_nl = ",\n"

        self._tabularize(self.path)

        self._data2SQLValues()

        self._create_table()

        # insert values
        SQL_statement = f"INSERT INTO {self.dbctxt.sql_table} VALUES \n {comma_nl.join(self.data)};"
        
        execute_statement(self.dbctxt, SQL_statement)

        q3c_index_table(self.dbctxt, self.ra, self.dec)

