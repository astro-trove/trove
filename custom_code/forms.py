from django import forms
from django.forms import inlineformset_factory
from tom_targets.models import TargetList
from .models import TargetListExtra
from datetime import datetime
import json


TargetListExtraFormset = inlineformset_factory(TargetList, TargetListExtra, fields=('key', 'value'),
                                               widgets={'value': forms.TextInput()})

TNS_FILTER_CHOICES = [
    (0, "Other"),
    (1, "Clear"),
    (10, "U-Johnson"),
    (11, "B-Johnson"),
    (12, "V-Johnson"),
    (13, "R-Cousins"),
    (14, "I-Cousins"),
    (15, "J-Bessel"),
    (16, "H-Bessel"),
    (17, "K-Bessel"),
    (18, "L"),
    (19, "M"),
    (20, "u-Sloan"),
    (21, "g-Sloan"),
    (22, "r-Sloan"),
    (23, "i-Sloan"),
    (24, "z-Sloan"),
    (25, "y-P1"),
    (26, "w-P1"),
]

TNS_INSTRUMENT_CHOICES = [
    (0, "Other"),
]


class TargetReportForm(forms.Form):
    ra = forms.FloatField()
    dec = forms.FloatField()
    reporting_group_id = forms.ChoiceField(choices=[
        (66, "SAGUARO"),
    ], initial=(66, "SAGUARO"))
    discovery_data_source_id = forms.ChoiceField(choices=[
        (66, "SAGUARO"),
    ], initial=(66, "SAGUARO"))
    reporter = forms.CharField()
    discovery_datetime = forms.DateTimeField(initial=datetime.utcnow())
    at_type = forms.ChoiceField(choices=[
        (0, "Other"),
        (1, "PSN"),
        (2, "PNV"),
        (3, "AGN"),
        (4, "NUC"),
        (5, "FRB"),
    ], initial=(1, "PSN"))
    non_detection__archiveid = forms.ChoiceField(choices=[
        (0, "Other"),
        (1, "SDSS"),
        (2, "DSS"),
    ], initial=(0, "Other"))
    non_detection__archival_remarks = forms.CharField(initial="BASS")
    obsdate = forms.DateTimeField()
    flux = forms.FloatField()
    flux_error = forms.FloatField()
    flux_units = forms.ChoiceField(choices=[
        (0, "Other"),
        (1, "ABMag"),
        (2, "STMag"),
        (3, "VegaMag"),
        (4, "erg cm(-2) sec(-1)"),
        (5, "erg cm(-2) sec(-1) Hz(-1)"),
        (6, "erg cm(-2) sec(-1) Ang(-1)"),
        (7, "counts sec(-1)"),
        (8, "Jy"),
        (9, "mJy"),
        (10, "Neutrino events"),
        (33, "Photons sec(-1) cm(-2)"),
    ], initial=(1, "ABMag"))
    filter_value = forms.ChoiceField(choices=TNS_FILTER_CHOICES, initial=(22, "r-Sloan"))
    instrument_value = forms.ChoiceField(choices=TNS_INSTRUMENT_CHOICES, initial=(0, "Other"))
    limiting_flux = forms.FloatField(required=False)
    exptime = forms.FloatField(required=False)
    observer = forms.CharField(required=False)
    comments = forms.CharField(required=False)

    def generate_tns_report(self):
        """
        Generate TNS bulk transient report according to the schema in this manual:
        https://sandbox.wis-tns.org/sites/default/files/api/TNS_bulk_reports_manual.pdf

        Returns the report as a JSON-formatted string
        """
        report_data = {
            "at_report": {
                "0": {
                    "ra": {
                        "value": self.cleaned_data['ra'],
                    },
                    "dec": {
                        "value": self.cleaned_data['dec'],
                    },
                    "reporting_group_id": self.cleaned_data['reporting_group_id'],
                    "discovery_data_source_id": self.cleaned_data['discovery_data_source_id'],
                    "reporter": self.cleaned_data['reporter'],
                    "discovery_datetime": self.cleaned_data['discovery_datetime'].strftime('%Y-%m-%d %H:%M:%S'),
                    "at_type": self.cleaned_data['at_type'],
                    "non_detection": {
                        "archiveid": self.cleaned_data['non_detection__archiveid'],
                        "archival_remarks": self.cleaned_data['non_detection__archival_remarks'],
                    },
                    "photometry": {
                        "photometry_group": {
                            "0": {
                                "obsdate": self.cleaned_data['obsdate'].strftime('%Y-%m-%d %H:%M:%S'),
                                "flux": self.cleaned_data['flux'],
                                "flux_error": self.cleaned_data['flux_error'],
                                "flux_units": self.cleaned_data['flux_units'],
                                "filter_value": self.cleaned_data['filter_value'],
                                "instrument_value": self.cleaned_data['instrument_value'],
                                "limiting_flux": self.cleaned_data['limiting_flux'],
                                "exptime": self.cleaned_data['exptime'],
                                "observer": self.cleaned_data['observer'],
                                "comments": self.cleaned_data['comments'],
                            },
                        }
                    },
                }
            }
        }
        return json.dumps(report_data)
