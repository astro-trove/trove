import django_filters
from .models import CSSField
import json
from django.conf import settings
from astropy.time import Time
from tom_targets.utils import cone_search_filter
from tom_nonlocalizedevents.models import NonLocalizedEvent

CREDIBLE_REGION_PERCENTS = [int(100 * p) for p in json.loads(settings.CREDIBLE_REGION_PROBABILITIES)]


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

    def nle_filter(self, queryset, name, value):
        if name == 'nonlocalizedevent':
            seq = value.sequences.last()
            mjd = Time(seq.details['time']).mjd
            queryset = queryset.filter(mjdmid__gte=mjd, mjdmid__lte=mjd + 3.,
                                       field__css_field_credible_regions__localization=seq.localization)
        return queryset

    def prob_filter(self, queryset, name, value):
        if name == 'probability':
            queryset = queryset.filter(field__css_field_credible_regions__smallest_percent__lte=value)
        return queryset

    nonlocalizedevent = django_filters.ModelChoiceFilter(queryset=NonLocalizedEvent.objects.order_by('-created'),
                                                         method='nle_filter', label='GW Event')
    probability = django_filters.ChoiceFilter(choices=[(p, p) for p in CREDIBLE_REGION_PERCENTS][::-1],
                                              method='prob_filter', label='Credible Region', empty_label=100)

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

