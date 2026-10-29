import json
import logging
import time

import requests
from dal import autocomplete
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.postgres.aggregates import StringAgg
from django.db.models import CharField, Min, Q, Value
from django.db.models.functions import Cast, Replace, Round
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic.edit import FormMixin, ProcessFormView, TemplateResponseMixin
from guardian.mixins import PermissionListMixin
from tom_catalogs.harvesters.tns import TNS_URL
from tom_common.hooks import run_hook
from tom_nonlocalizedevents.models import EventCandidate, NonLocalizedEvent
from tom_targets.forms import SiderealTargetCreateForm
from tom_targets.models import BaseTarget
from tom_targets.permissions import targets_for_user
from tom_targets.views import TargetCreateView, TargetListView
from tom_targets.views import TargetNameSearchView as OldTargetNameSearchView

from trove_targets.templatetags.target_extras import split_name
from trove_targets.models import Target

from .filters import TroveTargetListFilterSet
from .forms import (
    TNS_CLASSIFICATION_CHOICES,
    TNS_FILTER_CHOICES,
    TNS_INSTRUMENT_CHOICES,
    CustomSiderealTargetCreateForm,
    TargetClassifyForm,
    TargetNLEForm,
    TargetReportForm,
)
from .hooks import update_or_create_target_extra
from .tables import TroveTargetTable

logger = logging.getLogger(__name__)

PERMISSIONS_MAP = {
    "PUBLIC": BaseTarget.Permissions.PUBLIC,
    "PRIVATE": BaseTarget.Permissions.PRIVATE,
    "OPEN": BaseTarget.Permissions.OPEN,
}

TNS = settings.BROKERS['TNS']  # includes the API credentials
TNS_MARKER = 'tns_marker' + json.dumps({'tns_id': TNS['bot_id'], 'type': 'bot', 'name': TNS['bot_name']})
TNS_FILTER_IDS = {name: fid for fid, name in TNS_FILTER_CHOICES}
TNS_INSTRUMENT_IDS = {name: iid for iid, name in TNS_INSTRUMENT_CHOICES}
TNS_CLASSIFICATION_IDS = {name: cid for cid, name in TNS_CLASSIFICATION_CHOICES}

class NLEAutocompleteView(LoginRequiredMixin, autocomplete.Select2QuerySetView):
    def get_queryset(self):
        qs = NonLocalizedEvent.objects.all()

        if self.q:
            # Simple case-insensitive search on the name field
            qs = qs.filter(event_id__icontains=self.q)

        return qs


class TargetAutocompleteView(LoginRequiredMixin, autocomplete.Select2QuerySetView):
    def get_queryset(self):
        # only what this user may view -- an unfiltered queryset let anyone
        # enumerate every target name a substring at a time
        qs = targets_for_user(self.request.user, Target.objects.all(), "view_target")

        if self.q:
            qs = qs.filter(name__icontains=self.q)

        # Filter list to remove any events that are already associated with event
        nle_id = self.forwarded.get("nonlocalizedevent")
        if nle_id:
            qs = qs.exclude(eventcandidate__nonlocalizedevent_id=nle_id)

        return qs


class CustomTargetCreateView(TargetCreateView):
    template_name = "trove_targets/custom_target_create_form.html"

    def get_context_data(self, **kwargs):
        """
        Inserts certain form data into the context dict.

        :returns: Dictionary with the following keys:

                  `type_choices`: ``tuple``: Tuple of 2-tuples of strings containing available target types in the TOM

                  `extra_form`: ``FormSet``: Django formset with fields for arbitrary key/value pairs
        :rtype: dict
        """
        context = super(CustomTargetCreateView, self).get_context_data(**kwargs)
        context["type_choices"] = [("SIDEREAL", "Sidereal")]  # Target.TARGET_TYPES
        # context['names_form'] = TargetNamesFormset(initial=[{'name': new_name}
        #                                                    for new_name
        #                                                    in self.request.GET.get('names', '').split(',')])
        # context['extra_form'] = TargetExtraFormset()

        context["target_nle_form"] = TargetNLEForm()

        return context

    def form_valid(self, form):

        # do the normal form_valid from the TOMToolkit TargetCreateView
        super(TargetCreateView, self).form_valid(form)

        # then also associate this target with the chosen NLE

        # Get the newly created target
        target = self.object

        # Get the selected NLE from the POST data
        nle_id = self.request.POST.get("nle_select")

        if nle_id:
            try:
                logger.info(
                    "Creating an EventCandidate from the user specific target and NLE"
                )
                ec, created = EventCandidate.objects.get_or_create(
                    target=target,
                    nonlocalizedevent=NonLocalizedEvent.objects.get(id=nle_id),
                )
            except NonLocalizedEvent.DoesNotExist:
                logger.info(f"User passed an invalid NLE id {nle_id}, skipping!")
                form.add_error(None, "Invalid NLE Event ID!")
                return super().form_invalid(form)

            logger.info("EventCandidate successfully created!")

        # Give the user access to the target they created
        self.object.give_user_access(self.request.user)

        # then also set the global permission group of the object to the default
        # TODO: If we ever change the permissions functionality and allow users to set
        #       permissions on upload, we need to change this!
        self.object.permissions = PERMISSIONS_MAP[settings.TARGET_DEFAULT_PERMISSION]
        self.object.save()

        # Run the target post save hook
        logger.info("Target post save hook: %s created: %s", target, True)
        run_hook("target_post_save", target=target, created=True)

        return redirect(self.get_success_url())

    def get_form_class(self):
        form_class = super(CustomTargetCreateView, self).get_form_class()
        print("ORIGINAL FORM CLASS:", form_class)
        if issubclass(form_class, SiderealTargetCreateForm):
            form_class = CustomSiderealTargetCreateForm

        print("Returning", form_class, form_class._meta.fields)
        return form_class

class TroveTargetListView(TargetListView):
    table_class = TroveTargetTable
    filterset_class = TroveTargetListFilterSet

    ordering = ['-created']

    def get_queryset(self, *args, **kwargs):
        qs = super().get_queryset(*args, **kwargs)

        # add the redshift to the queryset in a nice format for the table
        qs = qs.annotate(
            _z = Replace(
                Cast("redshift", output_field=CharField()),
                Value("NaN"),
                Value("—"),
                output_field=CharField()
            )
        )

        # add the date of first detection
        qs = qs.annotate(
            first_detection=Min(
                "photometryreduceddatum__timestamp",
                filter=Q(
                    ~Q(photometryreduceddatum__source_name__in=["ATLAS", "ATLAS FP"]), # exclude ATLAS FP which can have spurious detections
                    photometryreduceddatum__brightness__isnull=False, # make sure it is a detection, not a limit
                )
            )
        )

        # associated NLEs
        qs = qs.annotate(
            associated_events = StringAgg(
                "eventcandidate__nonlocalizedevent__event_id",
                delimiter="\n",
                distinct=True,
                order_by="eventcandidate__nonlocalizedevent__event_id"
            )
        )

        # round the RA/Dec columns to consistent decimal places
        radec_precision = 6
        qs = qs.annotate(
            _ra=Round("ra", precision=radec_precision),
            _dec=Round("dec", precision=radec_precision),
        )

        return qs



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
        photometry = target.photometryreduceddatum_set.all()
        if photometry.exists():
            reduced_datum = photometry.latest()
            initial['observation_date'] = reduced_datum.timestamp
            initial['flux'] = reduced_datum.brightness
            initial['flux_error'] = reduced_datum.brightness_error
            filter_name = reduced_datum.bandpass
            if filter_name in TNS_FILTER_IDS:
                initial['filter'] = (TNS_FILTER_IDS[filter_name], filter_name)
            instrument_name = reduced_datum.instrument
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
        if target.redshift:
            initial['redshift'] = target.redshift
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
                target.redshift = form.cleaned_data['redshift']
                messages.success(self.request, f"Redshift set to {form.cleaned_data['redshift']}")

        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse_lazy('targets:detail', kwargs=self.kwargs)


class TargetNameSearchView(OldTargetNameSearchView):
    """
    View for searching by target name. If the search returns one result, the view redirects to the corresponding
    TargetDetailView. Otherwise, the view redirects to the TargetListView.
    """

    def get(self, request, *args, **kwargs):
        self.kwargs['name'] = self.kwargs["search_str"]
        return super().get(request, *args, **kwargs)




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
