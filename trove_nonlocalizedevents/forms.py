from django import forms
from django.urls import reverse

from tom_nonlocalizedevents.models import EventCandidate
from trove_targets.models import Target

from dal import autocomplete


class EventCandidateSearchForm(forms.Form):
    target__name = forms.CharField(
        label="Filter table by target name:",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Enter target name...",
            }
        ),
    )

    def __init__(self, *args, nle_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.nle_id = nle_id

        # Add hidden field for nonlocalizedevent if provided
        if nle_id:
            self.fields["nonlocalizedevent"] = forms.CharField(
                widget=forms.HiddenInput(), initial=nle_id, required=False
            )


class CreateEventCandidateFromNLEForm(forms.Form):
    target_name_to_link = forms.ModelChoiceField(
        queryset=Target.objects.all(),  # start with none
        label="Search for a target to link to this non-localized event:",
        required=True,
        widget=autocomplete.ModelSelect2(
            url="trove_targets:target-autocomplete",
            attrs={
                "data-placeholder": "Start typing to search...",
                "data-minimum-input-length": 1,
            },
        ),
    )
