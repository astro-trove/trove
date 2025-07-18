from   astropy.table import Table
import psycopg

from   settings_local import *
import NEDLVS

SOURCE_DATA =       "NEDLVS_20250602.fits"
POSTGRES_DB =       "NEDLVS"
POSTGRES_TABLE =    "NEDLVS"

# #####################################################################
# Encapsulate processing steps into functions
# #####################################################################
def convert_dtypes(ap_dtype: str) -> str:
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

        return conversion_table[ap_dtype]

def relational_schema(NEDLVS_table: Table) -> str:
    new_schema = "( "

    # ap_types = set() # used in devel to get src data types (in the fits file, not implementation)
    for i in range(len(NEDLVS_table.colnames)):
        colname = NEDLVS_table.colnames[i]
        np_dtype = str(type(NEDLVS_table[colname][0]))
        ap_dtype = NEDLVS_table[NEDLVS_table.colnames[i]].dtype.str
        converted = convert_dtypes(ap_dtype)
        
        # ap_types.add(ap_dtype) # used in devel to get src data types (in the fits file, not implementation)
        new_schema += f"\n{colname.ljust(20)}\t{converted},"

    new_schema = new_schema[:-1] # get rid of the trailing comma bc PGSQL syntax won't ignore it
    new_schema += ")"
    # print(ap_types) # used in devel to get src data types (in the fits file, not implementation)
    return new_schema

def record2values(NEDLVS2_table: Table, row: int) -> str:
    # String will look like "INSERT INTO {TABLENAME} VALUES ('<pad string with single quote>', <comma separate everything>);"
    values = "( "

    cols = range(len(NEDLVS2_table.columns))

    for current_col in cols:
        value = str(NEDLVS2_table[row][current_col])
        
        if value == "--":
             value = "null"

        if value == "text":
            values += f"\'{value}\', "
        else:
            values += f"{value}, "

    values = values[:-2] # strip trailing comma bc PGSQL syntax won't accept it
    values += " )"

    return values


# #####################################################################
# Interfacing with PGSQL server starts here
# ##################################################################### 

table = Table.read(SOURCE_DATA)

# #####################################################################
# Define the relational schema from source data
# #####################################################################
new_schema = relational_schema(table)


# #####################################################################
# Create the new table using the new schema
# #####################################################################
# TODO: overwrite records, overwrite schema, append?
SQL_statement = f"CREATE TABLE {POSTGRES_TABLE} {new_schema};"
print(SQL_statement)
with psycopg.connect(host=POSTGRES_HOST, user=POSTGRES_USER, port=POSTGRES_port, password=POSTGRES_PASSWORD, dbname=POSTGRES_DB) as conn:
    with conn.cursor() as cur:
        try:
            cur.execute(SQL_statement)
            conn.commit()
        except psycopg.errors.DuplicateTable:
            print(f"Table {POSTGRES_TABLE} already exists. Attemtping to continue with existing schema...")
        except Exception as e:
            print(e)

# #####################################################################
# Insert records into the new table
# #####################################################################
for i in range(len(table)):
    SQL_statement = f"INSERT INTO {POSTGRES_TABLE} VALUES {record2values(table, i)};"
    print(SQL_statement)
    with psycopg.connect(host=POSTGRES_HOST, user=POSTGRES_USER, port=POSTGRES_port, password=POSTGRES_PASSWORD, dbname=POSTGRES_DB) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(SQL_statement)
                conn.commit()
            except Exception as e:
                print(e)

print(f"{__file__} done.")
