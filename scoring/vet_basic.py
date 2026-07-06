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

import logging
import warnings
import time

from trove_targets.models import Target
from tom_targets.models import TargetExtra

from dynamic_catalogs import UserGalaxy

from candidate_vetting.vet import (
    GALAXY_CATALOGS,
    point_source_association,
    host_association,
    agn_association_2d,
    save_score_to_targetextra,
    run_mpc,
)
from .vet_phot import find_public_phot
from .tasks import async_mpc

logger = logging.getLogger(__name__)


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
        galaxy_catalogs = [UserGalaxy] + GALAXY_CATALOGS
        host_df = host_association(target_id,
                                   galaxy_catalogs=galaxy_catalogs)

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
