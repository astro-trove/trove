"""
This is an abstract base class for catalogs
"""
import os
from abc import ABC, abstractmethod

from django.db import models

from .util import RADIUS_ARCSEC, cone_search_q3c, pcc_q3c 

# database connection constants
DB_HOST = os.getenv('POSTGRES_HOST', 'localhost')
DB_NAME = os.getenv('POSTGRES_DB', 'sassy')
DB_PASS = os.getenv('POSTGRES_PASSWORD', None)
DB_PORT = os.getenv('POSTGRES_PORT', 5432)
DB_USER = os.getenv('POSTGRES_USER', 'sassy')

DB_CONNECT = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

class Catalog(ABC):

    def __init__(
            self,
            name:str,
            verbose:bool=False
    ):
        """A catalog object for querying

        Parameters
        ----------
        name : str
            The name of the catalog
        verbose : bool, default=True
            If the class should be verbose and print a bunch of stuff (for debug)
        """
        
        self.name = name
        self._verbose = verbose

    @classmethod
    def __subclasshook__(cls, C):
        if cls is Catalog:
            return any("query" in B.__dict__ for B in C.__mro__)
        return NotImplemented
    
    @abstractmethod
    def query():
        """The method that actually does the querying

        This is an abstract method and must be implemented to construct the class
        """
        return NotImplemented
    
class StaticCatalog(Catalog):

    ra_colname = None
    dec_colname = None
    catalog_model = None
    
    def __init__(
            self,
            verbose:bool=False
    ):
        """A catalog object for querying static astronomy catalogs (like galaxy
        catalogs)

        Parameters
        ----------
        name : str
            The name of the catalog
        db_connect : str, optional
            The connection url for the database, uses global variables
        verbose : bool, default=True
            If the class should be verbose and print a bunch of stuff (for debug)
        """

        self.catalog_type = "static"

        self.colnames = {
            "name",
            "ra",
            "dec",
            "z",
            "z_err",
            "z_neg_err",
            "z_pos_err",
            "lumdist",
            "lumdist_err",
            "lumdist_neg_err",
            "lumdist_pos_err",
            "z_type",
            "default_mag"
        }
        
        super().__init__(self.__class__.__name__, verbose=verbose)        

    def __init_subclass__(cls, *args, **kwargs):
        if not getattr(cls, 'ra_colname'):
            # then default to ra
            cls.ra_colname = "ra"
        if not getattr(cls, 'dec_colname'):
            # then default to dec
            cls.dec_colname = "dec"
        if not getattr(cls, "catalog_model") and not isinstance(cls.catalog_model, models.Model):
            raise TypeError(f"Can't instantiate abstract class {cls.__name__} without catalog_model attribute defined")

        return super().__init_subclass__(*args, **kwargs)

    def query(self, ra, dec, radius=RADIUS_ARCSEC):
        """Do a cone search query on this catalog 
        """
        return cone_search_q3c(
            self.catalog_model.objects.all(),
            ra,
            dec,
            radius=radius,
            ra_colname=self.ra_colname,
            dec_colname=self.dec_colname
        )

    def _pcc_filter(self, ra, dec, pcc_max=0.5):
        return pcc_q3c(
            self.catalog_model.objects.all(),
            ra,
            dec,
            pcc_max,
            self.mag_colname,
            ra_colname=self.ra_colname,
            dec_colname=self.dec_colname
        )
        
    def _standardize_df(self, df):
        if not getattr(self, "colmap"):
            raise TypeError("Missing the colmap, can't standardize dataset!")
        df = df.rename(columns=self.colmap)
        return df[list(self.colnames & set(df.columns))]
    
class PhotCatalog(Catalog):
    def __init__(
            self,
            name:str,
            nthreads:int=2,
            verbose:bool=False
    ):
        """A catalog object for querying photometry catalogs (like ZTF and ATLAS)

        Parameters
        ----------
        name : str
            The name of the catalog
        nthreads : int
            The number of threads to execute the query with
        verbose : bool, default=True
            If the class should be verbose and print a bunch of stuff (for debug)
        """

        self.catalog_type = "photometry"
        self.nthreads = nthreads
        super().__init__(name, verbose=verbose)

