from django_filters.views import FilterView
from .models import SurveyObservationRecord
from .filters import SurveyObservationRecordFilter


class SurveyObservationListView(FilterView):
    """
    View for listing candidates in the TOM.
    """
    template_name = 'tom_surveys/observation_list.html'
    paginate_by = 25
    strict = False
    model = SurveyObservationRecord
    filterset_class = SurveyObservationRecordFilter
