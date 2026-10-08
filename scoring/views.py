"""
Page views for candidate vetting
"""

import numpy as np
from datetime import datetime, timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import View
from django.views.generic.base import RedirectView
from django.views.generic.edit import FormView
from django.http import HttpResponseRedirect, HttpResponseForbidden, QueryDict
from django.urls import reverse
from django.shortcuts import redirect, render
from dal import autocomplete

from trove_targets.models import Target
from tom_nonlocalizedevents.models import (
    EventCandidate,
    NonLocalizedEvent,
    EventLocalization,
)

from candidate_vetting.vet import (
    GALAXY_CATALOGS,
    host_association,
    localization_sequence_from_name,
)
from candidate_vetting.public_catalogs.phot_catalogs import ZTF_Forced_Phot

from .forms import (VettingChoiceForm,
                    RedshiftUpdateForm,
                    NonLocalizedEventAssociateTargetsForm
                    )
from .config import (FORM_CHOICE_FUNC_MAP,
                     VETTING_FORM_CHOICES,
                     VETTING_FORM_INITIALS,
                     DETECTION_HORIZON_DEFAULTS
                     )
from .tasks import vet_all_async, associate_targets_with_nle_async
from .phot_method import (
    KILONOVA_VETTING_MODE,
    PHOT_METHOD_CHOICES,
    PHOT_METHOD_KILONOVA,
    PHOT_METHOD_LABELS,
    PHOT_METHOD_TROVE,
    get_phot_method,
)
from .util import most_likely_class_for_event
from .util import get_vet_all_progress, most_likely_class_for_event
from .vet_basic import vet_basic
from .vet_phot import find_public_phot
from .dynamic_catalogs import UserGalaxy

from custom_code.templatetags.nonlocalizedevent_extras import get_most_likely_class
from custom_code.templatetags.target_list_extras import galaxy_table



def resolve_event_id(value):
    if not value:
        return None
    nle = NonLocalizedEvent.objects.filter(event_id=value).first()
    if nle is None and str(value).isdigit():
        nle = NonLocalizedEvent.objects.filter(id=int(value)).first()
    return nle.event_id if nle else None


def methods_for_event(event_id):
    cls = most_likely_class_for_event(event_id)
    return VETTING_FORM_CHOICES.get(cls, VETTING_FORM_CHOICES[""]), cls


def event_for_target(value, target_pk):
    event_id = resolve_event_id(value)
    if event_id and EventCandidate.objects.filter(
        target_id=target_pk, nonlocalizedevent__event_id=event_id
    ).exists():
        return event_id
    return None


def _vetting_method_fields(form, event_id, request):
    if event_id is None:
        form.fields["vetting_method"].choices = [("basic", "Basic Vetting")]
    else:
        choices, cls = methods_for_event(event_id)
        form.fields["vetting_method"].choices = choices
        form.fields["vetting_method"].initial = VETTING_FORM_INITIALS.get(
            cls, VETTING_FORM_INITIALS[""]
        )
    return _phot_method_field(form, request)
  

def _phot_method_field(form, request):
    """
    Offer the scorer choice, defaulting to whatever the site toggle shows.
    """
    kn_available = any(
        value == "KN" for value, _ in form.fields["vetting_method"].choices
    )
    if not kn_available:
        del form.fields["phot_method"]
        return form

    form.fields["phot_method"].choices = [
        (m, PHOT_METHOD_LABELS[m]) for m in PHOT_METHOD_CHOICES
    ]
    form.fields["phot_method"].initial = get_phot_method(request)
    form.fields["phot_method"].widget.attrs.update({
        "data-kn-only": PHOT_METHOD_KILONOVA,
        "data-fallback": PHOT_METHOD_TROVE,
    })
    form.fields["vetting_method"].widget.attrs["data-kn-mode"] = KILONOVA_VETTING_MODE
    return form


def _clean_phot_method(value):
    """A submitted scorer name, or None to leave the decision to the callee."""
    return value if value in PHOT_METHOD_CHOICES else None


class TargetVettingFormView(LoginRequiredMixin, FormView):
    template_name = "scoring/vetting_form.html"
    form_class = VettingChoiceForm

    # overriding the get_form function
    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        target_pk = self.kwargs["pk"]
        events = list(dict.fromkeys(
            EventCandidate.objects.filter(target_id=target_pk)
            .select_related("nonlocalizedevent")
            .values_list("nonlocalizedevent__event_id", flat=True)
        ))
        if not events:  # nothing to choose between, so basic vetting only
            del form.fields["nle"]
            return _vetting_method_fields(form, None, self.request)

        form.fields["nle"].choices = [("", "\u2014 No event (Basic Vetting only) \u2014")] + [
            (eid, f"{eid} \u2014 {methods_for_event(eid)[1] or 'unknown class'}")
            for eid in events
        ]
        # on POST, build the methods from the submitted event so validation sees
        # the same set the user was shown
        submitted = self.request.POST.get("nle") if self.request.method == "POST" else None
        return _vetting_method_fields(form, event_for_target(submitted, target_pk), self.request)

    def get(self, request, *args, **kwargs):
        referer = request.META.get("HTTP_REFERER")
        if referer:
            self.request.session["nle_id"] = urlparse(referer).query
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        # and now we can actually perform the vetting and redirect
        pk = self.kwargs["pk"]
        vetting_mode = form.cleaned_data["vetting_method"]

        # generate the base url
        base_url = reverse("scoring:vet", kwargs=dict(pk=pk, vetting_mode=vetting_mode))

        chosen = form.cleaned_data.get("nle")
        params = [f"nonlocalizedevent={chosen}"] if chosen else []
        phot_method = _clean_phot_method(form.cleaned_data.get("phot_method"))
        if phot_method:
            params.append(f"phot_method={phot_method}")
        if params:
            base_url += "?" + "&".join(params)
        return redirect(base_url)


class VettingMethodsPartialView(LoginRequiredMixin, View):
    """
    The vetting-method and scorer fields for the event the user just picked.
    """

    def get(self, request, pk, *args, **kwargs):
        form = VettingChoiceForm()
        del form.fields["nle"]
        form = _vetting_method_fields(form, event_for_target(request.GET.get("nle"), pk), self.request)
        return render(
            request, "scoring/partials/vetting_method_fields.html", {"form": form}
        )


class TargetVettingView(LoginRequiredMixin, RedirectView):
    """
    View that runs or reruns the candidate vetting code and stores the results
    """

    def get(self, request, *args, **kwargs):
        """
        Method that handles the GET requests for this view. Calls the vetting
        code for different transients.
        """
        target_pk = kwargs["pk"]
        target = Target.objects.get(pk=target_pk)
        vetting_mode = kwargs.get("vetting_mode", "basic")

        # the query parameter may carry either form of identifier
        nonlocalized_event_name = resolve_event_id(
            request.GET.get("nonlocalizedevent")
        )

        # then run the vetting
        vetting_func = FORM_CHOICE_FUNC_MAP[vetting_mode]
        if vetting_mode == "basic":
            vet_basic(target.id, stop_on_zero=False)
            messages.info(request, "Ran basic vetting.")
        elif nonlocalized_event_name is None:
            messages.error(
                request,
                "Ran basic vetting. If you expected event-dependent "
                + "vetting, ensure an event is specified in the URL.",
            )
        else:
            # Only the KN pipeline takes a scorer; the others have just one.
            phot_method = (_clean_phot_method(request.GET.get("phot_method"))
                           or get_phot_method(request))
            extra = {"phot_method": phot_method} if vetting_mode == "KN" and phot_method else {}
            vetting_func(target.id, nonlocalized_event_name, **extra)
            label = (f" using {PHOT_METHOD_LABELS[phot_method]} for scoring photometry"
                     if extra else "")
            messages.info(request, f"Ran vetting in {vetting_mode} mode{label}.")

        toreverse = reverse("targets:detail", kwargs=dict(pk=target_pk))

        return redirect(toreverse)  # this redirects back to the original target page


class TargetFPView(LoginRequiredMixin, RedirectView):
    """
    Class to run forced photometry for a target
    """

    def get(self, request, *args, **kwargs):

        messages.info(
            request,
            "Checking for new public forced photometry. This can take ~minutes for ATLAS and ~hours-days for ZTF. We suggest you check back later.",
        )

        target = Target.objects.get(id=kwargs["pk"])

        # check TNS and ATLAS
        find_public_phot(target=target, days_ago_max=365, queue_priority=0)

        # then also run ZTF forced photometry
        # this will only actually be ingested after the ZTF forced photometry runs
        ztf = ZTF_Forced_Phot()
        ztf.query(target=target, days_ago=365)

        return HttpResponseRedirect(self.get_redirect_url())

    def get_redirect_url(self):
        """
        Returns redirect URL as specified in the HTTP_REFERER field of the request.

        :returns: referer
        :rtype: str
        """
        referer = self.request.META.get("HTTP_REFERER", "/")
        return referer


class TargetRedshiftUpdateFormView(LoginRequiredMixin, FormView):
    template_name = "scoring/update_redshift_form.html"
    form_class = RedshiftUpdateForm

    # overriding the get_form function
    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        # set a default z_err
        form.fields["z_err"].initial = 0.001
        # get target, potential host galaxies, their IDs, and provenance (source)
        target = Target.objects.get(id=self.kwargs["pk"])
        form.target = target
        # a target that has never been vetted has no host galaxy table at all
        galaxies = galaxy_table(target)["galaxies"] or []
        form.galaxies = galaxies
        galaxy_choices_ids = [
            (gid, gid) for gid in dict.fromkeys(str(g["ID"]) for g in galaxies)
        ]
        galaxy_choices_sources = [
            (gs, gs) for gs in np.unique([g["Source"] for g in galaxies])
        ]
        form.fields["host_galaxy_id"].choices = galaxy_choices_ids
        form.fields["host_galaxy_source"].choices = galaxy_choices_sources
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sources_by_galaxy = {}
        for galaxy in getattr(context["form"], "galaxies", []):
            sources = sources_by_galaxy.setdefault(str(galaxy["ID"]), [])
            if str(galaxy["Source"]) not in sources:
                sources.append(str(galaxy["Source"]))
        context["sources_by_galaxy"] = sources_by_galaxy
        return context

    def get(self, request, *args, **kwargs):
        referer = request.META.get("HTTP_REFERER")
        if referer:
            self.request.session["nle_id"] = urlparse(referer).query
        return super().get(request, *args, **kwargs)

    def form_invalid(self, form):
        # TROVE surfaces these through bootstrap_messages in the base template
        for error in form.non_field_errors():
            messages.error(self.request, error)
        return super().form_invalid(form)

    def form_valid(self, form):
        host_galaxy_id = form.cleaned_data["host_galaxy_id"]
        host_galaxy_source = form.cleaned_data["host_galaxy_source"]
        z = form.cleaned_data["z"]
        z_err = form.cleaned_data["z_err"]
        if not z_err:  # in case user accidentally deleted it
            z_err = form.fields["z_err"].initial
        submitter = form.cleaned_data["submitter"]

        # add new entry to user-defined galaxy catalog
        print(f"\nhost_galaxy = {host_galaxy_id}\t{host_galaxy_source}")
        print(f"z = {z}")
        print(f"z_err = {z_err}")
        pk = self.kwargs["pk"]
        target = Target.objects.get(id=pk)
        UserGalaxy()._add_galaxy(
            target,
            form.galaxies,
            z,
            z_err,
            host_galaxy_id,
            host_galaxy_source,
            submitter,
        )

        # re-run host association, including the galaxy we just added
        host_association(target_id=pk, galaxy_catalogs=[UserGalaxy] + GALAXY_CATALOGS)

        # re-run vetting if NLE was provided by referer
        # the session key is missing when the form was opened without a referer
        nle_name_or_id = (
            self.request.session.get("nle_id", "").split("=")[-1].split("/")[0]
        )
        if nle_name_or_id.isdigit():
            nle = NonLocalizedEvent.objects.get(id=nle_name_or_id)
        else:
            try:
                nle = NonLocalizedEvent.objects.get(event_id=nle_name_or_id)
            except NonLocalizedEvent.DoesNotExist:
                nle = None

        if nle:
            nle_eventseq = localization_sequence_from_name(nle.event_id)
            nle_most_likely_class = get_most_likely_class(
                nle_eventseq.details
            )  # most likely class for the NLE
            try:
                vetting_choices = VETTING_FORM_CHOICES[nle_most_likely_class]
            except KeyError:
                vetting_choices = VETTING_FORM_CHOICES[
                    ""
                ]  # allow all types of vetting if most likely class not recognized

            vetting_modes = [v for v, _ in vetting_choices]
            vetting_modes.remove("basic")  # no need to re-run basic vetting
            for vm in vetting_modes:
                FORM_CHOICE_FUNC_MAP[vm](
                    target_id=pk, nonlocalized_event_name=nle.event_id
                )
            messages.info(
                self.request,
                "Added a new host galaxy redshift, re-ran host association, and "
                + f"re-performed vetting in {', '.join(vetting_modes)} vetting mode(s).",
            )
        else:
            messages.info(
                self.request,
                "Added a new host galaxy redshift and re-ran host association. "
                + "Did NOT re-run vetting as a nonlocalized event (NLE) was not provided in the URL.",
            )

        # generate the base url
        base_url = reverse("targets:detail", kwargs=dict(pk=pk))

        # then also preserve the query parameters
        query_str = self.request.session.pop("nle_id", "")
        print("QUERY STRING:", query_str)
        if query_str:
            base_url += f"?{query_str}"

        return redirect(base_url)


class TargetVettingSelectedFormView(LoginRequiredMixin, FormView):
    """Vet the candidates ticked on the event page.

    Same form and same task queue as Vet All; the only difference is which
    candidates go in. Selected ids arrive by POST from the candidate table and
    ride through the vetting-method form as hidden inputs.
    """

    template_name = "scoring/vetting_form.html"
    form_class = VettingChoiceForm

    def selected_ids(self):
        """Ids from the table's POST, or from this form's own resubmission."""
        return self.request.POST.getlist("candidates")

    def candidates(self):
        """The candidates to vet: the ticked rows, or every row matching the
        filters when the user asked for all of them.

        Checkboxes only reach the server for the page you can see, so "select
        all" re-applies the table's own filters rather than posting 456 ids.
        """
        scoped = EventCandidate.objects.filter(
            nonlocalizedevent_id=self.kwargs["pk"]
        ).select_related("target")
        if self.request.POST.get("select_all_matching"):
            from trove_nonlocalizedevents.permissions import candidates_for_user
            from trove_nonlocalizedevents.views import filter_candidates

            params = QueryDict(self.request.POST.get("filters", ""))
            scoped = filter_candidates(
                candidates_for_user(self.request.user, scoped), params
            )
            target_name = params.get("target__name")
            if target_name:
                scoped = scoped.filter(target__name__icontains=target_name)
            # the distance cut is not a queryset filter, so apply it the same way
            # the table does or "select all" vets rows the table did not show
            from trove_nonlocalizedevents.views import _as_float, within_max_distance
            from scoring.util import host_distances

            distance_max = _as_float(params.get("distance_max"))
            if distance_max is not None:
                scoped = list(scoped)
                return within_max_distance(
                    scoped, host_distances(scoped), distance_max)
            return scoped
        return scoped.filter(id__in=self.selected_ids())

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # The table's POST carries the selection, not a vetting method, so it is
        # not an attempt to submit this form. Binding it to that POST marked
        # every field as missing before the user had chosen anything.
        if (self.request.method == "POST"
                and "vetting_method" not in self.request.POST):
            kwargs.pop("data", None)
            kwargs.pop("files", None)
        return kwargs

    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        nle = NonLocalizedEvent.objects.get(id=self.kwargs["pk"])
        cls = most_likely_class_for_event(nle.event_id)
        form.fields["vetting_method"].choices = VETTING_FORM_CHOICES.get(
            cls, VETTING_FORM_CHOICES[""]
        )
        form.fields["vetting_method"].initial = VETTING_FORM_INITIALS.get(
            cls, VETTING_FORM_INITIALS[""]
        )
        if "nle" in form.fields:  # the event is in this view's own URL
            del form.fields["nle"]
        return _phot_method_field(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["selected_ids"] = self.selected_ids()
        context["select_all_matching"] = self.request.POST.get("select_all_matching", "")
        context["filters"] = self.request.POST.get("filters", "")
        return context

    def post(self, request, *args, **kwargs):
        # the first POST comes from the table and only carries the selection, so
        # show the form; later POSTs carry the chosen method and run the vetting
        if "vetting_method" not in request.POST:
            return self.render_to_response(self.get_context_data(form=self.get_form()))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        nle = NonLocalizedEvent.objects.get(id=self.kwargs["pk"])
        candidates = list(self.candidates())
        back = redirect(f"/eventcandidates/?nonlocalizedevent={nle.id}")
        if not candidates:
            messages.warning(self.request, "No candidates were selected.")
            return back

        vetting_mode = form.cleaned_data["vetting_method"]
        phot_method = _clean_phot_method(form.cleaned_data.get("phot_method"))
        vet_all_async(candidates, nle, vetting_mode, phot_method=phot_method,
                      started_by=self.request.user.get_username(),
                      run_kind="selected")
        # imported here to keep scoring.views out of an import cycle
        from trove_nonlocalizedevents.views import invalidate_scored_candidates_cache

        invalidate_scored_candidates_cache(str(nle.id))
        # then also preserve the query parameters
        query_str = self.request.session.pop("nle_id", "")
        params = [query_str] if query_str else []
        if phot_method:
            params.append(f"phot_method={phot_method}")
        if params:
            base_url += "?" + "&".join(params)
        return redirect(base_url)


class TargetVettingAllView(LoginRequiredMixin, RedirectView):
    """
    View that runs or reruns the candidate vetting code and stores the results,
    for all candidates
    """

    def get(self, request, *args, **kwargs):
        """
        Method that handles the GET requests for this view. Calls the vetting
        code for different transients.
        """
        pk = kwargs["pk"]
        vetting_mode = kwargs.get("vetting_mode", "basic")

        # get the nonlocalized event
        nle = NonLocalizedEvent.objects.filter(id=pk)[0]

        # get all of the event candidates
        ecs = EventCandidate.objects.filter(nonlocalizedevent_id=nle.id).order_by(
            "target__name"
        )

        # The scorer the user picked on the form, sent with every task so the
        # whole run uses it -- workers have no session to read the toggle from,
        # and it could be flipped mid-run in any case.
        phot_method = (_clean_phot_method(request.GET.get("phot_method"))
                       or get_phot_method(request))

        # then run the vetting, asynchronously
        messages.info(
            self.request,
            f"Vetting {len(candidates)} selected candidate"
            f"{'' if len(candidates) == 1 else 's'} in {vetting_mode} mode; "
            "this takes a few seconds each, so check back shortly.",
        )
        return back


class NonLocalizedEventAssociateTargetsFormView(LoginRequiredMixin, FormView):
    template_name = "scoring/nle_associate_targets_form.html"
    form_class = NonLocalizedEventAssociateTargetsForm

    # overriding the get_form function
    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)

        # set a default SNR_min
        form.fields["snr_min"].initial = 5

        # get NLE
        nle_id = self.request.session["nle_id"].split("=")[-1]
        nle_eventseq = localization_sequence_from_name(
            NonLocalizedEvent.objects.get(id=nle_id)
        )
        nle_most_likely_class = get_most_likely_class(
            nle_eventseq.details
        )  # most likely class for the NLE

        # set a default time horizon based on NLE most likely class
        try:
            form.fields["first_det_tmin"].initial, form.fields["first_det_tmax"].initial = DETECTION_HORIZON_DEFAULTS[nle_most_likely_class]
        except KeyError: # if NLE most likely class not recognized
            form.fields["first_det_tmin"].initial, form.fields["first_det_tmax"].initial = DETECTION_HORIZON_DEFAULTS[""]
        return form

    # overriding the get_context_data function
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['skymap_prob_contour'] = settings.SKYMAP_PROB_CONTOUR
        return context

    def get(self, request, *args, **kwargs):
        referer = request.META.get("HTTP_REFERER")
        if referer:
            self.request.session["nle_id"] = urlparse(referer).query
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        pk = self.kwargs["pk"]
        first_det_tmin = form.cleaned_data["first_det_tmin"]
        first_det_tmax = form.cleaned_data["first_det_tmax"]
        snr_min = form.cleaned_data["snr_min"]

        # get the nonlocalized event
        nle = NonLocalizedEvent.objects.filter(id=pk)[0]
        seq = nle.sequences.last()
        try:
            nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f%z")
        except ValueError:
            nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f")

        # helpful prints
        first_det_tmin_toprint = (nle_time + timedelta(days=first_det_tmin)).strftime("%Y-%m-%d %H:%M:%S")
        first_det_tmax_toprint = (nle_time + timedelta(days=first_det_tmax)).strftime("%Y-%m-%d %H:%M:%S")
        if snr_min > 0:
            messages.info(
                self.request,
                f"Searching for targets within the {settings.SKYMAP_PROB_CONTOUR*100:.0f}% "+
                f"localization of {nle.event_id}, with first detection with "+
                f"SNR > {snr_min} and between "+
                f"{first_det_tmin_toprint} and {first_det_tmax_toprint}. This "+
                "will take a few seconds per target in the localization "+
                "region; check back later to see if new event candidates have "+
                "been created!"
            )
        else:
            messages.info(
                self.request,
                f"Searching for targets within the {settings.SKYMAP_PROB_CONTOUR*100:.0f}% "+
                f"localization of {nle.event_id}, with first detection between "+
                f"{first_det_tmin_toprint} and {first_det_tmax_toprint}. This "+
                "will take a few seconds per target in the localization "+
                "region; check back later to see if new event candidates have "+
                "been created!"
            )

        # run the association asynchronously
        associate_targets_with_nle_async(nle, first_det_tmin, first_det_tmax, snr_min)

        return redirect(
            f"/eventcandidates/?nonlocalizedevent={nle.id}"
        )  # this redirects back to the NLE page
