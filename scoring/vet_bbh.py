"""
The "pipeline" to vet candidate counterparts to nonlocalized BBH events based on
their resemblance to an AGN flare: a brightening of a pre-existing, persistently
variable AGN rather than a fresh transient.

Reuses the skymap / host / AGN-catalog / distance scoring machinery from the KN-style
vetters (see vet_bns.py) as-is. The genuinely new pieces are:

1. `agn_score` is inverted relative to the KN vetters: a Milliquas/RomaBzcat match is
   supporting evidence (it can only help), not disqualifying, since a *missing* match
   may just mean that particular AGN hasn't been ingested into either catalog yet.
2. `host_nuclear_score`: how close the candidate sits to its best-matched host galaxy's
   nucleus, scored continuously (inversely proportional to the offset) rather than as a
   hard cut, anchored at the originally proposed 2" threshold.
3. `agn_flare_score`: model-agnostic anomaly detection. AGN-flare theory is
   underdeveloped and unconfirmed observationally, so rather than fitting any particular
   variability model (damped random walk, power spectral density, etc.) this just
   characterizes the empirical pre-merger scatter per filter (robust median + MAD) and
   flags a significant *brightening* excursion above that envelope within the
   post-merger window.
"""

import logging
from typing import Optional
from astropy.time import Time, TimeDelta
from astropy import units as u
import numpy as np
import pandas as pd

from .scoring import (
    update_score_factor,
    delete_score_factor,
    host_distance_match,
    get_distance_score,
    skymap_association,
)
from .vet_basic import vet_basic
from .vet_phot import (
    _get_post_disc_phot,
    _get_pre_disc_phot,
    PHOT_SCORE_MIN,
)

from trove_targets.models import Target
from tom_nonlocalizedevents.models import (
    EventCandidate,
    NonLocalizedEvent,
    EventSequence,
)

logger = logging.getLogger(__name__)

PARAM_RANGES = dict(
    t_pre=0,  # baseline = all photometry before the GW trigger (dt < 0)
    t_post=400,  # flare window: 0-400 days after the GW trigger
    min_baseline_pts=5,  # minimum pre-trigger points in a filter to trust its baseline
    flare_sigma_thresh=5.0,  # detection significance for a "flare", matches
    # PREDETECTION_SNR_THRESHOLD's 5-sigma convention elsewhere in vet_phot.py
    nuclear_offset_anchor=2.0 * u.arcsec,  # score = anchor / offset, capped at 1
    agn_boost_multiplier=5.0,
)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def fit_agn_baseline(prephot: Optional[pd.DataFrame], min_baseline_pts: int = 5) -> dict:
    """
    Characterize the pre-merger "typical variability envelope" of the candidate host
    AGN, per filter, without assuming any particular variability model.

    Uses the robust median magnitude and 1.4826*MAD (a robust estimator of the
    standard deviation) of the pre-merger photometry in each filter. This is
    deliberately model-agnostic: it says nothing about *why* an AGN varies (DRW, PSD,
    etc.), only how much it has historically varied, which is all that's needed to
    flag a later excursion as unusual.

    Parameters
    ----------
    prephot : pd.DataFrame or None
        Pre-merger photometry, as returned by `vet_phot._get_pre_disc_phot`. Expected
        columns: mag, magerr, filter, upperlimit.
    min_baseline_pts : int
        Minimum number of detections required in a filter before its baseline is
        considered trustworthy.

    Returns
    -------
    dict mapping filter -> dict(mag=<median mag>, std=<robust scatter>, n=<n points>)
    Filters with too few points are simply absent from the returned dict.
    """
    baseline = {}
    if prephot is None or not len(prephot):
        return baseline

    phot = prephot[~prephot.upperlimit]
    for filt, group in phot.groupby("filter"):
        mags = group.mag.to_numpy(dtype=float)
        if len(mags) < min_baseline_pts:
            continue
        median_mag = float(np.median(mags))
        robust_std = 1.4826 * float(np.median(np.abs(mags - median_mag)))
        # floor the scatter at the median measurement error so a baseline that
        # happens to be tightly time-sampled (MAD -> 0) doesn't make every later
        # point look artificially significant
        median_err = float(np.median(group.magerr.to_numpy(dtype=float)))
        robust_std = max(robust_std, median_err)
        baseline[filt] = dict(mag=median_mag, std=robust_std, n=int(len(mags)))
    return baseline


def detect_flare(
    postphot: Optional[pd.DataFrame],
    baseline: dict,
    sigma_thresh: float = 5.0,
):
    """
    Look for a significant brightening excursion above the AGN baseline within the
    post-merger photometry.

    Only brightening (flux excess relative to baseline) counts as a candidate flare,
    matching the physical picture of an accretion-episode re-brightening; a dimming
    excursion is not flagged.

    Parameters
    ----------
    postphot : pd.DataFrame or None
        Post-merger photometry in the scoring window, as returned by
        `vet_phot._get_post_disc_phot`.
    baseline : dict
        Output of `fit_agn_baseline`.
    sigma_thresh : float
        Not used to filter here -- the caller compares the returned significance to
        this threshold. Kept as an argument for symmetry / future use.

    Returns
    -------
    (max_significance, best_row): the largest brightening significance found and its
    corresponding photometry row (a pandas Series), or (np.nan, None) if nothing in
    postphot has a filter with a fitted baseline.
    """
    if postphot is None or not len(postphot) or not baseline:
        return np.nan, None

    phot = postphot[~postphot.upperlimit]
    phot = phot[phot["filter"].isin(baseline.keys())]
    if not len(phot):
        return np.nan, None

    significance = [
        (baseline[row["filter"]]["mag"] - row.mag)
        / np.sqrt(baseline[row["filter"]]["std"] ** 2 + row.magerr**2)
        for _, row in phot.iterrows()
    ]
    phot = phot.assign(significance=significance)
    idx = phot.significance.idxmax()
    return float(phot.significance.loc[idx]), phot.loc[idx]


def vet_bbh(
    target_id: int,
    nonlocalized_event_name: Optional[str] = None,
    param_ranges: dict = PARAM_RANGES,
):
    logger.info("Running BBH vetting (AGN-flare vetting)")

    # get the correct EventCandidate object for this target_id and nonlocalized event
    nonlocalized_event = NonLocalizedEvent.objects.get(event_id=nonlocalized_event_name)
    event_candidate = EventCandidate.objects.get(
        nonlocalizedevent_id=nonlocalized_event.id, target_id=target_id
    )
    target = Target.objects.get(id=target_id)

    ## check skymap association
    if np.isfinite(param_ranges["t_post"]):
        gw_disc_date = (
            EventSequence.objects.filter(  # GW discovery time
                nonlocalizedevent_id=nonlocalized_event.id
            )
            .last()
            .details["time"]
        )
        max_time = Time(gw_disc_date) + TimeDelta(param_ranges["t_post"] * u.day)
    else:  # just use current time
        max_time = Time.now()
    skymap_score = skymap_association(
        nonlocalized_event_name, target_id, max_time=max_time
    )
    update_score_factor(event_candidate, "skymap_score", skymap_score)

    ## get dataframes of potential hosts / AGN
    host_df, agn_df = vet_basic(event_candidate.target.id)

    ## host-nuclear-offset scoring: how close is the candidate to its best-matched
    ## host's nucleus? host_df already comes back sorted by ascending Pcc (best match
    ## first). Uses the unfiltered host_df since offset doesn't depend on redshift.
    nuclear_anchor = param_ranges["nuclear_offset_anchor"].to(u.arcsec).value
    if len(host_df):
        offset = host_df.iloc[0].offset
        if offset is not None and np.isfinite(offset):
            host_nuclear_score = (
                1.0
                if offset <= 0
                else _clamp(nuclear_anchor / offset, PHOT_SCORE_MIN, 1.0)
            )
            update_score_factor(event_candidate, "host_nuclear_score", host_nuclear_score)
        else:
            delete_score_factor(event_candidate, "host_nuclear_score")
    else:
        # no host found at all -- don't bias the score, consistent with how
        # host_distance_score is left neutral below when no host is found
        delete_score_factor(event_candidate, "host_nuclear_score")

    # some cleanup before distance scoring
    if len(host_df): ### TODO: these are filler values, should just change them to nulls in our database
        host_df = host_df[host_df.z != -99.0] # LS DR9 North
        host_df = host_df[host_df.z != -999.0] # PS1-STRM
        host_df = host_df[host_df.z != -9999.0] # SDSS DR12 photo-z
        host_df = host_df[~np.isnan(host_df.z)]

    ## distance scoring
    if target.redshift is not None and not np.isnan(target.redshift):
        # use target redshift, so no need to compute distance scores for galaxies
        host_score, host_name = get_distance_score(
            host_df, target_id, nonlocalized_event_name
        )
        update_score_factor(event_candidate, "host_distance_score", host_score)

    elif len(host_df) != 0:
        # then run the distance comparison for each of these hosts
        host_df = host_distance_match(host_df, target_id, nonlocalized_event_name)

        host_score, host_name, host_catalog = get_distance_score(
            host_df, target_id, nonlocalized_event_name
        )
        update_score_factor(event_candidate, "host_distance_score", host_score)
        update_score_factor(event_candidate, "host_name", host_name)
        update_score_factor(event_candidate, "host_catalog", host_catalog)

    else:
        # if no target redshift is known and no hosts are found, we don't want
        # to bias the final score (host may just be too far / undetected)
        delete_score_factor(event_candidate, "host_distance_score")
        delete_score_factor(event_candidate, "host_name")
        delete_score_factor(event_candidate, "host_catalog")

    ## AGN-catalog boost: association can only help, never hurt, since a missing
    ## Milliquas/RomaBzcat match may just mean that AGN hasn't been ingested yet
    if len(agn_df) != 0:
        agn_score = max(1.0, param_ranges["agn_boost_multiplier"])
    else:
        agn_score = 1.0
    update_score_factor(event_candidate, "agn_score", agn_score)

    ## photometric flare scoring
    prephot = _get_pre_disc_phot(
        target_id=target.id,
        nonlocalized_event=nonlocalized_event,
        t_pre=param_ranges["t_pre"],
    )
    baseline = fit_agn_baseline(prephot, min_baseline_pts=param_ranges["min_baseline_pts"])

    postphot = _get_post_disc_phot(
        target_id=target_id,
        nonlocalized_event=nonlocalized_event,
        t_post=param_ranges["t_post"],
        t_pre=param_ranges["t_pre"],
    )
    max_significance, _flare_row = detect_flare(
        postphot, baseline, sigma_thresh=param_ranges["flare_sigma_thresh"]
    )

    if baseline and postphot is not None and len(postphot) and np.isfinite(max_significance):
        agn_flare_score = (
            1.0
            if max_significance >= param_ranges["flare_sigma_thresh"]
            else PHOT_SCORE_MIN
        )
        update_score_factor(event_candidate, "agn_flare_score", agn_flare_score)
    else:
        # not enough baseline and/or post-merger photometry to judge either way --
        # don't bias the score
        delete_score_factor(event_candidate, "agn_flare_score")
