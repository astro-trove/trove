from django.forms import (
    Form,
    ChoiceField,
    FloatField,
    CharField,
    RadioSelect,
    Select
)
from .config import VETTING_FORM_CHOICES

from trove_targets.models import Target

from custom_code.templatetags.target_list_extras import galaxy_table

class VettingChoiceForm(Form):
    vetting_method = ChoiceField(
        choices = VETTING_FORM_CHOICES,
        widget = RadioSelect()
    )
    
class RedshiftUpdateForm(Form):
    
    host_galaxy_id = ChoiceField(
        choices = [],
        widget = Select(),
        label="Host Galaxy Name"
    )
    host_galaxy_source = ChoiceField(
        choices = [],
        widget = Select(),
        label="Host Galaxy Source"
    )

    z = FloatField(label="Redshift")
    z_err = FloatField(label="Redshift uncertainty")
    
    submitter = CharField(label="Submitter")
