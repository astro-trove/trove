"""
Services to query ASASSN's SkyPatrol and static variable star catalog
"""

from astropy.coordinates import SkyCoord

from sassy_q3c_models.asassn_q3c_orm import AsAssnQ3cRecord
from sassy_q3c_models.asassn_q3c_orm_filters import asassn_q3c_orm_filters

from pyasassn.client import SkyPatrolClient

from .catalog import StaticCatalog, PhotCatalog
from .util import RADIUS_ARCSEC, _QUERY_METHOD_DOCSTRING

class ASASSN_Variable_Stars(StaticCatalog):
    """ASASSN variable star catalog
    """
    
    def query(
            ra:list[float],
            dec:list[float],
            radius:float=RADIUS_ARCSEC
    ):
        f"""Query the ASASSN Variable Star catalog (Flesch 2021) for matches to
        kilonova candidates

        {_QUERY_METHOD_DOCSTRING}
        """

        _begin = time.time()

        _coords, _radius = self._package_coords(ra, dec, radius)
        
        star = []
        staroffset = []
        match=0

        for _i, _e in enumerate(_coords):

            # set up query
            try:
                query = self.session.query(AsAssnQ3cRecord)
                query = asassn_q3c_orm_filters(query, {'cone': f'{_e[0]},{_e[1]},{_radius}'})
            except Exception as _e3:
                if self._verbose:
                    print(f"{_e3}")
                    print(f"Failed to execute query for RA, Dec = ({_e[0]}, {_e[1]}), index={_i}")
                continue
            # execute the query
            if len(query.all()) == 0:
                star.append('None')
                staroffset.append(-99.0)
            else:
                match+=1
                for _x in AsAssnQ3cRecord.serialize_list(query.all()):
                    if self._verbose:
                        print(f'>>> ASAS-SN Variable Star MATCH at RA, Dec = ({_e[0]},{_e[1]}), index={_i}!')

                    # add the query dictionary to the qso list and modify the qprob list
                    star.append(_x['asassn_name']) #{**_x, **{'Candidate': names[_i], 'Probability': 1.0, 'Candidate_RA': _e[0], 'Candidate_Dec': _e[1]}})

                    asassn = SkyCoord(_x['ra']*u.deg, _x['dec']*u.deg)
                    cand = SkyCoord(_e[0]*u.deg, _e[1]*u.deg)
                    staroffset.append(cand.separation(asassn).arcsec)

        _end = time.time()

        if self._verbose:
            print(f"Completed ASAS-SN search in {_end-_begin:.3f} sec")
            print(f"Found {match} variable stars in {len(coords)} candidates")

        return star, staroffset

class ASASSN_SkyPatrol(PhotCatalog):
    """ASASSN Forced photometry server
    """
    
    def query(
            ra:list[float],
            dec:list[float],
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
