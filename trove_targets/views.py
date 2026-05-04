import logging

from django.shortcuts import render, redirect
from django.views.generic.edit import CreateView
from dal import autocomplete

from tom_common.hooks import run_hook

from tom_targets.views import TargetCreateView
from tom_targets.models import Target
from tom_targets.forms import TargetForm, SiderealTargetCreateForm

from tom_nonlocalizedevents.models import NonLocalizedEvent, EventCandidate

from .forms import TargetNLEForm, CustomSiderealTargetCreateForm

logger = logging.getLogger(__name__)

class NLEAutocompleteView(autocomplete.Select2QuerySetView):

    def get_queryset(self):
        qs = NonLocalizedEvent.objects.all()

        print(f"DEBUG: self.q = {self.q}")
        print(f"DEBUG: queryset count = {qs.count()}")
        if self.q:
            # Simple case-insensitive search on the name field
            qs = qs.filter(event_id__icontains=self.q)
        
        return qs

class CustomTargetCreateView(TargetCreateView):
    template_name = 'trove_targets/custom_target_create_form.html'

    def get_context_data(self, **kwargs):
        """
        Inserts certain form data into the context dict.

        :returns: Dictionary with the following keys:

                  `type_choices`: ``tuple``: Tuple of 2-tuples of strings containing available target types in the TOM

                  `extra_form`: ``FormSet``: Django formset with fields for arbitrary key/value pairs
        :rtype: dict
        """
        context = super(CustomTargetCreateView, self).get_context_data(**kwargs)
        context['type_choices'] = [("SIDEREAL", "Sidereal")] #Target.TARGET_TYPES
        #context['names_form'] = TargetNamesFormset(initial=[{'name': new_name}
        #                                                    for new_name
        #                                                    in self.request.GET.get('names', '').split(',')])
        #context['extra_form'] = TargetExtraFormset()

        context['target_nle_form'] = TargetNLEForm()
        
        return context

    def form_valid(self, form):

        # do the normal form_valid from the TOMToolkit TargetCreateView 
        super(TargetCreateView, self).form_valid(form)

        # then also associate this target with the chosen NLE

        # Get the newly created target
        target = self.object

        # Get the selected NLE from the POST data
        nle_id = self.request.POST.get('nle_select')

        if nle_id:
            try:
                logger.info("Creating an EventCandidate from the user specific target and NLE")
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

        # Run the target post save hook
        logger.info('Target post save hook: %s created: %s', target, True)
        run_hook('target_post_save', target=target, created=True)
        
        return redirect(self.get_success_url())

    def get_form_class(self):
        form_class = super(CustomTargetCreateView, self).get_form_class()
        print("ORIGINAL FORM CLASS:", form_class)
        if issubclass(form_class, SiderealTargetCreateForm):
            form_class = CustomSiderealTargetCreateForm
            
        print("Returning", form_class, form_class._meta.fields)
        return form_class
