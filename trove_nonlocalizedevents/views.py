import json
from django_filters.views import FilterView
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.urls import reverse
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic.base import View

from trove_targets.models import Target
from tom_targets.models import TargetExtra
from tom_targets.permissions import targets_for_user
from tom_nonlocalizedevents.models import NonLocalizedEvent, EventCandidate
from scoring.models import ScoreFactor
from scoring.phot_method import (
    KILONOVA,
    TROVE,
    get_kilonova_params,
    get_phot_method,
    scored_candidates_cache_key,
    set_phot_method,
)
from scoring.util import get_event_candidate_scores
from tom_dataproducts.models import ReducedDatum
from custom_code.templatetags.skymap_extras import skymap, get_preferred_localization

from astropy.coordinates import SkyCoord
from astropy.time import Time

from .forms import (
    EventCandidateSearchForm,
    CreateEventCandidateFromNLEForm,
)


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
            return ["trove_nonlocalizedevents/candidate_table_body.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        agn_toggle = cache.get("agn_toggle", True)

        # The photometry method belongs to the event being viewed, not the site:
        # a KilonovaSCORER run scores one event's candidates, and only that
        # event has kilonova_score rows. Without an event in the URL this is the
        # cross-event list, which has no run behind it and stays on TROVE.
        nle_id = self.request.GET.get("nonlocalizedevent")
        phot_method = get_phot_method(nle_id)

        # Create cache key from filters (excluding page number)
        query_params = self.request.GET.copy()
        query_params.pop("page", None)
        filter_key = query_params.urlencode()
        cache_key = scored_candidates_cache_key(filter_key, agn_toggle, phot_method)

        # Check cache first (ToggleAgnCacheView pre-warms this key for the
        # current NLE when the AGN toggle is flipped)
        scored_candidates = cache.get(cache_key)
        if scored_candidates is None:
            # Not in cache—score all candidates
            all_candidates = self.filterset.qs
            scored_candidates = get_event_candidate_scores(
                all_candidates, agn_toggle=agn_toggle, phot_method=phot_method
            )
            # Cache for 5 minutes
            cache.set(cache_key, scored_candidates, 60 * 5)

        # Paginate the cached scored list
        paginator = Paginator(scored_candidates, self.paginate_by)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context["page_obj"] = page_obj
        context["object_list"] = page_obj.object_list
        context["phot_method"] = phot_method
        # the KNS column of the score cell; the toggles and the run status come
        # from {% scoring_controls %}, which reads its own state
        context["phot_method_is_kilonova"] = phot_method == KILONOVA

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

    # pinned to the TROVE method, not the site-wide setting: the report text
    # below describes that procedure by name and cites the paper it comes from,
    # so the numbers in the table have to be the ones it is describing
    candidates = list(
        get_event_candidate_scores(candidates, agn_toggle=False, phot_method=TROVE)
    )  # [:ncands]

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


def scoring_control_redirect(request, nle_id=None):
    """Where a scoring toggle sends you after it has done its work.

    Back to the page the toggle was clicked on. The controls are a pluggable
    module (``{% scoring_controls %}``) that may be rendered on any page, so
    they cannot assume the candidate list -- landing somewhere else would make
    them unusable everywhere but there. The referer is checked against the
    site's own hosts before being trusted, since it is attacker-controllable.

    The candidate list is the fallback for a missing or foreign referer.
    """
    referer = request.META.get("HTTP_REFERER")
    if referer and url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return referer

    url = reverse("custom_code:event-candidates")
    if nle_id:
        url += f"?nonlocalizedevent={nle_id}"
    return url


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
            cache_key = scored_candidates_cache_key(
                f"nonlocalizedevent={nle_id}", new_val
            )
            cache.set(cache_key, scored_candidates, 60 * 5)
        return redirect(scoring_control_redirect(request, nle_id))


class TogglePhotScoringMethodView(LoginRequiredMixin, View):
    """Record which photometry method one event's candidate list should rank by.

    **This starts no computation.** It writes the choice to the cache and
    returns; the KilonovaSCORER run is launched by ``Vet All``
    (:class:`scoring.views.TargetVettingAllView`), which reads the choice back.

    That split is the whole point of the control. Scoring a large event is tens
    of minutes, so a user setting up a comparison wants to flip this, change
    the AGN toggle, adjust whatever else, and only then pay for one pass over
    the candidates. Kicking off a run per click would charge them for every
    intermediate state they never intended to keep.

    Flipping to KilonovaSCORER before any run has happened is therefore
    harmless but also inert: with no ``kilonova_score`` rows to read, every
    candidate falls back to the TROVE photometry check
    (:func:`scoring.util.get_event_candidate_scores`) until Vet All is pressed.
    """

    def get(self, request, *args, **kwargs):
        nle_id = request.GET.get("nonlocalizedevent")
        redirect_url = scoring_control_redirect(request, nle_id)
        if not nle_id:
            # the setting belongs to an event; the cross-event list has none
            messages.warning(
                request,
                "The photometry scoring method is set per non-localized event. "
                "Open an event's candidate list and toggle it there.",
            )
            return redirect(redirect_url)

        # No messages here. The toggle is reached over htmx (the candidate list
        # is inside an hx-boost container and serves candidate_table_body.html
        # to htmx requests), and that fragment does not render
        # {% bootstrap_messages %} -- which lives in the base template. Django
        # only consumes a message when something iterates it, so anything queued
        # here would survive the swap, pile up in the session, and land as a
        # stack of blue alerts on the next full page load. The button's own
        # label already reports the method.
        new_method = TROVE if get_phot_method(nle_id) == KILONOVA else KILONOVA
        # the parameters are stored with the choice so the run Vet All starts
        # uses whatever was configured at the time the method was picked
        params = get_kilonova_params(nle_id) if new_method == KILONOVA else None
        set_phot_method(new_method, nle_id, params)
        return redirect(redirect_url)


class SkymapPartialView(View):
    def get(self, request, *args, **kwargs):
        nle_id = request.GET.get("nonlocalizedevent")
        if not nle_id:
            return HttpResponse("")
        nle = NonLocalizedEvent.objects.get(id=nle_id)
        localization = get_preferred_localization(nle)
        if localization is None:
            return HttpResponse("<p>No Skymap Found</p>")
        context = skymap({"request": request}, localization)
        return render(request, "tom_nonlocalizedevents/partials/skymap.html", context)


class RefreshCandidateList(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        nle_id = request.GET.get("nonlocalizedevent")
        if nle_id:
            return redirect(reverse('custom_code:event-candidates') + f'?nonlocalizedevent={nle_id}')
        return redirect(reverse('curstom_code:event-candidates'))