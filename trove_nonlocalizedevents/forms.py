import logging

from django import forms
from django.urls import reverse

from tom_nonlocalizedevents.models import EventCandidate
from trove_targets.models import Target

from dal import autocomplete

from scoring.phot_method import (
    DEFAULT_KILONOVA_PARAMS,
    GRID_OFFSET_ACTIONS,
    KILONOVA,
    PHOT_METHOD_CHOICES,
    get_kilonova_params,
    get_phot_method,
)

logger = logging.getLogger(__name__)


class EventCandidateSearchForm(forms.Form):
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

    def __init__(self, *args, nle_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.nle_id = nle_id

        # Add hidden field for nonlocalizedevent if provided
        if nle_id:
            self.fields["nonlocalizedevent"] = forms.CharField(
                widget=forms.HiddenInput(), initial=nle_id, required=False
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


def _grid_choices():
    """Simulation grids on disk, as ``(path, label)`` choices.

    The grid ladder is a generated artifact living outside the repo (see
    ``TROVE_GRID_DIR``), so this is read at form-construction time rather than
    baked in. The empty choice defers the decision to the scorer, which picks
    the rung nearest each candidate's own distance -- almost always what you
    want, since candidate distances span the whole ladder.
    """
    choices = [("", "Automatic - nearest grid to each candidate's distance")]
    try:
        from scoring.KilonovaScorer.grids import GRID_DIR, available_grids

        grids = available_grids()
    except Exception:  # noqa: BLE001 - a missing/unreadable grid dir must not 500 the page
        logger.exception("Could not list KilonovaSCORER simulation grids")
        return choices

    for row in grids.itertuples():
        dist = f"{row.distance_mpc:.0f} Mpc" if row.distance_mpc == row.distance_mpc else "unknown distance"
        choices.append(
            (str(row.path), f"{row.path.name} ({dist}, {row.size_mb / 1000:.1f} GB)")
        )
    if len(choices) == 1:
        logger.warning("No KilonovaSCORER simulation grids found in %s", GRID_DIR)
    return choices


class PhotScoringMethodForm(forms.Form):
    """Which photometry scoring method the candidate list ranks with.

    The ``kilonova_*`` fields configure the KilonovaSCORER run and are ignored
    when the TROVE method is selected -- they are hidden by the modal's own
    JavaScript in that case, so they are all ``required=False`` and are only
    validated for sanity, not presence.
    """

    phot_method = forms.ChoiceField(
        label="Photometry scoring method",
        choices=PHOT_METHOD_CHOICES,
        widget=forms.RadioSelect,
        required=True,
    )

    # --- what to score against ---------------------------------------------
    grid = forms.ChoiceField(
        label="Simulation grid",
        required=False,
        help_text=(
            "Population of simulated kilonovae the light curve is compared to. "
            "Grids are generated at a fixed luminosity distance, so one is "
            "picked per distance bin."
        ),
    )
    mode = forms.ChoiceField(
        label="Band matching",
        required=False,
        choices=[
            ("survey", "Survey - match each observation through its own bandpass"),
            ("canonical", "Canonical - collapse everything onto g/r/i/z"),
        ],
        help_text="Canonical is for LSST-like grids; survey keeps ATLAS c/o, GOTO L, etc. distinct.",
    )
    max_grid_offset = forms.FloatField(
        label="Max grid distance offset",
        required=False,
        min_value=0.0,
        help_text=(
            "As a fraction of the candidate's own distance. A grid is generated "
            "at one luminosity distance, and the k-correction and time dilation "
            "there change the shape of the magnitude distribution per band and "
            "epoch, which cannot be corrected afterwards. 0.5 allows e.g. 290 Mpc "
            "against the 400 Mpc rung (38% off)."
        ),
    )
    grid_offset_action = forms.ChoiceField(
        label="When no grid is close enough",
        required=False,
        choices=GRID_OFFSET_ACTIONS,
        help_text=(
            "Generating a rung takes ~30 minutes and ~4 GB of disk, and scoring "
            "waits until it finishes."
        ),
    )
    map_wide_bands = forms.BooleanField(
        label="Include unfiltered / very wide bands",
        required=False,
        help_text=(
            "Fold Clear, GOTO L, BlackGem q and ATLAS wide onto their nearest "
            "SDSS band instead of dropping them (~1.5% of TROVE photometry). "
            "Their zero point depends on each pipeline's calibration."
        ),
    )

    # --- which photometry to score -----------------------------------------
    dt_min = forms.FloatField(
        label="Earliest epoch (days after trigger)",
        required=False,
        help_text="0 drops pre-merger data, as KilonovaSCORER's own loader does.",
    )
    dt_max = forms.FloatField(
        label="Latest epoch (days after trigger)",
        required=False,
        help_text=(
            "The grids simulate 0-10 days, so epochs past 10 cannot be scored "
            "at all - raising this only fetches photometry the scorer discards. "
            "Blank means no upper limit."
        ),
    )
    snr_min = forms.FloatField(
        label="Minimum S/N",
        required=False,
        min_value=0.0,
        help_text="Leave blank to keep every detection. Upper limits are always dropped.",
    )
    min_obs = forms.IntegerField(
        label="Minimum epochs per candidate",
        required=False,
        min_value=1,
        help_text="Candidates with fewer are reported as too sparse rather than scored.",
    )
    min_bands = forms.IntegerField(
        label="Minimum bands per candidate",
        required=False,
        min_value=1,
    )

    # --- scorer internals (the modal keeps these collapsed) -----------------
    time_bin_width = forms.FloatField(
        label="Time bin width (days)",
        required=False,
        min_value=0.0,
        help_text="Width of the bins observations are matched to simulations in.",
    )
    k_near = forms.FloatField(
        label="ROPE half-width k_near",
        required=False,
        min_value=0.0,
        help_text="Paper fiducial: 1.5.",
    )
    n_kde_sim = forms.IntegerField(
        label="KDE Monte-Carlo samples",
        required=False,
        min_value=1,
        help_text="Higher is smoother and slower.",
    )
    min_sim_points = forms.IntegerField(
        label="Minimum simulations per bin",
        required=False,
        min_value=1,
        help_text="Bins with fewer simulations are not scored.",
    )
    overlap_k = forms.FloatField(
        label="ABC ROPE half-width (sigma)",
        required=False,
        min_value=0.0,
    )

    def __init__(self, *args, nle_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.nle_id = nle_id

        self.fields["grid"].choices = _grid_choices()

        # prefill from whatever THIS event is set to, so reopening the modal
        # shows the run that produced the scores on screen; a different event's
        # settings are a different cache key and never bleed in
        params = get_kilonova_params(nle_id)
        self.fields["phot_method"].initial = get_phot_method(nle_id)
        for name, value in params.items():
            field_name = "grid" if name == "grid_path" else name
            if field_name in self.fields:
                self.fields[field_name].initial = value

        # a grid that has since been deleted or moved would otherwise fail
        # validation with no way for the user to see why
        valid_grids = {value for value, _ in self.fields["grid"].choices}
        if self.fields["grid"].initial not in valid_grids:
            self.fields["grid"].initial = ""

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("phot_method") != KILONOVA:
            return cleaned

        dt_min = cleaned.get("dt_min")
        dt_max = cleaned.get("dt_max")
        if dt_min is not None and dt_max is not None and dt_max <= dt_min:
            self.add_error("dt_max", "Must be later than the earliest epoch.")
        return cleaned

    def kilonova_params(self) -> dict:
        """The cleaned KilonovaSCORER parameters, defaults filling any blanks.

        A blank numeric field means "use the default", not "use None" -- the
        scorer would take ``None`` for a threshold literally and crash.
        ``dt_max`` and ``snr_min`` are the exceptions: for them blank genuinely
        means "no limit", which is what ``None`` encodes downstream.
        """
        params = dict(DEFAULT_KILONOVA_PARAMS)
        params["grid_path"] = self.cleaned_data.get("grid") or ""
        params["map_wide_bands"] = bool(self.cleaned_data.get("map_wide_bands"))
        params["dt_max"] = self.cleaned_data.get("dt_max")
        params["snr_min"] = self.cleaned_data.get("snr_min")

        for name in (
            "mode",
            "max_grid_offset",
            "grid_offset_action",
            "dt_min",
            "min_obs",
            "min_bands",
            "time_bin_width",
            "k_near",
            "n_kde_sim",
            "min_sim_points",
            "overlap_k",
        ):
            value = self.cleaned_data.get(name)
            if value not in (None, ""):
                params[name] = value
        return params
