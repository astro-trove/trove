from django import forms

from tom_nonlocalizedevents.models import NonLocalizedEvent
from dal import autocomplete

class TargetNLEForm(forms.Form):
    nle_select = forms.ModelChoiceField(
        queryset=NonLocalizedEvent.objects.all(),
        label="Choose a poorly localized event",
        required=True,
        widget=autocomplete.ModelSelect2(
            url='trove_targets:nonlocalizedevent-autocomplete',
            attrs={
                'data-placeholder': 'Start typing to search...',
                'data-minimum-input-length': 1,
            }
        )
    )
