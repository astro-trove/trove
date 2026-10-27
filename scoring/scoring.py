import importlib.metadata
import json
import logging
import time
from datetime import timezone
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import sqlalchemy as sa
from astropy import units as u
from astropy.time import Time
from candidate_vetting.vet import (
    GALAXY_CATALOGS,
    HOST_ASSOC_RADIUS,
    HOST_DF_COLMAP_INVERSE,
    PCC_THRESHOLD,
    run_mpc,
)
from django.conf import settings
from django.db.models import Count, Max
from scipy.stats import norm
from sqlalchemy.orm import Session
from tom_nonlocalizedevents.healpix_utils import (
    SaSkymapTile,
    # uniq_to_bigintrange,
    # update_all_credible_region_percents_for_candidates
    sa_engine,
)
from tom_nonlocalizedevents.models import EventLocalization, NonLocalizedEvent
from tom_targets.models import TargetExtra

from trove_targets.models import Target
from trove_targets.healpix_utils import SaTarget

from .distance_helpers import hybrid_distance_score
from .dynamic_catalogs import UserGalaxy
from .models import CatalogWriteCounters, ScoreFactor, UserGalaxyQ3C
from .tasks import async_mpc
from .vet_phot import PHOT_SCORE_MIN

logger = logging.getLogger(__name__)


NUCLEAR_SCALE_KPC = 0.5
ASTROMETRIC_PRECISION_ARCSEC = 2.0

# built from, so we can tell whether it is still current
HOST_GALAXY_CACHE_KEY = "Host Galaxies Cache Key"

REQUIRED_HOST_COLUMNS = ("name", "z", "z_type", "lumdist", "z_err", "lumdist_err")

# how long to reuse a reading of the galaxy catalog state, in seconds
CATALOG_STATE_TTL = 60
_CATALOG_STATE = {"read_at": 0.0, "state": None}

cosmo = settings.COSMO
logger = logging.getLogger(__name__)

GALAXY_CATALOG_RANKING = {c.__name__: i for i, c in enumerate([UserGalaxy] + GALAXY_CATALOGS)}

### TODO: these are filler values, should just change them to nulls in our database
# LS DR9 North / DELVE DR3, PS1-STRM, SDSS DR12 photo-z / DELVE DR3
Z_BAD_VALUES = (-99.0, -999.0, -9999.0)

HOST_ID_COLUMNS = ("ID", "troveID")


def read_host_galaxies_json(value: str) -> pd.DataFrame:
    """The saved "Host Galaxies" JSON as a dataframe, with the ID columns exact."""
    rows = json.loads(value)
    host_df = pd.DataFrame(rows)
    # pandas makes the whole column float64 if any ID is null, rounding IDs above 2**53
    for col in HOST_ID_COLUMNS:
        if col in host_df.columns:
            host_df[col] = pd.Series([row.get(col) for row in rows], dtype=object)
    return host_df


def clean_host_df(host_df: pd.DataFrame) -> pd.DataFrame:
    """Drop host galaxy rows with bad values."""
    if not len(host_df):
        return host_df

    z = pd.to_numeric(host_df.z, errors="coerce")
    host_df = host_df[~z.isin(Z_BAD_VALUES)]

    for col in ("lumdist", "Dist"):
        if col in host_df.columns:
            dist = pd.to_numeric(host_df[col], errors="coerce")
            host_df = host_df[dist.notna()]

    for col in ("lumdist_neg_err", "lumdist_pos_err"):
        if col in host_df.columns:
            err = pd.to_numeric(host_df[col], errors="coerce")
            host_df = host_df[err.notna()]

    return host_df


# score factors that don't depend on the EM model, saved once under em_model="basic"
BASIC_SCORE_KEYS = {
    "mpc_match_sep", "mpc_match_date", "mpc_match_name", "mpc_score",
    "host_catalog", "host_name", "host_distance_score",
    "skymap_score", "ps_score", "localization_id",
}


def update_score_factor(event_candidate, key, value, em_model):
    ScoreFactor.objects.create(
        event_candidate=event_candidate,
        key=key,
        value=value,
        em_model="basic" if key in BASIC_SCORE_KEYS else em_model,
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
        targ_score = hybrid_distance_score(
            nle_dist, targ_dist, nle_dist_err, targ_dist_err, targ_dist_err
        )
        # This should be done here and not in the hybrid_distance_score
        # because if hybrid_distance_score gives a 1 for an unscorable host galaxy,
        # then the .idxmax() would always prefer unscorable host galaxies to real scores,
        # even if the real scores are very good
        if np.isnan(targ_score):
            targ_score = 1.0
        return targ_score, None # None because there is no host name

    # callers may pass an unfiltered host_df, so clean it here too
    host_df = clean_host_df(host_df)

    # then use the redshift of user-uploaded host galaxies
    userz_distance_hosts = host_df[host_df.z_type == "user spec-z"]
    userz_distance_hosts.reset_index(inplace=True)  # avoid iloc exception
    if userz_distance_hosts["hybrid_distance_score"].notna().any():
        max_score = userz_distance_hosts.hybrid_distance_score.max()
        max_score_host_name = userz_distance_hosts.iloc[
            userz_distance_hosts["hybrid_distance_score"].idxmax()
        ]["name"]
        max_score_host_catalog = userz_distance_hosts.iloc[
            userz_distance_hosts["hybrid_distance_score"].idxmax()
        ]["catalog"]
        return max_score, max_score_host_name, max_score_host_catalog

    # then use the redshift independent measurements of distances
    ind_distance_hosts = host_df[host_df.z_type.isin(["z-ind.", "spec-z/z-ind."])]
    ind_distance_hosts.reset_index(inplace=True)  # avoid iloc exception
    if ind_distance_hosts["hybrid_distance_score"].notna().any():
        max_score = ind_distance_hosts.hybrid_distance_score.max()
        max_score_host_name = ind_distance_hosts.iloc[
            ind_distance_hosts["hybrid_distance_score"].idxmax()
        ]["name"]
        max_score_host_catalog = ind_distance_hosts.iloc[
            ind_distance_hosts["hybrid_distance_score"].idxmax()
        ]["catalog"]
        return max_score, max_score_host_name, max_score_host_catalog

    # then use the specz hosts
    specz_hosts = host_df[host_df.z_type.str.contains("spec-z")]
    specz_hosts.reset_index(inplace=True)  # avoid iloc exception
    if specz_hosts["hybrid_distance_score"].notna().any():
        max_score = specz_hosts.hybrid_distance_score.max()
        max_score_host_name = specz_hosts.iloc[
            specz_hosts["hybrid_distance_score"].idxmax()
        ]["name"]
        max_score_host_catalog = specz_hosts.iloc[
            specz_hosts["hybrid_distance_score"].idxmax()
        ]["catalog"]
        return max_score, max_score_host_name, max_score_host_catalog

    # then if we don't know the spec-z or have an independent distance measure use the photo-z's
    photoz_hosts = host_df[host_df.z_type == "photo-z"]
    photoz_hosts.reset_index(inplace=True)  # avoid iloc exception
    if photoz_hosts["hybrid_distance_score"].notna().any():
        max_score = photoz_hosts.hybrid_distance_score.max()
        max_score_host_name = photoz_hosts.iloc[
            photoz_hosts["hybrid_distance_score"].idxmax()
        ]["name"]
        max_score_host_catalog = photoz_hosts.iloc[
            photoz_hosts["hybrid_distance_score"].idxmax()
        ]["catalog"]
        return max_score, max_score_host_name, max_score_host_catalog

    # no potential host
    return 1.0, None, None # Nones because there are no host names or host catalogs


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


def _host_used_for_distance_scoring(host_df, target_id, nonlocalized_event_name):
    if not len(host_df) or "ID" not in host_df.columns:
        return None
    try:
        nle = NonLocalizedEvent.objects.get(event_id=nonlocalized_event_name)
        score_factors = ScoreFactor.objects.filter(
            event_candidate__target_id=target_id,
            event_candidate__nonlocalizedevent_id=nle.id
        )
        host_name = score_factors.filter(
            key="host_name",
        ).latest().value
    except (NonLocalizedEvent.DoesNotExist, ScoreFactor.DoesNotExist, ValueError):
        return None

    # the catalog is optional, it only disambiguates IDs shared across catalogs
    host_catalog = score_factors.filter(
        key = "host_catalog"
    ).order_by("-timestamp", "-id").first()

    factors = dict(
        host_name = host_name,
        host_catalog = host_catalog.value if host_catalog else None
    )

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
    host_df = read_host_galaxies_json(hosts[0].value)

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
        ind_distance_hosts = host_df[host_df.z_type.isin(["z-ind.", "spec-z/z-ind."])]
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


def _localization_from_name(nonlocalized_event_name, max_time=None):
    """Most recent EventLocalization for this event, dated at or before max_time."""
    if max_time is None:
        max_time = Time.now()

    all_localizations = EventLocalization.objects.filter(
        nonlocalizedevent__event_id=nonlocalized_event_name
    )
    localization = (
        all_localizations
        .filter(date__lte=max_time.to_datetime(timezone=timezone.utc))
        .order_by("-date")
        .first()
    )
    # nothing at or before max_time: fall back to the earliest
    return localization or all_localizations.order_by("date").first()

def mpc_score_from_match(mpc_match_name) -> int:
    """0 if there is a Minor Planet Center match, else 1. run_mpc stores the
    string "None" when nothing matched; None means the check hasn't run."""
    return int(mpc_match_name in (None, "None"))


def classification_score(
        target,
        expected_em_transient:str=None
) -> float:
    """Rules out candidates that TNS have already classified

    Follows this general psuedocode logic
    if classification==SN Ia and any GW event: score = 0
    elif classification==TDE and any GW event: score = 0
    elif classification.startswith("SN") and (GW event == BNS, NSBH, or BBH): score = 0
    elif classification.startswith("SN") and (GW event == SSM) and (expected_em_transient == KN): score = 0
    else: score = 1
    """
    # get the classification, default to an empty string if key not present,
    # strip any extraneous chars
    classification = getattr(target, "classification", "")
    if classification is None:
        classification = ""
    clean_class = classification.strip()

    # calculate the classification score
    if clean_class in {"TDE", "SN Ia"}:
        # if the transient is classified as a TDE or SN Ia then the score is 0
        # because these are not expected to be GW counterparts
        # TODO: When implementing neutrino vetting we should be more careful about
        #       removing TDEs here!!
        return 0

    elif (
            (clean_class.startswith("SN") or clean_class.startswith("SLSN")) and
            expected_em_transient not in {"KN-in-SN", "super-KN"}
    ):
        # for KN and AGN flares we don't want to include SN or SLSN, for KN-in-SN or
        # superKN they could *maybe* be a counterpart
        return 0

    # if neither of those cases are matched we can just return 1
    return 1

def _catalog_state():
    """Fingerprint inputs shared by every target: galaxy catalog write activity
    and the user-galaxy table.

    Each catalog contributes its relid and its cumulative insert + update +
    delete count, so we can use this to detect when any new host galaxies have
    been ingested or anything has changed in the catalogs. relid also catches a
    catalog rebuilt as a new table and renamed over the old one
    """
    now = time.time()
    if (
        _CATALOG_STATE["state"] is not None
        and now - _CATALOG_STATE["read_at"] < CATALOG_STATE_TTL
    ):
        return _CATALOG_STATE["state"]

    tables = sorted(c.catalog_model._meta.db_table for c in GALAXY_CATALOGS)
    try:
        catalog_writes = {
            name: [relid, (ins or 0) + (upd or 0) + (dels or 0)]
            for name, relid, ins, upd, dels in CatalogWriteCounters.objects.using(
                "catalogs"
            )
            .filter(relname__in=tables)
            .values_list("relname", "relid", "n_tup_ins", "n_tup_upd", "n_tup_del")
        }
    except Exception as e:  # a stats lookup must never break vetting
        logger.warning(f"Could not read galaxy catalog write counters: {e}")
        catalog_writes = None

    # count and max id, so that a deletion invalidates the cache too
    user_galaxies = UserGalaxyQ3C.objects.aggregate(n=Count("id"), latest=Max("id"))

    state = {
        "catalog_writes": catalog_writes,
        "user_galaxies": [user_galaxies["n"], user_galaxies["latest"]],
    }
    _CATALOG_STATE.update(read_at=now, state=state)
    return state


def invalidate_host_galaxy_cache(target_ids=None) -> int:
    """Drop saved host association fingerprints so the next vet re-queries the
    galaxy catalogs. Returns how many targets were invalidated.
    """
    fingerprints = TargetExtra.objects.filter(key=HOST_GALAXY_CACHE_KEY)
    if target_ids is not None:
        fingerprints = fingerprints.filter(target_id__in=target_ids)

    count = fingerprints.count()
    fingerprints.delete()
    return count


def _host_galaxy_cache_key(target) -> str:
    """Fingerprint of the host association inputs we can check cheaply"""
    try:
        vetting_version = importlib.metadata.version("candidate_vetting")
    except importlib.metadata.PackageNotFoundError:
        vetting_version = "unknown"

    return json.dumps(
        {
            "ra": target.ra,
            "dec": target.dec,
            "catalogs": [c.__name__ for c in [UserGalaxy] + GALAXY_CATALOGS],
            "radius": HOST_ASSOC_RADIUS,
            "pcc_threshold": PCC_THRESHOLD,
            "candidate_vetting": vetting_version,
            **_catalog_state(),
        },
        sort_keys=True,
    )


def _cached_host_df(target_extras, cache_key: str):
    """Rebuild the host galaxy dataframe from the saved "Host Galaxies"
    TargetExtra, or None when we have no usable cache for this target.
    """
    saved_key = target_extras.filter(key=HOST_GALAXY_CACHE_KEY)
    if not saved_key.exists() or saved_key[0].value != cache_key:
        return None

    hosts = target_extras.filter(key="Host Galaxies")
    if not hosts.exists():
        return None

    df = read_host_galaxies_json(hosts[0].value)
    if not len(df):
        return None  # cheaper to re-run than to hand-build the empty columns

    # _save_host_galaxy_df renames the columns on the way in, so undo that
    df = df.rename(columns=HOST_DF_COLMAP_INVERSE)

    # Stored columns have changed across candidate_vetting versions and older
    # rows lack z_type, which get_distance_score needs. The version is in the
    # fingerprint, but verifying is cheap and being wrong is not.
    missing = [c for c in REQUIRED_HOST_COLUMNS if c not in df.columns]
    if missing:
        logger.info(
            f"Saved host galaxies are missing {missing}, re-running the "
            + "association rather than trusting them"
        )
        return None

    # asymmetric errors are stored collapsed: [neg, pos] when they differ, a
    # scalar when they do not. Split them back out.
    for collapsed, neg_col, pos_col in (
        ("z_err", "z_neg_err", "z_pos_err"),
        ("lumdist_err", "lumdist_neg_err", "lumdist_pos_err"),
    ):
        errs = [
            err if isinstance(err, (list, tuple)) else [err, err]
            for err in df[collapsed]
        ]
        df[neg_col] = [err[0] for err in errs]
        df[pos_col] = [err[1] for err in errs]

    # offset is the angular distance in arcsec, ang_dist the same in degrees
    df["ang_dist"] = df["offset"] / 3600

    return df


def _minor_planet_score(
    target, target_extras, use_async_mpc: bool = False
):
    """The 0-or-1 minor planet score, running the MPC match if we don't have it
    yet. 1 means no minor planet match, which is the good case. Returns None
    when there is no verdict -- the match was queued rather than run, or the
    lookup failed -- in which case the caller has nothing to gate on.
    """
    if use_async_mpc:
        logger.info("Sending MPC to the async queue, check back later for results")
        async_mpc.enqueue(target.id)
        return None
    logger.info("Running MPC in real-time, this may take a bit...")
    try:
        run_mpc(target.id)
    except Exception as e:
        # I was having a few errors with this, so wrapped it in a try/except,
        # but possibly unnecessary
        logger.warning(f"MPC lookup failed for {target.name}, skipping it: {e}")
        return None
    match = target_extras.filter(key="mpc_match_name").first()
    return mpc_score_from_match(match.value if match else None)

def nuclear_offset_score(
    host_rows, floor: float = PHOT_SCORE_MIN, scale_kpc: float = NUCLEAR_SCALE_KPC,
    astrometric_precision_arcsec: float = ASTROMETRIC_PRECISION_ARCSEC,
) -> Tuple[Optional[float], dict]:
    if not host_rows:
        return None, {}
    for row in host_rows:
        if not isinstance(row, dict):
            continue
        off, dist = row.get("Offset"), row.get("Dist")
        if off is None or dist is None:
            continue
        try:
            off, dist = float(off), float(dist)
        except (TypeError, ValueError):
            continue
        if not (np.isfinite(off) and np.isfinite(dist)) or dist <= 0:
            continue

        # "Dist" is a luminosity distance; an angle becomes a physical size
        # through the angular-diameter distance, D_A = D_L / (1+z)^2
        z = _host_redshift(row, dist)
        d_a = dist / (1.0 + z) ** 2
        offset_kpc = projected_offset_kpc(off, d_a)
        precision_kpc = projected_offset_kpc(astrometric_precision_arcsec, d_a)
        if precision_kpc < scale_kpc:
            # astrometry resolves the nucleus, so judge the physical offset
            regime, scale, offset, unit = "physical", scale_kpc, offset_kpc, " kpc"
        else:
            # it doesn't, so an offset can only be compared with angular position
            regime, scale, offset, unit = "angular", astrometric_precision_arcsec, off, '"'
        score = float(np.clip(scale / (scale + offset), floor, 1.0))
        return score, dict(
            host=row.get("ID"), offset_arcsec=round(off, 3), z=round(z, 4),
            luminosity_distance_mpc=round(dist, 1), angular_diameter_distance_mpc=round(d_a, 1),
            offset_kpc=round(offset_kpc, 3), astrometric_precision_kpc=round(precision_kpc, 3),
            regime=regime,
            verdict=f"{regime}: {offset:.2f}{unit} offset against a {scale:g}{unit} scale",
        )
    return None, {}

def _host_redshift(row: dict, luminosity_distance_mpc: float) -> float:
    try:
        z = float(row.get("z"))
        if np.isfinite(z) and z >= 0:
            return z
    except (TypeError, ValueError):
        pass
    from astropy.cosmology import z_at_value
    from django.conf import settings

    try:
        return float(z_at_value(settings.COSMO.luminosity_distance, luminosity_distance_mpc * u.Mpc))
    except Exception:  # below the root-finder's range
        return 0.0


def projected_offset_kpc(offset_arcsec: float, angular_diameter_distance_mpc: float) -> float:
    return (
        float(offset_arcsec)*u.arcsec * float(angular_diameter_distance_mpc)*u.Mpc
    ).to(
        u.radian*u.kpc
    ).value

def _host_rows(target) -> list:
    import json

    from tom_targets.models import TargetExtra

    extra = TargetExtra.objects.filter(target_id=target.id, key="Host Galaxies").first()
    if not extra or not extra.value:
        return []
    try:
        rows = json.loads(extra.value) if isinstance(extra.value, str) else extra.value
    except (ValueError, TypeError):
        return []
    return rows if isinstance(rows, list) else []


def _agn_nucleus_row(target, agn_df) -> Optional[dict]:
    # the nearest matched AGN marks the nucleus; Dist is None when the match has no distance
    if agn_df is None or not len(agn_df) or not {"ra", "dec"} <= set(agn_df.columns):
        return None
    from astropy.coordinates import SkyCoord

    sep = SkyCoord(target.ra * u.deg, target.dec * u.deg).separation(
        SkyCoord(agn_df.ra.to_numpy(float) * u.deg, agn_df.dec.to_numpy(float) * u.deg)
    ).arcsec
    agn = agn_df.iloc[int(np.nanargmin(sep))]
    dist = agn.get("lumdist")
    has_dist = dist is not None and np.isfinite(float(dist)) and float(dist) > 0
    return dict(ID=agn.get("name"), Offset=float(np.nanmin(sep)),
                Dist=float(dist) if has_dist else None, z=agn.get("z"))

def flare_confidence_score(
    significance: float,
    thresh: float,
    floor: float = PHOT_SCORE_MIN,
    center_frac: float = 1.0,
    width_frac: float = 0.25,
) -> float:
    # normal-CDF sigmoid; a significance at center_frac * thresh scores ~0.55
    center = center_frac * thresh
    width = max(width_frac * thresh, 1e-6)
    raw = norm.cdf(significance, loc=center, scale=width)
    return float(np.clip(floor + (1.0 - floor) * raw, floor, 1.0))
