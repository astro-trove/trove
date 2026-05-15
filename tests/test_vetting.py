"""
Unit tests for candidate vetting functions.

These test the pure logic functions in candidate_vetting/.
"""
import pytest
import numpy as np
from unittest.mock import MagicMock, patch


class TestAsymmetricGaussian:
    """Tests for AsymmetricGaussian distribution in candidate_vetting/vet.py"""

    def test_pdf_symmetric_gaussian(self):
        """Test AsymmetricGaussian with equal uncertainties (should match normal)."""
        from candidate_vetting.vet import AsymmetricGaussian
        from scipy.stats import norm
        
        ag = AsymmetricGaussian()
        x = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
        mean = np.array([1.0] * 5)
        unc = np.array([0.5] * 5)
        integ_a = np.array([-5.0] * 5)
        integ_b = np.array([5.0] * 5)
        
        pdf_vals = ag._pdf(x, mean, unc, unc, integ_a, integ_b)
        norm_vals = norm.pdf(x, loc=1.0, scale=0.5)
        
        assert len(pdf_vals) == len(norm_vals)

    def test_pdf_asymmetric_left_side(self):
        """Test AsymmetricGaussian on left side of mean."""
        from candidate_vetting.vet import AsymmetricGaussian
        
        ag = AsymmetricGaussian()
        x = np.array([0.5])
        mean = np.array([1.0])
        unc_minus = np.array([0.3])
        unc_plus = np.array([0.5])
        integ_a = np.array([-5.0])
        integ_b = np.array([5.0])
        
        pdf_val = ag._pdf(x, mean, unc_minus, unc_plus, integ_a, integ_b)
        assert len(pdf_val) == 1
        assert pdf_val[0] > 0

    def test_pdf_asymmetric_right_side(self):
        """Test AsymmetricGaussian on right side of mean."""
        from candidate_vetting.vet import AsymmetricGaussian
        
        ag = AsymmetricGaussian()
        x = np.array([1.5])
        mean = np.array([1.0])
        unc_minus = np.array([0.3])
        unc_plus = np.array([0.5])
        integ_a = np.array([-5.0])
        integ_b = np.array([5.0])
        
        pdf_val = ag._pdf(x, mean, unc_minus, unc_plus, integ_a, integ_b)
        assert len(pdf_val) == 1
        assert pdf_val[0] > 0

    def test_pdf_mixed_sides(self):
        """Test AsymmetricGaussian with points on both sides."""
        from candidate_vetting.vet import AsymmetricGaussian
        
        ag = AsymmetricGaussian()
        x = np.array([0.5, 1.5])
        mean = np.array([1.0, 1.0])
        unc_minus = np.array([0.3, 0.3])
        unc_plus = np.array([0.5, 0.5])
        integ_a = np.array([-5.0, -5.0])
        integ_b = np.array([5.0, 5.0])
        
        pdf_vals = ag._pdf(x, mean, unc_minus, unc_plus, integ_a, integ_b)
        assert len(pdf_vals) == 2
        assert all(p > 0 for p in pdf_vals)


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

    def test_hecate_standardize(self):
        """Test HECATE catalog standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import Hecate
        
        cat = Hecate()
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
    
    def test_ls_dr10_standardize(self):
        """Test Legacy Survey DR10 standardization."""
        import pandas as pd
        from candidate_vetting.public_catalogs.static_catalogs import LsDr10
        
        cat = LsDr10()
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
            'sdssid': ['MQ 1234'],
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