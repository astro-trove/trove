"""
Unit tests for the EM model JSONs in scoring/em_models/ and their pydantic schema.
"""
import os

import pytest


class TestEMModelFiles:
    """Every EM model JSON loads and describes the transient it's named for."""

    def test_expected_models_are_loaded(self):
        from scoring.em_models.util import MODELS, MODELS_BY_NAME

        assert set(MODELS_BY_NAME) == {"KN", "KN-in-SN", "super-KN", "AGN-flare"}
        assert len(MODELS) == len(MODELS_BY_NAME)  # no two files share a name

    def test_template_is_not_loaded(self):
        from scoring.em_models.util import MODEL_FILES

        assert MODEL_FILES
        assert all(f.endswith(".json") for f in MODEL_FILES)
        assert "model.template" not in {os.path.basename(f) for f in MODEL_FILES}

    @pytest.mark.parametrize("name", ["KN", "KN-in-SN", "super-KN", "AGN-flare"])
    def test_detection_horizon_is_an_interval(self, name):
        from scoring.em_models.util import MODELS_BY_NAME

        lo, hi = MODELS_BY_NAME[name].detection_horizon
        assert lo < hi

    def test_detection_horizon_defaults_follow_the_models(self):
        from scoring.config import DETECTION_HORIZON_DEFAULTS

        assert list(DETECTION_HORIZON_DEFAULTS["BNS"]) == [0, 10]
        assert list(DETECTION_HORIZON_DEFAULTS["NSBH"]) == [0, 10]
        assert list(DETECTION_HORIZON_DEFAULTS["SSM"]) == [-1, 10]
        assert list(DETECTION_HORIZON_DEFAULTS["BBH"]) == [0, 400]

    def test_agn_flare_horizon_matches_bbh_model(self):
        from custom_code.hooks import AGN_FLARE_HORIZON_DAYS
        from scoring.em_models.util import MODELS_BY_NAME

        assert AGN_FLARE_HORIZON_DAYS == MODELS_BY_NAME["AGN-flare"].detection_horizon[1] == 400

    def test_agn_flare_has_flare_parameters(self):
        from scoring.em_models.util import MODELS_BY_NAME

        model = MODELS_BY_NAME["AGN-flare"]
        assert model.phot_method.agn_flare
        assert not model.phot_method.metric
        for key in ("min_baseline_pts", "flare_sigma_thresh", "flare_pair_max_gap_days",
                    "flare_corroboration_window_days", "flare_score_center_frac",
                    "flare_score_width_frac"):
            assert getattr(model.phot_parameter, key) is not None, key

    @pytest.mark.parametrize("name", ["KN", "KN-in-SN", "super-KN"])
    def test_metric_models_have_phot_metric_parameters(self, name):
        from scoring.em_models.util import MODELS_BY_NAME

        model = MODELS_BY_NAME[name]
        assert model.phot_method.metric
        for key in ("lum_max", "peak_time", "decay_rate", "max_predets", "min_time_separation"):
            assert getattr(model.phot_parameter, key) is not None, key

    def test_ps_waived_for_agn_flare_only_on_agn_match(self):
        from scoring.util import ps_counts_toward

        assert ps_counts_toward("KN", agn_score=1.0)
        assert ps_counts_toward("AGN-flare", agn_score=None)
        assert ps_counts_toward("AGN-flare", agn_score=0.1)
        assert not ps_counts_toward("AGN-flare", agn_score=1.0)


class TestTransientModelSchema:
    """The pydantic schema rejects malformed model JSONs."""

    def test_missing_field_is_rejected(self, tmp_path):
        import json

        from pydantic import ValidationError

        from scoring.em_models.schema import TransientModelSchema
        from scoring.em_models.util import MODELS

        data = MODELS[0].model_dump()
        del data["score_controls"]
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(data))

        with pytest.raises(ValidationError):
            TransientModelSchema.from_json(bad)
