"""
Code to query dynamically updating photometry catalogs
"""

from .catalog import PhotCatalog
from .util import _QUERY_METHOD_DOCSTRING, RADIUS_ARCSEC

class ASASSN_SkyPatrol(PhotCatalog):
    """ASASSN Forced photometry server
    """
    
    def query(
            self,
            ra:float,
            dec:float,
            radius:float=RADIUS_ARCSEC
    ):
        f"""Query the ASASSN SkyPatrol forced photometry service

        {_QUERY_METHOD_DOCSTRING}
        """
        client = SkyPatrolClient()
        light_curve = client.cone_search(
            ra_deg=ra,
            dec_deg=dec,
            radius=radius,
            units="arcsec",
            download=True,
            threads=nthreads,
        )
        
        return light_curve.data

class ZTF_Forced_Phot(PhotCatalog):
    """ZTF Forced photometry server
    """

    def query(
            self,
            ra:float,
            dec:float,
            radius:float=RADIUS_ARCSEC
    ):
        f"""Query the ZTF forced photometry service

        {_QUERY_METHOD_DOCSTRING}
        """
        pass

class ATLAS_Forced_Phot(PhotCatalog):
    """ATLAS Forced photometry server
    """

    def query(
            self,
            ra:float,
            dec:float,
            radius:float = RADIUS_ARCSEC
    )
        
