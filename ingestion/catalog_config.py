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

comma_nl = ",\n"

Catalogs = Enum('Catalogs', 
[
    ('COSMICFLOWS4', 'COSMIC_FLOWS_4'),
    ('DESIDR1',      'DESI_DR1'),
    ('FERMILPSC',    'Fermi_LPSC'),
    ('FERMI3FHL',    'Fermi_3FHL'),
    ('HECATE2',      'HECATE2'),
    ('NEDLVS',       'NEDLVS'),
    ('TWOMASS',      'Two_MASS'),
    ('ZTFVARSTAR',   'ZTF_varstar')
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

    def _tabularize(self, path: str):
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


class PlainTextConfig(CatalogConfig):
    def __init__(self, dbctxt: DBctxt, path: str, chunksize: int = 1000):
        self.dbctxt:            DBctxt = dbctxt
        self.path:              str    = path
        self.relational_schema: str    = ""
        self.table                     = None
        self.chunksize                 = chunksize
        self.ra                        = "ra"
        self.dec                       = "dec"

    def _tabularize(self, path: str):
        with open(path, "r") as f:
            content = f.read()
            content = content.split("\n")
            for i, row in enumerate(content):
                row = row.split(",")
                content[i] = row
        
        self.table = content
    
    def _clean_data(self):
        pass
    
    def _relational_schema(self):
        return self.relational_schema
    
    def _create_table(self):
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
                except Exception as e: 
                    raise e

        logger.info("done creating table.")
    
    def _data2SQLValues(self) -> str:
        SQL_VALUE = ""

        for i, row in enumerate(self.table):
            SQL_VALUE = "("
            SQL_VALUE += ", ".join(row)
            SQL_VALUE += ")"
            self.table[i] = SQL_VALUE

    def insert_all(self):
        self._tabularize(self.path)
        self._clean_data()
        self._relational_schema()
        self._create_table()
        self._data2SQLValues()

        for i in range(len(self.table) // self.chunksize + 1):
            logger.debug(f"Inserting chunk {i+1} of {len(self.table) // self.chunksize + 1}")
            SQL_STATEMENT = f"INSERT INTO {self.dbctxt.sql_table} VALUES {comma_nl.join(self.table[(i * self.chunksize):(i + 1) * self.chunksize])};"
            execute_statement(self.dbctxt, SQL_STATEMENT)

        q3c_index_table(self.dbctxt, self.ra, self.dec)


class CosmicFlows4Config(PlainTextConfig):
    def __init__(self, dbctxt, path):
        super().__init__(dbctxt, path)
        self.ra  = "RAJ2000"
        self.dec = "DEJ2000"
        self.relational_schema = [
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
            "E_BmV double precision",
            "logM double precision",
            "fRel double precision",
            "fracNearby double precision"
        ]

    def _clean_data(self):
        self.table = self.table[1:-1]
        for i, row in enumerate(self.table):
            self.table[i][1] = f"\'{row[1]}\'"

            for j, col in enumerate(row):
                if (col == ""):
                    self.table[i][j] = "NULL"


class HecateV2Config(PlainTextConfig):
    def __init__(self, dbctxt: DBctxt, path: str, chunksize: int = 1000):
        super().__init__(dbctxt, path)
        self.ra = "RAdeg"
        self.dec = "DEdeg"
        
        self.bytes_ranges = [
            range(1,7),
            range(9,38),
            range(40,58),
            range(60,78),
            range(80,98),
            range(100,117),
            range(119,128),
            range(130,139),
            range(141,142),
            range(144,153),
            range(155,164),
            range(166,175),
            range(177,178),
            range(180,200),
            range(202,211),
            range(213,215),
            range(217,225),
            range(227,238),
            range(240,252),
            range(254,265),
            range(267,279),
            range(281,292),
            range(294,304),
            range(306,306),
            range(308,314),
            range(316,323),
            range(325,330),
            range(332,337),
            range(339,344),
            range(346,351),
            range(353,357),
            range(359,363),
            range(365,369),
            range(371,375),
            range(377,388),
            range(390,401),
            range(403,414),
            range(416,427),
            range(429,429),
            range(431,431),
            range(433,433),
            range(435,435),
            range(437,447),
            range(449,459),
            range(461,471),
            range(473,482),
            range(484,488),
            range(490,494),
            range(496,500),
            range(502,506),
            range(508,520),
            range(522,529),
            range(531,539),
            range(541,549),
            range(551,560),
            range(562,571),
            range(573,582),
            range(584,594),
            range(596,607),
            range(609,620),
            range(622,627),
            range(629,634),
            range(636,641),
            range(643,647),
            range(649,656),
            range(658,662),
            range(664,664),
            range(666,677),
            range(679,690),
            range(692,703),
            range(705,716),
            range(718,729),
            range(731,741),
            range(743,754),
            range(756,767),
            range(769,780),
            range(782,793),
            range(795,806),
            range(808,819),
            range(821,826),
            range(828,833),
            range(835,846),
            range(848,859),
            range(861,872),
            range(874,885),
            range(887,898),
            range(900,911),
            range(913,924),
            range(926,937),
            range(939,950),
            range(952,963),
            range(965,976),
            range(978,989),
            range(991,1002),
            range(1004,1015),
            range(1017,1024),
            range(1026,1031),
            range(1033,1038),
            range(1040,1045),
            range(1047,1047),
            range(1049,1053),
            range(1055,1060),
            range(1062,1067),
            range(1069,1074),
            range(1076,1081),
            range(1083,1088),
            range(1090,1101),
            range(1103,1114),
            range(1116,1125),
            range(1127,1135),
            range(1137,1146),
            range(1148,1152),
            range(1154,1156),
            range(1158,1166),
            range(1168,1176),
            range(1178,1182),
            range(1184,1186),
            range(1188,1212),
            range(1214,1221),
            range(1223,1230),
            range(1232,1240),
            range(1242,1250),
            range(1252,1258),
            range(1260,1286),
            range(1288,1289),
            range(1291,1293),
            range(1295,1348)
        ]

        self.relational_schema = [
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
            "EoBmV         float8",
            "e_EoBmV       float8",
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

    def _tabularize(self, path):
        with open(path, "r") as file:
            self.table = file.read()
            self.table = self.table.split("\n")
            for i in range(len(self.table)):
                row = []
                for j in range(len(self.bytes_ranges)):
                    row.append(self.table[i][(self.bytes_ranges[j].start):(self.bytes_ranges[j].stop)])
                    
                self.table[i] = row
    
    def _clean_data(self):
        self.table = self.table[:-1]
        for row in range(len(self.table)):
            for col in range(len(self.table[row])):
                self.table[row][col] = self.table[row][col].replace(" ", "")

                if (self.relational_schema[col].split()[1] == "text"):
                    self.table[row][col] = f"\'{self.table[row][col]}\'"

                if (self.table[row][col] == "---" or self.table[row][col] == "--" or self.table[row][col] == "-" or self.table[row][col] == " " or self.table[row][col] == "" or self.table[row][col] == None or self.table[row][col] == "\'\'"):
                    self.table[row][col] = "NULL"
    

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

