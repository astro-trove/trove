"""
Vetting code for non-localized events with transients
"""
import time

import numpy as np
import pandas as pd
from astropy.coordinates import SkyCoord
from astropy import units as u

from trove_targets.models import Target
from tom_nonlocalizedevents.models import EventCandidate, EventLocalization, SkymapTile
from tom_nonlocalizedevents.healpix_utils import (
    sa_engine,
    SaSkymapTile,
    uniq_to_bigintrange,
    update_all_credible_region_percents_for_candidates
)
from candidate_vetting.public_catalogs.static_catalogs import (
    DesiSpec,
    GladePlus,
    Gwgc,
    Hecate,
    LsDr10,
    Ps1,
    Sdss12Photoz
)

def skymap_association(localization:EventLocalization, target:Target, prob:float=0.95):

    with Session(sa_engine) as session:

        # calculate the cumalative probability density for the tiles
        # SHOULDN'T THIS BE STORED IN THE SaSkymapTile OBJECT???
        cum_prob = sa.func.sum(
            SaSkymapTile.probdensity * SaSkymapTile.tile.area
        ).over(
            order_by=SaSkymapTile.probdensity.desc()
        ).label(
            'cum_prob'
        )

        # find the localization region in the SaSkymapTile
        subquery = sa.select(
            SaSkymapTile.probdensity,
            cum_prob
        ).filter(
            SaSkymapTile.localization_id == localization.id
        ).subquery()

        # Filter on the skymap and take all of the tiles that are within the
        # cumulative probability density contour passed in as "prob"
        min_probdensity = sa.select(
            sa.func.min(subquery.columns.probdensity)
        ).filter(
            subquery.columns.cum_prob <= prob
        ).scalar_subquery()

        # write the query for the Target table
        query = sa.select(
            Target.id
        ).filter(
            Target.id.in_(target_ids),
            SaSkymapTile.localization_id == localization.id,
            SaSkymapTile.tile.contains(sa.cast(Target.healpix, sa.BigInteger)),
            SaSkymapTile.probdensity >= min_probdensity
        )

        # execute the query
        results = session.query(query)

def pcc(r:list[float], m:list[float]):
    """
    Probability of chance coincidence calculation (Bloom et al. 2002)

    PARAMETERS
    ----------
    r : transient-galaxy offsets, array of floats
        arcseconds
    m : magnitudes of galaxies, array of floats

    RETURNS
    -------
    Pcc values : array of floats [0,1]
    """
    sigma = (1/(0.33*np.log(10)))*10**(0.33*(m-24)-2.44)
    prob = 1-np.exp(-(np.pi*(r**2)*sigma))

    return prob
        
def host_association(target:Target, radius=60):
    """
    Find all of the potential hosts associated with this target
    """
    catalogs = (
        DesiSpec,
        GladePlus,
        Gwgc,
        Hecate,
        LsDr10,
        Ps1,
        Sdss12Photoz
    )

    ra, dec = target.ra, target.dec

    start = time.time()
    res = []
    for catalog in catalogs:
        cat = catalog()
        query_set = cat.query(ra, dec, radius)

        # if no queries are returned we can skip this catalog
        if len(query_set) == 0: continue

        # convert to a dataframe and standardize the column names
        df = pd.DataFrame(
            list(
                query_set.values()
            )
        ) 
        df = cat.to_standardized_catalog(df)

        # some extra cleaning before continuing
        df = df[df.z > 0.02] # otherwise it probably isn't a real redshift
        df = df.dropna(
            subset=["default_mag", "ra", "dec", "z", "lumdist"]
        ) # drop rows without the information we need
        
        # calculate Pcc
        coord = SkyCoord(ra, dec, unit="deg")
        catalog_coord = SkyCoord(df.ra, df.dec, unit="deg")
        seps = coord.separation(catalog_coord).arcsec
        df["pcc"] = pcc(df["default_mag"], seps)
        
        # now save the cleaned dataset
        df["catalog"] = cat.__class__.__name__
        res.append(df)
        
    df = pd.concat(res).reset_index(drop=True)

    # now find duplicated galaxies within _radius
    _radius = 2 # arcseconds
    coords = SkyCoord(ra=df.ra.values*u.deg, dec=df.dec.values*u.deg)
    idx1, idx2, _, _ = coords.search_around_sky(coords, _radius*u.arcsec)

    # remove duplicated indices
    # (since we are matching the same catalog against itself)
    idx2 = idx2[idx1 != idx2]

    # find the unique set of the indexes
    close_indices = set(idx1.tolist() + idx2.tolist())

    # Then drop the latter match
    df = df.drop(index=np.unique(idx2)).reset_index(drop=True)

    # Finally, sort inversely by pcc
    df = df.sort_values("pcc", ascending=True)
    
    end = time.time()
    print(df)
    print(f"Queries finished in {end-start}s")
    
    return df
