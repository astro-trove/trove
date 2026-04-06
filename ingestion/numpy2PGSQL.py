# convert type from <astropy table>[<colname>].dtype.str to Postgres datatype
# https://numpy.org/doc/stable/reference/arrays.dtypes.html
# https://numpy.org/doc/2.3/reference/generated/numpy.dtype.byteorder.html
# https://numpy.org/doc/stable/reference/arrays.interface.html#arrays-interface
# https://www.postgresql.org/docs/current/datatype.html
#
# Apparently should use "test" over "char [ n ]"
# https://wiki.postgresql.org/wiki/Don't_Do_This#Text_storage

# Converts a string representation of a numpy datatype to its PGSQL analog
# Implemented using match control flow instead of a dict...
# to accomodate different dtype lengths

def convert(dtype_str: str) -> str:
    match (dtype_str[1]):
        case "b":
            if (dtype_str == "|b1"):
                return "boolean"
        case "f":
            match (int(dtype_str[2:])):
                case 4:
                    return "float4"
                case 8:
                    return "float8"
        case "i":
            match (int(dtype_str[2:])):
                case 1:
                    return "int2"
                case 2:
                    return "int2"
                case 4:
                    return "int4"
                case 8:
                    return "int8"
        case "S":
            return "text"
        case "U":
            return "text"
        case "u":
            match (int(dtype_str[2:])):
                # SQL does not support unsigned ints but PGSQL supports "serial"
                case 1:
                    return "smallint"
                case 4:
                    return "bigint"
                case 8:
                    return "bigserial"

    if (isinstance(dtype_str,  str)):
        raise ValueError("unrecognized input datatype string representation")
    else:
        raise TypeError("passed non-string argument to convert()")
