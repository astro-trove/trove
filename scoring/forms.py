from django.forms import (
    Form,
    ChoiceField,
    FloatField,
    CharField,
    # RadioSelect,
    Select
)

class VettingChoiceForm(Form):
    vetting_method = ChoiceField(
        choices = [], # these are specified in the view
        widget = Select(),
        label = "Vetting Method"
    )
    
class RedshiftUpdateForm(Form):
    
    host_galaxy_id = ChoiceField(
        choices = [], # these are specified in the view
        widget = Select(),
        label="Host Galaxy Name"
    )
    host_galaxy_source = ChoiceField(
        choices = [], # these are specified in the view
        widget = Select(),
        label="Host Galaxy Source"
    )

    z = FloatField(label="Redshift")
    z_err = FloatField(label="Redshift uncertainty [default 0.001]", required=False)
    
    submitter = CharField(label="Submitter")
