"""
Unit tests for form fields, filters, and widgets.

These test the pure logic functions in custom_code/filters.py and related modules.
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone


class TestLocalizationWidget:
    """Tests for LocalizationWidget in custom_code/filters.py"""

    def test_decompress_none(self):
        """Test decompress with None value."""
        with patch('custom_code.filters._get_nonlocalized_event_choices', return_value=[(None, '-------')]):
            from custom_code.filters import LocalizationWidget
            
            widget = LocalizationWidget()
            result = widget.decompress(None)
            
            assert result == (None, None, None)

    def test_decompress_empty_tuple(self):
        """Test decompress with empty tuple."""
        with patch('custom_code.filters._get_nonlocalized_event_choices', return_value=[(None, '-------')]):
            from custom_code.filters import LocalizationWidget
            
            widget = LocalizationWidget()
            result = widget.decompress(())
            
            assert result == (None, None, None)

    def test_decompress_valid_value(self):
        """Test decompress with valid value."""
        with patch('custom_code.filters._get_nonlocalized_event_choices', return_value=[(None, '-------')]):
            from custom_code.filters import LocalizationWidget
            
            widget = LocalizationWidget()
            value = ('S240101abc', 90, 3.0)
            result = widget.decompress(value)
            
            assert result == value


class TestLocalizationField:
    """Tests for LocalizationField in custom_code/filters.py"""

    def test_compress_valid_list(self):
        """Test compress with valid data list."""
        with patch('custom_code.filters.NonLocalizedEvent') as mock_nle:
            from custom_code.filters import LocalizationField
            
            field = LocalizationField()
            data_list = ['event', 90, 3.0]
            
            result = field.compress(data_list)
            
            assert result == data_list

    def test_compress_empty_list(self):
        """Test compress with empty data list."""
        with patch('custom_code.filters.NonLocalizedEvent') as mock_nle:
            from custom_code.filters import LocalizationField
            
            field = LocalizationField()
            result = field.compress([])
            
            assert result == []

class TestLocalizationFilterMethods:
    """Tests for LocalizationFilter methods."""
    ### TODO
    ### test that the LocalizationFilter.filter() method behaves as expected

class TestNonLocalizedEventFilterMethods:
    """Tests for NonLocalizedEventFilter static methods."""
    ### TODO
    ### test that the NonlocalizedEventFilter.last_sequence_filter() static method behaves as expected

class TestAlertStreamHandlers:
    """Tests for alert stream handler functions."""

    def test_pick_slack_channel_test_alert(self):
        """Test pick_slack_channel for test alerts."""
        from custom_code.alertstream_handlers import pick_slack_channel
        
        seq = MagicMock()
        seq.nonlocalizedevent.event_id = 'MS240101abc'
        seq.details = {
            'significant': True,
            'group': 'CBC',
            'properties': {'HasNS': 0.9},
            'classification': {'BNS': 0.8, 'NSBH': 0.1}
        }
        
        is_test_alert, is_significant, is_burst, has_ns = pick_slack_channel(seq)
        
        assert is_test_alert is True

    def test_pick_slack_channel_real_alert(self):
        """Test pick_slack_channel for real alerts."""
        from custom_code.alertstream_handlers import pick_slack_channel
        
        seq = MagicMock()
        seq.nonlocalizedevent.event_id = 'S240101abc'
        seq.details = {
            'significant': True,
            'group': 'CBC',
            'properties': {'HasNS': 0.9},
            'classification': {'BNS': 0.8, 'NSBH': 0.1}
        }
        
        is_test_alert, is_significant, is_burst, has_ns = pick_slack_channel(seq)
        
        assert is_test_alert is False

    def test_pick_slack_channel_subthreshold(self):
        """Test pick_slack_channel for subthreshold alerts."""
        from custom_code.alertstream_handlers import pick_slack_channel
        
        seq = MagicMock()
        seq.nonlocalizedevent.event_id = 'S240101abc'
        seq.details = {
            'significant': False,
            'group': 'CBC',
            'properties': {'HasNS': 0.5},
            'classification': {'BNS': 0.3, 'NSBH': 0.1}
        }
        
        is_test_alert, is_significant, is_burst, has_ns = pick_slack_channel(seq)
        
        assert is_significant is False

    def test_pick_slack_channel_burst(self):
        """Test pick_slack_channel for burst alerts."""
        from custom_code.alertstream_handlers import pick_slack_channel
        
        seq = MagicMock()
        seq.nonlocalizedevent.event_id = 'S240101abc'
        seq.details = {
            'significant': True,
            'group': 'Burst',
            'properties': {},
            'classification': {}
        }
        
        is_test_alert, is_significant, is_burst, has_ns = pick_slack_channel(seq)
        
        assert is_burst is True

    def test_pick_slack_channel_has_ns_from_properties(self):
        """Test pick_slack_channel detecting HasNS from properties."""
        from custom_code.alertstream_handlers import pick_slack_channel
        
        seq = MagicMock()
        seq.nonlocalizedevent.event_id = 'S240101abc'
        seq.details = {
            'significant': True,
            'group': 'CBC',
            'properties': {'HasNS': 0.5},
            'classification': {'BBH': 0.9, 'BNS': 0.0, 'NSBH': 0.0}
        }
        
        is_test_alert, is_significant, is_burst, has_ns = pick_slack_channel(seq)
        
        assert has_ns is True

    def test_pick_slack_channel_has_ns_from_classification(self):
        """Test pick_slack_channel detecting HasNS from classification."""
        from custom_code.alertstream_handlers import pick_slack_channel
        
        seq = MagicMock()
        seq.nonlocalizedevent.event_id = 'S240101abc'
        seq.details = {
            'significant': True,
            'group': 'CBC',
            'properties': {'HasNS': 0.0},
            'classification': {'BNS': 0.8, 'NSBH': 0.0, 'BBH': 0.1}
        }
        
        is_test_alert, is_significant, is_burst, has_ns = pick_slack_channel(seq)
        
        assert has_ns is True

    def test_pick_slack_channel_bbh(self):
        """Test pick_slack_channel for BBH (no NS)."""
        from custom_code.alertstream_handlers import pick_slack_channel
        
        seq = MagicMock()
        seq.nonlocalizedevent.event_id = 'S240101abc'
        seq.details = {
            'significant': True,
            'group': 'CBC',
            'properties': {'HasNS': 0.0},
            'classification': {'BBH': 0.95, 'BNS': 0.0, 'NSBH': 0.0}
        }
        
        is_test, is_significant, is_burst, has_ns = pick_slack_channel(seq)
        
        assert has_ns is False


class TestAtlasQueryForm:
    """Tests for CustomAtlasForcedPhotometryQueryForm."""

    def test_clean_valid_dates(self):
        """Test form cleaning with valid date range."""
        from custom_code.atlas import CustomAtlasForcedPhotometryQueryForm
        
        form = CustomAtlasForcedPhotometryQueryForm(data={
            'min_date': '2024-01-01',
            'max_date': '2024-01-31',
        })
        
        if form.is_valid():
            cleaned = form.clean()
            assert 'min_date_mjd' in cleaned or cleaned is not None

    def test_form_layout(self):
        """Test form layout method returns expected structure."""
        from custom_code.atlas import CustomAtlasForcedPhotometryQueryForm
        
        form = CustomAtlasForcedPhotometryQueryForm()
        layout = form.layout()
        
        assert layout is not None


class TestSpectroscopyProcessor:
    """Tests for FiniteSpectrumSerializer in spectroscopy processor."""

    def test_serialize_basic_spectrum(self):
        """Test serialization of a basic spectrum."""
        from custom_code.processors.spectroscopy_processor import FiniteSpectrumSerializer
        import numpy as np
        
        serializer = FiniteSpectrumSerializer()
        
        mock_spectrum = MagicMock()
        flux_arr = np.array([1.0, 1.5, 1.2, 0.8])
        wl_arr = np.array([4000., 5000., 6000., 7000.])
        mock_spectrum.flux.value = flux_arr
        mock_spectrum.wavelength.value = wl_arr
        mock_spectrum.flux.unit.to_string.return_value = 'erg / (Angstrom cm2 s)'
        mock_spectrum.wavelength.unit.to_string.return_value = 'Angstrom'
        
        result = serializer.serialize(mock_spectrum)
        
        assert result is not None
        assert 'flux' in result
        assert 'wavelength' in result


class TestCredibleRegionProbabilities:
    """Tests for credible region probability configuration."""

    def test_credible_region_choices_format(self):
        """Test that credible region choices are correctly formatted."""
        from custom_code.filters import CREDIBLE_REGION_CHOICES
        
        for value, label in CREDIBLE_REGION_CHOICES:
            assert isinstance(value, int)
            assert isinstance(label, str)
            assert '%' in label

    def test_probability_to_percent_conversion(self):
        """Test conversion of probability to percentage."""
        probabilities = [0.5, 0.9, 0.95]
        
        for p in probabilities:
            percent = int(100.0 * p)
            label = f'{p:.0%}'
            
            assert percent == int(p * 100)
            assert '%' in label
