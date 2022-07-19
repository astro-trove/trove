import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic.edit import CreateView, TemplateResponseMixin, FormMixin, ProcessFormView
from django_filters.views import FilterView
from django.shortcuts import redirect
from guardian.mixins import PermissionListMixin

from tom_targets.models import Target, TargetList
from custom_code.models import Candidate
from custom_code.filters import CandidateFilter
from .forms import TargetListExtraFormset, TargetReportForm, TNS_FILTER_CHOICES, TNS_INSTRUMENT_CHOICES

import json
import requests
from saguaro_tom import settings
# from tom_catalogs.harvesters.tns import TNS_URL
TNS_URL = 'https://sandbox.wis-tns.org/api'  # TODO: change this to the main site
TNS = settings.BROKERS['TNS']  # includes the API credentials
TNS_FILTER_IDS = {name: fid for fid, name in TNS_FILTER_CHOICES}
TNS_INSTRUMENT_IDS = {name: iid for iid, name in TNS_INSTRUMENT_CHOICES}

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


class TargetReportView(PermissionListMixin, TemplateResponseMixin, FormMixin, ProcessFormView):
    """
    View that handles reporting a target to the TNS.
    """
    form_class = TargetReportForm
    template_name = 'tom_targets/targetreport_form.html'

    def get_initial(self):
        target = Target.objects.get(pk=self.kwargs['pk'])
        initial = {
            'ra': target.ra,
            'dec': target.dec,
            'reporter': f'{self.request.user.get_full_name()}, on behalf of SAGUARO',
        }
        if target.reduceddatum_set.exists():
            reduced_datum = target.reduceddatum_set.latest()
            initial['obsdate'] = reduced_datum.timestamp
            initial['flux'] = reduced_datum.value['magnitude']
            initial['flux_error'] = reduced_datum.value['error']
            initial['filter_value'] = (TNS_FILTER_IDS.get(reduced_datum.value['filter'], 0),
                                       reduced_datum.value['filter'])
            initial['instrument_value'] = (TNS_INSTRUMENT_IDS.get(reduced_datum.value['instrument'], 0),
                                           reduced_datum.value['instrument'])
        return initial

    def form_valid(self, form):
        report_data = {
            "at_report": {
                "0": {
                    "ra": {
                        "value": form.cleaned_data['ra'],
                    },
                    "dec": {
                        "value": form.cleaned_data['dec'],
                    },
                    "reporting_group_id": form.cleaned_data['reporting_group_id'],
                    "discovery_data_source_id": form.cleaned_data['discovery_data_source_id'],
                    "reporter": form.cleaned_data['reporter'],
                    "discovery_datetime": form.cleaned_data['discovery_datetime'].strftime('%Y-%m-%d %H:%M:%S'),
                    "at_type": form.cleaned_data['at_type'],
                    "non_detection": {
                        "archiveid": form.cleaned_data['non_detection__archiveid'],
                        "archival_remarks": form.cleaned_data['non_detection__archival_remarks'],
                    },
                    "photometry": {
                        "photometry_group": {
                            "0": {
                                "obsdate": form.cleaned_data['obsdate'].strftime('%Y-%m-%d %H:%M:%S'),
                                "flux": form.cleaned_data['flux'],
                                "flux_error": form.cleaned_data['flux_error'],
                                "flux_units": form.cleaned_data['flux_units'],
                                "filter_value": form.cleaned_data['filter_value'],
                                "instrument_value": form.cleaned_data['instrument_value'],
                                "limiting_flux": form.cleaned_data['limiting_flux'],
                                "exptime": form.cleaned_data['exptime'],
                                "observer": form.cleaned_data['observer'],
                                "comments": form.cleaned_data['comments'],
                            },
                        }
                    },
                }
            }
        }

        # submit the data to the TNS
        tns_marker = 'tns_marker' + json.dumps({'tns_id': TNS['bot_id'], 'type': 'bot', 'name': TNS['bot_name']})
        json_data = {'api_key': TNS['api_key'], 'data': json.dumps(report_data)}
        response = requests.post(TNS_URL + '/bulk-report', headers={'User-Agent': tns_marker}, data=json_data)
        response.raise_for_status()

        # get the response from the TNS
        json_data = {'api_key': TNS['api_key'], 'report_id': response.json()['data']['report_id']}
        response = requests.post(TNS_URL + '/bulk-report-reply', headers={'User-Agent': tns_marker}, data=json_data)
        response.raise_for_status()
        feedback = response.json()['data']['feedback']['at_report'][0]
        for msg in feedback.values():
            prefix = msg['prefix']
            objname = msg['objname']

        # update the target name
        target = Target.objects.get(pk=self.kwargs['pk'])
        target.name = prefix + objname
        target.save()
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse_lazy('targets:detail', kwargs=self.kwargs)
