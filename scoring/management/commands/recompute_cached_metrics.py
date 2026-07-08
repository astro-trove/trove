"""
Recompute distance-scoring metrics for every record in a *_records.json
cache, entirely offline (no DB / SSH tunnel).

A cached record already stores everything host_distance_match() needs to
rebuild both distance PDFs -- test_mean/test_std (the GW distance estimate at
that host's sky position, which _get_nle_distance_pdf just turns into
norm.pdf(loc=test_mean, scale=test_std)) and distance/lumdist_neg_err/
lumdist_pos_err (the host's own asymmetric distance uncertainty). So when only
the metric *functions* change (not the underlying distance data), replaying
them against the cache reproduces exactly what a full DB re-collection would
give, without re-querying anything.

By default only recomputes the closed-form probability metrics (Consistent
Probability, Improved Consistent Probability, Hybrid Consistent Probability)
-- cheap, no PDF construction needed. Pass --include-bc to also redo bc/
bc_norm, which numerically build an AsymmetricGaussian PDF and integrate it
per record (~7ms/record vs. ~microseconds for the closed-form metrics; see
time_distance_metrics.py), so only worth it when those two actually changed.

Example
-------
python manage.py recompute_cached_metrics \
    --records /home/sopanda25/trove/out/distance_metric_bias_records.json \
    --output /home/sopanda25/trove/out/distance_metric_bias_records.json
"""

import contextlib
import io
import json

import numpy as np
from scipy.stats import norm

from django.core.management.base import BaseCommand

from scoring.dist_scoring_helpers import (
    AsymmetricGaussian, bc, bc_norm_median_asymmetric, consistency_probability, cons_prob_3, hybrid_cons_prob,
)
from scoring.scoring import D_LIM_LOWER, D_LIM_UPPER

REQUIRED_FIELDS = ("distance", "lumdist_neg_err", "lumdist_pos_err", "test_mean", "test_std")


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--records", type=str, required=True)
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help="Where to save the updated cache (default: overwrite --records in place)",
        )
        parser.add_argument(
            "--include-bc",
            action="store_true",
            help="Also recompute bc/bc_norm (slow -- builds+integrates a PDF per record)",
        )

    def handle(self, records, output, include_bc, **kwargs):
        with open(records) as f:
            all_records = json.load(f)

        output = output or records
        _lumdist = np.linspace(D_LIM_LOWER, D_LIM_UPPER, int(10 * D_LIM_UPPER)) if include_bc else None

        n_ok, n_failed = 0, 0
        for rec in all_records:
            if not all(k in rec and rec[k] is not None and not np.isnan(rec[k]) for k in REQUIRED_FIELDS):
                n_failed += 1
                continue
            try:
                if include_bc:
                    test_pdf = norm.pdf(_lumdist, loc=rec["test_mean"], scale=rec["test_std"])
                    cur_pdf = AsymmetricGaussian().pdf(
                        _lumdist,
                        mean=rec["distance"],
                        unc_minus=rec["lumdist_neg_err"],
                        unc_plus=rec["lumdist_pos_err"],
                        integ_a=1e-9,
                        integ_b=_lumdist[-1],
                    )
                    rec["bc"] = bc(cur_pdf, test_pdf, _lumdist)
                    rec["bc_norm"] = bc_norm_median_asymmetric(
                        test_pdf, cur_pdf, rec["test_mean"], rec["lumdist_neg_err"], rec["lumdist_pos_err"], _lumdist
                    )

                rec["Consistent Probability"] = consistency_probability(
                    rec["test_mean"], rec["distance"], rec["test_std"], rec["lumdist_neg_err"], rec["lumdist_pos_err"]
                )
                rec["Improved Consistent Probability"] = cons_prob_3(
                    rec["test_mean"], rec["distance"], rec["test_std"], rec["lumdist_neg_err"], rec["lumdist_pos_err"]
                )
                # hybrid_cons_prob has a leftover debug print() on one of its
                # branches -- suppress it here so 2000+ records doesn't flood stdout
                with contextlib.redirect_stdout(io.StringIO()):
                    rec["Hybrid Consistent Probability"] = hybrid_cons_prob(
                        rec["test_mean"], rec["distance"], rec["test_std"], rec["lumdist_neg_err"], rec["lumdist_pos_err"]
                    )
                n_ok += 1
            except Exception as e:
                print(f"Failed to recompute record: {e}")
                n_failed += 1

        with open(output, "w") as f:
            json.dump(all_records, f)

        print(f"Recomputed {n_ok} records ({n_failed} skipped/failed) -> {output}")
