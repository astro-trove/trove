"""
Page views for candidate vetting
"""
from urllib.parse import urlparse, parse_qs

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic.base import RedirectView
from django.views.generic.edit import FormView
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy, reverse
from django.shortcuts import redirect

from trove_targets.models import Target
from .forms import VettingChoiceForm 
from candidate_vetting.vet_bns import vet_bns
from candidate_vetting.vet_kn_in_sn import vet_kn_in_sn
from candidate_vetting.vet_super_kn import vet_super_kn
from candidate_vetting.vet_basic import vet_basic
from candidate_vetting.vet_phot import find_public_phot
from candidate_vetting.public_catalogs.phot_catalogs import ZTF_Forced_Phot

import requests

from .config import FORM_CHOICE_FUNC_MAP

class TargetVettingFormView(FormView):
    template_name = "candidate_vetting/vetting_form.html"
    form_class = VettingChoiceForm

    # TODO: Only give the user the form if there is a non-localized event associated
    #       with this target. If there isn't, this should just redirect to the basic
    #       target vetting!

    def get(self, request, *args, **kwargs):
        referer = request.META.get("HTTP_REFERER")
        if referer:
            self.request.session['nle_id'] = urlparse(referer).query
        return super().get(request, *args, **kwargs)
    
    def form_valid(self, form):
        pk = self.kwargs["pk"]
        vetting_mode = form.cleaned_data["vetting_method"]
        
        # generate the base url
        base_url = reverse(
            'candidate_vetting:vet',
            kwargs=dict(
                pk=pk,
                vetting_mode=vetting_mode
            )
        )

        # then also preserve the query parameters
        query_str = self.request.session.pop('nle_id', '')
        print("QUERY STRING:", query_str)
        if query_str:
            base_url += f"?{query_str}"
                    
        return redirect(base_url)
    
class TargetVettingView(LoginRequiredMixin, RedirectView):
    """
    View that runs or reruns the kilonova candidate vetting code and stores the results
    """
    def get(self, request, *args, **kwargs):
        """
        Method that handles the GET requests for this view. Calls the vetting 
        code for different transients.
        """        
        target_pk = kwargs['pk']
        target = Target.objects.get(pk=target_pk)
        vetting_mode = kwargs.get("vetting_mode", "basic")

        # get the nonlocalized event name from the referer
        nonlocalized_event_name = request.GET.get("nonlocalizedevent")

        # then run the vetting
        vetting_func = FORM_CHOICE_FUNC_MAP[vetting_mode]
        if vetting_mode == "basic" or nonlocalized_event_name is None:
            vet_basic(target.id)
        else:
            vetting_func(target.id, nonlocalized_event_name)
        
        return redirect(
            reverse(
                "targets:detail",
                kwargs=dict(pk=target_pk)
            )
        ) # this redirects back to the original target page
        
class TargetFPView(LoginRequiredMixin, RedirectView):
    """
    Class to run forced photometry for a target 
    """

    def get(self, request, *args, **kwargs):

        messages.info(request, "Checking for new public forced photometry. This can take ~minutes for ATLAS and ~hours-days for ZTF. We suggest you check back later.")
        
        target = Target.objects.get(id=kwargs['pk'])

        # check TNS and ATLAS
        find_public_phot(
            target=target,
            days_ago_max=365,
            queue_priority=0
        )

        # then also run ZTF forced photometry
        # this will only actually be ingested after the ZTF forced photometry runs
        ztf = ZTF_Forced_Phot()
        ztf.query(
            target=target,
            days_ago=365
        )
        
        return HttpResponseRedirect(self.get_redirect_url())

    def get_redirect_url(self):
        """
        Returns redirect URL as specified in the HTTP_REFERER field of the request.

        :returns: referer
        :rtype: str
        """
        referer = self.request.META.get('HTTP_REFERER', '/')
        return referer
