import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic.edit import CreateView
from django_filters.views import FilterView
from django.shortcuts import redirect
from guardian.mixins import PermissionListMixin

from tom_targets.models import Target, TargetList
from custom_code.models import Candidate
from custom_code.filters import CandidateFilter
from .forms import TargetListExtraFormset

logger = logging.getLogger(__name__)


class TargetGroupingCreateView(LoginRequiredMixin, CreateView):
    """
    View that handles the creation of ``TargetList`` objects, also known as target groups. Requires authentication.
    """
    model = TargetList
    fields = ['name']
    success_url = reverse_lazy('targets:targetgrouping')
    template_name = 'tom_targets/targetlist_form.html'

    def form_valid(self, form):
        """
        Runs after form validation. Creates the ``TargetList``, and creates any ``TargetListExtra`` objects,
        then redirects to the success URL.

        :param form: Form data for target creation
        :type form: subclass of TargetCreateForm
        """
        super().form_valid(form)
        extra = TargetListExtraFormset(self.request.POST)
        if extra.is_valid():
            extra.instance = self.object
            extra.save()
        else:
            form.add_error(None, extra.errors)
            form.add_error(None, extra.non_form_errors())
            return super().form_invalid(form)
        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        """
        Inserts certain form data into the context dict.

        :returns: Dictionary with the following keys:

                  `type_choices`: ``tuple``: Tuple of 2-tuples of strings containing available target types in the TOM

                  `extra_form`: ``FormSet``: Django formset with fields for arbitrary key/value pairs
        :rtype: dict
        """
        context = super(TargetGroupingCreateView, self).get_context_data(**kwargs)
        context['extra_form'] = TargetListExtraFormset()
        return context


class CandidateListView(PermissionListMixin, FilterView):
    """
    View for listing candidates in the TOM.
    """
    template_name = 'tom_targets/candidate_list.html'
    paginate_by = 25
    strict = False
    model = Candidate
    filterset_class = CandidateFilter

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        return context
