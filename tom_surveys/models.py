from django.contrib.auth.models import User
from django.db import models

from tom_observations.facility import get_service_class
from tom_common.hooks import run_hook


class SurveyField(models.Model):
    name = models.CharField(max_length=6, primary_key=True)
    ra = models.FloatField()
    dec = models.FloatField()
    ecliptic_lng = models.FloatField()
    ecliptic_lat = models.FloatField()
    galactic_lng = models.FloatField()
    galactic_lat = models.FloatField()
    healpix = models.BigIntegerField()
    adjacent = models.ManyToManyField('self')
    has_reference = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class SurveyObservationRecord(models.Model):
    """
    Class representing a wide-field survey observation in a TOM.

    A SurveyObservationRecord corresponds to a single exposure at a facility, and is associated with a single field.

    :param survey_field: The ``SurveyField`` with which this object is associated.
    :type survey_field: SurveyField

    :param user: The ``User`` who requested this observation.

    :param facility: The facility at which this observation is taken. Should be the name specified in the corresponding
        TOM facility module, if one exists.
    :type facility: str

    :param parameters: The set of parameters used in the API request made to create the observation
    :type parameters: dict

    :param status: The current status of the observation. Should be a valid status in the corresponding TOM facility
        module, if one exists.
    :type status: str

    :param observation_id: An identifier for the observation from the facility.
    :type observation_id: str

    :param scheduled_start: The time at which the observation is scheduled to begin, according to the facility.
    :type scheduled_start: datetime

    :param scheduled_end: The time at which the observation is scheduled to end, according to the facility.
    :type scheduled_end: datetime

    :param created: The time at which this object was created.
    :type created: datetime

    :param modified: The time at which this object was last updated.
    :type modified: datetime
    """
    survey_field = models.ForeignKey(SurveyField, on_delete=models.CASCADE)
    user = models.ForeignKey(User, null=True, default=None, on_delete=models.DO_NOTHING)
    facility = models.CharField(max_length=50)
    parameters = models.JSONField()
    observation_id = models.CharField(max_length=255)
    status = models.CharField(max_length=200)
    scheduled_start = models.DateTimeField(null=True)
    scheduled_end = models.DateTimeField(null=True)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created',)
        models.UniqueConstraint(fields=['facility', 'observation_id'], name='unique_facility_observation_id')

    def save(self, *args, **kwargs):
        if self.id:
            presave_data = SurveyObservationRecord.objects.get(pk=self.id)
            super().save(*args, **kwargs)
            if self.status != presave_data.status:
                run_hook('observation_change_state', self, presave_data.status)
        else:
            super().save(*args, **kwargs)
            run_hook('observation_change_state', self, None)

    @property
    def terminal(self):
        facility = get_service_class(self.facility)
        return self.status in facility().get_terminal_observing_states()

    @property
    def failed(self):
        facility = get_service_class(self.facility)
        return self.status in facility().get_failed_observing_states()

    @property
    def url(self):
        facility = get_service_class(self.facility)
        return facility().get_observation_url(self.observation_id)

    def update_status(self):
        facility = get_service_class(self.facility)
        facility().update_observation_status(self.id)

    def save_data(self):
        facility = get_service_class(self.facility)
        facility().save_data_products(self)

    def __str__(self):
        return '{0} @ {1}'.format(self.survey_field, self.facility)
