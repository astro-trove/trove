"""
The "pipeline" to vet candidate counterparts to nonlocalized BBH events based on
their resemblance to an AGN flare: a brightening of a pre-existing, persistently
variable AGN rather than a fresh transient.

Reuses the skymap / host / AGN-catalog / distance scoring machinery from the KN-style
vetters (see vet_bns.py) as-is. Most of that reused machinery, plus `agn_score` and the
short-delay/long-delay flare-timescale split behind `t_post`, is not a new design here
-- it is this same collaboration's own published methodology, Vieira et al. 2026 (the
S251112cm follow-up paper, arXiv:2603.17009), Appendix A "Vetting for BBH
Merger-Induced AGN Flaring" and Section 4.1. What *is* new relative to that paper:

1. `agn_score` reproduces Vieira+2026's S_AGN exactly: a Milliquas (Flesch 2023) match
   within 2" is supporting evidence when vetting for AGN-flares (it can only help, not
   disqualify, since a *missing* match may just mean that AGN hasn't been ingested
   yet) -- inverted from S_AGN for KNe/KN-in-SNe/super-KNe, where an AGN match instead
   disqualifies the candidate. Vieira+2026 uses a hard 0/1 S_AGN; the x5
   `agn_boost_multiplier` used here instead of a flat 1.0 is TROVE's own choice, not
   from the paper.
2. `host_nuclear_score`: how close the candidate sits to its best-matched host's
   nucleus, scored continuously via `nuclear_offset_score` (a smooth score/(scale+
   offset) falloff, no plateau/hard-cut) rather than Vieira+2026's binary "<2 arcsec"
   S_AGN gate. The half-credit scale stays anchored at Vieira+2026's own 2" (~1 kpc at
   their ~93 Mpc event), for a reason that took a second literature pass to get right:
   the *true* physical offset of a BBH-induced AGN flare from the SMBH is far below
   arcsec resolution at any realistic GW host distance, so there's no case for
   tightening past 2" -- see the note on `nuclear_offset_scale` below.
3. `agn_flare_score`: the photometric anomaly-detection piece. Vieira+2026 explicitly
   did *not* build this -- "it is challenging to impose robust constraints on the
   luminosities and timescales of BBH-induced AGN flares" (Appendix A), and their one
   AGN-associated candidate with S_AGN-flare > 0 (S251112cm X3) used an unconstrained
   S_phot,AGN-flare = 1 (i.e. photometry wasn't actually scored: 0.62 x 0.21 x 1 x 1 x
   1 = 0.13, matching their reported score). This module's `fit_agn_baseline`/
   `detect_flare`/`flare_confidence_score` is TROVE's attempt to actually fill that
   gap: model-agnostic, since no AGN intrinsic-variability model (DRW, CARMA, PSD,
   etc.) has been fit here -- it just characterizes the empirical pre-merger scatter
   per filter (robust median + MAD) and scores a *brightening* excursion above that
   envelope within the post-merger window continuously via a normal-CDF sigmoid in
   significance, rather than a hard pass/fail at 5-sigma. Distinguishing a genuine BBH
   flare from ordinary AGN variability from photometry alone is still an open problem
   -- spectroscopy (asymmetric broadening of emission lines) is the actual
   literature-endorsed discriminant, which is out of scope for TROVE's automated
   pipeline.
4. `flare_shape_score` (NOT model-agnostic, excluded from the default score): goes one step past "is there an
   excursion" (`agn_flare_score`) to "is its *timing* consistent with a real
   BBH-flare mechanism". Positional offset from the nucleus turned out not to be a
   usable photometric-independent discriminant (point 2 above) since the true offset
   is unresolvable, so this leans on photometry instead, per Darc et al. 2025 (PhRvD
   112, 063019 -- the same paper behind the short-delay/long-delay split in `t_post`,
   which actually fits three physical emission-mechanism models against real
   long-term photometry of a GW/AGN-flare candidate, S231206cc): McKernan et al. 2019
   (ram-pressure-stripped Hill sphere), Rodriguez-Ramirez et al. 2025 (jet-cocoon
   emergence), and Tagawa et al. 2024 (jet breakout + shock cooling).

   Unlike `agn_flare_score`, this is explicitly *not* model-agnostic, and it's worth
   being precise about why: it only scores a candidate well if its (delay, duration)
   falls near the envelope one of these three *specific* papers happened to predict
   from *their own* chosen parameter grids (particular kick-velocity ranges,
   particular SMBH-mass ranges). A real flare from a mechanism outside these three,
   or from parameters outside what these authors explored, would be penalized here
   even though `agn_flare_score` wouldn't care. (Even Darc+2025's own method is a
   hybrid, not purely agnostic: a model-agnostic ~10-20% flux-amplitude gate,
   methodologically close to this module's MAD-based `agn_flare_score`, followed by
   model-specific timing checks -- so "adopt Darc+2025's method" and "stay
   model-agnostic" are in tension, not the same thing.) Because of that, this factor
   is always computed and stored (it's cheap -- no external calls), but kept out of
   the default score product: `scoring/util.get_event_candidate_scores`'
   `flare_shape_toggle` (default False) decides at *read* time whether it's
   included, exactly the way `agn_toggle` decides whether `agn_score` is -- not a
   vet-time flag here, so a user can flip it live from the BBH scoring-adjustments
   panel without triggering a re-vet. See `flare_shape_score`'s docstring for the
   exact bounds and their provenance.

   Caveat found by validating against realistic TROVE-grade photometry (sparse
   ATLAS-forced-photometry-like cadence/depth, not ZTF-partnership-grade data --
   see the validation script referenced in the PR/commit this was added in): two of
   the three models' delay windows (mck19, tgw24) span the *entire* 0-300 day
   post-merger range, so `flare_shape_score` returns ~1.0 for essentially any
   post-merger detection, real excursion or pure noise, once a delay/duration can be
   estimated at all. It has little power to reject noise on its own; its practical
   value is as a secondary "which mechanism, if any, is favored" annotation on top
   of `agn_flare_score` (which does carry real discriminating power -- see that
   validation), not as an independent gate.

`nuclear_offset_scale` (2.0"): kept at Vieira+2026's own S_AGN radius rather than
tightened to the sub-arcsec nuclear-vs-off-nuclear boundaries used in local SN/AGN
morphological classification (e.g. Sanders et al. 2015, PS1-MDS, arXiv:1501.01314,
~0.26"/~0.48" nuclear/off-nuclear populations with a ~0.54" dividing line). That
literature answers a different question -- resolved host morphology in nearby,
well-sampled surveys -- than what actually matters here: how far from the SMBH can a
BBH-induced flare *physically* occur? McKernan et al. 2019 (ApJL 884, L50,
doi:10.3847/2041-8213/ab4886)'s ram-pressure-stripped-Hill-sphere channel puts the
off-center flare at ~1e3 gravitational radii, which for a 1e6-1e8 Msun SMBH is order
10-1e3 AU -- and Rodriguez-Ramirez et al. 2025 (PhRvD 111, 083020,
doi:10.1103/PhysRevD.111.083020)'s disk-wind/kick-angle model likewise displaces the
remnant by a small fraction of the local disk scale height before jet formation. Both
are many orders of magnitude below the ~kpc scale that 1" subtends at any plausible GW
host distance (tens-hundreds of Mpc), so no currently modeled BBH-flare mechanism
predicts an offset that arcsec-scale astrometry could actually resolve as
"off-nucleus". A resolved offset therefore argues *against* nuclear origin rather than
being a fine discriminator near zero, and there's no theoretical support for scoring
sub-arcsec offsets any differently from offset=0; 2" (matching `agn_score`'s own
radius) stays a generously-inclusive, not overly tight, envelope.

`t_post` (400 days): Vieira+2026 (Appendix A) cites Darc et al. 2025 (PhRvD 112,
063019, doi:10.1103/6rg8-2xxz) for a two-population classification of BBH-AGN flares
by delay time -- "short-delay flares are expected to occur within <~50 days of the
merger and are typically associated with relatively short durations. In contrast,
long-delay flares can peak between ~50 and ~400 days, and last longer." 400 days is
therefore the literature-motivated upper edge of the long-delay population, not an
arbitrary wide net: it's sized to catch both the fast "kicked remnant punches through
the disk" channel (days-to-weeks; Kimura et al. 2021, ApJ 916, 111,
doi:10.3847/1538-4357/ac0535; McKernan et al. 2019 above; the O3 ZTF systematic
search of Graham et al. 2023, ApJ 942, 99, doi:10.3847/1538-4357/aca480, used a
tighter <=60 day window for exactly this short-delay population) and the slower
disk-response/viscous-afterglow channel (Rodriguez-Ramirez et al. 2023, MNRAS 527,
6076, doi:10.1093/mnras/stad3575; Tagawa et al. 2024, ApJ 966, 21,
doi:10.3847/1538-4357/ad2e0b; Rodriguez-Ramirez et al. 2025 above -- their disk-wind
parameter study finds peak times of ~10-80 days for v_k = 200-400 km/s,
M_SMBH = 1e6-1e7 Msun). The tradeoff is that the longer window also gives ordinary AGN
variability more time to produce a false positive in `agn_flare_score`; that's an
open recall-vs-precision question, not one the literature settles further.

References
----------
Darc et al. 2025, PhRvD 112, 063019, doi:10.1103/6rg8-2xxz
Graham et al. 2020, PhRvL 124, 251102, doi:10.1103/PhysRevLett.124.251102
Graham et al. 2023, ApJ 942, 99, doi:10.3847/1538-4357/aca480
Kimura, Murase & Bartos 2021, ApJ 916, 111, doi:10.3847/1538-4357/ac0535
McKernan et al. 2019, ApJL 884, L50, doi:10.3847/2041-8213/ab4886
Rodriguez-Ramirez et al. 2023, MNRAS 527, 6076, doi:10.1093/mnras/stad3575
Rodriguez-Ramirez, Nemmen & Bom 2025, PhRvD 111, 083020, doi:10.1103/PhysRevD.111.083020
Sanders et al. 2015 (PS1-MDS), arXiv:1501.01314
Tagawa et al. 2024, ApJ 966, 21, doi:10.3847/1538-4357/ad2e0b
Vieira et al. 2026, arXiv:2603.17009 ("Search For a Counterpart to the Subsolar Mass
    Gravitational Wave Candidate S251112cm" -- this collaboration's own paper)
"""

import logging
from typing import Optional
from astropy.time import Time, TimeDelta
from astropy import units as u
import numpy as np
import pandas as pd
from scipy.stats import norm

from .scoring import (
    update_score_factor,
    delete_score_factor,
    host_distance_match,
    get_distance_score,
    skymap_association,
    clean_host_df,
    _localization_from_name,
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
    t_post=400,  # flare window: 0-400 days after the GW trigger -- the upper edge
    # of the "long-delay" flare population of Darc et al. 2025 (PhRvD 112, 063019),
    # as reported in Vieira et al. 2026 (arXiv:2603.17009) Appendix A; see module
    # docstring for the short-delay (<=~50 day) vs. long-delay (~50-400 day) split
    min_baseline_pts=5,  # minimum pre-trigger points in a filter to trust its baseline
    flare_sigma_thresh=5.0,  # reference significance for "confident flare" in
    # flare_confidence_score, matches PREDETECTION_SNR_THRESHOLD's 5-sigma
    # convention elsewhere in vet_phot.py
    flare_score_center_frac=0.5,  # sigmoid midpoint, as a fraction of flare_sigma_thresh
    flare_score_width_frac=0.25,  # sigmoid transition width, as a fraction of flare_sigma_thresh
    nuclear_offset_scale=2.0 * u.arcsec,  # half-credit offset in nuclear_offset_score;
    # matches agn_score's own Milliquas match radius (Vieira et al. 2026 Section 4.1,
    # ~1 kpc at their ~93 Mpc event) -- see module docstring for why this is *not*
    # tightened further despite sub-arcsec thresholds appearing elsewhere in the
    # nuclear-transient literature
    agn_boost_multiplier=5.0,
)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def nuclear_offset_score(offset: float, scale: float, floor: float = PHOT_SCORE_MIN) -> float:
    """
    Continuously graded "is this candidate at its host's nucleus" score.

    score = scale / (scale + offset): 1.0 at offset=0, 0.5 at offset=scale, falling
    off smoothly (no plateau, no hard cut) and floored at `floor` for very large
    offsets. See the module docstring for how `scale` (nuclear_offset_scale) was
    chosen from the literature.

    Parameters
    ----------
    offset : float
        Angular offset from the candidate to its best-matched host, in arcsec.
        Negative values (shouldn't occur, but not worth erroring over) are treated
        as zero.
    scale : float
        The half-credit offset, in arcsec.
    """
    offset = max(offset, 0.0)
    return _clamp(scale / (scale + offset), floor, 1.0)


def flare_confidence_score(
    significance: float,
    thresh: float,
    floor: float = PHOT_SCORE_MIN,
    center_frac: float = 0.5,
    width_frac: float = 0.25,
) -> float:
    """
    Map a brightening significance (in sigma, as returned by `detect_flare`) onto a
    continuous [floor, 1.0] confidence score via a normal-CDF sigmoid, instead of a
    hard pass/fail cut at `thresh`.

    The sigmoid is centered at `center_frac * thresh` with transition width
    `width_frac * thresh`, so with the defaults a significance of 0 sits near
    `floor`, `thresh` itself sits near (but not at) the ceiling, and significances
    well above `thresh` saturate at 1.0 -- deliberately similar to the old hard-cut
    behavior at exactly `thresh`, but without the discontinuity.
    """
    center = center_frac * thresh
    width = max(width_frac * thresh, 1e-6)
    raw = norm.cdf(significance, loc=center, scale=width)
    return _clamp(floor + (1.0 - floor) * raw, floor, 1.0)


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


def _flare_significance_series(
    postphot: Optional[pd.DataFrame], baseline: dict
) -> Optional[pd.DataFrame]:
    """
    Shared helper for `detect_flare` and `estimate_flare_extent`: attach a
    brightening-significance column (in sigma, baseline vs. observed mag) to every
    detection in `postphot` whose filter has a fitted `baseline`. Returns None if
    nothing qualifies.
    """
    if postphot is None or not len(postphot) or not baseline:
        return None

    phot = postphot[~postphot.upperlimit]
    phot = phot[phot["filter"].isin(baseline.keys())]
    if not len(phot):
        return None

    significance = [
        (baseline[row["filter"]]["mag"] - row.mag)
        / np.sqrt(baseline[row["filter"]]["std"] ** 2 + row.magerr**2)
        for _, row in phot.iterrows()
    ]
    return phot.assign(significance=significance)


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
    phot = _flare_significance_series(postphot, baseline)
    if phot is None:
        return np.nan, None
    idx = phot.significance.idxmax()
    return float(phot.significance.loc[idx]), phot.loc[idx]


def estimate_flare_extent(
    postphot: Optional[pd.DataFrame],
    baseline: dict,
    extent_sigma_thresh: float = 3.0,
):
    """
    Estimate a candidate flare's delay-from-merger and duration, for comparing
    against the timing envelopes of published BBH-flare emission models
    (`flare_shape_score`).

    `postphot` must carry a `dt` column (days since the GW trigger), as produced by
    `vet_phot._get_post_disc_phot`.

    Returns
    -------
    (delay_days, duration_days) : delay_days is `dt` of the single most significant
    brightening point (same point `detect_flare` would report). duration_days is the
    span between the earliest and latest points *anywhere* in postphot with
    significance >= `extent_sigma_thresh` -- deliberately looser than
    `detect_flare`'s usual 5-sigma detection threshold, since this is meant to
    bracket the whole elevated episode rather than find its single most significant
    point. duration_days is None (rather than 0) when fewer than two such points
    exist, since ground-based survey cadence is usually too sparse to trust a
    duration estimate from a single point -- `flare_shape_score` treats that as
    "unconstrained", not "instantaneous". Both are None if no flare is detectable at
    all (mirrors `detect_flare`'s (nan, None) for "no data", but None here since
    delay/duration aren't naturally NaN-typed floats read back out of ScoreFactor).
    """
    phot = _flare_significance_series(postphot, baseline)
    if phot is None:
        return None, None

    idx = phot.significance.idxmax()
    delay_days = float(phot.dt.loc[idx])

    elevated = phot[phot.significance >= extent_sigma_thresh]
    if len(elevated) < 2:
        return delay_days, None
    duration_days = float(elevated.dt.max() - elevated.dt.min())
    return delay_days, duration_days


# (delay_lo, delay_hi, duration_lo, duration_hi) envelopes, in observed-frame days
# since the GW trigger, for the three BBH-in-AGN-disk emission mechanisms Darc et al.
# 2025 (PhRvD 112, 063019) fit against real long-term photometry of a GW/AGN-flare
# candidate (S231206cc). Bounds are deliberately generous envelopes around each
# paper's own quoted numbers, not sharp physical limits -- ground-based survey
# cadence isn't good enough to trust more precision than "roughly consistent with
# this channel":
#   mck19  -- McKernan et al. 2019 (ApJL 884, L50): ram-pressure-stripped Hill
#             sphere. Delay ranges from <3 days (kick velocity v_k >~ 500 km/s) to
#             ~300 days (v_k <~ 100 km/s); duration ~1-100 days.
#   jrr_i  -- Rodriguez-Ramirez et al. 2025 (PhRvD 111, 083020): jet-cocoon thermal
#             diffusion. Needs v_k >~ 200 km/s to form an efficient jet at all; delay
#             ~50-100+ days, duration ~20-100+ days (both open-ended upward, capped
#             here at a generous but finite value).
#   tgw24  -- Tagawa et al. 2024 (ApJ 966, 21): jet breakout + shock cooling. Delay
#             favors <~50 days for close-in mergers (<0.005 pc, though those are
#             usually too short to detect) out to 40-300 days for ~1 pc mergers;
#             duration ~10-200+ days.
FLARE_SHAPE_MODELS = dict(
    mck19=dict(delay=(0.0, 300.0), duration=(1.0, 100.0)),
    jrr_i=dict(delay=(50.0, 150.0), duration=(20.0, 150.0)),
    tgw24=dict(delay=(0.0, 300.0), duration=(10.0, 250.0)),
)


def _box_edge_score(x: float, lo: float, hi: float, margin: float, floor: float) -> float:
    """1.0 inside [lo, hi]; falls off smoothly outside via margin/(margin+excess)."""
    if lo <= x <= hi:
        return 1.0
    excess = (lo - x) if x < lo else (x - hi)
    return _clamp(margin / (margin + excess), floor, 1.0)


def flare_shape_score(
    delay_days: float,
    duration_days: Optional[float],
    models: dict = FLARE_SHAPE_MODELS,
    floor: float = PHOT_SCORE_MIN,
) -> float:
    """
    Score how consistent an observed flare's timing is with *any one* of the
    published BBH-in-AGN-disk emission models in `models`, rather than a single
    one-size-fits-all cut -- TROVE has no way to know a priori which mechanism (if
    any) applies to a given candidate, since that depends on kick velocity, SMBH
    mass, and merger location within the disk. Takes the best-fitting model's score,
    not a penalized combination across all three.

    Each model's fit is `delay_score * duration_score`, each of which is 1.0 inside
    that model's envelope and falls off smoothly (not a hard cut) outside it, via
    `_box_edge_score` with the box's own width as the falloff scale.

    `duration_days` may be None (too little post-merger photometry above the
    extent-detection threshold in `estimate_flare_extent` to bracket a span) -- in
    that case only delay is checked, since a single data point can't rule a model's
    duration range in or out.
    """
    best = floor
    for model in models.values():
        delay_lo, delay_hi = model["delay"]
        score = _box_edge_score(delay_days, delay_lo, delay_hi, delay_hi - delay_lo, floor)
        if duration_days is not None:
            dur_lo, dur_hi = model["duration"]
            score *= _box_edge_score(duration_days, dur_lo, dur_hi, dur_hi - dur_lo, floor)
        best = max(best, score)
    return best


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

    # vet_bbh never writes these -- they're KN/KN-in-SN/super-KN-specific factors.
    # A candidate can carry them from an earlier vetting pass, e.g. from before its
    # event was (re)classified as BBH. AGN-flare's PARAM_RANGES has no lum_max/
    # peak_time/decay_rate bounds to check them against, so a leftover row here
    # crashes scoring/util.py's get_event_candidate_scores with a KeyError for
    # every candidate in that call, not just this one -- clear them out on every
    # BBH vetting pass so they can't linger.
    for stale_key in ("phot_peak_lum", "phot_peak_time", "phot_decay_rate"):
        delete_score_factor(event_candidate, stale_key)

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

    # the skymap and distance scores are only valid for one localization, so
    # record which one produced them (matches vet_kn.py / vet_super_kn.py)
    localization = _localization_from_name(nonlocalized_event_name, max_time=max_time)
    update_score_factor(event_candidate, "localization_id", localization.id)

    ## get dataframes of potential hosts / AGN
    host_df, agn_df, keep_vetting = vet_basic(event_candidate.target.id)
    if not keep_vetting:
        # PS or MPC association already zeroed this candidate's score and the
        # host/AGN associations were not (re)performed -- same as vet_kn.py
        return

    ## host-nuclear-offset scoring: how close is the candidate to its best-matched
    ## host's nucleus? host_df already comes back sorted by ascending Pcc (best match
    ## first). Uses the unfiltered host_df since offset doesn't depend on redshift.
    nuclear_scale = param_ranges["nuclear_offset_scale"].to(u.arcsec).value
    if len(host_df):
        offset = host_df.iloc[0].offset
        if offset is not None and np.isfinite(offset):
            host_nuclear_score = nuclear_offset_score(offset, nuclear_scale)
            update_score_factor(event_candidate, "host_nuclear_score", host_nuclear_score)
        else:
            delete_score_factor(event_candidate, "host_nuclear_score")
    else:
        # no host found at all -- don't bias the score, consistent with how
        # host_distance_score is left neutral below when no host is found
        delete_score_factor(event_candidate, "host_nuclear_score")

    # some cleanup before distance scoring
    host_df = clean_host_df(host_df)

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
        agn_score = param_ranges["agn_boost_multiplier"]
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
        agn_flare_score = flare_confidence_score(
            max_significance,
            param_ranges["flare_sigma_thresh"],
            floor=PHOT_SCORE_MIN,
            center_frac=param_ranges["flare_score_center_frac"],
            width_frac=param_ranges["flare_score_width_frac"],
        )
        update_score_factor(event_candidate, "agn_flare_score", agn_flare_score)

        # model-based layer: is the flare's timing consistent with a *specific*
        # published emission model's envelope? Not model-agnostic (see module
        # docstring point 4), so it is always computed and stored here but kept out
        # of the default score product -- scoring/util.get_event_candidate_scores'
        # flare_shape_toggle decides whether it's included, at read time, the same
        # way agn_toggle decides whether agn_score is. That -- not a vet-time
        # PARAM_RANGES flag -- is what lets a user flip it live without a re-vet.
        delay_days, duration_days = estimate_flare_extent(postphot, baseline)
        if delay_days is not None:
            shape_score = flare_shape_score(delay_days, duration_days)
            update_score_factor(event_candidate, "flare_shape_score", shape_score)
        else:
            delete_score_factor(event_candidate, "flare_shape_score")
    else:
        # not enough baseline and/or post-merger photometry to judge either way --
        # don't bias the score
        delete_score_factor(event_candidate, "agn_flare_score")
        delete_score_factor(event_candidate, "flare_shape_score")
