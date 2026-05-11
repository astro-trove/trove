from django_filters.views import FilterView
from django.core.cache import cache
from django.core.paginator import Paginator
from django.shortcuts import redirect
from django.urls import reverse
from django.http import JsonResponse
from trove_targets.models import Target
from tom_targets.permissions import targets_for_user
from tom_nonlocalizedevents.models import EventCandidate
from candidate_vetting.util import get_event_candidate_scores

from .forms import EventCandidateSearchForm

from dal import autocomplete


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
            .select_related("target", "nonlocalizedevent")
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
            all_candidates = self.filterset.qs
            scored_candidates = get_event_candidate_scores(all_candidates)
            # Cache for 5 minutes
            cache.set(cache_key, scored_candidates, 60 * 5)

        # Paginate the cached scored list
        paginator = Paginator(scored_candidates, self.paginate_by)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context["page_obj"] = page_obj
        context["object_list"] = page_obj.object_list

        nle_id = self.request.GET.get("nonlocalizedevent")
        context["eventcandidate_filter_form"] = EventCandidateSearchForm(nle_id=nle_id)

        return context

    def get(self, request, *args, **kwargs):
        print(f"DEBUG: request.GET={request.GET}")
        candidate_id = request.GET.get("target_name")
        print(f"DEBUG: candidate_id={candidate_id}")
        if candidate_id:
            try:
                candidate = EventCandidate.objects.select_related(
                    "target", "nonlocalizedevent"
                ).get(pk=candidate_id)
                return redirect(
                    reverse("targets:detail", args=[candidate.target.id])
                    + f"?nonlocalizedevent={candidate.nonlocalizedevent.event_id}"
                )
            except (EventCandidate.DoesNotExist, ValueError):
                pass
        return super().get(request, *args, **kwargs)


class EventCandidateAutocompleteView(autocomplete.Select2QuerySetView):
    def get_queryset(self):
        nle_id = self.request.GET.get("nonlocalizedevent")
        qs = EventCandidate.objects.all()
        if nle_id:
            qs = qs.filter(nonlocalizedevent_id=nle_id)

        if self.q:
            # Simple case-insensitive search on the name field
            qs = qs.filter(target__name__icontains=self.q)

        return qs


def generate_report(request):
    nle_id = request.GET.get("nonlocalizedevent")
    try:
        ncands = int(request.GET.get("n", 10))
    except ValueError:
        ncands = 10  # this means the user didn't pass an integer to the n param

    candidates = EventCandidate.objects.filter(
        nonlocalizedevent_id=nle_id
    ).select_related("target", "nonlocalizedevent")

    candidates = list(get_event_candidate_scores(candidates))  # [:ncands]

    lines = [f"Event: {nle_id}", "=" * 40, ""]
    for i, ec in enumerate(candidates, 1):
        if i > ncands:
            break
        lines.append(f"{i}. {ec.target.name}")
        lines.append(f"   Score: {ec.score}")
        lines.append(f"   Event: {ec.nonlocalizedevent.event_id}")
        lines.append("")

    return JsonResponse({"text": "\n".join(lines)})
