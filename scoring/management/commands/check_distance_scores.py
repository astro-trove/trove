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

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from django.core.management.base import BaseCommand, CommandError

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


def _plot_metric_histograms(plotting_data, output_path, event, n_bins=25, log_floor=1e-30):
    """
    One histogram per metric (small multiples), stacked bars colored by the
    type of distance measurement (redshift, spec-z, photo-z, z-ind, ...).

    Scores span many orders of magnitude (down to ~1e-200 for badly-mismatched
    hosts), so a linear-scale histogram would just show one giant bar at zero.
    We histogram log10(score) instead, with a shared floor for any score below
    log_floor, so the spread is actually visible and every panel is on the
    same scale for direct comparison.
    """
    metrics_present = [m for m in METRIC_ORDER if m in plotting_data]
    bins = np.linspace(np.log10(log_floor), 0, n_bins + 1)

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
        colors = [CATEGORY_COLORS[c] for c in cats_present]
        ax.hist(data, bins=bins, stacked=True, color=colors, edgecolor="white", linewidth=0.5)
        ax.set_title(metric, fontsize=10)
        ax.set_xlabel("log10(score)", fontsize=8)
        ax.set_ylabel("count", fontsize=8)
        ax.tick_params(labelsize=7)

    # turn off any unused trailing panels (metric count isn't always a multiple of n_cols)
    for j in range(len(metrics_present), n_rows * n_cols):
        axes[j // n_cols][j % n_cols].axis("off")

    # one shared legend for the whole figure, in the same fixed category order
    # used for color assignment everywhere else
    handles = [plt.Rectangle((0, 0), 1, 1, color=color) for color in CATEGORY_COLORS.values()]
    fig.legend(
        handles, CATEGORY_COLORS.keys(), loc="lower center",
        ncol=len(CATEGORY_COLORS), frameon=False,
    )

    fig.suptitle(f"Distance-metric scores by host redshift type -- {event}", fontsize=13)
    fig.tight_layout(rect=[0, 0.05, 1, 0.96])
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def check_target_distance_score(target_id: int, nonlocalized_event_name: str):
    """
    Run the same distance-scoring path vet_kn_in_sn takes for a single target,
    and return (score, host_name, note) for reporting.
    """
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
        return 1.0, None, "no host candidates after cleanup"

    host_df = host_distance_match(host_df, target_id, nonlocalized_event_name)
    ret_scores = get_distance_score_diagnostic(host_df, target_id, nonlocalized_event_name)
    return ret_scores


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
            help="Path to save the histogram figure",
            type=str,
            default="distance_metric_histograms.png",
        )

    def handle(self, event, limit=15, target_name=None, output="distance_metric_histograms.png", **kwargs):
        try:
            nle = NonLocalizedEvent.objects.get(event_id=event)
        except NonLocalizedEvent.DoesNotExist:
            raise CommandError(f"No NonLocalizedEvent found with event_id={event!r}")

        candidates = EventCandidate.objects.filter(nonlocalizedevent_id=nle.id)

        plotting_data = defaultdict(lambda: defaultdict(list))
        for ec in candidates:
            try:
                diag_scores = check_target_distance_score(
                    ec.target_id, event
                )
                for key, val in diag_scores.items():
                    if len(val) == 3 and val[2]:
                        plotting_data[key][val[2]].append(val[0])
            except Exception as e:
                print(f"  {ec.target_id}: FAILED - {e}")

        if plotting_data:
            path = _plot_metric_histograms(plotting_data, output, event)
            print(f"Saved histogram figure to {path}")
        else:
            print("No plotting data collected.")
