"""
Unit tests for template tags and filters.

These test the pure logic functions in trove_targets/templatetags/ and trove_nonlocalizedevents/templatetags/.
"""
import pytest
import numpy as np
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


class TestNonLocalizedEventExtras:
    """Tests for trove_nonlocalizedevents/templatetags/nonlocalizedevent_extras.py"""

    def test_format_inverse_far_yr_none(self):
        """Test format_inverse_far_yr with None input."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import format_inverse_far_yr
        assert format_inverse_far_yr(None) == ''
        assert format_inverse_far_yr(0) == ''

    def test_format_inverse_far_yr_large_value(self):
        """Test format_inverse_far_yr with large 1/FAR values (many years)."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import format_inverse_far_yr
        result = format_inverse_far_yr(1e-4)
        assert 'yr' in result
        assert 'kyr' in result or float(result.split()[0]) > 1000

    def test_format_inverse_far_yr_medium_value(self):
        """Test format_inverse_far_yr with medium 1/FAR values."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import format_inverse_far_yr
        result = format_inverse_far_yr(0.1)
        assert 'yr' in result

    def test_format_inverse_far_yr_small_value(self):
        """Test format_inverse_far_yr with small 1/FAR values (days)."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import format_inverse_far_yr
        result = format_inverse_far_yr(10.0)
        assert 'd' in result

    def test_format_inverse_far_none(self):
        """Test format_inverse_far with None input."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import format_inverse_far
        assert format_inverse_far(None) == ''

    def test_format_inverse_far_conversion(self):
        """Test that format_inverse_far correctly converts Hz to years."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import format_inverse_far
        far_hz = 3.168808781402895e-08
        result = format_inverse_far(far_hz)
        assert 'yr' in result or 'd' in result

    def test_format_distance_none(self):
        """Test format_distance with None localization."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import format_distance
        assert format_distance(None) == ''

    def test_format_distance_no_distance_mean(self):
        """Test format_distance when distance_mean is None."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import format_distance
        loc = MagicMock()
        loc.distance_mean = None
        assert format_distance(loc) == ''

    def test_format_distance_mpc(self):
        """Test format_distance with distance in Mpc range."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import format_distance
        loc = MagicMock()
        loc.distance_mean = 500.0
        loc.distance_std = 50.0
        result = format_distance(loc)
        assert 'Mpc' in result
        assert '500' in result

    def test_format_distance_gpc(self):
        """Test format_distance with distance in Gpc range."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import format_distance
        loc = MagicMock()
        loc.distance_mean = 2000.0
        loc.distance_std = 200.0
        result = format_distance(loc)
        assert 'Gpc' in result

    def test_format_area_degrees(self):
        """Test format_area with area in degrees."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import format_area
        result = format_area(100.0)
        assert 'deg²' in result
        assert '100' in result

    def test_format_area_arcmin(self):
        """Test format_area with area in arcmin range."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import format_area
        result = format_area(0.1)
        assert 'arcmin²' in result

    def test_format_area_arcsec(self):
        """Test format_area with area in arcsec range."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import format_area
        result = format_area(0.0001)
        assert 'arcsec²' in result

    def test_get_most_likely_class_none(self):
        """Test get_most_likely_class with None details."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import get_most_likely_class
        assert get_most_likely_class(None) is None

    def test_get_most_likely_class_ssm(self):
        """Test get_most_likely_class with SSM search."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import get_most_likely_class
        details = {'search': 'SSM', 'group': 'CBC'}
        assert get_most_likely_class(details) == 'SSM'

    def test_get_most_likely_class_cbc(self):
        """Test get_most_likely_class with CBC group."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import get_most_likely_class
        details = {
            'search': 'AllSky',
            'group': 'CBC',
            'classification': {'BNS': 0.8, 'NSBH': 0.1, 'BBH': 0.05, 'Terrestrial': 0.05}
        }
        assert get_most_likely_class(details) == 'BNS'

    def test_get_most_likely_class_burst(self):
        """Test get_most_likely_class with Burst group."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import get_most_likely_class
        details = {'search': 'AllSky', 'group': 'Burst'}
        assert get_most_likely_class(details) == 'Burst'

    def test_percentformat_valid(self):
        """Test percentformat with valid input."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import percentformat
        assert percentformat(0.5) == '50%'
        assert percentformat(0.123, 1) == '12.3%'
        assert percentformat(0.9876, 2) == '98.76%'

    def test_percentformat_invalid(self):
        """Test percentformat with invalid input."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import percentformat
        assert percentformat('invalid') == 'invalid'

    def test_millisecondformat_valid(self):
        """Test millisecondformat with valid input."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import millisecondformat
        assert millisecondformat(0.001) == '1 ms'
        assert millisecondformat(0.0015, 1) == '1.5 ms'

    def test_millisecondformat_invalid(self):
        """Test millisecondformat with invalid input - raises TypeError."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import millisecondformat
        try:
            result = millisecondformat('invalid')
            assert result == 'invalid'
        except (TypeError, ValueError):
            pass

    def test_truncate_short_string(self):
        """Test truncate with string shorter than limit."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import truncate
        assert truncate('test', 5) == 'test'

    def test_truncate_long_string(self):
        """Test truncate with string longer than limit."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import truncate
        assert truncate('testing', 5) == 'test.'

    def test_truncate_exact_length(self):
        """Test truncate with string exactly at limit."""
        from trove_nonlocalizedevents.templatetags.nonlocalizedevent_extras import truncate
        assert truncate('tests', 5) == 'tests'


class TestPhotometryExtras:
    """Tests for trove_targets/templatetags/photometry_extras.py"""

    def test_format_mag_with_error(self):
        """Test format_mag with magnitude and error."""
        from trove_targets.templatetags.photometry_extras import format_mag
        datum = {'magnitude': 18.5, 'error': 0.1}
        result = format_mag(datum)
        assert '18.50' in result
        assert '±' in result
        assert '0.10' in result

    def test_format_mag_with_limit(self):
        """Test format_mag with magnitude and limit flag."""
        from trove_targets.templatetags.photometry_extras import format_mag
        datum = {'magnitude': 20.0, 'limit': True}
        result = format_mag(datum)
        assert '>' in result
        assert '20.00' in result

    def test_format_mag_magnitude_only(self):
        """Test format_mag with magnitude only."""
        from trove_targets.templatetags.photometry_extras import format_mag
        datum = {'magnitude': 19.0}
        result = format_mag(datum)
        assert '19.00' in result
        assert '±' not in result

    def test_format_mag_no_magnitude(self):
        """Test format_mag without magnitude."""
        from trove_targets.templatetags.photometry_extras import format_mag
        datum = {'limit': 21.0}
        result = format_mag(datum)
        assert result is None

    def test_format_mag_precision(self):
        """Test format_mag with different precision."""
        from trove_targets.templatetags.photometry_extras import format_mag
        datum = {'magnitude': 18.567, 'error': 0.123}
        result = format_mag(datum, d=3)
        assert '18.567' in result
        assert '0.123' in result

    def test_error_to_snr(self):
        """Test error_to_snr conversion."""
        from trove_targets.templatetags.photometry_extras import error_to_snr
        error = 0.1
        snr = error_to_snr(error)
        expected = 2.5 / np.log(10.) / error
        assert abs(snr - expected) < 1e-10

    def test_error_to_snr_small_error(self):
        """Test error_to_snr with small error (high SNR)."""
        from trove_targets.templatetags.photometry_extras import error_to_snr
        error = 0.01
        snr = error_to_snr(error)
        assert snr > 100


class TestTargetExtras:
    """Tests for trove_targets/templatetags/target_extras.py"""

    def test_ecliptic_lng(self, mock_target):
        """Test ecliptic longitude calculation."""
        from trove_targets.templatetags.target_extras import ecliptic_lng
        result = ecliptic_lng(mock_target)
        assert isinstance(result, float)
        assert -180 <= result <= 360

    def test_ecliptic_lat(self, mock_target):
        """Test ecliptic latitude calculation."""
        from trove_targets.templatetags.target_extras import ecliptic_lat
        result = ecliptic_lat(mock_target)
        assert isinstance(result, float)
        assert -90 <= result <= 90

    def test_split_name_with_prefix(self):
        """Test split_name with standard prefix."""
        from trove_targets.templatetags.target_extras import split_name
        result = split_name('AT2024abc')
        assert result['prefix'] == 'AT'
        assert result['basename'] == '2024abc'
        assert result['tns_objname'] == '2024abc'

    def test_split_name_sn_prefix(self):
        """Test split_name with SN prefix."""
        from trove_targets.templatetags.target_extras import split_name
        result = split_name('SN2024xyz')
        assert result['prefix'] == 'SN'
        assert result['basename'] == '2024xyz'
        assert result['tns_objname'] == '2024xyz'

    def test_split_name_no_prefix(self):
        """Test split_name without recognized prefix."""
        from trove_targets.templatetags.target_extras import split_name
        result = split_name('ZTF24aabbcc')
        assert result['prefix'] == 'ZTF'
        assert result['basename'] == '24aabbcc'
        assert result['tns_objname'] is None

    def test_split_name_numbers_only(self):
        """Test split_name with numbers only."""
        from trove_targets.templatetags.target_extras import split_name
        result = split_name('12345')
        assert result['prefix'] == ''
        assert result['basename'] == '12345'


class TestSkymapExtras:
    """Tests for trove_nonlocalizedevents/templatetags/skymap_extras.py"""

    def test_get_preferred_localization_with_sequence(self):
        """Test get_preferred_localization with valid sequence."""
        from trove_nonlocalizedevents.templatetags.skymap_extras import get_preferred_localization
        nle = MagicMock()
        seq = MagicMock()
        seq.localization = MagicMock()
        seq.external_coincidence = None
        nle.sequences.last.return_value = seq
        
        result = get_preferred_localization(nle)
        assert result == seq.localization

    def test_get_preferred_localization_with_external_coincidence(self):
        """Test get_preferred_localization with external coincidence."""
        from trove_nonlocalizedevents.templatetags.skymap_extras import get_preferred_localization
        nle = MagicMock()
        seq = MagicMock()
        external_loc = MagicMock()
        seq.external_coincidence = MagicMock()
        seq.external_coincidence.localization = external_loc
        nle.sequences.last.return_value = seq
        
        result = get_preferred_localization(nle)
        assert result == external_loc

    def test_get_preferred_localization_no_sequence(self):
        """Test get_preferred_localization with no sequence."""
        from trove_nonlocalizedevents.templatetags.skymap_extras import get_preferred_localization
        nle = MagicMock()
        nle.sequences.last.return_value = None
        
        result = get_preferred_localization(nle)
        assert result is None

    def test_secondslater(self):
        """Test secondslater filter."""
        from trove_nonlocalizedevents.templatetags.skymap_extras import secondslater
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = secondslater(dt, 3600)
        expected = dt + timedelta(seconds=3600)
        assert result == expected

    def test_secondslater_negative(self):
        """Test secondslater with negative seconds."""
        from trove_nonlocalizedevents.templatetags.skymap_extras import secondslater
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = secondslater(dt, -1800)
        expected = dt + timedelta(seconds=-1800)
        assert result == expected


class TestTargetListExtras:
    """Tests for trove_targets/templatetags/target_list_extras.py"""

    def test_islist_with_list(self):
        """Test islist with Python list."""
        from trove_targets.templatetags.target_list_extras import islist
        assert islist([1, 2, 3]) is True

    def test_islist_with_numpy_array(self):
        """Test islist with numpy array."""
        from trove_targets.templatetags.target_list_extras import islist
        assert islist(np.array([1, 2, 3])) is True

    def test_islist_with_string(self):
        """Test islist with string."""
        from trove_targets.templatetags.target_list_extras import islist
        assert islist('test') is False

    def test_islist_with_dict(self):
        """Test islist with dict."""
        from trove_targets.templatetags.target_list_extras import islist
        assert islist({'a': 1}) is False

    def test_islist_with_tuple(self):
        """Test islist with tuple (should return False)."""
        from trove_targets.templatetags.target_list_extras import islist
        assert islist((1, 2, 3)) is False

    def test_islist_with_none(self):
        """Test islist with None."""
        from trove_targets.templatetags.target_list_extras import islist
        assert islist(None) is False

    def test_format_redshift_parts_coarse_uncertainty(self):
        from trove_targets.templatetags.target_list_extras import format_redshift_parts

        parts = format_redshift_parts(0.04, 0.01)
        assert parts == {"z": "0.04", "err": "0.01"}

    def test_format_redshift_parts_fine_uncertainty_1e4(self):
        from trove_targets.templatetags.target_list_extras import format_redshift_parts

        parts = format_redshift_parts(0.039214, 0.0001)
        assert parts == {"z": "0.03921", "err": "0.0001"}

    def test_format_redshift_parts_fine_uncertainty_1e5(self):
        from trove_targets.templatetags.target_list_extras import format_redshift_parts

        parts = format_redshift_parts(0.039214, 0.00001)
        assert parts == {"z": "0.039214", "err": "0.00001"}

    def test_format_redshift_parts_no_error(self):
        from trove_targets.templatetags.target_list_extras import format_redshift_parts

        parts = format_redshift_parts(0.04, float("nan"))
        assert parts == {"z": "0.04", "no_err": True}

    def test_format_redshift_parts_asymmetric(self):
        from trove_targets.templatetags.target_list_extras import format_redshift_parts

        parts = format_redshift_parts(0.039214, [0.00008, 0.00012])
        assert parts["z"] == "0.03921"
        assert parts["neg"] == "0.00008"
        assert parts["pos"] == "0.0001"

    def test_redshift_cell_symmetric(self):
        from trove_targets.templatetags.target_list_extras import redshift_cell

        html = redshift_cell(0.039214, 0.0001)
        assert "0.03921" in html
        assert "0.0001" in html
        assert "&plusmn;" in html


class TestEventCandidateExtras:
    """Tests for trove_nonlocalizedevents/templatetags/event_candidate_extras.py"""

    def test_float_format_default(self):
        """Test _float_format with default unit."""
        from trove_nonlocalizedevents.templatetags.event_candidate_extras import _float_format
        assert _float_format(3.14159) == '3.14 '

    def test_float_format_with_unit(self):
        """Test _float_format with specified unit."""
        from trove_nonlocalizedevents.templatetags.event_candidate_extras import _float_format
        assert _float_format(3.14159, 'mag/day') == '3.14 mag/day'

    def test_sci_format_positive_exponent(self):
        """Test _sci_format with positive exponent."""
        from trove_nonlocalizedevents.templatetags.event_candidate_extras import _sci_format
        result = _sci_format(1.5e10, 'erg/s')
        assert '1.50' in result
        assert '10<sup>10</sup>' in result
        assert 'erg/s' in result

    def test_sci_format_negative_exponent(self):
        """Test _sci_format with negative exponent."""
        from trove_nonlocalizedevents.templatetags.event_candidate_extras import _sci_format
        result = _sci_format(1.5e-5, 'cm')
        assert '1.50' in result
        assert '10<sup>' in result
        assert '-' in result

    def test_bool_format_yesno_true(self):
        """Test _bool_format with truthy value."""
        from trove_nonlocalizedevents.templatetags.event_candidate_extras import _bool_format_yesno
        assert _bool_format_yesno(1.0) == "No"

    def test_bool_format_yesno_false(self):
        """Test _bool_format with falsy value."""
        from trove_nonlocalizedevents.templatetags.event_candidate_extras import _bool_format_yesno
        assert _bool_format_yesno(0.0) == "Yes"

    def test_bool_format_yesno_fractional(self):
        """Test _bool_format with fractional value."""
        from trove_nonlocalizedevents.templatetags.event_candidate_extras import _bool_format_yesno
        assert _bool_format_yesno(0.7) == "No"


class TestAssociatedAgn:
    """Tests for the "Associated AGN" display on the target page."""

    AGN_JSON = (
        '[{"ID": "far", "RA": 150.0, "Dec": 30.001, "Dist": null, "DistErr": null,'
        ' "z": 0.1, "zErr": null, "Source": "Milliquas"},'
        ' {"ID": "near", "RA": 150.0, "Dec": 30.0001, "Dist": 400.0, "DistErr": 20.0,'
        ' "z": 0.09, "zErr": 0.001, "Source": "DesiDr1Quasar"}]'
    )

    def _patch_te(self, module, value):
        te = None if value is None else MagicMock(value=value)
        mock_te = patch(f"{module}.TargetExtra")
        m = mock_te.start()
        m.objects.filter.return_value.first.return_value = te
        return mock_te

    def test_load_associated_agn_sorted_with_offset(self, mock_target):
        from trove_targets.templatetags.target_list_extras import load_associated_agn
        p = self._patch_te("trove_targets.templatetags.target_list_extras", self.AGN_JSON)
        try:
            agns = load_associated_agn(mock_target)
        finally:
            p.stop()
        assert [a["ID"] for a in agns] == ["near", "far"]
        assert agns[0]["Offset"] == pytest.approx(0.36, abs=0.01)
        assert agns[1]["Offset"] == pytest.approx(3.6, abs=0.01)

    def test_load_associated_agn_blanks_sentinel_redshift(self, mock_target):
        from trove_targets.templatetags.target_list_extras import load_associated_agn
        value = ('[{"ID": "pso", "RA": 150.0, "Dec": 30.0, "Dist": null, "DistErr": null,'
                 ' "z": -999.0, "zErr": 1000.0, "Source": "Ps1Qso"}]')
        p = self._patch_te("trove_targets.templatetags.target_list_extras", value)
        try:
            agn, = load_associated_agn(mock_target)
        finally:
            p.stop()
        assert agn["z"] is None and agn["zErr"] is None

    @pytest.mark.parametrize("value", [None, "[]"])
    def test_load_associated_agn_empty(self, mock_target, value):
        from trove_targets.templatetags.target_list_extras import load_associated_agn
        p = self._patch_te("trove_targets.templatetags.target_list_extras", value)
        try:
            assert load_associated_agn(mock_target) == []
        finally:
            p.stop()

    def test_agn_table_renders(self, mock_target):
        from django.template.loader import render_to_string
        from trove_targets.templatetags.target_list_extras import load_associated_agn
        p = self._patch_te("trove_targets.templatetags.target_list_extras", self.AGN_JSON)
        try:
            html = render_to_string("tom_targets/partials/agn_table.html",
                                    {"agns": load_associated_agn(mock_target)})
        finally:
            p.stop()
        assert "near" in html and "DesiDr1Quasar" in html
        assert "400.0&nbsp;&plusmn;&nbsp;20.0" in html

    @pytest.mark.parametrize("value,expected", [(AGN_JSON, True), ("[]", False), (None, False)])
    def test_has_associated_agn(self, value, expected):
        from trove_nonlocalizedevents.templatetags.event_candidate_extras import _has_associated_agn
        p = self._patch_te("trove_nonlocalizedevents.templatetags.event_candidate_extras", value)
        try:
            assert _has_associated_agn(1) is expected
        finally:
            p.stop()


class TestLightCurveSummary:
    """Photometry-tab Light Curve Summary box"""

    def _ctx(self, metrics=None, side_effect=None):
        from trove_nonlocalizedevents.templatetags import event_candidate_extras as ece
        with patch.object(ece, "_basic_light_curve_metrics_cached",
                          return_value=metrics, side_effect=side_effect):
            return ece.light_curve_summary(MagicMock())

    def _rows(self, metrics):
        return {r["label"]: r["value"] for r in self._ctx(metrics)["rows"]}

    def test_all_rows(self):
        rows = self._rows(dict(t0=60000, t0_is_fallback=False, lum=2e42,
                               peak_time=4.0, decay_rate=0.05))
        assert rows == {
            "Transient Start (t<sub>0</sub>)":
                "MJD 60000.00 (2023-02-25)<br>midpoint of the last non-detection and the first detection",
            "Maximum Luminosity": "2.00 x 10<sup>42</sup> erg/s",
            "Rise Time (t<sub>0</sub> to peak)": "4.00 days",
            "Light Curve Slope": "0.05 mag/day",
        }

    def test_missing_values_are_left_out_and_fallback_is_noted(self):
        rows = self._rows(dict(t0=60000, t0_is_fallback=True, lum=None,
                               peak_time=4.0, decay_rate=None))
        assert list(rows) == ["Transient Start (t<sub>0</sub>)", "Rise Time (t<sub>0</sub> to peak)"]
        assert "no non-detection before the first detection" in rows["Transient Start (t<sub>0</sub>)"]

    def test_no_detections(self):
        assert self._ctx(None) == {"rows": []}

    def test_errors_do_not_break_the_page(self):
        assert self._ctx(side_effect=RuntimeError) == {"rows": []}

    def test_render_says_event_independent(self):
        from django.template.loader import render_to_string
        ctx = self._ctx(dict(t0=60000, t0_is_fallback=False, lum=None,
                             peak_time=4.0, decay_rate=0.05))
        html = render_to_string("tom_targets/partials/light_curve_summary.html", ctx)
        assert "Light Curve Summary" in html
        assert "Independent of any associated GW or other poorly localized event" in html
        assert "<sub>0</sub>" in html  # labels are not escaped

    def test_render_nothing_without_rows(self):
        from django.template.loader import render_to_string
        html = render_to_string("tom_targets/partials/light_curve_summary.html", {"rows": []})
        assert html.strip() == ""
