from django_filters.views import FilterView
from django.core.cache import cache
from django.core.paginator import Paginator
from trove_targets.models import Target
from tom_targets.permissions import targets_for_user
from tom_nonlocalizedevents.models import EventCandidate
from candidate_vetting.util import get_event_candidate_scores


class EventCandidateListView(FilterView):
    """
    View for listing candidates in the TOM.
    """

    model = EventCandidate
    template_name = "trove_nonlocalizedevents/candidate_list.html"
    # We need to skip pagination for ordering, if we ever have more
    # candidates than this we have an issue...
    paginate_by = 20

    def get_queryset(self):
        """
        Gets the set of ``Candidate`` objects associated with ``Target`` objects that
        the user has permission to view.

        :returns: Set of ``Candidate`` objects
        :rtype: QuerySet
        """
        qs = (
            super()
            .get_queryset()
            .filter(
                target__in=targets_for_user(
                    self.request.user, Target.objects.all(), "view_target"
                )
            )
        )

        # Filter by nonlocalizedevent if provided in URL
        nonlocalizedevent_id = self.request.GET.get("nonlocalizedevent")
        if nonlocalizedevent_id:
            qs = qs.filter(nonlocalizedevent_id=nonlocalizedevent_id)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Create cache key from filters (excluding page number)
        filter_key = self.request.GET.urlencode().split("&page=")[0]
        cache_key = f"event_candidates_scored_{filter_key}"

        # Check cache first
        scored_candidates = cache.get(cache_key)

        if scored_candidates is None:
            # Not in cache—score all candidates
            all_candidates = self.get_queryset()
            scored_candidates = get_event_candidate_scores(all_candidates)
            # Cache for 5 minutes
            cache.set(cache_key, scored_candidates, 60 * 5)

        # Paginate the cached scored list
        paginator = Paginator(scored_candidates, self.paginate_by)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context["page_obj"] = page_obj
        context["object_list"] = page_obj.object_list

        return context
