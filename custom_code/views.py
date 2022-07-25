import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.http import HttpResponseRedirect
from django.views.generic.base import RedirectView
from django.views.generic.edit import CreateView, TemplateResponseMixin, FormMixin, ProcessFormView
from django_filters.views import FilterView
from django.shortcuts import redirect
from guardian.mixins import PermissionListMixin

from tom_targets.models import Target, TargetList, TargetExtra
from custom_code.models import Candidate
from custom_code.filters import CandidateFilter
from .forms import TargetListExtraFormset, TargetReportForm, TargetClassifyForm
from .forms import TNS_FILTER_CHOICES, TNS_INSTRUMENT_CHOICES, TNS_CLASSIFICATION_CHOICES

import json
import requests
from saguaro_tom import settings
import time

from kne_cand_vetting.catalogs import static_cats_query
from kne_cand_vetting.galaxy_matching import galaxy_search

# from tom_catalogs.harvesters.tns import TNS_URL
TNS_URL = 'https://sandbox.wis-tns.org/api'  # TODO: change this to the main site
TNS = settings.BROKERS['TNS']  # includes the API credentials
TNS_MARKER = 'tns_marker' + json.dumps({'tns_id': TNS['bot_id'], 'type': 'bot', 'name': TNS['bot_name']})
TNS_FILTER_IDS = {name: fid for fid, name in TNS_FILTER_CHOICES}
TNS_INSTRUMENT_IDS = {name: iid for iid, name in TNS_INSTRUMENT_CHOICES}
TNS_CLASSIFICATION_IDS = {name: cid for cid, name in TNS_CLASSIFICATION_CHOICES}

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


def upload_files_to_tns(files):
    """
    Upload files to the Transient Name Server according to this manual:
    https://sandbox.wis-tns.org/sites/default/files/api/TNS_bulk_reports_manual.pdf
    """
    json_data = {'api_key': TNS['api_key']}
    response = requests.post(TNS_URL + '/file-upload', headers={'User-Agent': TNS_MARKER}, data=json_data, files=files)
    response.raise_for_status()
    new_filenames = response.json()['data']
    logger.info(f"Uploaded {', '.join(new_filenames)} to the TNS")
    return new_filenames


def send_tns_report(data):
    """
    Send a JSON bulk report to the Transient Name Server according to this manual:
    https://sandbox.wis-tns.org/sites/default/files/api/TNS_bulk_reports_manual.pdf
    """
    json_data = {'api_key': TNS['api_key'], 'data': data}
    response = requests.post(TNS_URL + '/bulk-report', headers={'User-Agent': TNS_MARKER}, data=json_data)
    response.raise_for_status()
    report_id = response.json()['data']['report_id']
    logger.info(f'Sent TNS report ID {report_id:d}')
    return report_id


def get_tns_report_reply(report_id, request):
    """
    Get feedback from the Transient Name Server in response to a bulk report according to this manual:
    https://sandbox.wis-tns.org/sites/default/files/api/TNS_bulk_reports_manual.pdf

    Posts an informational message in a banner on the page using ``request``
    """
    json_data = {'api_key': TNS['api_key'], 'report_id': report_id}
    for _ in range(6):
        time.sleep(5)
        response = requests.post(TNS_URL + '/bulk-report-reply', headers={'User-Agent': TNS_MARKER}, data=json_data)
        if response.ok:
            break
    response.raise_for_status()
    feedback_section = response.json()['data']['feedback']
    feedbacks = []
    if 'at_report' in feedback_section:
        feedbacks += feedback_section['at_report']
    if 'classification_report' in feedback_section:
        feedbacks += feedback_section['classification_report'][0]['classification_messages']
    for feedback in feedbacks:
        if '100' in feedback:  # transient object was inserted
            iau_name = 'AT' + feedback['100']['objname']
            log_message = f'New transient {iau_name} was created'
            logger.info(log_message)
            messages.success(request, log_message)
            break
        elif '101' in feedback:  # transient object exists
            iau_name = feedback['101']['prefix'] + feedback['101']['objname']
            log_message = f'Existing transient {iau_name} was reported'
            logger.info(log_message)
            messages.info(request, log_message)
            break
        elif '121' in feedback:  # object name prefix has changed
            iau_name = feedback['121']['new_object_name']
            log_message = f'Transient name changed to {iau_name}'
            logger.info(log_message)
            messages.success(request, log_message)
            break
    else:  # this should never happen
        iau_name = None
        log_message = 'Problem getting response from TNS'
        logger.error(log_message)
        messages.error(request, log_message)
    return iau_name


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
        photometry = target.reduceddatum_set.filter(data_type='photometry')
        if photometry.exists():
            reduced_datum = photometry.latest()
            initial['obsdate'] = reduced_datum.timestamp
            initial['flux'] = reduced_datum.value['magnitude']
            initial['flux_error'] = reduced_datum.value['error']
            filter_name = reduced_datum.value.get('filter')
            if filter_name in TNS_FILTER_IDS:
                initial['filter_value'] = (TNS_FILTER_IDS[filter_name], filter_name)
            instrument_name = reduced_datum.value.get('instrument')
            if instrument_name in TNS_INSTRUMENT_IDS:
                initial['instrument_value'] = (TNS_INSTRUMENT_IDS[instrument_name], instrument_name)
        return initial

    def form_valid(self, form):
        report_id = send_tns_report(form.generate_tns_report())
        iau_name = get_tns_report_reply(report_id, self.request)

        # update the target name
        if iau_name is not None:
            target = Target.objects.get(pk=self.kwargs['pk'])
            target.name = iau_name
            target.save()
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse_lazy('targets:detail', kwargs=self.kwargs)


class TargetClassifyView(PermissionListMixin, TemplateResponseMixin, FormMixin, ProcessFormView):
    """
    View that handles classifying a target on the TNS.
    """
    form_class = TargetClassifyForm
    template_name = 'tom_targets/targetclassify_form.html'

    def get_initial(self):
        target = Target.objects.get(pk=self.kwargs['pk'])
        initial = {
            'name': target.name.replace('AT', '').replace('SN', ''),
            'classifier': f'{self.request.user.get_full_name()}, on behalf of SAGUARO',
        }
        classifications = target.targetextra_set.filter(key='Classification')
        if classifications.exists():
            classification = classifications.first().value
            if classification in TNS_CLASSIFICATION_IDS:
                initial['classification'] = (TNS_CLASSIFICATION_IDS[classification], classification)
        redshift = target.targetextra_set.filter(key='Redshift')
        if redshift.exists():
            initial['redshift'] = redshift.first().value
        spectra = target.reduceddatum_set.filter(data_type='spectroscopy')
        if spectra.exists():
            spectrum = spectra.latest()
            initial['observation_date'] = spectrum.timestamp
            initial['ascii_file'] = spectrum.data_product.data
        return initial

    def form_valid(self, form):
        new_filenames = upload_files_to_tns(form.files_to_upload())
        report_id = send_tns_report(form.generate_tns_report(new_filenames))
        iau_name = get_tns_report_reply(report_id, self.request)

        # update the target name
        if iau_name is not None:
            target = Target.objects.get(pk=self.kwargs['pk'])
            target.name = iau_name
            target.save()
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse_lazy('targets:detail', kwargs=self.kwargs)


def update_or_create_target_extra(target, key, value):
    """
    Check if a ``TargetExtra`` with the given key exists for a given target. If it exists, update the value. If it does
    not exist, create it with the input value.
    """
    te, created = TargetExtra.objects.get_or_create(target=target, key=key)
    te.value = value
    te.save()


class TargetVettingView(LoginRequiredMixin, RedirectView):
    """
    View that runs or reruns the kilonova candidate vetting code and stores the results
    """
    def get(self, request, *args, **kwargs):
        """
        Method that handles the GET requests for this view. Calls the kilonova vetting code.
        """
        target = Target.objects.get(pk=kwargs['pk'])
        qprob, qso, qoffset, asassnprob, asassn, asassnoffset = static_cats_query([target.ra], [target.dec])

        update_or_create_target_extra(target=target, key='QSO Match', value=qso[0])
        if qso[0] != 'None':
            update_or_create_target_extra(target=target, key='QSO Prob.', value=qprob[0])
            update_or_create_target_extra(target=target, key='QSO Offset', value=qoffset[0])

        update_or_create_target_extra(target=target, key='ASASSN Match', value=asassn[0])
        if asassn[0] != 'None':
            update_or_create_target_extra(target=target, key='ASASSN Prob.', value=asassnprob[0])
            update_or_create_target_extra(target=target, key='ASASSN Offset', value=asassnoffset[0])

        matches, hostdict = galaxy_search([target.ra], [target.dec])
        update_or_create_target_extra(target=target, key='Host Galaxies', value=json.dumps(hostdict))

        return HttpResponseRedirect(self.get_redirect_url())

    def get_redirect_url(self):
        """
        Returns redirect URL as specified in the HTTP_REFERER field of the request.

        :returns: referer
        :rtype: str
        """
        referer = self.request.META.get('HTTP_REFERER', '/')
        return referer
