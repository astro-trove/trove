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
"""

import json
import warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from astropy import units as u

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from trove_targets.models import Target
from tom_nonlocalizedevents.models import EventCandidate, NonLocalizedEvent

from candidate_vetting.vet import host_association

from scoring.scoring import cosmo, get_distance_score_diagnostic, host_distance_match, get_distance_score


def _clean_host_df(host_df):
    """Same filler-value / NaN cleanup used in vet_kn_in_sn before scoring."""
    if not len(host_df):
        return host_df
    host_df = host_df[host_df.z != -99.0]  # LS DR9 North
    host_df = host_df[host_df.z != -999.0]  # PS1-STRM
    host_df = host_df[host_df.z != -9999.0]  # SDSS DR12 photo-z
    host_df = host_df[~np.isnan(host_df.z)]
    return host_df


# fixed categorical color per distance-measurement type, kept the same across
# every subplot so e.g. "spec-z" is always the same color everywhere
CATEGORY_COLORS = {
    "redshift": "#2a78d6",
    "user-redshift": "#1baf7a",
    "spec-z": "#eda100",
    "photo-z": "#008300",
    "ind": "#4a3aa7",
}

METRIC_ORDER = [
    "bc",
    "bc_norm",
    "zscore",
    "Resampled zscore",
    "Conditional JSD Metric",
    "Consistent Probability",
    "Improved Consistent Probability",
]


def _plot_metric_boxplots(plotting_data, output_path, event, log_floor=1e-30, exclude_categories=None):
    exclude_categories = exclude_categories or set()
    metrics_present = [m for m in METRIC_ORDER if m in plotting_data]

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

        # drop NaNs per category -- a handful of hosts (mostly photo-z, up to
        # ~5% for bc_norm) produce NaN scores (missing catalog z-uncertainty
        # propagating through the metric calculation), and matplotlib's
        # boxplot silently produces an empty/invisible box for the whole
        # category if even one NaN is present in its data
        data = [
            np.log10(np.clip(np.asarray(by_category[c], dtype=float), log_floor, 1.0))
            for c in cats_present
        ]
        data = [d[~np.isnan(d)] for d in data]

        bp = ax.boxplot(
            data, patch_artist=True, widths=0.6,
            # 5th-95th percentile whiskers instead of the default 1.5*IQR rule:
            # these scores are heavily right-skewed (a dense cluster near 0
            # plus a long tail down to the floor), so Tukey's rule flags huge
            # numbers of points as "outliers" and the plot turns into a wall
            # of dots.
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


def _plot_score_vs_uncertainty(records, output_path, event, log_floor=1e-30):
    """
    Score vs. distance uncertainty, one panel per metric, colored by z-type.

    Tests the bias hypothesis directly: if wider (less certain) distance
    estimates get systematically higher overlap scores regardless of z-type,
    points should trend upward with uncertainty within every color, not just
    cluster by category on the x-axis like the boxplots do.
    """
    metrics_present = [m for m in METRIC_ORDER if any(m in r for r in records)]

    n_cols = 2
    n_rows = -(-len(metrics_present) // n_cols)  # ceil division
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(11, 3.4 * n_rows), squeeze=False)

    for i, metric in enumerate(metrics_present):
        ax = axes[i // n_cols][i % n_cols]
        for cat, color in CATEGORY_COLORS.items():
            xs = [r["uncertainty"] for r in records if r["z_type"] == cat and metric in r]
            if not xs:
                continue
            ys = np.log10(np.clip([r[metric] for r in records if r["z_type"] == cat and metric in r], log_floor, 1.0))
            ax.scatter(xs, ys, s=8, alpha=0.35, color=color, edgecolor="none")

        ax.set_xscale("log")
        ax.set_title(metric, fontsize=10)
        ax.set_xlabel("distance uncertainty (Mpc)", fontsize=8)
        ax.set_ylabel("log10(score)", fontsize=8)
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
        targ_dist_err = cosmo.luminosity_distance(1e-3).to(u.Mpc).value
        diag_scores = get_distance_score_diagnostic(
            pd.DataFrame(), target_id, nonlocalized_event_name
        )
        record = {metric: val[0] for metric, val in diag_scores.items()}
        record["uncertainty"] = targ_dist_err
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

    records = []
    for _, row in host_df.iterrows():
        z_type = _bucket_z_type(row.z_type)
        if z_type is None:
            continue
        record = {metric: row[metric] for metric in METRIC_ORDER if metric in row}
        if not record:
            continue
        record["uncertainty"] = (row.lumdist_neg_err + row.lumdist_pos_err) / 2
        record["z_type"] = z_type
        records.append(record)
    return records


def _score_one_candidate(ec, event):
    """
    Thread-pool worker: each candidate's cost is almost entirely network wait
    (host_association queries ~12 external galaxy catalogs sequentially), so
    running candidates concurrently gives a near-linear speedup instead of
    burning wall-clock time on I/O one candidate at a time.
    """
    try:
        records = _collect_host_records(ec.target_id, event)
        return ec.target.name, records, None
    except Exception as e:
        return ec.target.name, None, str(e)
    finally:
        # each thread lazily opens its own DB connections (default + catalogs
        # aliases); close them here so a big candidate list doesn't leave
        # hundreds of connections open on the shared DB tunnel
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
            plotting_data, f"{output}_boxplots.png", event, exclude_categories={"ind"}
        )
        scatter_path = _plot_score_vs_uncertainty(
            all_records, f"{output}_score_vs_uncertainty.png", event
        )
        print(f"Saved boxplots to {boxplot_path}")
        print(f"Saved score-vs-uncertainty plot to {scatter_path}")
