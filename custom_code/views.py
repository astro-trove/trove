import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.http import HttpResponseRedirect
from django.views.generic.base import RedirectView
from django.views.generic.edit import TemplateResponseMixin, FormMixin, ProcessFormView, UpdateView
from django_filters.views import FilterView
from django.shortcuts import redirect
from guardian.mixins import PermissionListMixin

from tom_targets.models import Target
from tom_targets.views import TargetNameSearchView as OldTargetNameSearchView
from tom_nonlocalizedevents.models import NonLocalizedEvent, EventCandidate
from .filters import NonLocalizedEventFilter
from .forms import TargetReportForm, TargetClassifyForm
from .forms import NonLocalizedEventFormHelper
from .forms import TNS_FILTER_CHOICES, TNS_INSTRUMENT_CHOICES, TNS_CLASSIFICATION_CHOICES
from .hooks import target_post_save, update_or_create_target_extra
from .templatetags.target_extras import split_name

import json
import requests
import time

from tom_catalogs.harvesters.tns import TNS_URL
TNS = settings.BROKERS['TNS']  # includes the API credentials
TNS_MARKER = 'tns_marker' + json.dumps({'tns_id': TNS['bot_id'], 'type': 'bot', 'name': TNS['bot_name']})
TNS_FILTER_IDS = {name: fid for fid, name in TNS_FILTER_CHOICES}
TNS_INSTRUMENT_IDS = {name: iid for iid, name in TNS_INSTRUMENT_CHOICES}
TNS_CLASSIFICATION_IDS = {name: cid for cid, name in TNS_CLASSIFICATION_CHOICES}

logger = logging.getLogger(__name__)


def upload_files_to_tns(files):
    """
    Upload files to the Transient Name Server according to this manual:
    https://sandbox.wis-tns.org/sites/default/files/api/TNS_bulk_reports_manual.pdf
    """
    json_data = {'api_key': TNS['api_key']}
    response = requests.post(TNS_URL + '/set/file-upload', headers={'User-Agent': TNS_MARKER}, data=json_data, files=files)
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
    response = requests.post(TNS_URL + '/set/bulk-report', headers={'User-Agent': TNS_MARKER}, data=json_data)
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
        response = requests.post(TNS_URL + '/get/bulk-report-reply', headers={'User-Agent': TNS_MARKER}, data=json_data)
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['target'] = Target.objects.get(pk=self.kwargs['pk'])
        return context

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
            initial['observation_date'] = reduced_datum.timestamp
            initial['flux'] = reduced_datum.value.get('magnitude')
            initial['flux_error'] = reduced_datum.value.get('error')
            filter_name = reduced_datum.value.get('filter')
            if filter_name in TNS_FILTER_IDS:
                initial['filter'] = (TNS_FILTER_IDS[filter_name], filter_name)
            instrument_name = reduced_datum.value.get('instrument')
            if instrument_name in TNS_INSTRUMENT_IDS:
                initial['instrument'] = (TNS_INSTRUMENT_IDS[instrument_name], instrument_name)
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['target'] = Target.objects.get(pk=self.kwargs['pk'])
        return context

    def get_initial(self):
        target = Target.objects.get(pk=self.kwargs['pk'])
        initial = {
            'name': split_name(target.name)['basename'],
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

            classification = dict(TNS_CLASSIFICATION_CHOICES)[int(form.cleaned_data['classification'])]
            update_or_create_target_extra(target, 'Classification', classification)
            messages.success(self.request, f"Classification set to {classification}")
            if form.cleaned_data['redshift']:
                update_or_create_target_extra(target, 'Redshift', form.cleaned_data['redshift'])
                messages.success(self.request, f"Redshift set to {form.cleaned_data['redshift']}")

        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse_lazy('targets:detail', kwargs=self.kwargs)


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

    def get_redirect_url(self):
        """
        Returns redirect URL as specified in the HTTP_REFERER field of the request.

        :returns: referer
        :rtype: str
        """
        referer = self.request.META.get('HTTP_REFERER', '/')
        return referer


class TargetNameSearchView(OldTargetNameSearchView):
    """
    View for searching by target name. If the search returns one result, the view redirects to the corresponding
    TargetDetailView. Otherwise, the view redirects to the TargetListView.
    """

    def get(self, request, *args, **kwargs):
        self.kwargs['name'] = request.GET.get('name').strip()
        return super().get(request, *args, **kwargs)


class NonLocalizedEventListView(FilterView):
    """
    Unadorned Django ListView subclass for NonLocalizedEvent model.
    """
    model = NonLocalizedEvent
    filterset_class = NonLocalizedEventFilter
    paginate_by = 100
    formhelper_class = NonLocalizedEventFormHelper

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

    def get_queryset(self):
        qs = NonLocalizedEvent.objects.filter(event_type='GW').order_by('-created')
        return qs


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
        EventCandidate.objects.create(nonlocalizedevent=nonlocalizedevent, target=target,
                                      viability_reason=viability_reason)
        return HttpResponseRedirect(self.get_redirect_url())

    def get_redirect_url(self):
        """
        Returns redirect URL as specified in the HTTP_REFERER field of the request.

        :returns: referer
        :rtype: str
        """
        referer = self.request.META.get('HTTP_REFERER', '/')
        return referer
