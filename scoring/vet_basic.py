"""
Basic vetting , possible even if no nonlocalized event associated with a
target. Does the following:
0. Checks for new photometry
1. AGN crossmatching
2. Host association
3. Point source association
4. MPC crossmatching

But without any direct scoring!

Steps 3 and 4 are not carried out if no new photometry and user has said
not to carry out those steps in absence of new photometry.

This should also be called before any photometry vetting in the NLE-related
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


def vet_basic(
    target_id: int,
    days_ago_max: int = 200,
    overwrite: bool = False,
    queue_priority: int = 0,
    skip_vet_if_no_new_phot: bool = False,
    use_async_mpc: bool = False,
):
    logger.info("Running basic vetting")

    # get the Target object associated with this target_id
    target = Target.objects.get(id=target_id)

    # get the TargetExtra object associated with this target_id
    te = TargetExtra.objects.filter(target_id=target.id)

    # then check for new photometry
    phot_query_start = time.time()
    created_new_phot = find_public_phot(
        target=target,
        forced_phot_tol=0,
        days_ago_max=days_ago_max,
        queue_priority=queue_priority,
    )
    logger.info(f"Finding public photometry took {time.time() - phot_query_start}s")

    # get associated AGN, host galaxies
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

    # stop here and return if no further vetting needed
    if skip_vet_if_no_new_phot and not created_new_phot:
        logger.info(
            "Skipping point source and minor planet vetting because no new "
            + "photometry and skip_vet_if_no_new_phot=True"
        )
        return host_df, agn_df

    # run the point source checker
    if overwrite or not te.filter(key="ps_score").exists():
        logger.info("Running Point Source Matching...")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ps_matches = point_source_association(target_id)
            ps_score = int(len(ps_matches) < 1)  # 1 if no ps_matches, 0 otherwise
            save_score_to_targetextra(target, "ps_score", ps_score)

    # run the minor planet checker
    if overwrite or not te.filter(key="mpc_match_name").exists():
        if use_async_mpc:
            logger.info("Sending MPC to the async queue, check back later for results")
            async_mpc.enqueue(target_id)
        else:
            logger.info("Running MPC in real-time, this may take a bit...")
            run_mpc(target_id)

    # return both agn_df and host_df
    return host_df, agn_df
