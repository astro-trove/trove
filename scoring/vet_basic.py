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

import logging
import warnings
import time

import pandas as pd

from trove_targets.models import Target
from tom_targets.models import TargetExtra


from candidate_vetting.vet import (
    GALAXY_CATALOGS,
    point_source_association,
    host_association,
    agn_association_2d,
    save_score_to_targetextra,
    run_mpc,
)

from .dynamic_catalogs import UserGalaxy
from .vet_phot import find_public_phot
from .tasks import async_mpc

logger = logging.getLogger(__name__)


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
        galaxy_catalogs = [UserGalaxy] + GALAXY_CATALOGS
        host_df = host_association(target_id,
                                   galaxy_catalogs=galaxy_catalogs)

    # return both agn_df and host_df
    return host_df, agn_df, True
