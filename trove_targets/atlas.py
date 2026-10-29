from astropy.time import Time
import logging
from crispy_forms.layout import Row, Column
from django import forms

from tom_dataproducts.single_target_data_service.atlas import AtlasForcedPhotometryService
from tom_dataproducts.single_target_data_service.single_target_data_service import BaseSingleTargetDataServiceQueryForm

logger = logging.getLogger(__name__)


class CustomAtlasForcedPhotometryService(AtlasForcedPhotometryService):
    name = 'ATLAS'

    def get_form(self):
        """
        This method returns the form for querying this service.
        """
        return CustomAtlasForcedPhotometryQueryForm


class CustomAtlasForcedPhotometryQueryForm(BaseSingleTargetDataServiceQueryForm):
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
                min_date_mjd = Time(min_date_float, format='mjd').mjd
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
                max_date_mjd = Time(max_date_float, format='mjd').mjd
            else:
                max_date_mjd = Time.now().mjd + max_date_float
        except ValueError:
            try:
                max_date_mjd = Time(cleaned_data['max_date']).mjd
            except ValueError:
                raise forms.ValidationError(f"The maximum date {cleaned_data['max_date']} could not be parsed")
        cleaned_data['max_date_mjd'] = max_date_mjd

        return cleaned_data
