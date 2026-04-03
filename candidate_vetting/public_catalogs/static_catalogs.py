"""
Define the static catalogs for querying
"""
from astropy import units as u
import pandas as pd
import numpy as np

from django.db.models import F, Q, Func, Value, IntegerField, Case, When, CharField
from django.db.models.functions import Cast
from django.conf import settings
cosmo = settings.COSMO

from .catalog import StaticCatalog
from .util import PS1_POINT_SOURCE_THRESHOLD, RADIUS_ARCSEC
from ..models import (
    AsassnQ3C,
    DesiSpecQ3C,
    FermiLatQ3C,
    Gaiadr3VariableQ3C,
    GladePlusQ3C,
    GwgcQ3C,
    HecateQ3C,
    LsDr10Q3C,
    MilliquasQ3C,
    Ps1Q3C,
    RomaBzcatQ3C,
    Sdss12PhotozQ3C,
    ZtfVarstarQ3C,
    TwomassQ3C,
    NedlvsQ3C,
    DesiDr1Q3C
)

class _Log10(Func):
    function = 'LOG10'
    template = '%(function)s(%(expressions)s)'

class AsassnVariableStar(StaticCatalog):
    catalog_model = AsassnQ3C

class TwoMass(StaticCatalog):
    catalog_model = TwomassQ3C
    ra_colname = "ra"
    dec_colname = "decl"
    
class DesiDr1(StaticCatalog):
    catalog_model = DesiDr1Q3C
    ra_colname = "target_ra"
    dec_colname = "target_dec"

    def __init__(self):
        self.catalog_model.objects = self.catalog_model.objects.filter(
            flux_r__gt=0, # anything with a negative flux (in the linear nanomaggy unit) can be ignored
            zwarn = 0, # we don't want anything with ZWARN > 0: https://data.desi.lbl.gov/doc/releases/dr1/
            zcat_primary = True # only take the best ("primary") redshift for this target
        ).annotate(
            default_mag=22.5-2.5*_Log10('flux_r')
        )

        self.colmap = {
            "id":"trove_uniq",
            "desiname":"name",
            "z":"z",
            "zerr":"z_err",
            "target_ra":"ra",
            "target_dec":"dec",
            "default_mag":"default_mag"
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
        return df
    
class NedLvs(StaticCatalog):
    catalog_model = NedlvsQ3C
    colmap = {
        "id":"trove_uniq",
        "objname":"name",
        "z":"z",
        "z_unc":"z_err", 
        "distmpc": "lumdist", # Mpc
        "distmpc_unc":"lumdist_err", # Mpc
        "ra":"ra",
        "dec":"dec",
        "m_j":"default_mag" # use 2MASS J for the Pcc magnitude
    }

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
            cosmo.luminosity_distance(df.z_err).to(u.Mpc).value,
            index = df.index
        ) 
        # when lumdist_err is NaN is when the z_err column is also NaN
        # so we assume an uncertainty on the distance of ~4.5 Mpc (the conversion
        # from z_err = 1e-3 to Mpc)
        df.lumdist_err = df.lumdist_err.fillna(lumdist_err)
        df["lumdist_neg_err"] = df.lumdist_err
        df["lumdist_pos_err"] = df.lumdist_err

        return df
    
class ZtfVarStar(StaticCatalog):
    catalog_model = ZtfVarstarQ3C
    ra_colname = "radeg"
    dec_colname = "dedeg"
    
class DesiSpec(StaticCatalog):
    catalog_model = DesiSpecQ3C
    ra_colname = "target_ra"
    dec_colname = "target_dec"
    
    def __init__(self):
        # flux_r is in nanomaggy
        self.catalog_model.objects = self.catalog_model.objects.filter(
            flux_r__gt=0
        ).annotate(
            default_mag=22.5-2.5*_Log10('flux_r')
        )

        self.colmap = {
            "did":"trove_uniq",
            "targetid":"name",
            "z":"z",
            "zerr":"z_err",
            "target_ra":"ra",
            "target_dec":"dec",
            "default_mag":"default_mag",
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
        return df    
        
class FermiLat(StaticCatalog):
    catalog_model = FermiLatQ3C
    
class Gaiadr3Variable(StaticCatalog):
    catalog_model = Gaiadr3VariableQ3C
    
class GladePlus(StaticCatalog):
    catalog_model = GladePlusQ3C
    colmap = {
        "gid":"trove_uniq",
        "gn":"name",
        "z_helio":"z",
        "z_err":"z_err", 
        "d_l": "lumdist", # Mpc
        "d_l_err":"lumdist_err", # Mpc
        "ra":"ra",
        "dec":"dec",
        "b":"default_mag" # magnitude column to use for pcc
    }

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
            cosmo.luminosity_distance(df.z_err).to(u.Mpc).value,
            index = df.index
        )
        df.lumdist_err = df.lumdist_err.fillna(lumdist_err)
        df["lumdist_neg_err"] = df.lumdist_err
        df["lumdist_pos_err"] = df.lumdist_err        
        return df

class Gwgc(StaticCatalog):
    catalog_model = GwgcQ3C
    colmap = {
        "gid":"trove_uniq",
        "name":"name",
        "dist":"lumdist", # Mpc
        "e_dist":"lumdist_err", # Mpc
        "ra":"ra",
        "dec":"dec",
        "b_app":"default_mag" # magnitude column to use for pcc
    }

    def to_standardized_catalog(self, df):
        df = self._standardize_df(df)
        df["lumdist_neg_err"] = df.lumdist_err
        df["lumdist_pos_err"] = df.lumdist_err
        df["z_type"] = "spec-z/z ind."
        return df
    
class Hecate(StaticCatalog):
    catalog_model = HecateQ3C
    colmap = {
        "hid":"trove_uniq",
        "objname":"name",
        "d":"lumdist", # Mpc
        "e_d":"lumdist_err", # Mpc
        "ra":"ra",
        "dec":"dec",
        "r":"default_mag" # magnitude to use for pcc
    }

    def to_standardized_catalog(self, df):
        df["lumdist_neg_err"] = df.d - df.d_lo68
        df["lumdist_pos_err"] = df.d_hi68 - df.d

        self.colmap["lumdist_neg_err"] = "lumdist_neg_err"
        self.colmap["lumdist_pos_err"] = "lumdist_pos_err"

        df["z_type"] = df.apply(
            lambda row : "z ind." if row.dmethod == "N" else "spec-z",
            axis=1
        )
        
        df["z_type"] = df.apply(
            lambda row : "z ind." if row.dmethod == "N" else "spec-z",
            axis=1
        )
        
        df = self._standardize_df(df)
        
        return df
    
class LsDr10(StaticCatalog):
    catalog_model = LsDr10Q3C
    dec_colname = "declination"
    
    def __init__(self):
        # flux_r is in nanomaggy
        self.catalog_model.objects = self.catalog_model.objects.filter(
            flux_r__gt=0
        ).annotate(
            default_mag=22.5-2.5*_Log10('flux_r')
        )

        self.colmap = {
            "lid":"trove_uniq",
            "objid":"name",
            "ra":"ra",
            "declination":"dec",
            "default_mag":"default_mag",
            "z_phot_mean":"z",
            "z_phot_std":"z_err",
        }

        # then, of course, init the super class
        super().__init__()

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
        return df

    def query(self, ra, dec, radius=RADIUS_ARCSEC):
        query_set = super().query(ra, dec, radius)
        return query_set.exclude(
            default_mag__lt = 18,
            mtype = "PSF" 
        ) # exclude PSF magnitudes that are likely point sources
        
class Milliquas(StaticCatalog):
    catalog_model = MilliquasQ3C

    def __init__(self):
        """Override the StaticCatalog init because Milliquas doesn't come with a
        redshift error column, so we need to make some assumptions
        """
        
        num_decimal = Func(
            Func(
                Cast(
                    F('z'),
                    CharField()
                ), # split_part only works with CharField, need to cast first
                Value('.'),
                Value(2),
                function='split_part',
                output_field=CharField()
            ), # this gets just the decimals of the z field as a string
            function="length",
            output_field=IntegerField()
        ) # then this counts the number of decimal places
        
        self.catalog_model.objects = self.catalog_model.objects.annotate(
            num_decimal = num_decimal # this gives a temp row with the number of decimals
        ).annotate(
            z_err=Case(
                When(num_decimal__lte = 1, then=F("z")*0.1),
                When(num_decimal = 2, then=F("z")*0.01),
                default=Value(1e-3)
            ), # this computes the z_err based on the assumptions outlined in the docs for this catalog
            z_type=Case(
                When(num_decimal__lte = 2, then=Value("photo-z")),
                default=Value("spec-z")
            )
        )

        # now that we have these annotations, we can define the colmap
        self.colmap = {
            "name":"name",
            "ra":"ra",
            "dec":"dec",
            "z":"z",
            "z_err":"z_err",
            "rmag":"default_mag" # mag col to use for pcc
        }
        
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
        return df
        
class Ps1(StaticCatalog):
    catalog_model = Ps1Q3C
    colmap = {
        "pid":"trove_uniq",
        "objname":"name",
        "ra":"ra",
        "dec":"dec",
        "z_phot":"z",
        "z_err":"z_err",
        "rmeanpsfmag":"default_mag" # mag col to use for pcc
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
        return df

class Ps1Galaxy(Ps1):

    def query(self, ra, dec, radius=RADIUS_ARCSEC):
        query_set = super().query(ra, dec, radius)
        return query_set.filter(
            ps_score__lte = PS1_POINT_SOURCE_THRESHOLD
        )

class Ps1PointSource(Ps1):

    def query(self, ra, dec, radius=RADIUS_ARCSEC):
        query_set = super().query(ra, dec, radius)
        return query_set.filter(
            ps_score__gt = PS1_POINT_SOURCE_THRESHOLD,
            prob_galaxy__lt = 0.7
        )
    
class RomaBzcat(StaticCatalog):
    catalog_model = RomaBzcatQ3C
    
class Sdss12Photoz(StaticCatalog):
    catalog_model = Sdss12PhotozQ3C
    colmap = {
        "sid":"trove_uniq",
        "sdssid":"name",
        "ra":"ra",
        "dec":"dec",
        "zph":"z",
        "e_zph":"z_err",
        "rmag":"default_mag"
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
        return df
