from astropy import units
from astropy.time import Time, TimezoneInfo
import numpy as np
import logging
from crispy_forms.layout import Row, Column
from django import forms

from tom_dataproducts.exceptions import InvalidFileFormatException
from tom_dataproducts.forced_photometry.atlas import AtlasForcedPhotometryService
from tom_dataproducts.forced_photometry.forced_photometry_service import BaseForcedPhotometryQueryForm
from tom_dataproducts.processors.atlas_processor import AtlasProcessor
from kne_cand_vetting.survey_phot import ATLAS_stack

logger = logging.getLogger(__name__)


class CustomAtlasForcedPhotometryService(AtlasForcedPhotometryService):
    name = 'ATLAS'

    def get_form(self):
        """
        This method returns the form for querying this service.
        """
        return CustomAtlasForcedPhotometryQueryForm


class CustomAtlasForcedPhotometryQueryForm(BaseForcedPhotometryQueryForm):
    min_date = forms.CharField(help_text='Days ago (negative) or MJD or YYYY-MM-DD HH:MM:SS (time optional)')
    max_date = forms.CharField(help_text='Days ago (negative) or MJD or YYYY-MM-DD HH:MM:SS (time optional)')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['min_date'].initial = -200.
        self.fields['max_date'].initial = 0.

    def layout(self):
        return Row(Column('min_date'), Column('max_date'))

    def clean(self):
        cleaned_data = super().clean()

        try:
            min_date_float = float(cleaned_data['min_date'])
            if min_date_float > 0:
                min_date_mjd = Time(min_date_float, format='mjd')
            else:
                min_date_mjd = Time.now().mjd + min_date_float
        except ValueError:
            try:
                min_date_mjd = Time(cleaned_data['min_date']).mjd
            except ValueError:
                raise forms.ValidationError(f"The minimum date {cleaned_data['min_date']} could not be parsed")
        cleaned_data['min_date_mjd'] = min_date_mjd

        try:
            max_date_float = float(cleaned_data['max_date'])
            if max_date_float > 0:
                max_date_mjd = Time(max_date_float, format='mjd')
            else:
                max_date_mjd = Time.now().mjd + max_date_float
        except ValueError:
            try:
                max_date_mjd = Time(cleaned_data['max_date']).mjd
            except ValueError:
                raise forms.ValidationError(f"The maximum date {cleaned_data['max_date']} could not be parsed")
        cleaned_data['max_date_mjd'] = max_date_mjd

        return cleaned_data


class ClippedStackedAtlasProcessor(AtlasProcessor):
    """
    Applies the sigma clipping and stacking recommended by David Young:
    https://github.com/thespacedoctor/plot-results-from-atlas-force-photometry-service/blob/main/plot_atlas_fp.py
    """

    def _process_photometry_from_plaintext(self, data_product):
        """
        Processes the photometric data from a plaintext file into a list of dicts. File is read using astropy as
        specified in the below documentation. The file is expected to be a multi-column delimited space delimited
        text file, as produced by the ATLAS forced photometry service at https://fallingstar-data.com/forcedphot
        See https://fallingstar-data.com/forcedphot/resultdesc/ for a description of the output format.

        The header looks like this:
        ###MJD   m   dm  uJy   duJy F err chi/N   RA  Dec   x   y  maj  min   phi  apfit mag5sig Sky   Obs

        :param data_product: ATLAS Photometric DataProduct which will be processed into a list of dicts
        :type data_product: DataProduct

        :returns: python list containing the photometric data from the DataProduct
        :rtype: list
        """
        photometry = []
        signal_to_noise_cutoff = 3.0  # cutoff to turn magnitudes into non-detection limits

        with open(data_product.data.path) as f:
            filecontent = f.read()

        data = ATLAS_stack(filecontent, logger)
        if len(data) < 1:
            raise InvalidFileFormatException('Empty table or invalid file type')

        try:
            for datum in data:
                time = Time(datum['mjd'], format='mjd')
                utc = TimezoneInfo(utc_offset=0*units.hour)
                time.format = 'datetime'
                value = {
                    'timestamp': time.to_datetime(timezone=utc),
                    'filter': str(datum['F']),
                    'telescope': 'ATLAS',
                }
                # If the signal is in the noise, calculate the non-detection limit from the reported flux uncertainty.
                # see https://fallingstar-data.com/forcedphot/resultdesc/
                signal_to_noise = datum['uJy'] / datum['duJy']
                if signal_to_noise <= signal_to_noise_cutoff:
                    value['limit'] = 23.9 - 2.5 * np.log10(signal_to_noise_cutoff * datum['duJy'])
                else:
                    value['magnitude'] = 23.9 - 2.5 * np.log10(datum['uJy'])
                    value['error'] = 2.5 / np.log(10.) / signal_to_noise

                photometry.append(value)
        except Exception as e:
            raise InvalidFileFormatException(e)

        return photometry
