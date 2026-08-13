from .models import ScoreFactor
from .dynamic_catalogs import UserGalaxy
from .healpix_utils import SaTarget

from candidate_vetting.vet import GALAXY_CATALOGS

import io
import logging
from datetime import timezone

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

from tom_nonlocalizedevents.models import EventLocalization
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
Z_SENTINELS = (-99.0, -999.0, -9999.0)


def clean_host_df(host_df: pd.DataFrame) -> pd.DataFrame:
    """
    Drop host-galaxy rows that cannot yield a meaningful distance score.

    Two classes of bad row are removed:

    1. Redshift sentinels and nulls. Several catalogs write -99 / -999 / -9999
       instead of a null redshift.
    2. Non-physical distances and uncertainties. Photo-z codes are trained on
       galaxy SEDs, so fitting one to a star extrapolates off the training
       manifold and can return a negative redshift -- which becomes a negative
       luminosity distance. Separately, Legacy Survey photo-z PDFs sometimes
       have inverted 68% bounds (z_phot_u68 < z_phot_mean), which makes
       lumdist_pos_err negative. Neither is caught by the sentinel check.

    Upstream filtering in candidate_vetting >= v0.5.2 (DelveDr3Galaxy's
    extendedness cut, the tightened Ps1Galaxy classifier cuts) removes most of
    the sources responsible, but no catalog except DESI DR1 guards positivity
    directly, so this stays as a version-independent backstop.
    See LIMITATIONS.md section 7.
    """
    if not len(host_df):
        return host_df

    # (1) sentinels and nulls, and (2) non-physical redshifts. z > 0 subsumes
    # the sentinels, which are all negative, but both are kept so the intent
    # stays readable.
    z = pd.to_numeric(host_df.z, errors="coerce")
    host_df = host_df[z.notna() & ~z.isin(Z_SENTINELS) & (z > 0)]

    # (2) non-physical distances. "lumdist" is the in-memory name from
    # candidate_vetting.host_association; "Dist" is the name used in the
    # "Host Galaxies" TargetExtra JSON.
    for col in ("lumdist", "Dist"):
        if col in host_df.columns:
            dist = pd.to_numeric(host_df[col], errors="coerce")
            host_df = host_df[dist.notna() & (dist > 0)]

    # (2) non-physical uncertainties. Zero is left in place: it is degenerate
    # rather than impossible, and hybrid_distance_score handles it explicitly.
    for col in ("lumdist_neg_err", "lumdist_pos_err"):
        if col in host_df.columns:
            err = pd.to_numeric(host_df[col], errors="coerce")
            host_df = host_df[err.notna() & (err >= 0)]

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
    max_time: Time = None,
):
    """
    Compute the hybrid distance score of putative host galaxies' distance
    distributions against the nonlocalized event distance distribution.

    The score blends an analytic Bhattacharyya coefficient (for galaxies whose
    distance uncertainty is comparable to or larger than the GW distance
    uncertainty) with a top-hat style score (for galaxies whose distance is
    much better constrained than the GW distance). See
    :mod:`scoring.distance_helpers`.

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

    # first, some cleanup
    # this is already done in vet_bns, vet_kn_in_sn, and vet_super_kn,
    # but we need to account for users calling this function for arbitrary
    # host_df, target, and NLE without prior filtering on host_df
    host_df = clean_host_df(host_df)

    # then use the redshift of user-uploaded host galaxies
    userz_distance_hosts = host_df[host_df.z_type == "user spec-z"]
    userz_distance_hosts.reset_index(inplace=True)  # avoid iloc exception
    if len(userz_distance_hosts):
        max_score = userz_distance_hosts["hybrid_distance_score"].max()
        max_score_host_name = userz_distance_hosts.iloc[
            userz_distance_hosts["hybrid_distance_score"].idxmax()
        ]["name"]
        return max_score, max_score_host_name

    # then use the redshift independent measurements of distances
    ind_distance_hosts = host_df[host_df.z_type == "z ind."]
    ind_distance_hosts.reset_index(inplace=True)  # avoid iloc exception
    if len(ind_distance_hosts):
        max_score = ind_distance_hosts["hybrid_distance_score"].max()
        max_score_host_name = ind_distance_hosts.iloc[
            ind_distance_hosts["hybrid_distance_score"].idxmax()
        ]["name"]
        return max_score, max_score_host_name

    # then use the specz hosts
    specz_hosts = host_df[host_df.z_type.str.contains("spec-z")]
    specz_hosts.reset_index(inplace=True)  # avoid iloc exception
    if len(specz_hosts):
        max_score = specz_hosts["hybrid_distance_score"].max()
        max_score_host_name = specz_hosts.iloc[
            specz_hosts["hybrid_distance_score"].idxmax()
        ]["name"]
        return max_score, max_score_host_name

    # then if we don't know the spec-z or have an independent distance measure use the photo-z's
    photoz_hosts = host_df[host_df.z_type == "photo-z"]
    photoz_hosts.reset_index(inplace=True)  # avoid iloc exception
    if len(photoz_hosts):
        max_score = photoz_hosts["hybrid_distance_score"].max()
        max_score_host_name = photoz_hosts.iloc[
            photoz_hosts["hybrid_distance_score"].idxmax()
        ]["name"]
        return max_score, max_score_host_name

    # no potential host
    return 1.0, None # None because there is no host name


def skymap_association(
    nonlocalized_event_name: str,
    target_id: int,
    max_time=None,
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
    host_df = clean_host_df(host_df)

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


def _distance_at_healpix(nonlocalized_event_name, target_id, max_time=None):
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


# The max_time arguments in this module all default to None, meaning "now",
# resolved here per call. They used to default to Time.now() directly in the
# signature, which does not mean "now": a default argument is evaluated once,
# when the module is imported, so every caller who did not pass max_time
# explicitly shared one Time object frozen at process start.
#
# That was harmless in a web request and actively wrong in a long-running
# db_worker, which is where Vet All runs. Only localizations dated at or before
# max_time are eligible below, so a worker that had been up since before a new
# skymap arrived could never see that skymap -- it kept scoring every candidate
# against the stale localization until the process was restarted, and silently,
# since candidates vetted by a fresh worker and by an old one referenced
# different skymaps with nothing recording which.
#
# That also made the automatic re-vet in custom_code.alertstream_handlers
# unsafe: the listener compares each candidate's stored localization_id against
# the current localization, so a worker stamping an older one would leave those
# candidates permanently stale and re-queued on every alert.
def _localization_from_name(nonlocalized_event_name, max_time=None):
    """Find the most recent EventLocalization object for this nonlocalized event

    Only localizations dated at or before max_time are eligible, so a candidate
    can be scored against the skymap that was current at a chosen point in time
    rather than always the latest one. max_time=None means now.
    """
    if max_time is None:
        max_time = Time.now()

    # Let the database do the filtering and the ordering. This used to pull
    # every localization for the event into python, build an astropy Time for
    # each one and sort them by hand, which is a lot of work for something
    # called several times per candidate -- skymap_association,
    # host_distance_match and get_distance_score each resolve the localization
    # separately.
    max_datetime = max_time.to_datetime(timezone=timezone.utc)
    all_localizations = EventLocalization.objects.filter(
        nonlocalizedevent__event_id=nonlocalized_event_name
    )

    localization = (
        all_localizations.filter(date__lte=max_datetime).order_by("-date").first()
    )
    if localization is not None:
        return localization

    # Nothing at or before max_time, so fall back to the earliest localization
    # we have. This preserves the old behaviour, which seeded its search with
    # the oldest localization and only replaced it when a later one also fit
    # under max_time.
    return all_localizations.order_by("date").first()
