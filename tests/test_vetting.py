"""
Unit tests for candidate vetting functions.

These test the pure logic functions in candidate_vetting/ and scoring/.
"""

import pytest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch


class TestPcc:
    """Tests for the probability of chance coincidence function."""

    def test_pcc_basic(self):
        """Test basic PCC calculation."""
        from candidate_vetting.vet import pcc

        r = np.array([1.0, 5.0, 10.0])
        m = np.array([18.0, 19.0, 20.0])

        result = pcc(r, m)

        assert len(result) == 3
        assert all(0 <= p <= 1 for p in result)

    def test_pcc_small_offset(self):
        """Test PCC with small offset (should give low probability)."""
        from candidate_vetting.vet import pcc

        r = np.array([0.1])
        m = np.array([18.0])

        result = pcc(r, m)
        assert result[0] < 0.1

    def test_pcc_large_offset(self):
        """Test PCC with large offset (should give higher probability)."""
        from candidate_vetting.vet import pcc

        r = np.array([50.0])
        m = np.array([18.0])

        result = pcc(r, m)
        assert result[0] > 0.1

    def test_pcc_bright_vs_faint(self):
        """Test that brighter galaxies have lower PCC at same offset."""
        from candidate_vetting.vet import pcc

        r = np.array([5.0, 5.0])
        m = np.array([16.0, 22.0])

        result = pcc(r, m)
        assert result[0] < result[1]

    def test_pcc_zero_offset(self):
        """Test PCC with zero offset."""
        from candidate_vetting.vet import pcc

        r = np.array([0.0])
        m = np.array([18.0])

        result = pcc(r, m)
        assert result[0] == 0.0


class TestStaticCatalogStandardization:
    """Tests for catalog standardization functions."""

    def test_desi_dr1_standrdize(self):
        """Test DESI DR1 catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import DesiDr1

        cat = DesiDr1()
        df = pd.DataFrame(
            {
                "name": "DESI123",
                "target_ra": [150.0],
                "target_dec": [30.0],
                "z": [0.1],
                "zerr": [0.001],
                "default_mag": [20.0],
            }
        )

        result = cat.to_standardized_catalog(df)

        assert "name" in result.columns
        assert "ra" in result.columns
        assert "dec" in result.columns
        assert "z" in result.columns

    def test_ned_lvs_standrdize(self):
        """Test NED-LVS catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import NedLvs

        cat = NedLvs()
        df = pd.DataFrame(
            {
                "objname": "NED123",
                "ra": [150.0],
                "dec": [30.0],
                "z": [0.03],
                "z_unc": [0.001],
                "distmpc": [130],
                "distmpc_unc": [5],
                "distmpc_method": "zIndependent",
                "m_j": [20.0],
            }
        )

        result = cat.to_standardized_catalog(df)

        assert "name" in result.columns
        assert "ra" in result.columns
        assert "dec" in result.columns
        assert "z" in result.columns
        assert "z_err" in result.columns
        assert "lumdist" in result.columns
        assert "lumdist_err" in result.columns
        assert "default_mag" in result.columns

    def test_desi_spec_standrdize(self):
        """Test DESI spectroscopic catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import DesiSpec

        cat = DesiSpec()
        df = pd.DataFrame(
            {
                "targetid": [12345],
                "target_ra": [150.0],
                "target_dec": [30.0],
                "z": [0.1],
                "zerr": [0.001],
                "default_mag": [20.0],
            }
        )

        result = cat.to_standardized_catalog(df)

        assert "name" in result.columns
        assert "ra" in result.columns
        assert "dec" in result.columns
        assert "z" in result.columns

    def test_glade_plus_standardize(self):
        """Test GladePlus catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import GladePlus

        cat = GladePlus()
        df = pd.DataFrame(
            {
                "name": ["GLADE12345"],
                "ra": [150.0],
                "dec": [30.0],
                "z_helio": [0.05],
                "z_err": [0.001],
                "d_l": [200.0],
                "d_l_err": [20.0],
                "b": [18.5],
                "dist_flag": [2],
            }
        )

        result = cat.to_standardized_catalog(df)

        assert "name" in result.columns
        assert "ra" in result.columns
        assert "dec" in result.columns
        assert "z" in result.columns
        assert "lumdist" in result.columns

    def test_gwgc_standardize(self):
        """Test GWGC catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import Gwgc

        cat = Gwgc()
        df = pd.DataFrame(
            {"ra": [150.0], "dec": [30.0], "dist": [50.0], "e_dist": [5.0], "b_app": [14.5], "name": ["NGC1234"]}
        )

        result = cat.to_standardized_catalog(df)

        assert "name" in result.columns
        assert "lumdist" in result.columns
        assert "lumdist_neg_err" in result.columns

    def test_hecate1_standardize(self):
        """Test HECATE1 catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import Hecate1

        cat = Hecate1()
        df = pd.DataFrame(
            {
                "ra": [150.0],
                "dec": [30.0],
                "d": [50.0],
                "d_lo68": [45.0],
                "d_hi68": [55.0],
                "e_d": [5.0],
                "r": [14.5],
                "objname": ["NGC1234"],
                "dmethod": ["S"],
            }
        )

        result = cat.to_standardized_catalog(df)

        assert "name" in result.columns
        assert "lumdist" in result.columns
        assert "lumdist_neg_err" in result.columns
        assert "lumdist_pos_err" in result.columns

    def test_ps1_standardize(self):
        """Test PS1 catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import Ps1

        cat = Ps1()
        df = pd.DataFrame(
            {
                "name": ["PS112345"],
                "ra": [150.0],
                "dec": [30.0],
                "z_phot": [0.1],
                "z_err": [0.01],
                "rmeanpsfmag": [20.0],
            }
        )

        result = cat.to_standardized_catalog(df)

        assert "name" in result.columns
        assert "ra" in result.columns
        assert "dec" in result.columns
        assert "z" in result.columns

    def test_sdss12_photoz_standardize(self):
        """Test SDSS DR12 photometric redshift catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import Sdss12Photoz

        cat = Sdss12Photoz()
        df = pd.DataFrame(
            {"name": ["SDSS12345"], "ra": [150.0], "dec": [30.0], "zph": [0.15], "e_zph": [0.02], "rmag": [19.5]}
        )

        result = cat.to_standardized_catalog(df)

        assert "name" in result.columns
        assert "z" in result.columns
        assert "lumdist" in result.columns

    def test_ls_dr10_south_standardize(self):
        """Test Legacy Survey DR10 standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import LsDr10South

        cat = LsDr10South()
        df = pd.DataFrame(
            {
                "lid": [12345],
                "ra": [150.0],
                "declination": [30.0],
                "z_phot_mean": [0.15],
                "z_phot_std": [0.02],
                "z_phot_l68": [0.13],
                "z_phot_u68": [0.17],
                "default_mag": [19.0],
            }
        )

        result = cat.to_standardized_catalog(df)

        assert "name" in result.columns
        assert "z" in result.columns
        assert "lumdist" in result.columns

    def test_milliquas_standardize(df):
        """Test Milliquas catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import Milliquas

        cat = Milliquas()
        df = pd.DataFrame(
            {"name": ["MQ 1234"], "ra": [150.0], "dec": [30.0], "z": [0.15], "z_err": [0.02], "rmag": [19.5]}
        )

        result = cat.to_standardized_catalog(df)

        assert "name" in result.columns
        assert "z" in result.columns
        assert "lumdist" in result.columns
        assert "z_type" in result.columns
        assert "default_mag" in result.columns


class TestCatalogBaseQueryset:
    @pytest.mark.parametrize(
        "catalog_name", ["DelveDr3", "DesiDr1", "DesiSpec", "LsDr9North", "LsDr10South", "Milliquas"]
    )
    def test_catalog_base_filters_do_not_accumulate(self, catalog_name):
        """Instantiating a catalog must not add filters to the shared model manager."""
        from candidate_vetting.public_catalogs import static_catalogs

        catalog = getattr(static_catalogs, catalog_name)
        sqls = [str(catalog().query(10, 10, 1).query) for _ in range(3)]

        assert sqls[0] == sqls[1] == sqls[2]


class TestAtlasForcedPhotParsing:
    """Tests for ATLAS forced photometry data parsing."""

    def test_atlas_stack_empty(self):
        """Test ATLAS stack with empty data."""
        from candidate_vetting.public_catalogs.phot_catalogs import ATLAS_Forced_Phot

        cat = ATLAS_Forced_Phot(name="ATLAS")
        result = cat._ATLAS_stack("")

        assert result is not None
        assert len(result) == 0

    def test_atlas_read_and_sigma_clip(self):
        """Test ATLAS sigma clipping function."""
        import logging
        from candidate_vetting.public_catalogs.phot_catalogs import ATLAS_Forced_Phot

        log = logging.getLogger(__name__)

        cat = ATLAS_Forced_Phot(name="ATLAS")

        sample_data = """###MJD m dm uJy duJy F err chi/N RA Dec x y maj min phi apfit mag5sig Obs
58000.0 19.0 0.1 100.0 10.0 o 0 1.0 150.0 30.0 0 0 1.0 1.0 0.0 0.0 20.0 01a58000o0001o
58001.0 19.1 0.1 95.0 10.0 o 0 1.0 150.0 30.0 0 0 1.0 1.0 0.0 0.0 20.0 01a58001o0001o
58002.0 19.2 0.1 90.0 10.0 o 0 1.0 150.0 30.0 0 0 1.0 1.0 0.0 0.0 20.0 01a58002o0001o
"""

        result = cat._ATLAS_read_and_sigma_clip_data(sample_data, log, clippingSigma=3.0)

        assert result is not None
        assert isinstance(result, list)

    def test_stack_photometry_basic(self):
        """Test basic photometry stacking."""
        from candidate_vetting.public_catalogs.phot_catalogs import ATLAS_Forced_Phot

        cat = ATLAS_Forced_Phot(name="ATLAS")

        magnitudes = {
            "o": {
                "mjds": [58000.0, 58000.5, 58001.0],
                "mags": [100.0, 95.0, 105.0],
                "magErrs": [10.0, 10.0, 10.0],
                "lim5sig": [20.0, 20.0, 20.0],
                "tels":["HKO", "TDO", "TDO"]
            },
            "c": {"mjds": [], "mags": [], "magErrs": [], "lim5sig": [], "tels":[]},
            "I": {"mjds": [], "mags": [], "lim5sig": [], "magErrs": [], "tels":[]},
        }

        result = cat._stack_photometry(magnitudes, binningDays=1.0)

        assert result is not None
        assert isinstance(result, list)


class TestNewCandidateVetting:
    """A newly associated candidate is scored with every EM model possible for its event."""

    @pytest.mark.parametrize("transients, expected_models", [
        (["AGN-flare"], ["AGN-flare"]),
        (["KN", "KN-in-SN", "super-KN"], ["KN", "KN-in-SN", "super-KN"]),
        (["AGN-flare", "TDE"], ["AGN-flare"]),  # TDE has no scoring model yet
        ([], []),
    ])
    def test_scores_each_possible_em_model(self, transients, expected_models):
        from trove_nonlocalizedevents import hooks

        cand = MagicMock()
        cand.target.id, cand.nonlocalizedevent.event_id = 1, "S250208ad"
        with patch.object(hooks, "get_possible_em_transients", return_value=transients), \
                patch.object(hooks, "score") as score:
            hooks.vet_new_candidate(cand)
        assert [c.kwargs["em_model_name"] for c in score.call_args_list] == expected_models
        for c in score.call_args_list:
            assert c.kwargs["target_id"] == 1
            assert c.kwargs["nonlocalized_event_name"] == "S250208ad"
            assert c.kwargs["basic_results"] is None

    def test_each_model_gets_fresh_basic_results(self):
        import pandas as pd
        from trove_nonlocalizedevents import hooks

        cand = MagicMock()
        cand.target.id, cand.nonlocalizedevent.event_id = 1, "S250208ad"
        host_df, agn_df = pd.DataFrame({"name": ["g1"]}), pd.DataFrame({"name": ["a1"]})
        with patch.object(hooks, "get_possible_em_transients", return_value=["KN", "KN-in-SN"]), \
                patch.object(hooks, "score") as score:
            hooks.vet_new_candidate(cand, basic_results=(host_df, agn_df, True))

        passed = [c.kwargs["basic_results"] for c in score.call_args_list]
        assert len(passed) == 2
        # each mode gets its own copies, so one can't modify another's dataframes
        assert passed[0][0] is not passed[1][0] and passed[0][0] is not host_df
        assert passed[0][1] is not passed[1][1] and passed[0][1] is not agn_df
        assert all(p[0].equals(host_df) and p[1].equals(agn_df) and p[2] for p in passed)



class TestDecayRateSign:
    """The decay rate is positive for a fading light curve, whichever
    model the AIC picks."""

    @pytest.mark.parametrize(
        "model_name, label, params",
        [
            # mag = 20 + log10(t)
            ("_powerlaw", "powerlaw", (-1.0, 20.0)),
            # fades, decelerating
            ("_broken_powerlaw_concave_down", "broken_concave_down", (1.0, 4.0, 20.0, 2.0)),
            # rises, then fades
            ("_broken_powerlaw_concave_up", "broken_concave_up", (-1.0, 2.0, 20.0, 2.0)),
        ],
    )
    def test_fading_light_curve_has_positive_decay_rate(self, model_name, label, params, caplog):
        from scoring import vet_phot

        t = np.linspace(0.5, 15, 40)
        mag = getattr(vet_phot, model_name)(t, *params)
        magerr = np.full_like(t, 0.01)

        with caplog.at_level("INFO", logger=vet_phot.logger.name):
            _, _, _, decay_rate = vet_phot.estimate_max_find_decay_rate(
                t, mag, magerr, t_pre=0, t_post=25
            )

        # make sure the AIC actually picked the model under test
        assert f"Model selected: {label} " in caplog.text
        assert decay_rate > 0


def _phot_df(mjd, mag, magerr, upperlimit=None, telescope="ZTF", filt="r"):
    """A photometry dataframe shaped like `vet_phot.load_phot_df`'s output"""
    from trove_targets.templatetags.photometry_extras import error_to_snr
    n = len(mjd)
    df = pd.DataFrame(dict(
        telescope=[telescope] * n if isinstance(telescope, str) else telescope,
        mjd=np.asarray(mjd, float),
        mag=np.asarray(mag, float),
        magerr=np.asarray(magerr, float),
        upperlimit=upperlimit if upperlimit is not None else [False] * n,
        filter=[filt] * n,
    ))
    df["snr"] = error_to_snr(df.magerr)
    return df


class TestEstimateT0:
    """t0 for targets without a GW event: halfway between the last
    non-detection and the first detection"""

    def test_midpoint_of_last_limit_and_first_detection(self):
        from scoring.vet_phot import estimate_t0
        df = _phot_df([58990, 59000, 59010, 59012, 59015],
                      [21, 21, 19, 18.5, 18.8],
                      [np.nan, np.nan, 0.05, 0.05, 0.05],
                      upperlimit=[True, True, False, False, False])
        assert estimate_t0(df, snr_min=3) == (59005, False)

    def test_low_snr_point_counts_as_non_detection(self):
        from scoring.vet_phot import estimate_t0
        # 59004 is a 1-sigma "detection", so not a detection
        df = _phot_df([59000, 59004, 59010], [21, 20.5, 19],
                      [np.nan, 1.0, 0.05], upperlimit=[True, False, False])
        assert estimate_t0(df, snr_min=3) == (59007, False)

    def test_limits_after_first_detection_are_ignored(self):
        from scoring.vet_phot import estimate_t0, T0_FALLBACK_DAYS
        df = _phot_df([59010, 59011, 59020], [19, 21, 19.5],
                      [0.05, np.nan, 0.05], upperlimit=[False, True, False])
        assert estimate_t0(df, snr_min=3) == (59010 - T0_FALLBACK_DAYS, True)

    def test_unreliable_forced_phot_is_not_the_first_detection(self):
        from scoring.vet_phot import estimate_t0, T0_FALLBACK_DAYS
        # a SNR ~4 ATLAS FP point is below FORCEDPHOT_SNR_THRESHOLD, so the
        # first detection is the ZTF one at 59010. It is above snr_min though,
        # so it isn't a non-detection either and doesn't set t0
        df = _phot_df([59006, 59010], [20, 19], [0.27, 0.05],
                      telescope=["ATLAS FP", "ZTF"])
        assert estimate_t0(df, snr_min=3) == (59010 - T0_FALLBACK_DAYS, True)

    @pytest.mark.parametrize("df", [
        pd.DataFrame(),
        _phot_df([59000, 59001], [21, 21], [np.nan, np.nan], upperlimit=[True, True]),
    ])
    def test_no_detections(self, df):
        from scoring.vet_phot import estimate_t0
        assert estimate_t0(df, snr_min=3) == (None, False)


class TestBasicLightCurveMetrics:
    """Light curve metrics measured from the target's own t0"""

    # rises then fades, in days since the true t0
    T = np.array([0.5, 1, 1.5, 2, 3, 4, 6, 8, 10, 13, 16, 20, 25, 30])

    def _df(self):
        from scoring import vet_phot
        mag = vet_phot._broken_powerlaw_concave_up(self.T, -1.0, 2.0, 20.0, 2.0)
        # a limit 1 day before t0's midpoint partner puts t0 at mjd 60000
        mjd = np.concatenate([[60000 - 0.5], 60000 + self.T])
        return _phot_df(mjd, np.concatenate([[22], mag]),
                        np.concatenate([[np.nan], np.full(len(self.T), 0.02)]),
                        upperlimit=[True] + [False] * len(self.T))

    def _run(self, dist):
        from scoring import vet_phot
        with patch.object(vet_phot, "load_phot_df", return_value=self._df()), \
             patch("scoring.scoring.target_default_distance", return_value=dist):
            return vet_phot.basic_light_curve_metrics(1)

    def test_matches_a_direct_fit_from_t0(self):
        from scoring import vet_phot
        metrics = self._run((40.0, 5.0))
        mag = vet_phot._broken_powerlaw_concave_up(self.T, -1.0, 2.0, 20.0, 2.0)
        _, _, peak_time, decay_rate = vet_phot.estimate_max_find_decay_rate(
            self.T, mag, np.full(len(self.T), 0.02), t_pre=0,
            t_post=vet_phot.BASIC_LC_T_POST,
            min_time_separation=vet_phot.BASIC_LC_MIN_TIME_SEPARATION,
        )
        assert metrics["t0"] == pytest.approx(59999.75 + 0.25)
        assert metrics["t0_is_fallback"] is False
        assert metrics["peak_time"] == pytest.approx(peak_time)
        assert metrics["decay_rate"] == pytest.approx(decay_rate)
        assert 0 < metrics["peak_time"] < 30 and metrics["decay_rate"] > 0
        assert metrics["lum"] > 0

    def test_no_luminosity_without_a_distance(self):
        metrics = self._run((None, None))
        assert metrics["lum"] is None
        assert metrics["peak_time"] is not None

    def test_no_detections_returns_none(self):
        from scoring import vet_phot
        df = _phot_df([60000], [21], [np.nan], upperlimit=[True])
        with patch.object(vet_phot, "load_phot_df", return_value=df):
            assert vet_phot.basic_light_curve_metrics(1) is None


class TestScorePhotUnchanged:
    """_score_phot results pinned from before measuring was split out of it"""

    RANGES = dict(phot_score_snr_min=3, t_pre=0, t_post=25, min_time_separation=0.0417,
                  lum_max=[1e40, 1e42], peak_time=[0, 5], decay_rate=[0.01, 1])

    @staticmethod
    def _df(dts, mags, errs, filt="r"):
        df = _phot_df(np.asarray(dts) + 60000, mags, errs, filt=filt)
        df["dt"] = np.asarray(dts, float)
        return df

    def _cases(self):
        t = np.array([0.5, 1, 2, 3, 5, 8, 12, 16, 20])
        mag = 20 + 2.5 * np.log10(t) - 1.5 * np.exp(-t / 2)
        return {
            "fading": (self._df(t, mag, [0.05] * len(t)),
                       [1.0, 2.2421764105915314e+41, 0.5, 0.049678643683471684]),
            "single": (self._df([1.0], [19.0], [0.05]),
                       [1.0, 9.601947203014883e+40, None, None]),
            "lowsnr": (self._df([1.0, 2.0], [19.0, 19.5], [1.0, 1.0]),
                       [1.0, None, None, None]),
            "nofilt": (self._df([1.0, 2.0], [19.0, 19.5], [0.05, 0.05], filt="z"),
                       [1.0, None, None, None]),
        }

    def test_results_unchanged(self):
        from scoring import vet_phot
        with patch("scoring.scoring.get_eventcandidate_default_distance",
                   return_value=(40.0, 5.0)):
            for name, (df, expected) in self._cases().items():
                got = vet_phot._score_phot(df, MagicMock(id=1),
                                           MagicMock(event_id="S1"), self.RANGES)[:4]
                assert got == pytest.approx(expected, rel=1e-6), name


class TestTargetDefaultDistance:
    """Distance for a target without a GW event"""

    HOSTS = (
        '[{"ID": "photz", "Source": "GLADE+", "PCC": 0.01, "Dist": 100.0,'
        ' "DistErr": 10.0, "z": 0.02, "z_type": "photo-z"},'
        ' {"ID": "specz", "Source": "GLADE+", "PCC": 0.05, "Dist": 120.0,'
        ' "DistErr": 1.0, "z": 0.025, "z_type": "spec-z"}]'
    )

    def _run(self, redshift, hosts_json):
        from scoring import scoring
        hosts = MagicMock()
        hosts.count.return_value = int(hosts_json is not None)
        hosts.__getitem__.return_value = MagicMock(value=hosts_json)
        with patch.object(scoring, "Target") as target, \
             patch.object(scoring, "TargetExtra") as te:
            target.objects.get.return_value = MagicMock(redshift=redshift)
            te.objects.filter.return_value = hosts
            return scoring.target_default_distance(1)

    def test_redshift_first(self):
        from scoring.scoring import cosmo
        dist, _ = self._run(0.01, self.HOSTS)
        assert dist == pytest.approx(cosmo.luminosity_distance(0.01).value)

    def test_spec_z_host_preferred(self):
        assert self._run(None, self.HOSTS) == (120.0, 1.0)

    def test_none_without_redshift_or_hosts(self):
        assert self._run(None, None) == (None, None)
        assert self._run(None, "[]") == (None, None)
