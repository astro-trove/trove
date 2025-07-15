import django.forms
import django_filters
import json
from django.conf import settings
from django.db.models import OuterRef, Subquery
from datetime import datetime, timedelta
import sys
from tom_nonlocalizedevents.models import NonLocalizedEvent, EventSequence

CREDIBLE_REGION_PROBABILITIES = json.loads(settings.CREDIBLE_REGION_PROBABILITIES)
CREDIBLE_REGION_CHOICES = [(int(100. * p), f'{p:.0%}') for p in CREDIBLE_REGION_PROBABILITIES]


def _get_nonlocalized_event_choices():
    return [(None, '-------')] + [(nle, str(nle)) for nle in NonLocalizedEvent.objects.order_by('-created')]


class LocalizationWidget(django.forms.widgets.MultiWidget):
    def __init__(self, **kwargs):
        widgets = {
            'event': django.forms.Select(choices=_get_nonlocalized_event_choices()),
            'prob': django.forms.Select(choices=CREDIBLE_REGION_CHOICES),
            'dt': django.forms.NumberInput(attrs={'placeholder': 'days after'}),
        }
        super().__init__(widgets, **kwargs)

    def decompress(self, value):
        return value or (None, None, None)


class LocalizationField(django.forms.MultiValueField):
    def __init__(self, **kwargs):
        fields = (
            django.forms.TypedChoiceField(choices=_get_nonlocalized_event_choices(),
                                          coerce=lambda name: NonLocalizedEvent.objects.get(event_id=name)),
            django.forms.TypedChoiceField(choices=CREDIBLE_REGION_CHOICES, coerce=int),
            django.forms.FloatField(min_value=0., initial=3.)
        )
        super().__init__(fields, widget=LocalizationWidget, **kwargs)

    def compress(self, data_list):
        return data_list


class LocalizationFilter(django_filters.Filter):
    field_class = LocalizationField

    def filter(self, queryset, value):
        if value and value[0]:
            nle, prob, dt = value
            seq = nle.sequences.last()
            if seq is None or seq.details is None:
                return queryset.none()
            tmin = datetime.strptime(seq.details['time'], '%Y-%m-%dT%H:%M:%S.%f%z')
            tmax = datetime.now(tmin.tzinfo) if dt is None else tmin + timedelta(days=dt)
            filter_kwargs = {
                'survey_field__credibleregions__localization': seq.localization,
                'survey_field__credibleregions__smallest_percent__lte': prob,
                'scheduled_start__gte': tmin,
                'scheduled_start__lte': tmax,
            }
            return queryset.filter(**filter_kwargs)
        else:
            return queryset


class NonLocalizedEventFilter(django_filters.FilterSet):
    prefix = django_filters.ChoiceFilter(choices=(('S', 'Real'), ('MS', 'Test')), label='Alert Type',
                                         field_name='event_id', lookup_expr='startswith')
    state = django_filters.ChoiceFilter(choices=(('ACTIVE', 'Active'), ('RETRACTED', 'Retracted')))

    @staticmethod
    def last_sequence_filter(queryset, name, value):
        """Filter on fields of the last EventSequence of a NonLocalizedEvent"""
        name_parts = name.split('__')
        field_name = '__'.join(name_parts[:-1])  # excluding the field lookup, e.g., __gte
        if name_parts[-2] == 'far':
            value = 3.168808781402895e-08 / float(value)  # yr to 1/Hz
        elif name_parts[-2].startswith('Has'):
            value = 0.01 * float(value)  # percent to decimal
        else:
            value = float(value)
        last_value = EventSequence.objects.filter(nonlocalizedevent_id=OuterRef('id')).order_by('-sequence_id').values(field_name)[:1]
        return queryset.annotate(**{field_name: Subquery(last_value)}).filter(**{name: value})

    inv_far_min = django_filters.NumberFilter('details__far__lte',
                                              method='last_sequence_filter', label='1/FAR', min_value=sys.float_info.epsilon,
                                              help_text='Significant CBC alerts have 1/FAR > 0.5 yr')
    distance_max = django_filters.NumberFilter('localization__distance_mean__lte',
                                               method='last_sequence_filter', label='Distance', min_value=0.)
    has_ns_min = django_filters.NumberFilter('details__properties__HasNS__gte',
                                             method='last_sequence_filter', label='HasNS', min_value=0., max_value=100.)
    has_remnant_min = django_filters.NumberFilter('details__properties__HasRemnant__gte',
                                                  method='last_sequence_filter', label='HasRemnant', min_value=0., max_value=100.)
