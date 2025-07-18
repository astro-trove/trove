from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Submit, HTML
from crispy_forms.bootstrap import AppendedText, PrependedAppendedText
from datetime import datetime
import json
import os


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

TNS_CLASSIFICATION_CHOICES = [
    (0, "Other"),
    (1, "SN"),
    (2, "SN I"),
    (3, "SN Ia"),
    (4, "SN Ib"),
    (5, "SN Ic"),
    (6, "SN Ib/c"),
    (7, "SN Ic-BL"),
    (9, "SN Ibn"),
    (10, "SN II"),
    (11, "SN IIP"),
    (12, "SN IIL"),
    (13, "SN IIn"),
    (14, "SN IIb"),
    (15, "SN I-faint"),
    (16, "SN I-rapid"),
    (18, "SLSN-I"),
    (19, "SLSN-II"),
    (20, "SLSN-R"),
    (23, "Afterglow"),
    (24, "LBV"),
    (25, "ILRT"),
    (26, "Nova"),
    (27, "CV"),
    (28, "Varstar"),
    (29, "AGN"),
    (30, "Galaxy"),
    (31, "QSO"),
    (40, "Light-Echo"),
    (50, "Std-spec"),
    (60, "Gap"),
    (61, "Gap I"),
    (62, "Gap II"),
    (65, "LRN"),
    (66, "FBOT"),
    (70, "Kilonova"),
    (99, "Impostor-SN"),
    (100, "SN Ia-pec"),
    (102, "SN Ia-SC"),
    (103, "SN Ia-91bg-like"),
    (104, "SN Ia-91T-like"),
    (105, "SN Iax[02cx-like]"),
    (106, "SN Ia-CSM"),
    (107, "SN Ib-pec"),
    (108, "SN Ic-pec"),
    (109, "SN Icn"),
    (110, "SN Ibn/Icn"),
    (111, "SN II-pec"),
    (112, "SN IIn-pec"),
    (115, "SN Ib-Ca-rich"),
    (116, "SN Ib/c-Ca-rich"),
    (117, "SN Ic-Ca-rich"),
    (118, "SN Ia-Ca-rich"),
    (120, "TDE"),
    (121, "TDE-H"),
    (122, "TDE-He"),
    (123, "TDE-H-He"),
    (200, "WR"),
    (201, "WR-WN"),
    (202, "WR-RC"),
    (203, "WR-WO"),
    (210, "M dwarf"),
]


class TargetReportForm(forms.Form):
    ra = forms.FloatField(label='R.A.')
    dec = forms.FloatField(label='Dec.')
    reporting_group = forms.ChoiceField(choices=[
        (66, "SAGUARO"),
    ], initial=(66, "SAGUARO"))
    discovery_data_source = forms.ChoiceField(choices=[
        (66, "SAGUARO"),
    ], initial=(66, "SAGUARO"))
    reporter = forms.CharField(widget=forms.Textarea(attrs={'rows': 1}))
    discovery_date = forms.DateTimeField(initial=datetime.utcnow())
    at_type = forms.ChoiceField(choices=[
        (0, "Other"),
        (1, "PSN"),
        (2, "PNV"),
        (3, "AGN"),
        (4, "NUC"),
        (5, "FRB"),
    ], initial=(1, "PSN"), label='AT type')
    archive = forms.ChoiceField(choices=[
        (0, "Other"),
        (1, "SDSS"),
        (2, "DSS"),
    ], initial=(0, "Other"))
    archival_remarks = forms.CharField(initial="CSS")
    observation_date = forms.DateTimeField()
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
    filter = forms.ChoiceField(choices=TNS_FILTER_CHOICES, initial=(22, "r-Sloan"))
    instrument = forms.ChoiceField(choices=TNS_INSTRUMENT_CHOICES, initial=(0, "Other"))
    limiting_flux = forms.FloatField(required=False)
    exposure_time = forms.FloatField(required=False)
    observer = forms.CharField(required=False)
    discovery_remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 1}))
    photometry_remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 1}))
    nondetection_remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 1}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.layout = Layout(
            Row(
                Column('reporter', css_class='col-md-6'),
                Column('reporting_group'),
                Column('discovery_data_source'),
            ),
            Row(
                Column(AppendedText('ra', 'deg')),
                Column(AppendedText('dec', 'deg')),
                Column('discovery_date'),
                Column('at_type'),
            ),
            Row(Column('discovery_remarks')),
            Row(HTML('<h4>Discovery Photometry</h4>')),
            Row(
                Column('observation_date'),
                Column('instrument'),
                Column('limiting_flux'),
                Column('observer'),
            ),
            Row(
                Column('flux'),
                Column('flux_error'),
                Column('flux_units'),
                Column('filter'),
            ),
            Row(Column('photometry_remarks')),
            Row(HTML('<h4>Last Nondetection</h4>')),
            Row(
                Column('archive'),
                Column('archival_remarks'),
                Column('nondetection_remarks', css_class='col-md-6'),
            ),
            Row(Column(Submit('submit', 'Submit Report'))),
        )

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
                    "reporting_group_id": self.cleaned_data['reporting_group'],
                    "discovery_data_source_id": self.cleaned_data['discovery_data_source'],
                    "reporter": self.cleaned_data['reporter'],
                    "discovery_datetime": self.cleaned_data['discovery_date'].strftime('%Y-%m-%d %H:%M:%S'),
                    "at_type": self.cleaned_data['at_type'],
                    "non_detection": {
                        "archiveid": self.cleaned_data['archive'],
                        "archival_remarks": self.cleaned_data['archival_remarks'],
                        "comments": self.cleaned_data['nondetection_remarks'],
                    },
                    "photometry": {
                        "photometry_group": {
                            "0": {
                                "obsdate": self.cleaned_data['observation_date'].strftime('%Y-%m-%d %H:%M:%S'),
                                "flux": self.cleaned_data['flux'],
                                "flux_error": self.cleaned_data['flux_error'],
                                "flux_units": self.cleaned_data['flux_units'],
                                "filter_value": self.cleaned_data['filter'],
                                "instrument_value": self.cleaned_data['instrument'],
                                "limiting_flux": self.cleaned_data['limiting_flux'],
                                "exptime": self.cleaned_data['exposure_time'],
                                "observer": self.cleaned_data['observer'],
                                "comments": self.cleaned_data['photometry_remarks'],
                            },
                        }
                    },
                }
            }
        }
        return json.dumps(report_data)


class TargetClassifyForm(forms.Form):
    name = forms.CharField()
    classifier = forms.CharField(widget=forms.Textarea(attrs={'rows': 1}))
    classification = forms.ChoiceField(choices=TNS_CLASSIFICATION_CHOICES, initial=(1, "SN"))
    redshift = forms.FloatField(required=False)
    group = forms.ChoiceField(choices=[
        (66, "SAGUARO"),
    ], initial=(66, "SAGUARO"))
    classification_remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 1}))
    observation_date = forms.DateTimeField()
    instrument = forms.ChoiceField(choices=TNS_INSTRUMENT_CHOICES, initial=(0, "Other"))
    exposure_time = forms.FloatField(required=False)
    observer = forms.CharField()
    reducer = forms.CharField(required=False)
    spectrum_type = forms.ChoiceField(choices=[
        (1, 'Object'),
        (2, 'Host'),
        (3, 'Sky'),
        (4, 'Arcs'),
        (5, 'Synthetic'),
    ])
    ascii_file = forms.FileField(label='ASCII file')
    fits_file = forms.FileField(label='FITS file', required=False)
    spectrum_remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 1}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.layout = Layout(
            Row(
                Column('classifier', css_class='col-md-8'),
                Column('group'),
            ),
            Row(
                Column('name'),
                Column('classification'),
                Column('redshift'),
            ),
            Row(Column('classification_remarks')),
            Row(HTML('<h4>Classification Spectrum</h4>')),
            Row(
                Column('observation_date'),
                Column('observer'),
                Column('reducer'),
            ),
            Row(
                Column('instrument'),
                Column('exposure_time'),
                Column('spectrum_type'),
            ),
            Row(
                Column('ascii_file'),
                Column('fits_file'),
            ),
            Row(Column('spectrum_remarks')),
            Row(Column(Submit('submit', 'Submit Report'))),
        )

    def files_to_upload(self):
        files_to_upload = {}
        if self.cleaned_data['ascii_file'] is not None:
            files_to_upload['files[0]'] = (
                os.path.basename(self.cleaned_data['ascii_file'].name),
                self.cleaned_data['ascii_file'].open(),
                'text/plain'
            )
        if self.cleaned_data['fits_file'] is not None:
            files_to_upload['files[1]'] = (
                os.path.basename(self.cleaned_data['fits_file'].name),
                self.cleaned_data['fits_file'].open(),
                'application/fits'
            )
        return files_to_upload

    def generate_tns_report(self, new_filenames=None):
        """
        Generate TNS bulk classification report according to the schema in this manual:
        https://sandbox.wis-tns.org/sites/default/files/api/TNS_bulk_reports_manual.pdf

        Returns the report as a JSON-formatted string
        """
        if new_filenames is None:
            ascii_filename = os.path.basename(self.cleaned_data['ascii_file'].name)
        else:
            ascii_filename = new_filenames[0]
        report_data = {
            "classification_report": {
                "0": {
                    "name": self.cleaned_data['name'],
                    "classifier": self.cleaned_data['classifier'],
                    "objtypeid": self.cleaned_data['classification'],
                    "redshift": self.cleaned_data['redshift'],
                    "groupid": self.cleaned_data['group'],
                    "remarks": self.cleaned_data['classification_remarks'],
                    "spectra": {
                        "spectra-group": {
                            "0": {
                                "obsdate": self.cleaned_data['observation_date'].strftime('%Y-%m-%d %H:%M:%S'),
                                "instrumentid": self.cleaned_data['instrument'],
                                "exptime": self.cleaned_data['exposure_time'],
                                "observer": self.cleaned_data['observer'],
                                "reducer": self.cleaned_data['reducer'],
                                "spectypeid": self.cleaned_data['spectrum_type'],
                                "ascii_file": ascii_filename,
                                "remarks": self.cleaned_data['spectrum_remarks'],
                            },
                        }
                    },
                }
            }
        }
        if self.cleaned_data['fits_file'] is not None:
            if new_filenames is None:
                fits_filename = os.path.basename(self.cleaned_data['fits_file'].name)
            else:
                fits_filename = new_filenames[1]
            report_data['classification_report']['0']['spectra']['spectra_group']['0']['fits_file'] = fits_filename
        return json.dumps(report_data)


class GWFormHelper(FormHelper):
    layout = Layout(
            Row(
                Column('prefix'),
                Column('state'),
                Column(PrependedAppendedText('inv_far_min', '>', 'yr')),
                # Column('classification'),
                Column(PrependedAppendedText('distance_max', '<', 'Mpc')),
                Column(PrependedAppendedText('has_ns_min', '>', '%')),
                Column(PrependedAppendedText('has_remnant_min', '>', '%')),
            ),
            Row(
                Column(
                    Submit('submit', 'Filter'),
                    HTML('<a href="{{ request.path }}" class="btn btn-secondary" title="Reset">Reset</a>'),
                    css_class='text-right',
                )
            )
        )


class NeutrinoFormHelper(FormHelper):
    layout = Layout(
            Row(
                Column('notice_type'),
                Column(PrependedAppendedText('inv_far_min', '>', 'yr')),
                Column(PrependedAppendedText('energy_min', '>', 'TeV')),
                Column(PrependedAppendedText('signalness_min', '>', '%')),
                Column('time'),
            ),
            Row(
                Column(
                    Submit('submit', 'Filter'),
                    HTML('<a href="{{ request.path }}" class="btn btn-secondary" title="Reset">Reset</a>'),
                    css_class='text-right',
                )
            )
        )
