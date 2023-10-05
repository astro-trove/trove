from django_filters.views import FilterView
from .models import TreasureMapPointing
from .filters import TreasureMapPointingFilter


def get_distinct(queryset, *attrs):
    """Get distinct members of a queryset according to one or more attributes and returns the attributes"""
    return queryset.values(*attrs).order_by(*attrs).distinct()


class TreasureMapPointingListView(FilterView):
    """
    View for listing candidates in the TOM.
    """
    template_name = 'tom_treasuremap/pointing_list.html'
    paginate_by = 100
    strict = False
    model = TreasureMapPointing
    filterset_class = TreasureMapPointingFilter

    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(object_list=object_list, **kwargs)
        context['observation_records'] = get_distinct(object_list, 'observation_record')
        context['fields_covered'] = get_distinct(object_list, 'observation_record__survey_field', 'nonlocalizedevent')
        context['nonlocalizedevents'] = get_distinct(object_list, 'nonlocalizedevent')
        return context
