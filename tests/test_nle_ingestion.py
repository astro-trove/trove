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
    fetch_gracedb_alert,
    iter_gracedb_superevents,
    latest_voevent_filename,
    load_alert_dict,
    load_skymap_bytes,
    parse_voevent_xml,
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


# A representative GraceDB superevent VOEvent (LVC/IGWN "Update" notice), trimmed to the
# fields ingestion cares about. Namespaced root + unqualified What/Param like real notices.
SAMPLE_VOEVENT_XML = b"""<?xml version="1.0" ?>
<voe:VOEvent xmlns:voe="http://www.ivoa.net/xml/VOEvent/v2.0"
             ivorn="ivo://gwnet/LVC#S190814bv-5-Update" role="observation" version="2.0">
  <Who>
    <Date>2019-08-15T10:19:10</Date>
    <Author><contactName>LIGO/Virgo</contactName></Author>
  </Who>
  <What>
    <Param name="Packet_Type" value="153"/>
    <Param name="internal" value="0"/>
    <Param name="Pkt_Ser_Num" value="5"/>
    <Param name="GraceID" value="S190814bv" ucd="meta.id"/>
    <Param name="AlertType" value="Update"/>
    <Param name="HardwareInj" value="0"/>
    <Param name="OpenAlert" value="1"/>
    <Param name="EventPage" value="https://gracedb.ligo.org/superevents/S190814bv/view/"/>
    <Param name="Instruments" value="H1,L1,V1"/>
    <Param name="FAR" value="2.033e-33"/>
    <Param name="Significant" value="1"/>
    <Param name="Group" value="CBC"/>
    <Param name="Pipeline" value="gstlal"/>
    <Param name="Search" value="AllSky"/>
    <Group type="GW_SKYMAP" name="LALInference.v1">
      <Param name="skymap_fits"
             value="https://gracedb.ligo.org/api/superevents/S190814bv/files/LALInference.v1.fits.gz"/>
    </Group>
    <Group type="Classification">
      <Param name="BNS" value="0.0"/>
      <Param name="NSBH" value="1.0"/>
      <Param name="BBH" value="0.0"/>
      <Param name="Terrestrial" value="0.0"/>
    </Group>
    <Group type="Properties">
      <Param name="HasNS" value="1.0"/>
      <Param name="HasRemnant" value="0.0"/>
      <Param name="HasMassGap" value="0.0"/>
    </Group>
  </What>
  <WhereWhen>
    <ObsDataLocation>
      <ObservationLocation>
        <AstroCoords coord_system_id="UTC-FK5-GEO">
          <Time><TimeInstant><ISOTime>2019-08-14T21:10:39.012957</ISOTime></TimeInstant></Time>
        </AstroCoords>
      </ObservationLocation>
    </ObsDataLocation>
  </WhereWhen>
</voe:VOEvent>
"""

SAMPLE_RETRACTION_XML = b"""<?xml version="1.0" ?>
<voe:VOEvent xmlns:voe="http://www.ivoa.net/xml/VOEvent/v2.0"
             ivorn="ivo://gwnet/LVC#S190814bv-6-Retraction" role="observation" version="2.0">
  <Who><Date>2019-08-15T11:00:00</Date></Who>
  <What>
    <Param name="GraceID" value="S190814bv"/>
    <Param name="AlertType" value="Retraction"/>
    <Param name="EventPage" value="https://gracedb.ligo.org/superevents/S190814bv/view/"/>
  </What>
</voe:VOEvent>
"""


class TestLatestVoeventFilename:
    def test_picks_highest_version(self):
        names = [
            "S190814bv-1-Preliminary.xml",
            "S190814bv-2-Initial.xml",
            "S190814bv-3-Preliminary.xml",
            "S190814bv-4-Update.xml",
            "S190814bv-5-Update.xml",
            "bayestar.fits.gz",
            "p_astro.json",
        ]
        assert latest_voevent_filename(names, "S190814bv") == "S190814bv-5-Update.xml"

    def test_ignores_revision_aliases(self):
        names = [
            "S190814bv-5-Update.xml",
            "S190814bv-5-Update.xml,0",
            "S190814bv-4-Update.xml,0",
        ]
        assert latest_voevent_filename(names, "S190814bv") == "S190814bv-5-Update.xml"

    def test_raises_when_no_voevent(self):
        with pytest.raises(ValueError):
            latest_voevent_filename(["bayestar.fits.gz"], "S190814bv")


class TestParseVoeventXml:
    def test_parses_update_alert(self):
        alert, skymap_url = parse_voevent_xml(SAMPLE_VOEVENT_XML)

        assert alert["superevent_id"] == "S190814bv"
        assert alert["alert_type"] == "UPDATE"
        assert alert["time_created"] == "2019-08-15T10:19:10Z"
        assert alert["urls"]["gracedb"].endswith("/superevents/S190814bv/view/")

        event = alert["event"]
        assert event["time"] == "2019-08-14T21:10:39.012957Z"
        assert event["far"] == pytest.approx(2.033e-33)
        assert event["significant"] is True
        assert event["instruments"] == ["H1", "L1", "V1"]
        assert event["group"] == "CBC"
        assert event["pipeline"] == "gstlal"
        assert event["search"] == "AllSky"
        assert event["properties"]["HasNS"] == pytest.approx(1.0)
        assert event["classification"]["NSBH"] == pytest.approx(1.0)

        assert skymap_url.endswith("/files/LALInference.v1.fits.gz")

    def test_retraction_has_no_event_or_skymap(self):
        alert, skymap_url = parse_voevent_xml(SAMPLE_RETRACTION_XML)
        assert alert["alert_type"] == "RETRACTION"
        assert alert["event"] is None
        assert skymap_url is None


class TestFetchGracedbAlert:
    def _make_session(self, files, voevent_xml, skymap_bytes):
        """A fake requests.Session whose .get dispatches by URL substring."""
        def fake_get(url, timeout=None):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if url.endswith("/files/"):
                resp.json.return_value = files
            elif url.endswith(".xml"):
                resp.content = voevent_xml
            else:
                resp.content = skymap_bytes
            return resp

        session = MagicMock()
        session.get.side_effect = fake_get
        return session

    def test_fetches_latest_voevent_and_skymap(self):
        files = {
            "S190814bv-4-Update.xml": "https://gracedb.test/api/.../S190814bv-4-Update.xml",
            "S190814bv-5-Update.xml": "https://gracedb.test/api/.../S190814bv-5-Update.xml",
        }
        session = self._make_session(files, SAMPLE_VOEVENT_XML, b"raw-fits-bytes")

        alert, skymap_bytes, voevent_name = fetch_gracedb_alert(
            "S190814bv", base_url="https://gracedb.test", session=session
        )

        assert voevent_name == "S190814bv-5-Update.xml"
        assert alert["superevent_id"] == "S190814bv"
        assert skymap_bytes == b"raw-fits-bytes"

    def test_retraction_returns_no_skymap(self):
        files = {"S190814bv-6-Retraction.xml": "https://gracedb.test/x/S190814bv-6-Retraction.xml"}
        session = self._make_session(files, SAMPLE_RETRACTION_XML, b"unused")

        alert, skymap_bytes, voevent_name = fetch_gracedb_alert(
            "S190814bv", base_url="https://gracedb.test", session=session
        )

        assert voevent_name == "S190814bv-6-Retraction.xml"
        assert skymap_bytes is None


class TestIngestLocalNleGracedbSource:
    """The --gracedb mode builds the alert from GraceDB instead of local files."""

    @patch(f"{_CMD}.connection")
    @patch(f"{_CMD}.ingest_local_igwn_alert")
    @patch(f"{_CMD}.ensure_multiorder_skymap_bytes", return_value=b"moc-fits")
    @patch(f"{_CMD}.fetch_gracedb_alert")
    @patch(f"{_CMD}.NonLocalizedEvent")
    def test_gracedb_source_ingests(
        self, mock_nle, mock_fetch, mock_convert, mock_ingest, mock_conn
    ):
        alert = {"superevent_id": "S190814bv", "alert_type": "UPDATE", "event": {}}
        mock_fetch.return_value = (alert, b"raw-fits", "S190814bv-5-Update.xml")
        mock_nle.objects.filter.return_value.first.return_value = None
        mock_ingest.return_value = (MagicMock(event_id="S190814bv"), MagicMock())

        call_command("ingest_local_nle", "--gracedb", "S190814bv", "--yes")

        mock_fetch.assert_called_once()
        assert mock_fetch.call_args.args[0] == "S190814bv"
        mock_ingest.assert_called_once()
        ingested_alert = mock_ingest.call_args.args[0]
        assert ingested_alert["event"]["skymap"] == b"moc-fits"

    def test_gracedb_and_positionals_are_mutually_exclusive(self):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command(
                "ingest_local_nle",
                str(GW170817_JSON),
                "--gracedb",
                "S190814bv",
            )

    def test_requires_a_source(self):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("ingest_local_nle")


class TestIterGracedbSuperevents:
    def _paged_session(self, pages):
        """Fake session whose .get returns successive page dicts (by ?start=)."""
        def fake_get(url, params=None, timeout=None):
            start = (params or {}).get("start", 0)
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = pages[start]
            return resp

        session = MagicMock()
        session.get.side_effect = fake_get
        return session

    def test_follows_pagination(self):
        pages = {
            0: {"numRows": 3, "superevents": [{"superevent_id": "S1"}, {"superevent_id": "S2"}]},
            2: {"numRows": 3, "superevents": [{"superevent_id": "S3"}]},
        }
        session = self._paged_session(pages)
        ids = [s["superevent_id"] for s in iter_gracedb_superevents(
            "category: Production", session=session, page_size=2
        )]
        assert ids == ["S1", "S2", "S3"]

    def test_respects_limit(self):
        pages = {
            0: {"numRows": 10, "superevents": [{"superevent_id": f"S{i}"} for i in range(5)]},
        }
        session = self._paged_session(pages)
        ids = [s["superevent_id"] for s in iter_gracedb_superevents(
            "q", session=session, page_size=5, limit=2
        )]
        assert ids == ["S0", "S1"]


class TestIngestLocalNleGracedbAll:
    """--gracedb-all enumerates events, skips existing ones, and ingests the rest."""

    @patch(f"{_CMD}.connection")
    @patch(f"{_CMD}.ingest_local_igwn_alert")
    @patch(f"{_CMD}.ensure_multiorder_skymap_bytes", return_value=b"moc-fits")
    @patch(f"{_CMD}.fetch_gracedb_alert")
    @patch(f"{_CMD}.iter_gracedb_superevents")
    @patch(f"{_CMD}.NonLocalizedEvent")
    def test_skips_existing_and_ingests_new(
        self, mock_nle, mock_iter, mock_fetch, mock_convert, mock_ingest, mock_conn
    ):
        mock_iter.return_value = [
            {"superevent_id": "S_existing"},
            {"superevent_id": "S_new"},
        ]

        # S_existing already in DB; S_new is not.
        def filter_side_effect(event_id):
            result = MagicMock()
            result.first.return_value = MagicMock() if event_id == "S_existing" else None
            return result

        mock_nle.objects.filter.side_effect = filter_side_effect
        mock_fetch.return_value = (
            {"superevent_id": "S_new", "event": {}},
            b"raw-fits",
            "S_new-1-Preliminary.xml",
        )
        mock_ingest.return_value = (MagicMock(event_id="S_new"), MagicMock())

        call_command("ingest_local_nle", "--gracedb-all", "--yes")

        # Only the new event is fetched and ingested.
        mock_fetch.assert_called_once()
        assert mock_fetch.call_args.args[0] == "S_new"
        mock_ingest.assert_called_once()

    @patch(f"{_CMD}.connection")
    @patch(f"{_CMD}.ingest_local_igwn_alert")
    @patch(f"{_CMD}.fetch_gracedb_alert")
    @patch(f"{_CMD}.iter_gracedb_superevents")
    @patch(f"{_CMD}.NonLocalizedEvent")
    def test_dry_run_does_not_fetch_or_ingest(
        self, mock_nle, mock_iter, mock_fetch, mock_ingest, mock_conn
    ):
        mock_iter.return_value = [{"superevent_id": "S_new"}]
        mock_nle.objects.filter.return_value.first.return_value = None

        call_command("ingest_local_nle", "--gracedb-all", "--dry-run")

        mock_fetch.assert_not_called()
        mock_ingest.assert_not_called()

    @patch(f"{_CMD}.connection")
    @patch(f"{_CMD}.iter_gracedb_superevents")
    def test_default_query_is_significant(self, mock_iter, mock_conn):
        from custom_code.nle_ingestion import GRACEDB_SIGNIFICANT_QUERY

        mock_iter.return_value = []
        call_command("ingest_local_nle", "--gracedb-all", "--dry-run")

        assert mock_iter.call_args.args[0] == GRACEDB_SIGNIFICANT_QUERY

    @patch(f"{_CMD}.connection")
    @patch(f"{_CMD}.iter_gracedb_superevents")
    def test_include_low_significance_uses_production_query(self, mock_iter, mock_conn):
        from custom_code.nle_ingestion import GRACEDB_PRODUCTION_QUERY

        mock_iter.return_value = []
        call_command(
            "ingest_local_nle",
            "--gracedb-all",
            "--include-low-significance",
            "--dry-run",
        )

        assert mock_iter.call_args.args[0] == GRACEDB_PRODUCTION_QUERY

    def test_gracedb_all_rejects_other_sources(self):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("ingest_local_nle", "--gracedb-all", "--gracedb", "S190814bv")
