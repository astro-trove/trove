import django_filters
from .models import Candidate
from tom_targets.utils import cone_search_filter


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

    snr_min = django_filters.NumberFilter('snr', 'gte', label='Min. S/N')
    mag_range = django_filters.NumericRangeFilter('mag', label='Magnitude')
    obsdate_range = django_filters.DateTimeFromToRangeFilter('obsdate', label='Obs. Date')
    mlscore_min = django_filters.NumberFilter('mlscore', 'gte', label='Min. ML Score')

    order = django_filters.OrderingFilter(
        fields=['obsdate', 'ra', 'dec', 'snr', 'mag', 'mlscore'],
        field_labels={
            'snr': 'S/N',
            'mag': 'Magnitude',
            'obsdate': 'Obs. Date',
            'mlscore': 'ML Score',
            'ra': 'R.A.',
            'dec': 'Dec.'
        }
    )

    class Meta:
        model = Candidate
        fields = ['cone_search', 'snr_min', 'mag_range', 'obsdate_range', 'field', 'classification', 'mlscore_min']
