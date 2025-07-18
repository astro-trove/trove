"""
Page views for candidate vetting
"""
from urllib.parse import urlparse, parse_qs

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic.base import RedirectView
from django.http import HttpResponseRedirect

from trove_targets.models import Target
from custom_code.hooks import target_post_save

import requests

class TargetVettingView(LoginRequiredMixin, RedirectView):
    """
    View that runs or reruns the kilonova candidate vetting code and stores the results
    """
    def get(self, request, *args, **kwargs):
        """
        Method that handles the GET requests for this view. Calls the kilonova vetting code.
        """        
        target = Target.objects.get(pk=kwargs['pk'])

        # get the nonlocalized event name from the referer
        query_params = parse_qs(
            urlparse(
                request.META.get("HTTP_REFERER")
            ).query
        )
        nonlocalized_event_name = query_params.get("nonlocalizedevent")
        if nonlocalized_event_name is not None:
            # because parse_qs returns lists for each query item
            nonlocalized_event_name = nonlocalized_event_name[0]

        banners, tns_query_status = target_post_save(
            target,
            created=True,
            nonlocalized_event_name=nonlocalized_event_name
        )
        for banner in banners:
            messages.success(request, banner)

        if tns_query_status is not None:
            messages.add_message(request,99,tns_query_status)

        return HttpResponseRedirect(self.get_redirect_url())

    def get_redirect_url(self):
        """
        Returns redirect URL as specified in the HTTP_REFERER field of the request.

        :returns: referer
        :rtype: str
        """
        referer = self.request.META.get('HTTP_REFERER', '/')
        return referer
