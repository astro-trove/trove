import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.urls import reverse_lazy
from django.http import HttpResponseRedirect, StreamingHttpResponse
from django.views.generic.base import RedirectView
from django.views.generic.edit import CreateView, TemplateResponseMixin, FormMixin, ProcessFormView, UpdateView
from django_filters.views import FilterView
from django.shortcuts import redirect
from guardian.mixins import PermissionListMixin

from tom_targets.models import Target, TargetList
from tom_targets.permissions import targets_for_user
from tom_dataproducts.models import ReducedDatum
from tom_targets.views import TargetNameSearchView as OldTargetNameSearchView, TargetListView as OldTargetListView
from tom_observations.views import ObservationCreateView as OldObservationCreateView
from tom_nonlocalizedevents.models import NonLocalizedEvent, EventLocalization, EventCandidate
from tom_surveys.models import SurveyObservationRecord
from tom_treasuremap.reporting import report_to_treasure_map
from .models import Candidate, SurveyFieldCredibleRegion
from .filters import CandidateFilter, CSSFieldCredibleRegionFilter, NonLocalizedEventFilter
from .forms import TargetListExtraFormset, TargetReportForm, TargetClassifyForm
from .forms import NonLocalizedEventFormHelper, CandidateFormHelper
from .forms import TNS_FILTER_CHOICES, TNS_INSTRUMENT_CHOICES, TNS_CLASSIFICATION_CHOICES
from .hooks import target_post_save, update_or_create_target_extra
from .tasks import target_run_mpc
from .templatetags.skymap_extras import get_preferred_localization
from .templatetags.target_extras import split_name

import json
import requests
import time
from io import StringIO

import paramiko
import os

from tom_catalogs.harvesters.tns import TNS_URL
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


class CandidateListView(FilterView):
    """
    View for listing candidates in the TOM.
    """
    template_name = 'tom_targets/candidate_list.html'
    paginate_by = 100
    strict = False
    model = Candidate
    filterset_class = CandidateFilter
    formhelper_class = CandidateFormHelper

    def get_filterset(self, filterset_class):
        kwargs = self.get_filterset_kwargs(filterset_class)
        filterset = filterset_class(**kwargs)
        filterset.form.helper = self.formhelper_class()
        return filterset

    def get_queryset(self):
        """
        Gets the set of ``Candidate`` objects associated with ``Target`` objects that the user has permission to view.

        :returns: Set of ``Candidate`` objects
        :rtype: QuerySet
        """
        return super().get_queryset().filter(
            target__in=targets_for_user(self.request.user, Target.objects.all(), 'view_target')
        ).annotate(detections=Count('target__candidate'))


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


class ObservationCreateView(OldObservationCreateView):
    """
    Modify the built-in ObservationCreateView to populate any "magnitude" field with the latest observed magnitude
    """
    template_name = 'tom_observations/observation_form.html'

    def get_initial(self):
        initial = super().get_initial()
        target = self.get_target()
        photometry = target.reduceddatum_set.filter(data_type='photometry')
        if photometry.exists():
            latest_photometry = photometry.latest().value
            if 'magnitude' in latest_photometry:
                initial['magnitude'] = latest_photometry['magnitude']
            elif 'limit' in latest_photometry:
                initial['magnitude'] = latest_photometry['limit']
        return initial


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

class TargetMPCView(LoginRequiredMixin, RedirectView):
    """
    View that runs or reruns the kilonova candidate vetting code and stores the results
    """
    def get(self, request, *args, **kwargs):
        """
        Method that handles the GET requests for this view. Calls the kilonova vetting code.
        """
        # get all detections of the target in question
        phot = ReducedDatum.objects.filter(target_id=kwargs["pk"], data_type="photometry",
                                           value__magnitude__isnull=False)
        if phot.exists():
            messages.info(request, "Running minor planet checker. Refresh after ~1 minute to see matches.")
            dramatiq_msg = target_run_mpc.send(phot.latest().id)  # check the latest detection
            logger.info(dramatiq_msg)
        else:
            messages.error(request, "Must have at least one photometric detection to run minor planet checker.")

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


class TargetListView(OldTargetListView):
    """
    View for listing targets in the TOM. Only shows targets that the user is authorized to view. Requires authorization.

    Identical to the built-in TargetListView but does not display unconfirmed candidates (names starting with "J")
    """
    def get_queryset(self):
        return super().get_queryset().exclude(name__startswith='J')


class CSSFieldListView(FilterView):
    """
    View for listing candidates in the TOM.
    """
    template_name = 'tom_nonlocalizedevents/cssfield_list.html'
    paginate_by = 100
    strict = False
    model = SurveyFieldCredibleRegion
    filterset_class = CSSFieldCredibleRegionFilter

    def get_eventlocalization(self):
        if 'localization_id' in self.kwargs:
            return EventLocalization.objects.get(id=self.kwargs['localization_id'])
        elif 'event_id' in self.kwargs:
            nle = NonLocalizedEvent.objects.get(event_id=self.kwargs['event_id'])
            return get_preferred_localization(nle)

    def get_nonlocalizedevent(self):
        if 'localization_id' in self.kwargs:
            localization = EventLocalization.objects.get(id=self.kwargs['localization_id'])
            return localization.nonlocalizedevent
        elif 'event_id' in self.kwargs:
            return NonLocalizedEvent.objects.get(event_id=self.kwargs['event_id'])

    def get_queryset(self):
        queryset = super().get_queryset()
        localization = self.get_eventlocalization()
        if localization is None:
            return queryset.none()
        else:
            return queryset.filter(localization=localization)

    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(object_list=object_list, **kwargs)
        context['nonlocalizedevent'] = self.get_nonlocalizedevent()
        context['eventlocalization'] = self.get_eventlocalization()
        return context


def generate_prog_file(css_credible_regions):
    return ','.join([cr.survey_field.name for cr in css_credible_regions]) + '\n'


def submit_to_css(css_credible_regions, event_id, request=None):
    filenames = []
    try:
        with paramiko.SSHClient() as ssh:
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(settings.CSS_HOSTNAME, username=settings.CSS_USERNAME,
                        disabled_algorithms={'pubkeys': ['rsa-sha2-256', 'rsa-sha2-512']})
            # See https://www.paramiko.org/changelog.html#2.9.0 for why disabled_algorithms is required
            sftp = ssh.open_sftp()
            for i, group in enumerate(css_credible_regions):
                filename = f'Saguaro_{event_id}_{i + 1:d}.prog'
                filenames.append(filename)
                with sftp.open(os.path.join(settings.CSS_DIRNAME, filename), 'w') as f:
                    f.write(generate_prog_file(group))
                banner = f'Submitted {filename} to CSS'
                logger.info(banner)
                if request is not None:
                    messages.success(request, banner)
    except Exception as e:
        logger.error(str(e))
        if request is not None:
            messages.error(request, str(e))
    return filenames


def create_observation_records(credible_regions, observation_id, user, facility, parameters=None):
    records = []
    for group, oid in zip(credible_regions, observation_id):
        for cr in group:
            record = SurveyObservationRecord.objects.create(
                survey_field=cr.survey_field,
                user=user,
                facility=facility,
                parameters=parameters or {},
                observation_id=oid,
                status='PENDING',
                scheduled_start=cr.scheduled_start,
            )
            cr.observation_record = record
            cr.save()
            records.append(record)
    return records


class CSSFieldExportView(CSSFieldListView):
    """
    View that handles the export of CSS Fields to .prog file(s).
    """
    def post(self, request, *args, **kwargs):
        css_credible_regions = self.get_selected_fields(request)
        text = ''.join([generate_prog_file(group) for group in css_credible_regions])
        return self.render_to_response(text)

    def get_selected_fields(self, request):
        target_ids = None if request.POST.get('isSelectAll') == 'True' else request.POST.getlist('selected-target')
        localization = self.get_eventlocalization()
        credible_regions = localization.surveyfieldcredibleregions.filter(group__isnull=False)
        if target_ids is not None:
            credible_regions = credible_regions.filter(id__in=target_ids)
        group_numbers = list(credible_regions.order_by('group').values_list('group', flat=True).distinct())
        # evaluate this as a list now to maintain the order
        groups = [list(credible_regions.filter(group=g).order_by('rank_in_group')) for g in group_numbers]
        return groups

    def render_to_response(self, text, **response_kwargs):
        """
        Returns a response containing the exported .prog file(s) of selected fields.

        :returns: response class with ASCII
        :rtype: StreamingHttpResponse
        """
        file_buffer = StringIO(text)
        file_buffer.seek(0)  # goto the beginning of the buffer
        response = StreamingHttpResponse(file_buffer, content_type="text/ascii")
        nle = self.get_nonlocalizedevent()
        filename = f"Saguaro_{nle.event_id}.prog"
        response['Content-Disposition'] = 'attachment; filename="{}"'.format(filename)
        return response


class CSSFieldSubmitView(LoginRequiredMixin, RedirectView, CSSFieldExportView):
    """
    View that handles the submission of CSS Fields to CSS and reporting to the GW Treasure Map.
    """
    def post(self, request, *args, **kwargs):
        """
        Method that handles the POST requests for this view.
        """
        css_credible_regions = self.get_selected_fields(request)
        nle = self.get_nonlocalizedevent()
        filenames = submit_to_css(css_credible_regions, nle.event_id, request=request)
        params = {'pos_angle': 0., 'depth': 20.5, 'depth_unit': 'ab_mag', 'band': 'open'}
        records = create_observation_records(css_credible_regions, filenames, request.user, 'CSS', params)
        response = report_to_treasure_map(records, nle)
        for message in response['SUCCESSES']:
            messages.success(request, message)
        for message in response['WARNINGS']:
            messages.warning(request, message)
        for message in response['ERRORS']:
            messages.error(request, message)
        return HttpResponseRedirect(self.get_redirect_url())

    def get_redirect_url(self):
        """
        Returns redirect URL as specified in the HTTP_REFERER field of the request.

        :returns: referer
        :rtype: str
        """
        referer = self.request.META.get('HTTP_REFERER', '/')
        return referer


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
