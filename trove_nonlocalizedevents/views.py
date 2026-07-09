import json
from django_filters.views import FilterView
from django.core.cache import cache
from django.core.paginator import Paginator
from django.shortcuts import redirect
from django.urls import reverse
from django.http import JsonResponse
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic.base import View

from trove_targets.models import Target
from tom_targets.models import TargetExtra
from tom_targets.permissions import targets_for_user
from tom_nonlocalizedevents.models import NonLocalizedEvent, EventCandidate
from scoring.models import ScoreFactor
from scoring.util import get_event_candidate_scores
from tom_dataproducts.models import ReducedDatum

from astropy.coordinates import SkyCoord
from astropy.time import Time

from .forms import EventCandidateSearchForm, CreateEventCandidateFromNLEForm

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

        # Filter by target name if provided
        target_name = self.request.GET.get("target__name")
        if target_name:
            qs = qs.filter(target__name__icontains=target_name)

        return qs

    def get_template_names(self):
        if self.request.htmx:
            return ["trove_nonlocalizedevents/candidate_table.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        agn_toggle = cache.get("agn_toggle", True)

        # Create cache key from filters (excluding page number)
        query_params = self.request.GET.copy()
        query_params.pop("page", None)
        filter_key = query_params.urlencode()
        cache_key = f"event_candidates_scored_{filter_key}_{agn_toggle}"

        # Check cache first (ToggleAgnCacheView pre-warms this key for the
        # current NLE when the AGN toggle is flipped)
        scored_candidates = cache.get(cache_key)
        if scored_candidates is None:
            # Not in cache—score all candidates
            all_candidates = self.filterset.qs
            scored_candidates = get_event_candidate_scores(all_candidates, agn_toggle=agn_toggle)
            # Cache for 5 minutes
            cache.set(cache_key, scored_candidates, 60 * 5)

        # Paginate the cached scored list
        paginator = Paginator(scored_candidates, self.paginate_by)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context["page_obj"] = page_obj
        context["object_list"] = page_obj.object_list
        context["agn_toggle"] = agn_toggle

        nle_id = self.request.GET.get("nonlocalizedevent")
        context["eventcandidate_filter_form"] = EventCandidateSearchForm(nle_id=nle_id)
        context["eventcandidate_create_form"] = CreateEventCandidateFromNLEForm()

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


class EventCandidateCreateFromNLEView(LoginRequiredMixin, View):
    """
    Handles the form submission and redirects to EventCandidateCreateView
    """

    def post(self, request, *args, **kwargs):
        form = CreateEventCandidateFromNLEForm(request.POST)

        if form.is_valid():
            target_id = (
                Target.objects.filter(name=form.cleaned_data["target_name_to_link"])
                .first()
                .id
            )
            event_id = NonLocalizedEvent.objects.get(
                id=request.GET.get("nonlocalizedevent")
            ).event_id

            # Redirect to the create-candidate view
            return redirect(
                "custom_code:create-candidate",
                event_id=event_id,
                target_id=target_id,
            )

        # If form is invalid, redirect back or re-render
        return redirect(request.META.get("HTTP_REFERER", "/"))


def generate_report(request):
    nle_id = request.GET.get("nonlocalizedevent")
    try:
        ncands = int(request.GET.get("n", 10))
    except ValueError:
        ncands = 10  # this means the user didn't pass an integer to the n param

    candidates = EventCandidate.objects.filter(
        nonlocalizedevent_id=nle_id
    ).select_related("target", "nonlocalizedevent")

    candidates = list(get_event_candidate_scores(candidates, agn_toggle=False))  # [:ncands]

    nle_name = NonLocalizedEvent.objects.get(id=nle_id)

    text = f"""
We analyzed candidate counterparts to the LIGO/Virgo/KAGRA (LVK) gravitational wave (GW) event {nle_name} using the Multi-messenger Tool for Rapid Object Vetting and Examination (TROVE). We searched within the 95th percentile localization region for candidate optical counterparts in host galaxies at the approximate luminosity distance of {nle_name}. We further crossmatch to minor planet, point source, and AGN catalogs and rule out sources that do not appear photometrically similar to kilonova light curves. For additional details, see the vetting procedures described in N. Franz, et al., 2025, arXiv:2510.17104.

Below, we report the top {ncands} candidates that remain viable after running our vetting procedure using publicly available information on all publicly reported sources, to date, on the Transient Name Server (TNS).  We include their TNS identifier, instrument with earliest detection, coordinates, cumulative probability at the coordinate location in the latest LVK map, most likely host redshift, joint GW luminosity distance and candidate redshift probability, most recent magnitude, epoch of that most recent magnitude, TROVE KN score. Candidates are ranked using a scoring procedure designed to identify kilonova counterparts to GW events (N. Franz, et al., 2025, arXiv:2510.17104). The reported candidates are not clearly identified as kilonovae.

| Name | Initial Detecting Instrument | RA [HMS] | Dec [DMS] | Localization Probability Contour | Most Likely Host-z | Joint Distance Probability | Most Recent Mag | Most Recent Mag Time [MJD] | TROVE KN Score |
| :------- | :------: | -------: | -------: | -------: | -------: | -------: | -------: | -------: | -------: |"""

    subscore_keys_to_report = ["skymap_score", "host_distance_score"]

    lines = [text]
    for i, ec in enumerate(candidates, 1):
        if i > ncands:
            break

        # get target info
        t = ec.target
        ra, dec = (
            SkyCoord(t.ra, t.dec, unit="deg")
            .to_string("hmsdms", precision=2)
            .split(" ")
        )

        # get subscore info
        sf = ScoreFactor.objects.filter(
            event_candidate=ec, key__in=subscore_keys_to_report
        )

        try:
            loc_prob = f"{float(sf.filter(key='skymap_score').first().value):.2f}"
        except AttributeError:
            loc_prob = None

        try:
            host_score = (
                f"{float(sf.filter(key='host_distance_score').first().value):.2f}"
            )
        except AttributeError:
            host_score = None

        # get details of the best matching host galaxy
        try:
            host_info = json.loads(
                TargetExtra.objects.filter(target_id=t.id, key="Host Galaxies")
                .first()
                .value
            )
            if isinstance(host_info, list):
                # this is if there are multiple hosts, otherwise host_info is already a
                # dict with the most likely info
                host_info = host_info[0]  # the first is the most likely because we sort

            host_str = f"{float(host_info['z']):.3f} ({host_info['Source']} {host_info['z_type']})"

        except (AttributeError, IndexError):
            host_str = None

        except KeyError:
            import pdb

            pdb.set_trace()

        # get photometry info
        first_phot = ReducedDatum.objects.filter(
            target_id=t.id, value__magnitude__isnull=False, value__error__isnull=False
        ).first()
        if first_phot:
            v = first_phot.value
            src_first = first_phot.source_name
            if "instrument" in v:
                src_str_first = f"{src_first}; {v['instrument']} {v['filter']}"
            elif "telescope" in v:
                src_str_first = f"{src_first}; {v['telescope']} {v['filter']}"
            else:
                src_str_first = f"{src_first}; {v['filter']}"
            src_str_first = src_str_first.replace(
                " (TNS)", ""
            )  # strip (TNS) if present
        else:
            src_str_first = None

        latest_phot = ReducedDatum.objects.filter(target_id=t.id).latest()
        if latest_phot:
            v = latest_phot.value
            src_latest = latest_phot.source_name

            if "instrument" in v:
                src_str_latest = f"({v['instrument']} {v['filter']}; {src_latest})"
            elif "telescope" in v:
                src_str_latest = f"({v['telescope']} {v['filter']}; {src_latest})"
            else:
                src_str_latest = f"({v['filter']}; {src_latest})"

            if "magnitude" in v:  # detection
                phot_str_latest = (
                    f"{v['magnitude']:.2f} +/- {v['error']:.2f} {src_str_latest}"
                )
            else:  # non-detection
                phot_str_latest = f">{v['limit']:.2f} {src_str_latest}"

            epoch_latest = Time(latest_phot.timestamp).mjd
            epoch_str_latest = f"{float(epoch_latest):.5f}"

        else:
            phot_str_latest = None
            epoch_str_latest = None
        # TODO: Currently we are defaulting to reporting the KN score, this should
        #       probably be fixed once we support BBH vetting!
        lines.append(
            f"| {t.name} | {src_str_first} | {ra} | {dec} | {loc_prob} | {host_str} | {host_score} | {phot_str_latest} | {epoch_str_latest} | {float(ec.score['KN']):.2f} |"
        )

    lines.append(
        f"""
We encourage additional follow up of these candidates to determine whether they remain viable counterparts to {nle_name}."""
    )

    return JsonResponse({"text": "\n".join(lines)})


class ToggleAgnCacheView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        new_val = not cache.get("agn_toggle", True)
        cache.set("agn_toggle", new_val)

        nle_id = request.GET.get("nonlocalizedevent")
        if nle_id:
            candidates = EventCandidate.objects.filter(
                nonlocalizedevent_id=nle_id
            ).select_related("target", "nonlocalizedevent")
            scored_candidates = get_event_candidate_scores(candidates, agn_toggle=new_val)

            # Re-sores all candidates after AGN-toggle change and saves to cache
            cache_key = f"event_candidates_scored_nonlocalizedevent={nle_id}_{new_val}"
            cache.set(cache_key, scored_candidates, 60 * 5)
            return redirect(reverse("custom_code:event-candidates") + f"?nonlocalizedevent={nle_id}")
        return redirect(reverse("custom_code:event-candidates"))


class RefreshCandidateList(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        nle_id = request.GET.get("nonlocalizedevent")
        if nle_id:
            return redirect(reverse('custom_code:event-candidates') + f'?nonlocalizedevent={nle_id}')
        return redirect(reverse('curstom_code:event-candidates'))