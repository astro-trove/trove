from django import forms
from django.urls import reverse

from tom_nonlocalizedevents.models import EventCandidate
from trove_targets.models import Target

from dal import autocomplete


class EventCandidateSearchForm(forms.Form):
    target_name = forms.ModelChoiceField(
        queryset=EventCandidate.objects.none(),  # start with none
        label="Search for a candidate:   ",
        required=False,
    )

    def __init__(self, *args, nle_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        url = reverse("trove_nonlocalizedevents:eventcandidate-autocomplete")
        if nle_id:
            url += f"?nonlocalizedevent={nle_id}"
        self.fields["target_name"].widget = autocomplete.ModelSelect2(
            url=url,
            attrs={
                "data-placeholder": "Start typing to search...",
                "data-minimum-input-length": 1,
            },
        )
        if nle_id:
            self.fields["target_name"].queryset = EventCandidate.objects.filter(
                nonlocalizedevent_id=nle_id
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
