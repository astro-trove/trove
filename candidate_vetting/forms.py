from django.forms import (
    Form,
    ChoiceField,
    RadioSelect
)
from .config import VETTING_FORM_CHOICES

class VettingChoiceForm(Form):
    vetting_method = ChoiceField(
        choices = VETTING_FORM_CHOICES,
        widget = RadioSelect()
    )
