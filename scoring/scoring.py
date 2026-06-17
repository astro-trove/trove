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

if minversion(np, "2.0.0"):
    np_trapz_fn = np.trapezoid
else:
    np_trapz_fn = np.trapz  # np.trapz is deprecated in numpy >2.0.0


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
            -0.5 * ((x[where_minus] - mean[where_minus]) / unc_minus[where_minus]) ** 2
        )  # Left side Gaussian-like
        plus_dist = np.exp(
            -0.5 * ((x[where_plus] - mean[where_plus]) / unc_plus[where_plus]) ** 2
        )  # Right side Gaussian-like

        return np.concatenate((minus_dist, plus_dist))

    def _pdf(self, x, mean, unc_minus, unc_plus, integ_a, integ_b):
        """**Normalized** asymmetric Gaussian PDF"""
        # unclear why, but even when floats are passed to this function for
        # args mean, unc_minus, unc_plus, integ_a, integ_b, they become lists
        # of the same value repeated len(x) times

        # numerically integrate asymmetric Gaussian, for normalization
        integ_x = np.linspace(integ_a[0], integ_b[0], x.shape[0])
        integ = np_trapz_fn(
            y=self._pdf_unnorm(integ_x, mean, unc_minus, unc_plus), x=integ_x
        )
        integ_norm = 1 / integ

        # return unnormalized PDF multiplied by normalization factor
        return self._pdf_unnorm(x, mean, unc_minus, unc_plus) * integ_norm


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
    host_pdfs = np.array(
        [
            AsymmetricGaussian().pdf(
                _lumdist,
                mean=row.lumdist,
                unc_minus=row.lumdist_neg_err,
                unc_plus=row.lumdist_pos_err,
                integ_a=1e-9,
                integ_b=_lumdist[-1],
            )
            for _, row in host_df.iterrows()
        ]
    )
    joint_prob = host_pdfs * test_pdf

    # finally, compute the Bhattacharyya coefficient for the overlap of these
    # two distributions. https://en.wikipedia.org/wiki/Bhattacharyya_distance
    # This coefficient is non-parametric which is good for our Asymmetric Gaussian
    # Original paper: http://www.jstor.org/stable/25047806
    host_df["dist_norm_joint_prob"] = trapezoid(np.sqrt(joint_prob), x=_lumdist, axis=1)
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
        _lumdist = np.linspace(D_LIM_LOWER, D_LIM_UPPER, int(10 * D_LIM_UPPER))
        nle_pdf = _get_nle_distance_pdf(_lumdist, nonlocalized_event_name, target_id)
        targ_dist = cosmo.luminosity_distance(targ.redshift).to(u.Mpc).value
        targ_dist_err = cosmo.luminosity_distance(1e-3).to(u.Mpc).value
        targ_pdf = norm.pdf(_lumdist, loc=targ_dist, scale=targ_dist_err)
        return trapezoid(
            np.sqrt(targ_pdf * nle_pdf), x=_lumdist
        ), None  # None because there is no host name

    # then use the redshift of user-uploaded host galaxies
    userz_distance_hosts = host_df[host_df.z_type == "user spec-z"]
    userz_distance_hosts.reset_index(inplace=True)  # avoid iloc exception
    if len(userz_distance_hosts):
        max_score = userz_distance_hosts.dist_norm_joint_prob.max()
        max_score_host_name = userz_distance_hosts.iloc[
            userz_distance_hosts["dist_norm_joint_prob"].idxmax()
        ]["name"]
        return max_score, max_score_host_name

    # then use the redshift independent measurements of distances
    ind_distance_hosts = host_df[host_df.z_type == "z ind."]
    ind_distance_hosts.reset_index(inplace=True)  # avoid iloc exception
    if len(ind_distance_hosts):
        max_score = ind_distance_hosts.dist_norm_joint_prob.max()
        max_score_host_name = ind_distance_hosts.iloc[
            ind_distance_hosts["dist_norm_joint_prob"].idxmax()
        ]["name"]
        return max_score, max_score_host_name

    # then use the specz hosts
    specz_hosts = host_df[host_df.z_type.str.contains("spec-z")]
    specz_hosts.reset_index(inplace=True)  # avoid iloc exception
    if len(specz_hosts):
        max_score = specz_hosts.dist_norm_joint_prob.max()
        max_score_host_name = specz_hosts.iloc[
            specz_hosts["dist_norm_joint_prob"].idxmax()
        ]["name"]
        return max_score, max_score_host_name

    # then if we don't know the spec-z or have an independent distance measure use the photo-z's
    max_score = host_df.dist_norm_joint_prob.max()
    max_score_host_name = host_df.iloc[host_df["dist_norm_joint_prob"].idxmax()]["name"]
    return max_score, max_score_host_name


def agn_distance_match(
    agn_df: pd.DataFrame,
    target_id: int,
    nonlocalized_event_name: str,
    max_time: Time = Time.now(),
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
        return agn_df  # continue to return an empty dataframe here, but with the correct columns

    # now crossmatch this distance to the to the AGNs dataframe
    _lumdist = np.linspace(D_LIM_LOWER, D_LIM_UPPER, int(10 * D_LIM_UPPER))

    test_pdf = _get_nle_distance_pdf(
        _lumdist, nonlocalized_event_name, target_id, max_time=max_time
    )
    agn_pdfs = np.array(
        [
            AsymmetricGaussian().pdf(
                _lumdist,
                mean=row.lumdist,
                unc_minus=row.lumdist_neg_err,
                unc_plus=row.lumdist_pos_err,
                integ_a=1e-9,
                integ_b=_lumdist[-1],
            )
            for _, row in agn_df.iterrows()
        ]
    )
    joint_prob = agn_pdfs * test_pdf

    # finally, compute the Bhattacharyya coefficient for the overlap of these
    # two distributions. https://en.wikipedia.org/wiki/Bhattacharyya_distance
    # This coefficient is non-parametric which is good for our Asymmetric Gaussian
    # Original paper: http://www.jstor.org/stable/25047806
    agn_df["dist_norm_joint_prob"] = trapezoid(np.sqrt(joint_prob), x=_lumdist, axis=1)
    return agn_df


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
