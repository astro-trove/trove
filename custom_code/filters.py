import django.forms
import django_filters
import json
from django.conf import settings
from django.db.models import F, FloatField, OuterRef, Subquery
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast
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
    @staticmethod
    def last_sequence_filter(queryset, name, value):
        """Filter on fields of the last EventSequence of a NonLocalizedEvent"""
        name_parts = name.split('__')
        field_name = '__'.join(name_parts[:-1])  # excluding the field lookup, e.g., __gte
        if name_parts[-2] == 'far':
            value = 3.168808781402895e-08 / float(value)  # yr to 1/Hz
        elif name_parts[-2].startswith('Has') or 'signalness' in name:
            value = 0.01 * float(value)  # percent to decimal
        else:
            value = float(value)
        last_value = EventSequence.objects.filter(nonlocalizedevent_id=OuterRef('id')).order_by('-sequence_id').values(field_name)[:1]
        return queryset.annotate(**{field_name: Subquery(last_value)}).filter(**{name: value})


# the CBC probabilities every GW alert carries; the most likely class is the
# largest of these, which is what `get_most_likely_class` reports
CLASSIFICATION_KEYS = ("BBH", "BNS", "NSBH", "Terrestrial")
SOURCE_TYPE_CHOICES = [(k, k) for k in CLASSIFICATION_KEYS] + [("SSM", "SSM")]


class GWFilter(NonLocalizedEventFilter):
    event_id = django_filters.CharFilter(field_name='event_id', lookup_expr='icontains',
                                         label='Event name')
    # "Real" read as though GWTC events were not; these name alert streams, not
    # which detections are genuine. Wording follows TreasureMap.
    prefix = django_filters.ChoiceFilter(choices=(('GW', 'GWTC'),
                                                  ('S', 'Observation'),
                                                  ('MS', 'Test')),
                                         empty_label='All',
                                         label='Alert Type', field_name='event_id', lookup_expr='startswith')
    state = django_filters.ChoiceFilter(choices=(('ACTIVE', 'Active'), ('RETRACTED', 'Retracted')))
    source_type = django_filters.ChoiceFilter(choices=SOURCE_TYPE_CHOICES, label='Source Type',
                                              method='most_likely_class_filter')
    has_ssm_min = django_filters.NumberFilter('details__properties__HasSSM__gte',
                                              method='last_sequence_filter', label='HasSSM',
                                              min_value=0., max_value=100.)

    @staticmethod
    def most_likely_class_filter(queryset, name, value):
        """Keep events whose most likely class is `value`.

        Mirrors `get_most_likely_class`: SSM is flagged by the search field,
        every other class is the largest of the CBC probabilities. Done in SQL
        so it composes with pagination instead of filtering a whole page away.
        """
        if not value:
            return queryset
        last_details = (EventSequence.objects
                        .filter(nonlocalizedevent_id=OuterRef('id'))
                        .order_by('-sequence_id').values('details')[:1])
        qs = queryset.annotate(_details=Subquery(last_details))
        qs = qs.annotate(_search=KeyTextTransform('search', '_details'),
                         _group=KeyTextTransform('group', '_details'))
        if value == 'SSM':
            return qs.filter(_search='SSM')
        qs = qs.exclude(_search='SSM').filter(_group='CBC').annotate(**{
            f'_p_{k}': Cast(KeyTextTransform(k, KeyTextTransform('classification', '_details')),
                            FloatField())
            for k in CLASSIFICATION_KEYS})
        for other in CLASSIFICATION_KEYS:
            if other != value:
                qs = qs.filter(**{f'_p_{value}__gte': F(f'_p_{other}')})
        return qs
    inv_far_min = django_filters.NumberFilter('details__far__lte',
                                              method='last_sequence_filter', label='1/FAR', min_value=sys.float_info.epsilon,
                                              help_text='Significant CBC alerts have 1/FAR > 0.5 yr')
    distance_max = django_filters.NumberFilter('localization__distance_mean__lte',
                                               method='last_sequence_filter', label='Distance', min_value=0.)
    has_ns_min = django_filters.NumberFilter('details__properties__HasNS__gte',
                                             method='last_sequence_filter', label='HasNS', min_value=0., max_value=100.)
    has_remnant_min = django_filters.NumberFilter('details__properties__HasRemnant__gte',
                                                  method='last_sequence_filter', label='HasRemnant', min_value=0., max_value=100.)


class NeutrinoFilter(NonLocalizedEventFilter):
    notice_type = django_filters.ChoiceFilter('details__notice_type', label='Notice Type',
                                              choices=[('BRONZE', 'Bronze'), ('GOLD', 'Gold')])
    inv_far_min = django_filters.NumberFilter('details__far__lte',
                                              method='last_sequence_filter', label='1/FAR', min_value=sys.float_info.epsilon)
    energy_min = django_filters.NumberFilter('details__energy__gte',
                                             method='last_sequence_filter', label='Energy', min_value=0.)
    signalness_min = django_filters.NumberFilter('details__signalness__gte',
                                                 method='last_sequence_filter', label='Signalness', min_value=0., max_value=100.)
    time = django_filters.DateFromToRangeFilter(field_name='details__time', label='Time', method='last_sequence_filter')
