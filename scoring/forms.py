from django.core.exceptions import ValidationError
from django.forms import (
    Form,
    ChoiceField,
    FloatField,
    CharField,
    # RadioSelect,
    Select
)

from .dynamic_catalogs import find_galaxy
from .phot_method import KILONOVA_VETTING_MODE, PHOT_METHOD_TROVE

class VettingChoiceForm(Form):
    vetting_method = ChoiceField(
        choices = [], # these are specified in the view
        widget = Select(),
        label = "Vetting Method",
    )
    # which photometry scoring method this run should use
    phot_method = ChoiceField(
        choices = [], # these are specified in the view
        widget = Select(),
        label = "Photometry Scoring Method",
    )

    def clean(self):
        """Only KN vetting can use KilonovaSCORER.

        The template disables the option for the other modes, but a disabled
        <option> is a hint to the browser, not a constraint -- a hand-made POST
        can still carry it. Forcing it here means the rule holds wherever the
        request came from.
        """
        cleaned = super().clean()
        if cleaned.get("vetting_method") != KILONOVA_VETTING_MODE:
            cleaned["phot_method"] = PHOT_METHOD_TROVE
        return cleaned
    
class RedshiftUpdateForm(Form):
    host_galaxy_id = ChoiceField(
        choices = [], # these are specified in the view
        widget = Select(),
        label="Host Galaxy Name",
    )
    host_galaxy_source = ChoiceField(
        choices = [], # these are specified in the view
        widget = Select(),
        label="Host Galaxy Source",
    )

    z = FloatField(label="Redshift")
    z_err = FloatField(label="Redshift uncertainty [default 0.001]", 
                       required=False,
    )
    
    submitter = CharField(label="Submitter")

    def clean(self):
        """The ID and the source are picked from two independent dropdowns, so
        they can name a pair that isn't in the host galaxy table. IDs repeat
        across catalogs, so a mismatched pair would otherwise store some other
        galaxy's position and magnitude.
        """
        cleaned = super().clean()
        galaxies = getattr(self, "galaxies", None)
        host_galaxy_id = cleaned.get("host_galaxy_id")
        host_galaxy_source = cleaned.get("host_galaxy_source")
        if not (galaxies and host_galaxy_id and host_galaxy_source):
            return cleaned
        if find_galaxy(galaxies, host_galaxy_id, host_galaxy_source) is not None:
            return cleaned

        # name the source they should have picked, the dropdowns don't show it
        sources = list(
            dict.fromkeys(
                str(g.get("Source"))
                for g in galaxies
                if str(g.get("ID")) == str(host_galaxy_id)
            )
        )
        if sources:
            raise ValidationError(
                f"{host_galaxy_id} is listed under {' or '.join(sources)}, not "
                f"{host_galaxy_source}. Change the Host Galaxy Source to match."
            )
        raise ValidationError(
            f"{host_galaxy_id} is no longer in the host galaxy table for this "
            "target. Reload the page and pick again."
        )

class NonLocalizedEventAssociateTargetsForm(Form):
    first_det_tmin = FloatField(label=r"Minimum time [days]")
    first_det_tmax = FloatField(label="Maximum time [days]")
    snr_min = FloatField(label="SNR minimum [default 5.0]", required=False)
