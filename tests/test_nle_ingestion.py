"""Tests for local IGWN non-localized event ingestion (issue #5)."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from astropy.table import Table
from django.core.management import call_command
from hop.models import JSONBlob
from ligo.skymap import moc
from ligo.skymap.io import write_sky_map
from tom_nonlocalizedevents.models import EventSequence, NonLocalizedEvent

from custom_code.nle_ingestion import (
    attach_skymap_to_alert,
    build_hop_message,
    decompress_fits_bytes,
    ensure_multiorder_skymap_bytes,
    load_alert_dict,
    load_skymap_bytes,
    read_skymap_table,
    upload_local_nle,
)

TESTS_DATA = Path(__file__).resolve().parent / "data"
GW170817_JSON = TESTS_DATA / "GW170817-update.json"
GW170817_SKYMAP = TESTS_DATA / "GW170817-bayestar.fits.gz"


def _tiny_multiorder_skymap_bytes() -> bytes:
    """Minimal multi-order skymap for fast DB tests (not GW170817 science data)."""
    order = 3
    ipix = np.array([0, 1, 2], dtype=np.int64)
    uniq = moc.nest2uniq(order, ipix)
    probdensity = np.full(3, 1.0 / (4 * np.pi))
    table = Table([uniq, probdensity], names=["UNIQ", "PROBDENSITY"])
    table.meta.update(
        {
            "DISTMEAN": 40.0,
            "DISTSTD": 10.0,
            "DATE": "2017-08-17T12:41:04.444458",
            "OBJECT": "GW170817",
        }
    )
    buffer = BytesIO()
    write_sky_map(buffer, table, nest=True)
    buffer.seek(0)
    return buffer.read()


def _flat_bayestar_skymap_bytes() -> bytes:
    """Minimal *flat* (rasterized) BAYESTAR-style skymap: NESTED + PROB, piecewise constant.

    Built so that derasterize can merge identical-valued sibling pixels back into coarse tiles,
    mimicking a real flat bayestar.fits.gz (which is a rasterization of a multi-order map).
    """
    nside = 8
    npix = 12 * nside ** 2
    prob = np.zeros(npix)
    prob[:64] = 1.0
    prob /= prob.sum()
    table = Table(
        {
            "PROB": prob,
            "DISTMU": np.full(npix, 40.0),
            "DISTSIGMA": np.full(npix, 10.0),
            "DISTNORM": np.full(npix, 1.0),
        }
    )
    table.meta.update(
        {
            "ORDERING": "NESTED",
            "DISTMEAN": 40.0,
            "DISTSTD": 10.0,
            "OBJECT": "GW170817",
            "DATE": "2017-08-17T12:41:04.444458",
        }
    )
    buffer = BytesIO()
    write_sky_map(buffer, table, nest=True)
    buffer.seek(0)
    return buffer.read()


@pytest.fixture
def flat_bayestar_skymap_bytes() -> bytes:
    return _flat_bayestar_skymap_bytes()


@pytest.fixture
def gw170817_alert() -> dict:
    return load_alert_dict(GW170817_JSON)


@pytest.fixture
def gw170817_skymap_bytes() -> bytes:
    if not GW170817_SKYMAP.is_file():
        pytest.skip(f"missing fixture skymap: {GW170817_SKYMAP}")
    return load_skymap_bytes(GW170817_SKYMAP)


@pytest.fixture
def tiny_multiorder_skymap_bytes() -> bytes:
    return _tiny_multiorder_skymap_bytes()


class TestNleIngestionHelpers:
    def test_load_alert_dict_gw170817(self, gw170817_alert):
        assert gw170817_alert["superevent_id"] == "GW170817"
        assert gw170817_alert["event"]["pipeline"] == "pycbc"
        assert "skymap" not in gw170817_alert["event"]

    def test_attach_skymap_to_alert(self, gw170817_alert):
        payload = b"fits-bytes-placeholder"
        enriched = attach_skymap_to_alert(gw170817_alert, payload)
        assert enriched["event"]["skymap"] is payload
        assert "skymap" not in gw170817_alert["event"]

    def test_build_hop_message_wraps_alert_in_list(self, gw170817_alert):
        message = build_hop_message(gw170817_alert)
        assert isinstance(message, JSONBlob)
        assert message.content[0]["superevent_id"] == "GW170817"

    def test_load_skymap_bytes_decompresses_gzip(self):
        if not GW170817_SKYMAP.is_file():
            pytest.skip(f"missing fixture skymap: {GW170817_SKYMAP}")
        raw = GW170817_SKYMAP.read_bytes()
        assert raw[:2] == b"\x1f\x8b"
        fits_bytes = load_skymap_bytes(GW170817_SKYMAP)
        assert fits_bytes[:2] != b"\x1f\x8b"
        assert b"SIMPLE" in fits_bytes[:80]

    def test_read_skymap_table_from_gzip_bytes(self):
        if not GW170817_SKYMAP.is_file():
            pytest.skip(f"missing fixture skymap: {GW170817_SKYMAP}")
        table = read_skymap_table(GW170817_SKYMAP.read_bytes())
        assert "PROB" in table.colnames

    def test_decompress_fits_bytes_idempotent_on_raw_fits(self, tiny_multiorder_skymap_bytes):
        assert decompress_fits_bytes(tiny_multiorder_skymap_bytes) == tiny_multiorder_skymap_bytes

    def test_ensure_multiorder_passes_through_existing_multiorder(self, tiny_multiorder_skymap_bytes):
        out = ensure_multiorder_skymap_bytes(tiny_multiorder_skymap_bytes)
        assert out == tiny_multiorder_skymap_bytes

    def test_ensure_multiorder_converts_flat_to_adaptive(self, flat_bayestar_skymap_bytes):
        import astropy_healpix as ah
        import astropy.units as u

        flat = Table.read(BytesIO(flat_bayestar_skymap_bytes), format="fits")
        converted_bytes = ensure_multiorder_skymap_bytes(flat_bayestar_skymap_bytes)
        moc_table = Table.read(BytesIO(converted_bytes), format="fits")

        assert "UNIQ" in moc_table.colnames
        assert "PROBDENSITY" in moc_table.colnames
        # adaptive: identical-valued siblings collapse, so far fewer tiles than flat pixels
        assert len(moc_table) < len(flat)
        # PROBDENSITY must be a probability density per steradian for confidence regions
        assert moc_table["PROBDENSITY"].unit == u.steradian ** -1
        # total probability integrates to ~1 (and the units are convertible -> no parse error)
        level, _ = ah.uniq_to_level_ipix(moc_table["UNIQ"])
        pixel_area = ah.nside_to_pixel_area(ah.level_to_nside(level))
        total_prob = (pixel_area * moc_table["PROBDENSITY"]).sum()
        assert abs(float(total_prob) - 1.0) < 1e-6

    @pytest.mark.slow
    def test_ensure_multiorder_skymap_bytes_gw170817(self, gw170817_skymap_bytes):
        converted = ensure_multiorder_skymap_bytes(gw170817_skymap_bytes)
        table = Table.read(BytesIO(converted), format="fits")
        assert "UNIQ" in table.colnames
        assert "PROBDENSITY" in table.colnames
        assert "DISTMU" in table.colnames


class TestUploadLocalNle:
    @patch("custom_code.nle_ingestion.handle_message_and_send_alerts")
    @patch("custom_code.nle_ingestion.ensure_multiorder_skymap_bytes")
    def test_upload_local_nle_returns_handler_result(
        self,
        mock_convert,
        mock_handle,
        tiny_multiorder_skymap_bytes,
    ):
        mock_convert.return_value = tiny_multiorder_skymap_bytes
        nle = MagicMock(spec=NonLocalizedEvent)
        nle.event_id = "GW170817"
        sequence = MagicMock(spec=EventSequence)
        mock_handle.return_value = (nle, sequence)

        result = upload_local_nle(GW170817_JSON, GW170817_SKYMAP)

        assert result == (nle, sequence)
        mock_convert.assert_called_once()
        mock_handle.assert_called_once()
        message = mock_handle.call_args.args[0]
        assert isinstance(message, JSONBlob)
        alert = message.content[0]
        assert alert["superevent_id"] == "GW170817"
        assert alert["event"]["skymap"] == tiny_multiorder_skymap_bytes
        assert mock_handle.call_args.args[1] is None

    @patch("custom_code.nle_ingestion.ingest_local_igwn_alert")
    @patch("custom_code.nle_ingestion.ensure_multiorder_skymap_bytes")
    def test_upload_local_nle_passes_converted_skymap_to_handler(
        self,
        mock_convert,
        mock_ingest,
        tiny_multiorder_skymap_bytes,
    ):
        mock_convert.return_value = tiny_multiorder_skymap_bytes
        existing = MagicMock(spec=NonLocalizedEvent)
        existing.event_id = "GW170817"
        sequence = MagicMock(spec=EventSequence)
        mock_ingest.return_value = (existing, sequence)

        result = upload_local_nle(GW170817_JSON, GW170817_SKYMAP)

        assert result == (existing, sequence)
        alert_passed = mock_ingest.call_args.args[0]
        assert alert_passed["superevent_id"] == "GW170817"
        assert alert_passed["event"]["skymap"] == tiny_multiorder_skymap_bytes


_CMD = "custom_code.management.commands.ingest_local_nle"


class TestIngestLocalNleExistenceCheck:
    """The ingest_local_nle command checks for an existing NLE before writing."""

    @patch(f"{_CMD}.connection")
    @patch(f"{_CMD}.ingest_local_igwn_alert")
    @patch(f"{_CMD}.load_skymap_bytes", return_value=b"fits-bytes")
    @patch(f"{_CMD}.NonLocalizedEvent")
    def test_skip_existing_aborts_when_event_exists(
        self, mock_nle, mock_load_skymap, mock_ingest, mock_conn
    ):
        existing = MagicMock()
        existing.id = 42
        existing.sequences.count.return_value = 3
        mock_nle.objects.filter.return_value.first.return_value = existing

        call_command(
            "ingest_local_nle",
            str(GW170817_JSON),
            str(GW170817_SKYMAP),
            "--no-convert-skymap",
            "--skip-existing",
        )

        mock_nle.objects.filter.assert_called_once_with(event_id="GW170817")
        mock_ingest.assert_not_called()

    @patch(f"{_CMD}.connection")
    @patch(f"{_CMD}.ingest_local_igwn_alert")
    @patch(f"{_CMD}.load_skymap_bytes", return_value=b"fits-bytes")
    @patch(f"{_CMD}.NonLocalizedEvent")
    def test_existing_without_skip_proceeds(
        self, mock_nle, mock_load_skymap, mock_ingest, mock_conn
    ):
        existing = MagicMock()
        existing.id = 42
        existing.sequences.count.return_value = 1
        mock_nle.objects.filter.return_value.first.return_value = existing
        mock_ingest.return_value = (MagicMock(event_id="GW170817"), MagicMock())

        call_command(
            "ingest_local_nle",
            str(GW170817_JSON),
            str(GW170817_SKYMAP),
            "--no-convert-skymap",
            "--yes",
        )

        mock_ingest.assert_called_once()

    @patch(f"{_CMD}.connection")
    @patch(f"{_CMD}.ingest_local_igwn_alert")
    @patch(f"{_CMD}.load_skymap_bytes", return_value=b"fits-bytes")
    @patch(f"{_CMD}.NonLocalizedEvent")
    def test_skip_existing_proceeds_when_event_absent(
        self, mock_nle, mock_load_skymap, mock_ingest, mock_conn
    ):
        mock_nle.objects.filter.return_value.first.return_value = None
        mock_ingest.return_value = (MagicMock(event_id="GW170817"), MagicMock())

        call_command(
            "ingest_local_nle",
            str(GW170817_JSON),
            str(GW170817_SKYMAP),
            "--no-convert-skymap",
            "--skip-existing",
            "--yes",
        )

        mock_ingest.assert_called_once()
