"""
Some common functions used in multiple places throughout the app
"""

import math
from astropy.units import Quantity
from django.db.models import FloatField
from django.db.models.functions import Cast
from tom_nonlocalizedevents.models import NonLocalizedEvent
from trove_targets.models import Target
from tom_targets.models import TargetExtra
from candidate_vetting.models import ScoreFactor

from candidate_vetting.vet_phot import PHOT_SCORE_MIN
from candidate_vetting.vet_bns import PARAM_RANGES as KN_PARAM_RANGES
from candidate_vetting.vet_kn_in_sn import PARAM_RANGES as KN_IN_SN_PARAM_RANGES
from candidate_vetting.vet_super_kn import PARAM_RANGES as SUPER_KN_PARAM_RANGES

# map imported parameter ranges to transients
TRANSIENTS = ["KN",
              "KN-in-SN",
              "super-KN"]
DICT_TRANSIENTS_PARAM_RANGES = {
    "KN":KN_PARAM_RANGES,
    "KN-in-SN":KN_IN_SN_PARAM_RANGES,
    "super-KN":SUPER_KN_PARAM_RANGES}


# default subscore names 
SUBSCORE_NAMES = ['skymap_score',
                  'host_distance_score',
                  'ps_score',
                  'agn_score',
                  'predetection_score',
                  'phot_peak_lum',
                  'phot_peak_time',
                  'phot_decay_rate']

# some of the keys in ScoreFactor are really just calculated values
# where the score depends on the type of non-localized event, so we need to 
# convert these to scores
VAL_NOT_SCORE_KEYS = {
    "phot_peak_lum":"lum_max",
    "phot_peak_time":"peak_time",
    "phot_decay_rate":"decay_rate"
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

def get_event_candidate_scores(event_candidates, 
                               dict_transients_param_ranges=DICT_TRANSIENTS_PARAM_RANGES,
                               subscore_names=SUBSCORE_NAMES):
    """Get the event candidate scores for everything in subscore_names

    event_candidates should be a django queryset of EventCandidate objects
    """ 

    val_not_score_keys = VAL_NOT_SCORE_KEYS

    ecs_out = []
    for ec in event_candidates:
        # set ec.score to be a dictionary mapping transient : score
        ec.score = {}
        
        for transient in TRANSIENTS: 
            # allowed parameter ranges for given transient
            param_ranges = dict_transients_param_ranges[transient]
            
            phot_score = 1 # reset to 1.0 for each transient
            
            # get all 'subscores' (sometimes actually calculated values)
            # for object; need to re-do this per transient because of step 
            # below where we exclude certain scores from the queryset
            subscores = ScoreFactor.objects.filter(
                event_candidate = ec,
                key__in = subscore_names
            ).annotate(
                value_float = Cast("value", FloatField())
            )
            
            # iterate though subscores/values which will determine subscores
            subscore_keys = subscores.values_list("key", flat=True)
            for subscore_key, param_range_key in val_not_score_keys.items():
                if subscore_key in subscore_names and subscore_key in subscore_keys:
                    val = subscores.get(
                        key = subscore_key
                    ).value_float
                    # check if within limits
                    val_max = max(param_ranges[param_range_key])
                    val_min = min(param_ranges[param_range_key])
                    if isinstance(val_min, Quantity):
                        val_min = val_min.value
                    if isinstance(val_max, Quantity):
                        val_max = val_max.value
                    
                    if val < val_min or val > val_max:
                        # multiply photometry score by PHOT_SCORE_MIN
                        phot_score *= PHOT_SCORE_MIN 
                
            subscores = subscores.exclude(
                key__in = list(val_not_score_keys.keys()) + TARGETEXTRA_KEYS
            ) # this removes those rows from the queryset
            
            # now we can compute the score just using multiplication
            subscore_list = list(
                subscores.values_list("value_float", flat=True)
            )
            subscore_list.append(phot_score)
            
            # now get all the scores stored in TargetExtra objects and append those
            te = TargetExtra.objects.filter(target_id = ec.target.id)
            ps_score_qs = te.filter(key="ps_score")
            if ps_score_qs.exists():
                ps_score = float(ps_score_qs.first().value)
                subscore_list.append(ps_score)
                
            mpc_match_name = te.filter(key="mpc_match_name")
            if mpc_match_name.exists():
                mpc_score = int(mpc_match_name.first().value == str(None))
                subscore_list.append(mpc_score)
                
            # save the score to a temporary field (dictionary) in the 
            # EventCandidate object
            ec.score[transient] = math.prod(subscore_list) # multiply the subscores
        ecs_out.append(ec)
    
    # sort by kilonova score, for now
    return sorted(ecs_out, reverse=True, key = lambda x : x.score["KN"])

def get_target_score(target_id):

    if target_id is None:
        return "Target ID is None!"

    target = Target.objects.get(id=target_id)

    out = {}
    for event_candidate in target.eventcandidate_set.all():
        nonlocalized_name = NonLocalizedEvent.objects.get(
            id = event_candidate.nonlocalizedevent_id
        ).event_id
        
        out[nonlocalized_name] = event_candidate.priority
    
    return out
