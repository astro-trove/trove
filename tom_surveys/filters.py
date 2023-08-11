import django_filters
from django.contrib.auth.models import User
from .models import SurveyField, SurveyObservationRecord


class SurveyObservationRecordFilter(django_filters.FilterSet):
    survey_field = django_filters.ModelChoiceFilter(queryset=SurveyField.objects, label='Field')
    user = django_filters.ModelChoiceFilter(queryset=User.objects, label='Requested by')
    facility = django_filters.ChoiceFilter(choices=[('CSS', 'CSS')])
    observation_id = django_filters.CharFilter('observation_id', label='Filename')
    status = django_filters.ChoiceFilter(choices=[('PENDING', 'PENDING'), ('COMPLETED', 'COMPLETED')])
    scheduled_start_range = django_filters.DateTimeFromToRangeFilter('scheduled_start', label='Time')
    order = django_filters.OrderingFilter(
        fields=['facility', 'survey_field', 'scheduled_start', 'status'],
        field_labels={
            'scheduled_start': 'Time',
        }
    )

    class Meta:
        model = SurveyObservationRecord
        fields = ['facility', 'survey_field', 'scheduled_start_range', 'status', 'user', 'observation_id']

