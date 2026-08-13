"""
Basic vetting , possible even if no nonlocalized event associated with a
target. Does the following:
0. Point source association
1. Checks for new photometry
2. MPC crossmatching
3. AGN crossmatching
4. Host association

But without any direct scoring!

The point source and MPC crossmatches come first because they are the only
checks here that produce a hard zero: both are 0-or-1 and both multiply into
the final candidate score (see scoring.util.get_event_candidate_scores), so a
match in either one means nothing computed afterwards can change the outcome.
Unless stop_on_zero_score is turned off, a zero in either ends the vetting
before the slow catalog queries below it.

Steps 0 and 2 are not carried out if no new photometry and user has said
not to carry out those steps in absence of new photometry. In that case they
also cannot run first, since whether they run at all depends on step 1.

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

from django.db import connections
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
from .models import UserGalaxyQ3C
from .vet_phot import find_public_phot
from .tasks import async_mpc

logger = logging.getLogger(__name__)

# TargetExtra key holding the inputs the saved "Host Galaxies" dataframe was
# built from, so we can tell whether it is still current
HOST_GALAXY_CACHE_KEY = "Host Galaxies Cache Key"

# Columns the scoring code reads off a rebuilt host dataframe: z / z_type drive
# the tier selection in get_distance_score, lumdist and its two uncertainties
# drive host_distance_match, and name is reported as the matched host
REQUIRED_HOST_COLUMNS = ("name", "z", "z_type", "lumdist", "z_err", "lumdist_err")

# how long to reuse a reading of the galaxy catalog state, in seconds
CATALOG_STATE_TTL = 60
_CATALOG_STATE = {"read_at": 0.0, "state": None}


def _catalog_state():
    """The parts of the host association fingerprint that are the same for
    every target: how many galaxies each catalog holds, and the user-uploaded
    galaxy table.

    Row counts are the re-ingestion signal. These tables are far too large to
    count directly -- roughly 9 TB in total, with DELVE DR3 alone at 2.6 billion
    rows -- so this reads Postgres' own planner estimate out of pg_class, which
    is O(1) regardless of table size. pg_stat_all_tables would be a better
    signal in principle, but n_tup_ins is 0 for every one of these tables (they
    were bulk loaded and the statistics never collected since), so it cannot
    tell "untouched" from "just replaced".

    Both readings are one round trip each, and the fingerprint is rebuilt once
    per candidate, so they are cached together. Deliberately a TTL rather than a
    permanent memo: a long-running db_worker must not be able to pin a stale
    answer for its whole lifetime, which is the same trap the max_time defaults
    in scoring.scoring used to fall into. The TTL is far shorter than any
    plausible ingest.

    catalog_rows is None if the estimate cannot be read, in which case we fall
    back to relying on invalidate_host_galaxy_cache being called explicitly.
    """
    now = time.time()
    if (
        _CATALOG_STATE["state"] is not None
        and now - _CATALOG_STATE["read_at"] < CATALOG_STATE_TTL
    ):
        return _CATALOG_STATE["state"]

    tables = sorted(c.catalog_model._meta.db_table for c in GALAXY_CATALOGS)
    try:
        with connections["catalogs"].cursor() as cursor:
            cursor.execute(
                "SELECT relname, reltuples::bigint FROM pg_class "
                "WHERE relname = ANY(%s) ORDER BY relname",
                [tables],
            )
            catalog_rows = dict(cursor.fetchall())
    except Exception as e:  # a stats lookup must never break vetting
        logger.warning(f"Could not read galaxy catalog row counts: {e}")
        catalog_rows = None

    # count *and* max id, so that a deletion invalidates the cache too
    user_galaxies = UserGalaxyQ3C.objects.aggregate(n=Count("id"), latest=Max("id"))

    state = {
        "catalog_rows": catalog_rows,
        "user_galaxies": [user_galaxies["n"], user_galaxies["latest"]],
    }
    _CATALOG_STATE.update(read_at=now, state=state)
    return state


def invalidate_host_galaxy_cache(target_ids=None) -> int:
    """Drop saved host association fingerprints so the next vetting run
    re-queries the galaxy catalogs. Returns how many targets were invalidated.

    Call this after re-ingesting a galaxy catalog. The catalog tables live in
    the separate "catalogs" database (candidate_vetting.routers.CatalogRouter
    sends everything in the candidate_vetting app there) while the fingerprints
    live in the default database, and _host_galaxy_cache_key cannot see catalog
    row contents from here -- so a re-ingest is otherwise invisible to the
    cache and every target keeps serving a stale association.

    Only the fingerprints are deleted, not the saved "Host Galaxies" rows, so
    target pages keep rendering their hosts until each target is next vetted.

    Importable so that ingestion code with this app on its path can call it
    directly; scoring/management/commands/invalidate_host_galaxies.py wraps it
    for callers that have to shell out instead.
    """
    fingerprints = TargetExtra.objects.filter(key=HOST_GALAXY_CACHE_KEY)
    if target_ids is not None:
        fingerprints = fingerprints.filter(target_id__in=target_ids)

    count = fingerprints.count()
    fingerprints.delete()
    return count


def _host_galaxy_cache_key(target) -> str:
    """A fingerprint of the host association inputs that we can cheaply check.

    Covered here: the target's position, which catalogs we search and with what
    cuts, and the user-uploaded galaxies -- the one mutable "catalog" in the
    list. If none of those moved, re-running the cone searches returns exactly
    what we already saved.

    Also covered: the installed candidate_vetting version, because an upgrade
    can change both which galaxies come back (v0.5.2 tightened DelveDr3's
    extendedness cut and the Ps1Galaxy classifier cuts, for instance) and which
    columns _save_host_galaxy_df writes. That second one matters -- there are
    already five different column sets in the "Host Galaxies" rows on disk from
    successive versions, and most of the older ones have no z_type column at
    all, which get_distance_score would blow up on.

    Partly covered: the contents of the static galaxy tables, via each table's
    estimated row count (see _catalog_row_counts). That catches a re-ingest
    which adds, removes or replaces rows, which is what a new data release
    does. It does NOT catch an in-place edit that leaves the row count
    unchanged -- correcting photo-z values across existing rows, say -- and the
    estimate itself can drift slightly when Postgres re-analyses a table, which
    costs an unnecessary re-query but never a wrong answer.

    So the row count is a backstop, not a guarantee. Catalog ingestion should
    still invalidate explicitly:

        python manage.py invalidate_host_galaxies

    or, from python, scoring.vet_basic.invalidate_host_galaxy_cache(). Failing
    both, vet_basic(..., overwrite=True) refreshes a single target.
    """
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

    # What _save_host_galaxy_df writes has changed over time -- there are five
    # different column sets in the rows already on disk, and the older ones are
    # missing z_type, which get_distance_score indexes on unconditionally. The
    # fingerprint pins the candidate_vetting version so a schema change should
    # already have invalidated the row, but a saved dataframe is cheap to check
    # and expensive to be wrong about, so verify rather than assume
    missing = [c for c in REQUIRED_HOST_COLUMNS if c not in df.columns]
    if missing:
        logger.info(
            f"Saved host galaxies are missing {missing}, re-running the "
            + "association rather than trusting them"
        )
        return None

    # it also collapses the asymmetric uncertainties into one column, holding
    # [neg, pos] when the two differ and a scalar when they do not. Everything
    # downstream (host_distance_match in particular) wants them separated again
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


def _point_source_score(target, target_extras, overwrite: bool = False) -> float:
    """The 0-or-1 point source score, running (and caching) the crossmatch if
    we don't have it yet. 1 means no point source match, which is the good case
    """
    cached = target_extras.filter(key="ps_score")
    if not overwrite and cached.exists():
        return float(cached[0].value)

    logger.info("Running Point Source Matching...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ps_matches = point_source_association(target.id)
    ps_score = int(len(ps_matches) < 1)  # 1 if no ps_matches, 0 otherwise
    save_score_to_targetextra(target, "ps_score", ps_score)
    return ps_score


def _minor_planet_score(
    target, target_extras, overwrite: bool = False, use_async_mpc: bool = False
):
    """The 0-or-1 minor planet score, running the MPC match if we don't have it
    yet. 1 means no minor planet match, which is the good case. Returns None
    when there is no verdict -- the match was queued rather than run, or the
    lookup failed -- in which case the caller has nothing to gate on.

    How the stored mpc_match_name becomes a score is duplicated from
    scoring.util.get_event_candidate_scores, so keep the two in sync!
    """
    if overwrite or not target_extras.filter(key="mpc_match_name").exists():
        if use_async_mpc:
            logger.info("Sending MPC to the async queue, check back later for results")
            async_mpc.enqueue(target.id)
            return None
        logger.info("Running MPC in real-time, this may take a bit...")
        try:
            run_mpc(target.id)
        except Exception as e:
            # run_mpc downloads orbit data from an external service, so this
            # fails for reasons that have nothing to do with the candidate --
            # an empty response parsed as JSON, a timeout, a rate limit. That
            # must not abort the vetting run: before the 0-or-1 checks were
            # moved to the front, a failure here at least left the AGN and host
            # associations already saved, whereas now it would lose them too.
            # No verdict means no gate, and the rest of the vetting continues.
            logger.warning(f"MPC lookup failed for {target.name}, skipping it: {e}")
            return None

    match = target_extras.filter(key="mpc_match_name")
    if not match.exists():
        return 1
    return int(match[0].value == str(None))


def vet_basic(
    target_id: int,
    days_ago_max: int = 200,
    overwrite: bool = False,
    queue_priority: int = 0,
    skip_vet_if_no_new_phot: bool = False,
    use_async_mpc: bool = False,
    stop_on_zero_score: bool = True,
):
    """Run the NLE-independent vetting for a target.

    Returns (host_df, agn_df, keep_vetting). keep_vetting is False when the
    point source or minor planet crossmatch has already zeroed this target's
    score and we stopped early, in which case the two dataframes are empty and
    the AGN / host associations have *not* been refreshed. Callers doing
    NLE-dependent scoring should return as soon as they see keep_vetting=False.

    Pass stop_on_zero_score=False to always do the full pass, e.g. when a user
    asks for one specific target and wants the host / AGN tables regardless of
    the score.
    """
    logger.info("Running basic vetting")

    # get the Target object associated with this target_id
    target = Target.objects.get(id=target_id)

    # get the TargetExtra object associated with this target_id
    te = TargetExtra.objects.filter(target_id=target.id)

    # can the 0-or-1 checks go first? Only if they are unconditional: when
    # skip_vet_if_no_new_phot is set, whether they run at all depends on the
    # photometry query below, so we have to keep them after it
    gate_first = not skip_vet_if_no_new_phot

    # point source matching is the cheapest check we have (a few indexed cone
    # searches, no network), so it goes ahead of even the photometry query
    if gate_first:
        ps_score = _point_source_score(target, te, overwrite=overwrite)
        if stop_on_zero_score and not ps_score:
            logger.info(
                f"{target.name} matches a known point source, so it scores 0 "
                + "regardless of anything else; skipping the rest of the vetting"
            )
            return pd.DataFrame(), pd.DataFrame(), False

    # then check for new photometry
    phot_query_start = time.time()
    created_new_phot = find_public_phot(
        target=target,
        forced_phot_tol=0,
        days_ago_max=days_ago_max,
        queue_priority=queue_priority,
    )
    logger.info(f"Finding public photometry took {time.time() - phot_query_start}s")

    # the minor planet check has to come after the photometry query, since it
    # decides what to do based on how many detections the target has
    if gate_first:
        mpc_score = _minor_planet_score(
            target, te, overwrite=overwrite, use_async_mpc=use_async_mpc
        )
        if stop_on_zero_score and mpc_score == 0:
            logger.info(
                f"{target.name} matches a known minor planet, so it scores 0 "
                + "regardless of anything else; skipping the rest of the vetting"
            )
            return pd.DataFrame(), pd.DataFrame(), False

    # get associated AGN, host galaxies
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # search for an AGN associated with the target
        agn_df = agn_association_2d(target_id)

        # do the Pcc analysis and find a host. These cone searches run against
        # every galaxy catalog we have and are the slowest thing in this
        # function, but they depend only on inputs that almost never change, so
        # reuse the saved dataframe whenever the fingerprint still matches
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

    if not gate_first:
        # stop here and return if no further vetting needed
        if not created_new_phot:
            logger.info(
                "Skipping point source and minor planet vetting because no new "
                + "photometry and skip_vet_if_no_new_phot=True"
            )
            return host_df, agn_df, True

        _point_source_score(target, te, overwrite=overwrite)
        _minor_planet_score(
            target, te, overwrite=overwrite, use_async_mpc=use_async_mpc
        )

    # return both agn_df and host_df
    return host_df, agn_df, True
