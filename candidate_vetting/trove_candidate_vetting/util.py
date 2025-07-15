"""
Some useful variables that will be used throughout this entire directory
"""
from django.db.models import Func, BooleanField

RADIUS_ARCSEC = 2.0

_QUERY_METHOD_DOCSTRING = f"""
Parameters
----------
ra : float
    The RA in degrees
dec : float
    The declination in degrees
radius : float, default={RADIUS_ARCSEC}
    The search radius in arcseconds

Returns
-------
QueryResult
    The result of the query, either a Galaxy or PointSource class
"""

# use 0.83 as the default threshold
# this is from 
PS1_POINT_SOURCE_THRESHOLD = 0.83

class ConeSearch(Func):
    function = "q3c_radial_query"
    arity = 5
    output_field = BooleanField()

def cone_search_q3c(
        queryset,
        ra,
        dec,
        radius=RADIUS_ARCSEC,
        ra_colname = "ra",
        dec_colname = "dec"
):
    f"""Do a cone search with q3c on the provided QuerySet

    Parameters
    ----------
    queryset : django.QuerySet
        A query set to filter with the cone search
    ra : float
        The RA in degrees
    dec : float
        The dec in degrees
    radius : float, default={RADIUS_ARCSEC}
        The search radius in arcseconds
    ra_colname: str, default='ra'
        The RA column name in the SQL table
    dec_colname: str, default="dec"
        The dec column name in the SQL table

    Returns
    -------
    django.QuerySet
        The filtered query set
    """

    return queryset.filter(
        ConeSearch(
            ra_colname,
            dec_colname,
            ra,
            dec,
            radius/3600
        )
    )
