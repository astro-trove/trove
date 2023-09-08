import django_filters
from django.contrib.auth.models import User
from tom_targets.utils import cone_search_filter
from custom_code.filters import LocalizationFilter
from .models import SurveyField, SurveyObservationRecord


class SurveyObservationRecordFilter(django_filters.FilterSet):
    @staticmethod
    def filter_cone_search(queryset, name, value):
        """
        Perform a cone search filter on this filter's queryset,
        using the cone search utlity method and the specified RA, DEC
        """
        if name == 'cone_search':
            ra, dec, radius = value.split(',')
        else:
            return queryset

        ra = float(ra)
        dec = float(dec)

        return cone_search_filter(queryset, ra, dec, radius)

    cone_search = django_filters.CharFilter(method='filter_cone_search', label='Cone Search',
                                            help_text='RA, Dec, Search Radius (degrees)')
    survey_field = django_filters.ModelChoiceFilter(queryset=SurveyField.objects, label='Field')
    user = django_filters.ModelChoiceFilter(queryset=User.objects, label='Requested by')
    facility = django_filters.ChoiceFilter(choices=[('CSS', 'CSS')])
    observation_id = django_filters.CharFilter('observation_id', label='Filename')
    status = django_filters.ChoiceFilter(choices=[('PENDING', 'PENDING'), ('COMPLETED', 'COMPLETED')])
    scheduled_start_range = django_filters.DateTimeFromToRangeFilter('scheduled_start', label='Time')
    localization = LocalizationFilter(label='Localization')
    order = django_filters.OrderingFilter(
        fields=['facility', 'survey_field', 'scheduled_start', 'status'],
        field_labels={
            'scheduled_start': 'Time',
        }
    )
