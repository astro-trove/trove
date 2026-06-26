"""
Unit tests for candidate vetting functions.

These test the pure logic functions in candidate_vetting/.
"""
import pytest
import numpy as np
from unittest.mock import MagicMock, patch


class TestAsymmetricGaussian:
    """Tests for AsymmetricGaussian distribution in candidate_vetting/vet.py"""

    INT_A, INT_B = 1e-9, 1e4

    def _pdf(self, ag, x, mean, unc_minus, unc_plus):
        x = np.atleast_1d(x)
        n = len(x)
        return ag._pdf(
            x,
            np.full(n, mean),
            np.full(n, unc_minus),
            np.full(n, unc_plus),
            np.full(n, self.INT_A),
            np.full(n, self.INT_B),
        )

    def test_pdf_symmetric_gaussian(self):
        """Symmetric uncertainties should match a truncated normal."""
        from candidate_vetting.vet import AsymmetricGaussian
        from scipy.integrate import trapezoid
        from scipy.stats import norm

        ag = AsymmetricGaussian()
        mean, scale = 100.0, 10.0
        x = np.linspace(self.INT_A, self.INT_B, 5000)
        pdf_vals = self._pdf(ag, x, mean, scale, scale)
        norm_vals = norm.pdf(x, loc=mean, scale=scale)
        norm_vals /= trapezoid(norm_vals, x)

        np.testing.assert_allclose(pdf_vals, norm_vals, rtol=0.02, atol=0.001)

    def test_pdf_asymmetric_left_side(self):
        """Test AsymmetricGaussian on left side of mean."""
        from candidate_vetting.vet import AsymmetricGaussian

        ag = AsymmetricGaussian()
        pdf_val = self._pdf(ag, [0.5], 1.0, 0.3, 0.5)
        assert len(pdf_val) == 1
        assert pdf_val[0] > 0

    def test_pdf_asymmetric_right_side(self):
        """Test AsymmetricGaussian on right side of mean."""
        from candidate_vetting.vet import AsymmetricGaussian

        ag = AsymmetricGaussian()
        pdf_val = self._pdf(ag, [1.5], 1.0, 0.3, 0.5)
        assert len(pdf_val) == 1
        assert pdf_val[0] > 0

    def test_pdf_mixed_sides(self):
        """Test AsymmetricGaussian with points on both sides."""
        from candidate_vetting.vet import AsymmetricGaussian

        ag = AsymmetricGaussian()
        pdf_vals = self._pdf(ag, [0.5, 1.5], 1.0, 0.3, 0.5)
        assert len(pdf_vals) == 2
        assert all(p > 0 for p in pdf_vals)

    def test_single_point_query_is_finite(self):
        """Regression: single-point queries must not divide by zero."""
        from candidate_vetting.vet import AsymmetricGaussian

        ag = AsymmetricGaussian()
        pdf_val = self._pdf(ag, [100.0], 100.0, 5.0, 20.0)
        assert np.isfinite(pdf_val[0])
        assert pdf_val[0] > 0

    def test_normalization_integrates_to_one(self):
        """Normalized PDF should integrate to ~1 over the truncation domain."""
        from candidate_vetting.vet import AsymmetricGaussian
        from scipy.integrate import trapezoid

        ag = AsymmetricGaussian()
        cases = [
            (100, 10, 10),
            (100, 5, 20),
            (40, 0.5, 0.5),
            (40, 2.0, 2.0),
            (80, 3, 15),
        ]
        x = np.linspace(self.INT_A, self.INT_B, 100000)
        for mean, um, up in cases:
            p = self._pdf(ag, x, mean, um, up)
            integral = trapezoid(p, x)
            assert np.isfinite(integral)
            assert 0.99 < integral < 1.01, (mean, um, up, integral)

    def test_continuous_at_mean(self):
        """Peak meets in the middle with value set by normalization, not by sigma."""
        from candidate_vetting.vet import AsymmetricGaussian

        ag = AsymmetricGaussian()
        mean, um, up = 100.0, 5.0, 20.0
        eps = np.logspace(-8, -2, 10)
        at_mean = self._pdf(ag, [mean], mean, um, up)[0]
        left = self._pdf(ag, mean - eps, mean, um, up)
        right = self._pdf(ag, mean + eps, mean, um, up)
        assert np.all(np.abs(left - at_mean) < 0.05)
        assert np.all(np.abs(right - at_mean) < 0.05)

    def test_narrow_uncertainty_does_not_blow_up(self):
        """Small but realistic Mpc errors must not produce infinite peak heights."""
        from candidate_vetting.vet import AsymmetricGaussian

        ag = AsymmetricGaussian()
        peak = self._pdf(ag, [40.0], 40.0, 0.5, 0.5)[0]
        assert np.isfinite(peak)
        assert peak < 10.0

    def test_zero_uncertainty_side_is_safe(self):
        """Zero-width side should not produce NaNs at the mean."""
        from candidate_vetting.vet import AsymmetricGaussian

        ag = AsymmetricGaussian()
        pdf_val = self._pdf(ag, [100.0], 100.0, 0.0, 10.0)
        assert np.isfinite(pdf_val[0])
        assert pdf_val[0] > 0

    def test_public_pdf_matches_internal(self):
        """Custom pdf() should bypass scipy rv_continuous NaN edge cases."""
        from candidate_vetting.vet import AsymmetricGaussian

        ag = AsymmetricGaussian()
        x = np.linspace(-5, 5, 500)
        public = ag.pdf(x, mean=0, unc_minus=0.5, unc_plus=1.0, integ_a=-10, integ_b=10)
        internal = ag._pdf(x, 0, 0.5, 1.0, -10, 10)
        np.testing.assert_allclose(public, internal)
        assert not np.any(np.isnan(public))

    @pytest.mark.parametrize(
        "mean,unc_minus,unc_plus,integ_a,integ_b",
        [
            (0, 0.5, 1.0, -100, 100),
            (100, 10, 10, 1e-9, 1e4),
            (100, 5, 20, 1e-9, 1e4),
            (40, 0.5, 0.5, 1e-9, 1e4),
            (100, 0.0, 10.0, 1e-9, 1e4),
            (100, 10.0, 0.0, 1e-9, 1e4),
            (100, 0.0, 0.0, 1e-9, 1e4),
            (100, 10, 10, 100, 100),
            (100, 10, 10, 200, 100),
            (500, 50, 100, 1e-9, 1e4),
            (134.19, 157.0, 157.0, 1e-9, 500),
            (100, 10, 10, 1e-9, 1e4),
        ],
        ids=[
            "mean_zero_negative_bounds",
            "symmetric_standard",
            "asymmetric_standard",
            "narrow_sigma",
            "zero_left_sigma",
            "zero_right_sigma",
            "both_sigma_zero",
            "empty_domain",
            "inverted_domain",
            "high_mean",
            "glade_photoz_large_err",
            "single_point_grid",
        ],
    )
    def test_edge_cases_no_nan(self, mean, unc_minus, unc_plus, integ_a, integ_b):
        """Edge-case parameter grids must not produce NaN or inf PDF values."""
        from candidate_vetting.vet import AsymmetricGaussian
        from scipy.integrate import trapezoid

        ag = AsymmetricGaussian()
        if integ_a >= integ_b:
            x = np.array([mean])
        elif mean == 100 and unc_minus == 10 and integ_a == 1e-9:
            x = np.array([100.0])
        else:
            x = np.linspace(max(integ_a, mean - 50), min(integ_b, mean + 50), 1000)

        pdf_vals = ag.pdf(x, mean, unc_minus, unc_plus, integ_a, integ_b)
        assert np.all(np.isfinite(pdf_vals))
        assert not np.any(np.isinf(pdf_vals))
        if integ_a < integ_b and np.ptp(x) > 0:
            integral = trapezoid(pdf_vals, x)
            assert np.isfinite(integral)
            if unc_minus > 0 or unc_plus > 0:
                assert 0.0 <= integral <= 1.01

    @pytest.mark.parametrize(
        "unc_minus,unc_plus,integ_a,integ_b",
        [
            (0.5, 1.0, -100, 100),
            (0.5, 1.0, -10, 10),
            (1.0, 1.0, 1e-9, 100),
            (5.0, 20.0, -50, 50),
            (0.0, 10.0, -20, 20),
            (10.0, 0.0, -20, 20),
            (0.5, 0.5, -5, 5),
        ],
        ids=[
            "wide_bounds",
            "moderate_bounds",
            "positive_lower_bound",
            "asymmetric_sigma",
            "zero_left_sigma",
            "zero_right_sigma",
            "narrow_symmetric",
        ],
    )
    def test_mean_zero_finite_and_normalized(
        self, unc_minus, unc_plus, integ_a, integ_b
    ):
        """mean=0 must yield finite PDFs with no NaN/inf and integrate to ~1."""
        from candidate_vetting.vet import AsymmetricGaussian
        from scipy.integrate import trapezoid

        ag = AsymmetricGaussian()
        x = np.linspace(integ_a, integ_b, 10000)
        pdf_vals = ag.pdf(x, 0, unc_minus, unc_plus, integ_a, integ_b)

        assert not np.any(np.isnan(pdf_vals))
        assert not np.any(np.isinf(pdf_vals))
        assert np.all(np.isfinite(pdf_vals))

        if unc_minus > 0 or unc_plus > 0:
            integral = trapezoid(pdf_vals, x)
            assert np.isfinite(integral)
            assert 0.99 < integral < 1.01
        else:
            assert np.all(pdf_vals == 0.0)


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
        df = pd.DataFrame({
            'desiname': "DESI123",
            'target_ra': [150.0],
            'target_dec': [30.0],
            'z': [0.1],
            'zerr': [0.001],
            'default_mag': [20.0]
        })
        
        result = cat.to_standardized_catalog(df)
        
        assert 'name' in result.columns
        assert 'ra' in result.columns
        assert 'dec' in result.columns
        assert 'z' in result.columns
        
    def test_ned_lvs_standrdize(self):
        """Test NED-LVS catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import NedLvs
        
        cat = NedLvs()
        df = pd.DataFrame({
            'objname': "NED123",
            'ra': [150.0],
            'dec': [30.0],
            'z': [0.03],
            'z_unc': [0.001],
            'distmpc': [130],
            'distmpc_unc': [5],
            'distmpc_method':'zIndependent',
            'm_j': [20.0]
        })
        
        result = cat.to_standardized_catalog(df)
        
        assert 'name' in result.columns
        assert 'ra' in result.columns
        assert 'dec' in result.columns
        assert 'z' in result.columns
        assert 'z_err' in result.columns
        assert 'lumdist' in result.columns
        assert 'lumdist_err' in result.columns
        assert 'default_mag' in result.columns


    def test_desi_spec_standrdize(self):
        """Test DESI spectroscopic catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import DesiSpec
        
        cat = DesiSpec()
        df = pd.DataFrame({
            'targetid': [12345],
            'target_ra': [150.0],
            'target_dec': [30.0],
            'z': [0.1],
            'zerr': [0.001],
            'default_mag': [20.0]
        })
        
        result = cat.to_standardized_catalog(df)
        
        assert 'name' in result.columns
        assert 'ra' in result.columns
        assert 'dec' in result.columns
        assert 'z' in result.columns

    def test_glade_plus_standardize(self):
        """Test GladePlus catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import GladePlus

        cat = GladePlus()
        df = pd.DataFrame({
            'gn': ['GLADE12345'],
            'ra': [150.0],
            'dec': [30.0],
            'z_helio': [0.05],
            'z_err': [0.001],
            'd_l': [200.0],
            'd_l_err': [20.0],
            'b': [18.5],
            'dist_flag': [2],
        })

        result = cat.to_standardized_catalog(df)

        assert 'name' in result.columns
        assert 'ra' in result.columns
        assert 'dec' in result.columns
        assert 'z' in result.columns
        assert 'lumdist' in result.columns
        assert result.lumdist_err.iloc[0] == 20.0

    def test_glade_plus_lumdist_err_from_redshift(self):
        """Photo-z GLADE+ sources use combined z_err and v_err for lumdist_err."""
        import numpy as np
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import (
            GladePlus,
            _lumdist_err_from_dz,
        )

        z = 0.0302022151011
        v_err = 0.000407402181527
        z_err = 0.0339967
        catalog_d_l_err = 1.886348
        expected = _lumdist_err_from_dz(z, np.hypot(v_err, z_err))

        cat = GladePlus()
        df = pd.DataFrame({
            'gn': [2664550],
            'ra': [150.0],
            'dec': [30.0],
            'z_helio': [z],
            'v_err': [v_err],
            'z_err': [z_err],
            'd_l': [134.188513],
            'd_l_err': [catalog_d_l_err],
            'b': [18.5],
            'dist_flag': [1],
        })

        result = cat.to_standardized_catalog(df)

        assert result.z_type.iloc[0] == 'photo-z'
        assert result.lumdist_err.iloc[0] == pytest.approx(expected, rel=1e-4)
        assert result.lumdist_err.iloc[0] > catalog_d_l_err
        assert result.lumdist_neg_err.iloc[0] == result.lumdist_err.iloc[0]
        assert result.lumdist_pos_err.iloc[0] == result.lumdist_err.iloc[0]

    def test_gwgc_standardize(self):
        """Test GWGC catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import Gwgc
        
        cat = Gwgc()
        df = pd.DataFrame({
            'ra': [150.0],
            'dec': [30.0],
            'dist': [50.0],
            'e_dist': [5.0],
            'b_app': [14.5],
            'name': ['NGC1234']
        })
        
        result = cat.to_standardized_catalog(df)
        
        assert 'name' in result.columns
        assert 'lumdist' in result.columns
        assert 'lumdist_neg_err' in result.columns

    def test_hecate1_standardize(self):
        """Test HECATE1 catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import Hecate1
        
        cat = Hecate1()
        df = pd.DataFrame({
            'ra': [150.0],
            'dec': [30.0],
            'd': [50.0],
            'd_lo68': [45.0],
            'd_hi68': [55.0],
            'e_d': [5.0],
            'r': [14.5],
            'objname': ['NGC1234'],
            'dmethod': ['S'],
        })
        
        result = cat.to_standardized_catalog(df)
        
        assert 'name' in result.columns
        assert 'lumdist' in result.columns
        assert 'lumdist_neg_err' in result.columns
        assert 'lumdist_pos_err' in result.columns

    def test_ps1_standardize(self):
        """Test PS1 catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import Ps1
        
        cat = Ps1()
        df = pd.DataFrame({
            'objname': ['PS112345'],
            'ra': [150.0],
            'dec': [30.0],
            'z_phot': [0.1],
            'z_err': [0.01],
            'rmeanpsfmag': [20.0]
        })
        
        result = cat.to_standardized_catalog(df)
        
        assert 'name' in result.columns
        assert 'ra' in result.columns
        assert 'dec' in result.columns
        assert 'z' in result.columns

    def test_sdss12_photoz_standardize(self):
        """Test SDSS DR12 photometric redshift catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import Sdss12Photoz
        
        cat = Sdss12Photoz()
        df = pd.DataFrame({
            'sdssid': ['SDSS12345'],
            'ra': [150.0],
            'dec': [30.0],
            'zph': [0.15],
            'e_zph': [0.02],
            'rmag': [19.5]
        })
        
        result = cat.to_standardized_catalog(df)
        
        assert 'name' in result.columns
        assert 'z' in result.columns
        assert 'lumdist' in result.columns
    
    def test_ls_dr10_south_standardize(self):
        """Test Legacy Survey DR10 standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import LsDr10South
        
        cat = LsDr10South()
        df = pd.DataFrame({
            'objid': [12345],
            'ra': [150.0],
            'declination': [30.0],
            'z_phot_mean': [0.15],
            'z_phot_std': [0.02],
            'z_phot_l68': [0.13],
            'z_phot_u68': [0.17],
            'default_mag': [19.0],
        })
        
        result = cat.to_standardized_catalog(df)
        
        assert 'name' in result.columns
        assert 'z' in result.columns
        assert 'lumdist' in result.columns
        
    def test_milliquas_standardize(df):
        """Test Milliquas catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import Milliquas
    
        cat = Milliquas()
        df = pd.DataFrame({
            'name': ['MQ 1234'],
            'ra': [150.0],
            'dec': [30.0],
            'z': [0.15],
            'z_err': [0.02],
            'rmag': [19.5]
        })
        
        result = cat.to_standardized_catalog(df)
        
        assert 'name' in result.columns
        assert 'z' in result.columns
        assert 'lumdist' in result.columns
        assert 'z_type' in result.columns
        assert 'default_mag' in result.columns


class TestRollingWindowSigmaClip:
    """Tests for the rolling window sigma clip function."""

    def test_empty_array(self):
        """Test sigma clip with empty array."""
        from candidate_vetting.public_catalogs.phot_catalogs import _rolling_window_sigma_clip
        
        result = _rolling_window_sigma_clip([])
        assert len(result) == 0

    def test_no_outliers(self):
        """Test sigma clip with no outliers."""
        from candidate_vetting.public_catalogs.phot_catalogs import _rolling_window_sigma_clip
        
        data = [1.0, 1.1, 0.9, 1.0, 1.05, 0.95, 1.0]
        result = _rolling_window_sigma_clip(data, clipping_sigma=3.0)
        
        assert len(result) == len(data)
        assert not any(result)

    def test_with_outlier(self):
        """Test sigma clip detects outlier in larger array."""
        from candidate_vetting.public_catalogs.phot_catalogs import _rolling_window_sigma_clip
        
        data = [1.0, 1.1, 0.9, 1.0, 1.05, 0.95, 1.0, 1.1, 0.9, 1.0, 1.05, 
                100.0,
                0.95, 1.0, 1.1, 0.9, 1.0, 1.05, 0.95, 1.0]
        result = _rolling_window_sigma_clip(data, clipping_sigma=2.0, window_size=11)
        
        assert len(result) == len(data)
        assert result[11] == True


class TestAtlasForcedPhotParsing:
    """Tests for ATLAS forced photometry data parsing."""

    def test_atlas_stack_empty(self):
        """Test ATLAS stack with empty data."""
        from candidate_vetting.public_catalogs.phot_catalogs import ATLAS_Forced_Phot
        
        cat = ATLAS_Forced_Phot(name='ATLAS')
        result = cat._ATLAS_stack('')
        
        assert result is not None
        assert len(result) == 0

    def test_atlas_read_and_sigma_clip(self):
        """Test ATLAS sigma clipping function."""
        from candidate_vetting.public_catalogs.phot_catalogs import ATLAS_Forced_Phot
        
        cat = ATLAS_Forced_Phot(name='ATLAS')
        
        sample_data = """###MJD m dm uJy duJy F err chi/N RA Dec x y maj min phi apfit mag5sig Obs
58000.0 19.0 0.1 100.0 10.0 o 0 1.0 150.0 30.0 0 0 1.0 1.0 0.0 0.0 20.0 01a58000o0001o
58001.0 19.1 0.1 95.0 10.0 o 0 1.0 150.0 30.0 0 0 1.0 1.0 0.0 0.0 20.0 01a58001o0001o
58002.0 19.2 0.1 90.0 10.0 o 0 1.0 150.0 30.0 0 0 1.0 1.0 0.0 0.0 20.0 01a58002o0001o
"""
        
        result = cat._ATLAS_read_and_sigma_clip_data(sample_data, clipping_sigma=3.0)
        
        assert result is not None
        assert isinstance(result, list)

    def test_stack_photometry_basic(self):
        """Test basic photometry stacking."""
        from candidate_vetting.public_catalogs.phot_catalogs import ATLAS_Forced_Phot
        
        cat = ATLAS_Forced_Phot(name='ATLAS')
        
        magnitudes = {
            'o': {'mjds': [58000.0, 58000.5, 58001.0], 'mags': [100.0, 95.0, 105.0], 
                  'magErrs': [10.0, 10.0, 10.0], 'lim5sig': [20.0, 20.0, 20.0]},
            'c': {'mjds': [], 'mags': [], 'magErrs': [], 'lim5sig': []},
            'I': {'mjds': [], 'mags': [], 'lim5sig': [], 'magErrs': []}
        }
        
        result = cat._stack_photometry(magnitudes, binning_days=1.0)
        
        assert result is not None
        assert isinstance(result, list)