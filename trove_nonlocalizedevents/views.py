from django.db.models import Count
from django_filters.views import FilterView
from trove_targets.models import Target
from tom_targets.permissions import targets_for_user
from tom_nonlocalizedevents.models import EventCandidate


class EventCandidateListView(FilterView):
    """
    View for listing candidates in the TOM.
    """
    model = EventCandidate
    template_name = 'trove_nonlocalizedevents/candidate_list.html'
    # We need to skip pagination for ordering, if we ever have more
    # candidates than this we have an issue...
    paginate_by = 100_000_000 

    def get_queryset(self):
        """
        Gets the set of ``Candidate`` objects associated with ``Target`` objects that the user has permission to view.

        :returns: Set of ``Candidate`` objects
        :rtype: QuerySet
        """
        return super().get_queryset().filter(
            target__in=targets_for_user(self.request.user, Target.objects.all(), 'view_target')
        )
