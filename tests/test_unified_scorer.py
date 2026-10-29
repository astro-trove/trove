"""
Unit tests for scoring/unified_scorer.py.

The ORM and every external lookup are mocked, so these check which score
factors each EM model writes, not the values of the underlying calculations.
"""
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


EVENT_NAME = "S250208ad"


@pytest.fixture
def scorer():
    """Patch out everything `score` talks to. Yields the module and a dict of
    the mocks; `written` collects the (key, value) pairs saved as score factors."""
    from scoring import unified_scorer

    target = MagicMock(id=1, redshift=None)
    target.name = "AT2025abc"
    candidate = MagicMock()
    written = {}

    def _update(event_candidate, key, value, em_model):
        written[key] = value

    postphot = pd.DataFrame({"mjd": [60000.0], "mag": [18.0], "magerr": [0.1],
                             "filter": ["g"], "upperlimit": [False]})
    mocks = dict(
        _update_score_factor=MagicMock(side_effect=_update),
        delete_score_factor=MagicMock(),
        localization_sequence_from_name=MagicMock(),
        get_most_likely_class=MagicMock(return_value="BNS"),
        skymap_association=MagicMock(return_value=0.8),
        _localization_from_name=MagicMock(return_value=MagicMock(id=7)),
        host_distance_match=MagicMock(),
        get_distance_score=MagicMock(),
        classification_score=MagicMock(return_value=1),
        _get_pre_disc_phot=MagicMock(return_value=None),
        _get_post_disc_phot=MagicMock(return_value=postphot),
        _score_phot=MagicMock(return_value=(0.5, 1e42, 2.0, -0.3, None, None)),
        kilonova_score_candidate=MagicMock(return_value=0.7),
        fit_agn_baseline=MagicMock(return_value={"g": {"mag": 19.0, "std": 0.1}}),
        detect_flare=MagicMock(return_value=(5.0, postphot.iloc[0])),
        flare_confidence_score=MagicMock(return_value=0.9),
        _host_rows=MagicMock(return_value=[]),
        _agn_nucleus_row=MagicMock(return_value=None),
        nuclear_offset_score=MagicMock(return_value=(None, {})),
        vet_basic=MagicMock(),
    )
    with ExitStack() as stack:
        for name, mock in mocks.items():
            stack.enter_context(patch.object(unified_scorer, name, mock))
        for model in ("Target", "NonLocalizedEvent", "EventCandidate", "EventSequence"):
            mocks[model] = stack.enter_context(patch.object(unified_scorer, model))
        mocks["Target"].objects.get.return_value = target
        mocks["EventCandidate"].objects.get.return_value = candidate
        mocks["EventSequence"].objects.filter.return_value.last.return_value.details = {
            "time": "2025-02-08T00:00:00"
        }
        mocks["written"] = written
        yield unified_scorer, mocks


def _basic_results(agn_df=None, keep_vetting=True):
    agn_df = pd.DataFrame() if agn_df is None else agn_df
    return pd.DataFrame(), agn_df, keep_vetting


class TestScoreEarlyExit:

    def test_stops_when_basic_vetting_zeroed_the_score(self, scorer):
        unified_scorer, mocks = scorer
        unified_scorer.score(1, EVENT_NAME, "KN", basic_results=_basic_results(keep_vetting=False))

        assert mocks["written"] == {}
        mocks["_get_post_disc_phot"].assert_not_called()

    def test_runs_vet_basic_with_model_crossmatch_flags(self, scorer):
        unified_scorer, mocks = scorer
        mocks["vet_basic"].return_value = _basic_results(keep_vetting=False)
        unified_scorer.score(1, EVENT_NAME, "AGN-flare")

        kwargs = mocks["vet_basic"].call_args.kwargs
        # AGN-flare waives point source matching, an AGN is often in the PS catalogs
        assert kwargs["run_ps"] is False
        assert kwargs["run_mpc"] is True
        assert kwargs["run_agn"] is True
        assert kwargs["run_host_association"] is True

    def test_classified_out_candidate_is_not_scored_further(self, scorer):
        unified_scorer, mocks = scorer
        mocks["classification_score"].return_value = 0
        unified_scorer.score(1, EVENT_NAME, "AGN-flare", basic_results=_basic_results())

        assert mocks["written"] == {}


class TestScoreKN:

    def test_writes_localization_and_phot_metric_factors(self, scorer):
        unified_scorer, mocks = scorer
        unified_scorer.score(1, EVENT_NAME, "KN", basic_results=_basic_results())
        written = mocks["written"]

        assert written["agn_score"] == 1  # KN is only penalized when an AGN matches
        assert written["skymap_score"] == 0.8
        assert written["localization_id"] == 7
        assert written["phot_peak_lum"] == 1e42
        assert written["phot_peak_time"] == 2.0
        assert written["phot_decay_rate"] == -0.3
        assert written["predetection_score"] == 1
        assert "agn_flare_score" not in written
        assert "nuclear_offset_score" not in written
        mocks["detect_flare"].assert_not_called()

    def test_phot_params_include_model_time_window(self, scorer):
        unified_scorer, mocks = scorer
        unified_scorer.score(1, EVENT_NAME, "KN", basic_results=_basic_results())

        param_ranges = mocks["_score_phot"].call_args.kwargs["param_ranges"]
        assert param_ranges["t_pre"] == 0
        assert param_ranges["t_post"] == 25
        assert param_ranges["lum_max"] == [0, 1e43]

    def test_no_hosts_and_no_redshift_clears_host_factors(self, scorer):
        unified_scorer, mocks = scorer
        unified_scorer.score(1, EVENT_NAME, "KN", basic_results=_basic_results())

        deleted = {c.args[1] for c in mocks["delete_score_factor"].call_args_list}
        assert {"host_distance_score", "host_name", "host_catalog"} <= deleted
        mocks["get_distance_score"].assert_not_called()

    def test_kilonovascorer_replaces_phot_metrics(self, scorer):
        unified_scorer, mocks = scorer
        unified_scorer.score(1, EVENT_NAME, "KN", basic_results=_basic_results(),
                             phot_method="kilonovascorer")

        assert mocks["written"]["kilonova_score"] == 0.7
        mocks["_score_phot"].assert_not_called()

    def test_kilonovascorer_unavailable_records_reason(self, scorer):
        from scoring.kilonova_scorer_helpers.util import KilonovaScoreUnavailable

        unified_scorer, mocks = scorer
        mocks["kilonova_score_candidate"].side_effect = KilonovaScoreUnavailable("no distance")
        unified_scorer.score(1, EVENT_NAME, "KN", basic_results=_basic_results(),
                             phot_method="kilonovascorer")

        assert mocks["written"]["kilonova_skip_reason"] == "no distance"
        assert "kilonova_score" not in mocks["written"]


class TestScoreAGNFlare:

    @pytest.mark.parametrize("agn_df, expected", [
        (pd.DataFrame({"name": ["agn1"]}), 1),
        (pd.DataFrame(), 0.1),
    ])
    def test_agn_score_rewards_an_agn_match(self, scorer, agn_df, expected):
        unified_scorer, mocks = scorer
        unified_scorer.score(1, EVENT_NAME, "AGN-flare", basic_results=_basic_results(agn_df))

        assert mocks["written"]["agn_score"] == expected

    def test_writes_flare_factors_and_skips_phot_metrics(self, scorer):
        unified_scorer, mocks = scorer
        unified_scorer.score(1, EVENT_NAME, "AGN-flare", basic_results=_basic_results())
        written = mocks["written"]

        assert written["agn_flare_score"] == 0.9
        assert written["baseline_mag_g"] == 19.0
        assert written["baseline_std_g"] == 0.1
        assert "predetection_score" not in written
        mocks["_score_phot"].assert_not_called()
        mocks["kilonova_score_candidate"].assert_not_called()
        # no host or AGN rows, so there is no nuclear offset to score
        mocks["delete_score_factor"].assert_any_call(
            mocks["EventCandidate"].objects.get.return_value, "nuclear_offset_score"
        )

    def test_flare_threshold_comes_from_model(self, scorer):
        unified_scorer, mocks = scorer
        unified_scorer.score(1, EVENT_NAME, "AGN-flare", basic_results=_basic_results())

        assert mocks["flare_confidence_score"].call_args.args == (5.0, 3.25)


class TestUpdateScoreFactor:

    @pytest.mark.parametrize("key, expected_model", [
        ("skymap_score", "basic"),
        ("host_distance_score", "basic"),
        ("agn_score", "KN"),
        ("phot_peak_lum", "KN"),
    ])
    def test_model_independent_keys_saved_under_basic(self, key, expected_model):
        from scoring import scoring

        with patch.object(scoring, "ScoreFactor") as ScoreFactor:
            scoring.update_score_factor("ec", key, 1.0, em_model="KN")

        ScoreFactor.objects.create.assert_called_once_with(
            event_candidate="ec", key=key, value=1.0, em_model=expected_model
        )
