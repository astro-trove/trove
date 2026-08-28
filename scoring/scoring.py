from .models import ScoreFactor
from .dynamic_catalogs import UserGalaxy
from .healpix_utils import SaTarget

from candidate_vetting.vet import GALAXY_CATALOGS

import io
import logging

import numpy as np
import pandas as pd

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

from .distance_helpers import hybrid_distance_score

cosmo = settings.COSMO
logger = logging.getLogger(__name__)

GALAXY_CATALOG_RANKING = {c.__name__: i for i, c in enumerate([UserGalaxy] + GALAXY_CATALOGS)}

### TODO: these are filler values, should just change them to nulls in our database
# LS DR9 North / DELVE DR3, PS1-STRM, SDSS DR12 photo-z / DELVE DR3
Z_BAD_VALUES = (-99.0, -999.0, -9999.0)


def clean_host_df(host_df: pd.DataFrame) -> pd.DataFrame:
    """Drop host galaxy rows with bad values."""
    if not len(host_df):
        return host_df

    z = pd.to_numeric(host_df.z, errors="coerce")
    host_df = host_df[z.notna() & ~z.isin(Z_BAD_VALUES)]

    for col in ("lumdist", "Dist"):
        if col in host_df.columns:
            dist = pd.to_numeric(host_df[col], errors="coerce")
            host_df = host_df[dist.notna()]

    for col in ("lumdist_neg_err", "lumdist_pos_err"):
        if col in host_df.columns:
            err = pd.to_numeric(host_df[col], errors="coerce")
            host_df = host_df[err.notna()]

    return host_df


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


def host_distance_match(
    host_df: pd.DataFrame,
    target_id: int,
    nonlocalized_event_name: str,
    max_time: Time = Time.now(),
):
    """
    Compute the hybrid distance score of putative host galaxies' distance
    distributions against the nonlocalized event distance distribution.

    The score blends an analytic Bhattacharyya coefficient (for galaxies whose
    distance uncertainty is comparable to or larger than the GW distance
    uncertainty) with a top-hat style score (for galaxies whose distance is
    much better constrained than the GW distance)

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
        Dataframe containing information on host galaxy, with added
        hybrid_distance_score column

    """
    if not len(host_df):
        host_df["hybrid_distance_score"] = []
        return host_df  # continue to return an empty dataframe here, but with the correct columns

    nle_dist, nle_dist_err = _distance_at_healpix(
        nonlocalized_event_name, target_id, max_time=max_time
    )

    host_df["hybrid_distance_score"] = [
        hybrid_distance_score(
            nle_dist,
            row.lumdist,
            nle_dist_err,
            row.lumdist_neg_err,
            row.lumdist_pos_err,
        )
        for _, row in host_df.iterrows()
    ]
    return host_df


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
        nle_dist, nle_dist_err = _distance_at_healpix(
            nonlocalized_event_name, target_id
        )
        targ_dist = cosmo.luminosity_distance(targ.redshift).to(u.Mpc).value
        targ_dist_err = cosmo.luminosity_distance(1e-3).to(u.Mpc).value
        return hybrid_distance_score(
            nle_dist, targ_dist, nle_dist_err, targ_dist_err, targ_dist_err
        ), None # None because there is no host name

    # callers may pass an unfiltered host_df, so clean it here too
    host_df = clean_host_df(host_df)

    # Trust user redshifts first, then redshift-independent distances, then
    # spec-z's, then photo-z's. Use the best-scoring galaxy in the first tier
    # that has one.
    tiers = (
        host_df.z_type == "user spec-z",
        host_df.z_type == "z ind.",
        host_df.z_type.str.contains("spec-z", na=False),
        host_df.z_type == "photo-z",
    )
    for tier in tiers:
        hosts = host_df[tier]
        # A NaN score is a galaxy we could not score -- a non-physical redshift
        # or distance. Those stay in host_df so they still show up in the score
        # details table, but they must not win the max.
        scores = hosts["hybrid_distance_score"].dropna()
        if not len(scores):
            continue
        best = hosts.loc[scores.idxmax()]
        return best["hybrid_distance_score"], best["name"], best["catalog"]

    # no potential host
    return 1.0, None, None # Nones because there are no host names or host catalogs


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


def _host_used_for_distance_scoring(host_df, target_id, nonlocalized_event_name):
    if not len(host_df) or "ID" not in host_df.columns:
        return None
    try:
        nle = NonLocalizedEvent.objects.get(event_id=nonlocalized_event_name)
        factors = dict(
            ScoreFactor.objects.filter(
                event_candidate__target_id=target_id,
                event_candidate__nonlocalizedevent_id=nle.id,
                key__in=["host_name", "host_catalog"],
            ).values_list("key", "value")
        )
    except (NonLocalizedEvent.DoesNotExist, ValueError):
        return None

    host_name = factors.get("host_name")
    if not host_name or host_name == "None":
        return None

    match = host_df[host_df["ID"].astype(str) == str(host_name)]
    # IDs are only unique within a catalog, so disambiguate when we know it
    catalog = factors.get("host_catalog")
    if len(match) > 1 and catalog and "Source" in host_df.columns:
        match = match[match["Source"].astype(str) == str(catalog)]
    if len(match) != 1:
        return None

    row = match.iloc[0]
    dist = pd.to_numeric(getattr(row, "Dist", None), errors="coerce")
    if dist is None or not np.isfinite(dist) or dist <= 0:
        return None
    return row


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
    host_df = clean_host_df(host_df)

    if not len(host_df):
        return _distance_at_healpix(nonlocalized_event_name, target_id)

    scored_host = _host_used_for_distance_scoring(
        host_df, target_id, nonlocalized_event_name
    )
    if scored_host is not None:
        return scored_host.Dist, scored_host.DistErr

    # otherwise fall back to a rank ordering of the various catalogs,
    # this will help later
    host_df["_rank_order"] = host_df.Source.replace(GALAXY_CATALOG_RANKING)
    host_df = host_df.sort_values(by=["_rank_order", "PCC"])

    # because we already sorted the dataframe by our "preferred" catalogs, we can
    # just always take the distances from the first row and return them
    # start with user-provided host spec z's
    if "z_type" in host_df.columns:
        userz_distance_hosts = host_df[host_df.z_type == "user spec-z"]
        ind_distance_hosts = host_df[host_df.z_type == "z ind."]
        specz_hosts = host_df[host_df.z_type.str.contains("spec-z", na=False)]
    else:
        _none = host_df.iloc[:0]
        userz_distance_hosts = ind_distance_hosts = specz_hosts = _none
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
