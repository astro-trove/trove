"""
This is an abstract base class for catalogs
"""
from abc import ABC, abstractmethod

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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

    def _package_coords(ra, dec, radius_as):
        """Package lists of ra and dec into a tuple and convert the radius to degrees

        Parameters
        ----------
        ra : list[float]
            A list of RA floats in degrees
        dec : list[float]
            A list of dec floats in degrees
        radius_as : float
            The search radius in arcseconds

        Returns
        -------
        tuple, float
            A tuple of RA, Decs and the radius in degrees
        """
        return tuple(zip(ra, dec)), radius_as/3600

    
class StaticCatalog(Catalog):
    def __init__(
            self,
            name:str,
            db_connect:str=DB_CONNECT,
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

        # connect to database
        try:
            self.engine = create_engine(db_connect)
            get_session = sessionmaker(bind=engine)
            self.session = get_session()
        except Exception as _e2:
            if self._verbose:
                print(f"{_e2}")
            raise Exception(f"Failed to connect to database")

        super().__init__(name, verbose=verbose)

class PhotCatalog(Catalog):
    def __init__(
            self,
            name:str,
            nthreads:int=2
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
        super().__init__(name, verbose=verbose)

