"""
Unit tests for the pure logic in scoring/vet_bbh.py: the baseline-variability fit
and flare-anomaly detection used to score BBH/AGN-flare candidates.
"""

import numpy as np
import pandas as pd
import pytest


def _phot_df(mag, magerr, filt, upperlimit=None, dt=None):
    n = len(mag)
    d = dict(
        mag=mag,
        magerr=magerr,
        filter=filt,
        upperlimit=[False] * n if upperlimit is None else upperlimit,
    )
    if dt is not None:
        d["dt"] = dt
    return pd.DataFrame(d)


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


class TestFlareConfidenceScore:
    def test_zero_significance_near_floor(self):
        from scoring.vet_bbh import flare_confidence_score
        from scoring.vet_phot import PHOT_SCORE_MIN

        score = flare_confidence_score(0.0, thresh=5.0, floor=PHOT_SCORE_MIN)
        assert score == pytest.approx(PHOT_SCORE_MIN, abs=0.05)

    def test_at_threshold_score_is_high_but_not_capped(self):
        from scoring.vet_bbh import flare_confidence_score

        score = flare_confidence_score(5.0, thresh=5.0)
        assert 0.8 < score < 1.0

    def test_well_above_threshold_saturates_near_one(self):
        from scoring.vet_bbh import flare_confidence_score

        score = flare_confidence_score(20.0, thresh=5.0)
        assert score == pytest.approx(1.0, abs=1e-3)

    def test_well_below_zero_floors(self):
        from scoring.vet_bbh import flare_confidence_score
        from scoring.vet_phot import PHOT_SCORE_MIN

        score = flare_confidence_score(-5.0, thresh=5.0, floor=PHOT_SCORE_MIN)
        assert score == pytest.approx(PHOT_SCORE_MIN, abs=1e-3)

    def test_monotonically_increasing_with_significance(self):
        from scoring.vet_bbh import flare_confidence_score

        sigs = [-5, 0, 2.5, 5, 10, 20]
        scores = [flare_confidence_score(s, thresh=5.0) for s in sigs]
        assert scores == sorted(scores)


class TestEstimateFlareExtent:
    def test_no_baseline_returns_none_none(self):
        from scoring.vet_bbh import estimate_flare_extent

        postphot = _phot_df([17.0], [0.05], ["r"], dt=[30.0])
        delay, duration = estimate_flare_extent(postphot, {})
        assert delay is None
        assert duration is None

    def test_single_significant_point_gives_delay_no_duration(self):
        from scoring.vet_bbh import estimate_flare_extent

        baseline = {"r": dict(mag=18.0, std=0.05, n=10)}
        postphot = _phot_df([16.0], [0.05], ["r"], dt=[42.0])
        delay, duration = estimate_flare_extent(postphot, baseline)
        assert delay == pytest.approx(42.0)
        assert duration is None

    def test_two_significant_points_bracket_duration(self):
        from scoring.vet_bbh import estimate_flare_extent

        baseline = {"r": dict(mag=18.0, std=0.05, n=10)}
        # both well above the default extent_sigma_thresh=3.0; the second (dt=60) is
        # also the most significant, so it should be reported as the delay
        postphot = _phot_df(
            [17.0, 16.0], [0.05, 0.05], ["r", "r"], dt=[30.0, 60.0]
        )
        delay, duration = estimate_flare_extent(postphot, baseline)
        assert delay == pytest.approx(60.0)
        assert duration == pytest.approx(30.0)

    def test_insignificant_points_dont_count_toward_duration(self):
        from scoring.vet_bbh import estimate_flare_extent

        baseline = {"r": dict(mag=18.0, std=0.05, n=10)}
        # one clearly significant point (dt=50) and one consistent-with-baseline
        # point (dt=10) that shouldn't count toward the elevated-episode span
        postphot = _phot_df(
            [16.0, 18.01], [0.05, 0.05], ["r", "r"], dt=[50.0, 10.0]
        )
        delay, duration = estimate_flare_extent(postphot, baseline)
        assert delay == pytest.approx(50.0)
        assert duration is None


class TestBoxEdgeScore:
    def test_inside_box_is_full_score(self):
        from scoring.vet_bbh import _box_edge_score

        assert _box_edge_score(50.0, 0.0, 100.0, 100.0, 0.1) == 1.0

    def test_at_edges_is_full_score(self):
        from scoring.vet_bbh import _box_edge_score

        assert _box_edge_score(0.0, 0.0, 100.0, 100.0, 0.1) == 1.0
        assert _box_edge_score(100.0, 0.0, 100.0, 100.0, 0.1) == 1.0

    def test_far_outside_box_floors(self):
        from scoring.vet_bbh import _box_edge_score

        assert _box_edge_score(10000.0, 0.0, 100.0, 100.0, 0.1) == pytest.approx(0.1, abs=1e-3)

    def test_monotonically_decreasing_away_from_box(self):
        from scoring.vet_bbh import _box_edge_score

        xs = [100.0, 150.0, 200.0, 500.0]
        scores = [_box_edge_score(x, 0.0, 100.0, 100.0, 0.1) for x in xs]
        assert scores == sorted(scores, reverse=True)


class TestFlareShapeScore:
    def test_delay_and_duration_inside_a_model_scores_high(self):
        from scoring.vet_bbh import flare_shape_score

        # inside jrr_i's (50-150, 20-150) box
        assert flare_shape_score(80.0, 60.0) == pytest.approx(1.0)

    def test_delay_only_still_checked_against_delay_range(self):
        from scoring.vet_bbh import flare_shape_score

        # duration unconstrained (None); delay=80 still inside multiple models'
        # delay ranges, so shouldn't be penalized for the missing duration
        assert flare_shape_score(80.0, None) == pytest.approx(1.0)

    def test_delay_far_outside_all_models_floors(self):
        from scoring.vet_bbh import flare_shape_score
        from scoring.vet_phot import PHOT_SCORE_MIN

        score = flare_shape_score(1e6, None)
        assert score == pytest.approx(PHOT_SCORE_MIN, abs=1e-3)

    def test_best_matching_model_wins(self):
        from scoring.vet_bbh import flare_shape_score

        # delay=10 is outside jrr_i's (50-150) range but inside mck19's and
        # tgw24's (0-300) ranges -- should still score well via those, not be
        # dragged down by jrr_i
        score = flare_shape_score(10.0, None)
        assert score == pytest.approx(1.0)


class TestFlareShapeScoresByModel:
    def test_returns_one_score_per_model(self):
        from scoring.vet_bbh import flare_shape_scores_by_model, FLARE_SHAPE_MODELS

        scores = flare_shape_scores_by_model(80.0, 60.0)
        assert set(scores.keys()) == set(FLARE_SHAPE_MODELS.keys())

    def test_max_of_breakdown_matches_aggregate(self):
        from scoring.vet_bbh import flare_shape_score, flare_shape_scores_by_model

        for delay, duration in [(80.0, 60.0), (10.0, None), (1e6, None)]:
            scores = flare_shape_scores_by_model(delay, duration)
            assert flare_shape_score(delay, duration) == pytest.approx(max(scores.values()))

    def test_penalizes_model_whose_delay_range_is_missed(self):
        from scoring.vet_bbh import flare_shape_scores_by_model

        # delay=10 is outside jrr_i's (50-150) range, so its score should be
        # lower than mck19/tgw24's, which both cover 0-300
        scores = flare_shape_scores_by_model(10.0, None)
        assert scores["jrr_i"] < scores["mck19"]
        assert scores["jrr_i"] < scores["tgw24"]
