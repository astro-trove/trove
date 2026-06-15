"""
Some useful variables that will be used throughout this entire directory
"""

import numpy as np

import warnings

from django.db.models import (
    Func,
    BooleanField,
    FloatField,
    DecimalField,
    ExpressionWrapper,
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
    if not fluxdict:
        warnings.warn("fluxdict is empty")
    elif "magnitude" in fluxdict.keys() and not ("error" in fluxdict.keys()):
        warnings.warn(
            "Data point contains a magnitude but not an associated " + "error"
        )
    elif not (
        ("magnitude" in fluxdict.keys() and "error" in fluxdict.keys())
        ^ ("limit" in fluxdict.keys())
    ):
        raise ValueError(
            "Must pass EITHER a magnitude and associated error "
            + "OR a limit, but not both"
        )

    _, created = ReducedDatum.objects.get_or_create(
        timestamp=time,
        value=fluxdict,
        source_name=source,
        data_type="photometry",
        target=target,
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
    queryset, ra, dec, radius=RADIUS_ARCSEC, ra_colname="ra", dec_colname="dec"
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

    return queryset.filter(ConeSearch(ra, dec, ra_colname, dec_colname, radius / 3600))


def pcc_q3c(
    queryset, ra, dec, pcc_max, mag_colname, ra_colname="ra", dec_colname="dec"
):
    """Do a cut on Pcc with q3c on the provided QuerySet. Pcc is from Bloom+2002 and
    we use the re-calibration from Berger2010.

    Parameters
    ----------
    queryset : django.QuerySet
        A query set to filter with the cone search
    ra : float
        The RA in degrees
    dec : float
        The dec in degrees
    pcc_max : float
        The maximum Pcc to consider
    mag_colname : str
        The magnitude column name to use for Pcc
    ra_colname: str, default='ra'
        The RA column name in the SQL table
    dec_colname: str, default="dec"
        The dec column name in the SQL table

    Returns
    -------
    django.QuerySet
        The filtered query set
    """
    pcc_prefactor = 1 / (0.33 * np.log(10))
    return queryset.annotate(
        ang_dist=AngDist(ra_colname, dec_colname, ra, dec),
        pcc_sigma=ExpressionWrapper(
            pcc_prefactor
            * Power(
                10,
                Greatest(0.33 * (F(mag_colname) - 24) - 2.44, Value(-10)),
            ),
            output_field=FloatField(),
        ),
        pcc=ExpressionWrapper(
            1
            - Exp(
                Greatest(
                    -1 * Pi() * Power(3600 * F("ang_dist"), 2) * F("pcc_sigma"),
                    -700,  # anything less than e^-700 will be zero anyways!
                ),
            ),
            output_field=FloatField(),
        ),
    ).filter(pcc__lte=pcc_max)


def citation(doi="", ads_bibcode="", version=None, data_url=""):
    """
    A citation decorator for a catalog class to allow us to keep track
    """

    def decorator(cls):

        doi_list = doi
        if not isinstance(doi, list):
            doi_list = [doi]

        ads_bibcode_list = ads_bibcode
        if not isinstance(ads_bibcode, list):
            ads_bibcode_list = [ads_bibcode]

        cls.dois = [d for d in doi_list]
        cls.bibcodes = [b for b in ads_bibcode_list]

        base_ads_url = "https://ui.adsabs.harvard.edu/abs/"
        cls.ads_urls = [base_ads_url + b for b in cls.bibcodes]

        cls.catalog_version = version
        cls.data_url = data_url

        return cls

    return decorator
