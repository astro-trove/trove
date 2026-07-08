# Distance-metric compute time comparison

Per-call execution time of the distance-scoring metrics (`bc`, `bc_norm`,
`Consistent Probability`, `Improved Consistent Probability`, `Hybrid
Consistent Probability`), measured on 40 real host records sampled from
`distance_metric_bias_records.json`, 20 timed repeats per record. Produced by
`scoring/management/commands/time_distance_metrics.py`.

| metric | mean (us) | median (us) | total (ms) | x slowdown |
|---|---:|---:|---:|---:|
| bc | 236.23 | 195.83 | 9.449 | 147.57x |
| bc_norm | 8480.60 | 8418.30 | 339.224 | 5297.55x |
| Consistent Probability | 1.60 | 1.49 | 0.064 | 1.00x |
| Improved Consistent Probability | 3.28 | 3.19 | 0.131 | 2.05x |
| Hybrid Consistent Probability | 4.76 | 4.64 | 0.191 | 2.98x |

![Distance-metric compute time per call](distance_metric_timing.png)

## Notes

- `bc` and `bc_norm` both numerically build an `AsymmetricGaussian` PDF (with
  its own internal trapezoidal integration over a 100,000-point distance
  grid) and compute a Bhattacharyya-coefficient overlap -- real numerical
  work. `bc_norm` costs ~36x more than plain `bc` since it does a *second*
  overlap (against a reshifted "best-case" host PDF) to normalize the score.
- `Consistent Probability`, `Improved Consistent Probability`, and `Hybrid
  Consistent Probability` are all closed-form `erfc` expressions on scalars,
  so they're 2-4 orders of magnitude cheaper than `bc`/`bc_norm`. Among the
  three, cost scales with how much extra branching/blending each does:
  `Consistent Probability` picks one tail (cheapest), `Improved Consistent
  Probability` blends both tails via one `erfc`-weight (~2x), and `Hybrid
  Consistent Probability` evaluates two separate `erfc` sigmas
  (`score_minus`/`score_plus`) plus a z-score/width-threshold branch check
  (~3x) -- still under 5us, negligible in absolute terms.
- `hybrid_cons_prob()` has a leftover debug `print("Z Score only")` on one of
  its branches; the timing script suppresses stdout during the timed calls
  (via `contextlib.redirect_stdout`) so it doesn't flood output or add I/O
  latency to the measured cost.
- At ~2112 host records (a full collection run), `bc_norm` alone accounts for
  roughly 18s of pure compute, `bc` adds another ~0.5s, while all three
  erfc-based metrics combined cost well under 25ms total -- negligible next
  to the per-candidate network/DB wait (~7-10s seen during real collection
  runs). Metric compute time is not a bottleneck in `check_distance_scores`;
  the SSH-tunneled galaxy queries are.

Reproduce with:

```
python manage.py time_distance_metrics \
    --records /home/sopanda25/trove/out/distance_metric_bias_records.json \
    --n 40 --repeats 20 \
    --output /home/sopanda25/trove/out/distance_metric_timing.png
```
