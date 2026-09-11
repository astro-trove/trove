"""
Unit tests for the hybrid distance score in scoring/distance_helpers.py.

These pin the score's shape contract -- the anchors at 0, 1 and 2 sigma -- so
that the hardcoded coefficients in distance_helpers cannot silently drift from
the behaviour they are documented as producing. That drift is exactly what
happened to the previous `box_edge_score` parameter, which claimed 0.95 at
2 sigma and delivered 0.805.
"""

import numpy as np
import pytest
from scipy.integrate import trapezoid

from scoring.distance_helpers import (
    bc,
    hybrid_distance_score,
    tophat_score,
    weight_logistic,
    WEIGHT_R0,
)

# The shape contract asserted below. These are the numbers documented in
# distance_helpers; the tests are what stop the code drifting from them.
NSIGMA, VETO_SCORE = 2.0, 0.05
REF_SIGMA, REF_SCORE = 1.0, 0.95

GW_MEAN, GW_STD = 300.0, 100.0


def at(u, **kw):
    """Top-hat score u sigma away from the GW mean."""
    return float(tophat_score(GW_MEAN + u * GW_STD, GW_MEAN, GW_STD, **kw))


class TestTophatAnchorsAreExact:
    def test_peak_is_exactly_one(self):
        assert at(0.0) == pytest.approx(1.0, abs=1e-12)

    def test_reference_anchor_is_exact(self):
        assert at(REF_SIGMA) == pytest.approx(REF_SCORE, abs=1e-12)

    def test_veto_anchor_is_exact(self):
        assert at(NSIGMA) == pytest.approx(VETO_SCORE, abs=1e-12)

    def test_anchors_hold_for_any_gw_sigma(self):
        for std in (5.0, 50.0, 1234.0):
            u = GW_MEAN + NSIGMA * std
            assert float(tophat_score(u, GW_MEAN, std)) == pytest.approx(
                VETO_SCORE, abs=1e-12)

    def test_symmetric_in_offset(self):
        for u in (0.5, 1.0, 2.0, 5.0):
            below = float(tophat_score(GW_MEAN - u * GW_STD, GW_MEAN, GW_STD))
            assert below == pytest.approx(at(u), rel=1e-12)


class TestTophatShape:
    def test_hard_veto_sits_at_two_sigma(self):
        assert at(1.0) > 0.9          # comfortably inside
        assert at(2.0) <= 0.05        # vetoed by 2 sigma
        assert at(2.5) < 0.02         # and stays down

    def test_strictly_decreasing(self):
        u = np.linspace(0, 40, 20001)
        v = tophat_score(GW_MEAN + u * GW_STD, GW_MEAN, GW_STD)
        assert np.all(np.diff(v) < 0)

    def test_bounded_and_finite_far_out(self):
        u = np.linspace(0, 1e4, 5001)
        v = tophat_score(GW_MEAN + u * GW_STD, GW_MEAN, GW_STD)
        assert np.all(np.isfinite(v))
        assert v.min() >= 0.0 and v.max() <= 1.0

    def test_never_reaches_zero(self):
        # scores multiply in scoring.util, so a hard zero would veto on distance alone
        assert at(1e6) > 0.0


class TestWeightLogistic:
    def test_crossover_is_exact(self):
        assert float(weight_logistic(WEIGHT_R0)) == pytest.approx(0.5, abs=1e-12)

    def test_monotonic_and_bounded(self):
        r = np.linspace(0, 50, 5001)
        w = weight_logistic(r)
        assert np.all(np.diff(w) >= 0)          # saturates to exactly 1.0 in float64
        assert w.min() >= 0.0 and w.max() <= 1.0

    def test_strictly_increasing_before_saturation(self):
        r = np.linspace(0, 5, 2001)
        w = weight_logistic(r)
        assert np.all(np.diff(w) > 0)
        assert 0.0 < w[0] < 0.05 and w[-1] > 0.99


class TestBhattacharyya:
    @staticmethod
    def _numeric(mu_gw, s_gw, mu_c, s_m, s_p, n=400_000, hi=1e4):
        x = np.linspace(1e-9, hi, n)
        gw = np.exp(-0.5 * ((x - mu_gw) / s_gw) ** 2)
        gw /= trapezoid(gw, x)
        c = np.where(x < mu_c,
                     np.exp(-0.5 * ((x - mu_c) / s_m) ** 2),
                     np.exp(-0.5 * ((x - mu_c) / s_p) ** 2))
        c /= trapezoid(c, x)
        return trapezoid(np.sqrt(gw * c), x)

    @pytest.mark.parametrize("args", [
        (300, 60, 300, 50, 50),
        (300, 60, 350, 20, 80),
        (300, 60, 500, 100, 100),
        (1000, 200, 900, 300, 100),
    ])
    def test_analytic_matches_numeric(self, args):
        assert bc(*args) == pytest.approx(self._numeric(*args), rel=2e-4)

    def test_bounded_by_one(self):
        # BC of two normalised PDFs cannot exceed 1
        for args in [(300, 60, 300, 50, 50), (300, 100, 300, 100, 100)]:
            assert bc(*args) <= 1.0 + 1e-9

    def test_identical_distributions_give_one(self):
        assert bc(300, 60, 300, 60, 60) == pytest.approx(1.0, rel=1e-6)


class TestHybridGuards:
    def test_rejects_negative_uncertainty(self):
        # the real failure: a LS row with inverted 68% bounds gave BC = 1.6377,
        # which np.clip turned into a perfect score
        assert np.isnan(hybrid_distance_score(69.8, 197.5, 18.1, 173.2, -108.9))

    def test_rejects_negative_distance(self):
        # a DELVE star with a negative photo-z
        assert np.isnan(hybrid_distance_score(300, -50.0, 100, 20, 20))

    def test_rejects_nan_inputs(self):
        assert np.isnan(hybrid_distance_score(300, 300, 100, np.nan, 50))
        assert np.isnan(hybrid_distance_score(np.nan, 300, 100, 50, 50))

    def test_rejects_non_positive_gw_sigma(self):
        assert np.isnan(hybrid_distance_score(300, 300, 0.0, 50, 50))

    def test_valid_input_is_in_range(self):
        s = hybrid_distance_score(300, 300, 100, 50, 50)
        assert 0.0 <= float(s) <= 1.0


class TestHybridBehaviour:
    def test_specz_host_at_gw_distance_scores_high(self):
        """The pathology the hybrid exists to fix: pure BC gave 0.14 here."""
        s = float(hybrid_distance_score(300, 300, 100, 1.0, 1.0))
        assert s > 0.9
        assert bc(300, 100, 300, 1.0, 1.0) < 0.2   # what the old scorer returned

    def test_well_measured_galaxy_beyond_veto_is_suppressed(self):
        s = float(hybrid_distance_score(300, 300 + 2 * 100, 100, 1.0, 1.0))
        assert s < 0.1

    def test_zero_uncertainty_is_handled_not_crashed(self):
        s = float(hybrid_distance_score(300, 300, 100, 0.0, 0.0))
        assert 0.0 <= s <= 1.0

    def test_both_tails_zero_is_delta_like_and_scores_high(self):
        """sigma_gal -> 0 at the GW distance is the top-hat limit."""
        assert float(hybrid_distance_score(300, 300, 100, 0.0, 0.0)) > 0.99

    @pytest.mark.parametrize("sm,sp", [(0.0, 300.0), (300.0, 0.0)])
    def test_one_sided_zero_does_not_collapse(self, sm, sp):
        """A zero on one tail is missing data, not a real measurement.

        Before the fill-from-the-other-tail repair, bc() could not be evaluated,
        bc_score fell back to 0, and that unavailable-treated-as-observed-zero
        dragged a perfectly centred galaxy down to 0.018.
        """
        s = float(hybrid_distance_score(300, 300, 100, sm, sp))
        assert s > 0.8

    def test_one_sided_zero_is_symmetric_in_which_tail_is_missing(self):
        lo = float(hybrid_distance_score(300, 300, 100, 0.0, 250.0))
        hi = float(hybrid_distance_score(300, 300, 100, 250.0, 0.0))
        assert lo == pytest.approx(hi, rel=1e-12)

    @pytest.mark.parametrize("sm", [1e-14, 1e-12, 1e-10, 1e-9, 1e-8, 1e-7, 1e-6])
    def test_tiny_tail_does_not_produce_a_spurious_perfect_score(self, sm):
        """bc() overflows for sigma many orders below the GW scale.

        It has returned inf and 8.7e101 there; without the guard the final clip
        turns that into 1.0. A galaxy that narrow is the delta-like limit, so
        the score should come from the top-hat instead.
        """
        centred = float(hybrid_distance_score(300, 300, 100, sm, 300.0))
        assert 0.0 <= centred <= 1.0
        # and a galaxy far outside the veto must still be suppressed
        far = float(hybrid_distance_score(300, 300 + 4 * 100, 100, sm, 300.0))
        assert far < 0.2

    def test_one_sided_zero_still_falls_off_with_offset(self):
        centred = float(hybrid_distance_score(300, 300, 100, 0.0, 300.0))
        far = float(hybrid_distance_score(300, 300 + 3 * 100, 100, 0.0, 300.0))
        assert far < centred
