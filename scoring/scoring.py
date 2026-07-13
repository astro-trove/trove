from scoring.dist_scoring_helpers import AsymmetricGaussian, bc, bc_norm_median_asymmetric, conditional_scoring, consistency_probability, cons_prob_3, hybrid_cons_prob, hybrid_cons_prob_v2, information_metric, resampled_zscore, zscore, hybrid

from .models import ScoreFactor
from .healpix_utils import SaTarget

from candidate_vetting.vet import GALAXY_CATALOGS

import io
import logging

import numpy as np
import pandas as pd
from scipy.stats import norm, rv_continuous
from scipy.integrate import trapezoid

from astropy.utils.introspection import minversion

import warnings

import sqlalchemy as sa
from sqlalchemy.orm import Session
from tom_nonlocalizedevents.healpix_utils import (
    sa_engine,
    SaSkymapTile,
    # uniq_to_bigintrange,
    # update_all_credible_region_percents_for_candidates
)
from tom_nonlocalizedevents.models import NonLocalizedEvent, EventLocalization
from tom_targets.models import TargetExtra

from astropy import units as u
from astropy.time import Time

from trove_targets.models import Target

from django.conf import settings

cosmo = settings.COSMO
logger = logging.getLogger(__name__)

GALAXY_CATALOG_RANKING = {c.__name__: i for i, c in enumerate(GALAXY_CATALOGS)}

# upper / lower bounds on distance for computing normal / asymmetric Gaussian
# distributions
D_LIM_LOWER = 1e-5  # 0.00001 Mpc
D_LIM_UPPER = 1e4  # 10,000 Mpc

def update_score_factor(event_candidate, key, value):
    ScoreFactor.objects.update_or_create(
        event_candidate=event_candidate, key=key, defaults=dict(value=value)
    )


def delete_score_factor(event_candidate, key):
    """This is basically only used since we are updating various scores
    and may want to delete some, rather than update them, in the process"""
    # first get any score factors that match this event candidate and key
    matches = ScoreFactor.objects.filter(event_candidate=event_candidate, key=key)

    if matches.count():
        matches.delete()


def _get_nle_distance_pdf(
    lumdist_array: np.ndarray,
    nonlocalized_event_name: str,
    target_id,
    max_time=Time.now(),
):
    # find the distance at the healpix
    dist, dist_err = _distance_at_healpix(
        nonlocalized_event_name, target_id, max_time=max_time
    )

    # let user know about hard-coded bounds on luminosity distance array
    warnings.warn(
        f"Using hard-coded D_LIM_LOWER = {D_LIM_LOWER} and "
        + f"D_LIM_UPPER = {D_LIM_UPPER} to construct log-spaced "
        + "distance array for calculating distance probability "
        + "distribution functions"
    )

    test_pdf = norm.pdf(lumdist_array, loc=dist, scale=dist_err)
    return test_pdf

def host_distance_match(
    host_df: pd.DataFrame,
    target_id: int,
    nonlocalized_event_name: str,
    max_time: Time = Time.now(),
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
        return host_df  # continue to return an empty dataframe here, but with the correct columns

    # now crossmatch this distance to the host galaxy dataframe
    _lumdist = np.linspace(D_LIM_LOWER, D_LIM_UPPER, int(10 * D_LIM_UPPER))

    test_pdf = _get_nle_distance_pdf(
        _lumdist, nonlocalized_event_name, target_id, max_time=max_time
    )

    test_mean, test_std = _distance_at_healpix(
        nonlocalized_event_name, target_id, max_time=max_time
    )

    print("\n###############################")
    print(f"GW={test_mean}+/-{test_std}")
    
    bc_ret = []
    zscore_ret = []
    resampled_zscore_ret = []
    bc_norm_ret = []
    cond_ret = []
    prob_cons_ret = []
    cons_prob_3_ret = []
    hybrid_cons_prob_ret = []
    hybrid_cons_prob_v2_ret = []
    hybrid_bc_tophat = []
    for _, row in host_df.iterrows():
        cur_pdf = AsymmetricGaussian().pdf(
            _lumdist,
            mean=row.lumdist,
            unc_minus=row.lumdist_neg_err,
            unc_plus=row.lumdist_pos_err,
            integ_a=1e-9,
            integ_b=_lumdist[-1],
        )

        print(f"\tGal={row.lumdist} +{row.lumdist_pos_err} -{row.lumdist_neg_err}")

        try:
            #bc_ret.append(bc(test_mean, test_std, row.lumdist, row.lumdist_neg_err, row.lumdist_pos_err))
            #bc_norm_ret.append(bc_norm_median_asymmetric(test_pdf, cur_pdf, test_mean, row.lumdist_neg_err, row.lumdist_pos_err, _lumdist))
            #zscore_ret.append(zscore(row.lumdist, test_mean, test_std))
            #resampled_zscore_ret.append(resampled_zscore(row.lumdist, test_mean, test_std, row.lumdist_neg_err, row.lumdist_pos_err))
            #cond_ret.append(conditional_scoring(cur_pdf, test_pdf, test_mean, row.lumdist, test_std))
            #prob_cons_ret.append(consistency_probability(test_mean, row.lumdist, test_std, row.lumdist_neg_err, row.lumdist_pos_err))
            #cons_prob_3_ret.append(cons_prob_3(test_mean, row.lumdist, test_std, row.lumdist_neg_err, row.lumdist_pos_err))
            #hybrid_cons_prob_ret.append(hybrid_cons_prob(test_mean, row.lumdist, test_std, row.lumdist_neg_err, row.lumdist_pos_err))
            #hybrid_cons_prob_v2_ret.append(hybrid_cons_prob_v2(test_pdf, cur_pdf, _lumdist, phot_type=row.z_type))
            hybrid_bc_tophat.append(
                hybrid(
                    gw_mean=test_mean,
                    galaxy_mean=row.lumdist,
                    gw_std=test_std,
                    galaxy_std_minus=row.lumdist_neg_err,
                    galaxy_std_plus=row.lumdist_pos_err
                )
            )
        except ZeroDivisionError:
            print(f"Skipping {target_id} {row.ID} because dist err is 0")
            #bc_ret.append(np.nan)
            #bc_norm_ret.append(np.nan)
            #zscore_ret.append(np.nan)
            #resampled_zscore_ret.append(np.nan)
            #cond_ret.append(np.nan)
            #prob_cons_ret.append(np.nan)
            #cons_prob_3_ret.append(np.nan)
            #hybrid_cons_prob_ret.append(np.nan)
            #hybrid_cons_prob_v2_ret.append(np.nan)
            hybrid_bc_tophat.append(np.nan)
            continue

        print()
        
    #host_df["bc"] = bc_ret
    #host_df["bc_norm"] = bc_norm_ret
    #host_df['Consistent Probability'] = prob_cons_ret
    #host_df['Improved Consistent Probability'] = cons_prob_3_ret
    #host_df['Hybrid Consistent Probability'] = hybrid_cons_prob_ret
    #host_df['Hybrid Consistent Probability v2'] = hybrid_cons_prob_v2_ret
    host_df['Hybrid BC/Tophat'] = hybrid_bc_tophat 
    
    return host_df

metrics = [
#    'bc',
#    'bc_norm',
#    'Consistent Probability',
#    'Improved Consistent Probability',
#    'Hybrid Consistent Probability',
#    'Hybrid Consistent Probability v2',
    'Hybrid BC/Tophat'
]

def get_distance_score_diagnostic(host_df, target_id, nonlocalized_event_name):
    """
    This gets the host score from the input host_df by first prioritizing target specific redshifts,
    then spec-z's, and then photo-z's. It assumes that any potential host within a
    Pcc < PCC_THRESHOLD is equally probable. It also uses the maximum probability galaxy
    to soften the effects of poor distance associations.
    """
    # first check if this target has a measured redshift
    targ = Target.objects.get(id=target_id)
    if targ.redshift is not None and not np.isnan(targ.redshift):
        _lumdist = np.linspace(D_LIM_LOWER, D_LIM_UPPER, int(10 * D_LIM_UPPER))
        nle_pdf = _get_nle_distance_pdf(_lumdist, nonlocalized_event_name, target_id)
        test_mean, test_std = _distance_at_healpix(nonlocalized_event_name, target_id)

        targ_dist = cosmo.luminosity_distance(targ.redshift).to(u.Mpc).value
        targ_dist_err = cosmo.luminosity_distance(1e-3).to(u.Mpc).value

        targ_pdf = AsymmetricGaussian().pdf(
            _lumdist,
            mean=targ_dist,
            unc_minus=targ_dist_err,
            unc_plus=targ_dist_err,
            integ_a=1e-9,
            integ_b=_lumdist[-1],
        )

        """
        "bc": (bc(test_mean, test_std, targ_dist, targ_dist_err, targ_dist_err), None, 'redshift'),
            "bc_norm": (
                bc_norm_median_asymmetric(
                    nle_pdf, targ_pdf, test_mean, targ_dist_err, targ_dist_err, _lumdist
                ),
                None,
                "redshift"
            ),
            "zscore": (zscore(targ_dist, test_mean, test_std), None, "redshift"),
            "Resampled zscore": (
                resampled_zscore(targ_dist, test_mean, test_std, targ_dist_err, targ_dist_err),
                None,
                "redshift"
            ),
            "Conditional JSD Metric": (
                conditional_scoring(targ_pdf, nle_pdf, test_mean, targ_dist, test_std),
                None,
                "redshift"
            ),
            "Consistent Probability": (
                consistency_probability(test_mean, targ_dist, test_std, targ_dist_err, targ_dist_err),
                None,
                "redshift"
            ),
            "Improved Consistent Probability": (
                cons_prob_3(test_mean, targ_dist, test_std, targ_dist_err, targ_dist_err),
                None,
                "redshift"
            ),
            "Hybrid Consistent Probability": (
                hybrid_cons_prob(test_mean, targ_dist, test_std, targ_dist_err, targ_dist_err),
                None,
                "redshift"
            ),
        """
        
        return {
            "Hybrid BC/Tophat":(
                hybrid(
                    gw_mean=test_mean,
                    galaxy_mean=targ_dist,
                    gw_std=test_std,
                    galaxy_std_minus=targ_dist_err,
                    galaxy_std_plus=targ_dist_err
                ),
                None,
                "redshift"
            )
        }

    # first, some cleanup
    # this is already done in vet_bns, vet_kn_in_sn, and vet_super_kn,
    # but we need to account for users calling this function for arbitrary
    # host_df, target, and NLE without prior filtering on host_df
    if len(host_df): ### TODO: these are filler values, should just change them to nulls in our database
        host_df = host_df[host_df.z != -99.0] # LS DR9 North
        host_df = host_df[host_df.z != -999.0] # PS1-STRM
        host_df = host_df[host_df.z != -9999.0] # SDSS DR12 photo-z
        host_df = host_df[~np.isnan(host_df.z)]

    # then use the redshift of user-uploaded host galaxies
    userz_distance_hosts = host_df[host_df.z_type == "user spec-z"]
    userz_distance_hosts.reset_index(inplace=True)  # avoid iloc exception

    return_scores = {}

    for metric in metrics:
        if len(userz_distance_hosts):
            max_score = userz_distance_hosts.bc.max()
            max_score_host_name = userz_distance_hosts.iloc[
                userz_distance_hosts[metric].idxmax()
            ]["name"]
            return_scores[metric] =  (max_score, max_score_host_name, "user-redshift")
            continue

        # then use the redshift independent measurements of distances
        ind_distance_hosts = host_df[host_df.z_type == "z ind."]
        ind_distance_hosts.reset_index(inplace=True)  # avoid iloc exception
        if len(ind_distance_hosts):
            max_score = ind_distance_hosts[metric].max()
            max_score_host_name = ind_distance_hosts.iloc[
                ind_distance_hosts[metric].idxmax()
            ]["name"]
            return_scores[metric]  = (max_score, max_score_host_name, 'ind')
            continue

        # then use the specz hosts
        specz_hosts = host_df[host_df.z_type.str.contains("spec-z")]
        specz_hosts.reset_index(inplace=True)  # avoid iloc exception
        if len(specz_hosts):
            max_score = specz_hosts[metric].max()
            max_score_host_name = specz_hosts.iloc[
                specz_hosts[metric].idxmax()
            ]["name"]
            return_scores[metric] = (max_score, max_score_host_name, "spec-z")
            continue

        # then if we don't know the spec-z or have an independent distance measure use the photo-z's
        photoz_hosts = host_df[host_df.z_type == "photo-z"]
        photoz_hosts.reset_index(inplace=True)  # avoid iloc exception
        if len(photoz_hosts):
            max_score = photoz_hosts[metric].max()
            max_score_host_name = photoz_hosts.iloc[
                photoz_hosts[metric].idxmax()
            ]["name"]
            return_scores[metric] = (max_score, max_score_host_name, "photo-z")
            continue

    # no potential host
    return return_scores # None because there is no host name 

def get_distance_score(host_df, target_id, nonlocalized_event_name):
    """
    This gets the host score from the input host_df by first prioritizing target specific redshifts,
    then spec-z's, and then photo-z's. It assumes that any potential host within a
    Pcc < PCC_THRESHOLD is equally probable. It also uses the maximum probability galaxy
    to soften the effects of poor distance associations.
    """
    # first check if this target has a measured redshift
    targ = Target.objects.get(id=target_id)
    if targ.redshift is not None and not np.isnan(targ.redshift):
        _lumdist = np.linspace(D_LIM_LOWER, D_LIM_UPPER, int(10 * D_LIM_UPPER))
        nle_pdf = _get_nle_distance_pdf(_lumdist, nonlocalized_event_name, target_id)
        targ_dist = cosmo.luminosity_distance(targ.redshift).to(u.Mpc).value
        targ_dist_err = cosmo.luminosity_distance(1e-3).to(u.Mpc).value
        targ_pdf = norm.pdf(_lumdist, loc=targ_dist, scale=targ_dist_err)
        return trapezoid(
            np.sqrt(targ_pdf * nle_pdf), x=_lumdist
        ), None  # None because there is no host name

    # first, some cleanup
    # this is already done in vet_bns, vet_kn_in_sn, and vet_super_kn,
    # but we need to account for users calling this function for arbitrary
    # host_df, target, and NLE without prior filtering on host_df
    if len(host_df): ### TODO: these are filler values, should just change them to nulls in our database
        host_df = host_df[host_df.z != -99.0] # LS DR9 North
        host_df = host_df[host_df.z != -999.0] # PS1-STRM
        host_df = host_df[host_df.z != -9999.0] # SDSS DR12 photo-z
        host_df = host_df[~np.isnan(host_df.z)]

    # then use the redshift of user-uploaded host galaxies
    userz_distance_hosts = host_df[host_df.z_type == "user spec-z"]
    userz_distance_hosts.reset_index(inplace=True)  # avoid iloc exception
    if len(userz_distance_hosts):
        max_score = userz_distance_hosts.bc.max()
        max_score_host_name = userz_distance_hosts.iloc[
            userz_distance_hosts["bc"].idxmax()
        ]["name"]
        return max_score, max_score_host_name

    # then use the redshift independent measurements of distances
    ind_distance_hosts = host_df[host_df.z_type == "z ind."]
    ind_distance_hosts.reset_index(inplace=True)  # avoid iloc exception
    if len(ind_distance_hosts):
        max_score = ind_distance_hosts.bc.max()
        max_score_host_name = ind_distance_hosts.iloc[
            ind_distance_hosts["bc"].idxmax()
        ]["name"]
        return max_score, max_score_host_name

    # then use the specz hosts
    specz_hosts = host_df[host_df.z_type.str.contains("spec-z")]
    specz_hosts.reset_index(inplace=True)  # avoid iloc exception
    if len(specz_hosts):
        max_score = specz_hosts.bc.max()
        max_score_host_name = specz_hosts.iloc[
            specz_hosts["bc"].idxmax()
        ]["name"]
        return max_score, max_score_host_name

    # then if we don't know the spec-z or have an independent distance measure use the photo-z's
    photoz_hosts = host_df[host_df.z_type == "photo-z"]
    photoz_hosts.reset_index(inplace=True)  # avoid iloc exception
    if len(photoz_hosts):
        max_score = photoz_hosts.bc.max()
        max_score_host_name = photoz_hosts.iloc[
            photoz_hosts["bc"].idxmax()
        ]["name"]
        return max_score, max_score_host_name

    # no potential host
    return 1.0, None # None because there is no host name


def skymap_association(
    nonlocalized_event_name: str,
    target_id: int,
    max_time=Time.now(),
    prob: float = 0.95,
) -> float:

    # grab the EventLocalization object for nonlocalized_event_name
    localization = _localization_from_name(nonlocalized_event_name, max_time=max_time)
    print(f"Localization Used: {localization} ({localization.date}; {max_time})")

    # find the healpix where this target is located
    target_hpx_subq = (
        sa.select(SaTarget.healpix)
        .filter(SaTarget.basetarget_ptr_id == target_id)
        .lateral()
    )

    # find the probdensity at the tile of the target_id
    # and for this localization id
    probdensity_subq = sa.select(
        sa.func.min(SaSkymapTile.probdensity).label("min_probdensity")
    ).filter(
        SaSkymapTile.tile.contains(target_hpx_subq.c.healpix),
        SaSkymapTile.localization_id == localization.id,
    )

    # then we can sum from that probability density to the maximum
    cumprob_query = sa.select(
        sa.func.sum(SaSkymapTile.probdensity * SaSkymapTile.tile.area)
    ).filter(
        SaSkymapTile.probdensity >= probdensity_subq.c.min_probdensity,
        SaSkymapTile.localization_id == localization.id,
    )

    # finally we can execute this cumprob_query and return 1 - the result
    with Session(sa_engine) as session:
        cumprob = session.execute(cumprob_query).fetchall()

    return 1 - cumprob[0][0]


def get_eventcandidate_default_distance(target_id: int, nonlocalized_event_name: str):

    # first check if this target has a redshift associated with it
    targ = Target.objects.get(id=target_id)
    if targ.redshift is not None and not np.isnan(targ.redshift):
        targ_dist = cosmo.luminosity_distance(targ.redshift).to(u.Mpc).value
        targ_dist_err = cosmo.luminosity_distance(1e-3).to(u.Mpc).value
        return targ_dist, targ_dist_err

    # then try to get out the host galaxy json file from target extra
    hosts = TargetExtra.objects.filter(target_id=target_id, key="Host Galaxies")
    if not hosts.count():
        return _distance_at_healpix(nonlocalized_event_name, target_id)
    host_df = pd.read_json(
        io.StringIO(hosts[0].value)
    )  # since we store the host info as a json str in the db

    # clean up dataframe
    if len(host_df): ### TODO: these are filler values, should just change them to nulls in our database
        host_df = host_df[host_df.z != -99.0] # LS DR9 North
        host_df = host_df[host_df.z != -999.0] # PS1-STRM
        host_df = host_df[host_df.z != -9999.0] # SDSS DR12 photo-z
        host_df = host_df[~np.isnan(host_df.z)]

    if not len(host_df):
        return _distance_at_healpix(nonlocalized_event_name, target_id)

    # if we've gotten to this point then the target has host galaxies associated with it!
    # first thing we need to do is assign a rank ordering to the various catalogs,
    # this will help later
    host_df["_rank_order"] = host_df.Source.replace(GALAXY_CATALOG_RANKING)
    host_df = host_df.sort_values(by=["_rank_order", "PCC"])

    # because we already sorted the dataframe by our "preferred" catalogs, we can
    # just always take the distances from the first row and return them
    # start with user-provided host spec z's
    userz_distance_hosts = host_df[host_df.z_type == "user spec-z"]
    ind_distance_hosts = host_df[host_df.z_type == "z ind."]
    specz_hosts = host_df[host_df.z_type.str.contains("spec-z")]
    if len(userz_distance_hosts):
        to_ret = userz_distance_hosts.iloc[0]

    # then z-indep host distances
    elif len(ind_distance_hosts):
        to_ret = ind_distance_hosts.iloc[0]

    # then spec-z's
    elif len(specz_hosts):
        to_ret = specz_hosts.iloc[0]

    # then photo-z's
    else:
        to_ret = host_df.iloc[0]

    return to_ret.Dist, to_ret.DistErr


def _distance_at_healpix(nonlocalized_event_name, target_id, max_time=Time.now()):
    """Computes the GW distance at the target_id healpix location"""

    localization = _localization_from_name(nonlocalized_event_name, max_time=max_time)
    # find the distance at the healpix
    query = sa.select(SaSkymapTile.distance_mean, SaSkymapTile.distance_std).filter(
        SaTarget.basetarget_ptr_id == target_id,
        SaSkymapTile.localization_id == localization.id,
        SaSkymapTile.tile.contains(SaTarget.healpix),
    )

    # execute the query
    with Session(sa_engine) as session:
        dist, dist_err = session.execute(query).fetchall()[0]

    return dist, dist_err


def _localization_from_name(nonlocalized_event_name, max_time=Time.now()):
    """Find the most recenet LocalizationEvent object from the nonlocalized event name"""
    # first find the localization to use
    localization_queryset = NonLocalizedEvent.objects.filter(
        event_id=nonlocalized_event_name
    )[0]

    all_localizations = EventLocalization.objects.filter(
        nonlocalizedevent_id=localization_queryset.id
    )

    all_localizations_sorted = sorted(all_localizations, key=lambda x: x.date)

    # now choose the most recent localization
    localization = all_localizations_sorted[0]
    if len(all_localizations_sorted) > 1:
        for loc in all_localizations_sorted[1:]:
            curr_loc_time = Time(localization.date, format="datetime")
            test_loc_time = Time(loc.date, format="datetime")
            if test_loc_time > curr_loc_time and test_loc_time <= max_time:
                localization = loc

    return localization
