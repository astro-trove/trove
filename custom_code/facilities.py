from tom_mmt.mmt import MMTBaseObservationForm, MMTImagingForm, MMTMMIRSImagingForm, MMTSpectroscopyForm, \
                        MMTMMIRSSpectroscopyForm, MMTFacility
from crispy_forms.layout import Layout, HTML


class CustomMMTObservationForm(MMTBaseObservationForm):
    def __init__(self, *args, **kwargs):
        user = kwargs['initial']['user']
        kwargs['initial']['notes'] = f'This is a rapid ToO for GW follow-up. ' \
                                     f'For questions please reach out to {user.get_full_name()} at {user.email}.\n\n' \
                                     f'{kwargs["initial"].get("notes", "")}'
        super().__init__(*args, **kwargs)


class CustomBinospecImagingForm(CustomMMTObservationForm, MMTImagingForm):
    pass


class CustomBinospecSpectroscopyForm(CustomMMTObservationForm, MMTSpectroscopyForm):
    pass


class CustomMMIRSObservationForm(CustomMMTObservationForm):
    def __init__(self, *args, **kwargs):
        kwargs['initial']['notes'] = 'Please use a random 30" dither pattern (4 exposures per position if K-band). ' \
                                     'Please guide for individual exposures. I have put in a dither size of 30" but ' \
                                     'this just specifies my estimated box size to not lose guiding (not the size of ' \
                                     'the individual dithers). We do not care about the position angle so adjust as ' \
                                     'needed for the guide star.'
        super().__init__(*args, **kwargs)

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

class CustomMMTFacility(MMTFacility):
    observation_forms = {
        'Binospec_Imaging': CustomBinospecImagingForm,
        'Binospec_Spectroscopy': CustomBinospecSpectroscopyForm,
        'MMIRS_Imaging': CustomMMIRSImagingForm,
        'MMIRS_Spectroscopy': CustomMMIRSSpectroscopyForm,
    }
