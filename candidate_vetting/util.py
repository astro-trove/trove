"""
Some common functions used in multiple places throughout the app
"""

from collections import OrderedDict
import math
from astropy.units import Quantity
from django.db.models import FloatField
from django.db.models.functions import Cast
from tom_nonlocalizedevents.models import (
    EventCandidate,
    EventLocalization,
    NonLocalizedEvent,
)
from trove_targets.models import Target
from tom_targets.models import TargetExtra
from candidate_vetting.models import ScoreFactor

from custom_code.templatetags.nonlocalizedevent_extras import get_most_likely_class

from candidate_vetting.vet import localization_sequence_from_name
from candidate_vetting.vet_phot import PHOT_SCORE_MIN
from candidate_vetting.vet_bns import PARAM_RANGES as KN_PARAM_RANGES
from candidate_vetting.vet_kn_in_sn import PARAM_RANGES as KN_IN_SN_PARAM_RANGES
from candidate_vetting.vet_super_kn import PARAM_RANGES as SUPER_KN_PARAM_RANGES

import time

# map imported parameter ranges to transients
TRANSIENTS = ["KN", "KN-in-SN", "super-KN"]
DICT_TRANSIENTS_PARAM_RANGES = {
    "KN": KN_PARAM_RANGES,
    "KN-in-SN": KN_IN_SN_PARAM_RANGES,
    "super-KN": SUPER_KN_PARAM_RANGES,
}


# default subscore names
SUBSCORE_NAMES = [
    "skymap_score",
    "host_distance_score",
    "ps_score",
    "agn_score",
    "predetection_score",
    "phot_peak_lum",
    "phot_peak_time",
    "phot_decay_rate",
]

# some of the keys in ScoreFactor are really just calculated values
# where the score depends on the type of non-localized event, so we need to
# convert these to scores
VAL_NOT_SCORE_KEYS = {
    "phot_peak_lum": "lum_max",
    "phot_peak_time": "peak_time",
    "phot_decay_rate": "decay_rate",
}

# these should now be stored in a TargetExtra object so the score needs to be
# accessed differently
TARGETEXTRA_KEYS = [
    "ps_score",
    "mpc_match_name",
    "mpc_match_sep",
    "mpc_match_date",
]
MPC_KEYS = [
    "mpc_match_name",
    "mpc_match_sep",
    "mpc_match_date",
]


def _check_phot_val(val, param_ranges, param_range_key):
    val_max = max(param_ranges[param_range_key])
    val_min = min(param_ranges[param_range_key])
    if isinstance(val_min, Quantity):
        val_min = val_min.value
    if isinstance(val_max, Quantity):
        val_max = val_max.value

    if val < val_min or val > val_max:
        # multiply photometry score by PHOT_SCORE_MIN
        return PHOT_SCORE_MIN
    return 1


def get_event_candidate_scores(
    event_candidates,
    dict_transients_param_ranges=DICT_TRANSIENTS_PARAM_RANGES,
    subscore_names=SUBSCORE_NAMES,
):
    """Get the event candidate scores for everything in subscore_names

    event_candidates should be a django queryset of EventCandidate objects
    """

    val_not_score_keys = VAL_NOT_SCORE_KEYS
    exclude_keys = set(val_not_score_keys.keys()) | set(TARGETEXTRA_KEYS)

    # only evaluate this once since it is time consuming
    event_candidates_list = list(event_candidates)

    # which transient types to consider?
    ### TODO: Right now, just does KN unless SSM; change this for BBH events
    try:
        nle_eventseq = localization_sequence_from_name(
            event_candidates_list[0].nonlocalizedevent.event_id
        )
        most_likely_class = get_most_likely_class(nle_eventseq.details)
    except IndexError:
        return []

    if most_likely_class == "SSM":
        transients = TRANSIENTS
    else:
        transients = ["KN"]

    # Batch load all related data at once
    target_ids = [ec.target_id for ec in event_candidates_list]

    # Prefetch TargetExtra for all targets at once
    target_extras_by_id = {}
    for te in TargetExtra.objects.filter(target_id__in=target_ids):
        if te.target_id not in target_extras_by_id:
            target_extras_by_id[te.target_id] = {}
        target_extras_by_id[te.target_id][te.key] = te.value

    # Prefetch all ScoreFactor objects at once
    score_factors = ScoreFactor.objects.filter(
        event_candidate__in=event_candidates_list, key__in=subscore_names
    ).annotate(value_float=Cast("value", FloatField()))

    # Group score factors by event candidate
    score_factors_by_ec = {}
    for sf in score_factors:
        ec_id = sf.event_candidate_id
        if ec_id not in score_factors_by_ec:
            score_factors_by_ec[ec_id] = {}
        score_factors_by_ec[ec_id][sf.key] = sf.value_float

    ecs_out = []
    for ec in event_candidates_list:
        # set ec.score to be a dictionary mapping transient : score
        ec.score = {}

        # get all 'subscores' (sometimes actually calculated values)
        # for object; need to re-do this per transient because of step
        # below where we exclude certain scores from the queryset
        sf_dict = score_factors_by_ec.get(ec.id, {})

        # Extract values that need special handling
        val_dict = {
            subscore_key: sf_dict[subscore_key]
            for subscore_key, param_range_key in val_not_score_keys.items()
            if subscore_key in sf_dict
        }

        # now get all the scores stored in TargetExtra objects
        te = target_extras_by_id.get(ec.target_id, {})
        ps_score = 1
        if "ps_score" in te:
            ps_score = float(te["ps_score"])

        mpc_score = 1
        if "mpc_match_name" in te:
            mpc_score = int(te["mpc_match_name"] == str(None))

        # remove keys we don't want and calculate a base subscore
        subscore_no_phot = (
            math.prod([sf_dict[key] for key in sf_dict if key not in exclude_keys])
            * ps_score
            * mpc_score
        )

        for transient in transients:
            # allowed parameter ranges for given transient
            param_ranges = dict_transients_param_ranges[transient]

            # compute the photometry score
            phot_score = math.prod(
                [
                    _check_phot_val(
                        val_dict[subscore_key], param_ranges, param_range_key
                    )
                    for subscore_key, param_range_key in val_not_score_keys.items()
                    if subscore_key in val_dict
                ]
            )

            # save the score to a temporary field (dictionary) in the
            # EventCandidate object
            ec.score[transient] = (
                subscore_no_phot * phot_score
            )  # multiply the subscores
        ecs_out.append(ec)

    print("Finished computing the scores, sorting and returning...", time.time())

    # sort by kilonova score, for now
    ## TODO: generalize this
    return sorted(ecs_out, reverse=True, key=lambda x: x.score["KN"])


def get_target_score(target_id):

    if target_id is None:
        return "Target ID is None!"

    target = Target.objects.get(id=target_id)

    out = {}
    for event_candidate in target.eventcandidate_set.all():
        nonlocalized_name = NonLocalizedEvent.objects.get(
            id=event_candidate.nonlocalizedevent_id
        ).event_id

        out[nonlocalized_name] = event_candidate.priority

    return out
