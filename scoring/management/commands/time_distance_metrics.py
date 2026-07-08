"""
Diagnostic: compare per-call execution time of the distance-scoring metrics
(bc, bc_norm, Consistent Probability, Improved Consistent Probability, Hybrid
Consistent Probability) on real host data, without touching the DB or SSH
tunnels.

Reuses a *_records.json cache (from check_distance_scores) since it already
holds everything needed to rebuild each host's two distance PDFs locally:
test_mean/test_std (the GW distance estimate at that host's sky position) and
distance/lumdist_neg_err/lumdist_pos_err (the host's own asymmetric distance
uncertainty). PDF construction happens once per sampled host (setup, not
timed) so each metric's timed block only measures that metric's own compute
cost, matching how it's actually called per-host-row in host_distance_match().

Example
-------
python manage.py time_distance_metrics \
    --records /home/sopanda25/trove/out/distance_metric_bias_records.json \
    --n 40 --repeats 20
"""

import contextlib
import io
import json
import random
import timeit

import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import norm

from django.core.management.base import BaseCommand, CommandError

from scoring.dist_scoring_helpers import (
    AsymmetricGaussian, bc, bc_norm_median_asymmetric, consistency_probability, cons_prob_3, hybrid_cons_prob,
)
from scoring.scoring import D_LIM_LOWER, D_LIM_UPPER
from scoring.management.commands.check_distance_scores import METRIC_COLORS

METRICS = [
    "bc", "bc_norm", "Consistent Probability", "Improved Consistent Probability", "Hybrid Consistent Probability",
]

REQUIRED_FIELDS = ("distance", "uncertainty", "lumdist_neg_err", "lumdist_pos_err", "test_mean", "test_std")


def _plot_metric_timings(results, output_path, n, repeats):
    """
    Boxplot (log-scale y-axis) of per-call compute time across the n sampled
    hosts, one box per metric -- shows both the mean cost and how much it
    varies host-to-host, not just a single bar per metric.
    """
    data = [results[m] * 1e6 for m in METRICS]  # seconds -> microseconds

    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    bp = ax.boxplot(
        data, patch_artist=True, widths=0.6,
        whis=(5, 95),
        medianprops=dict(color="#0b0b0b", linewidth=1.5),
        flierprops=dict(marker="o", markersize=3, markerfacecolor="#898781", markeredgecolor="none", alpha=0.5),
    )
    for patch, metric in zip(bp["boxes"], METRICS):
        patch.set_facecolor(METRIC_COLORS.get(metric, "#52514e"))
        patch.set_alpha(0.85)

    ax.set_yscale("log")
    ax.set_xticks(range(1, len(METRICS) + 1))
    ax.set_xticklabels(METRICS, rotation=12, ha="right", fontsize=9)
    ax.set_ylabel("time per call (µs, log scale)", fontsize=10)
    ax.set_title(
        f"Distance-metric compute time per call (n={n} hosts, {repeats} repeats each)",
        fontsize=12,
    )
    ax.tick_params(labelsize=8)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            "--records",
            help="Path to a *_records.json cache from check_distance_scores",
            type=str,
            required=True,
        )
        parser.add_argument(
            "--n",
            help="Number of host records to sample (default 40)",
            type=int,
            default=40,
        )
        parser.add_argument(
            "--repeats",
            help="Timed repeats per sampled record per metric, for stable "
            "per-call timing (default 20)",
            type=int,
            default=20,
        )
        parser.add_argument(
            "--seed",
            help="Random seed for sampling (default 0)",
            type=int,
            default=0,
        )
        parser.add_argument(
            "--output",
            help="Path to save the timing boxplot (default distance_metric_timing.png)",
            type=str,
            default="distance_metric_timing.png",
        )

    def handle(self, records, n, repeats, seed, output, **kwargs):
        with open(records) as f:
            all_records = json.load(f)

        usable = [r for r in all_records if all(k in r and r[k] is not None for k in REQUIRED_FIELDS)]
        usable = [r for r in usable if not any(np.isnan(r[k]) for k in REQUIRED_FIELDS)]
        if len(usable) < n:
            raise CommandError(f"Only {len(usable)} usable records in {records}, need at least {n}")

        random.seed(seed)
        sample = random.sample(usable, n)

        _lumdist = np.linspace(D_LIM_LOWER, D_LIM_UPPER, int(10 * D_LIM_UPPER))

        # per-metric list of (setup_once) closures -- PDF construction happens
        # here, outside the timed section, since it's a per-host constant that
        # host_distance_match() would only build once before calling each metric
        timed_fns = {metric: [] for metric in METRICS}
        for rec in sample:
            test_pdf = norm.pdf(_lumdist, loc=rec["test_mean"], scale=rec["test_std"])
            cur_pdf = AsymmetricGaussian().pdf(
                _lumdist,
                mean=rec["distance"],
                unc_minus=rec["lumdist_neg_err"],
                unc_plus=rec["lumdist_pos_err"],
                integ_a=1e-9,
                integ_b=_lumdist[-1],
            )

            timed_fns["bc"].append(
                lambda test_pdf=test_pdf, cur_pdf=cur_pdf: bc(cur_pdf, test_pdf, _lumdist)
            )
            timed_fns["bc_norm"].append(
                lambda test_pdf=test_pdf, cur_pdf=cur_pdf, rec=rec: bc_norm_median_asymmetric(
                    test_pdf, cur_pdf, rec["test_mean"], rec["lumdist_neg_err"], rec["lumdist_pos_err"], _lumdist
                )
            )
            timed_fns["Consistent Probability"].append(
                lambda rec=rec: consistency_probability(
                    rec["test_mean"], rec["distance"], rec["test_std"], rec["lumdist_neg_err"], rec["lumdist_pos_err"]
                )
            )
            timed_fns["Improved Consistent Probability"].append(
                lambda rec=rec: cons_prob_3(
                    rec["test_mean"], rec["distance"], rec["test_std"], rec["lumdist_neg_err"], rec["lumdist_pos_err"]
                )
            )
            timed_fns["Hybrid Consistent Probability"].append(
                lambda rec=rec: hybrid_cons_prob(
                    rec["test_mean"], rec["distance"], rec["test_std"], rec["lumdist_neg_err"], rec["lumdist_pos_err"]
                )
            )

        print(f"Timing {len(METRICS)} metrics over {n} sampled host records, {repeats} repeats each...\n")

        results = {}
        for metric in METRICS:
            per_call_times = []
            # hybrid_cons_prob has a leftover debug print() on one of its
            # branches -- suppress it here so timing this metric doesn't
            # flood stdout or pick up print()'s own I/O latency per call
            with contextlib.redirect_stdout(io.StringIO()):
                for fn in timed_fns[metric]:
                    total = timeit.timeit(fn, number=repeats)
                    per_call_times.append(total / repeats)
            results[metric] = np.array(per_call_times)

        fastest_mean = min(np.mean(v) for v in results.values())

        col_widths = (32, 12, 14, 14, 12)
        header_cells = ("metric", "mean (us)", "median (us)", "total (ms)", "x slowdown")
        header = "".join(f"{c:<{w}}" if i == 0 else f"{c:>{w}}" for i, (c, w) in enumerate(zip(header_cells, col_widths)))
        print(header)
        print("-" * len(header))
        for metric in METRICS:
            times = results[metric]
            mean_us = np.mean(times) * 1e6
            median_us = np.median(times) * 1e6
            total_ms = np.sum(times) * 1e3
            slowdown = np.mean(times) / fastest_mean
            print(f"{metric:<32}{mean_us:>12.2f}{median_us:>14.2f}{total_ms:>14.3f}{slowdown:>11.2f}x")

        # markdown table, easy to paste elsewhere
        print("\n| metric | mean (us) | median (us) | total (ms) | x slowdown |")
        print("|---|---:|---:|---:|---:|")
        for metric in METRICS:
            times = results[metric]
            mean_us = np.mean(times) * 1e6
            median_us = np.median(times) * 1e6
            total_ms = np.sum(times) * 1e3
            slowdown = np.mean(times) / fastest_mean
            print(f"| {metric} | {mean_us:.2f} | {median_us:.2f} | {total_ms:.3f} | {slowdown:.2f}x |")

        plot_path = _plot_metric_timings(results, output, n, repeats)
        print(f"\nSaved timing boxplot to {plot_path}")
