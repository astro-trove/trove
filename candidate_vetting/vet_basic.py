"""
the vet_basic function is for when there is not a NLE associated with a transient and
simply does
0. Checks for new photometry
1. point source association
2. MPC crossmatching
3. AGN crossmatching
4. Host association

But without any direct scoring!

This should also be called before any photometry vetting in the NLE-related
vetting modules. That way we can reduce the code duplication between them!
"""


import logging
import io
from astropy.time import Time
import numpy as np
import pandas as pd

from trove_targets.models import Target
from tom_dataproducts.models import ReducedDatum
from tom_targets.models import TargetExtra

from .vet import (
    point_source_association,
    host_association,
    agn_association_2d,
    save_score_to_targetextra,
    HOST_DF_COLMAP_INVERSE
)
from .vet_phot import find_public_phot

from trove_mpc import Transient

logger = logging.getLogger(__name__)

def vet_basic(
        target_id:int,
        days_ago_max:int=200,
        overwrite:bool=False,
        queue_priority:int=0
):
    logger.info("Running basic vetting")
    
    # get the Target object associated with this target_id
    target = Target.objects.get(id=target_id)

    # then check for new photometry
    find_public_phot(
        target=target,
        forced_phot_tol = 0,
        days_ago_max=days_ago_max,
        queue_priority=queue_priority
    )

    # get the TargetExtra object associated with this target_id 
    te = TargetExtra.objects.filter(target_id=target.id)
    
    ## run the point source checker
    if overwrite or not te.filter(key="ps_score").exists():
        logger.info("Running Point Source Matching...")
        ps_score = point_source_association(target_id)
        save_score_to_targetextra(target, "ps_score", ps_score)

    ## search for an AGN associated with the target
    agn_df = agn_association_2d(
        target_id, 
        radius=2 # 2 arcseconds
    )
        
    ## run the minor planet checker
    if overwrite or not te.filter(key="mpc_match_name").exists():
        # get photometry, throwing out limiting mags, phot with no error, and phot with SNR < 5
        phot = ReducedDatum.objects.filter(
            target_id=target_id,
            data_type="photometry",
            value__magnitude__isnull=False,
            value__error__isnull=False,
            value__error__lte=2.5/np.log(10)/5
        )
        # if more than (5-sigma) 1 detection, likely not a MPC object
        if phot.exists() and len(phot) > 1:
            logger.warn("This candidate has more than 1 >5-sigma detection, "+
                        "skipping MPC!")
            mpc_match = None
        # if only 1 detection, run MPC match
        elif phot.exists() and len(phot) == 1:
            latest_det = phot.latest()
            date = Time(latest_det.timestamp).mjd
            t = Transient(target.ra, target.dec)
            mpc_match = t.minor_planet_match(date)
        # no detections --> can't do MPC match
        else:
            logger.warn("This candidate has no photometry, skipping MPC!")
            mpc_match = None

        if mpc_match is not None:
            # update the score factor information
            save_score_to_targetextra(
                target, "mpc_match_name", mpc_match.match_name
            )
            save_score_to_targetextra(
                target, "mpc_match_sep", mpc_match.distance
            )
            save_score_to_targetextra(
                target, "mpc_match_date", latest_det.timestamp
            )
        else:
            save_score_to_targetextra(
                target, "mpc_match_name", None
            )
            
    ## do the Pcc analysis and find a host
    host_df = host_association(
        target_id,
        radius = 5*60
    )
     
    ## return both agn_df and host_df       
    return host_df, agn_df
