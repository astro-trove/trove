"""
Basic vetting , possible even if no nonlocalized event associated with a
target. Does the following:
0. Point source association
1. Checks for new photometry
2. MPC crossmatching
3. AGN crossmatching
4. Host association

PS and MPC crossmatches come early because may produce subscores of 0 or 1.

This should be called before any photometry vetting in the NLE-related
vetting modules. That way we can reduce the code duplication between them!
"""

import importlib.metadata
import io
import json
import logging
import warnings
import time

import pandas as pd

from django.db.models import Count, Max

from trove_targets.models import Target
from tom_targets.models import TargetExtra


from candidate_vetting.vet import (
    GALAXY_CATALOGS,
    HOST_ASSOC_RADIUS,
    HOST_DF_COLMAP_INVERSE,
    PCC_THRESHOLD,
    point_source_association,
    host_association,
    agn_association_2d,
    save_score_to_targetextra,
    run_mpc,
)

from .dynamic_catalogs import UserGalaxy
from .models import PgStatAllTables, UserGalaxyQ3C
from .vet_phot import find_public_phot
from .tasks import async_mpc

logger = logging.getLogger(__name__)

# built from, so we can tell whether it is still current
HOST_GALAXY_CACHE_KEY = "Host Galaxies Cache Key"

REQUIRED_HOST_COLUMNS = ("name", "z", "z_type", "lumdist", "z_err", "lumdist_err")

# how long to reuse a reading of the galaxy catalog state, in seconds
CATALOG_STATE_TTL = 60
_CATALOG_STATE = {"read_at": 0.0, "state": None}


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
            for name, relid, ins, upd, dels in PgStatAllTables.objects.using(
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

    df = pd.read_json(io.StringIO(hosts[0].value), orient="records")
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
    match = target_extras.filter(key="mpc_match_name")
    if match.exists():
        return int(match[0].value == str(None))
    else:
        return 1


def vet_basic(
    target_id: int,
    days_ago_max: int = 200,
    overwrite: bool = False,
    queue_priority: int = 0,
    skip_vet_if_no_new_phot: bool = False,
    use_async_mpc: bool = False,
    stop_on_zero: bool = True,
):
    """Run the NLE-independent vetting for a target and return
    (`host_df`, `agn_df`, `keep_vetting`). `keep_vetting` is False when PS or
    MPC has already zero'd this target's score and we stopped early, in which
    case the two dataframes are empty the AGN / host associations have *not*
    been reperformed.

    Pass `stop_on_zero = False` to always do the full pass and not stop
    regardless of PS or MPC association.
    """
    logger.info("Running basic vetting")

    target = Target.objects.get(id=target_id)
    te = TargetExtra.objects.filter(target_id=target.id)

    # (0) point source association
    # perform if overwrite or PS score doesn't exist
    if overwrite or not te.filter(key="ps_score").exists():
        logger.info("Running Point Source Matching...")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ps_matches = point_source_association(target_id)
            ps_score = int(len(ps_matches) < 1)  # 1 if no ps_matches, 0 otherwise
            save_score_to_targetextra(target, "ps_score", ps_score)
        if stop_on_zero and ps_score == 0:
            logger.info(
                f"{target.name} matches a known point source, so it scores 0 "
                +"regardless of anything else; skipping rest of vetting"
            )
            return pd.DataFrame(), pd.DataFrame(), False

    # (1) check for new photometry
    phot_query_start = time.time()
    created_new_phot = find_public_phot(
        target=target,
        forced_phot_tol=0,
        days_ago_max=days_ago_max,
        queue_priority=queue_priority,
    )
    logger.info("Finding public photometry took "+
                f"{(time.time() - phot_query_start):.2f}s")

    # (2) Minor Planet Center association
    # proceed if overwrite **OR** MPC score doesn't exist...
    if overwrite or not te.filter(key="mpc_match_name").exists():
        if skip_vet_if_no_new_phot and not created_new_phot: # ... but stop if skip_vet_if_no_new_phot **AND** no new phot created
            logger.info(
                        "Skipping Minor Planet Center association because no new "
                        +"photometry and skip_vet_if_no_new_phot=True"
            )
        else: # ... proceed otherwise
            mpc_score = _minor_planet_score(
                target, te, use_async_mpc=use_async_mpc
            )
            if stop_on_zero and mpc_score == 0:
                logger.info(
                    f"{target.name} matches a Minor Planet Center object, so it "
                    +"scores 0 regardless of anything else; skipping rest of vetting"
                )
                return pd.DataFrame(), pd.DataFrame(), False

    # (3), (4) get associated AGN, host galaxies
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # search for an AGN associated with the target
        agn_df = agn_association_2d(target_id)

        # do the Pcc analysis and find a host
        # these cone searches run against every galaxy catalog we have and are
        # the slowest thing in this function, so reuse the cached dataframe
        # whenever the fingerprint still matches
        cache_key = _host_galaxy_cache_key(target)
        host_df = None if overwrite else _cached_host_df(te, cache_key)
        if host_df is None:
            galaxy_catalogs = [UserGalaxy] + GALAXY_CATALOGS
            host_df = host_association(target_id,
                                       galaxy_catalogs=galaxy_catalogs)
            save_score_to_targetextra(target, HOST_GALAXY_CACHE_KEY, cache_key)
        else:
            logger.info(
                f"Reusing the saved host galaxy association for {target.name}, "
                + "nothing it depends on has changed"
            )

    # return both agn_df and host_df
    return host_df, agn_df, True
