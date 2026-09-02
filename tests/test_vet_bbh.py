"""
Unit tests for the pure logic in scoring/vet_bbh.py: the baseline-variability fit
and flare-anomaly detection used to score BBH/AGN-flare candidates.
"""

import numpy as np
import pandas as pd
import pytest


def _phot_df(mag, magerr, filt, upperlimit=None):
    n = len(mag)
    return pd.DataFrame(
        dict(
            mag=mag,
            magerr=magerr,
            filter=filt,
            upperlimit=[False] * n if upperlimit is None else upperlimit,
        )
    )


class TestClamp:
    def test_within_bounds_unchanged(self):
        from scoring.vet_bbh import _clamp

        assert _clamp(0.5, 0.1, 1.0) == 0.5

    def test_clamps_to_lower_bound(self):
        from scoring.vet_bbh import _clamp

        assert _clamp(0.01, 0.1, 1.0) == 0.1

    def test_clamps_to_upper_bound(self):
        from scoring.vet_bbh import _clamp

        assert _clamp(5.0, 0.1, 1.0) == 1.0


class TestFitAgnBaseline:
    def test_none_input_returns_empty(self):
        from scoring.vet_bbh import fit_agn_baseline

        assert fit_agn_baseline(None) == {}

    def test_empty_dataframe_returns_empty(self):
        from scoring.vet_bbh import fit_agn_baseline

        assert fit_agn_baseline(_phot_df([], [], [])) == {}

    def test_too_few_points_dropped(self):
        from scoring.vet_bbh import fit_agn_baseline

        # only 3 points in 'r', below the default min_baseline_pts=5
        phot = _phot_df([18.0, 18.1, 17.9], [0.05, 0.05, 0.05], ["r"] * 3)
        assert fit_agn_baseline(phot, min_baseline_pts=5) == {}

    def test_enough_points_computes_robust_stats(self):
        from scoring.vet_bbh import fit_agn_baseline

        mags = [18.0, 18.2, 17.8, 18.1, 17.9, 18.0]
        phot = _phot_df(mags, [0.05] * len(mags), ["r"] * len(mags))
        baseline = fit_agn_baseline(phot, min_baseline_pts=5)

        assert "r" in baseline
        assert baseline["r"]["n"] == len(mags)
        assert baseline["r"]["mag"] == pytest.approx(np.median(mags))
        assert baseline["r"]["std"] > 0

    def test_upperlimits_excluded(self):
        from scoring.vet_bbh import fit_agn_baseline

        mags = [18.0, 18.2, 17.8, 18.1, 17.9]
        upperlimit = [False, False, False, False, True]
        phot = _phot_df(mags, [0.05] * len(mags), ["r"] * len(mags), upperlimit)
        # only 4 real detections, below min_baseline_pts=5
        assert fit_agn_baseline(phot, min_baseline_pts=5) == {}

    def test_filters_are_independent(self):
        from scoring.vet_bbh import fit_agn_baseline

        mags_r = [18.0, 18.2, 17.8, 18.1, 17.9]
        mags_g = [19.0, 19.1]  # too few points in g
        phot = pd.concat(
            [
                _phot_df(mags_r, [0.05] * len(mags_r), ["r"] * len(mags_r)),
                _phot_df(mags_g, [0.05] * len(mags_g), ["g"] * len(mags_g)),
            ],
            ignore_index=True,
        )
        baseline = fit_agn_baseline(phot, min_baseline_pts=5)
        assert "r" in baseline
        assert "g" not in baseline


class TestDetectFlare:
    def test_no_baseline_returns_nan(self):
        from scoring.vet_bbh import detect_flare

        postphot = _phot_df([17.0], [0.05], ["r"])
        sig, row = detect_flare(postphot, {})
        assert np.isnan(sig)
        assert row is None

    def test_no_postphot_returns_nan(self):
        from scoring.vet_bbh import detect_flare

        baseline = {"r": dict(mag=18.0, std=0.1, n=5)}
        sig, row = detect_flare(None, baseline)
        assert np.isnan(sig)
        assert row is None

    def test_filter_without_baseline_ignored(self):
        from scoring.vet_bbh import detect_flare

        baseline = {"r": dict(mag=18.0, std=0.1, n=5)}
        postphot = _phot_df([15.0], [0.05], ["g"])  # bright, but no g baseline
        sig, row = detect_flare(postphot, baseline)
        assert np.isnan(sig)
        assert row is None

    def test_significant_brightening_detected(self):
        from scoring.vet_bbh import detect_flare

        baseline = {"r": dict(mag=18.0, std=0.05, n=10)}
        # ~2 mag brighter than baseline with tight uncertainty -> huge significance
        postphot = _phot_df([16.0], [0.05], ["r"])
        sig, row = detect_flare(postphot, baseline, sigma_thresh=5.0)
        assert sig > 5.0
        assert row["mag"] == 16.0

    def test_dimming_is_not_flagged_as_the_max(self):
        from scoring.vet_bbh import detect_flare

        baseline = {"r": dict(mag=18.0, std=0.05, n=10)}
        # one brightening point and one dimming point in the same window --
        # the brightening one should win, and its significance should be positive
        postphot = _phot_df([16.0, 20.0], [0.05, 0.05], ["r", "r"])
        sig, row = detect_flare(postphot, baseline)
        assert row["mag"] == 16.0
        assert sig > 0

    def test_consistent_with_baseline_gives_low_significance(self):
        from scoring.vet_bbh import detect_flare

        baseline = {"r": dict(mag=18.0, std=0.1, n=10)}
        postphot = _phot_df([18.02], [0.05], ["r"])
        sig, row = detect_flare(postphot, baseline, sigma_thresh=5.0)
        assert sig < 5.0
