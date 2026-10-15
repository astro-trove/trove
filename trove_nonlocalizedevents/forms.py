from django import forms
from django.urls import reverse

from tom_nonlocalizedevents.models import EventCandidate
from trove_targets.models import Target

from dal import autocomplete


class EventCandidateSearchForm(forms.Form):
    # bound to request.GET so the boxes keep what you filtered on. Without this,
    # django-bootstrap4 marks every filled box is-valid and draws a green tick.
    bound_css_class = ""

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

    first_det_after = forms.DateField(
        label="Detected after:", required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    first_det_before = forms.DateField(
        label="Detected before:", required=False,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    distance_max = forms.FloatField(
        label="Max. distance (Mpc):", required=False, min_value=0.0,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "e.g. 200"}),
    )
    distance_type = forms.ChoiceField(
        label="Distance method:", required=False,
        choices=[("", "Any"), ("spec-z", "spec-z"),
                 ("photo-z", "photo-z"),
                 ("z-ind.", "z-independent")],
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    cone_ra = forms.FloatField(
        label="Cone RA:", required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "any"}),
    )
    cone_dec = forms.FloatField(
        label="Cone Dec:", required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "any"}),
    )

    cone_radius = forms.FloatField(
        label="Cone radius (\u2033):", required=False,
        min_value=0.0, initial=None,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "default 2"}),
    )

    def clean(self):
        """A cone needs a centre: a radius on its own would silently do nothing."""
        cleaned = super().clean()
        ra, dec = cleaned.get("cone_ra"), cleaned.get("cone_dec")
        if (ra is None) != (dec is None):
            raise forms.ValidationError("Cone search needs both an RA and a Dec.")
        if cleaned.get("cone_radius") is not None and ra is None:
            raise forms.ValidationError(
                "Cone radius needs an RA and a Dec to search around."
            )
        return cleaned

    def __init__(self, *args, nle_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.nle_id = nle_id

        # Add hidden field for nonlocalizedevent if provided. The create form on
        # the same page has a field of this name too, so give this one its own id
        # rather than letting both auto-generate "id_nonlocalizedevent".
        if nle_id:
            self.fields["nonlocalizedevent"] = forms.CharField(
                widget=forms.HiddenInput(attrs={"id": "id_filter_nonlocalizedevent"}),
                initial=nle_id, required=False
            )


class CreateEventCandidateFromNLEForm(forms.Form):
    target_name_to_link = forms.ModelChoiceField(
        queryset=Target.objects.all(),  # start with none
        label="Search for a target to link to this non-localized event:",
        required=True,
        widget=autocomplete.ModelSelect2(
            url="trove_targets:target-autocomplete",
            forward=["nonlocalizedevent"],
            attrs={
                "data-placeholder": "Start typing to search...",
                "data-minimum-input-length": 1,
            },
        ),
    )

    def __init__(self, *args, nle_id=None, **kwargs):
        super().__init__(*args, **kwargs)

        # Carries the event to the autocomplete so targets already linked to it are not offered
        if nle_id:
            self.fields["nonlocalizedevent"] = forms.CharField(
                widget=forms.HiddenInput(), initial=nle_id, required=False
            )
