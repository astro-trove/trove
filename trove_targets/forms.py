from django import forms

from tom_nonlocalizedevents.models import NonLocalizedEvent
from tom_targets.forms import TargetForm, SiderealTargetCreateForm

# from tom_targets.models import Target
from trove_targets.models import Target
from tom_targets.base_models import REQUIRED_SIDEREAL_FIELDS

from dal import autocomplete


class TargetNLEForm(forms.Form):
    nle_select = forms.ModelChoiceField(
        queryset=NonLocalizedEvent.objects.all(),
        label="Choose a poorly localized event",
        required=True,
        widget=autocomplete.ModelSelect2(
            url="trove_targets:nonlocalizedevent-autocomplete",
            attrs={
                "data-placeholder": "Start typing to search...",
                "data-minimum-input-length": 1,
            },
        ),
    )


class CustomSiderealTargetCreateForm(SiderealTargetCreateForm):
    def __init__(self, *args, **kwargs):
        super(TargetForm, self).__init__(*args, **kwargs)
        for field in REQUIRED_SIDEREAL_FIELDS:
            self.fields[field].required = True

    class Meta(SiderealTargetCreateForm.Meta):
        fields = [
            "name",
            "ra",
            "dec",
            "epoch",
            "distance",
            "distance_err",
            "type",
            "parallax",
            "classification",
            "redshift",
        ]
