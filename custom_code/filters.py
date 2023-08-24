import django.forms
import django_filters
from tom_surveys.models import SurveyField
import json
from django.conf import settings
from datetime import datetime, timedelta
from tom_targets.utils import cone_search_filter
from tom_nonlocalizedevents.models import NonLocalizedEvent
from .models import Candidate
from .cssfield_selection import rank_css_fields

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
            if queryset.model == Candidate:
                filter_kwargs = {
                    'observation_record__survey_field__credibleregions__localization': seq.localization,
                    'observation_record__survey_field__credibleregions__smallest_percent__lte': prob,
                    'observation_record__scheduled_start__gte': tmin,
                    'observation_record__scheduled_start__lte': tmax,
                }
            else:  # assume the model is SurveyObservationRecord itself
                filter_kwargs = {
                    'survey_field__credibleregions__localization': seq.localization,
                    'survey_field__credibleregions__smallest_percent__lte': prob,
                    'scheduled_start__gte': tmin,
                    'scheduled_start__lte': tmax,
                }
            return queryset.filter(**filter_kwargs)
        else:
            return queryset


class CandidateFilter(django_filters.FilterSet):
    cone_search = django_filters.CharFilter(method='filter_cone_search', label='Cone Search',
                                            help_text='RA, Dec, Search Radius (degrees)')

    def filter_cone_search(self, queryset, name, value):
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

    observation_record__survey_field = django_filters.ModelChoiceFilter(queryset=SurveyField.objects, label='Survey Field')
    classification = django_filters.ChoiceFilter(choices=[(0, 'Transient'), (1, 'Moving Object')])
    snr_min = django_filters.NumberFilter('snr', 'gte', label='Min. S/N')
    mag_range = django_filters.NumericRangeFilter('mag', label='Magnitude')
    obsdate_range = django_filters.DateTimeFromToRangeFilter('observation_record__scheduled_start', label='Obs. Date')
    mlscore_range = django_filters.NumericRangeFilter('mlscore', 'gte', label='ML Old')
    mlscore_real_range = django_filters.NumericRangeFilter('mlscore_real', label='ML Real')
    mlscore_bogus_range = django_filters.NumericRangeFilter('mlscore_bogus', label='ML Bogus')
    localization = LocalizationFilter(label='Localization')

    order = django_filters.OrderingFilter(
        fields=['observation_record__scheduled_start', 'ra', 'dec', 'snr', 'mag', 'mlscore', 'mlscore_real', 'mlscore_bogus'],
        field_labels={
            'snr': 'S/N',
            'mag': 'Magnitude',
            'observation_record__scheduled_start': 'Obs. Date',
            'mlscore': 'ML Old',
            'mlscore_real': 'ML Real',
            'mlscore_bogus': 'ML Bogus',
            'ra': 'R.A.',
            'dec': 'Dec.'
        }
    )


class CSSFieldWidget(django.forms.widgets.MultiWidget):
    def __init__(self, **kwargs):
        widgets = {
            'ngroups': django.forms.NumberInput(attrs={'placeholder': '# groups'}),
            'nfields': django.forms.NumberInput(attrs={'placeholder': '# fields'}),
        }
        super().__init__(widgets, **kwargs)

    def decompress(self, value):
        return value or (None, None)


class CSSFieldField(django.forms.MultiValueField):
    def __init__(self, **kwargs):
        fields = (
            django.forms.IntegerField(min_value=0, initial=3),
            django.forms.IntegerField(min_value=0, initial=12)
        )
        super().__init__(fields, widget=CSSFieldWidget, **kwargs)

    def compress(self, data_list):
        return data_list


class CSSFieldFilter(django_filters.Filter):
    field_class = CSSFieldField

    def filter(self, queryset, value):
        if value:
            rank_css_fields(queryset, n_groups=value[0], n_select=value[1])
            return queryset.filter(group__isnull=False, rank_in_group__isnull=False).order_by('group', 'rank_in_group')
        else:
            return queryset.order_by('group', 'rank_in_group')


class CSSFieldCredibleRegionFilter(django_filters.FilterSet):
    grouping = CSSFieldFilter(label='Grouping')
    order = django_filters.OrderingFilter(fields=['name', 'ra', 'dec', 'probability_contained',
                                                  'group', 'rank_in_group'])


class NonLocalizedEventFilter(django_filters.FilterSet):
    prefix = django_filters.ChoiceFilter(choices=(('S', 'Real'), ('MS', 'Test')), label='Alert Type',
                                         field_name='event_id', lookup_expr='startswith')
    state = django_filters.ChoiceFilter(choices=(('ACTIVE', 'Active'), ('RETRACTED', 'Retracted')))

    @staticmethod
    def inv_far_filter(queryset, name, min_inv_far):
        max_far = 3.168808781402895e-08 / float(min_inv_far)  # yr to 1/Hz
        return queryset.filter(**{name + '__lte': max_far}).distinct()  # TODO: only look at latest update
    inv_far_min = django_filters.NumberFilter('sequences__details__far',
                                              method='inv_far_filter', label='1/FAR', min_value=0.,
                                              help_text='Significant CBC alerts have 1/FAR > 0.5 yr')

    # classification = django_filters.MultipleChoiceFilter(
    #     choices=(
    #         ('BNS', 'BNS'),
    #         ('NSBH', 'NSBH'),
    #         ('BBH', 'BBH'),
    #         ('Burst', 'Burst'),
    #         ('Terrestrial', 'Terrestrial'),
    #     ),
    #     label='Classification(s)',
    #     help_text="Doesn't currently work",
    # )
    distance_max = django_filters.NumberFilter('sequences__localization__distance_mean',
                                               lookup_expr='lte', label='Distance', min_value=0.,
                                               distinct=True)  # TODO: only look at latest update

    @staticmethod
    def percent_gte_decimal(queryset, name, value):
        """Compare a float to a Django Decimal in a JSON-serializable way"""
        return queryset.filter(**{name + '__gte': 0.01 * float(value)}).distinct()  # TODO: only look at latest update
    has_ns_min = django_filters.NumberFilter('sequences__details__properties__HasNS',
                                             method='percent_gte_decimal', label='HasNS',
                                             min_value=0., max_value=100., help_text='Very slow')
    has_remnant_min = django_filters.NumberFilter('sequences__details__properties__HasRemnant',
                                                  method='percent_gte_decimal', label='HasRemnant',
                                                  min_value=0., max_value=100., help_text='Very slow')
