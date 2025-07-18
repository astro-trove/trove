"""
Vetting code for non-localized events with transients
"""
import time

import numpy as np
import pandas as pd
from scipy.stats import norm, rv_continuous
from scipy.integrate import trapezoid

from astropy.coordinates import SkyCoord
from astropy import units as u
from astropy.time import Time

import sqlalchemy as sa
from sqlalchemy.orm import Session

from trove_targets.models import Target
from .models import ScoreFactor
from custom_code.healpix_utils import SaTarget
from tom_nonlocalizedevents.models import (
    EventCandidate,
    EventLocalization,
    SkymapTile,
    NonLocalizedEvent
)
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
    Ps1Galaxy,
    Sdss12Photoz,
    AsassnVariableStar,
    Gaiadr3Variable,
    Ps1PointSource
)

# After we order the dataframe by the Pcc score, take this number of hosts from the top
N_TOP_PCC = 50

class AsymmetricGaussian(rv_continuous):
    """
    Custom Asymmetric Gaussian distribution for uneven uncertainties
    """
    def _pdf(self, x, mean, unc_minus, unc_plus):        
        # normalization factor
        # from https://forum.pyro.ai/t/simplest-way-to-generate-an-assymetric-gaussian/5736
        norm = np.sqrt(2)/(np.sqrt(np.pi) * (unc_minus + unc_plus))

        # piecewise return a Gaussian depending on the side of the mean you are on
        where_minus = np.where(x < mean)[0]
        where_plus = np.where(x >= mean)[0]

        minus_dist = np.exp(
            -((x[where_minus] - mean[where_minus]) / unc_minus[where_minus])**2 / 2
        ) # Left side Gaussian-like
        plus_dist = np.exp(
            -((x[where_plus] - mean[where_plus]) / unc_plus[where_plus])**2 / 2
        ) # Right side Gaussian-like

        return minus_dist.tolist()+plus_dist.tolist()

def _localization_from_name(nonlocalized_event_name):
    """Find the most recenet LocalizationEvent object from the nonlocalized event name
    """
    # first find the localization to use
    localization_queryset = NonLocalizedEvent.objects.filter(
        event_id=nonlocalized_event_name
    )[0]

    all_localizations = EventLocalization.objects.filter(
        nonlocalizedevent_id=localization_queryset.id
    )

    # now choose the most recent localization
    localization = all_localizations[0]
    for loc in all_localizations:
        curr_loc_time = Time(localization.date, format="datetime")
        test_loc_time = Time(loc.date, format="datetime")
        if test_loc_time > curr_loc_time:
            localization = loc

    return localization

def update_score_factor(event_candidate, key, value):
    ScoreFactor.objects.update_or_create(
        event_candidate = event_candidate,
        key = key,
        value = value
    )

def update_event_candidate_score(event_candidate, score):
    event_candidate.priority = score
    
def skymap_association(
        nonlocalized_event_name:str,
        target_id:int,
        prob:float=0.95
) -> float:

    # grab the EventLocalization object for nonlocalized_event_name
    localization = _localization_from_name(nonlocalized_event_name)
    
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
        cum_prob
    ).filter(
        SaTarget.basetarget_ptr_id == target_id,
        SaSkymapTile.localization_id == localization.id,
        SaSkymapTile.tile.contains(SaTarget.healpix),
        SaSkymapTile.probdensity >= min_probdensity
    )

    # execute the query
    with Session(sa_engine) as session:
        skymap_score = session.execute(
            query
        ).fetchall()

    if len(skymap_score) == 0:
        return 0

    # need [0][0] because it is a list of tuples
    return 1 - float(skymap_score[0][0])
        
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
        
def host_association(target_id:int, radius=5*60, nkeep=N_TOP_PCC):
    """
    Find all of the potential hosts associated with this target
    """
    catalogs = (
        DesiSpec,
        GladePlus,
        Gwgc,
        Hecate,
        LsDr10,
        Ps1Galaxy,
        Sdss12Photoz
    )

    target = Target.objects.filter(id=target_id)[0]
    ra, dec = target.ra, target.dec
    coord = SkyCoord(ra, dec, unit="deg")
        
    start = time.time()
    res = []
    for catalog in catalogs:
        cat = catalog()
        query_set = cat.query(ra, dec, radius)

        # if no queries are returned we can skip this catalog
        if query_set.count() == 0: continue

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
            subset=["default_mag", "ra", "dec", "lumdist"]
        ) # drop rows without the information we need
                
        # now save the cleaned dataset
        df["catalog"] = cat.__class__.__name__
        res.append(df)
        
    df = pd.concat(res).reset_index(drop=True)

    # calculate Pcc
    catalog_coord = SkyCoord(df.ra, df.dec, unit="deg")
    seps = coord.separation(catalog_coord).arcsec
    df["pcc"] = pcc(df["default_mag"], seps)
    
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
    df = df.sort_values("pcc", ascending=True)[:nkeep]
    
    end = time.time()
    print(df)
    print(f"Queries finished in {end-start}s")
    
    return df

def host_distance_match(
        host_df:pd.DataFrame,
        target_id:int,
        nonlocalized_event_name:str
):

    localization = _localization_from_name(nonlocalized_event_name)
    # find the distance at the healpix
    query = sa.select(
        SaSkymapTile.distance_mean,
        SaSkymapTile.distance_std
    ).filter(
        SaTarget.basetarget_ptr_id == target_id,
        SaSkymapTile.localization_id == localization.id,
        SaSkymapTile.tile.contains(SaTarget.healpix)
    )

    # execute the query
    with Session(sa_engine) as session:
        dist, dist_err = session.execute(
            query
        ).fetchall()[0]

    # now crossmatch this distance to the host galaxy dataframe
    _lumdist = np.linspace(0, 10_000, 10_000)
    test_pdf = norm.pdf(_lumdist, loc=dist, scale=dist_err)
    host_pdfs = np.array([ 
        AsymmetricGaussian().pdf(
            _lumdist,
            mean=row.lumdist,
            unc_minus = row.lumdist_neg_err,
            unc_plus = row.lumdist_pos_err
        ) for _,row in host_df.iterrows() 
    ])
    joint_prob = host_pdfs*test_pdf

    host_df["dist_norm_joint_prob"] = trapezoid(joint_prob, axis=1)
    return host_df

def point_source_association(target_id:int, radius:float=2):

    target = Target.objects.get(id=target_id)
    ra, dec = target.ra, target.dec
    
    point_source_catalogs = [
        AsassnVariableStar,
        Gaiadr3Variable,
        Ps1PointSource
    ]

    for catalog in point_source_catalogs:
        cat = catalog()
        query_set = cat.query(ra, dec, radius)

        # if no matches returned, good! We can check another PS catalog
        if query_set.count() == 0: continue

        # otherwise we need to return a score of 0 for this candidate because
        # it corresponds to a point source
        return 0

    return 1
