import django.forms
import django_filters
from .models import CSSField
import json
from django.conf import settings
from datetime import datetime, timedelta
from tom_targets.utils import cone_search_filter
from tom_nonlocalizedevents.models import NonLocalizedEvent

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
        return value


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
        if value and value[1]:
            nle, prob, dt = value
            seq = nle.sequences.last()
            if seq is None or seq.details is None:
                return queryset.none()
            tmin = datetime.strptime(seq.details['time'], '%Y-%m-%dT%H:%M:%S.%f%z')
            tmax = datetime.now(tmin.tzinfo) if dt is None else tmin + timedelta(days=dt)
            return queryset.filter(field__css_field_credible_regions__localization=seq.localization,
                                   field__css_field_credible_regions__smallest_percent__lte=prob,
                                   obsdate__gte=tmin, obsdate__lte=tmax)
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

    field = django_filters.ModelChoiceFilter(queryset=CSSField.objects)
    classification = django_filters.ChoiceFilter(choices=[(0, 'Transient'), (1, 'Moving Object')])
    snr_min = django_filters.NumberFilter('snr', 'gte', label='Min. S/N')
    mag_range = django_filters.NumericRangeFilter('mag', label='Magnitude')
    obsdate_range = django_filters.DateTimeFromToRangeFilter('obsdate', label='Obs. Date')
    mlscore_range = django_filters.NumericRangeFilter('mlscore', 'gte', label='ML Old')
    mlscore_real_range = django_filters.NumericRangeFilter('mlscore_real', label='ML Real')
    mlscore_bogus_range = django_filters.NumericRangeFilter('mlscore_bogus', label='ML Bogus')
    localization = LocalizationFilter(label='Localization')

    order = django_filters.OrderingFilter(
        fields=['obsdate', 'ra', 'dec', 'snr', 'mag', 'mlscore', 'mlscore_real', 'mlscore_bogus'],
        field_labels={
            'snr': 'S/N',
            'mag': 'Magnitude',
            'obsdate': 'Obs. Date',
            'mlscore': 'ML Old',
            'mlscore_real': 'ML Real',
            'mlscore_bogus': 'ML Bogus',
            'ra': 'R.A.',
            'dec': 'Dec.'
        }
    )

