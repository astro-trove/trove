"""
Define the static catalogs for querying
"""

from astropy import units as u
from astropy.cosmology import z_at_value
import pandas as pd
import numpy as np

from django.db.models import F, Q, Func, Value, IntegerField, Case, When, CharField
from django.db.models.functions import Cast
from django.conf import settings

from .catalog import StaticCatalog
from .util import PS1_POINT_SOURCE_THRESHOLD, RADIUS_ARCSEC, citation
from ..models import (
    AsassnQ3C,
    Cosmicflows4Q3C,
    DesiSpecQ3C,
    DesiDr1Q3C,
    FermiLatQ3C,
    Gaiadr3VariableQ3C,
    GladePlusQ3C,
    GwgcQ3C,
    Hecate1Q3C,
    Hecate2Q3C,
    LsDr9NorthQ3C,
    LsDr10SouthQ3C,
    MilliquasQ3C,
    NedlvsQ3C,
    Ps1Q3C,
    RomaBzcatQ3C,
    Sdss12PhotozQ3C,
    TwomassQ3C,
    ZtfVarstarQ3C,
)


cosmo = settings.COSMO


class _Log10(Func):
    function = "LOG10"
    template = "%(function)s(%(expressions)s)"


@citation(
    doi=["10.1088/0004-637X/788/1/48", "10.1093/mnras/stac3801"],
    ads_bibcode=["2014ApJ...788...48S", "2023MNRAS.519.5271C"],
    version=10,
)
class AsassnVariableStar(StaticCatalog):
    """
    Version X (10) of the ASAS-SN catalog of variable stars; ASAS-SN described
    in Shappee et al 2014
    """

    catalog_model = AsassnQ3C


@citation(
    doi=[
        "10.1088/0004-6256/138/2/323",
        "10.1038/s41550-024-02370-0",
        "10.3847/1538-4357/ac94d8",
        "10.1051/0004-6361/202556896",
        "10.1088/1674-4527/ac6416",
        "10.3847/1538-4365/ad409d",
    ],
    ads_bibcode=[
        "2009AJ....138..323T",
        "2024NatAs...8.1610V",
        "2023ApJ...944...94T",
        "2026A%26A...706A.284T",
        "2022RAA....22f5001Z",
        "2024ApJS..272...39W",
    ],
    version=4,
    data_url="https://cdsarc.cds.unistra.fr/viz-bin/cat/J/A+A/706/A284#/browse",
)
class Cosmicflows4(StaticCatalog):
    """
    Cosmicflows 4 catalogue as curated by REGALADE who crossmatched
    with photometry catalogs (which are also included in the citation list)
    """

    catalog_model = Cosmicflows4Q3C
    ra_colname = "raj2000"
    dec_colname = "dej2000"
    mag_colname = "rmag"

    colmap = {
        "cid": "trove_uniq",
        "name": "name",
        "z": "z",
        "dist": "lumdist",  # Mpc
        "e_dist": "lumdist_err",  # Mpc
        "raj2000": "ra",
        "dej2000": "dec",
        "rmag": "default_mag",  # magnitude column to use for pcc
    }

    def to_standardized_catalog(self, df):
        df["lumdist_neg_err"] = df.e_dist
        df["lumdist_pos_err"] = df.e_dist

        self.colmap["lumdist_neg_err"] = "lumdist_neg_err"
        self.colmap["lumdist_pos_err"] = "lumdist_pos_err"

        df["z_type"] = "z-ind."
        df["submitter"] = ""

        df = self._standardize_df(df)

        return df


@citation(doi="10.3847/1538-3881/ae4c43", ads_bibcode="2026AJ....171..285D")
class DesiDr1(StaticCatalog):
    """
    Data Release 1 of the Dark Energy Spectroscopic Instrument spectroscopic
    redshifts
    """

    catalog_model = DesiDr1Q3C
    ra_colname = "target_ra"
    dec_colname = "target_dec"
    mag_colname = "default_mag"

    def __init__(self):
        self.catalog_model.objects = self.catalog_model.objects.filter(
            flux_r__gt=0,  # anything with a negative flux (in the linear nanomaggy unit) can be ignored
            zwarn=0,  # we don't want anything with ZWARN > 0: https://data.desi.lbl.gov/doc/releases/dr1/
            zcat_primary=True,  # only take the best ("primary") redshift for this target
            z__gt=0,  # some z's are negative for some reason
        ).annotate(default_mag=22.5 - 2.5 * _Log10("flux_r"))

        self.colmap = {
            "id": "trove_uniq",
            "desiname": "name",
            "z": "z",
            "zerr": "z_err",
            "target_ra": "ra",
            "target_dec": "dec",
            "default_mag": "default_mag",
        }

        # then, of course, init the super class
        super().__init__()

    def to_standardized_catalog(self, df):
        df = self._standardize_df(df)
        df["lumdist"] = cosmo.luminosity_distance(df.z).to(u.Mpc).value
        df["lumdist_err"] = cosmo.luminosity_distance(df.z_err).to(u.Mpc).value
        df["z_neg_err"] = df.z_err
        df["z_pos_err"] = df.z_err
        df["lumdist_neg_err"] = df.lumdist_err
        df["lumdist_pos_err"] = df.lumdist_err
        df["z_type"] = "spec-z"
        df["submitter"] = ""
        return df


class DesiSpec(StaticCatalog):
    catalog_model = DesiSpecQ3C
    ra_colname = "target_ra"
    dec_colname = "target_dec"
    mag_colname = "default_mag"

    def __init__(self):
        # flux_r is in nanomaggy
        self.catalog_model.objects = self.catalog_model.objects.filter(
            flux_r__gt=0
        ).annotate(default_mag=22.5 - 2.5 * _Log10("flux_r"))

        self.colmap = {
            "did": "trove_uniq",
            "targetid": "name",
            "z": "z",
            "zerr": "z_err",
            "target_ra": "ra",
            "target_dec": "dec",
            "default_mag": "default_mag",
        }

        # then, of course, init the super class
        super().__init__()

    def to_standardized_catalog(self, df):
        df = self._standardize_df(df)
        df["lumdist"] = cosmo.luminosity_distance(df.z).to(u.Mpc).value
        df["lumdist_err"] = cosmo.luminosity_distance(df.z_err).to(u.Mpc).value
        df["z_neg_err"] = df.z_err
        df["z_pos_err"] = df.z_err
        df["lumdist_neg_err"] = df.lumdist_err
        df["lumdist_pos_err"] = df.lumdist_err
        df["z_type"] = "spec-z"
        df["submitter"] = ""
        return df


@citation()
class FermiLat(StaticCatalog):
    """
    TODO: Add catalog description
    TODO: Add citation
    """

    catalog_model = FermiLatQ3C


@citation(doi="10.1051/0004-6361/202243940", ads_bibcode="2023A%26A...674A...1G")
class Gaiadr3Variable(StaticCatalog):
    catalog_model = Gaiadr3VariableQ3C


@citation(doi="10.1093/mnras/stac1443", ads_bibcode="2022MNRAS.514.1403D")
class GladePlus(StaticCatalog):
    catalog_model = GladePlusQ3C
    mag_colname = "b"

    colmap = {
        "gid": "trove_uniq",
        "gn": "name",
        "z_helio": "z",
        "z_err": "z_err",
        "d_l": "lumdist",  # Mpc
        "d_l_err": "lumdist_err",  # Mpc
        "ra": "ra",
        "dec": "dec",
        "b": "default_mag",  # magnitude column to use for pcc
    }

    def __init__(self):
        super().__init__()
        self.ogcols += ["dist_flag"]  # for the z_type column

    def to_standardized_catalog(self, df):

        def _parse_dist_flag_col(row):
            if row.dist_flag <= 1:
                return "photo-z"
            return "spec-z"

        df["z_type"] = df.apply(_parse_dist_flag_col, axis=1)

        df = self._standardize_df(df)
        df["z_neg_err"] = df.z_err
        df["z_pos_err"] = df.z_err

        lumdist_err = pd.Series(
            cosmo.luminosity_distance(df.z_err).to(u.Mpc).value, index=df.index
        )
        df.lumdist_err = df.lumdist_err.fillna(lumdist_err)
        df["lumdist_neg_err"] = df.lumdist_err
        df["lumdist_pos_err"] = df.lumdist_err
        df["submitter"] = ""
        return df


@citation(doi="10.1088/0264-9381/28/8/085016", ads_bibcode="2011CQGra..28h5016W")
class Gwgc(StaticCatalog):
    catalog_model = GwgcQ3C
    colmap = {
        "gid": "trove_uniq",
        "name": "name",
        "dist": "lumdist",  # Mpc
        "e_dist": "lumdist_err",  # Mpc
        "ra": "ra",
        "dec": "dec",
        "b_app": "default_mag",  # magnitude column to use for pcc
    }
    mag_colname = "b_app"

    def to_standardized_catalog(self, df):
        df = self._standardize_df(df)
        df["lumdist_neg_err"] = df.lumdist_err
        df["lumdist_pos_err"] = df.lumdist_err
        df["z_type"] = "spec-z/z ind."
        df["submitter"] = ""
        return df


@citation(doi="10.1093/mnras/stab1799", ads_bibcode="2021MNRAS.506.1896K")
class Hecate1(StaticCatalog):
    catalog_model = Hecate1Q3C
    mag_colname = "r"
    colmap = {
        "hid": "trove_uniq",
        "objname": "name",
        "d": "lumdist",  # Mpc
        "e_d": "lumdist_err",  # Mpc
        "ra": "ra",
        "dec": "dec",
        "r": "default_mag",  # magnitude to use for pcc
    }

    def __init__(self):
        super().__init__()
        self.ogcols += ["d_lo68", "d_hi68", "dmethod"]

    def to_standardized_catalog(self, df):
        df["lumdist_neg_err"] = df.d - df.d_lo68
        df["lumdist_pos_err"] = df.d_hi68 - df.d

        self.colmap["lumdist_neg_err"] = "lumdist_neg_err"
        self.colmap["lumdist_pos_err"] = "lumdist_pos_err"

        df["z_type"] = df.apply(
            lambda row: "z ind." if row.dmethod == "N" else "spec-z", axis=1
        )

        df["submitter"] = ""

        df = self._standardize_df(df)

        return df


@citation(
    doi="10.1093/mnras/stag522",
    ads_bibcode="2026MNRAS.548ag522K",
    version=2,
    data_url="https://cdsarc.cds.unistra.fr/viz-bin/cat/J/MNRAS/548/G522#/article",
)
class Hecate2(StaticCatalog):
    catalog_model = Hecate2Q3C
    ra_colname = "radeg"
    dec_colname = "dedeg"
    mag_colname = "rmag"

    colmap = {
        "hid": "trove_uniq",
        "objname": "name",
        "dist": "lumdist",  # Mpc
        "e_dist": "lumdist_err",  # Mpc
        "radeg": "ra",
        "dedeg": "dec",
        "rmag": "default_mag",  # magnitude to use for pcc
    }

    def __init__(self):
        super().__init__()
        self.ogcols += ["f_dist"]

    def to_standardized_catalog(self, df):
        df["lumdist_neg_err"] = df.e_dist
        df["lumdist_pos_err"] = df.e_dist
        # df["z"] = z_at_value(cosmo.luminosity_distance, ### TODO: I think this my be slow, but HECATEv2 doesn't have z's. Remove?
        #                     df.dist.values * u.Mpc)

        self.colmap["lumdist_neg_err"] = "lumdist_neg_err"
        self.colmap["lumdist_pos_err"] = "lumdist_pos_err"
        # self.colmap["z"] = "z"

        df["z_type"] = df.apply(
            lambda row: "z ind." if row.f_dist == 0 else "spec-z", axis=1
        )

        df["submitter"] = ""

        df = self._standardize_df(df)

        return df


@citation(
    doi=["10.3847/1538-3881/ab089d", "10.1088/1475-7516/2023/11/097"],
    ads_bibcode=["2019AJ....157..168D", "2023JCAP...11..097Z"],
    version="DR9 North",
    data_url=[
        "https://portal.nersc.gov/cfs/cosmo/data/legacysurvey/dr9/north/sweep/9.0/",
        "https://portal.nersc.gov/cfs/cosmo/data/legacysurvey/dr9/north/sweep/9.1-photo-z/",
    ],
)
class LsDr9North(StaticCatalog):
    """
    Northwern sweeps of Data Release 9 of the Legacy Survey, with object
    photo-z's computed following Zhou et al 2023
    """

    catalog_model = LsDr9NorthQ3C
    dec_colname = "dec"

    def __init__(self):
        # flux_r is in nanomaggy
        self.catalog_model.objects = self.catalog_model.objects.filter(
            flux_r__gt=0
        ).annotate(default_mag=22.5 - 2.5 * _Log10("flux_r"))

        self.colmap = {
            "lid": "trove_uniq",
            "objid": "name",
            "ra": "ra",
            "dec": "dec",
            "default_mag": "default_mag",
            "z_phot_mean": "z",
            "z_phot_std": "z_err",
        }

        self.mag_colname = "default_mag"

        # then, of course, init the super class
        super().__init__()
        self.ogcols += ["z_phot_l68", "z_phot_u68"]

    def to_standardized_catalog(self, df):
        df["z_neg_err"] = df.z_phot_mean - df.z_phot_l68
        df["z_pos_err"] = df.z_phot_u68 - df.z_phot_mean

        self.colmap["z_neg_err"] = "z_neg_err"
        self.colmap["z_pos_err"] = "z_pos_err"

        df = self._standardize_df(df)
        df["lumdist"] = cosmo.luminosity_distance(df.z).to(u.Mpc).value
        df["lumdist_err"] = cosmo.luminosity_distance(df.z_err).to(u.Mpc).value
        df["lumdist_neg_err"] = cosmo.luminosity_distance(df.z_neg_err).to(u.Mpc).value
        df["lumdist_pos_err"] = cosmo.luminosity_distance(df.z_pos_err).to(u.Mpc).value
        df["z_type"] = "photo-z"
        df["submitter"] = ""
        return df

    def query(self, ra, dec, radius=RADIUS_ARCSEC):
        query_set = super().query(ra, dec, radius)
        return query_set.exclude(
            default_mag__lt=18, type="PSF"
        )  # exclude PSF magnitudes that are likely point sources


@citation(
    doi=["10.3847/1538-3881/ab089d", "10.1088/1475-7516/2023/11/097"],
    ads_bibcode=["2019AJ....157..168D", "2023JCAP...11..097Z"],
    version="DR10 South",
    data_url=[
        "https://portal.nersc.gov/cfs/cosmo/data/legacysurvey/dr10/south/sweep/10.1/",
        "https://portal.nersc.gov/cfs/cosmo/data/legacysurvey/dr10/south/sweep/10.1-photo-z/",
    ],
)
class LsDr10South(StaticCatalog):
    """
    Southern sweeps of Data Release 10 of the Legacy Survey, with object
    photo-z's computed following Zhou et al 2023
    """

    catalog_model = LsDr10SouthQ3C
    dec_colname = "declination"

    def __init__(self):
        # flux_r is in nanomaggy
        self.catalog_model.objects = self.catalog_model.objects.filter(
            flux_r__gt=0
        ).annotate(default_mag=22.5 - 2.5 * _Log10("flux_r"))

        self.colmap = {
            "lid": "trove_uniq",
            "objid": "name",
            "ra": "ra",
            "declination": "dec",
            "default_mag": "default_mag",
            "z_phot_mean": "z",
            "z_phot_std": "z_err",
        }

        self.mag_colname = "default_mag"

        # then, of course, init the super class
        super().__init__()
        self.ogcols += ["z_phot_l68", "z_phot_u68"]

    def to_standardized_catalog(self, df):
        df["z_neg_err"] = df.z_phot_mean - df.z_phot_l68
        df["z_pos_err"] = df.z_phot_u68 - df.z_phot_mean

        self.colmap["z_neg_err"] = "z_neg_err"
        self.colmap["z_pos_err"] = "z_pos_err"

        df = self._standardize_df(df)
        df["lumdist"] = cosmo.luminosity_distance(df.z).to(u.Mpc).value
        df["lumdist_err"] = cosmo.luminosity_distance(df.z_err).to(u.Mpc).value
        df["lumdist_neg_err"] = cosmo.luminosity_distance(df.z_neg_err).to(u.Mpc).value
        df["lumdist_pos_err"] = cosmo.luminosity_distance(df.z_pos_err).to(u.Mpc).value
        df["z_type"] = "photo-z"
        df["submitter"] = ""
        return df

    def query(self, ra, dec, radius=RADIUS_ARCSEC):
        query_set = super().query(ra, dec, radius)
        return query_set.exclude(
            default_mag__lt=18, mtype="PSF"
        )  # exclude PSF magnitudes that are likely point sources


@citation(doi="10.21105/astro.2308.01505", ads_bibcode="2023OJAp....6E..49F", version=8)
class Milliquas(StaticCatalog):
    """
    Million Quasars Catalog, Version 8
    """

    catalog_model = MilliquasQ3C

    def __init__(self):
        """Override the StaticCatalog init because Milliquas doesn't come with a
        redshift error column, so we need to make some assumptions
        """

        num_decimal = Func(
            Func(
                Cast(
                    F("z"), CharField()
                ),  # split_part only works with CharField, need to cast first
                Value("."),
                Value(2),
                function="split_part",
                output_field=CharField(),
            ),  # this gets just the decimals of the z field as a string
            function="length",
            output_field=IntegerField(),
        )  # then this counts the number of decimal places

        self.catalog_model.objects = self.catalog_model.objects.annotate(
            num_decimal=num_decimal  # this gives a temp row with the number of decimals
        ).annotate(
            z_err=Case(
                When(num_decimal__lte=1, then=F("z") * 0.1),
                When(num_decimal=2, then=F("z") * 0.01),
                default=Value(1e-3),
            ),  # this computes the z_err based on the assumptions outlined in the docs for this catalog
            z_type=Case(
                When(num_decimal__lte=2, then=Value("photo-z")), default=Value("spec-z")
            ),
        )

        # now that we have these annotations, we can define the colmap
        self.colmap = {
            "name": "name",
            "ra": "ra",
            "dec": "dec",
            "z": "z",
            "z_err": "z_err",
            "rmag": "default_mag",  # mag col to use for pcc
        }

        self.mag_colname = "rmag"

        # then, of course, init the super class
        super().__init__()

    def to_standardized_catalog(self, df):
        df = self._standardize_df(df)
        df["z_neg_err"] = df.z_err
        df["z_pos_err"] = df.z_err
        df["lumdist"] = cosmo.luminosity_distance(df.z).to(u.Mpc).value
        df["lumdist_err"] = cosmo.luminosity_distance(df.z_err).to(u.Mpc).value
        df["lumdist_neg_err"] = df.lumdist_err
        df["lumdist_pos_err"] = df.lumdist_err
        df["z_type"] = "spec-z"
        df["submitter"] = ""
        return df


@citation(doi="10.3847/1538-4365/acdd06", ads_bibcode="2023ApJS..268...14C")
class NedLvs(StaticCatalog):
    """
    Local Volume Survey (LVS) of the NASA Extragalactic Database (NED), dominated
    by redshift-independent distances and spectroscopic redshifts of galaxies, with
    some photometric redshifts; acquired 2 June 2025
    """

    mag_colname = "m_j"
    catalog_model = NedlvsQ3C
    colmap = {
        "id": "trove_uniq",
        "objname": "name",
        "z": "z",
        "z_unc": "z_err",
        "distmpc": "lumdist",  # Mpc
        "distmpc_unc": "lumdist_err",  # Mpc
        "ra": "ra",
        "dec": "dec",
        "m_j": "default_mag",  # use 2MASS J for the Pcc magnitude
    }

    def __init__(self):
        super().__init__()
        self.ogcols += ["distmpc_method", "z_tech"]

    def to_standardized_catalog(self, df):
        def _get_ztype(row):
            if row.distmpc_method == "zIndependent":
                return "z ind."
            if row.z_tech == "SPEC":
                return "spec-z"
            return "photo-z"

        df["z_type"] = df.apply(_get_ztype, axis=1)

        df = self._standardize_df(df)

        # some rows don't have uncertainty on redshift
        # we can assume these are spec-z's with very small uncertainty
        df["z_err"] = df.z_err.fillna(1e-3)
        df["z_neg_err"] = df.z_err
        df["z_pos_err"] = df.z_err

        lumdist_err = pd.Series(
            cosmo.luminosity_distance(df.z_err).to(u.Mpc).value, index=df.index
        )
        # when lumdist_err is NaN is when the z_err column is also NaN
        # so we assume an uncertainty on the distance of ~4.5 Mpc (the conversion
        # from z_err = 1e-3 to Mpc)
        df.lumdist_err = df.lumdist_err.fillna(lumdist_err)
        df["lumdist_neg_err"] = df.lumdist_err
        df["lumdist_pos_err"] = df.lumdist_err

        df["submitter"] = ""

        return df


@citation(doi="10.1093/mnras/staa2587", ads_bibcode="2021MNRAS.500.1633B")
class Ps1(StaticCatalog):
    """
    Pan-STARRS 1 Source Types and Redshifts with Machine Learning (PS1-STRM)
    catalogue, which classifies sources as point sources, quasars, or galaxies
    """

    catalog_model = Ps1Q3C
    colmap = {
        "pid": "trove_uniq",
        "objname": "name",
        "ra": "ra",
        "dec": "dec",
        "z_phot": "z",
        "z_err": "z_err",
        "rmeanpsfmag": "default_mag",  # mag col to use for pcc
    }
    mag_colname = "rmeanpsfmag"

    def to_standardized_catalog(self, df):
        df = self._standardize_df(df)
        df["z_neg_err"] = df.z_err
        df["z_pos_err"] = df.z_err
        df["lumdist"] = cosmo.luminosity_distance(df.z).to(u.Mpc).value
        df["lumdist_err"] = cosmo.luminosity_distance(df.z_err).to(u.Mpc).value
        df["lumdist_neg_err"] = df.lumdist_err
        df["lumdist_pos_err"] = df.lumdist_err
        df["z_type"] = "photo-z"
        df["submitter"] = ""
        return df


@citation(doi="10.1093/mnras/staa2587", ads_bibcode="2021MNRAS.500.1633B")
class Ps1Galaxy(Ps1):
    """
    Pan-STARRS 1 Source Types and Redshifts with Machine Learning (PS1-STRM)
    catalogue, which classifies sources as point sources, quasars, or galaxies,
    selecting for objects with point source score < 0.83
    """

    def query(self, ra, dec, radius=RADIUS_ARCSEC):
        query_set = super().query(ra, dec, radius)
        return query_set.filter(
            ps_score__lte=PS1_POINT_SOURCE_THRESHOLD, rmeanpsfmag__gt=0
        )


@citation(doi="10.1093/mnras/staa2587", ads_bibcode="2021MNRAS.500.1633B")
class Ps1PointSource(Ps1):
    """
    Pan-STARRS 1 Source Types and Redshifts with Machine Learning (PS1-STRM)
    catalogue, which classifies sources as point sources, quasars, or galaxies,
    selecting for objects with galaxy score < 0.7
    """

    def query(self, ra, dec, radius=RADIUS_ARCSEC):
        query_set = super().query(ra, dec, radius)
        return query_set.filter(
            ps_score__gt=PS1_POINT_SOURCE_THRESHOLD, prob_galaxy__lt=0.7
        )


@citation()
class RomaBzcat(StaticCatalog):
    """
    TODO: Add catalog description
    TODO: Add citation
    """

    catalog_model = RomaBzcatQ3C


@citation(
    doi="10.1088/0067-0049/219/1/12",
    ads_bibcode="2015ApJS..219...12A",
    version="DR11 & DR12",
)
class Sdss12Photoz(StaticCatalog):
    """
    Photometric redshifts catalog built using imaging and spectra from Data
    Releases 11 and 12 of SDSS
    """

    mag_colname = "rmag"
    catalog_model = Sdss12PhotozQ3C
    colmap = {
        "sid": "trove_uniq",
        "sdssid": "name",
        "ra": "ra",
        "dec": "dec",
        "zph": "z",
        "e_zph": "z_err",
        "rmag": "default_mag",
    }

    def to_standardized_catalog(self, df):
        df = self._standardize_df(df)
        df["z_neg_err"] = df.z_err
        df["z_pos_err"] = df.z_err
        df["lumdist"] = cosmo.luminosity_distance(df.z).to(u.Mpc).value
        df["lumdist_err"] = cosmo.luminosity_distance(df.z_err).to(u.Mpc).value
        df["lumdist_neg_err"] = df.lumdist_err
        df["lumdist_pos_err"] = df.lumdist_err
        df["z_type"] = "photo-z"
        df["submitter"] = ""
        return df


@citation()
class TwoMass(StaticCatalog):
    """
    TODO: Add short description
    TODO: Add citation
    """

    catalog_model = TwomassQ3C
    ra_colname = "ra"
    dec_colname = "decl"


@citation(doi="10.3847/1538-4365/ab9cae", ads_bibcode="2020ApJS..249...18C")
class ZtfVarStar(StaticCatalog):
    """
    Periodic variable stars from the Zwicky Transient Facility
    """

    catalog_model = ZtfVarstarQ3C
    ra_colname = "radeg"
    dec_colname = "dedeg"
