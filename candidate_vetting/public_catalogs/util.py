"""
Some useful variables that will be used throughout this entire directory
"""
import numpy as np
from django.db.models import (
    Func, BooleanField, FloatField, DecimalField, ExpressionWrapper
)
from django.db.models import F, Value
from django.db.models.functions import Pi, Exp, Power

from tom_dataproducts.models import ReducedDatum

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

def create_phot(target, time, fluxdict, source):
    """This creates a photometry point for "target"

    Returns True if it was created, false if it already existed
    """
    _, created = ReducedDatum.objects.get_or_create(
        timestamp = time,
        value = fluxdict,
        source_name = source,
        data_type = "photometry",
        target = target
    )
    return created
    
# use 0.83 as the default threshold
# this is from 
PS1_POINT_SOURCE_THRESHOLD = 0.83

class ConeSearch(Func):
    function = "q3c_join"
    arity = 5
    output_field = BooleanField()

class AngDist(Func):
    function = "q3c_dist"
    arity = 4
    output_field = FloatField()

class Greatest(Func):
    function = "GREATEST"
    arity = 2
    output_field = FloatField()
    
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
            ra,
            dec,
            ra_colname,
            dec_colname,
            radius/3600
        )
    )

def pcc_q3c(
        queryset,
        ra,
        dec,
        pcc_max,
        mag_colname,
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
    pcc_prefactor = 1.31604388456
    return queryset.annotate(
        ang_dist = AngDist(
            ra_colname, dec_colname, ra, dec
        ),
        pcc_sigma = ExpressionWrapper(
            pcc_prefactor * Power(
                10,
                Greatest(
                    0.33*(F(mag_colname) - 24) - 2.44,
                    Value(-10)
                ),
            ),
            output_field = FloatField()
        ),
        pcc = ExpressionWrapper(
            np.pi * Power(F("ang_dist")*3600, 2) * F("pcc_sigma"),
            output_field = FloatField()
        )
    ).filter(
        pcc__lt = pcc_max
    )
