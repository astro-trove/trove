import logging
from typing import Optional, Tuple
from astropy.time import Time, TimeDelta
from astropy import units as u
import numpy as np
import pandas as pd
from scipy.stats import norm

from .scoring import (
    host_distance_match,
    get_distance_score,
    skymap_association,
    clean_host_df,
    _localization_from_name,
    classification_score
)
from .models import ScoreFactor
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

# Darc et al. 2025 (arXiv:2506.02224) follow every BBH-in-AGN-disc model to
# 400 d after the merger
AGN_FLARE_HORIZON_DAYS = 400

PARAM_RANGES = dict(
    t_pre=0,
    t_post=AGN_FLARE_HORIZON_DAYS,
    min_baseline_pts=2,  # n=1 gives MAD=0, which inflates flare significance
    flare_sigma_thresh=3.25,
    flare_pair_max_gap_days=5.0, # an adjacent observed night this close must confirm the flare
    flare_corroboration_window_days=0.5, # Half-width, in days, of the window an epoch must be corroborated within.
    flare_score_center_frac=1.0,  # sigmoid midpoint, as a fraction of flare_sigma_thresh
    flare_score_width_frac=0.25,  # sigmoid width, as a fraction of flare_sigma_thresh
    agn_match_score=1.0,
    agn_miss_score=0.1,
)


KPC_PER_ARCSEC_PER_MPC = 4.84813681e-3  # kpc per arcsec at 1 Mpc
NUCLEAR_SCALE_KPC = 0.5
ASTROMETRIC_PRECISION_ARCSEC = 2.0


def _robust_scatter(mags: np.ndarray, errs: np.ndarray) -> Tuple[float, float]:
    median_mag = float(np.median(mags))
    # 1.4826 rescales the MAD to a Gaussian sigma for normally distributed data
    robust_std = 1.4826 * float(np.median(np.abs(mags - median_mag)))
    median_err = float(np.median(errs))
    return max(robust_std, median_err), median_err


def fit_agn_baseline(
    prephot: Optional[pd.DataFrame], min_baseline_pts: int = 2,
) -> dict:
    # baseline has keys of filters, and the values are a dictionary that
    # includes the median magnitude, and a measure of the deviation of points
    # which is photometric errors or adjusted-MAD
    baseline = {}
    # prephot is all of the photometry data before GW detection
    if prephot is None or not len(prephot):
        return baseline

    phot = prephot[~prephot.upperlimit]
    for filt, group in phot.groupby("filter"):
        mags = group.mag.to_numpy(dtype=float)
        if len(mags) < min_baseline_pts:
            continue
        median_mag = float(np.median(mags))
        robust_std, median_err = _robust_scatter(
            mags, group.magerr.to_numpy(dtype=float)
        )
        baseline[filt] = dict(
            mag=median_mag, std=robust_std, n=int(len(mags)), median_err=median_err,
        )
    return baseline


def _corroborated_significance(
    phot: pd.DataFrame, window_days: float
) -> np.ndarray:
    sig = phot.significance.to_numpy(dtype=float)

    times = None
    for col in ("mjd", "dt"):
        if col in phot.columns:
            times = phot[col].to_numpy(dtype=float)
            break
    if times is None:
        # Only hand-built frames reach this; real photometry always carries mjd.
        return sig

    filters = phot["filter"].to_numpy()
    out = np.empty(sig.size, dtype=float)
    for i in range(sig.size):
        near = (filters == filters[i]) & (np.abs(times - times[i]) <= window_days)
        out[i] = np.median(sig[near])
    return out


def _flare_significance_series(
    postphot: Optional[pd.DataFrame],
    baseline: dict,
    corroboration_window_days: float = 0.5,
) -> Optional[pd.DataFrame]:
    # Returns list of photometry points --> z-scores compared to pre-merger baseline
    if postphot is None or not len(postphot) or not baseline:
        return None

    phot = postphot[~postphot.upperlimit]
    phot = phot[phot["filter"].isin(baseline.keys())]
    if not len(phot):
        return None

    # Adding photometry error with baseline's error in quadrature
    # significance is just a z-score
    significance = [
        (baseline[row["filter"]]["mag"] - row.mag)
        / np.sqrt(baseline[row["filter"]]["std"] ** 2 + row.magerr**2)
        for _, row in phot.iterrows()
    ]
    phot = phot.assign(significance=significance)
    return phot.assign(
        significance_corroborated=_corroborated_significance(
            phot, corroboration_window_days
        )
    )


def detect_flare(
    postphot: Optional[pd.DataFrame],
    baseline: dict,
    corroboration_window_days: float = 0.5,
    pair_max_gap_days: float = 0.0,
):
    # each night's peak corroborated significance, capped by its better adjacent observed
    # night when one lies within pair_max_gap_days, and an isolated night counts as it is.
    phot = _flare_significance_series(postphot, baseline, corroboration_window_days)
    if phot is None:
        return np.nan, None
    night = np.floor(phot.mjd.to_numpy(dtype=float)) if "mjd" in phot.columns else np.arange(len(phot))
    peaks = phot.significance_corroborated.groupby(night).idxmax()  # sorted by night
    nights = peaks.index.to_numpy(dtype=float)
    nightly = phot.significance_corroborated.loc[peaks].to_numpy()
    best, limiting = -np.inf, 0
    for i in range(len(nightly)):
        near = [j for j in (i - 1, i + 1)
                if 0 <= j < len(nightly) and abs(nights[j] - nights[i]) <= pair_max_gap_days]
        j = max(near, key=lambda n: nightly[n]) if near else i
        weaker = i if nightly[i] <= nightly[j] else j
        if nightly[weaker] > best:
            best, limiting = nightly[weaker], weaker
    return float(best), phot.loc[peaks.iloc[limiting]]


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

def agn_association_score(agn_df, match: float = 1.0, miss: float = 0.1) -> float:
    return float(match) if agn_df is not None and len(agn_df) else float(miss)


def projected_offset_kpc(offset_arcsec: float, angular_diameter_distance_mpc: float) -> float:
    return float(offset_arcsec) * float(angular_diameter_distance_mpc) * KPC_PER_ARCSEC_PER_MPC


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


def vet_bbh(
    target_id: int,
    nonlocalized_event_name: Optional[str] = None,
    param_ranges: dict = PARAM_RANGES,
):
    logger.info("Running BBH vetting (AGN-flare vetting)")

    nonlocalized_event = NonLocalizedEvent.objects.get(event_id=nonlocalized_event_name)
    event_candidate = EventCandidate.objects.get(
        nonlocalizedevent_id=nonlocalized_event.id, target_id=target_id
    )
    target = Target.objects.get(id=target_id)

    # Store all required updates/deletes and then does all of them at once.
    # Removes the latency of reaching the tunnel everytime
    pending_updates: dict = {}
    pending_deletes: set = set()

    def update_score_factor(_event_candidate, key, value):
        pending_deletes.discard(key)
        pending_updates[key] = value

    def delete_score_factor(_event_candidate, key):
        pending_updates.pop(key, None)
        pending_deletes.add(key)

    def _flush_score_factors():
        if pending_deletes:
            ScoreFactor.objects.filter(
                event_candidate=event_candidate, key__in=pending_deletes
            ).delete()
            pending_deletes.clear()
        if pending_updates:
            ScoreFactor.objects.bulk_create(
                [ScoreFactor(event_candidate=event_candidate, key=k, value=v)
                 for k, v in pending_updates.items()],
                update_conflicts=True, unique_fields=["event_candidate", "key"],
                update_fields=["value"],
            )
            pending_updates.clear()

    class_score = classification_score(getattr(target, "classification", None))
    update_score_factor(event_candidate, "classification_score", class_score)
    if class_score == 0:
        _flush_score_factors()
        logger.info(
            "BBH vetting: %s is classified %r, a supernova; scores 0 and the rest of "
            "the sub-scores are skipped", target.name, target.classification,
        )
        return

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
        nonlocalized_event_name, target_id, max_time=max_time # type: ignore
    )
    update_score_factor(event_candidate, "skymap_score", skymap_score)

    localization = _localization_from_name(nonlocalized_event_name, max_time=max_time)
    update_score_factor(event_candidate, "localization_id", localization.id)

    # stop_on_zero=False: a point-source match shouldn't stop AGN-flare vetting
    host_df, agn_df, keep_vetting = vet_basic(
        event_candidate.target.id, stop_on_zero=False
    )
    if not keep_vetting:
        _flush_score_factors()
        return

    agn_score = agn_association_score(
        agn_df, param_ranges["agn_match_score"], param_ranges["agn_miss_score"]
    )
    update_score_factor(event_candidate, "agn_score", agn_score)

    host_df = clean_host_df(host_df)
    if target.redshift is not None and not np.isnan(target.redshift):
        host_score, host_name = get_distance_score(
            host_df, target_id, nonlocalized_event_name
        )
        update_score_factor(event_candidate, "host_distance_score", host_score)
    elif len(host_df) != 0:
        host_df = host_distance_match(host_df, target_id, nonlocalized_event_name)

        host_score, host_name, host_catalog = get_distance_score(
            host_df, target_id, nonlocalized_event_name
        )
        update_score_factor(event_candidate, "host_distance_score", host_score)
        # a catalog row can be missing either, and ScoreFactor.value is not nullable
        for key, value in (("host_name", host_name), ("host_catalog", host_catalog)):
            if value is None or (isinstance(value, float) and np.isnan(value)):
                delete_score_factor(event_candidate, key)
            else:
                update_score_factor(event_candidate, key, value)
    else:

        delete_score_factor(event_candidate, "host_distance_score")
        delete_score_factor(event_candidate, "host_name")
        delete_score_factor(event_candidate, "host_catalog")

    prephot = _get_pre_disc_phot(
        target_id=target.id,
        nonlocalized_event=nonlocalized_event,
        t_pre=param_ranges["t_pre"],
    )
    postphot = _get_post_disc_phot(
        target_id=target_id,
        nonlocalized_event=nonlocalized_event,
        t_post=param_ranges["t_post"],
        t_pre=param_ranges["t_pre"],
    )
    ## photometric flare scoring
    baseline = fit_agn_baseline(
        prephot, min_baseline_pts=param_ranges["min_baseline_pts"]
    )
    for filt, entry in baseline.items():
        update_score_factor(event_candidate, f"baseline_mag_{filt}", entry["mag"])
        update_score_factor(event_candidate, f"baseline_std_{filt}", entry["std"])
    max_significance, flare_row = detect_flare(
        postphot, baseline,
        corroboration_window_days=param_ranges["flare_corroboration_window_days"],
        pair_max_gap_days=param_ranges["flare_pair_max_gap_days"],
    )

    agn_flare_score = None
    # NaN means no usable photometry, so no verdict
    if baseline and postphot is not None and len(postphot) and np.isfinite(max_significance):
        agn_flare_score = flare_confidence_score(
            max_significance,
            param_ranges["flare_sigma_thresh"],
            floor=PHOT_SCORE_MIN,
            center_frac=param_ranges["flare_score_center_frac"],
            width_frac=param_ranges["flare_score_width_frac"],
        )
        update_score_factor(event_candidate, "agn_flare_score", agn_flare_score)
    else:
        # not enough baseline, post-merger photometry, or post-merger nights
        delete_score_factor(event_candidate, "agn_flare_score")

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
        delete_score_factor(event_candidate, "nuclear_offset_score")
    else:
        update_score_factor(event_candidate, "nuclear_offset_score", offset_score)

    _flush_score_factors()

    logger.info(
        "BBH vetting: agn=%.2f flare=%s nuclear_offset=%s",
        agn_score, agn_flare_score, offset_score,
    )
