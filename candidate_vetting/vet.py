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
from tom_targets.models import TargetExtra
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

# After we order the dataframe by the Pcc score, remove any host matches with a greater
# Pcc score than this
PCC_THRESHOLD = 0.1

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

        non_normalized = np.array(minus_dist.tolist()+plus_dist.tolist())
        norm_factor = trapezoid(non_normalized) # integrate over the non normalized PDF
        return non_normalized/norm_factor
        
def _localization_from_name(nonlocalized_event_name, max_time=Time.now()):
    """Find the most recenet LocalizationEvent object from the nonlocalized event name
    """
    # first find the localization to use
    localization_queryset = NonLocalizedEvent.objects.filter(
        event_id=nonlocalized_event_name
    )[0]

    all_localizations = EventLocalization.objects.filter(
        nonlocalizedevent_id=localization_queryset.id
    )

    all_localizations_sorted = sorted(all_localizations, key=lambda x : x.date)

    # now choose the most recent localization
    localization = all_localizations_sorted[0]
    if len(all_localizations_sorted) > 1:
        for loc in all_localizations_sorted[1:]:
            curr_loc_time = Time(localization.date, format="datetime")
            test_loc_time = Time(loc.date, format="datetime")
            if test_loc_time > curr_loc_time and test_loc_time <= max_time:
                localization = loc

    return localization

def _distance_at_healpix(nonlocalized_event_name, target_id, max_time=Time.now()):
    """Computes the GW distance at the target_id healpix location"""

    localization = _localization_from_name(nonlocalized_event_name, max_time=max_time)
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

    return dist, dist_err

def update_score_factor(event_candidate, key, value):
    ScoreFactor.objects.update_or_create(
        event_candidate = event_candidate,
        key = key,
        defaults = dict(value = value)
    )

def update_event_candidate_score(event_candidate, score):
    event_candidate.priority = score
    event_candidate.save()

def _save_host_galaxy_df(df, target):

    # first delete the host galaxy key for this target if it already exists
    if TargetExtra.objects.filter(target_id=target.id, key="Host Galaxies").exists():
        TargetExtra.objects.filter(target_id=target.id, key="Host Galaxies").delete()
    
    col_map = {
        "name":"ID",
        "pcc":"PCC",
        "offset":"Offset",
        "ra":"RA",
        "dec": "Dec",
        "lumdist":"Dist",
        "lumdist_err":"DistErr",
        "z":"z",
        "z_err":"zErr",
        "default_mag":"Mags",
        "catalog":"Source"
    }
    newdf = df[
        [
            "name",
            "pcc",
            "offset",
            "ra",
            "dec",
            "lumdist",
            "z",
            "default_mag",
            "catalog"
        ]
    ]
    newdf["z_err"] = [
        [neg, pos] if neg != pos # errors are asymmetric
        else neg # errors are not assymetric
        for neg, pos in zip(df.z_neg_err, df.z_pos_err)
    ]
    newdf["lumdist_err"] = [
        [neg, pos] if neg != pos # errors are asymmetric
        else neg # errors are not assymetric
        for neg, pos in zip(df.lumdist_neg_err, df.lumdist_pos_err)
    ]
    newdf = newdf.rename(columns=col_map)
    TargetExtra.objects.update_or_create(
        target=target,
        key="Host Galaxies",
        value=newdf.to_json(orient="records")
    )
    
def skymap_association(
        nonlocalized_event_name:str,
        target_id:int,
        max_time = Time.now(),
        prob:float=0.95
) -> float:

    # grab the EventLocalization object for nonlocalized_event_name
    localization = _localization_from_name(nonlocalized_event_name, max_time=max_time)
    print(f"Localization Used: {localization} ({localization.date}; {max_time})")

    # find the healpix where this target is located
    target_hpx_subq = sa.select(
        SaTarget.healpix
    ).filter(
        SaTarget.basetarget_ptr_id == target_id
    ).lateral()
    
    # find the probdensity at the tile of the target_id
    # and for this localization id
    probdensity_subq = sa.select(
        sa.func.min(SaSkymapTile.probdensity).label("min_probdensity")
    ).filter(
        SaSkymapTile.tile.contains(target_hpx_subq.c.healpix),
        SaSkymapTile.localization_id == localization.id
    )
    
    # then we can sum from that probability density to the maximum
    cumprob_query = sa.select(
        sa.func.sum(
            SaSkymapTile.probdensity * SaSkymapTile.tile.area
        )
    ).filter(
        SaSkymapTile.probdensity >= probdensity_subq.c.min_probdensity,
        SaSkymapTile.localization_id == localization.id
    )

    # finally we can execute this cumprob_query and return 1 - the result
    with Session(sa_engine) as session:
        cumprob = session.execute(cumprob_query).fetchall()

    return 1 - cumprob[0][0]
        
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
        
def host_association(target_id:int, radius=50, pcc_threshold=PCC_THRESHOLD):
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
        query_set = cat.query(ra, dec, radius=radius)
            
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
        if "z" in df:
            # we only need to do this if the redshift is in the dataframe
            # if it isn't then that's fine because it means the catalog we are using
            # had a derived distance in it already!
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
    df["offset"] = seps
    df["pcc"] = pcc(df["default_mag"], seps)

    # TODO: We will need to put some deduplication code for the galaxy dataframe
    #       here at some point. For now it seems to work without it though!

    # filter out anything above PCC_THRESHOLD
    # Or, alternatively, only keep the best matching galaxy (if all of the galaxies
    # have a Pcc greater than PCC_THRESHOLD)
    pcc_threshold = max(pcc_threshold, df.pcc.min())
    
    # Finally, filter out anything <= pcc_threshold and sort inversely by pcc
    ret_df = df[df.pcc <= pcc_threshold].sort_values("pcc", ascending=True)
    
    end = time.time()
    print(ret_df)
    print(f"Queries finished in {end-start}s")

    # save the host galaxy dataframe to the TargetExtra "Host Galaxies" keyword
    _save_host_galaxy_df(ret_df, target)
    
    return ret_df

def host_distance_match(
        host_df:pd.DataFrame,
        target_id:int,
        nonlocalized_event_name:str,
        max_time:Time=Time.now()
):

    # find the distance at the healpix
    dist, dist_err = _distance_at_healpix(nonlocalized_event_name, target_id, max_time=max_time)
        
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
    
    # finally, compute the Bhattacharyya coefficient for the overlap of these
    # two distributions. https://en.wikipedia.org/wiki/Bhattacharyya_distance
    # This coefficient is non-parametric which is good for our Asymmetric Gaussian
    # Original paper: http://www.jstor.org/stable/25047806
    host_df["dist_norm_joint_prob"] = trapezoid(np.sqrt(joint_prob), axis=1)
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
