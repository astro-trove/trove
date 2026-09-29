import json
from django_filters.views import FilterView
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Min, Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.http import JsonResponse, HttpResponse, QueryDict
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.generic.base import View
from django.contrib import messages

from trove_targets.models import Target
from tom_targets.models import TargetExtra
from tom_targets.utils import cone_search_filter
from tom_nonlocalizedevents.models import NonLocalizedEvent, EventCandidate
from trove_nonlocalizedevents.permissions import candidates_for_user
import logging

from scoring.models import ScoreFactor
from scoring.util import (
    get_event_candidate_scores,
    get_last_vet_all_run,
    get_no_score_message,
    get_vet_all_progress,
    kilonova_scores_exist,
    most_likely_class_for_event,
    KN_STYLE_CLASSES,
)
from scoring.phot_method import (
    PHOT_METHOD_CHOICES,
    get_phot_method,
    phot_method_label,
    toggle_phot_method,
)
logger = logging.getLogger(__name__)
from tom_dataproducts.models import ReducedDatum
from custom_code.templatetags.skymap_extras import skymap, get_preferred_localization

from astropy.coordinates import SkyCoord
from astropy.time import Time

from .forms import EventCandidateSearchForm, CreateEventCandidateFromNLEForm


#: How long a scored candidate list is held for. A "Vet All" run rewrites those
#: scores continuously, so while one is going it is held for less.
SCORE_CACHE_PERIOD = 60 * 5
SCORE_CACHE_PERIOD_WHILE_VETTING = 60

def scored_candidates_cache_key(query_params, agn_toggle, phot_method):
    """
    Cache key for the scored candidate list matching a set of filters.

    Everything that reads, writes or invalidates that cache goes through here,
    so the three cannot drift apart and leave the page serving scores nothing
    can clear.

    ``phot_method`` belongs in the key because it decides which stored factor
    each row displays: a list scored under the other method is stale, not merely
    older. ``agn_toggle`` is in it for the same reason.
    """
    query_params = query_params.copy()
    query_params.pop("page", None)  # every page shares one scored list
    # score_min is applied to the scored list rather than the queryset, so every
    # threshold can share one scoring run instead of forcing a fresh one
    query_params.pop("score_min", None)
    return (f"event_candidates_scored_{query_params.urlencode()}"
            f"_{agn_toggle}_{phot_method}")


#: z_type strings that count as each kind of distance measurement, as stored in
#: the "Host Galaxies" TargetExtra. "user spec-z" is a spectroscopic redshift too.
DISTANCE_TYPE_PATTERNS = {
    "spec-z": ["spec-z", "user spec-z"],
    "photo-z": ["photo-z"],
    "z-ind": ["z ind.", "z-ind."],
}


def _as_float(value):
    """A query-string number, or None if it is absent or not a number."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_date(value):
    """A query-string YYYY-MM-DD date, or None.

    `parse_date` returns None for a malformed string but raises for a
    well-formed impossible one like 2025-13-45; a half-typed date in the box
    must not 500 the page either way.
    """
    if not value:
        return None
    try:
        return parse_date(value)
    except ValueError:
        return None


def invalidate_scored_candidates_cache(query_params):
    """
    Drop the cached scored candidate lists for a set of filters.
    """
    if not hasattr(query_params, "copy"):  # an NLE id: build the unfiltered key
        query_params = QueryDict(urlencode({"nonlocalizedevent": query_params}))

    for agn_toggle in (True, False):
        for phot_method in PHOT_METHOD_CHOICES:
            cache.delete(scored_candidates_cache_key(
                query_params, agn_toggle, phot_method))


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
        # the skymap above the table draws the same set -- see `candidates_for_user`
        qs = candidates_for_user(self.request.user, super().get_queryset()).select_related(
            "target", "nonlocalizedevent"
        )

        # Filter by nonlocalizedevent if provided in URL
        nonlocalizedevent_id = self.request.GET.get("nonlocalizedevent")
        if nonlocalizedevent_id:
            qs = qs.filter(nonlocalizedevent_id=nonlocalizedevent_id)

        # Filter by target name if provided
        target_name = self.request.GET.get("target__name")
        if target_name:
            qs = qs.filter(target__name__icontains=target_name)

        return self.apply_candidate_filters(qs)

    def apply_candidate_filters(self, qs):
        """The filters that can be answered from the database.

        Score is not one of them: scores are computed in Python, so that filter
        is applied to the scored list in `get_context_data`.
        """
        get = self.request.GET

        distance_max = _as_float(get.get("distance_max"))
        if distance_max is not None:
            qs = qs.filter(target__distance__lte=distance_max)

        # first detection == earliest photometry point, as in
        # custom_code.hooks.associate_targets_with_nle
        after = _as_date(get.get("first_det_after"))
        before = _as_date(get.get("first_det_before"))
        if after or before:
            first_det = (
                ReducedDatum.objects.filter(
                    data_type="photometry", value__magnitude__isnull=False
                )
                .values("target_id")
                .annotate(min_timestamp=Min("timestamp"))
            )
            if after:
                first_det = first_det.filter(min_timestamp__date__gte=after)
            if before:
                first_det = first_det.filter(min_timestamp__date__lte=before)
            qs = qs.filter(target_id__in=first_det.values_list("target_id", flat=True))

        # How the host galaxy's distance was measured. z_type lives inside the
        # "Host Galaxies" TargetExtra JSON, so this matches the stored text
        # (compact separators, no space after the colon). A target counts if any
        # of its host galaxies was measured that way.
        distance_type = get.get("distance_type")
        if distance_type in DISTANCE_TYPE_PATTERNS:
            match = Q()
            for pattern in DISTANCE_TYPE_PATTERNS[distance_type]:
                match |= Q(value__icontains=f'"z_type":"{pattern}')
            host_targets = TargetExtra.objects.filter(
                Q(key="Host Galaxies") & match
            ).values_list("target_id", flat=True)
            qs = qs.filter(target_id__in=host_targets)

        ra, dec = _as_float(get.get("cone_ra")), _as_float(get.get("cone_dec"))
        if ra is not None and dec is not None:
            radius = _as_float(get.get("cone_radius")) or 2.0  # arcsec, as in scoring.api
            targets = cone_search_filter(
                Target.objects.filter(id__in=qs.values_list("target_id", flat=True)),
                ra, dec, radius / 3600.0,
            )
            qs = qs.filter(target_id__in=targets.values_list("id", flat=True))

        return qs

    def get_template_names(self):
        if self.request.htmx:
            return ["trove_nonlocalizedevents/candidate_table_body.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        agn_toggle = cache.get("agn_toggle", True)
        nle_id = self.request.GET.get("nonlocalizedevent")

        phot_method = get_phot_method()

        vet_all_progress = get_vet_all_progress(nle_id)

        cache_key = scored_candidates_cache_key(self.request.GET, agn_toggle, phot_method)

        # Check cache first (ToggleAgnCacheView pre-warms this key for the
        # current NLE when the toggle is flipped)
        scored_candidates = cache.get(cache_key)
        if scored_candidates is None:
            # Not in cache—score all candidates
            all_candidates = self.filterset.qs
            scored_candidates = get_event_candidate_scores(
                all_candidates, agn_toggle=agn_toggle, phot_method=phot_method,
            )
            # a run in progress rewrites these scores continuously, so hold them
            # for less time than usual to keep the page closer to the truth
            if vet_all_progress and vet_all_progress["running"]:
                cache_timeout = SCORE_CACHE_PERIOD_WHILE_VETTING
            else:
                cache_timeout = SCORE_CACHE_PERIOD
            cache.set(cache_key, scored_candidates, cache_timeout)

        # Scores are computed in Python, so this filter cannot be a queryset
        # filter; it runs on the scored list, after the cache.
        score_min = _as_float(self.request.GET.get("score_min"))
        if score_min is not None:
            scored_candidates = [
                candidate for candidate in scored_candidates
                if max((candidate.score or {}).values(), default=0) >= score_min
            ]

        # Paginate the cached scored list
        paginator = Paginator(scored_candidates, self.paginate_by)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context["page_obj"] = page_obj
        context["object_list"] = page_obj.object_list
        context["agn_toggle"] = agn_toggle

        # One column per transient type rather than every score stacked into a
        # single cell. Events scored for one type only then say which it is.
        score_columns = []
        for candidate in page_obj.object_list:
            for transient in (candidate.score or {}):
                if transient not in score_columns:
                    score_columns.append(transient)
        context["score_columns"] = score_columns

        context["phot_method"] = phot_method
        context["phot_method_label"] = phot_method_label()

        context["vet_all_progress"] = vet_all_progress
        # standing record of when these scores were last refreshed in bulk,
        # which outlives the transient progress notice above
        context["last_vet_all"] = get_last_vet_all_run(nle_id)

        context["eventcandidate_filter_form"] = EventCandidateSearchForm(
            self.request.GET or None, nle_id=nle_id)
        # open the extra filters if any of them are in play, so an active filter
        # is never hidden behind a collapsed panel
        context["filters_active"] = any(
            self.request.GET.get(f) for f in
            ("score_min", "distance_max", "distance_type", "first_det_after",
             "first_det_before", "cone_ra", "cone_dec", "cone_radius")
        )
        context["eventcandidate_create_form"] = CreateEventCandidateFromNLEForm(nle_id=nle_id)

        context["no_score_message"] = None
        event_class = None
        if nle_id:
            nle = NonLocalizedEvent.objects.filter(id=nle_id).first()
            if nle:
                event_class = most_likely_class_for_event(nle.event_id)
                context["no_score_message"] = get_no_score_message(event_class)

        # shown whichever way the toggle is set: the toggle stays locked on light
        # curve metrics until the event has KilonovaSCORER scores. KN-style events
        # only, since KilonovaSCORER means nothing for BBH/AGN-flare scoring
        context["kilonova_scores_missing"] = (
            event_class in KN_STYLE_CLASSES and bool(scored_candidates)
            and not kilonova_scores_exist(nonlocalizedevent_id=int(nle_id))
        )

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
            target_id = form.cleaned_data["target_name_to_link"].id

            try:
                event_id = NonLocalizedEvent.objects.get(
                    id=request.GET.get("nonlocalizedevent")
                ).event_id
            except (NonLocalizedEvent.DoesNotExist, ValueError):
                messages.error(request, "Could not tell which event to link that target to.")
                return redirect(request.META.get("HTTP_REFERER", "/"))

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

| Name | Initial Detecting Instrument | RA [HMS] | Dec [DMS] | Localization Probability Contour | Most Likely Host-z | Joint Distance Probability | Most Recent Mag | Most Recent Mag Time [MJD] | TROVE Score |
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
        # report whichever transient type this candidate scored best as -- for KN
        # events that's (typically) "KN", but a BBH event's ec.score only ever has
        # an "AGN-flare" key (see scoring/util.py's most_likely_class branching), so
        # a hardcoded ec.score["KN"] would KeyError for every BBH candidate
        best_score = max(ec.score.values()) if ec.score else float("nan")
        lines.append(
            f"| {t.name} | {src_str_first} | {ra} | {dec} | {loc_prob} | {host_str} | {host_score} | {phot_str_latest} | {epoch_str_latest} | {best_score:.2f} |"
        )

    lines.append(
        f"""
We encourage additional follow up of these candidates to determine whether they remain viable counterparts to {nle_name}."""
    )

    return JsonResponse({"text": "\n".join(lines)})


def _return_to(request, fallback):
    nxt = request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return nxt
    return fallback


class ToggleAgnCacheView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        new_val = not cache.get("agn_toggle", True)
        cache.set("agn_toggle", new_val)

        nle_id = request.GET.get("nonlocalizedevent")
        if nle_id:
            candidates = EventCandidate.objects.filter(
                nonlocalizedevent_id=nle_id
            ).select_related("target", "nonlocalizedevent")
            phot_method = get_phot_method()
            scored_candidates = get_event_candidate_scores(
                candidates, agn_toggle=new_val, phot_method=phot_method,
            )

            # Re-scores all candidates after AGN-toggle change and saves to
            # cache. The key must match the one the list view builds -- every
            # other toggle's *current* value included -- otherwise this
            # pre-warm writes a key nothing reads and the list re-scores anyway.
            cache_key = scored_candidates_cache_key(
                QueryDict(f"nonlocalizedevent={nle_id}"), new_val, phot_method,
            )
            cache.set(cache_key, scored_candidates, SCORE_CACHE_PERIOD)
            params = {"nonlocalizedevent": nle_id}
            return redirect(_return_to(
                request,
                reverse("custom_code:event-candidates") + "?" + urlencode(params),
            ))
        return redirect(_return_to(request, reverse("custom_code:event-candidates")))


class TogglePhotMethodCacheView(LoginRequiredMixin, View):
    """Flip the photometry scorer between TROVE and KilonovaSCORER.

    Deliberately lighter than :class:`ToggleAgnCacheView`, which rescores the
    whole candidate list on every press. This one only writes the cache key --
    no rescoring, no vetting queued, no stored ``ScoreFactor`` row touched.
    The next Vet All reads the value and uses it.

    That difference is the point. The AGN flag changes an arithmetic factor
    already held in memory, so recomputing is cheap. Switching photometry
    scorer would mean re-running KilonovaSCORER against the simulation grid for
    every candidate -- minutes of compute, triggered by a single click, on a
    page a user may only be browsing.
    """

    def get(self, request, *args, **kwargs):
        new_val = toggle_phot_method()
        logger.info("Photometry scoring method switched to %r", new_val)

        nle_id = request.GET.get("nonlocalizedevent")
        # No rescoring -- but the cached scored list was built displaying the
        # OTHER method's factor, so it has to go or the page keeps showing the
        # old numbers under the new label. The method is part of the cache key,
        # so the entry for the new method is simply absent and gets rebuilt from
        # stored ScoreFactor rows: a read, not a re-vet.
        url = reverse("custom_code:event-candidates")
        params = {}
        if nle_id:
            params["nonlocalizedevent"] = nle_id
        if params:
            url += "?" + urlencode(params)
        return redirect(_return_to(request, url))


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
    """
    Throw away the cached scores for the current candidate list and reload it.

    The reload on its own was not a refresh: it sent the user back to a page
    that served its scores straight out of the cache for up to five minutes,
    which is precisely the wrong answer while a "Vet All" run is rewriting
    those scores underneath.
    """

    def get(self, request, *args, **kwargs):
        invalidate_scored_candidates_cache(request.GET)
        # Both toggles are site-wide and either of them can be flipped by
        # anyone, so clear every combination rather than only the one currently
        # selected -- otherwise a refresh leaves a stale list behind whichever
        # toggle the next viewer happens to be on.
        for agn_toggle in (True, False):
            for phot_method in PHOT_METHOD_CHOICES:
                cache.delete(scored_candidates_cache_key(
                    request.GET, agn_toggle, phot_method))

        # send the user back to the list they were looking at, filters and all
        query_string = request.GET.urlencode()
        url = reverse("custom_code:event-candidates")
        if query_string:
            url += f"?{query_string}"
        return redirect(url)


class VetAllProgressPartialView(View):
    """
    Just the "Vet All" progress notice.

    The candidate list polls this while a run is going so the notice keeps up
    with the queue, which costs three counts, rather than re-scoring every
    candidate on the page.
    """

    def get(self, request, *args, **kwargs):
        nle_id = request.GET.get("nonlocalizedevent")

        # This view answers with a fragment, which is not a page. Anyone who
        # arrives here directly -- a reload, a bookmark, a stale history entry --
        # wants the candidate list, so send them there rather than showing them
        # a stray notice on a blank page.
        if not getattr(request, "htmx", False):
            url = reverse("custom_code:event-candidates")
            if nle_id:
                url += f"?nonlocalizedevent={nle_id}"
            return redirect(url)

        return render(
            request,
            "trove_nonlocalizedevents/partials/vet_all_progress.html",
            {"vet_all_progress": get_vet_all_progress(nle_id)},
        )

def vet_all_cooldown_notice(request):
    messages.warning(
        request,
        "A user has recently run vetting on all candidates, placing it on "+
        "cooldown. The vetting results will update for all users. Please try "+
        "again later if you need to re-vet *everything* again (you can still "+
        "vet individual targets via the target pages)."
    )
    return redirect(request.META.get('HTTP_REFERER', '/'))
