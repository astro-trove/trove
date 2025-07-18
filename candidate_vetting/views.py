"""
Page views for candidate vetting
"""

class TargetVettingView(LoginRequiredMixin, RedirectView):
    """
    View that runs or reruns the kilonova candidate vetting code and stores the results
    """
    def get(self, request, *args, **kwargs):
        """
        Method that handles the GET requests for this view. Calls the kilonova vetting code.
        """        
        target = Target.objects.get(pk=kwargs['pk'])
        banners, tns_query_status = target_post_save(target, created=True)
        for banner in banners:
            messages.success(request, banner)

        if tns_query_status is not None:
            messages.add_message(request,99,tns_query_status)

        return HttpResponseRedirect(self.get_redirect_url())

    def get_redirect_url(self, nonlocalized_event_name):
        """
        Returns redirect URL as specified in the HTTP_REFERER field of the request.

        :returns: referer
        :rtype: str
        """
        referer = self.request.META.get('HTTP_REFERER', '/')
        return referer
