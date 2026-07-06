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

import warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from trove_targets.models import Target
from tom_nonlocalizedevents.models import EventCandidate, NonLocalizedEvent

from candidate_vetting.vet import host_association

from scoring.scoring import get_distance_score_diagnostic, host_distance_match, get_distance_score


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


def _plot_metric_boxplots(plotting_data, output_path, event, log_floor=1e-30):
    metrics_present = [m for m in METRIC_ORDER if m in plotting_data]

    n_cols = 2
    n_rows = -(-len(metrics_present) // n_cols)  # ceil division
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(11, 3.2 * n_rows), squeeze=False)

    for i, metric in enumerate(metrics_present):
        ax = axes[i // n_cols][i % n_cols]
        by_category = plotting_data[metric]
        cats_present = [c for c in CATEGORY_COLORS if by_category.get(c)]

        if not cats_present:
            ax.axis("off")
            continue

        data = [np.log10(np.clip(by_category[c], log_floor, 1.0)) for c in cats_present]
        bp = ax.boxplot(
            data, patch_artist=True, widths=0.6,
            medianprops=dict(color="#0b0b0b", linewidth=1.5),
            flierprops=dict(marker="o", markersize=3, markerfacecolor="#898781", markeredgecolor="none"),
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


def check_target_distance_score(target_id: int, nonlocalized_event_name: str):
    target = Target.objects.get(id=target_id)

    if target.redshift is not None and not np.isnan(target.redshift):
        ret_scores = get_distance_score_diagnostic(
            pd.DataFrame(), target_id, nonlocalized_event_name
        )
        return ret_scores

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        host_df = host_association(target_id)
    host_df = _clean_host_df(host_df)

    if not len(host_df):
        # nothing to compare against -- no bias data point, not a fake category
        return {}

    host_df = host_distance_match(host_df, target_id, nonlocalized_event_name)
    ret_scores = get_distance_score_diagnostic(host_df, target_id, nonlocalized_event_name)
    return ret_scores


def _score_one_candidate(ec, event):
    """
    Thread-pool worker: each candidate's cost is almost entirely network wait
    (host_association queries ~12 external galaxy catalogs sequentially), so
    running candidates concurrently gives a near-linear speedup instead of
    burning wall-clock time on I/O one candidate at a time.
    """
    try:
        diag_scores = check_target_distance_score(ec.target_id, event)
        return ec.target.name, diag_scores, None
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

    def handle(self, event, limit=None, target_name=None, output="distance_metric_bias", workers=8, **kwargs):
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

        plotting_data = defaultdict(lambda: defaultdict(list))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_score_one_candidate, ec, event) for ec in candidates]
            for i, future in enumerate(as_completed(futures), start=1):
                name, diag_scores, error = future.result()
                if error:
                    print(f"[{i}/{total}] {name}: FAILED - {error}")
                    continue
                for key, val in diag_scores.items():
                    if len(val) == 3 and val[2]:
                        plotting_data[key][val[2]].append(val[0])
                print(f"[{i}/{total}] {name}: done")

        if plotting_data:
            boxplot_path = _plot_metric_boxplots(plotting_data, f"{output}_boxplots.png", event)
            print(f"Saved boxplots to {boxplot_path}")
        else:
            print("No plotting data collected.")
