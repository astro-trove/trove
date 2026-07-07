"""
Small checker to exercise host_distance_match() / get_distance_score() against
real, already-ingested nonlocalized events, so you can debug the distance
scoring pipeline without going through the "Vet All" -> django_tasks worker
queue (whose print()/logging output lands in the worker process, not here).

Runs in-process, so any print() or logger calls inside scoring.scoring will
show up directly in this command's output.

Examples
--------
# quick sanity check: AT2017gfo has a known target redshift, so this should
# hit the get_distance_score() "known redshift" path directly and print a
# high (~1) distance score
python manage.py check_distance_scores --event GW170817

# check the first 15 candidates (by PCC/ingestion order) for a real O5 event
python manage.py check_distance_scores --event S251112cm --limit 15

# check one specific candidate by target name
python manage.py check_distance_scores --event S251112cm --target-name AT2025addb

# Full Run with Cache
python manage.py check_distance_scores --event S251112cm --from-cache /home/sopanda25/trove/out/distance_metric_bias_records.json --output /home/sopanda25/trove/out/distance_metric_bias 2>&1 | tail -10
"""

import json
import warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, LogNorm
from astropy import units as u

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from trove_targets.models import Target
from tom_nonlocalizedevents.models import EventCandidate, NonLocalizedEvent

from candidate_vetting.vet import host_association

from scipy.stats import norm, rankdata

from scoring.scoring import (
    cosmo, get_distance_score_diagnostic, host_distance_match,
    _distance_at_healpix
)


def _clean_host_df(host_df):
    """Same filler-value / NaN cleanup used in vet_kn_in_sn before scoring."""
    if not len(host_df):
        return host_df
    host_df = host_df[host_df.z != -99.0]  # LS DR9 North
    host_df = host_df[host_df.z != -999.0]  # PS1-STRM
    host_df = host_df[host_df.z != -9999.0]  # SDSS DR12 photo-z
    host_df = host_df[~np.isnan(host_df.z)]
    return host_df

CATEGORY_COLORS = {
    "redshift": "#2a78d6",
    "user-redshift": "#1baf7a",
    "spec-z": "#eda100",
    "photo-z": "#008300",
    "ind": "#4a3aa7",
}

METRIC_ORDER = [
    "bc_norm",
    "Consistent Probability",
    "Improved Consistent Probability",
]


def _plot_metric_boxplots(
    plotting_data, output_path, event, log_floor=1e-30, exclude_categories=None, metric_order=None
):
    exclude_categories = exclude_categories or set()
    metrics_present = [m for m in (metric_order or METRIC_ORDER) if m in plotting_data]

    n_cols = 2
    n_rows = -(-len(metrics_present) // n_cols)  # ceil division
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(11, 3.2 * n_rows), squeeze=False)

    for i, metric in enumerate(metrics_present):
        ax = axes[i // n_cols][i % n_cols]
        by_category = plotting_data[metric]
        cats_present = [
            c for c in CATEGORY_COLORS if c not in exclude_categories and by_category.get(c)
        ]

        if not cats_present:
            ax.axis("off")
            continue

        # log10(score): raw scores are heavily right-skewed (a dense cluster
        # near 0 plus a long tail down toward the floor), so a linear axis
        # collapses most boxes into an unreadable sliver at y=0
        # drop NaNs per category -- a handful of hosts (mostly photo-z, up to
        # ~5% for bc_norm) produce NaN scores (missing catalog z-uncertainty
        # propagating through the metric calculation)
        data = [
            np.log10(np.clip(np.asarray(by_category[c], dtype=float), log_floor, 1.0))
            for c in cats_present
        ]
        data = [d[~np.isnan(d)] for d in data]

        bp = ax.boxplot(
            data, patch_artist=True, widths=0.6,
            # 5th-95th percentile whiskers instead of the default 1.5*IQR rule
            whis=(5, 95),
            medianprops=dict(color="#0b0b0b", linewidth=1.5),
            flierprops=dict(marker="o", markersize=2, markerfacecolor="#898781", markeredgecolor="none", alpha=0.4),
        )
        for patch, cat in zip(bp["boxes"], cats_present):
            patch.set_facecolor(CATEGORY_COLORS[cat])
            patch.set_alpha(0.85)

        ax.set_xticks(range(1, len(cats_present) + 1))
        ax.set_xticklabels(cats_present, rotation=20, ha="right", fontsize=7)
        ax.set_title(metric, fontsize=10)
        ax.set_ylabel("log10(score)", fontsize=8)
        ax.tick_params(labelsize=7)

    for j in range(len(metrics_present), n_rows * n_cols):
        axes[j // n_cols][j % n_cols].axis("off")

    fig.suptitle(f"Distance-metric score distributions by host redshift type -- {event}", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path

# marker shape per z-type, keyed the same way as CATEGORY_COLORS/_bucket_z_type
# output -- shape carries z-type identity, color (below) separately carries
# distance uncertainty, so the two encodings don't collide
TYPE_MARKERS = {
    "redshift": "x",
    "user-redshift": "^",
    "spec-z": "o",
    "photo-z": "v",
    "ind": "s",
}

def _plot_log_score_vs_dist(records, output_path, event, log_floor=1e-30, metric_order=None):
    """
    Score vs. host distance, one panel per metric, color-coded by distance
    uncertainty (continuous, sequential colormap since uncertainty is a
    magnitude here, not a category) and marker-shaped by z-type.

    Tests whether score depends on *where* a host sits along the distance
    axis, and whether the wide-uncertainty bias shows up as a color gradient
    within a single panel (pale/wide-uncertainty points scoring differently
    than dark/tight ones at the same distance).
    """
    metrics_present = [m for m in (metric_order or METRIC_ORDER) if any(m in r for r in records)]

    cmap = LinearSegmentedColormap.from_list(
        "seq_blue", ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
    )
    # a linear color scale compresses almost everything into the pale end,
    # since uncertainty spans ~1 to several thousand Mpc -- use a log-scale
    # color normalization so the gradient is visible across the full range,
    # while the colorbar itself still shows real Mpc values, not log10
    all_uncertainty = np.clip([r["uncertainty"] for r in records if "distance" in r], 1e-2, None)
    vmin, vmax = np.percentile(all_uncertainty, [2, 98])  # robust range, ignore extreme outliers
    color_norm = LogNorm(vmin=max(vmin, 1e-2), vmax=vmax)

    n_cols = 2
    n_rows = -(-len(metrics_present) // n_cols)  # ceil division
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(11, 3.6 * n_rows), squeeze=False)

    mappable = None
    for i, metric in enumerate(metrics_present):
        ax = axes[i // n_cols][i % n_cols]
        usable = [r for r in records if metric in r and "distance" in r]
        if not usable:
            continue

        # one vectorized scatter call per z-type (not per point) -- cheap
        # even at ~2000 points, and keeps each category's marker shape fixed
        for z_type, marker in TYPE_MARKERS.items():
            cat_records = [r for r in usable if r["z_type"] == z_type]
            if not cat_records:
                continue
            xs = np.array([r["distance"] for r in cat_records])
            ys = np.log(np.clip([r[metric] for r in cat_records], log_floor, 1.0))
            cs = np.clip([r["uncertainty"] for r in cat_records], 1e-2, None)
            mappable = ax.scatter(
                xs, ys, c=cs, cmap=cmap, norm=color_norm, marker=marker,
                s=8, alpha=0.5, edgecolor="none", zorder=2,
            )

        ax.set_xscale("log")
        ax.set_title(metric, fontsize=10)
        ax.set_xlabel("distance (Mpc)", fontsize=8)
        ax.set_ylabel("log (score)", fontsize=8)
        ax.tick_params(labelsize=7)

    for j in range(len(metrics_present), n_rows * n_cols):
        axes[j // n_cols][j % n_cols].axis("off")

    fig.suptitle(f"Distance-metric score vs. host distance -- {event}", fontsize=13)
    fig.tight_layout(rect=[0, 0.05, 0.85, 0.96])

    if mappable is not None:
        cbar = fig.colorbar(mappable, ax=axes, fraction=0.03, pad=0.02)
        cbar.set_label("distance uncertainty (Mpc)", fontsize=9)

    # shape legend: neutral gray markers so it reads as identity, not
    # implying anything about the continuous uncertainty color scale
    shape_handles = [
        plt.Line2D([0], [0], marker=marker, color="none", markerfacecolor="none",
                   markeredgecolor="#52514e", markersize=7, linestyle="none")
        for marker in TYPE_MARKERS.values()
    ]
    fig.legend(
        shape_handles, TYPE_MARKERS.keys(), loc="lower center",
        ncol=len(TYPE_MARKERS), frameon=False,
    )

    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path

def _plot_score_vs_dist(records, output_path, event, log_floor=1e-30, metric_order=None):
    """
    Score vs. host distance, one panel per metric, color-coded by distance
    uncertainty (continuous, sequential colormap since uncertainty is a
    magnitude here, not a category) and marker-shaped by z-type.

    Tests whether score depends on *where* a host sits along the distance
    axis, and whether the wide-uncertainty bias shows up as a color gradient
    within a single panel (pale/wide-uncertainty points scoring differently
    than dark/tight ones at the same distance).
    """
    metrics_present = [m for m in (metric_order or METRIC_ORDER) if any(m in r for r in records)]

    cmap = LinearSegmentedColormap.from_list(
        "seq_blue", ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
    )
    # a linear color scale compresses almost everything into the pale end,
    # since uncertainty spans ~1 to several thousand Mpc -- use a log-scale
    # color normalization so the gradient is visible across the full range,
    # while the colorbar itself still shows real Mpc values, not log10
    all_uncertainty = np.clip([r["uncertainty"] for r in records if "distance" in r], 1e-2, None)
    vmin, vmax = np.percentile(all_uncertainty, [2, 98])  # robust range, ignore extreme outliers
    color_norm = LogNorm(vmin=max(vmin, 1e-2), vmax=vmax)

    n_cols = 2
    n_rows = -(-len(metrics_present) // n_cols)  # ceil division
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(11, 3.6 * n_rows), squeeze=False)

    mappable = None
    for i, metric in enumerate(metrics_present):
        ax = axes[i // n_cols][i % n_cols]
        usable = [r for r in records if metric in r and "distance" in r]
        if not usable:
            continue

        # one vectorized scatter call per z-type (not per point) -- cheap
        # even at ~2000 points, and keeps each category's marker shape fixed
        for z_type, marker in TYPE_MARKERS.items():
            cat_records = [r for r in usable if r["z_type"] == z_type]
            if not cat_records:
                continue
            xs = np.array([r["distance"] for r in cat_records])
            ys = np.clip([r[metric] for r in cat_records], log_floor, 1.0)
            cs = np.clip([r["uncertainty"] for r in cat_records], 1e-2, None)
            mappable = ax.scatter(
                xs, ys, c=cs, cmap=cmap, norm=color_norm, marker=marker,
                s=8, alpha=0.5, edgecolor="none", zorder=2,
            )

        ax.set_xscale("log")
        ax.set_title(metric, fontsize=10)
        ax.set_xlabel("distance (Mpc)", fontsize=8)
        ax.set_ylabel("raw (score)", fontsize=8)
        ax.tick_params(labelsize=7)

    for j in range(len(metrics_present), n_rows * n_cols):
        axes[j // n_cols][j % n_cols].axis("off")

    fig.suptitle(f"Distance-metric score vs. host distance -- {event}", fontsize=13)
    fig.tight_layout(rect=[0, 0.05, 0.85, 0.96])

    if mappable is not None:
        cbar = fig.colorbar(mappable, ax=axes, fraction=0.03, pad=0.02)
        cbar.set_label("distance uncertainty (Mpc)", fontsize=9)

    # shape legend: neutral gray markers so it reads as identity, not
    # implying anything about the continuous uncertainty color scale
    shape_handles = [
        plt.Line2D([0], [0], marker=marker, color="none", markerfacecolor="none",
                   markeredgecolor="#52514e", markersize=7, linestyle="none")
        for marker in TYPE_MARKERS.values()
    ]
    fig.legend(
        shape_handles, TYPE_MARKERS.keys(), loc="lower center",
        ncol=len(TYPE_MARKERS), frameon=False,
    )

    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def _plot_score_percentile_vs_dist(records, output_path, event, metric_order=None):
    """
    Score PERCENTILE (within-metric rank, 0-100%) vs. host distance, instead
    of the raw or log10 score value.

    Both the raw and log versions clump: raw because most scores are
    genuinely near 0 on a linear scale (median score is ~0.005-0.15
    depending on the metric), and log because a long tail of scores span
    down to ~1e-150 to ~1e-290 -- any fixed floor either bunches that whole
    tail onto one flat line, or (with no floor) compresses everything else
    into an unreadable sliver near the top. Percentile rank sidesteps this
    entirely: it's bounded to [0, 100] and evenly spread by construction,
    regardless of how extreme the underlying dynamic range is, since it only
    depends on order, not magnitude.
    """
    metrics_present = [m for m in (metric_order or METRIC_ORDER) if any(m in r for r in records)]

    cmap = LinearSegmentedColormap.from_list(
        "seq_blue", ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
    )
    all_uncertainty = np.clip([r["uncertainty"] for r in records if "distance" in r], 1e-2, None)
    vmin, vmax = np.percentile(all_uncertainty, [2, 98])  # robust range, ignore extreme outliers
    color_norm = LogNorm(vmin=max(vmin, 1e-2), vmax=vmax)

    n_cols = 2
    n_rows = -(-len(metrics_present) // n_cols)  # ceil division
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(11, 3.6 * n_rows), squeeze=False)

    mappable = None
    for i, metric in enumerate(metrics_present):
        ax = axes[i // n_cols][i % n_cols]
        usable = [r for r in records if metric in r and "distance" in r]
        if not usable:
            continue

        # rank within this metric's full population (not per z-type), so
        # every category shares the same percentile scale in this panel
        raw_vals = np.array([r[metric] for r in usable], dtype=float)
        percentiles = rankdata(raw_vals, method="average", nan_policy="omit") / np.sum(~np.isnan(raw_vals)) * 100

        for z_type, marker in TYPE_MARKERS.items():
            idx = [j for j, r in enumerate(usable) if r["z_type"] == z_type]
            if not idx:
                continue
            xs = np.array([usable[j]["distance"] for j in idx])
            ys = percentiles[idx]
            cs = np.clip([usable[j]["uncertainty"] for j in idx], 1e-2, None)
            mappable = ax.scatter(
                xs, ys, c=cs, cmap=cmap, norm=color_norm, marker=marker,
                s=8, alpha=0.5, edgecolor="none", zorder=2,
            )

        ax.set_xscale("log")
        ax.set_title(metric, fontsize=10)
        ax.set_xlabel("distance (Mpc)", fontsize=8)
        ax.set_ylabel("score percentile (%)", fontsize=8)
        ax.tick_params(labelsize=7)

    for j in range(len(metrics_present), n_rows * n_cols):
        axes[j // n_cols][j % n_cols].axis("off")

    fig.suptitle(f"Distance-metric score percentile vs. host distance -- {event}", fontsize=13)
    fig.tight_layout(rect=[0, 0.05, 0.85, 0.96])

    if mappable is not None:
        cbar = fig.colorbar(mappable, ax=axes, fraction=0.03, pad=0.02)
        cbar.set_label("distance uncertainty (Mpc)", fontsize=9)

    shape_handles = [
        plt.Line2D([0], [0], marker=marker, color="none", markerfacecolor="none",
                   markeredgecolor="#52514e", markersize=7, linestyle="none")
        for marker in TYPE_MARKERS.values()
    ]
    fig.legend(
        shape_handles, TYPE_MARKERS.keys(), loc="lower center",
        ncol=len(TYPE_MARKERS), frameon=False,
    )

    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def _plot_score_vs_uncertainty(records, output_path, event, log_floor=1e-30, metric_order=None):
    """
    Score vs. distance uncertainty, one panel per metric, colored by z-type.

    Tests the bias hypothesis directly: if wider (less certain) distance
    estimates get systematically higher overlap scores regardless of z-type,
    points should trend upward with uncertainty within every color, not just
    cluster by category on the x-axis like the boxplots do.
    """
    metrics_present = [m for m in (metric_order or METRIC_ORDER) if any(m in r for r in records)]

    n_cols = 2
    n_rows = -(-len(metrics_present) // n_cols)  # ceil division
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(11, 3.4 * n_rows), squeeze=False)

    for i, metric in enumerate(metrics_present):
        ax = axes[i // n_cols][i % n_cols]
        for cat, color in CATEGORY_COLORS.items():
            xs = [r["uncertainty"] for r in records if r["z_type"] == cat and metric in r]
            if not xs:
                continue
            ys = np.clip([r[metric] for r in records if r["z_type"] == cat and metric in r], log_floor, 1.0)
            ax.scatter(xs, ys, s=8, alpha=0.35, color=color, edgecolor="none")

        ax.set_xscale("log")
        ax.set_title(metric, fontsize=10)
        ax.set_xlabel("distance uncertainty (Mpc)", fontsize=8)
        ax.set_ylabel("raw (score)", fontsize=8)
        ax.tick_params(labelsize=7)

    for j in range(len(metrics_present), n_rows * n_cols):
        axes[j // n_cols][j % n_cols].axis("off")

    handles = [
        plt.Line2D([0], [0], marker="o", color="none", markerfacecolor=color, markersize=6)
        for color in CATEGORY_COLORS.values()
    ]
    fig.legend(
        handles, CATEGORY_COLORS.keys(), loc="lower center",
        ncol=len(CATEGORY_COLORS), frameon=False,
    )

    fig.suptitle(f"Distance-metric score vs. host distance uncertainty -- {event}", fontsize=13)
    fig.tight_layout(rect=[0, 0.05, 1, 0.96])
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def _bucket_z_type(raw_z_type):
    """
    Mirror the z-type priority buckets get_distance_score_diagnostic uses, so
    records line up with the same fixed category/color mapping everywhere.
    """
    if raw_z_type == "user spec-z":
        return "user-redshift"
    if raw_z_type == "z ind.":
        return "ind"
    if "spec-z" in raw_z_type:
        return "spec-z"
    if raw_z_type == "photo-z":
        return "photo-z"
    return None


def _collect_host_records(target_id: int, nonlocalized_event_name: str):
    """
    Returns one record per evaluated host (or a single record for the target's
    own redshift, if known), each holding every metric's score plus the
    distance uncertainty and z-type bucket that produced it.

    This uses every evaluated host, not just the best-scoring one per metric
    (which is all get_distance_score_diagnostic returns) -- that's needed to
    plot score vs. uncertainty at all, and it also gives the boxplots a much
    larger, more representative sample per category instead of one point per
    target.
    """
    target = Target.objects.get(id=target_id)

    if target.redshift is not None and not np.isnan(target.redshift):
        targ_dist = cosmo.luminosity_distance(target.redshift).to(u.Mpc).value
        targ_dist_err = cosmo.luminosity_distance(1e-3).to(u.Mpc).value
        test_mean, test_std = _distance_at_healpix(nonlocalized_event_name, target_id)
        diag_scores = get_distance_score_diagnostic(
            pd.DataFrame(), target_id, nonlocalized_event_name
        )
        record = {metric: val[0] for metric, val in diag_scores.items()}
        record["distance"] = targ_dist
        record["uncertainty"] = targ_dist_err
        # raw ingredients needed to rebuild this record's two PDFs locally
        # (e.g. for a parameter sweep on a metric) without re-querying the DB
        record["lumdist_neg_err"] = targ_dist_err
        record["lumdist_pos_err"] = targ_dist_err
        record["test_mean"] = test_mean
        record["test_std"] = test_std
        record["z_type"] = "redshift"
        return [record]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        host_df = host_association(target_id)
    host_df = _clean_host_df(host_df)

    if not len(host_df):
        # nothing to compare against -- no bias data point, not a fake category
        return []

    host_df = host_distance_match(host_df, target_id, nonlocalized_event_name)
    test_mean, test_std = _distance_at_healpix(nonlocalized_event_name, target_id)

    records = []
    for _, row in host_df.iterrows():
        z_type = _bucket_z_type(row.z_type)
        if z_type is None:
            continue
        record = {metric: row[metric] for metric in METRIC_ORDER if metric in row}
        if not record:
            continue
        record["distance"] = row.lumdist
        record["uncertainty"] = (row.lumdist_neg_err + row.lumdist_pos_err) / 2
        record["lumdist_neg_err"] = row.lumdist_neg_err
        record["lumdist_pos_err"] = row.lumdist_pos_err
        record["test_mean"] = test_mean
        record["test_std"] = test_std
        record["z_type"] = z_type
        records.append(record)
    return records

def _score_one_candidate(ec, event):
    try:
        records = _collect_host_records(ec.target_id, event)
        return ec.target.name, records, None
    except Exception as e:
        return ec.target.name, None, str(e)
    finally:
        for conn in connections.all():
            conn.close()


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            "--event",
            help="Nonlocalized event_id, e.g. S251112cm or GW170817",
            type=str,
            required=True,
        )
        parser.add_argument(
            "--limit",
            help="Max number of candidates to check (default 15, use 0 for all)",
            type=int,
            default=15,
        )
        parser.add_argument(
            "--target-name",
            help="Only check the candidate with this target name",
            type=str,
            default=None,
        )
        parser.add_argument(
            "--output",
            help="Base path to save the figures (heatmap/boxplot suffixes added)",
            type=str,
            default="distance_metric_bias",
        )
        parser.add_argument(
            "--workers",
            help="Number of candidates to score concurrently (default 8). Each "
            "candidate is almost all network wait, so this is a big speedup, "
            "but keep it moderate -- too high adds load to the shared DB tunnel",
            type=int,
            default=8,
        )
        parser.add_argument(
            "--from-cache",
            help="Path to a previously-saved *_records.json file -- skips "
            "re-querying the DB/catalogs and just replots the cached records",
            type=str,
            default=None,
        )

    def handle(
        self, event, limit=None, target_name=None, output="distance_metric_bias",
        workers=8, from_cache=None, **kwargs,
    ):
        if from_cache:
            with open(from_cache) as f:
                all_records = json.load(f)
            print(f"Loaded {len(all_records)} host records from {from_cache}")
        else:
            try:
                nle = NonLocalizedEvent.objects.get(event_id=event)
            except NonLocalizedEvent.DoesNotExist:
                raise CommandError(f"No NonLocalizedEvent found with event_id={event!r}")

            candidates = EventCandidate.objects.filter(nonlocalizedevent_id=nle.id)
            if target_name:
                candidates = candidates.filter(target__name=target_name)
                if not candidates.exists():
                    raise CommandError(f"No candidate named {target_name!r} found for {event}")
            elif limit:
                candidates = candidates[:limit]

            candidates = list(candidates)
            total = len(candidates)

            all_records = []
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(_score_one_candidate, ec, event) for ec in candidates]
                for i, future in enumerate(as_completed(futures), start=1):
                    name, records, error = future.result()
                    if error:
                        print(f"[{i}/{total}] {name}: FAILED - {error}")
                        continue
                    all_records.extend(records)
                    print(f"[{i}/{total}] {name}: done ({len(records)} host record(s))")

            if not all_records:
                print("No plotting data collected.")
                return

            cache_path = f"{output}_records.json"
            with open(cache_path, "w") as f:
                json.dump(all_records, f)
            print(f"Cached {len(all_records)} host records to {cache_path}")

        plotting_data = defaultdict(lambda: defaultdict(list))
        for rec in all_records:
            for metric in METRIC_ORDER:
                if metric in rec:
                    plotting_data[metric][rec["z_type"]].append(rec[metric])

        boxplot_path = _plot_metric_boxplots(
            plotting_data, f"{output}_raw_boxplots.png", event, exclude_categories={"ind"}
        )
        scatter_path = _plot_score_vs_uncertainty(
            all_records, f"{output}_raw_score_vs_uncertainty.png", event
        )
        dist_path = _plot_score_vs_dist(
            all_records, f"{output}_raw_score_vs_distance.png", event
        )
        log_dist_path = _plot_log_score_vs_dist(
            all_records, f"{output}_log_score_vs_distance.png", event
        )
        percentile_dist_path = _plot_score_percentile_vs_dist(
            all_records, f"{output}_percentile_score_vs_distance.png", event
        )
        print(f"Saved boxplots to {boxplot_path}")
        print(f"Saved score-vs-uncertainty plot to {scatter_path}")
        print(f"Saved score-vs-distance plot to {dist_path}")
        print(f"Saved Log_score-vs-distance plot to {log_dist_path}")
        print(f"Saved percentile-score-vs-distance plot to {percentile_dist_path}")