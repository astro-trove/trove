from django.conf import settings
from django import forms
import django_filters

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, Row, Column, HTML

from tom_targets.filters import TargetFilterSet
from tom_targets.utils import cone_search_filter

class TroveTargetListFilterSet(TargetFilterSet):
    """
    A custom filterset form for the target list page, modified from the existing one
    implemented in the TOMToolkit
    """

    # define the django_filters filter classes
    target_name_search = django_filters.CharFilter(
        field_name='name',
        method='general_search',
        label='Candidate Name',
        widget=forms.TextInput
    )

    
    has_associated_events = django_filters.BooleanFilter(
        field_name='associated_events',
        method='filter_has_associated_events',
        label='Only show candidates associated with events',
        widget=forms.CheckboxInput,
    )
    
    associated_event = django_filters.CharFilter(
        field_name="associated_events",
        method="filter_associated_event",
        label="Associated Event",
        widget=forms.TextInput
    )
    
    # define the methods that actually do the filtering
    def filter_has_associated_events(self, queryset, name, value):
        if value:
            return queryset.filter(
                eventcandidate__nonlocalizedevent__isnull=False
            ).distinct().order_by("-associated_events")
        return queryset.order_by("-associated_events")

    def filter_associated_event(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(associated_events__icontains=value)

        
    @property
    def form(self):
        """Override form property to configure crispy forms helper. This is to remove
        the Submit button which is not needed because HTMX is making AJAX requests.

        Also, add the FormHelper.Layout definition
        """
        if not hasattr(self, '_form'):
            self._form = super().form
            # Configure crispy forms helper - no submit button, no form tag
            self._form.helper = FormHelper()
            self._form.helper.form_tag = False  # Don't render <form> tags (template handles it)
            self._form.helper.disable_csrf = True  # Template handles CSRF if needed
            self._form.helper.form_show_labels = True  # Explicitly clear any inputs/buttons

            # Prepare extra fields for the layout
            extra_field_names = [f['name'] for f in settings.EXTRA_FIELDS]
            extra_columns = [Column(name, css_class='form-group col-md-3') for name in extra_field_names]

            # Define the structure using Bootstrap Grid (Row/Column)
            self._form.helper.layout = Layout(
                # Row 1: Primary Search parameters
                Row(
                    Column('target_name_search', css_class='form-group col-md-4', title="Candidate Name"),
                    Column('associated_event', css_class='form-group col-md-4'),
                ),
                Row(
                    Column(
                        'has_associated_events',
                        css_class='form-group col-md-4 mb-0'
                    ),
                    css_class='mt-0'
                ),
                # 2. The Toggle Button (HTML)
                HTML("""
                <div class="row">
                <div class="col-md-12 mb-2">
                <a class="btn btn-link p-0" data-toggle="collapse"
                href="#advancedFilters"
                role="button" aria-expanded="false" aria-controls="advancedFilters">Advanced Filters &rsaquo;</a>
                </div>
                </div>
                """),
                # 3. The Collapsible Container (Hidden by default)
                Div(
                    # Row 3: Cone Searches
                    Row(
                        Column('cone_search', css_class='form-group col-md-6'),
                        Column('target_cone_search', css_class='form-group col-md-6'),
                    ),
                    # Row 4: Dynamically added extra fields
                    Row(
                        *extra_columns,
                    ) if extra_columns else HTML(""),

                    # Bootstrap classes for functionality
                    css_class='collapse',
                    css_id='advancedFilters'  # must match the href in the "Advanced" HTML button above
                )
            )
        return self._form
