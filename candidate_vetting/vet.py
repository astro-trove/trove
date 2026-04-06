"""
Vetting code for non-localized events with transients
"""
import time
import io

import numpy as np
import pandas as pd
from scipy.stats import norm, rv_continuous
from scipy.integrate import trapezoid, quad

from astropy.utils.introspection import minversion
if minversion(np, "2.0.0"):
    np_trapz_fn = np.trapezoid
else:
    np_trapz_fn = np.trapz # np.trapz is deprecated in numpy >2.0.0 

import warnings

from astropy.coordinates import SkyCoord
from astropy import units as u
from astropy.time import Time

import sqlalchemy as sa
from sqlalchemy.orm import Session

from django.conf import settings
cosmo = settings.COSMO

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
    ZtfVarStar,
    Ps1PointSource,
    Milliquas,
    NedLvs,
    TwoMass,
    DesiDr1
)

HOST_DF_COLMAP = {
    "name":"ID",
    "pcc":"PCC",
    "offset":"Offset",
    "ra":"RA",
    "dec": "Dec",
    "lumdist":"Dist",
    "lumdist_err":"DistErr",
    "z":"z",
    "z_err":"zErr",
    "z_type":"z_type",
    "default_mag":"Mags",
    "catalog":"Source"
}
HOST_DF_COLMAP_INVERSE = {v:k for k,v in HOST_DF_COLMAP.items()}

# After we order the dataframe by the Pcc score, remove any host matches with a greater
# Pcc score than this
PCC_THRESHOLD = 0.15 # this is the value used in Rastinejad+2022

# upper / lower bounds on distance for computing normal / asymmetric Gaussian
# distributions
D_LIM_LOWER = 1e-5 # 0.00001 Mpc
D_LIM_UPPER = 1e4  # 10,000 Mpc 

# rank order of the galaxy catalogs for getting the "default" distance to this transient
# this is kinda arbitrary, but generally I consider
# 1) is this a redshift catalog or a galaxy distance catalog? An actual galaxy distance
#    catalog is preferred over a general redshift catalog
# 2) Does this catalog have spec-z's or photo-z's? A spec-z catalog is preferred.
GALAXY_CATALOGS = [
    GladePlus,
    Gwgc,
    Hecate,
    DesiDr1,
    # DesiSpec, # this duplicates with DESI DR1 (which also includes the EDR data)
    NedLvs,
    LsDr10,
    Ps1Galaxy,
    Sdss12Photoz
]

GALAXY_CATALOG_RANKING = {c.__name__:i for i,c in enumerate(GALAXY_CATALOGS)}
    
class AsymmetricGaussian(rv_continuous):
    """
    Custom Asymmetric Gaussian distribution for uneven uncertainties
    """
    def _pdf_unnorm(self, x, mean, unc_minus, unc_plus):
        """**Unnormalized** asymmetric Gaussian PDF"""
        # piecewise return a Gaussian depending on the side of the mean you are on
        where_minus = np.where(x < mean)[0]
        where_plus = np.where(x >= mean)[0]

        minus_dist = np.exp(
            -0.5*((x[where_minus] - mean[where_minus]) / unc_minus[where_minus])**2
        ) # Left side Gaussian-like
        plus_dist = np.exp(
            -0.5*((x[where_plus] - mean[where_plus]) / unc_plus[where_plus])**2
        ) # Right side Gaussian-like

        return np.concatenate((minus_dist, plus_dist))
    
    def _pdf(self, x, mean, unc_minus, unc_plus, integ_a, integ_b):
        """**Normalized** asymmetric Gaussian PDF"""
        # unclear why, but even when floats are passed to this function for 
        # args mean, unc_minus, unc_plus, integ_a, integ_b, they become lists 
        # of the same value repeated len(x) times
        
        # numerically integrate asymmetric Gaussian, for normalization
        integ_x = np.linspace(integ_a[0], integ_b[0], x.shape[0])
        integ = np_trapz_fn(
            y=self._pdf_unnorm(integ_x, mean, unc_minus, unc_plus),
            x=integ_x
        )
        integ_norm = 1 / integ

        # return unnormalized PDF multiplied by normalization factor
        return self._pdf_unnorm(x, mean, unc_minus, unc_plus) * integ_norm
        
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

def delete_score_factor(event_candidate, key):
    """This is basically only used since we are updating various scores
    and may want to delete some, rather than update them, in the process"""
    # first get any score factors that match this event candidate and key
    matches = ScoreFactor.objects.filter(
        event_candidate=event_candidate,
        key = key
    )

    if matches.count():
        matches.delete()

def save_score_to_targetextra(target, key, score):
    """
    Saves the scores that don't change to a TargetExtra object rather than a ScoreFactor
    This is for:
    1. point source score
    2. MPC score
    Since they are independent of the NLE that we are vetting the target against
    """

    # first delete the host galaxy key for this target if it already exists
    te = TargetExtra.objects.filter(target_id=target.id, key=key)
    if te.exists():
        te.delete()

    # then save the new score
    TargetExtra.objects.update_or_create(
        target=target,
        key=key,
        value=score
    )

        
def _save_host_galaxy_df(df, target):

    # first delete the host galaxy key for this target if it already exists
    if TargetExtra.objects.filter(target_id=target.id, key="Host Galaxies").exists():
        TargetExtra.objects.filter(target_id=target.id, key="Host Galaxies").delete()
    
    newdf = df[
        [
            "name",
            "pcc",
            "offset",
            "ra",
            "dec",
            "lumdist",
            "z",
            "z_type",
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
    newdf = newdf.rename(columns=HOST_DF_COLMAP)
    TargetExtra.objects.update_or_create(
        target=target,
        key="Host Galaxies",
        value=newdf.to_json(orient="records")
    )

def _save_associated_agn_df(df, target):

    # first delete the associated AGN key for this target if it already exists
    if TargetExtra.objects.filter(target_id=target.id, key="Associated AGN").exists():
        TargetExtra.objects.filter(target_id=target.id, key="Associated AGN").delete()
    
    col_map = {
        "name":"ID",
        # "pcc":"PCC",
        # "offset":"Offset",
        "ra":"RA",
        "dec": "Dec",
        "lumdist":"Dist",
        "lumdist_err":"DistErr",
        "z":"z",
        "z_err":"zErr",
        # "default_mag":"Mags",
        "catalog":"Source"
    }
    newdf = df[
        [
            "name",
            # "pcc",
            # "offset",
            "ra",
            "dec",
            "lumdist",
            "z",
            # "default_mag",
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
        key="Associated AGN",
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

    target = Target.objects.filter(id=target_id)[0]
    ra, dec = target.ra, target.dec
    coord = SkyCoord(ra, dec, unit="deg")
        
    start = time.time()
    res = []
    for catalog in GALAXY_CATALOGS:
        cat = catalog()
        print(f"Querying {cat}...")
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
            subset=["default_mag", "ra", "dec", "lumdist", "lumdist_err"]
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

    # Finally, filter out anything <= pcc_threshold and sort inversely by pcc
    ret_df = df[df.pcc <= pcc_threshold].sort_values("pcc", ascending=True)
    
    end = time.time()
    print(ret_df)
    print(f"Queries finished in {end-start}s")

    # save the host galaxy dataframe to the TargetExtra "Host Galaxies" keyword
    if not len(ret_df):
        # then we don't need to actually save any host information
        return ret_df
    _save_host_galaxy_df(ret_df, target)
    return ret_df

def _get_nle_distance_pdf(lumdist_array:np.ndarray, nonlocalized_event_name:str, target_id, max_time=Time.now()):
    # find the distance at the healpix
    dist, dist_err = _distance_at_healpix(nonlocalized_event_name, target_id, max_time=max_time)

    # let user know about hard-coded bounds on luminosity distance array
    warnings.warn(f"Using hard-coded D_LIM_LOWER = {D_LIM_LOWER} and "+
                  f"D_LIM_UPPER = {D_LIM_UPPER} to construct log-spaced "+
                  "distance array for calculating distance probability "+
                  "distribution functions")

    test_pdf = norm.pdf(lumdist_array, loc=dist, scale=dist_err)
    return test_pdf

def host_distance_match(
        host_df:pd.DataFrame,
        target_id:int,
        nonlocalized_event_name:str,
        max_time:Time=Time.now()
):
    """
    Compute integrated joint probability (Bhattacharyya coefficient) of 
    putative host galaxies' distance distributions and nonlocalized event 
    distance distribution.

    Parameters
    ----------
    host_df : pd.DataFrame
        Dataframe containing information on host galaxies
    target_id : int
        ID for target
    nonlocalized_event_name : str
        Name for nonlocalized event
    max_time : Time, optional
        Time at which to extract nonlocalized event localization; 
        default is Time.now()

    Returns
    -------
    host_df : pd.DataFrame
        Dataframe containing information on host galaxy, with added integrated 
        joint probability

    """
        
    if not len(host_df):        
        host_df["dist_norm_joint_prob"] = []
        return host_df # continue to return an empty dataframe here, but with the correct columns
    
    # now crossmatch this distance to the host galaxy dataframe
    _lumdist = np.linspace(D_LIM_LOWER, D_LIM_UPPER, int(10*D_LIM_UPPER))

    test_pdf = _get_nle_distance_pdf(
        _lumdist,
        nonlocalized_event_name,
        target_id,
        max_time=max_time
    )
    host_pdfs = np.array([ 
        AsymmetricGaussian().pdf(
            _lumdist,
            mean=row.lumdist,
            unc_minus = row.lumdist_neg_err,
            unc_plus = row.lumdist_pos_err,
            integ_a=1e-9,
            integ_b=_lumdist[-1]
        ) for _,row in host_df.iterrows() 
    ])
    joint_prob = host_pdfs*test_pdf

    # finally, compute the Bhattacharyya coefficient for the overlap of these
    # two distributions. https://en.wikipedia.org/wiki/Bhattacharyya_distance
    # This coefficient is non-parametric which is good for our Asymmetric Gaussian
    # Original paper: http://www.jstor.org/stable/25047806
    host_df["dist_norm_joint_prob"] = trapezoid(
        np.sqrt(joint_prob),
        x=_lumdist,
        axis=1
    )
    return host_df

def get_distance_score(host_df, target_id, nonlocalized_event_name):
    """
    This get's the host score from the input host_df by first prioritizing target specific redshifts,
    then spec-z's, and then photo-z's. It assumes that any potential host within a
    Pcc < PCC_THRESHOLD is equally probable. It also uses the maximum probability galaxy
    to soften the effects of poor distance associations.
    """
    # first check if this target has a measured redshift
    targ = Target.objects.get(id=target_id)
    if targ.redshift is not None and not np.isnan(targ.redshift):
        _lumdist = np.linspace(D_LIM_LOWER, D_LIM_UPPER, int(10*D_LIM_UPPER))
        nle_pdf = _get_nle_distance_pdf(_lumdist,  nonlocalized_event_name, target_id)
        targ_dist = cosmo.luminosity_distance(targ.redshift).to(u.Mpc).value
        targ_dist_err = cosmo.luminosity_distance(1e-3).to(u.Mpc).value
        targ_pdf = norm.pdf(_lumdist, loc=targ_dist, scale=targ_dist_err)
        return trapezoid(
            np.sqrt(targ_pdf*nle_pdf),
            x=_lumdist
        )

    # then use the redshift independent measurements of distances
    ind_distance_hosts = host_df[host_df.z_type == "z ind."]
    if len(ind_distance_hosts):
        return ind_distance_hosts.dist_norm_joint_prob.max()

    # then use the specz hosts
    specz_hosts = host_df[host_df.z_type.str.contains("spec-z")]
    if len(specz_hosts):
        return specz_hosts.dist_norm_joint_prob.max()

    # then if we don't know the spec-z or have an independent distance measure use the photo-z's
    return host_df.dist_norm_joint_prob.max()

def get_eventcandidate_default_distance(target_id:int, nonlocalized_event_name:str):

    # first check if this target has a redshift associated with it
    targ = Target.objects.get(id = target_id)
    if targ.redshift is not None and not np.isnan(targ.redshift):
        targ_dist = cosmo.luminosity_distance(targ.redshift).to(u.Mpc).value
        targ_dist_err = cosmo.luminosity_distance(1e-3).to(u.Mpc).value
        return targ_dist, targ_dist_err

    # then try to get out the host galaxy json file from target extra
    hosts = TargetExtra.objects.filter(target_id = target_id, key='Host Galaxies')
    if not hosts.count():
        return _distance_at_healpix(nonlocalized_event_name, target_id)

    host_df = pd.read_json(
        io.StringIO(
            hosts[0].value
        )
    ) # since we store the host info as a json str in the db
    if not len(host_df):
        return _distance_at_healpix(nonlocalized_event_name, target_id)

    # if we've gotten to this point then the target has host galaxies associated with it!
    # first thing we need to do is assign a rank ordering to the various catalogs,
    # this will help later
    host_df["_rank_order"] = host_df.Source.replace(GALAXY_CATALOG_RANKING)
    host_df = host_df.sort_values(by=["_rank_order", "PCC"])

    # because we already sorted the dataframe by our "preferred" catalogs, we can
    # just always take the distances from the first row and return them
    # so let's start with z independent measures of the distance
    ind_distance_hosts = host_df[host_df.z_type == "z ind."]
    specz_hosts = host_df[host_df.z_type.str.contains("spec-z")]
    if len(ind_distance_hosts):
        to_ret = ind_distance_hosts.iloc[0]
        
    # then spec-z's
    elif len(specz_hosts):
        to_ret = specz_hosts.iloc[0]
        
    # then photo-z's
    else:
        to_ret = host_df.iloc[0]
    
    return to_ret.Dist, to_ret.DistErr

def point_source_association(target_id:int, radius:float=2):

    target = Target.objects.get(id=target_id)
    ra, dec = target.ra, target.dec
    
    point_source_catalogs = [
        AsassnVariableStar,
        Gaiadr3Variable,
        Ps1PointSource,
        ZtfVarStar,

        # this is the 2MASS point source catalog
        # I'm leaving it commented out because we need to test it a bit more before
        # using it!
        #TwoMass 
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

def agn_association_2d(target_id:int, radius:float=2):
    """
    This searches the AGN catalogs for a match for this target
    """
    
    target = Target.objects.get(id=target_id)
    ra, dec = target.ra, target.dec
    
    agn_catalogs = [
        Milliquas
    ] # there is currently only one, but this should help to "future proof" the code

    agn_matches = None
    res = []
    start = time.time()
    for catalog in agn_catalogs:
        cat = catalog()
        query_set = cat.query(ra, dec, radius)

        # no match found here! let's check another catalog!
        if query_set.count() == 0: continue

        if agn_matches is None:
            agn_matches = query_set
        else:
            agn_matches |= query_set # this will perform a SQL UNION on the query sets
        
        # convert to a dataframe and standardize the column names
        df = pd.DataFrame(
            list(
                agn_matches.values()
            )
        ) 
        df = cat.to_standardized_catalog(df)

        # some extra cleaning before continuing
        df = df.dropna(
            subset=["default_mag", "ra", "dec", "lumdist"]
        ) # drop rows without the information we need
                
        # now save the cleaned dataset
        df["catalog"] = cat.__class__.__name__
        res.append(df)
    
    if len(res) > 0: # when no matches, nothing to concatenate
        df = pd.concat(res).reset_index(drop=True)
    else: # return an empty dataframe
        return pd.DataFrame({})

    # put any more cleaning up / filtering here; none for now
    ret_df = df.copy()
    
    end = time.time()
    print(ret_df)
    print(f"Queries finished in {end-start}s")

    # save the host galaxy dataframe to the TargetExtra "Associated AGN" keyword
    _save_associated_agn_df(ret_df, target)
    
    return ret_df

def agn_distance_match(
        agn_df:pd.DataFrame,
        target_id:int,
        nonlocalized_event_name:str,
        max_time:Time=Time.now()
):
    """
    Compute integrated joint probability (Bhattacharyya coefficient) of 
    AGN distance distributions and nonlocalized event distance distribution.

    Parameters
    ----------
    agn_df : pd.DataFrame
        Dataframe containing information on potential associated AGN(s)
    target_id : int
        ID for target
    nonlocalized_event_name : str
        Name for nonlocalized event
    max_time : Time, optional
        Time at which to extract nonlocalized event localization; 
        default is Time.now()

    Returns
    -------
    agn_df : pd.DataFrame
        Dataframe containing information on AGN(s), with added integrated 
        joint probability

    """
    if not len(agn_df):        
        agn_df["dist_norm_joint_prob"] = []
        return agn_df # continue to return an empty dataframe here, but with the correct columns
        
    # now crossmatch this distance to the to the AGNs dataframe
    _lumdist = np.linspace(D_LIM_LOWER, D_LIM_UPPER, int(10*D_LIM_UPPER))

    test_pdf = _get_nle_distance_pdf(
        _lumdist,
        nonlocalized_event_name,
        target_id,
        max_time=max_time
    )
    agn_pdfs = np.array([ 
        AsymmetricGaussian().pdf(
            _lumdist,
            mean=row.lumdist,
            unc_minus = row.lumdist_neg_err,
            unc_plus = row.lumdist_pos_err,
            integ_a=1e-9,
            integ_b=_lumdist[-1]
        ) for _,row in agn_df.iterrows() 
    ])
    joint_prob = agn_pdfs*test_pdf
    
    # finally, compute the Bhattacharyya coefficient for the overlap of these
    # two distributions. https://en.wikipedia.org/wiki/Bhattacharyya_distance
    # This coefficient is non-parametric which is good for our Asymmetric Gaussian
    # Original paper: http://www.jstor.org/stable/25047806
    agn_df["dist_norm_joint_prob"] = trapezoid(
        np.sqrt(joint_prob), 
        x=_lumdist,
        axis=1
    )
    return agn_df
