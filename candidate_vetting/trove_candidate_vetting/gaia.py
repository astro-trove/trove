"""
Query Gaia for point sources
"""
from astropy.coordinates import SkyCoord

from .catalog import StaticCatalog

from sassy_q3c_models.gaiadr3variable_q3c_orm import GaiaDR3VariableQ3cRecord
from sassy_q3c_models.gaiadr3variable_q3c_orm_filters import gaiadr3variable_q3c_orm_filters

class Gaia(StaticCatalog):
    """Class for Gaia queries
    """

    def query(
            ra:list[float],
            dec:list[float]
            radius:float=RADIUS_ARCSEC
    ):
        f"""Query Gaia DR3 for matches to kilonova candidates

        {_QUERY_METHOD_DOCSTRING}
        """

        _coords, _radius = self._package_coords(ra, dec, radius)
        
        star = []
        staroffset = []
        starclass = []
        match=0

        for _i, _e in enumerate(_coords):

            # set up query
            try:
                query = self.session.query(GaiaDR3VariableQ3cRecord)
                query = gaiadr3variable_q3c_orm_filters(query, {'cone': f'{_e[0]},{_e[1]},{_radius}'})
            except Exception as _e3:
                if self._verbose:
                    print(f"{_e3}")
                    print(f"Failed to execute query for RA, Dec = ({_e[0]}, {_e[1]}), index={_i}")
                continue
            # execute the query
            if len(query.all()) == 0:
                star.append('None')
                staroffset.append(-99.0)
                starclass.append('None')
            else:
                match+=1
                for _x in GaiaDR3VariableQ3cRecord.serialize_list(query.all()):
                    if self._verbose:
                        print(f'>>> Gaia Star MATCH at RA, Dec = ({_e[0]},{_e[1]}), index={_i}!')

                    star.append(_x['source_id'])
                    starclass.append(_x['classification'])

                    gaia = SkyCoord(_x['ra']*u.deg, _x['dec']*u.deg)
                    cand = SkyCoord(_e[0]*u.deg, _e[1]*u.deg)
                    staroffset.append(cand.separation(gaia).arcsec)

        _end = time.time()

        if self._verbose:
            print(f"Found {match} variable stars in {len(coords)} candidates")

        return star, staroffset, starclass
