import json
import logging
from urllib.parse import urlparse

from astropy.coordinates import SkyCoord
from astropy.time import Time
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Min, Prefetch, Q
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.generic.base import RedirectView, View
from django_filters.views import FilterView
from tom_dataproducts.models import PhotometryReducedDatum
from tom_nonlocalizedevents.models import EventCandidate, EventSequence, NonLocalizedEvent
from tom_targets.models import TargetExtra
from tom_targets.permissions import targets_for_user
from tom_targets.utils import cone_search_filter

from scoring.models import ScoreFactor
from scoring.phot_method import (
    PHOT_METHOD_CHOICES,
    get_phot_method,
    phot_method_label,
    toggle_phot_method,
)
from scoring.util import (
    KN_STYLE_CLASSES,
    get_agn_toggle,
    get_event_candidate_scores,
    get_last_vet_multi_run,
    get_last_vet_per_candidate,
    get_vet_multi_progress,
    host_distances,
    kilonova_scores_exist,
    latest_score_factors,
    most_likely_class_for_event,
    set_agn_toggle,
)
from scoring.unified_scorer import score
from scoring.views import methods_for_event
from trove_nonlocalizedevents.permissions import (
    candidates_for_user,
    nonlocalizedevents_for_user
)
from trove_nonlocalizedevents.templatetags.skymap_extras import (
    get_preferred_localization,
    skymap
)

from trove_targets.models import Target

from .filters import GWFilter, NeutrinoFilter
from .forms import CreateEventCandidateFromNLEForm, EventCandidateSearchForm, GWFormHelper, NeutrinoFormHelper

logger = logging.getLogger(__name__)

#: How long a scored candidate list is held for. A "Vet All" run rewrites those
#: scores continuously, so while one is going it is held for less.
SCORE_CACHE_PERIOD = 60 * 5
SCORE_CACHE_PERIOD_WHILE_VETTING = 60

def scored_candidates_cache_key(query_params, agn_toggle, phot_method):
    """
    Cache key for the scored candidate list matching a set of filters.
    """
    query_params = query_params.copy()
    query_params.pop("page", None)  # every page shares one scored list
    query_params.pop("sort", None)
    return (f"event_candidates_scored_{query_params.urlencode()}"
            f"_{agn_toggle}_{phot_method}")


# z_type strings that count as each kind of distance measurement, as stored in
# the "Host Galaxies" TargetExtra. "user spec-z" is a spectroscopic redshift too.
# some JSON configs escape the / character
DISTANCE_TYPE_PATTERNS = {
    "spec-z": ["spec-z", "user spec-z", "spec-z/z-ind.", r"spec-z\/z-ind."],
    "photo-z": ["photo-z"],
    "z-ind.": ["z-ind.", "spec-z/z-ind.", r"spec-z\/z-ind."],
}

#: sub-score columns, chosen by how the event is scored. agn_score is left out
#: because the AGN toggle can take it out of the total it would appear to explain.
#: The 2D and distance scores are left out as well: the table already carries the
#: RA/Dec and the distance they are computed from, and a candidate's page has them.
SUBSCORE_COLUMNS_BBH = ["agn_flare_score", "nuclear_offset_score"]
SUBSCORE_COLUMNS_KN = []
SUBSCORE_LABELS = {
    "agn_flare_score": "Flare Score",
    "nuclear_offset_score": "Nuclear Offset Score",
}


#: sort key for the distance column, which is a measurement rather than a score
#: and so is not a ScoreFactor key like the columns above
DISTANCE_SORT_KEY = "distance_estimate"


def subscore_columns_for(event_class):
    """The sub-score columns worth showing for this kind of event."""
    return SUBSCORE_COLUMNS_BBH if event_class == "BBH" else SUBSCORE_COLUMNS_KN


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


def within_max_distance(candidates, distances, distance_max):
    """The candidates inside a distance cut, as the Distance column measures it.

    Not a queryset filter: the distance is read back out of the host galaxy that
    vetting recorded, so it is only known in Python. `target.distance` is a
    different quantity -- the target's own redshift distance, which is set for a
    small minority of candidates -- and filtering on it dropped rows the column
    was showing as nearby.
    """
    if distance_max is None:
        return candidates
    # no known distance cannot be shown to be within the cut, so it does not pass
    return [c for c in candidates
            if c.id in distances and distances[c.id].distance <= distance_max]


def filter_candidates(qs, get):
    """The filters that can be answered from the database.

    Shared by the candidate table and by "select all matching", so the rows you
    are shown and the rows you vet are chosen the same way. Score and distance
    are not here: both are computed in Python, so they run on the scored list.
    """

    # first detection == earliest photometry point, as in
    after = _as_date(get.get("first_det_after"))
    before = _as_date(get.get("first_det_before"))
    if after or before:
        first_det = (
            PhotometryReducedDatum.objects.filter(brightness__isnull=False)
            .values("target_id")
            .annotate(min_timestamp=Min("timestamp"))
        )
        if after:
            first_det = first_det.filter(min_timestamp__date__gte=after)
        if before:
            first_det = first_det.filter(min_timestamp__date__lte=before)
        qs = qs.filter(target_id__in=first_det.values_list("target_id", flat=True))

    # z_type lives inside the "Host Galaxies" TargetExtra JSON, so match the
    # stored text (compact separators); any host measured that way counts
    distance_type = get.get("distance_type")
    if distance_type in DISTANCE_TYPE_PATTERNS:
        match = Q()
        for pattern in DISTANCE_TYPE_PATTERNS[distance_type]:
            match |= Q(value__icontains=f'"z_type":"{pattern}"')
        host_targets = TargetExtra.objects.filter(
            Q(key="Host Galaxies") & match
        ).values_list("target_id", flat=True)
        qs = qs.filter(target_id__in=host_targets)

    ra, dec = _as_float(get.get("cone_ra")), _as_float(get.get("cone_dec"))
    if ra is not None and dec is not None:
        # `or` would turn an explicit 0 into the default; 0 means 0
        radius = _as_float(get.get("cone_radius"))
        if radius is None:
            radius = 2.0  # arcsec, as in scoring.api
        targets = cone_search_filter(
            Target.objects.filter(id__in=qs.values_list("target_id", flat=True)),
            ra, dec, radius / 3600.0,
        )
        qs = qs.filter(target_id__in=targets.values_list("id", flat=True))

    return qs


def visible_candidates(candidates, user):
    """
    Drop candidates whose target this user may not view.
    """
    if user.is_superuser:
        return candidates
    target_ids = {c.target_id for c in candidates}
    # Fast path for the common case: an authenticated user can view anything
    # that is not PRIVATE, so with nothing restricted here there is no work to
    # do. Anonymous users are excluded because they may only see OPEN targets.
    if user.is_authenticated and not Target.objects.filter(
        id__in=target_ids, permissions=Target.Permissions.PRIVATE
    ).exists():
        return candidates
    # otherwise defer to the permission layer rather than restate its rules
    allowed = set(
        targets_for_user(
            user, Target.objects.filter(id__in=target_ids), "view_target"
        ).values_list("id", flat=True)
    )
    return [c for c in candidates if c.target_id in allowed]


class EventCandidateListView(LoginRequiredMixin, FilterView):
    """
    View for listing candidates in the TOM.
    """

    model = EventCandidate
    template_name = "trove_nonlocalizedevents/candidate_list.html"
    # We need to skip pagination for ordering, if we ever have more
    # candidates than this we have an issue...
    paginate_by = 20

    def handle_no_permission(self):
        # without this an anonymous visitor just saw an empty table, with no
        # hint that logging in was the missing piece
        messages.warning(self.request, "You must be logged in to view events' candidates.")
        return super().handle_no_permission()

    def get_queryset(self):
        """
        Gets the set of ``Candidate`` objects for this event.

        Not filtered by permission: the scored list built from this is cached
        and shared, so `visible_candidates` applies the filter on the way out.

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
        return filter_candidates(qs, self.request.GET)

    def get_template_names(self):
        if self.request.htmx:
            return ["trove_nonlocalizedevents/candidate_table_body.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        agn_toggle = get_agn_toggle(self.request)
        nle_id = self.request.GET.get("nonlocalizedevent")

        phot_method = get_phot_method(self.request)

        vet_multi_progress = get_vet_multi_progress(nle_id)

        cache_key = scored_candidates_cache_key(self.request.GET, agn_toggle,
                                                phot_method)

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
            if vet_multi_progress and vet_multi_progress["running"]:
                cache_timeout = SCORE_CACHE_PERIOD_WHILE_VETTING
            else:
                cache_timeout = SCORE_CACHE_PERIOD
            cache.set(cache_key, scored_candidates, cache_timeout)

        # Scores are computed in Python, so this filter cannot be a queryset
        # filter; it runs on the scored list, after the cache.

        # Which total score the table is sorted by. Several scores means several
        # columns, and the user picks which one orders the table.
        all_transients = []
        for candidate in scored_candidates:
            for transient in (candidate.score or {}):
                if transient not in all_transients:
                    all_transients.append(transient)
        event_class = None
        if nle_id:
            nle_for_class = NonLocalizedEvent.objects.filter(id=nle_id).first()
            if nle_for_class:
                event_class = most_likely_class_for_event(nle_for_class.event_id)
        subscore_columns = subscore_columns_for(event_class)

        # sub-scores for every scored candidate, so these columns can be sorted
        # on rather than only the ones on the current page
        subscores = {}
        if subscore_columns:
            for sf in latest_score_factors(ScoreFactor.objects.filter(
                event_candidate_id__in=[c.id for c in scored_candidates],
                key__in=subscore_columns,
            )).only("event_candidate_id", "key", "value"):
                try:
                    subscores.setdefault(
                        sf.event_candidate_id, {})[sf.key] = float(sf.value)
                except (TypeError, ValueError):
                    pass
        context["subscores"] = subscores
        context["distance_sort_key"] = DISTANCE_SORT_KEY

        sortable = all_transients + subscore_columns + [DISTANCE_SORT_KEY]
        sort_key = self.request.GET.get("sort")
        if sort_key not in sortable:
            sort_key = all_transients[0] if all_transients else None

        # Reading a distance back costs that candidate's host galaxy table, so
        # the whole list is only fetched when the column is sorted on or cut by;
        # the rest of the time the page is all that gets shown.
        distance_max = _as_float(self.request.GET.get("distance_max"))
        needs_all = sort_key == DISTANCE_SORT_KEY or distance_max is not None
        distance_estimates = host_distances(scored_candidates) if needs_all else {}
        scored_candidates = within_max_distance(
            scored_candidates, distance_estimates, distance_max)

        def sort_value(candidate):
            """Whatever the chosen column shows for this candidate."""
            if sort_key in subscore_columns:
                return subscores.get(candidate.id, {}).get(sort_key, -1)
            return (candidate.score or {}).get(sort_key, 0)

        if sort_key == DISTANCE_SORT_KEY:
            # nearest first, unlike the scores, and no distance known sorts last
            scored_candidates = sorted(scored_candidates, key=lambda c: (
                c.id not in distance_estimates,
                getattr(distance_estimates.get(c.id), "distance", 0.0),
            ))
        elif sort_key:
            scored_candidates = sorted(scored_candidates, reverse=True, key=sort_value)
        context["sort_key"] = sort_key
        # the cached list is everyone's; this viewer sees their share of it
        scored_candidates = visible_candidates(scored_candidates, self.request.user)

        # Paginate the cached scored list
        paginator = Paginator(scored_candidates, self.paginate_by)
        page_number = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context["paginator"] = paginator
        context["is_paginated"] = page_obj.has_other_pages()
        context["page_obj"] = page_obj
        context["object_list"] = page_obj.object_list
        context["agn_toggle"] = agn_toggle

        # One column per transient type rather than every score stacked into a
        # single cell. Events scored for one type only then say which it is.
        context["score_columns"] = all_transients

        # Latest magnitude for every target on this page, in one query rather
        # than one per row.
        target_ids = [c.target_id for c in page_obj.object_list]
        latest_mag = {}
        for datum in (PhotometryReducedDatum.objects
                      .filter(target_id__in=target_ids, brightness__isnull=False)
                      .order_by("target_id", "timestamp")
                      .only("target_id", "timestamp", "brightness",
                            "brightness_error", "bandpass")):
            # ordered, so the last wins
            latest_mag[datum.target_id] = {
                "magnitude": datum.brightness,
                "error": datum.brightness_error,
                "filter": datum.bandpass,
            }

        # A target with no magnitude at all has only non-detections; show the
        # deepest limit as ">20.7" rather than a blank that reads as missing data.
        undetected = [t for t in target_ids if t not in latest_mag]
        for datum in (PhotometryReducedDatum.objects
                      .filter(target_id__in=undetected, limit__isnull=False)
                      .order_by("target_id", "timestamp")
                      .only("target_id", "timestamp", "limit", "bandpass")):
            latest_mag[datum.target_id] = {
                "limit": datum.limit,
                "filter": datum.bandpass,
                "is_limit": True,
            }
        context["latest_mag"] = latest_mag

        if not needs_all:
            distance_estimates = host_distances(page_obj.object_list)
        context["distance_estimates"] = distance_estimates

        context["subscore_columns"] = subscore_columns
        context["subscore_labels"] = SUBSCORE_LABELS

        context["last_vet_timestamp"] = get_last_vet_per_candidate(scored_candidates)

        context["phot_method"] = phot_method
        context["phot_method_label"] = phot_method_label(request=self.request)

        context["vet_multi_progress"] = vet_multi_progress
        # standing record of when these scores were last refreshed in bulk,
        # which outlives the transient progress notice above
        context["last_vet_multi"] = get_last_vet_multi_run(nle_id)

        context["eventcandidate_filter_form"] = EventCandidateSearchForm(
            self.request.GET or None, nle_id=nle_id)
        # open the extra filters if any of them are in play, so an active filter
        # is never hidden behind a collapsed panel
        context["filters_active"] = any(
            self.request.GET.get(f) for f in
            ("distance_max", "distance_type", "first_det_after",
             "first_det_before", "cone_ra", "cone_dec", "cone_radius")
        )
        context["eventcandidate_create_form"] = CreateEventCandidateFromNLEForm(nle_id=nle_id)

        context["no_score_message"] = None
        # shown whichever way the toggle is set: the toggle stays locked on light
        # curve metrics until the event has KilonovaSCORER scores. KN-style events
        # only, since KilonovaSCORER means nothing for BBH/AGN-flare scoring
        event_class = None
        if nle_id:
            nle = NonLocalizedEvent.objects.filter(id=nle_id).first()
            if nle:
                event_class = most_likely_class_for_event(nle.event_id)

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
                "trove_nonlocalizedevents:create-candidate",
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
        sf = latest_score_factors(ScoreFactor.objects.filter(
            event_candidate=ec, key__in=subscore_keys_to_report
        ))

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

        # get photometry info
        first_phot = PhotometryReducedDatum.objects.filter(
            target_id=t.id, brightness__isnull=False, brightness_error__isnull=False
        ).first()
        if first_phot:
            src_first = first_phot.source_name
            if first_phot.instrument:
                src_str_first = f"{src_first}; {first_phot.instrument} {first_phot.bandpass}"
            elif first_phot.telescope:
                src_str_first = f"{src_first}; {first_phot.telescope} {first_phot.bandpass}"
            else:
                src_str_first = f"{src_first}; {first_phot.bandpass}"
            src_str_first = src_str_first.replace(
                " (TNS)", ""
            )  # strip (TNS) if present
        else:
            src_str_first = None

        latest_phot = PhotometryReducedDatum.objects.filter(
            target_id=t.id
        ).order_by("timestamp").last()
        if latest_phot:
            src_latest = latest_phot.source_name

            if latest_phot.instrument:
                src_str_latest = f"({latest_phot.instrument} {latest_phot.bandpass}; {src_latest})"
            elif latest_phot.telescope:
                src_str_latest = f"({latest_phot.telescope} {latest_phot.bandpass}; {src_latest})"
            else:
                src_str_latest = f"({latest_phot.bandpass}; {src_latest})"

            if latest_phot.brightness is not None:  # detection
                phot_str_latest = (
                    f"{latest_phot.brightness:.2f} +/- {latest_phot.brightness_error:.2f} {src_str_latest}"
                )
            else:  # non-detection
                phot_str_latest = f">{latest_phot.limit:.2f} {src_str_latest}"

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
        new_val = set_agn_toggle(request, not get_agn_toggle(request))

        nle_id = request.GET.get("nonlocalizedevent")
        if nle_id:
            # unfiltered, matching what the list view caches and then
            # filters on the way out
            candidates = EventCandidate.objects.filter(
                nonlocalizedevent_id=nle_id
            ).select_related("target", "nonlocalizedevent")
            phot_method = get_phot_method(request)
            scored_candidates = get_event_candidate_scores(
                candidates, agn_toggle=new_val, phot_method=phot_method,
            )

            # Re-scores all candidates after AGN-toggle change and saves to
            # cache. The key must match the one the list view builds -- every
            # other toggle's *current* value included -- otherwise this
            # pre-warm writes a key nothing reads and the list re-scores anyway.
            cache_key = scored_candidates_cache_key(
                QueryDict(f"nonlocalizedevent={nle_id}"), new_val, phot_method
            )
            cache.set(cache_key, scored_candidates, SCORE_CACHE_PERIOD)
            params = {"nonlocalizedevent": nle_id}
            return redirect(_return_to(
                request,
                reverse("trove_nonlocalizedevents:event-candidates") + "?" + urlencode(params),
            ))
        return redirect(_return_to(request, reverse("trove_nonlocalizedevents:event-candidates")))


class TogglePhotMethodCacheView(LoginRequiredMixin, View):
    """
    Flip the photometry scorer between TROVE and KilonovaSCORER.
    """

    def get(self, request, *args, **kwargs):
        new_val = toggle_phot_method(request)
        logger.info("Photometry scoring method switched to %r", new_val)

        nle_id = request.GET.get("nonlocalizedevent")
        # the cached scored list was built displaying the other method's factor,
        # so it has to go or the page keeps showing the
        # old numbers under the new label
        url = reverse("trove_nonlocalizedevents:event-candidates")
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
    """

    def get(self, request, *args, **kwargs):
        invalidate_scored_candidates_cache(request.GET)

        # send the user back to the list they were looking at, filters and all
        query_string = request.GET.urlencode()
        url = reverse("trove_nonlocalizedevents:event-candidates")
        if query_string:
            url += f"?{query_string}"
        return redirect(url)


class VetMultiProgressPartialView(View):
    """
    Just the "Vet All" / "Vet Selected" progress notice.

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
            url = reverse("trove_nonlocalizedevents:event-candidates")
            if nle_id:
                url += f"?nonlocalizedevent={nle_id}"
            return redirect(url)

        return render(
            request,
            "trove_nonlocalizedevents/partials/vet_multi_progress.html",
            {"vet_multi_progress": get_vet_multi_progress(nle_id)},
        )


def vet_all_cooldown_notice(request):
    messages.warning(
        request,
        "A user has recently run vetting on all candidates, placing it on "+
        "cooldown. The cooldown period is "+
        f"{settings.VETTING_COOLDOWN_PERIOD / 3600:.0f} hours from the time of the "+
        "user submitting the request to vet all. The vetting results will update for "+
        "all users. Please try again later if you truly need to re-vet *everything* "+
        "again. You can still vet individual candidates via the candidate pages or "+
        "select some subset of candidates to vet."
    )
    return redirect(request.META.get('HTTP_REFERER', '/'))

class NonLocalizedEventListView(FilterView):
    """
    Unadorned Django ListView subclass for NonLocalizedEvent model.
    """
    model = NonLocalizedEvent
    paginate_by = 100

    def get_filterset(self, filterset_class):
        kwargs = self.get_filterset_kwargs(filterset_class)
        filterset = filterset_class(**kwargs)
        filterset.form.helper = self.formhelper_class()
        return filterset

    def get_queryset(self):
        # '-created' is most recent first
        qs = NonLocalizedEvent.objects.order_by('-created')
        return qs


class GWListView(NonLocalizedEventListView):
    """
    Unadorned Django ListView subclass for NonLocalizedEvent model.
    """
    template_name = 'tom_nonlocalizedevents/gw_list.html'
    filterset_class = GWFilter
    formhelper_class = GWFormHelper

    def get_queryset(self):
        # each row reads the newest sequence several times; prefetching it and
        # reading the cache via `last_sequence` takes this page from ~795 queries to 2
        qs = (NonLocalizedEvent.objects.filter(event_type='GW')
              .prefetch_related(Prefetch(
                  'sequences',
                  queryset=EventSequence.objects.select_related('localization')
                                                .order_by('sequence_id')))
              .order_by('-event_id'))
        try:
            search_str = self.kwargs["search_str"] # TRY to find a search string
            qs = qs.filter(event_id__icontains=search_str).order_by('-event_id')
        except KeyError:
            pass
        return qs


# class GWListSearchView(GWListView):
#     """
#     GWListView but with filtering by event ID through search_name
#     """
#     template_name = 'tom_nonlocalizedevents/gw_list.html'
#     filterset_class = GWFilter
#     formhelper_class = GWFormHelper

#     def get_queryset(self):
#         search_name = self.kwargs["search_name"]
#         qs = super().get_queryset().filter(event_id__icontains=search_name)
#         return qs


class GRBListView(NonLocalizedEventListView):
    """
    Unadorned Django ListView subclass for NonLocalizedEvent model.
    """
    template_name = 'tom_nonlocalizedevents/grb_list.html'

    def get_queryset(self):
        qs = NonLocalizedEvent.objects.filter(event_type='GRB').order_by('-created')
        return qs


class NeutrinoListView(NonLocalizedEventListView):
    """
    Unadorned Django ListView subclass for NonLocalizedEvent model.
    """
    template_name = 'tom_nonlocalizedevents/neutrino_list.html'
    filterset_class = NeutrinoFilter
    formhelper_class = NeutrinoFormHelper

    def get_queryset(self):
        qs = NonLocalizedEvent.objects.filter(event_type='NU').order_by('-created')
        return qs


class UnknownListView(NonLocalizedEventListView):
    """
    Unadorned Django ListView subclass for NonLocalizedEvent model.
    """
    template_name = 'tom_nonlocalizedevents/unknown_list.html'

    def get_queryset(self):
        qs = NonLocalizedEvent.objects.filter(event_type='UNK').order_by('-created')
        return qs


class EventCandidateCreateView(LoginRequiredMixin, RedirectView):
    """
    View that handles the association of a target with a NonLocalizedEvent on a button press
    """
    def get(self, request, *args, **kwargs):
        """
        Method that handles the GET requests for this view.
        """
        nonlocalizedevent = NonLocalizedEvent.objects.get(event_id=self.kwargs['event_id'])
        target = Target.objects.get(id=self.kwargs['target_id'])
        viability_reason = f'added from candidates list by {self.request.user.first_name}'

        # (target, nonlocalizedevent) is unique, so linking a target that is
        # already a candidate used to raise IntegrityError and return a 500.
        try:
            ec, created = EventCandidate.objects.get_or_create(
                nonlocalizedevent=nonlocalizedevent, target=target,
                defaults={'viability_reason': viability_reason})
        except IntegrityError:
            created = False  # two submits raced; the other one made the link

        if created:
            invalidate_scored_candidates_cache(nonlocalizedevent.id)
            # the page they came from may have had filters on, which is a
            # cache key of its own and would otherwise still be stale
            referer_query = urlparse(self.get_redirect_url()).query
            if referer_query:
                invalidate_scored_candidates_cache(QueryDict(referer_query))
            # perform vetting
            vetting_choices = methods_for_event(nonlocalizedevent.event_id)[0]
            vetting_modes = [v for v, _ in vetting_choices]
            for vm in vetting_modes:
                score(
                    target_id=ec.target.id,
                    nonlocalized_event_name=nonlocalizedevent.event_id,
                    em_model_name=vm
                )
            messages.success(
                self.request,
                f"Linked {target.name} to {nonlocalizedevent.event_id} and "
                + f"re-performed vetting in {', '.join(vetting_modes)} vetting mode(s).",
            )
        else:
            messages.info(request, f'{target.name} is already a candidate of '
                                   f'{nonlocalizedevent.event_id}.')

        return HttpResponseRedirect(self.get_redirect_url())

    def get_redirect_url(self):
        """
        Returns redirect URL as specified in the HTTP_REFERER field of the request.

        :returns: referer
        :rtype: str
        """
        referer = self.request.META.get('HTTP_REFERER', '/')
        return referer


class GWNonLocalizedEventOrTargetNameSearchView(RedirectView):
    """
    View for searching by nonlocalized event **or** target name. If only a
    single GW NLE match, pulls up the candidate list for that GW NLE. If
    multiple matches, pulls up the page with a list of GW NLEs, filtered by
    the string. If no NLE matches, searches instead for targets.
    """

    def get(self, request, *args, **kwargs):
        search_str = request.GET.get("name").strip()
        all_gw_nles = nonlocalizedevents_for_user(request.user, NonLocalizedEvent.objects.filter(event_type="GW"))
        gw_nles = all_gw_nles.filter(event_id__icontains=search_str,
                                     event_type="GW")
        try:
            nle = gw_nles.get()
        except NonLocalizedEvent.MultipleObjectsReturned as exc:
            print(exc)
            return HttpResponseRedirect(reverse('trove_nonlocalizedevents:gw-list-search', kwargs={"search_str":search_str}))
        except NonLocalizedEvent.DoesNotExist:
            return HttpResponseRedirect(reverse('trove_targets:search', kwargs={"search_str":search_str}))

        return HttpResponseRedirect(f"/eventcandidates/?nonlocalizedevent={nle.id}")
