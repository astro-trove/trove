"""
Pytest configuration and fixtures for the SAGUARO TOM test suite.
"""
import os
import pytest
import django
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from decimal import Decimal


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'saguaro_tom.settings')


@pytest.fixture(scope='session', autouse=True)
def django_setup():
    """Initialize Django settings before running tests."""
    django.setup()


@pytest.fixture
def mock_target():
    """Create a mock Target object for testing."""
    target = MagicMock()
    target.id = 1
    target.name = 'AT2024abc'
    target.ra = 150.0
    target.dec = 30.0
    target.distance = 100.0
    target.eventcandidate_set = MagicMock()
    target.eventcandidate_set.all.return_value = []
    return target


@pytest.fixture
def mock_localization():
    """Create a mock EventLocalization object for testing."""
    localization = MagicMock()
    localization.distance_mean = 500.0
    localization.distance_std = 50.0
    localization.area_50 = 100.0
    localization.area_90 = 500.0
    localization.date = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return localization


@pytest.fixture
def mock_nonlocalized_event():
    """Create a mock NonLocalizedEvent object for testing."""
    nle = MagicMock()
    nle.id = 1
    nle.event_id = 'S240101abc'
    nle.event_type = 'GW'
    nle.state = 'ACTIVE'
    return nle


@pytest.fixture
def mock_sequence():
    """Create a mock EventSequence object for testing."""
    seq = MagicMock()
    seq.sequence_id = 1
    seq.details = {
        'group': 'CBC',
        'search':'AllSky',
        'pipeline':'gstlal',
        'significant': True,
        'time': '2024-01-01T00:00:00.000000+00:00',
        'far': 1e-10,
        'instruments': ['H1', 'L1', 'V1'],
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
    seq.nonlocalizedevent = MagicMock()
    seq.nonlocalizedevent.event_id = 'S240101abc'
    seq.localization = MagicMock()
    seq.external_coincidence = None
    return seq


@pytest.fixture
def mock_request():
    """Create a mock Django request object."""
    request = MagicMock()
    request.user = MagicMock()
    request.user.is_authenticated = True
    request.GET = {}
    return request


@pytest.fixture
def mock_context(mock_request):
    """Create a mock template context."""
    return {'request': mock_request}


@pytest.fixture
def sample_photometry_datum():
    """Create sample photometry datum for testing."""
    return {
        'magnitude': 18.5,
        'error': 0.1,
        'filter': 'r'
    }


@pytest.fixture
def sample_host_galaxy_df():
    """Create a sample host galaxy DataFrame for testing."""
    import pandas as pd
    return pd.DataFrame({
        'name': ['Galaxy1', 'Galaxy2', 'Galaxy3'],
        'ra': [150.0, 150.1, 150.2],
        'dec': [30.0, 30.1, 30.2],
        'z': [0.05, 0.06, 0.04],
        'z_neg_err': [0.001, 0.002, 0.001],
        'z_pos_err': [0.001, 0.002, 0.001],
        'lumdist': [200.0, 250.0, 180.0],
        'lumdist_neg_err': [20.0, 25.0, 18.0],
        'lumdist_pos_err': [20.0, 25.0, 18.0],
        'default_mag': [18.0, 19.0, 17.5],
        'offset': [2.5, 5.0, 1.0],
        'pcc': [0.1, 0.2, 0.05],
        'catalog': ['GladePlus', 'DesiDr1', 'Hecate1']
    })
