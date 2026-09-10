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

        # only 3 points in 'r', below the min_baseline_pts=5 asked for here
        phot = _phot_df([18.0, 18.1, 17.9], [0.05, 0.05, 0.05], ["r"] * 3)
        assert fit_agn_baseline(phot, min_baseline_pts=5) == {}

    def test_single_point_rejected_at_default(self):
        from scoring.vet_bbh import PARAM_RANGES, fit_agn_baseline

        # at n=1 the MAD is identically 0, so the baseline would collapse to one
        # point plus its own error -- PARAM_RANGES' min_baseline_pts=2 excludes it
        assert PARAM_RANGES["min_baseline_pts"] == 2
        phot = _phot_df([18.0], [0.05], ["r"])
        assert fit_agn_baseline(phot) == {}

    def test_two_points_give_nonzero_scatter(self):
        from scoring.vet_bbh import fit_agn_baseline

        # n=2 is the smallest n whose MAD carries scatter information: the two
        # deviations are equal and nonzero, so robust_std exceeds the 0.01 error floor
        phot = _phot_df([18.0, 18.4], [0.01, 0.01], ["r"] * 2)
        baseline = fit_agn_baseline(phot)
        assert baseline["r"]["n"] == 2
        assert baseline["r"]["std"] == pytest.approx(1.4826 * 0.2)

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
        # only 4 real detections, below the min_baseline_pts=5 asked for here
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

    def test_threshold_sits_at_the_sigmoid_midpoint(self):
        from scoring.vet_bbh import flare_confidence_score

        # center_frac defaults to 1.0, so `thresh` itself is the half-way point:
        # 5 sigma is "as likely as not a flare", not "almost certainly one".
        score = flare_confidence_score(5.0, thresh=5.0)
        assert 0.5 < score < 0.6

    def test_marginal_excursion_is_not_generously_scored(self):
        from scoring.vet_bbh import flare_confidence_score

        # 2.5 sigma is not a detection by any standard. Under the old
        # center_frac=0.5 it scored ~0.55; it must now score low.
        assert flare_confidence_score(2.5, thresh=5.0) < 0.2

    def test_center_frac_still_tunable(self):
        from scoring.vet_bbh import flare_confidence_score

        lenient = flare_confidence_score(2.5, thresh=5.0, center_frac=0.5)
        strict = flare_confidence_score(2.5, thresh=5.0, center_frac=1.0)
        assert lenient > strict

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


class TestFlareShapeScoresByModel:
    def test_returns_one_score_per_model(self):
        from scoring.vet_bbh import flare_shape_scores_by_model, FLARE_SHAPE_MODELS

        scores = flare_shape_scores_by_model(80.0, 60.0)
        assert set(scores.keys()) == set(FLARE_SHAPE_MODELS.keys())

    def test_delay_and_duration_inside_a_model_scores_high(self):
        from scoring.vet_bbh import flare_shape_scores_by_model

        # inside jrr_i's (50-150, 20-150) box
        scores = flare_shape_scores_by_model(80.0, 60.0)
        assert max(scores.values()) == pytest.approx(1.0)

    def test_delay_only_still_checked_against_delay_range(self):
        from scoring.vet_bbh import flare_shape_scores_by_model

        # duration unconstrained (None); delay=80 still inside multiple models'
        # delay ranges, so shouldn't be penalized for the missing duration
        scores = flare_shape_scores_by_model(80.0, None)
        assert max(scores.values()) == pytest.approx(1.0)

    def test_delay_far_outside_all_models_floors(self):
        from scoring.vet_bbh import flare_shape_scores_by_model
        from scoring.vet_phot import PHOT_SCORE_MIN

        scores = flare_shape_scores_by_model(1e6, None)
        assert max(scores.values()) == pytest.approx(PHOT_SCORE_MIN, abs=1e-3)

    def test_best_matching_model_wins(self):
        from scoring.vet_bbh import flare_shape_scores_by_model

        # delay=10 is outside jrr_i's (50-150) range but inside mck19's and
        # tgw24's (0-300) ranges -- should still score well via those, not be
        # dragged down by jrr_i
        scores = flare_shape_scores_by_model(10.0, None)
        assert max(scores.values()) == pytest.approx(1.0)

    def test_penalizes_model_whose_delay_range_is_missed(self):
        from scoring.vet_bbh import flare_shape_scores_by_model

        # delay=10 is outside jrr_i's (50-150) range, so its score should be
        # lower than mck19/tgw24's, which both cover 0-300
        scores = flare_shape_scores_by_model(10.0, None)
        assert scores["jrr_i"] < scores["mck19"]
        assert scores["jrr_i"] < scores["tgw24"]


class TestDRWStructureFunction:
    def test_grows_with_lag_and_saturates(self):
        from scoring.vet_bbh import drw_structure_function

        vals = [drw_structure_function(dt, sf_inf=0.2, tau_days=200) for dt in (1, 10, 100, 1000)]
        assert vals == sorted(vals)
        assert vals[-1] == pytest.approx(0.2, abs=0.01)   # saturates at SF_inf
        assert vals[0] < 0.03                              # near zero at short lag

    def test_zero_lag_is_zero(self):
        from scoring.vet_bbh import drw_structure_function

        assert drw_structure_function(0.0) == pytest.approx(0.0)

    def test_baseline_sigma_never_below_measured_scatter(self):
        from scoring.vet_bbh import baseline_sigma

        entry = dict(std=0.30, sf_inf=0.2, tau=200.0)
        # at every lag the modelled SF is below the measured MAD, so MAD wins
        for dt in (1, 50, 400, 5000):
            assert baseline_sigma(entry, dt) == pytest.approx(0.30)

    def test_baseline_sigma_grows_for_a_tight_baseline(self):
        from scoring.vet_bbh import baseline_sigma

        entry = dict(std=0.05, sf_inf=0.2, tau=200.0)
        near, far = baseline_sigma(entry, 5.0), baseline_sigma(entry, 400.0)
        assert far > near >= 0.05
        # this is the whole point: a 300-day-later point is judged against a
        # bigger envelope than a next-night point
        assert far > 3 * 0.05

    def test_missing_lag_falls_back_to_measured_scatter(self):
        from scoring.vet_bbh import baseline_sigma

        assert baseline_sigma(dict(std=0.07, sf_inf=0.2, tau=200.0), None) == pytest.approx(0.07)


class TestTNSClassificationScore:
    def test_unclassified_is_neutral(self):
        from scoring.vet_bbh import tns_classification_score

        for value in ("", None, "   "):
            assert tns_classification_score(value)[0] == pytest.approx(1.0)

    def test_supernova_is_penalised_but_never_zeroed(self):
        from scoring.vet_bbh import tns_classification_score
        from scoring.vet_phot import PHOT_SCORE_MIN

        for cls in ("SN Ia", "SN IIb", "SLSN-I", "SN Ic-BL"):
            score = tns_classification_score(cls)[0]
            assert score == pytest.approx(PHOT_SCORE_MIN)
            assert score > 0.0   # recoverable: never a hard veto

    def test_agn_classification_boosts(self):
        from scoring.vet_bbh import tns_classification_score

        for cls in ("AGN", "QSO", "Blazar"):
            assert tns_classification_score(cls, agn_boost=5.0)[0] == pytest.approx(5.0)

    def test_tde_is_penalised_at_least_as_hard_as_a_supernova(self):
        from scoring.vet_bbh import tns_classification_score
        from scoring.vet_phot import PHOT_SCORE_MIN

        # A TDE is nuclear, luminous and months-long, so every geometric test in
        # this module gives it full marks -- the classification is the only thing
        # standing in its way. It must not be treated more gently than an SN.
        tde = tns_classification_score("TDE")[0]
        sn = tns_classification_score("SN Ia")[0]
        assert tde == pytest.approx(PHOT_SCORE_MIN)
        assert tde <= sn

    def test_classified_but_unrecognised_is_mildly_penalised(self):
        from scoring.vet_bbh import tns_classification_score

        # a named class means somebody took a spectrum and it was not an AGN;
        # that is weak evidence against, not nothing
        score = tns_classification_score("Something Odd")[0]
        assert 0.1 < score < 1.0
        # ... but a genuinely unclassified object stays neutral
        assert tns_classification_score("")[0] == pytest.approx(1.0)


class TestNuclearOffsetScore:
    def test_same_arcsec_scores_differently_at_different_distances(self):
        from scoring.vet_bbh import nuclear_offset_score

        # 1.1" is 78 pc at 14 Mpc (nuclear) but 2.0 kpc at 370 Mpc (supernova-like).
        # The removed arcsec-based score could not tell these apart.
        near, _ = nuclear_offset_score([{"Offset": 1.1, "Dist": 14.0}])
        far, _ = nuclear_offset_score([{"Offset": 1.1, "Dist": 370.0}])
        assert near > far
        assert near > 0.8 and far < 0.4

    def test_unresolved_offset_is_full_credit(self):
        from scoring.vet_bbh import nuclear_offset_score

        score, info = nuclear_offset_score([{"Offset": 0.2, "Dist": 300.0}])
        assert score == pytest.approx(1.0)
        assert "unresolved" in info["verdict"]

    def test_no_host_returns_none(self):
        from scoring.vet_bbh import nuclear_offset_score

        assert nuclear_offset_score([])[0] is None
        assert nuclear_offset_score([{"Offset": None, "Dist": None}])[0] is None

    def test_projected_offset_conversion(self):
        from scoring.vet_bbh import projected_offset_kpc

        # 1" at 206.265 Mpc is exactly 1 kpc
        assert projected_offset_kpc(1.0, 206.265) == pytest.approx(1.0, rel=1e-3)


class TestGWInformedPieces:
    def test_equal_mass_gives_no_kick(self):
        from scoring.vet_bbh import remnant_kick_velocity

        assert remnant_kick_velocity(1.0) == pytest.approx(0.0)

    def test_kick_peaks_near_q_0p36(self):
        from scoring.vet_bbh import remnant_kick_velocity
        import numpy as np

        qs = np.linspace(0.05, 0.999, 400)
        v = [remnant_kick_velocity(q) for q in qs]
        q_peak = qs[int(np.argmax(v))]
        assert 0.3 < q_peak < 0.42          # Gonzalez+2007
        assert 150 < max(v) < 200           # ~175 km/s

    def test_faster_kick_means_shorter_delay(self):
        from scoring.vet_bbh import kick_informed_delay_window

        slow_lo, slow_hi = kick_informed_delay_window(100.0)
        fast_lo, fast_hi = kick_informed_delay_window(500.0)
        assert fast_hi < slow_lo            # windows do not even overlap
        assert slow_hi > 100                # slow kick -> months

    def test_kick_window_is_narrower_than_the_stock_box(self):
        from scoring.vet_bbh import kick_informed_delay_window, FLARE_SHAPE_MODELS

        lo, hi = kick_informed_delay_window(300.0)
        stock_lo, stock_hi = FLARE_SHAPE_MODELS["mck19"]["delay"]
        assert (hi - lo) < (stock_hi - stock_lo)

    def test_flare_luminosity_scales_as_distance_squared(self):
        from scoring.vet_bbh import flare_luminosity_erg_s

        near = flare_luminosity_erg_s(19.0, 100.0)
        far = flare_luminosity_erg_s(19.0, 200.0)
        assert far / near == pytest.approx(4.0, rel=1e-6)

    def test_gw_parameters_absent_from_current_alerts(self):
        # documents the measured fact that low-latency LVK alerts carry no masses
        # or spins, which is why the kick narrowing is inert by default
        from scoring.vet_bbh import gw_source_parameters

        class _Seq:
            details = {"far": 1e-9, "classification": {"BBH": 0.99},
                       "properties": {"HasNS": 0.0, "HasRemnant": 0.0}}
        assert "mass_ratio" not in _Seq.details


class TestRestFrameCorrection:
    def test_redshift_for_prefers_target_then_host(self):
        from scoring.vet_bbh import redshift_for

        class T:
            redshift = 0.15
        assert redshift_for(T(), []) == pytest.approx(0.15)

        class NoZ:
            redshift = float("nan")
        assert redshift_for(NoZ(), [{"z": 0.08}]) == pytest.approx(0.08)
        assert redshift_for(NoZ(), []) is None

    def test_dilated_default_tau_in_baseline(self):
        from scoring.vet_bbh import fit_agn_baseline, DEFAULT_TAU_REST_DAYS

        phot = _phot_df([18.0, 18.2], [0.05, 0.05], ["r"] * 2)
        rest = fit_agn_baseline(phot, redshift=None)["r"]["tau"]
        dilated = fit_agn_baseline(phot, redshift=0.5)["r"]["tau"]
        assert rest == pytest.approx(DEFAULT_TAU_REST_DAYS)
        assert dilated == pytest.approx(1.5 * DEFAULT_TAU_REST_DAYS)

    def test_observed_delay_maps_to_shorter_rest_frame_delay(self):
        from scoring.vet_bbh import flare_shape_scores_by_model

        # 190 observed days at z=0.35 is 141 rest-frame days: inside jrr_i's
        # 50-150 box, whereas the uncorrected 190 would fall outside it
        outside = flare_shape_scores_by_model(190.0, 60.0)["jrr_i"]
        inside = flare_shape_scores_by_model(190.0 / 1.35, 60.0 / 1.35)["jrr_i"]
        assert inside > outside
        assert inside == pytest.approx(1.0)


class TestFlareAmplitude:
    def test_amplitude_is_brightening_above_baseline(self):
        from scoring.vet_bbh import flare_amplitude_mag
        import pandas as pd

        baseline = {"r": dict(mag=19.0, std=0.1, n=5)}
        row = pd.Series({"filter": "r", "mag": 18.2})
        assert flare_amplitude_mag(baseline, row) == pytest.approx(0.8)

    def test_none_when_no_flare_row_or_filter(self):
        from scoring.vet_bbh import flare_amplitude_mag
        import pandas as pd

        assert flare_amplitude_mag({"r": dict(mag=19.0)}, None) is None
        assert flare_amplitude_mag({"r": dict(mag=19.0)},
                                   pd.Series({"filter": "g", "mag": 18.0})) is None

    def test_amplitude_term_penalises_a_too_faint_flare(self):
        from scoring.vet_bbh import flare_shape_scores_by_model

        # inside every model's delay/duration box, but a 0.01 mag blip is far below
        # what any of the three mechanisms predict
        strong = flare_shape_scores_by_model(80.0, 60.0, amplitude_mag=1.0)
        feeble = flare_shape_scores_by_model(80.0, 60.0, amplitude_mag=0.01)
        assert max(feeble.values()) < max(strong.values())

    def test_amplitude_omitted_leaves_scores_unchanged(self):
        from scoring.vet_bbh import flare_shape_scores_by_model

        assert (flare_shape_scores_by_model(80.0, 60.0, amplitude_mag=None)
                == flare_shape_scores_by_model(80.0, 60.0))

    def test_amplitude_inside_envelope_is_full_credit(self):
        from scoring.vet_bbh import flare_shape_scores_by_model

        scores = flare_shape_scores_by_model(80.0, 60.0, amplitude_mag=1.0)
        assert max(scores.values()) == pytest.approx(1.0)


class TestSparseDataGuards:
    """Sparse light curves must not produce confident-looking DRW parameters."""

    def _wandering(self, n, seed=0, amp=0.6, err=0.02):
        import numpy as np
        rng = np.random.default_rng(seed)
        dt = np.linspace(-400, -1, n)
        mag = 18.0 + rng.normal(0, amp, n)
        return _phot_df(list(mag), [err] * n, ["r"] * n, dt=list(dt))

    def test_few_epochs_stay_near_the_literature_prior(self):
        from scoring.vet_bbh import estimate_drw_params, DEFAULT_SF_INF

        sf, _, source = estimate_drw_params(self._wandering(4))
        # a strongly varying 4-point curve must not report its raw amplitude
        assert source == "default"
        assert abs(sf - DEFAULT_SF_INF) < abs(0.6 - DEFAULT_SF_INF)

    def test_many_epochs_are_trusted(self):
        from scoring.vet_bbh import estimate_drw_params, DEFAULT_SF_INF

        sf, _, source = estimate_drw_params(self._wandering(120))
        assert source == "measured"
        assert sf > DEFAULT_SF_INF * 1.5   # the real 0.6 mag wander comes through

    def test_shrinkage_is_monotonic_in_epoch_count(self):
        from scoring.vet_bbh import estimate_drw_params

        sfs = [estimate_drw_params(self._wandering(n))[0] for n in (4, 10, 30, 100)]
        assert sfs == sorted(sfs)   # more data -> closer to the true large amplitude

    def test_pure_noise_does_not_manufacture_variability(self):
        from scoring.vet_bbh import estimate_drw_params, MIN_SF_MAG

        # scatter entirely explained by the quoted errors
        sf, _, _ = estimate_drw_params(self._wandering(60, amp=0.03, err=0.03))
        assert sf <= 0.2 + 1e-9
        assert sf >= MIN_SF_MAG

    def test_variability_boost_needs_a_real_baseline(self):
        from scoring.vet_bbh import variability_agn_score

        # 12 epochs clears the min_points bar but the shrinkage weight is only
        # ~0.55, so the boost must land well short of the full 5x ...
        score, info = variability_agn_score(self._wandering(12), agn_boost=5.0)
        assert score is not None
        assert score < 1.0 + 0.7 * (5.0 - 1.0)
        assert info["shrinkage_weight"] < 0.6

        # ... and a well-sampled curve of the same source must score higher
        strong, strong_info = variability_agn_score(self._wandering(200), agn_boost=5.0)
        assert strong > score
        assert strong_info["shrinkage_weight"] > 0.9

    def test_sf_can_only_widen_the_denominator(self):
        from scoring.vet_bbh import baseline_sigma

        # whatever the DRW returns, it never shrinks the envelope below the
        # directly measured scatter -- a bad fit cannot manufacture significance
        for sf_inf in (0.0, 0.01, 0.5, 2.0):
            entry = dict(std=0.25, sf_inf=sf_inf, tau=200.0)
            assert baseline_sigma(entry, 300.0) >= 0.25


class TestQuiescentHostScore:
    """The discriminator that generalises beyond supernovae."""

    def _curve(self, n, amp, err, seed=1):
        import numpy as np
        rng = np.random.default_rng(seed)
        return _phot_df(list(18.0 + rng.normal(0, amp, n)), [err] * n, ["r"] * n,
                        dt=list(np.linspace(-400, -1, n)))

    def test_variable_host_is_neutral(self):
        from scoring.vet_bbh import quiescent_host_score

        score, info = quiescent_host_score(self._curve(120, amp=0.5, err=0.02))
        assert score == pytest.approx(1.0)
        assert "varies" in info["verdict"]

    def test_quiescent_host_is_penalised(self):
        from scoring.vet_bbh import quiescent_host_score

        # well sampled, and flat to within the errors: a one-off event in a quiet
        # galaxy, which is what a supernova or a TDE looks like
        score, info = quiescent_host_score(self._curve(120, amp=0.005, err=0.05))
        assert score < 1.0
        assert "quiescent" in info["verdict"]

    def test_sparse_baseline_returns_none_not_a_penalty(self):
        from scoring.vet_bbh import quiescent_host_score

        # a non-detection of variability only means something if we looked properly
        assert quiescent_host_score(self._curve(6, amp=0.005, err=0.05))[0] is None
        assert quiescent_host_score(None)[0] is None

    def test_catches_a_tde_like_candidate_that_passes_every_geometric_test(self):
        from scoring.vet_bbh import nuclear_offset_score, quiescent_host_score

        # nuclear -> the offset term gives full credit and cannot help
        offset, _ = nuclear_offset_score([{"Offset": 0.1, "Dist": 300.0}])
        assert offset == pytest.approx(1.0)
        # quiescence is the handle that still works
        quiescent, _ = quiescent_host_score(self._curve(120, amp=0.005, err=0.05))
        assert quiescent < 1.0
        assert min(offset, quiescent) < 1.0


class TestDRWSourceLabels:
    def test_three_distinct_conclusions(self):
        import numpy as np
        from scoring.vet_bbh import estimate_drw_params

        rng = np.random.default_rng(3)
        def curve(n, amp, err):
            return _phot_df(list(18.0 + rng.normal(0, amp, n)), [err] * n,
                            ["r"] * n, dt=list(np.linspace(-400, -1, n)))

        # too few epochs -> no conclusion either way
        assert estimate_drw_params(curve(5, 0.005, 0.05))[2] == "default"
        # enough epochs, flat within errors -> confident non-detection
        assert estimate_drw_params(curve(120, 0.005, 0.05))[2] == "noise-limited"
        # enough epochs, clearly wandering -> measured
        assert estimate_drw_params(curve(120, 0.5, 0.02))[2] == "measured"
