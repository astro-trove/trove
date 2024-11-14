from tom_mmt.mmt import (MMTBinospecObservationForm, MMTBinospecImagingForm, MMTBinospecSpectroscopyForm,
                         MMTMMIRSObservationForm, MMTMMIRSImagingForm, MMTMMIRSSpectroscopyForm, MMTFacility,
                         MMTCamObservationForm)
from crispy_forms.layout import Layout, HTML
from django.conf import settings

SAGUARO_NOTE = 'This is a rapid ToO for GW follow-up. ' \
               f'For questions, please contact the SAGUARO team at {settings.CONTACT_EMAIL}.'
MMIRS_NOTE = 'Please use a random 30" dither pattern (4 exposures per position if K-band). ' \
             'Please guide for individual exposures. ' \
             'I have put in a dither size of 30" but this just specifies my estimated box size to not lose guiding ' \
             '(not the size of the individual dithers). ' \
             'We do not care about the position angle so adjust as needed for the guide star.'


class CustomBinospecObservationForm(MMTBinospecObservationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'notes' not in kwargs['initial']:
            kwargs['initial']['notes'] = SAGUARO_NOTE


class CustomBinospecImagingForm(CustomBinospecObservationForm, MMTBinospecImagingForm):
    pass


class CustomBinospecSpectroscopyForm(CustomBinospecObservationForm, MMTBinospecSpectroscopyForm):
    pass


class CustomMMIRSObservationForm(MMTMMIRSObservationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'notes' not in kwargs['initial']:
            kwargs['initial']['notes'] = f'{SAGUARO_NOTE}\n\n{MMIRS_NOTE}'

    def layout(self):
        return Layout(
            HTML('<p><a href="https://docs.google.com/document/d/1-ZKvIQxH7LRqWM5hAyqlVBLhtnncGlCYYHWzkghN-jk/edit">'
                 'More Information about MMIRS observations</a></p>'),
            *super().layout().fields
        )


class CustomMMIRSImagingForm(CustomMMIRSObservationForm, MMTMMIRSImagingForm):
    pass


class CustomMMIRSSpectroscopyForm(CustomMMIRSObservationForm, MMTMMIRSSpectroscopyForm):
    pass


class CustomMMTCamObservationForm(MMTCamObservationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'notes' not in kwargs['initial']:
            kwargs['initial']['notes'] = SAGUARO_NOTE


class CustomMMTFacility(MMTFacility):
    observation_forms = {
        'Binospec_Imaging': CustomBinospecImagingForm,
        'Binospec_Spectroscopy': CustomBinospecSpectroscopyForm,
        'MMIRS_Imaging': CustomMMIRSImagingForm,
        'MMIRS_Spectroscopy': CustomMMIRSSpectroscopyForm,
        'MMTCam': CustomMMTCamObservationForm,
    }
