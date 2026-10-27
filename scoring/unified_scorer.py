"""
*Generalized* functionality for computing the score from the em_model jsons
"""
import logging
import time
import warnings
from functools import partial

import numpy as np
import pandas as pd
from astropy import units as u
from astropy.time import Time, TimeDelta
from candidate_vetting.vet import (
    GALAXY_CATALOGS,
    agn_association_2d,
    host_association,
    localization_sequence_from_name,
    point_source_association,
    save_score_to_targetextra,
)
from tom_nonlocalizedevents.models import EventCandidate, EventSequence, NonLocalizedEvent
from tom_targets.models import TargetExtra

from custom_code.templatetags.nonlocalizedevent_extras import get_most_likely_class
from trove_targets.models import Target

from .dynamic_catalogs import UserGalaxy
from .em_models.util import MODELS_BY_NAME
from .kilonova_scorer_helpers.util import (
    DT_MAX,
    KilonovaScoreUnavailable,
    kilonova_score_candidate,
)
from .scoring import (
    ASTROMETRIC_PRECISION_ARCSEC,
    HOST_GALAXY_CACHE_KEY,
    _agn_nucleus_row,
    _cached_host_df,
    _catalog_state,
    _host_galaxy_cache_key,
    _host_rows,
    _localization_from_name,
    _minor_planet_score,
    classification_score,
    clean_host_df,
    delete_score_factor,
    flare_confidence_score,
    get_distance_score,
    host_distance_match,
    nuclear_offset_score,
    skymap_association,
)
from .scoring import update_score_factor as _update_score_factor
from .vet_phot import (
    OPTICAL_FILTS,
    PHOT_SCORE_MIN,
    PREDETECTION_SNR_THRESHOLD,
    _get_post_disc_phot,
    _get_pre_disc_phot,
    _score_phot,
    detect_flare,
    find_public_phot,
    fit_agn_baseline,
    get_predetection_stats,
)

logger = logging.getLogger(__name__)

def score(
        target_id: int,
        nonlocalized_event_name: str,
        em_model_name: str,
        basic_results:tuple=None,
        phot_method:str="metrics",
        **kwargs
):

    params = MODELS_BY_NAME[em_model_name]
    target = Target.objects.get(id=target_id)
    nonlocalized_event = NonLocalizedEvent.objects.get(event_id=nonlocalized_event_name)
    candidate = EventCandidate.objects.get(
        target=target,
        nonlocalizedevent=nonlocalized_event
    )
    nle_eventseq = localization_sequence_from_name(nonlocalized_event.event_id)
    nle_type = get_most_likely_class(nle_eventseq.details)

    update_score_factor = partial(_update_score_factor, em_model=params.name)

    # run basic vetting
    if basic_results is None:
        basic_results = vet_basic(
            target_id,
            run_mpc=kwargs.pop("run_mpc", params.mpc_crossmatch),
            run_agn=kwargs.pop("run_agn", params.agn_crossmatch),
            run_ps=kwargs.pop("run_ps", params.ps_crossmatch),
            run_host_association=kwargs.pop("host_association", params.host_association),
            **kwargs
        )
    host_df, agn_df, keep_vetting = basic_results

    if not keep_vetting:
        # Scoring has already produced 0, so there is no point in scoring
        # the distance or photometry because it can't change the score
        return

    # some cleanup
    host_df = clean_host_df(host_df)

    if params.classification_score:
        class_score = classification_score(target, params.name)
        if class_score == 0:
            return


    if params.agn_crossmatch:
        if len(agn_df):
            agn_assoc_score = params.score_controls.agn.match
        else:
            agn_assoc_score = params.score_controls.agn.no_match
        update_score_factor(candidate, "agn_score", agn_assoc_score)


    if params.localization_score:
        ## check skymap association
        if np.isfinite(params.t_post):
            gw_disc_date = (
                EventSequence.objects.filter(  # GW discovery time
                    nonlocalizedevent_id=nonlocalized_event.id
                )
                .last()
                .details["time"]
            )
            max_time = Time(gw_disc_date) + TimeDelta(params.t_post * u.day)
        else:  # just use current time
            max_time = Time.now()
        skymap_score = skymap_association(
            nonlocalized_event_name, target_id, max_time=max_time
        )
        update_score_factor(candidate, "skymap_score", skymap_score)

        # the skymap and distance scores are only valid for one localization, so
        # record which one produced them
        localization = _localization_from_name(nonlocalized_event_name, max_time=max_time)
        update_score_factor(candidate, "localization_id", localization.id)

    if params.distance_score:
        if target.redshift is not None and not np.isnan(target.redshift):
            # use target redshift, so no need to compute distance scores for galaxies
            host_score, host_name = get_distance_score(
                host_df, target_id, nonlocalized_event_name
            )
            update_score_factor(candidate, "host_distance_score", host_score)

        elif len(host_df) != 0:
            # then run the distance comparison for each of these hosts
            host_df = host_distance_match(host_df, target_id, nonlocalized_event_name)

            # choose the maximum score
            if nle_type not in {"FXT", "LGRB", "SGRB"}: # These don't have distances
                host_score, host_name, host_catalog = get_distance_score(
                    host_df, target_id, nonlocalized_event_name
                )
                update_score_factor(candidate, "host_distance_score", host_score)
                update_score_factor(candidate, "host_name", host_name)
                update_score_factor(candidate, "host_catalog", host_catalog)

        else:
            # if no target redshift is known and no hosts are found, we don't want
            # to bias the final score (host may just be too far)
            host_score = 1

            # and we should also clear out any existing scores / host names for it
            delete_score_factor(candidate, "host_distance_score")
            delete_score_factor(candidate, "host_name")
            delete_score_factor(candidate, "host_catalog")


    prephot = _get_pre_disc_phot(
        target_id=target_id,
        nonlocalized_event=nonlocalized_event,
        t_pre=params.t_pre,
    )
    postphot = _get_post_disc_phot(
        target_id=target_id,
        nonlocalized_event=nonlocalized_event,
        t_post=params.t_post,
        t_pre=params.t_pre,
    )

    if params.phot_method.knscorer and phot_method=="kilonovascorer":
        try:
            phot_score = kilonova_score_candidate(
                target_id=target_id,
                nonlocalized_event=nonlocalized_event,
                candidate_name=target.name,
                allphot=postphot,
                t_post=np.nanmin([params.t_post, DT_MAX]),
                overlap_k=3.0,
            )
            update_score_factor(candidate, "kilonova_score", phot_score)
            delete_score_factor(candidate, "kilonova_skip_reason")
        except KilonovaScoreUnavailable as exc:
            # Unscoreable is not the same as rejected. Falling through to the
            # TROVE factor keeps the candidate rankable instead of zeroing it
            # for missing a distance or a grid band.
            logger.warning("KilonovaSCORER unavailable for %s: %s", target.name, exc)
            update_score_factor(candidate, "kilonova_skip_reason", str(exc))
            delete_score_factor(candidate, "kilonova_score")


    if params.phot_method.metric and phot_method=="metrics":
        phot_score, lum, max_time, decay_rate, _, _ = _score_phot(
            allphot=postphot,
            target=target,
            nonlocalized_event=nonlocalized_event,
            param_ranges=params.phot_parameter.model_dump() | {"t_pre": params.t_pre, "t_post": params.t_post},
            filt=OPTICAL_FILTS
        )

        if lum is not None:
            update_score_factor(candidate, "phot_peak_lum", lum.value)
        else:
            delete_score_factor(candidate, "phot_peak_lum")

        if max_time is not None:
            update_score_factor(candidate, "phot_peak_time", max_time)
        else:
            delete_score_factor(candidate, "phot_peak_time")

        if decay_rate is not None:
            update_score_factor(candidate, "phot_decay_rate", decay_rate)
        else:
            delete_score_factor(candidate, "phot_decay_rate")


    if params.phot_method.agn_flare:
        ## photometric flare scoring
        baseline = fit_agn_baseline(
            prephot, min_baseline_pts=params.phot_parameter.min_baseline_pts
        )
        for filt, entry in baseline.items():
            update_score_factor(candidate, f"baseline_mag_{filt}", entry["mag"])
            update_score_factor(candidate, f"baseline_std_{filt}", entry["std"])

        max_significance, flare_row = detect_flare(
            postphot, baseline,
            corroboration_window_days=params.phot_parameter.flare_corroboration_window_days,
            pair_max_gap_days=params.phot_parameter.flare_pair_max_gap_days,
        )

        agn_flare_score = None
        # NaN means no usable photometry, so no verdict
        if baseline and postphot is not None and len(postphot) and np.isfinite(max_significance):
            agn_flare_score = flare_confidence_score(
                max_significance,
                params.phot_parameter.flare_sigma_thresh,
                floor=PHOT_SCORE_MIN,
                center_frac=params.phot_parameter.flare_score_center_frac,
                width_frac=params.phot_parameter.flare_score_width_frac,
            )
            update_score_factor(candidate, "agn_flare_score", agn_flare_score)
        else:
            # not enough baseline, post-merger photometry, or post-merger nights
            delete_score_factor(candidate, "agn_flare_score")


    if params.nuclear_offset_score:
        # measured from the matched AGN when there is one, else from the host galaxy
        host_rows = _host_rows(target)
        agn_row = _agn_nucleus_row(target, agn_df)
        if agn_row is not None and agn_row["Dist"] is None:
            # no AGN distance, so only the astrometric precision can judge the offset
            precision = ASTROMETRIC_PRECISION_ARCSEC
            offset_score = float(np.clip(precision / (precision + agn_row["Offset"]), PHOT_SCORE_MIN, 1.0))
        else:
            offset_score, _ = nuclear_offset_score(
                ([agn_row] if agn_row else []) + host_rows, floor=PHOT_SCORE_MIN
            )

        if offset_score is None:
            delete_score_factor(candidate, "nuclear_offset_score")
        else:
            update_score_factor(candidate, "nuclear_offset_score", offset_score)


    if params.phot_method.predets_filter:
        # check for *reliable* predetections before time t_pre
        prephot = _get_pre_disc_phot(
            target_id=target_id,
            nonlocalized_event=nonlocalized_event,
            t_pre=params.t_pre,
        )
        predet_score = params.score_controls.phot.match
        if prephot is not None and len(prephot):
            try:
                n_predets, _ = get_predetection_stats(
                    prephot.mjd.values,
                    prephot.magerr.values,
                    window_size=5,  # +/-5 day window size
                    det_snr_thresh=PREDETECTION_SNR_THRESHOLD,
                )
            except ValueError:
                n_predets = [
                    0
                ]  # this ValueError only happens when there aren't any predets
            if any(v >= params.phot_parameter.max_predets for v in n_predets):
                predet_score = params.score_controls.phot.no_match
        update_score_factor(candidate, "predetection_score", predet_score)

def vet_basic(
        target_id: int,
        days_ago_max: int = 200,
        overwrite: bool = False,
        queue_priority: int = 0,
        skip_vet_if_no_new_phot: bool = False,
        use_async_mpc: bool = False,
        stop_on_zero: bool = True,
        query_atlas_fp: bool = True,
        query_tns_phot: bool = True,
        query_antares_phot: bool = True,
        run_mpc: bool = True,
        run_ps: bool = True,
        run_agn: bool = True,
        run_host_association: bool = True
):
    """Run the NLE-independent vetting for a target and return
    (`host_df`, `agn_df`, `keep_vetting`). `keep_vetting` is False when PS or
    MPC has already zero'd this target's score and we stopped early, in which
    case the two dataframes are empty the AGN / host associations have *not*
    been reperformed.

    Pass `stop_on_zero = False` to always do the full pass and not stop
    regardless of PS or MPC association.

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
    logger.info("Running basic vetting")

    target = Target.objects.get(id=target_id)
    te = TargetExtra.objects.filter(target_id=target.id)

    # (0) point source association
    # perform if overwrite or PS score doesn't exist
    if run_ps and (overwrite or not te.filter(key="ps_score").exists()):
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
        query_atlas_fp=query_atlas_fp,
        query_tns_phot=query_tns_phot,
        query_antares_phot=query_antares_phot,
    )
    logger.info("Finding public photometry took "+
                f"{(time.time() - phot_query_start):.2f}s")

    # (2) Minor Planet Center association
    # proceed if overwrite **OR** MPC score doesn't exist...
    if run_mpc and (overwrite or not te.filter(key="mpc_match_name").exists()):
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

        agn_df = pd.DataFrame({})
        if run_agn:
            # search for an AGN associated with the target
            agn_df = agn_association_2d(target_id)

        host_df = pd.DataFrame({})
        if run_host_association:
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
                if _catalog_state()["catalog_writes"] is not None:
                    save_score_to_targetextra(target, HOST_GALAXY_CACHE_KEY, cache_key)
                else:
                    logger.warning(
                        f"Not saving the host galaxy fingerprint for {target.name}: "
                        + "couldn't confirm the galaxy catalogs are unchanged"
                    )
            else:
                logger.info(
                    f"Reusing the saved host galaxy association for {target.name}, "
                    + "nothing it depends on has changed"
                )

    # return both agn_df and host_df
    return host_df, agn_df, True
