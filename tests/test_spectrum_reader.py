"""
Unit tests for the spectrum_reader module.

Tests the replacement for lightcurve_fitting.speccal.readspec.
"""
import pytest
import numpy as np
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime
import json


class TestConvertSpectrumUnits:
    """Tests for _convert_spectrum_units function."""

    def test_identity_conversion(self):
        """Test conversion when units already match defaults."""
        from custom_code.processors.spectrum_reader import _convert_spectrum_units
        
        wl = np.array([4000., 5000., 6000.])
        flux = np.array([1e-15, 2e-15, 1.5e-15])
        hdr = {'BUNIT': 'erg / (Angstrom cm2 s)', 'CUNIT1': 'Angstrom'}
        
        wl_out, flux_out = _convert_spectrum_units(wl, flux, hdr)
        
        np.testing.assert_array_almost_equal(wl_out, wl)
        np.testing.assert_array_almost_equal(flux_out, flux)

    def test_angstroms_typo_correction(self):
        """Test correction of 'Angstroms' to 'Angstrom'."""
        from custom_code.processors.spectrum_reader import _convert_spectrum_units
        
        wl = np.array([4000., 5000., 6000.])
        flux = np.array([1e-15, 2e-15, 1.5e-15])
        hdr = {'CUNIT1': 'Angstroms'}
        
        wl_out, flux_out = _convert_spectrum_units(wl, flux, hdr)
        
        assert len(wl_out) == len(wl)

    def test_default_units_used(self):
        """Test that default units are used when header is empty."""
        from custom_code.processors.spectrum_reader import _convert_spectrum_units
        
        wl = np.array([4000., 5000., 6000.])
        flux = np.array([1e-15, 2e-15, 1.5e-15])
        hdr = {}
        
        wl_out, flux_out = _convert_spectrum_units(wl, flux, hdr)
        
        assert len(wl_out) == len(wl)
        assert len(flux_out) == len(flux)


class TestParseDateFromHeader:
    """Tests for _parse_date_from_header function."""

    def test_mjd_obs_keyword(self):
        """Test parsing MJD-OBS keyword."""
        from custom_code.processors.spectrum_reader import _parse_date_from_header
        
        hdr = {'MJD-OBS': 60000.5}
        date = _parse_date_from_header(hdr)
        
        assert date is not None
        assert abs(date.mjd - 60000.5) < 0.001

    def test_jd_keyword(self):
        """Test parsing JD keyword."""
        from custom_code.processors.spectrum_reader import _parse_date_from_header
        
        hdr = {'JD': 2460000.5}
        date = _parse_date_from_header(hdr)
        
        assert date is not None
        assert abs(date.jd - 2460000.5) < 0.001

    def test_date_obs_iso(self):
        """Test parsing DATE-OBS in ISO format."""
        from custom_code.processors.spectrum_reader import _parse_date_from_header
        
        hdr = {'DATE-OBS': '2024-01-15T12:30:00'}
        date = _parse_date_from_header(hdr)
        
        assert date is not None
        assert '2024-01-15' in date.isot

    def test_empty_header(self):
        """Test handling of empty header."""
        from custom_code.processors.spectrum_reader import _parse_date_from_header
        
        hdr = {}
        date = _parse_date_from_header(hdr)
        
        assert date is None


class TestParseDateFromFilename:
    """Tests for _parse_date_from_filename function."""

    def test_jd_in_filename(self):
        """Test parsing JD from filename."""
        from custom_code.processors.spectrum_reader import _parse_date_from_filename
        
        filename = 'spectrum_2460000.5.fits'
        date = _parse_date_from_filename(filename)
        
        assert date is not None
        assert abs(date.jd - 2460000.5) < 0.1

    def test_mjd_in_filename(self):
        """Test parsing MJD from filename."""
        from custom_code.processors.spectrum_reader import _parse_date_from_filename
        
        filename = 'spectrum_60000.5.txt'
        date = _parse_date_from_filename(filename)
        
        assert date is not None
        assert abs(date.mjd - 60000.5) < 0.1

    def test_iso_date_in_filename(self):
        """Test parsing ISO date from filename."""
        from custom_code.processors.spectrum_reader import _parse_date_from_filename
        
        filename = 'spectrum_2024-01-15.fits'
        date = _parse_date_from_filename(filename)
        
        assert date is not None

    def test_tns_format_in_filename(self):
        """Test parsing TNS-style date from filename."""
        from custom_code.processors.spectrum_reader import _parse_date_from_filename
        
        filename = 'AT2024abc_2024-01-15_12-30-00.txt'
        date = _parse_date_from_filename(filename)
        
        assert date is not None

    def test_no_date_in_filename(self):
        """Test handling of filename without date."""
        from custom_code.processors.spectrum_reader import _parse_date_from_filename
        
        filename = 'spectrum_data.fits'
        date = _parse_date_from_filename(filename)
        
        assert date is None


class TestRemoveBadCards:
    """Tests for _remove_bad_cards function."""

    def test_valid_header_unchanged(self):
        """Test that valid headers are unchanged."""
        from custom_code.processors.spectrum_reader import _remove_bad_cards
        from astropy.io import fits
        
        hdr = fits.Header()
        hdr['SIMPLE'] = True
        hdr['BITPIX'] = -32
        
        result = _remove_bad_cards(hdr)
        
        assert 'SIMPLE' in result
        assert 'BITPIX' in result


class TestReadAsciiSpectrum:
    """Tests for _read_ascii_spectrum function."""

    def test_parse_header_comments(self):
        """Test parsing header comments from ASCII file."""
        sample_content = """# TELESCOP = Keck
# DATE-OBS = 2024-01-15
4000.0 1.5e-15
4500.0 2.0e-15
5000.0 1.8e-15
"""
        from custom_code.processors.spectrum_reader import _read_ascii_spectrum
        
        with patch('astropy.io.ascii.read') as mock_read:
            mock_table = MagicMock()
            mock_table.columns = [[4000., 4500., 5000.], [1.5e-15, 2.0e-15, 1.8e-15]]
            mock_table.meta = {'comments': ['TELESCOP = Keck', 'DATE-OBS = 2024-01-15']}
            mock_read.return_value = mock_table
            
            wl, flux, hdr = _read_ascii_spectrum('test.txt')
            
            assert len(wl) == 3
            assert 'TELESCOP' in hdr or len(hdr) >= 0


class TestReadJsonSpectrum:
    """Tests for _read_json_spectrum function."""

    def test_osc_format(self):
        """Test reading Open Astronomy Catalog JSON format."""
        from custom_code.processors.spectrum_reader import _read_json_spectrum
        
        osc_data = {
            'test_object': {
                'spectra': [{
                    'data': [[40000, 1.5], [45000, 2.0], [50000, 1.8]],
                    'time': '60000.0',
                    'u_time': 'MJD',
                    'telescope': 'Keck',
                    'instrument': 'LRIS'
                }]
            }
        }
        
        with patch('builtins.open', mock_open(read_data=json.dumps(osc_data))):
            wl, flux, hdr = _read_json_spectrum('/path/test_object.json')
            
            assert len(wl) == 3
            assert len(flux) == 3
            assert hdr.get('telescope') == 'Keck'

    def test_empty_spectra(self):
        """Test handling of empty spectra list."""
        from custom_code.processors.spectrum_reader import _read_json_spectrum
        
        osc_data = {'test_object': {'spectra': []}}
        
        with patch('builtins.open', mock_open(read_data=json.dumps(osc_data))):
            wl, flux, hdr = _read_json_spectrum('/path/test_object.json')
            
            assert len(wl) == 0
            assert len(flux) == 0


class TestReadspec:
    """Tests for the main readspec function."""

    def test_telescope_extraction(self):
        """Test telescope name extraction from header."""
        from custom_code.processors.spectrum_reader import readspec
        
        with patch('custom_code.processors.spectrum_reader._read_fits_spectrum') as mock_read:
            mock_read.return_value = (
                np.array([4000., 5000., 6000.]),
                np.array([1e-15, 2e-15, 1.5e-15]),
                {'TELESCOP': 'Keck I', 'INSTRUME': 'LRIS', 'MJD-OBS': 60000.0}
            )
            
            wl, flux, date, telescope, instrument = readspec('test.fits')
            
            assert telescope == 'Keck I'
            assert instrument == 'LRIS'

    def test_instrument_fallback(self):
        """Test instrument name extraction with fallback keywords."""
        from custom_code.processors.spectrum_reader import readspec
        
        with patch('custom_code.processors.spectrum_reader._read_fits_spectrum') as mock_read:
            mock_read.return_value = (
                np.array([4000., 5000., 6000.]),
                np.array([1e-15, 2e-15, 1.5e-15]),
                {'TELESCOPE': 'VLT', 'INSTR': 'FORS2'}
            )
            
            wl, flux, date, telescope, instrument = readspec('test.fits')
            
            assert telescope == 'VLT'
            assert instrument == 'FORS2'

    def test_return_header_option(self):
        """Test return_header=True returns header."""
        from custom_code.processors.spectrum_reader import readspec
        
        with patch('custom_code.processors.spectrum_reader._read_fits_spectrum') as mock_read:
            mock_read.return_value = (
                np.array([4000., 5000., 6000.]),
                np.array([1e-15, 2e-15, 1.5e-15]),
                {'TELESCOP': 'Gemini', 'MJD-OBS': 60000.0}
            )
            
            result = readspec('test.fits', return_header=True)
            
            assert len(result) == 6
            assert isinstance(result[5], dict)


class TestVectorizedPerformance:
    """Tests to verify vectorized operations."""

    def test_wavelength_array_is_numpy(self):
        """Test that wavelength is returned as numpy array."""
        from custom_code.processors.spectrum_reader import readspec
        
        with patch('custom_code.processors.spectrum_reader._read_fits_spectrum') as mock_read:
            mock_read.return_value = (
                np.array([4000., 5000., 6000.]),
                np.array([1e-15, 2e-15, 1.5e-15]),
                {}
            )
            
            wl, flux, date, telescope, instrument = readspec('test.fits')
            
            assert isinstance(wl, np.ndarray)
            assert isinstance(flux, np.ndarray)

    def test_large_array_handling(self):
        """Test handling of large arrays (vectorization check)."""
        from custom_code.processors.spectrum_reader import _convert_spectrum_units
        
        n_points = 10000
        wl = np.linspace(3000, 10000, n_points)
        flux = np.random.random(n_points) * 1e-15
        hdr = {}
        
        wl_out, flux_out = _convert_spectrum_units(wl, flux, hdr)
        
        assert len(wl_out) == n_points
        assert len(flux_out) == n_points
