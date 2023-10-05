import django_filters
from tom_surveys.models import SurveyField
from tom_nonlocalizedevents.models import NonLocalizedEvent


class TreasureMapPointingFilter(django_filters.FilterSet):
    id = django_filters.NumericRangeFilter('treasuremap_id', label='ID')
    gw_event = django_filters.ModelChoiceFilter('nonlocalizedevent__event_id', label='GW Event',
                                                queryset=NonLocalizedEvent.objects.filter(event_id__startswith='S').order_by('-event_id'))
    facility = django_filters.CharFilter('observation_record__facility', label='Facility')
    field = django_filters.ModelChoiceFilter('observation_record__survey_field', label='Field',
                                             queryset=SurveyField.objects)
    observation_time = django_filters.DateTimeFromToRangeFilter('observation_record__scheduled_start',
                                                                label='Observation Time')
    status = django_filters.ChoiceFilter('status', choices=(('COMPLETED', 'COMPLETED'), ('PENDING', 'PENDING')))
    order = django_filters.OrderingFilter(fields=['id', 'observation_time'], field_labels={'id': 'ID'})
