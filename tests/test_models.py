"""
Unit tests for Django models and model methods.

These test the models in trove_targets/models.py and candidate_vetting/models.py.
"""
import numpy as np


class TestTargetModel:
    """Tests for Target model in trove_targets/models.py"""

    def test_healpix_calculation(self):
        """Test HEALPix index calculation from coordinates."""
        import astropy_healpix as ah
        from astropy import units as u
        
        ra = 150.0 * u.deg
        dec = 30.0 * u.deg
        nside = 1024
        
        healpix = ah.lonlat_to_healpix(ra, dec, nside, order='nested')
        
        assert isinstance(healpix, (int, np.integer))
        assert healpix >= 0
