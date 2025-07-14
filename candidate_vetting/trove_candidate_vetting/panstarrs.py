"""
Query Panstarrs 
"""
from astropy.coordinates import SkyCoord

from sassy_q3c_models.ps1_q3c_orm import Ps1Q3cRecord
from sassy_q3c_models.ps1_q3c_orm_filters import ps1_q3c_orm_filters

from .catalog import StaticCatalog, PhotCatalog

class Panstarrs_Point_Source(StaticCatalog):
    """
    Query PS1 point source catalog from SASSY
    """

    def query(
            ra:float,
            dec:float
            radius:float=RADIUS_ARCSEC
    ):
        f"""Query Panstarrs point source catalog for matches to kilonova candidates

        {_QUERY_METHOD_DOCSTRING}
        """
        
        # set up query
        try:
            query = self.session.query(Ps1Q3cRecord)
            query = ps1_q3c_orm_filters(query, {'cone': f'{_e[0]},{_e[1]},{_radius}',
                                                'ps_score__gte': PS1_POINT_SOURCE_THRESHOLD})
        except Exception as _e3:
            if self._verbose:
                print(f"{_e3}")
                print(f"Failed to execute query for RA, Dec = ({_e[0]}, {_e[1]}), index={_i}")
            continue

        # execute the query
        if query.count() == 0:
            star = 'None'
            starprob = 0.0
            staroffset = -99.0

        elif query.count() > 1:
            star = 'Multiple matches'
            starprob = 0.0
            staroffset = -99.0

        else:  # exactly one match
            match += 1
            if self._verbose:
                print(f'>>> PS1 source MATCH at RA, Dec = ({_e[0]},{_e[1]}), index={_i}!')
            _x = query.one().serialized()
            star.append(_x['pid'])
            starprob.append(_x['ps_score'])
            ps1 = SkyCoord(_x['ra'] * u.deg, _x['dec'] * u.deg)
            cand = SkyCoord(_e[0] * u.deg, _e[1] * u.deg)
            staroffset.append(cand.separation(ps1).arcsec)

        _end = time.time()

        if self._verbose:
            print(f"Found {match} variable stars in {len(coords)} candidates")
            
        return starprob, star, staroffset

class Panstarrs_STRM(StaticCatalog):
    """Query the Panstarrs STRM photo-z catalog for galaxy matches
    """

    def query(
            ra:list[float],
            dec:list[float]
            radius:float=RADIUS_ARCSEC
    ):
        f"""Query Panstarrs point source catalog for matches to kilonova candidates

        {_QUERY_METHOD_DOCSTRING}
        """

        _coords, _radius = self._package_coords(ra, dec, radius)

        gal_offset = []; mag = []; filt = []; z = []; z_err = []; gal_ra = [];
        gal_dec = []; source = []; name = []; m=0

        try:
            query = self.session.query(Ps1Q3cRecord)
            query = ps1_q3c_orm_filters(query, {'cone': f'{ra},{dec},{_radius}', 'ps_score__lte': PS1_POINT_SOURCE_THRESHOLD})
        except Exception as _e3:
            if self._verbose:
                print(f"{_e3}")
                print(f"Failed to execute query for RA, Dec = ({ra},{dec})")

        if len(query.all()) > 0:
            m+=1
            for _x in Ps1Q3cRecord.serialize_list(query.all()):

                #### DO NOT HAVE MAGNITUDE YET
                if _x['rmeanpsfmag'] is not None and _x['rmeanpsfmag'] != -999.:
                    if np.isfinite(_x['z_phot']) and _x['z_phot'] != -999.:
                        z.append(_x['z_phot'])
                        z_err.append(_x['z_err'])
                    else:
                        continue
                    mag.append(_x['rmeanpsfmag'])
                    filt.append('r')
                    gal = SkyCoord(_x['ra']*u.deg, _x['dec']*u.deg)
                    cand = SkyCoord(ra*u.deg, dec*u.deg)
                    gal_offset.append(cand.separation(gal).arcsec)
                    gal_ra.append(_x['ra'])
                    gal_dec.append(_x['dec'])
                    source.append('PS1_STRM')
                    name.append(_x['psps_objid'])

        return m, gal_ra, gal_dec, gal_offset, mag, filt, z, z_err, source, name
