"""
Page views for candidate vetting
"""

import numpy as np
from datetime import datetime, timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic.base import RedirectView
from django.views.generic.edit import FormView
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.shortcuts import redirect

from trove_targets.models import Target
from tom_nonlocalizedevents.models import (
    EventCandidate,
    NonLocalizedEvent,
    EventLocalization,
)
from .forms import (VettingChoiceForm,
                    RedshiftUpdateForm,
                    NonLocalizedEventAssociateTargetsForm
                    )
from .config import (FORM_CHOICE_FUNC_MAP,
                     VETTING_FORM_CHOICES,
                     DETECTION_HORIZON_DEFAULTS
                     )

from .vet import host_association, localization_sequence_from_name
from .tasks import vet_all_async
from .vet_basic import vet_basic
from .vet_phot import find_public_phot
from .public_catalogs.phot_catalogs import ZTF_Forced_Phot
from .public_catalogs.dynamic_catalogs import UserGalaxy

from custom_code.templatetags.nonlocalizedevent_extras import get_most_likely_class
from custom_code.templatetags.target_list_extras import galaxy_table
from dal import autocomplete


class TargetVettingFormView(FormView):
    template_name = "candidate_vetting/vetting_form.html"
    form_class = VettingChoiceForm

    # TODO: Only give the user the form if there is a non-localized event associated
    #       with this target. If there isn't, this should just redirect to the basic
    #       target vetting!

    # overriding the get_form function
    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)

        # if NLE was provided by referer, use it to choose what vetting is allowed
        nle_name_or_id = self.request.session["nle_id"].split("=")[-1].split("/")[0]
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
                form.fields["vetting_method"].choices = VETTING_FORM_CHOICES[
                    nle_most_likely_class
                ]
            except KeyError:
                form.fields["vetting_method"].choices = VETTING_FORM_CHOICES[
                    ""
                ]  # allow all types of vetting if most likely class not recognized
        else:
            form.fields["vetting_method"].choices = VETTING_FORM_CHOICES[""]
        return form

    def get(self, request, *args, **kwargs):
        referer = request.META.get("HTTP_REFERER")
        if referer:
            self.request.session["nle_id"] = urlparse(referer).query
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        pk = self.kwargs["pk"]
        vetting_mode = form.cleaned_data["vetting_method"]

        # generate the base url
        base_url = reverse(
            "candidate_vetting:vet", kwargs=dict(pk=pk, vetting_mode=vetting_mode)
        )

        # then also preserve the query parameters
        query_str = self.request.session.pop("nle_id", "")
        print("QUERY STRING:", query_str)
        if query_str:
            print(base_url)
            base_url += f"?{query_str}"
            print(base_url)
        return redirect(base_url)


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

        # get the nonlocalized event name from the referer
        nonlocalized_event_name = request.GET.get("nonlocalizedevent")

        # then run the vetting
        vetting_func = FORM_CHOICE_FUNC_MAP[vetting_mode]
        if vetting_mode == "basic" or nonlocalized_event_name is None:
            vet_basic(target.id)
            messages.info(
                request,
                "Ran basic vetting. If you expected non-localized event (NLE)-dependent "
                + "vetting, ensure an NLE is specified in the URL.",
            )
        else:
            vetting_func(target.id, nonlocalized_event_name)
            messages.info(request, f"Ran vetting in {vetting_mode} mode.")

        if nonlocalized_event_name:
            toreverse = (
                reverse("targets:detail", kwargs=dict(pk=target_pk))
                + f"?nonlocalizedevent={nonlocalized_event_name}"
            )

        else:
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


class TargetRedshiftUpdateFormView(FormView):
    template_name = "candidate_vetting/update_redshift_form.html"
    form_class = RedshiftUpdateForm

    # overriding the get_form function
    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        # set a default z_err
        form.fields["z_err"].initial = 0.001
        # get target, potential host galaxies, their IDs, and provenance (source)
        target = Target.objects.get(id=self.kwargs["pk"])
        form.target = target
        galaxies = galaxy_table(target)["galaxies"]
        galaxy_choices_ids = [(g["ID"], g["ID"]) for g in galaxies]
        galaxy_choices_sources = [
            (gs, gs) for gs in np.unique([g["Source"] for g in galaxies])
        ]
        form.fields["host_galaxy_id"].choices = galaxy_choices_ids
        form.fields["host_galaxy_source"].choices = galaxy_choices_sources
        return form

    def get(self, request, *args, **kwargs):
        referer = request.META.get("HTTP_REFERER")
        if referer:
            self.request.session["nle_id"] = urlparse(referer).query
        return super().get(request, *args, **kwargs)

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
        galaxies = galaxy_table(target)["galaxies"]
        UserGalaxy()._add_galaxy(
            target, galaxies, z, z_err, host_galaxy_id, host_galaxy_source, submitter
        )

        # re-run host association
        host_association(target_id=pk)

        # re-run vetting if NLE was provided by referer
        nle_name_or_id = self.request.session["nle_id"].split("=")[-1].split("/")[0]
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


class TargetVettingAllFormView(FormView):
    template_name = "candidate_vetting/vetting_form.html"
    form_class = VettingChoiceForm

    # overriding the get_form function
    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        nle_id = self.request.session["nle_id"].split("=")[-1]
        nle_eventseq = localization_sequence_from_name(
            NonLocalizedEvent.objects.get(id=nle_id)
        )
        nle_most_likely_class = get_most_likely_class(
            nle_eventseq.details
        )  # most likely class for the NLE
        try:
            form.fields["vetting_method"].choices = VETTING_FORM_CHOICES[
                nle_most_likely_class
            ]
        except KeyError:
            form.fields["vetting_method"].choices = VETTING_FORM_CHOICES[
                ""
            ]  # allow all types of vetting if most likely class not recognized
        return form

    def get(self, request, *args, **kwargs):
        referer = request.META.get("HTTP_REFERER")
        if referer:
            self.request.session["nle_id"] = urlparse(referer).query
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        pk = self.kwargs["pk"]
        vetting_mode = form.cleaned_data["vetting_method"]

        # generate the base url
        base_url = reverse(
            "candidate_vetting:vet_all", kwargs=dict(pk=pk, vetting_mode=vetting_mode)
        )

        # then also preserve the query parameters
        query_str = self.request.session.pop("nle_id", "")
        print("QUERY STRING:", query_str)
        if query_str:
            base_url += f"?{query_str}"
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

        # then run the vetting, asynchronously
        messages.info(
            request,
            f"Vetting all candidates in {vetting_mode} vetting mode. This may take a few seconds per candidate; check back later.",
        )
        vet_all_async(ecs, nle, vetting_mode)

        return redirect(
            f"/eventcandidates/?nonlocalizedevent={nle.id}"
        )  # this redirects back to the NLE page



class NonLocalizedEventAssociateTargetsFormView(FormView):
    template_name = "candidate_vetting/nle_associate_targets_form.html"
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
            print(DETECTION_HORIZON_DEFAULTS[nle_most_likely_class])
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
        nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f%z")

        # helpful prints
        first_det_tmin_toprint = (nle_time + timedelta(days=first_det_tmin)).strftime("%Y-%m-%dT%H:%M:%S")
        first_det_tmax_toprint = (nle_time + timedelta(days=first_det_tmax)).strftime("%Y-%m-%dT%H:%M:%S")
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
                f"localization of {nle.event_id}, with first detection between"+
                f"{first_det_tmin_toprint} and {first_det_tmax_toprint}. This "+
                "will take a few seconds per candidate in the localization "+
                "region; check back later to see if new event candidates have "+
                "been created!"
            )

        # run the association asynchronously
        ### TODO

        return redirect(
            f"/eventcandidates/?nonlocalizedevent={nle.id}"
        )  # this redirects back to the NLE page