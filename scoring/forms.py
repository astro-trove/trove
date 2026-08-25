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
    # Which photometry scorer this run should use. Chosen per run, here, rather
    # than read from the site-wide toggle on the candidate list: that toggle
    # decides which stored score is DISPLAYED and switching it rescores nothing,
    # so letting it also steer vetting made one control mean two things. Only
    # the KN pipeline consults this; the other modes have one scorer.
    phot_method = ChoiceField(
        choices = [], # these are specified in the view
        widget = Select(),
        label = "Photometry Scoring (KN vetting only)",
        required = False,
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

class NonLocalizedEventAssociateTargetsForm(Form):
    first_det_tmin = FloatField(label=r"Minimum time [days]")
    first_det_tmax = FloatField(label="Maximum time [days]")
    snr_min = FloatField(label="SNR minimum [default 5.0]", required=False)
