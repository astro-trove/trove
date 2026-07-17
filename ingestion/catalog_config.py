from enum import Enum
import gzip
import os
import logging

from astropy.table import Table, join
import fitsio
import numpy as np
import psycopg2

from ingest_extras import *
import numpy2PGSQL

logger = logging.getLogger(__name__)

comma_nl = ",\n" # because `",\n".join(...)` doesn't work

Catalogs = Enum('Catalogs', 
[
    ('ALLWISE',      'allWISE'),
    ('COSMICFLOWS4', 'COSMIC_FLOWS_4'),
    ('DESIDR1',      'DESI_DR1'),
    ('FERMILPSC',    'Fermi_LPSC'),
    ('FERMI3FHL',    'Fermi_3FHL'),
    ('HEASARCMASTERXRAY', 'HEASARC_Master_XRay'),
    ('HEASARCMASTERRADIO', 'HEASARC_Master_Radio'),
    ('HECATE2',      'HECATE2'),
    ('LSDR9',        'LS_DR9'),
    ('NEDLVS',       'NEDLVS'),
    ('TWOMASS',      'Two_MASS'),
    ('ZTFVARSTAR',   'ZTF_varstar')
])

class CatalogConfig():
    """ Abstract class """
    def __init__(self, dbctxt: DBctxt, path: str, chunk_rows: int = 100000):
        self.dbctxt: DBctxt               = dbctxt
        self.path: str                    = path
        self.relational_schema: list[str] = None
        self.data                         = None
        self.chunk_rows: int              = int(chunk_rows)
        self.ra: str                      = "ra"
        self.dec: str                     = "dec"

    # ##########################################################################
    # "private"
    # ##########################################################################
    def _tabularize(self, path: str):
        """ ex: return Table.read(path) """
        raise NotImplementedError()
    
    def _clean_data(self):
        raise NotImplementedError()
    
    def _relational_schema(self) -> str:
        """ define relational_schema if necessary, assign to self.relational_schema, and return self.relational_schema """
        raise NotImplementedError()

    def _create_table(self):
        """ Generate SQL statement to create table """
        logger.info(f"Creating new table in database {self.dbctxt.POSTGRES_DB} on host {self.dbctxt.POSTGRES_HOST}.")

        SQL_statement = ""
        
        SQL_statement += f"CREATE TABLE IF NOT EXISTS {self.dbctxt.sql_table} ({comma_nl.join(self.relational_schema)}); TRUNCATE {self.dbctxt.sql_table} RESTART IDENTITY;"
        
        with psycopg2.connect(host=self.dbctxt.POSTGRES_HOST, port=self.dbctxt.POSTGRES_PORT, dbname=self.dbctxt.POSTGRES_DB, user=self.dbctxt.POSTGRES_USER, password=self.dbctxt.POSTGRES_PASSWORD) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(SQL_statement)
                    conn.commit()
                except psycopg2.errors.DuplicateTable:
                    raise f"Table {self.dbctxt.sql_table} already exists. Attemtping to continue with existing schema..."
                except Exception as e: 
                    raise e

        logger.info("done creating table.")

    def _data2SQL(self, rows: range) -> list[str]:
        all_values = []

        for row_index in rows:
            values = f"({', '.join(self.data[row_index])})"
            all_values.append(values)

        return all_values

    # ##########################################################################
    # "public"
    # ##########################################################################
    def insert_all(self):
        self._tabularize(self.path)
        self._relational_schema()
        self._clean_data()
        self._create_table()

        for i in range(0, len(self.data), self.chunk_rows):

            SQL_statement = ""
            rows = range(i, min(len(self.data), i + self.chunk_rows))

            logger.info(f"Inserting values for rows {rows.start}-{rows.stop} of {len(self.data)}.")

            SQL_statement = f"INSERT INTO {self.dbctxt.sql_table} VALUES {comma_nl.join(self._data2SQL(rows))};"

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


class BasicAstropyConfig(CatalogConfig):
    """ Abstract class """
    def __init__(self, dbctxt: DBctxt, path: str, chunk_rows: int = 100000):
        super().__init__(dbctxt, path)

    def _tabularize(self, path: str, format: str = None):
        self.data = Table.read(path, format)

    def _relational_schema(self):
            self.relational_schema = []

            # ap_types = set() # used in devel to get src data types (in the fits file, not implementation)
            for col_index in range(len(self.data.colnames)):
                colname = self.data.colnames[col_index]
                np_dtype = str(type(self.data[colname][0]))
                ap_dtype = self.data[colname].dtype.str
                pg_dtype = numpy2PGSQL.convert(ap_dtype)

                if (len(self.data[colname].shape) > 1):
                    pg_dtype += f"[{self.data[colname].shape[1]}]"
                
                # ap_types.add(ap_dtype) # used in devel to get src data types (in the fits file, not implementation)
                self.relational_schema.append(f"\n{colname.ljust(20)}\t{pg_dtype}")
            
            return self.relational_schema

    def _clean_data(self):
        for i, row in enumerate(self.data):
            for j, col in enumerate(row):
                value = str(self.data[i][j])
                dtype = numpy2PGSQL.convert(self.data[self.data.colnames[j]].dtype.str)

                if (type(self.data[i][j]) is np.ma.core.MaskedConstant):
                    value = "NULL"

                if (type(self.data[i][j]) is np.ndarray):
                    value = "'{" + f'{", ".join(value.replace("[", "").replace("]", "").split())}' + "}'"

                if dtype == "text":
                    value = value.replace(" ", "")
                    
                    if (value == "" or value == " " or value == "-" or value == "--" or value == "---" or value == " --"):
                        value = "NULL"
                    
                    value = value.replace('"', '"""') # SQL escapes a quote with another quote
                    value = value.replace("'", "''")

                self.data[i][j] = value


class AllWISEConfig(CatalogConfig):
    relational_schema = [
    "designation text",
    "ra double precision",
    "dec double precision",
    "sigra double precision",
    "sigdec double precision",
    "sigradec double precision",
    "glon double precision",
    "glat double precision",
    "elon double precision",
    "elat double precision",
    "wx double precision",
    "wy double precision",
    "cntr bigserial",
    "source_id text",
    "coadd_id text",
    "src integer",
    "w1mpro double precision",
    "w1sigmpro double precision",
    "w1snr double precision",
    "w1rchi2 real",
    "w2mpro double precision",
    "w2sigmpro double precision",
    "w2snr double precision",
    "w2rchi2 real",
    "w3mpro double precision",
    "w3sigmpro double precision",
    "w3snr double precision",
    "w3rchi2 real",
    "w4mpro double precision",
    "w4sigmpro double precision",
    "w4snr double precision",
    "w4rchi2 real",
    "rchi2 real",
    "nb integer",
    "na integer",
    "w1sat double precision",
    "w2sat double precision",
    "w3sat double precision",
    "w4sat double precision",
    "satnum text",
    "ra_pm double precision",
    "dec_pm double precision",
    "sigra_pm double precision",
    "sigdec_pm double precision",
    "sigradec_pm double precision",
    "pmra integer",
    "sigpmra integer",
    "pmdec integer",
    "sigpmdec integer",
    "w1rchi2_pm real",
    "w2rchi2_pm real",
    "w3rchi2_pm real",
    "w4rchi2_pm real",
    "rchi2_pm real",
    "pmcode text",
    "cc_flags text",
    "rel text",
    "ext_flg integer",
    "var_flg text",
    "ph_qual text",
    "det_bit integer",
    "moon_lev text",
    "w1nm integer",
    "w1m integer",
    "w2nm integer",
    "w2m integer",
    "w3nm integer",
    "w3m integer",
    "w4nm integer",
    "w4m integer",
    "w1cov double precision",
    "w2cov double precision",
    "w3cov double precision",
    "w4cov double precision",
    "w1cc_map integer",
    "w1cc_map_str text",
    "w2cc_map integer",
    "w2cc_map_str text",
    "w3cc_map integer",
    "w3cc_map_str text",
    "w4cc_map integer",
    "w4cc_map_str text",
    "best_use_cntr int8",
    "ngrp smallint",
    "w1flux real",
    "w1sigflux real",
    "w1sky double precision",
    "w1sigsk double precision",
    "w1conf double precision",
    "w2flux real",
    "w2sigflux real",
    "w2sky double precision",
    "w2sigsk double precision",
    "w2conf double precision",
    "w3flux real",
    "w3sigflux real",
    "w3sky double precision",
    "w3sigsk double precision",
    "w3conf double precision",
    "w4flux real",
    "w4sigflux real",
    "w4sky double precision",
    "w4sigsk double precision",
    "w4conf double precision",
    "w1mag double precision",
    "w1sigm double precision",
    "w1flg integer",
    "w1mcor double precision",
    "w2mag double precision",
    "w2sigm double precision",
    "w2flg integer",
    "w2mcor double precision",
    "w3mag double precision",
    "w3sigm double precision",
    "w3flg integer",
    "w3mcor double precision",
    "w4mag double precision",
    "w4sigm double precision",
    "w4flg integer",
    "w4mcor double precision",
    "w1mag_1 double precision",
    "w1sigm_1 double precision",
    "w1flg_1 integer",
    "w2mag_1 double precision",
    "w2sigm_1 double precision",
    "w2flg_1 integer",
    "w3mag_1 double precision",
    "w3sigm_1 double precision",
    "w3flg_1 integer",
    "w4mag_1 double precision",
    "w4sigm_1 double precision",
    "w4flg_1 integer",
    "w1mag_2 double precision",
    "w1sigm_2 double precision",
    "w1flg_2 integer",
    "w2mag_2 double precision",
    "w2sigm_2 double precision",
    "w2flg_2 integer",
    "w3mag_2 double precision",
    "w3sigm_2 double precision",
    "w3flg_2 integer",
    "w4mag_2 double precision",
    "w4sigm_2 double precision",
    "w4flg_2 integer",
    "w1mag_3 double precision",
    "w1sigm_3 double precision",
    "w1flg_3 integer",
    "w2mag_3 double precision",
    "w2sigm_3 double precision",
    "w2flg_3 integer",
    "w3mag_3 double precision",
    "w3sigm_3 double precision",
    "w3flg_3 integer",
    "w4mag_3 double precision",
    "w4sigm_3 double precision",
    "w4flg_3 integer",
    "w1mag_4 double precision",
    "w1sigm_4 double precision",
    "w1flg_4 integer",
    "w2mag_4 double precision",
    "w2sigm_4 double precision",
    "w2flg_4 integer",
    "w3mag_4 double precision",
    "w3sigm_4 double precision",
    "w3flg_4 integer",
    "w4mag_4 double precision",
    "w4sigm_4 double precision",
    "w4flg_4 integer",
    "w1mag_5 double precision",
    "w1sigm_5 double precision",
    "w1flg_5 integer",
    "w2mag_5 double precision",
    "w2sigm_5 double precision",
    "w2flg_5 integer",
    "w3mag_5 double precision",
    "w3sigm_5 double precision",
    "w3flg_5 integer",
    "w4mag_5 double precision",
    "w4sigm_5 double precision",
    "w4flg_5 integer",
    "w1mag_6 double precision",
    "w1sigm_6 double precision",
    "w1flg_6 integer",
    "w2mag_6 double precision",
    "w2sigm_6 double precision",
    "w2flg_6 integer",
    "w3mag_6 double precision",
    "w3sigm_6 double precision",
    "w3flg_6 integer",
    "w4mag_6 double precision",
    "w4sigm_6 double precision",
    "w4flg_6 integer",
    "w1mag_7 double precision",
    "w1sigm_7 double precision",
    "w1flg_7 integer",
    "w2mag_7 double precision",
    "w2sigm_7 double precision",
    "w2flg_7 integer",
    "w3mag_7 double precision",
    "w3sigm_7 double precision",
    "w3flg_7 integer",
    "w4mag_7 double precision",
    "w4sigm_7 double precision",
    "w4flg_7 integer",
    "w1mag_8 double precision",
    "w1sigm_8 double precision",
    "w1flg_8 integer",
    "w2mag_8 double precision",
    "w2sigm_8 double precision",
    "w2flg_8 integer",
    "w3mag_8 double precision",
    "w3sigm_8 double precision",
    "w3flg_8 integer",
    "w4mag_8 double precision",
    "w4sigm_8 double precision",
    "w4flg_8 integer",
    "w1magp double precision",
    "w1sigp1 double precision",
    "w1sigp2 double precision",
    "w1k double precision",
    "w1ndf integer",
    "w1mlq double precision",
    "w1mjdmin double precision",
    "w1mjdmax double precision",
    "w1mjdmean double precision",
    "w2magp double precision",
    "w2sigp1 double precision",
    "w2sigp2 double precision",
    "w2k double precision",
    "w2ndf integer",
    "w2mlq double precision",
    "w2mjdmin double precision",
    "w2mjdmax double precision",
    "w2mjdmean double precision",
    "w3magp double precision",
    "w3sigp1 double precision",
    "w3sigp2 double precision",
    "w3k double precision",
    "w3ndf integer",
    "w3mlq double precision",
    "w3mjdmin double precision",
    "w3mjdmax double precision",
    "w3mjdmean double precision",
    "w4magp double precision",
    "w4sigp1 double precision",
    "w4sigp2 double precision",
    "w4k double precision",
    "w4ndf integer",
    "w4mlq double precision",
    "w4mjdmin double precision",
    "w4mjdmax double precision",
    "w4mjdmean double precision",
    "rho12 integer",
    "rho23 integer",
    "rho34 integer",
    "q12 integer",
    "q23 integer",
    "q34 integer",
    "xscprox double precision",
    "w1rsemi double precision",
    "w1ba double precision",
    "w1pa double precision",
    "w1gmag double precision",
    "w1gerr double precision",
    "w1gflg integer",
    "w2rsemi double precision",
    "w2ba double precision",
    "w2pa double precision",
    "w2gmag double precision",
    "w2gerr double precision",
    "w2gflg integer",
    "w3rsemi double precision",
    "w3ba double precision",
    "w3pa double precision",
    "w3gmag double precision",
    "w3gerr double precision",
    "w3gflg integer",
    "w4rsemi double precision",
    "w4ba double precision",
    "w4pa double precision",
    "w4gmag double precision",
    "w4gerr double precision",
    "w4gflg integer",
    "tmass_key integer",
    "r_2mass double precision",
    "pa_2mass double precision",
    "n_2mass integer",
    "j_m_2mass double precision",
    "j_msig_2mass double precision",
    "h_m_2mass double precision",
    "h_msig_2mass double precision",
    "k_m_2mass double precision",
    "k_msig_2mass double precision",
    "x double precision",
    "y double precision",
    "z double precision",
    "spt_ind integer",
    "htm20 int8"
    ]
    
    def __init__(self, dbctxt, path, chunk_rows = 100000):
        super().__init__(dbctxt, path, chunk_rows=100000)

    def _tabularize(self, path: str):
        with open(path, 'r') as file:
            self.data = []
            
            new_table = file.readlines()

            for i, row in enumerate(new_table):
                self.data.append(row.split("|")[:-1])

            del new_table
            
    
    def _clean_data(self):
        for i, row in enumerate(self.data):
            for j, col in enumerate(row):
                if col == '':
                    col = 'NULL'
                if col == None:
                    col = 'NULL'
                if ' text' in self.relational_schema[j]:
                    col = f"'{col}'"
                self.data[i][j] = col
    
    def _relational_schema(self) -> str:
        self.relational_schema = AllWISEConfig.relational_schema
        return self.relational_schema

    def insert_all(self):
        self._relational_schema()
        self._create_table()

        for i in range(1, 49):
            srcname = f"{os.path.dirname(self.path)}/wise-allwise-cat-part{i:02}"
            self._tabularize(srcname)
            self._clean_data()

            for j in range(0, len(self.data), self.chunk_rows):
                SQL_statement = ""
                rows = range(j, min(len(self.data), j + self.chunk_rows))
            
                logger.info(f"Inserting values for rows {rows.start}-{rows.stop} of {len(self.data)}. File {i} of 48.")
            
                SQL_statement = f"INSERT INTO {self.dbctxt.sql_table} VALUES {comma_nl.join(self._data2SQL(rows))};"
            
                # connect to db & insert
                with psycopg2.connect(host=self.dbctxt.POSTGRES_HOST, user=self.dbctxt.POSTGRES_USER, port=self.dbctxt.POSTGRES_PORT, password=self.dbctxt.POSTGRES_PASSWORD, dbname=self.dbctxt.POSTGRES_DB) as conn:
                    with conn.cursor() as cur:
                        try:
                            cur.execute(SQL_statement)
                            conn.commit()
                        except Exception as e:
                            raise e
                logger.info("chunk complete.")
            
        q3c_index_table(self.dbctxt, self.ra, self.dec)
        logger.info("table ingested.")


class CosmicFlows4Config(CatalogConfig):
    relational_schema = [
            "recno bigint",
            "Name text",
            "RAJ2000 double precision",
            "DEJ2000 double precision",
            "Dist double precision",
            "z double precision",
            "DistMin double precision",
            "DistMax double precision",
            "e_Dist double precision",
            "DistInput double precision",
            "e_DistInput double precision",
            "DistTmean double precision",
            "r_DistInput double precision",
            "Ndist double precision",
            "R1 double precision",
            "R2 double precision",
            "PA double precision",
            "r_R1 double precision",
            "IdCat double precision",
            "gaia_uppercase_g_mag double precision",
            "BPmag double precision",
            "PM double precision",
            "angDist double precision",
            "rmagpsf double precision",
            "gmag double precision",
            "rmag double precision",
            "imag double precision",
            "zmag double precision",
            "W1mag double precision",
            "W2mag double precision",
            "dK double precision",
            "r_gmag double precision",
            "r_W1mag double precision",
            "ebv double precision",
            "logM double precision",
            "fRel double precision",
            "fracNearby double precision"
        ]
    
    def __init__(self, dbctxt: DBctxt, path: str, chunk_rows: int = 1000):
        super().__init__(dbctxt, path, chunk_rows)
        self.ra  = "RAJ2000"
        self.dec = "DEJ2000"

    def _tabularize(self, path: str):
        with open(path, "r") as f:
            content = f.read()
            content = content.split("\n")
            for i, row in enumerate(content):
                row = row.split(",")
                content[i] = row

        self.data = content
        self.data = self.data[1:-1]

    def _clean_data(self):
        for i, row in enumerate(self.data):
            self.data[i][1] = f"\'{row[1]}\'"

            for j, col in enumerate(row):
                if (col == ""):
                    self.data[i][j] = "NULL"

                value = str(self.data[i][j])
                dtype = numpy2PGSQL.convert(self.data[self.data.colnames[j]].dtype.str)

                if (type(self.data[i][j]) is np.ma.core.MaskedConstant):
                    value = "NULL"

                if (type(self.data[i][j]) is np.ndarray):
                    value = "'{" + f'{", ".join(value.replace("[", "").replace("]", "").split())}' + "}'"

                if dtype == "text":
                    value = value.replace(" ", "")

                    if (value == "" or value == " " or value == "-" or value == "--" or value == "---" or value == " --"):
                        value = "NULL"

                    value = value.replace('"', '"""') # SQL escapes a quote with another quote
                    value = value.replace("'", "''")

                self.data[i][j] = value

    def _relational_schema(self):
        self.relational_schema = CosmicFlows4Config.relational_schema
        return self._relational_schema


class HeasarcMasterRadioConfig(CatalogConfig):
    bytes_ranges = [
        range(1, 40),
        range(41, 55),
        range(56, 66),
        range(67, 76),
        range(77, 86),
        range(87, 97),
        range(98, 110),
        range(111, 119),
        range(120, 128),
        range(129, 145),
        range(147, 161),
        range(162, 178)
    ]
    
    relational_schema = [
        "name               text",
        "database_table     text",
        "ra                 float8",
        "dec                float8",
        "flux_6_cm          float8",
        "flux_20_cm         float8",
        "flux_other         float8",
        "lii                float8",
        "bii                float8",
        "flux_20_cm_error   float8",
        "flux_6_cm_error    float8",
        "flux_other_error   float8"
    ]

    def __init__(self, dbctxt: DBctxt, path: str, chunk_rows: int = 100000):
        super().__init__(dbctxt, path, chunk_rows)
        self.bytes_ranges = HeasarcMasterRadioConfig.bytes_ranges
        self.relational_schema = HeasarcMasterRadioConfig.relational_schema

    def _tabularize(self, path):
        with open(path, "r") as file:
            self.data = file.read()
            self.data = self.data.split("\n")
            self.data = self.data[3:-1]
        
        for i, row in enumerate(self.data):
            new_val = []

            for r in HeasarcMasterRadioConfig.bytes_ranges:
                new_val.append(row[r.start:r.stop])

            self.data[i] = new_val

    def _relational_schema(self):
        self.relational_schema = HeasarcMasterRadioConfig.relational_schema
        return self.relational_schema

    def _clean_data(self):
        for i, row in enumerate(self.data):
            new_row = []
            tmp = None

            tmp = f"'{row[0].strip()}'"
            if tmp == '':
                tmp = 'NULL'
            new_row.append(str(tmp))

            tmp = f"'{row[1].strip()}'"
            if tmp == '':
                tmp = 'NULL'
            new_row.append(str(tmp))
            
            tmp = row[2].split()
            if tmp == []:
                tmp = 'NULL'
                new_row.append(tmp)
            elif (len(tmp) < 3):        # table contains "<decimal number> !!" where not sexagesimal
                tmp = tmp[0]
                new_row.append(str(tmp))
            else:
                new_row.append(str(sexagesimal2decimal(int(tmp[0]), int(tmp[1]), float(tmp[2]))))

            tmp = row[3].split()
            if tmp == []:
                tmp = 'NULL'
                new_row.append(tmp)
            elif (len(tmp) < 3):        # table contains "<decimal number> !!" where not sexagesimal
                tmp = tmp[0]
                new_row.append(str(tmp))
            else:
                new_row.append(str(sexagesimal2decimal(int(tmp[0]), int(tmp[1]), float(tmp[2]))))

            tmp = row[4].strip()
            if (tmp == ''):
                tmp = 'NULL'
            new_row.append(tmp)

            tmp = row[5].strip()
            if (tmp == ''):
                tmp = 'NULL'
            new_row.append(tmp)

            tmp = row[6].strip()
            if (tmp == ''):
                tmp = 'NULL'
            new_row.append(tmp)

            tmp = row[7].strip()
            if (tmp == ''):
                tmp = 'NULL'
            new_row.append(tmp)

            tmp = row[8].strip()
            if (tmp == ''):
                tmp = 'NULL'
            new_row.append(tmp)

            tmp = row[9].strip()
            if (tmp == ''):
                tmp = 'NULL'
            new_row.append(tmp)

            tmp = row[10].strip()
            if (tmp == ''):
                tmp = 'NULL'
            new_row.append(tmp)

            tmp = row[11].strip()
            if (tmp == ''):
                tmp = 'NULL'
            new_row.append(tmp)

            self.data[i] = new_row


class HeasarcMasterXRayConfig(CatalogConfig):
    bytes_ranges = [
        range(1, 33),
        range(34, 50),
        range(51, 65),
        range(66, 76),
        range(77, 93),
        range(94, 104),
        range(105, 119),
        range(120, 131),
        range(132, 141),
        range(142, 151),
        range(152, 164),
        range(165, 173),
        range(174, 225)
    ]
    
    relational_schema = [
        "name               text", 
        "ra                 float8",
        "dec                float8",
        "count_rate         float8",
        "count_rate_error   float8",
        "flux               float8",
        "database_table     text",
        "observatory        text",
        "lii                float8",
        "bii                float8",
        "error_radius       float4",
        "exposure           integer",
        "class              text"
    ]

    def __init__(self, dbctxt: DBctxt, path: str, chunk_rows: int = 100000):
        super().__init__(dbctxt, path, chunk_rows)
        self.bytes_ranges = HeasarcMasterXRayConfig.bytes_ranges
        self.relational_schema = HeasarcMasterXRayConfig.relational_schema

    def _tabularize(self, path):
        with open(path, "r") as file:
            self.data = file.read()
            self.data = self.data.split("\n")
            self.data = self.data[3:-1]
        
        for i, row in enumerate(self.data):
            new_val = []

            for r in HeasarcMasterXRayConfig.bytes_ranges:
                new_val.append(row[r.start:r.stop])

            self.data[i] = new_val

    def _relational_schema(self):
        self.relational_schema = HeasarcMasterXRayConfig.relational_schema
        return self.relational_schema

    def _clean_data(self):
        for i, row in enumerate(self.data):
            new_row = []
            tmp = None

            tmp = f"'{row[0].strip()}'"
            if tmp == '':
                tmp = 'NULL'
            new_row.append(str(tmp))
            
            tmp = row[1].split()
            if tmp == []:
                tmp = 'NULL'
                new_row.append(tmp)
            elif (len(tmp) < 3):        # table contains "<decimal number> !!" where not sexagesimal
                tmp = tmp[0]
                new_row.append(str(tmp))
            else:
                new_row.append(str(sexagesimal2decimal(int(tmp[0]), int(tmp[1]), float(tmp[2]))))

            tmp = row[2].split()
            if tmp == []:
                tmp = 'NULL'
                new_row.append(tmp)
            elif (len(tmp) < 3):        # table contains "<decimal number> !!" where not sexagesimal
                tmp = tmp[0]
                new_row.append(str(tmp))
            else:
                new_row.append(str(sexagesimal2decimal(int(tmp[0]), int(tmp[1]), float(tmp[2]))))

            tmp = row[3].strip()
            if (tmp == ''):
                tmp = 'NULL'
            new_row.append(tmp)

            tmp = row[4].strip()
            if (tmp == ''):
                tmp = 'NULL'
            new_row.append(tmp)

            tmp = row[5].strip()
            if (tmp == ''):
                tmp = 'NULL'
            new_row.append(tmp)

            tmp = f"'{row[6].strip()}'"
            if tmp == '':
                tmp = 'NULL'
            new_row.append(tmp)

            tmp = f"'{row[7].strip()}'"
            if tmp == '':
                tmp = 'NULL'
            new_row.append(str(tmp))

            tmp = row[8].strip()
            if (tmp == ''):
                tmp = 'NULL'
            new_row.append(tmp)

            tmp = row[9].strip()
            if (tmp == ''):
                tmp = 'NULL'
            new_row.append(tmp)

            tmp = row[10].strip()
            if (tmp == ''):
                tmp = 'NULL'
            new_row.append(tmp)

            tmp = f"{row[11].strip()}"
            if tmp == '':
                tmp = 'NULL'
            new_row.append(tmp)

            tmp = f"'{row[12].strip()}'"
            if tmp == '':
                tmp = 'NULL'
            new_row.append(tmp)

            self.data[i] = new_row
                

class HecateV2Config(CatalogConfig):
    bytes_ranges = [
        range(0,7),
        range(8,38),
        range(39,58),
        range(59,78),
        range(79,98),
        range(99,117),
        range(118,128),
        range(129,139),
        range(140,142),
        range(143,153),
        range(154,164),
        range(165,175),
        range(176,178),
        range(179,200),
        range(201,211),
        range(212,215),
        range(216,225),
        range(226,238),
        range(239,252),
        range(253,265),
        range(266,279),
        range(280,292),
        range(293,304),
        range(305,306),
        range(307,314),
        range(315,323),
        range(324,330),
        range(331,337),
        range(338,344),
        range(345,351),
        range(352,357),
        range(358,363),
        range(364,369),
        range(370,375),
        range(376,388),
        range(389,401),
        range(402,414),
        range(415,427),
        range(428,429),
        range(430,431),
        range(432,433),
        range(434,435),
        range(436,447),
        range(448,459),
        range(460,471),
        range(472,482),
        range(483,488),
        range(489,494),
        range(495,500),
        range(501,506),
        range(507,520),
        range(521,529),
        range(530,539),
        range(540,549),
        range(550,560),
        range(561,571),
        range(572,582),
        range(583,594),
        range(595,607),
        range(608,620),
        range(621,627),
        range(628,634),
        range(635,641),
        range(642,647),
        range(648,656),
        range(657,662),
        range(663,664),
        range(665,677),
        range(678,690),
        range(691,703),
        range(704,716),
        range(717,729),
        range(730,741),
        range(742,754),
        range(755,767),
        range(768,780),
        range(781,793),
        range(794,806),
        range(807,819),
        range(820,826),
        range(827,833),
        range(834,846),
        range(847,859),
        range(860,872),
        range(873,885),
        range(886,898),
        range(899,911),
        range(912,924),
        range(925,937),
        range(938,950),
        range(951,963),
        range(964,976),
        range(977,989),
        range(990,1002),
        range(1003,1015),
        range(1016,1024),
        range(1025,1031),
        range(1032,1038),
        range(1039,1045),
        range(1046,1047),
        range(1048,1053),
        range(1054,1060),
        range(1061,1067),
        range(1068,1074),
        range(1075,1081),
        range(1082,1088),
        range(1089,1101),
        range(1102,1114),
        range(1115,1125),
        range(1126,1135),
        range(1136,1146),
        range(1147,1152),
        range(1153,1156),
        range(1157,1166),
        range(1167,1176),
        range(1177,1182),
        range(1183,1186),
        range(1187,1212),
        range(1213,1221),
        range(1222,1230),
        range(1231,1240),
        range(1241,1250),
        range(1251,1258),
        range(1259,1286),
        range(1287,1289),
        range(1290,1293),
        range(1294,1348)
    ]

    relational_schema = [
        "PGC            int8",
        "OBJNAME        text",
        "ALLWISE        text",
        "OBJID          text",
        "SPECOBJID      text",
        "PS2            text",
        "RAdeg          float8",
        "DEdeg          float8",
        "Fastrom        int8",
        "R1             float8",
        "R2             float8",
        "PA             float8",
        "Rflag          text",
        "ROrigin        text",
        "T              float8",
        "e_T            float8",
        "Incl           float8",
        "HRV            float8",
        "e_HRV          float8",
        "Vvir           float8",
        "e_Vvir         float8",
        "Dist           float8",
        "e_Dist         float8",
        "f_Dist         int8",
        "AG             float8",
        "AI             float8",
        "Umagtot        float8",
        "Bmagtot        float8",
        "Vmagtot        float8",
        "Imagtot        float8",
        "e_Umagtot      float8",
        "e_Bmagtot      float8",
        "e_Vmagtot      float8",
        "e_Imagtot      float8",
        "FS12           float8",
        "FS25           float8",
        "FS60           float8",
        "FS100          float8",
        "q_FS12         int8",
        "q_FS25         int8",
        "q_FS60         int8",
        "q_FS100        int8",
        "W1mag          float8",
        "W2mag          float8",
        "W3mag          float8",
        "W4mag          float8",
        "e_W1mag        float8",
        "e_W2mag        float8",
        "e_W3mag        float8",
        "e_W4mag        float8",
        "ALLWISEap      text",
        "q_ALLWISE      text",
        "WF1mag         float8",
        "WF2mag         float8",
        "WF3mag         float8",
        "WF4mag         float8",
        "e_WF1mag       float8",
        "e_WF2mag       float8",
        "e_WF3mag       float8",
        "e_WF4mag       float8",
        "Jmag           float8",
        "Hmag           float8",
        "Kmag           float8",
        "e_Jmag         float8",
        "e_Hmag         float8",
        "e_Kmag         float8",
        "R2MASS         int8",
        "umag           float8",
        "gmag           float8",
        "rmag           float8",
        "imag           float8",
        "zmag           float8",
        "ymag           float8",
        "e_umag         float8",
        "e_gmag         float8",
        "e_rmag         float8",
        "e_imag         float8",
        "e_zmag         float8",
        "e_ymag         float8",
        "RoptPhot       text",
        "q_optPhot      text",
        "FHbeta         float8",
        "e_FHbeta       float8",
        "FOIII5007      float8",
        "e_FOIII5007    float8",
        "FOI6300        float8",
        "e_FOI6300      float8",
        "FHalpha        float8",
        "e_FHalpha      float8",
        "FNII6584       float8",
        "e_FNII6584     float8",
        "FSII6717       float8",
        "e_FSII6717     float8",
        "FSII6731       float8",
        "e_FSII6731     float8",
        "Rspec          text",
        "W1_W2_RF       float8",
        "W2_W3_RF       float8",
        "G_R_FIB_RF     float8",
        "ActivClass     int8",
        "RActivClass    text",
        "SFGProb        float8",
        "AGNProb        float8",
        "LINERProb      float8",
        "COMPProb       float8",
        "PASSProb       float8",
        "ebv            float8",
        "e_ebv          float8",
        "logSFRGSW      float8",
        "logMstarGSW    float8",
        "logSFRHEC      float8",
        "q_logSFRHEC    text",
        "r_logSFRHEC    text",
        "n_logSFRHEC    text",
        "logMstarHEC    float8",
        "q_logMstarHEC  text",
        "r_logMstarHEC  text",
        "n_logMstarHEC  text",
        "Metal          float8",
        "n_Metal        text",
        "logMBH         float8",
        "r_logMBH       text",
        "DupFlag        int8",
        "BlendFlag      text",
        "Star           text",
        "WCNotes        text",
        "Notes          text"
    ]

    def __init__(self, dbctxt: DBctxt, path: str, chunk_rows: int = 100000):
        super().__init__(dbctxt, path, chunk_rows)
        self.ra = "RAdeg"
        self.dec = "DEdeg"
        self.bytes_ranges = HecateV2Config.bytes_ranges
        self.relational_schema = HecateV2Config.relational_schema

    def _tabularize(self, path):
        with open(path, "r") as file:
            self.data = file.read()
            self.data = self.data.split("\n")
            for i in range(len(self.data)):
                row = []
                for j in range(len(self.bytes_ranges)):
                    row.append(self.data[i][(self.bytes_ranges[j].start):(self.bytes_ranges[j].stop)])
                    
                self.data[i] = row
    
    def _clean_data(self):
        self.data = self.data[:-1]
        for row in range(len(self.data)):
            for col in range(len(self.data[row])):
                self.data[row][col] = self.data[row][col].replace(" ", "")

                if (self.relational_schema[col].split()[1] == "text"):
                    self.data[row][col] = f"\'{self.data[row][col]}\'"

                if (self.data[row][col] == "---" or self.data[row][col] == "--" or self.data[row][col] == "-" or self.data[row][col] == " " or self.data[row][col] == "" or self.data[row][col] == None or self.data[row][col] == "\'\'"):
                    self.data[row][col] = "NULL"
    
    def _bytes_ranges(self):
        self.bytes_ranges = HecateV2Config.bytes_ranges
        return HecateV2Config.bytes_ranges
    
    def _relational_schema(self):
        self.relational_schema = HecateV2Config.relational_schema
        return self.relational_schema


class DESIDR1Config(BasicAstropyConfig):
    COEFF_COUNT = 10
    COEFF_INDEX = 9
    
    def __init__(self, dbctxt: DBctxt, path: str, chunk_rows: int = 100000):
        super().__init__(dbctxt, path, chunk_rows)
        self.ra  = "TARGET_RA"
        self.dec = "TARGET_DEC"

    def _tabularize(self, path):
        logger.info(f"Opening data file {path}...")
        try:
            self.data = Table(fitsio.read(path, ext=1)) # this will take a while, this is a large file
        except Exception as e:
            logger.info(e)

        logger.info("done loading data file.")

    def _clean_data(self):
        oldcol = self.data['COEFF'].data
        self.data.remove_column('COEFF')
        for i in range(DESIDR1Config.COEFF_COUNT):
            self.data.add_column(oldcol[:, i], index=DESIDR1Config.COEFF_INDEX + i, name=f"COEFF_{i:02}")


class LSDR9Config(BasicAstropyConfig):
    def __init__(self, dbctxt, path, chunk_rows = 100000):
        super().__init__(dbctxt, path, chunk_rows)

    def _tabularize(self, path):
        if (os.path.isfile(path) and os.path.isfile(path.replace(".fits", "-pz.fits"))):
            sweep = Table.read(path, format="fits")
            sweep_pz = Table.read(path.replace(".fits", "-pz.fits"), format="fits")
            self.data = join(sweep, sweep_pz)

    def _clean_data(self):
        pass

    def insert_all(self):
        filenames = os.listdir(self.path)
        filenames = [filename for filename in filenames if (not ("-pz.fits" in filename) and (".fits" in filename))]

        # create table once
        self._tabularize(f"{os.path.dirname(self.path)}/{filenames[0]}")
        self._relational_schema()
        self._clean_data()
        self._create_table()

        # for each file read data and insert to DB
        for file_index, file in enumerate(filenames):
            self._tabularize(f"{os.path.dirname(self.path)}/{file}")
            self._clean_data()
            
            for i in range(0, len(self.data), self.chunk_rows):

                stringified_chunk = ""
                SQL_statement = ""
                rows = range(i, min(len(self.data), i + self.chunk_rows))

                logger.info(f"File {file_index + 1} of {len(filenames)}. Inserting values for rows {rows.start}-{rows.stop} of {len(self.data)}.")

                chunked_vals = self._data2SQL(rows)

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

        q3c_index_table(self.dbctxt, self.ra, self.dec)
        logger.info("done.")


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
        super().__init__(dbctxt, path, chunk_rows)
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
        for rownum, record in enumerate(self.data):
            for elementnum, element in enumerate(record):
                if "character" in self.relational_schema[elementnum]:
                    element = element.replace('"', '"""') # SQL escapes a quote with another quote
                    element = element.replace("'", "''")
                    record[elementnum] = f"\'{element}\'"
                
                if "date" in self.relational_schema[elementnum]:
                    record[elementnum] = f"\'{element}\'"

            self.data[rownum] = record

    def _relational_schema(self):
        self.relational_schema = TwoMASSConfig.relational_schema
        return self.relational_schema

    def _data2SQL(self, rows):
        for rowindex, row in enumerate(self.data):
            self.data[rowindex] = f"({', '.join(row)})"

    def insert_all(self):
        SQL_statement = ""

        self._relational_schema()
        self._create_table()

        filenames = sorted(os.listdir(self.path))
        
        for index, filename in enumerate(filenames):
            
            if filename[:4] == "psc_" and filename[-3:] == ".gz":
                logger.info(f"File index: {index}\t filename: {filename}")
                
                self._tabularize(f"{self.path}/{filename}")
                self._clean_data()
                self._data2SQL()

                for start_index in range(0, len(self.data), self.chunk_rows):
                    logger.info(f"{self.path}/{filename} rows {start_index}:{min(len(self.data), start_index + self.chunk_rows)}")
                    SQL_statement = f"INSERT INTO {self.dbctxt.sql_table} VALUES \n {comma_nl.join(self.data[start_index:start_index + self.chunk_rows])};"
                    execute_statement(self.dbctxt, SQL_statement)
                
                logger.info(f"file {self.path}/{filename} complete.")    
        
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
        super().__init__(dbctxt, path, chunk_rows)
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
        for rownum, record in enumerate(self.data):
            for elementnum, element in enumerate(record):
                if "character" in self.relational_schema[elementnum]:
                    element = element.replace('"', '"""') # SQL escapes a quote with another quote
                    element = element.replace("'", "''")
                    record[elementnum] = f"\'{element}\'"
            self.data[rownum] = record

    def _relational_schema(self):
        self.relational_schema = ZTFVarStarConfig.relational_schema
        return self.relational_schema
