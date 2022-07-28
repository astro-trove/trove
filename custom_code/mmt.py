from tom_observations.facility import BaseRoboticObservationForm, BaseRoboticObservationFacility
from tom_targets.models import Target
from astropy.coordinates import SkyCoord
from django import forms
from crispy_forms.layout import Layout, Row, Column
from crispy_forms.bootstrap import AppendedText
from mmtapi import mmtapi
from saguaro_tom.settings import FACILITIES
import requests
from datetime import datetime
import re

MMT_SETTINGS = FACILITIES['MMT']


class MMTBaseObservationForm(BaseRoboticObservationForm):
    magnitude = forms.FloatField()
    visits = forms.IntegerField(initial=1, min_value=1)
    exposure_time = forms.IntegerField(min_value=1)
    number_of_exposures = forms.IntegerField(initial=2, min_value=1)
    priority = forms.ChoiceField(choices=[
        (3, 'low'),
        (2, 'medium'),
        (1, 'high'),
    ], initial=(3, 'low'))
    target_of_opportunity = forms.BooleanField(initial=True)


class MMTImagingForm(MMTBaseObservationForm):
    filter = forms.ChoiceField(choices=[('g', 'g'), ('r', 'r'), ('i', 'i'), ('z', 'z')])

    def layout(self):
        return Layout(
            Row(Column('magnitude'), Column(AppendedText('exposure_time', 's')), Column('filter')),
            Row(Column('visits'), Column('number_of_exposures'), Column('priority')),
            Row(Column('target_of_opportunity')),
        )

    def observation_payload(self):
        target = Target.objects.get(pk=self.cleaned_data['target_id'])
        ra, dec = SkyCoord(target.ra, target.dec, unit='deg').to_string('hmsdms', sep=':', precision=1).split()
        payload = {
            'observationtype': 'imaging',
            'objectid': re.sub('[^a-zA-Z0-9]', '', target.name),  # only alphanumeric characters allowed
            'ra': ra,
            'dec': dec,
            'epoch': 2000,
            'magnitude': self.cleaned_data['magnitude'],
            'maskid': 110,
            'filter': self.cleaned_data['filter'],
            'visits': self.cleaned_data['visits'],
            'exposuretime': self.cleaned_data['exposure_time'],
            'numberexposures': self.cleaned_data['number_of_exposures'],
            'priority': self.cleaned_data['priority'],
            'targetofopportunity': self.cleaned_data['target_of_opportunity'],
        }
        return payload


class MMTSpectroscopyForm(MMTBaseObservationForm):
    filter = forms.ChoiceField(choices=[('LP3500', 'LP3500'), ('LP3800', 'LP3800')], initial=('LP3800', 'LP3800'))
    grating = forms.ChoiceField(choices=[(270, 270), (600, 600), (1000, 1000)])
    central_wavelength = forms.FloatField(min_value=4108, max_value=9279, initial=7380)
    slit_width = forms.ChoiceField(choices=[
        ('Longslit0_75', '0.75'),
        ('Longslit1', '1.00'),
        ('Longslit1_25', '1.25'),
        ('Longslit1_5', '1.50'),
        ('Longslit5', '5.00'),
    ])

    def layout(self):
        return Layout(
            Row(Column('magnitude'), Column(AppendedText('exposure_time', 's')), Column('filter')),
            Row(
                Column(AppendedText('grating', 'l/mm')),
                Column(AppendedText('central_wavelength', 'Å')),
                Column(AppendedText('slit_width', 'arcsec'))
            ),
            Row(Column('visits'), Column('number_of_exposures'), Column('priority')),
            Row(Column('target_of_opportunity')),
        )

    def observation_payload(self):
        target = Target.objects.get(pk=self.cleaned_data['target_id'])
        ra, dec = SkyCoord(target.ra, target.dec, unit='deg').to_string('hmsdms', sep=':', precision=1).split()
        maskid = {
            'Longslit0_75': 113,
            'Longslit1': 111,
            'Longslit1_25': 131,
            'Longslit1_5': 114,
            'Longslit5': 121,
        }.get(self.cleaned_data['slit_width'])
        payload = {
            'observationtype': 'longslit',
            'objectid': re.sub('[^a-zA-Z0-9]', '', target.name),  # only alphanumeric characters allowed
            'ra': ra,
            'dec': dec,
            'epoch': 2000,
            'magnitude': self.cleaned_data['magnitude'],
            'grating': self.cleaned_data['grating'],
            'centralwavelength': self.cleaned_data['central_wavelength'],
            'slitwidth': self.cleaned_data['slit_width'],
            'maskid': maskid,
            'filter': self.cleaned_data['filter'],
            'visits': self.cleaned_data['visits'],
            'exposuretime': self.cleaned_data['exposure_time'],
            'numberexposures': self.cleaned_data['number_of_exposures'],
            'priority': self.cleaned_data['priority'],
            'targetofopportunity': self.cleaned_data['target_of_opportunity'],
        }
        return payload


class MMTFacility(BaseRoboticObservationFacility):
    name = 'MMT'
    observation_forms = {
        'IMAGING': MMTImagingForm,
        'SPECTROSCOPY': MMTSpectroscopyForm,
    }
    SITES = {
        'F. L. Whipple': {
            'sitecode': 'flwo',
            'latitude': 31.688944,
            'longitude': -110.884611,
            'elevation': 2616
        },
    }

    def data_products(self, observation_id, product_id=None):
        datalist = mmtapi.Datalist(token=MMT_SETTINGS['api_key'])
        datalist.get(targetid=observation_id)

        # flatten the dictionary structure across all "queues"
        data_products = []
        for dp in datalist.data:
            file_info = dp['datafiles']
            file_info['id'] = file_info['datafileid']
            data_products += file_info

        return data_products

    def get_form(self, observation_type):
        return self.observation_forms.get(observation_type, MMTBaseObservationForm)

    def get_observation_status(self, observation_id):
        return {'state': 'UNKNOWN', 'scheduled_start': None, 'scheduled_end': None}

    def submit_observation(self, observation_payload):
        target = mmtapi.Target(token=MMT_SETTINGS['api_key'], payload=observation_payload)
        target.post()
        return [target.id]

    def validate_observation(self, observation_payload):
        mmtapi.Target(token=MMT_SETTINGS['api_key'], payload=observation_payload)  # validate is called by __init__
        # TODO: It would be better if Target.validate could return the errors instead of printing them.
        # TODO: Then we could capture them here and display them as a banner on the web page.
        return []

    def get_terminal_observing_states(self):
        return []

    def get_observing_sites(self):
        return self.SITES

    def cancel_observation(self, observation_id):
        target = mmtapi.Target(token=MMT_SETTINGS['api_key'], payload={'targetid': observation_id})
        target.delete()

    def get_observation_url(self, observation_id):
        pass

    def get_facility_status(self):
        response = requests.get('https://scheduler.mmto.arizona.edu/APIv2/trimester//schedule/all')
        schedule = response.json()
        for run in schedule['published']['runs']:
            start = datetime.strptime(run['start'], '%Y-%m-%d %H:%M:%S-%f')
            end = datetime.strptime(run['end'], '%Y-%m-%d')
            if start < datetime.now() < end:
                if run['type'] == 'queue':
                    status = run['title']
                else:
                    status = f"{run['instrument']['name']} ({run['title']})"
                break
        else:
            status = 'NO RUN SCHEDULED'
        facility_status = {
            'code': 'MMT',
            'sites': [{
                'code': 'flwo',
                'telescopes': [{
                    'code': 'flwo.doma.6m5a',
                    'status': status,
                }]
            }]
        }
        return facility_status

    def get_facility_weather_urls(self):
        return {'code': 'MMT', 'sites': [{'code': 'flwo',
                                          'weather_url': 'https://www.mmto.org/current-weather-at-the-mmt/'}]}
