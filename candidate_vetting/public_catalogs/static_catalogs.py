"""
Define the static catalogs for querying
"""
from astropy import units as u
import pandas as pd

from django.db.models import Func, Q
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
    Sdss12PhotozQ3C
)

class _Log10(Func):
    function = 'LOG10'
    template = '%(function)s(%(expressions)s)'

class AsassnVariableStar(StaticCatalog):
    catalog_model = AsassnQ3C
    
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
            "targetid":"name",
            "z":"z",
            "zerr":"z_err",
            "target_ra":"ra",
            "target_dec":"dec",
            "default_mag":"default_mag"
        }
        
        super().__init__()

    def to_standardized_catalog(self, df):
        df = self._standardize_df(df)
        df["lumdist"] = cosmo.luminosity_distance(df.z).to(u.Mpc).value 
        df["lumdist_err"] = cosmo.luminosity_distance(df.z_err).to(u.Mpc).value
        df["z_neg_err"] = df.z_err
        df["z_pos_err"] = df.z_err
        df["lumdist_neg_err"] = df.lumdist_err 
        df["lumdist_pos_err"] = df.lumdist_err
        return df
    
        
class FermiLat(StaticCatalog):
    catalog_model = FermiLatQ3C
    
class Gaiadr3Variable(StaticCatalog):
    catalog_model = Gaiadr3VariableQ3C
    
class GladePlus(StaticCatalog):
    catalog_model = GladePlusQ3C
    colmap = {
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
        return df
    
class Hecate(StaticCatalog):
    catalog_model = HecateQ3C
    colmap = {
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
            "objid":"name",
            "ra":"ra",
            "declination":"dec",
            "default_mag":"default_mag",
            "z_phot_mean":"z",
            "z_phot_std":"z_err",
        }

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
        return df

    def query(self, ra, dec, radius=RADIUS_ARCSEC):
        query_set = super().query(ra, dec, radius)
        return query_set.exclude(
            default_mag__lt = 18,
            mtype = "PSF" 
        ) # exclude PSF magnitudes that are likely point sources
        
class Milliquas(StaticCatalog):
    catalog_model = MilliquasQ3C
    
class Ps1(StaticCatalog):
    catalog_model = Ps1Q3C
    colmap = {
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
            ps_score__gt = PS1_POINT_SOURCE_THRESHOLD
        )
    
class RomaBzcat(StaticCatalog):
    catalog_model = RomaBzcatQ3C
    
class Sdss12Photoz(StaticCatalog):
    catalog_model = Sdss12PhotozQ3C
    colmap = {
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
        return df
