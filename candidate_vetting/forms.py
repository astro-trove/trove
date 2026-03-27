from django.forms import (
    Form,
    ChoiceField,
    FloatField,
    RadioSelect,
    Select
)
from .config import VETTING_FORM_CHOICES

class VettingChoiceForm(Form):
    vetting_method = ChoiceField(
        choices = VETTING_FORM_CHOICES,
        widget = RadioSelect()
    )
    
class RedshiftUpdateForm(Form):
    host_galaxy = ChoiceField(
        choices = VETTING_FORM_CHOICES,
        widget = Select(),
        label="Host Galaxy"
    )
    z = FloatField(label="Redshift")
    z_err = FloatField(label="Redshift uncertainty")
