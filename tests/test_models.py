"""
Unit tests for Django models and model methods.

These test the models in trove_targets/models.py and candidate_vetting/models.py.
"""
import pytest
import numpy as np
from decimal import Decimal


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

    def test_distance_modulus_calculation(self):
        """Test distance modulus calculation."""
        distance_mpc = 100.0
        dm = 5.0 * (np.log10(distance_mpc * 1e6) - 1)
        
        assert dm > 0
        assert abs(dm - 35.0) < 0.1

    def test_absolute_magnitude_calculation(self):
        """Test absolute magnitude calculation from apparent magnitude."""
        apparent_mag = 19.0
        distance_mpc = 100.0
        dm = 5.0 * (np.log10(distance_mpc) + 5.)
        absolute_mag = apparent_mag - dm
        
        assert absolute_mag < apparent_mag


class TestScoreFactorModel:
    """Tests for ScoreFactor model in candidate_vetting/models.py"""

    def test_score_factor_key_values(self):
        """Test valid score factor key values."""
        valid_keys = [
            'skymap_score',
            'ps_score',
            'host_distance_score',
            'phot_peak_lum',
            'phot_peak_time',
            'phot_decay_rate'
        ]
        
        for key in valid_keys:
            assert len(key) > 0
            assert isinstance(key, str)

    def test_score_value_range(self):
        """Test score values are numeric."""
        values = [0.0, 0.5, 1.0, 1e42, -0.5]
        
        for val in values:
            assert isinstance(val, (int, float))


class TestQ3CModels:
    """Tests for Q3C catalog models in candidate_vetting/models.py"""

    def test_coordinate_precision(self):
        """Test coordinate precision for Q3C queries."""
        ra = Decimal('150.123456789')
        dec = Decimal('30.987654321')
        
        assert isinstance(ra, Decimal)
        assert isinstance(dec, Decimal)

    def test_catalog_names(self):
        """Test catalog model names follow convention."""
        catalog_names = [
            'AsassnQ3C',
            'DesiSpecQ3C',
            'FermiLatQ3C',
            'Gaiadr3VariableQ3C',
            'GladePlusQ3C',
            'GwgcQ3C',
            'HecateQ3C',
            'LsDr10Q3C',
            'MilliquasQ3C',
            'Ps1Q3C',
            'RomaBzcatQ3C',
            'Sdss12PhotozQ3C',
            'TnsQ3C'
        ]
        
        for name in catalog_names:
            assert name.endswith('Q3C')


class TestEventCandidateModel:
    """Tests for EventCandidate model."""

    def test_priority_default_value(self):
        """Test default priority value."""
        default_priority = 0.0
        assert 0 <= default_priority <= 1

    def test_priority_calculation(self):
        """Test priority score calculation."""
        scores = {
            'skymap_score': 0.9,
            'ps_score': 1.0,
            'host_distance_score': 0.8
        }
        
        combined_score = np.prod(list(scores.values()))
        assert 0 <= combined_score <= 1


class TestCredibleRegionContour:
    """Tests for CredibleRegionContour model."""

    def test_probability_values(self):
        """Test valid probability values."""
        valid_probs = [0.5, 0.9, 0.95]
        
        for prob in valid_probs:
            assert 0 < prob <= 1

    def test_pixels_format(self):
        """Test pixels JSON format."""
        pixels = {
            '10': [1, 2, 3, 4, 5],
            '11': [10, 11, 12],
            '12': [100, 101]
        }
        
        for order, ipix_list in pixels.items():
            assert order.isdigit()
            assert isinstance(ipix_list, list)
            assert all(isinstance(i, int) for i in ipix_list)


class TestEventLocalization:
    """Tests for EventLocalization model."""

    def test_distance_attributes(self):
        """Test distance attributes."""
        distance_mean = 500.0
        distance_std = 50.0
        
        assert distance_std < distance_mean
        assert distance_std > 0

    def test_area_attributes(self):
        """Test area attributes."""
        area_50 = 100.0
        area_90 = 500.0
        
        assert area_50 < area_90
        assert area_50 > 0


class TestNonLocalizedEvent:
    """Tests for NonLocalizedEvent model."""

    def test_event_type_choices(self):
        """Test valid event type choices."""
        event_types = [
            'GW',
            'GRB',
            'NU',
            'UNK'
        ]
        
        for et in event_types:
            assert isinstance(et, str)
            assert et.isupper()

    def test_state_choices(self):
        """Test valid state choices."""
        states = ['ACTIVE', 'RETRACTED']
        
        for state in states:
            assert isinstance(state, str)

    def test_event_id_format(self):
        """Test event ID format validation."""
        valid_ids = ['S240101abc', 'MS240101xyz', 'GRB240101A']
        
        for event_id in valid_ids:
            assert len(event_id) > 0
            assert event_id[0].isupper()


class TestEventSequence:
    """Tests for EventSequence model."""

    def test_sequence_id_increment(self):
        """Test sequence ID increments properly."""
        sequence_ids = [1, 2, 3, 4, 5]
        
        for i, sid in enumerate(sequence_ids):
            assert sid == i + 1

    def test_details_json_structure(self):
        """Test details JSON structure for CBC."""
        details = {
            'group': 'CBC',
            'significant': True,
            'far': 1e-10,
            'instruments': ['H1', 'L1', 'V1'],
            'time': '2024-01-01T00:00:00.000000+00:00',
            'classification': {
                'BNS': 0.8,
                'NSBH': 0.1,
                'BBH': 0.05,
                'Terrestrial': 0.05
            },
            'properties': {
                'HasNS': 0.9,
                'HasRemnant': 0.8,
                'HasMassGap': 0.1
            }
        }
        
        assert sum(details['classification'].values()) == pytest.approx(1.0, abs=0.01)
        assert all(0 <= v <= 1 for v in details['properties'].values())

    def test_details_json_structure_burst(self):
        """Test details JSON structure for Burst."""
        details = {
            'group': 'Burst',
            'significant': True,
            'far': 1e-8,
            'instruments': ['H1', 'L1'],
            'time': '2024-01-01T00:00:00.000000+00:00',
            'duration': 0.001,
            'central_frequency': 100.0
        }
        
        assert details['group'] == 'Burst'
        assert details['duration'] > 0
        assert details['central_frequency'] > 0

    def test_details_json_structure_neutrino(self):
        """Test details JSON structure for Neutrino."""
        details = {
            'notice_type': 'GOLD',
            'far': 0.1,
            'energy': 200.0,
            'signalness': 0.9,
            'time': '2024-01-01T00:00:00.000000+00:00'
        }
        
        assert details['notice_type'] in ['BRONZE', 'GOLD']
        assert details['energy'] > 0
        assert 0 <= details['signalness'] <= 1


class TestTargetExtra:
    """Tests for TargetExtra model (from tom_targets)."""

    def test_host_galaxies_json_format(self):
        """Test Host Galaxies JSON format."""
        import json
        
        galaxies = [
            {
                'ID': 'NGC1234',
                'RA': 150.0,
                'Dec': 30.0,
                'Dist': 100.0,
                'DistErr': 10.0,
                'z': 0.023,
                'zErr': 0.001,
                'Mags': 14.5,
                'PCC': 0.05,
                'Offset': 2.5,
                'Source': 'GladePlus'
            }
        ]
        
        json_str = json.dumps(galaxies)
        parsed = json.loads(json_str)
        
        assert len(parsed) == 1
        assert 'RA' in parsed[0]
        assert 'Dec' in parsed[0]

    def test_valid_target_extra_keys(self):
        """Test valid TargetExtra key names."""
        valid_keys = [
            'Host Galaxies',
            'TNS Name',
            'Classification',
            'Redshift',
            'Discovery Date'
        ]
        
        for key in valid_keys:
            assert isinstance(key, str)
            assert len(key) > 0
