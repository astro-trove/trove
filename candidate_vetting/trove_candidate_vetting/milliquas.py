"""
Milliquas Catalog class
"""
from astropy.coordinates import SkyCoord

from sassy_q3c_models.milliquas_q3c_orm import MilliQuasQ3cRecord
from sassy_q3c_models.milliquas_q3c_orm_filters import milliquas_q3c_orm_filters

from .catalog import StaticCatalog
from .util import RADIUS_ARCSEC, _QUERY_METHOD_DOCSTRING

class Milliquas(StaticCatalog):

    def query(
            ra:list[float],
            dec:list[float]
            radius:float=RADIUS_ARCSEC
    ):
        f"""Query the Million Quasar Catalog (Flesch 2021) for matches to
        kilonova candidates

        {_QUERY_METHOD_DOCSTRING}
        """

        # set variable(s)
        _begin = time.time()

        _coords, _radius = self._package_coords(ra, dec, radius)
        
        qso = []
        qoffset = []
        match=0
        
        # for all (RA, Dec) tuples, execute cone search and log candidates with qpct > 97(%)
        for _i, _e in enumerate(_coords):

            # set up query
            try:
                query = self.session.query(MilliQuasQ3cRecord)
                query = milliquas_q3c_orm_filters(query, {'cone': f'{_e[0]},{_e[1]},{_radius}', 'q__gte': 97})
            except Exception as _e3:
                if self._verbose:
                    print(f"{_e3}")
                print(f"Failed to execute query for RA, Dec = ({_e[0]}, {_e[1]}), index={_i}")
                continue

            # execute the query
            if len(query.all()) == 0:
                qso.append('None')
                qoffset.append(-99.0)
            else:
                match+=1
                for _x in MilliQuasQ3cRecord.serialize_list(query.all()):
                    print(f'>>> QUASAR MATCH at RA, Dec = ({_e[0]},{_e[1]}), index={_i}!')

                    # add the query dictionary to the qso list and modify the qprob list
                    qso.append(_x['name']) #{**_x, **{'Candidate': names[_i], 'Probability': 1.0, 'Candidate_RA': _e[0], 'Candidate_Dec': _e[1]}})

                    QSO = SkyCoord(_x['ra']*u.deg, _x['dec']*u.deg)
                    cand = SkyCoord(_e[0]*u.deg, _e[1]*u.deg)
                    qoffset.append(cand.separation(QSO).arcsec)

        # done
        _end = time.time()

        # return list of probabilities (although I think you'd be better off returning the qso list!)
        if self._verbose:
            print(f"Completed Milliquas search in {_end-_begin:.3f} sec")
            print(f"Found {match} QSOs in {len(coords)} candidates")

        return qso, qoffset
