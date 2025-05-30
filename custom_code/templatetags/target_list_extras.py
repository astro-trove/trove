from django import template
from ..models import Candidate, TargetListExtra
from tom_targets.models import Target, TargetExtra
from tom_targets.permissions import targets_for_user
from tom_surveys.models import SurveyField, SurveyObservationRecord
from .skymap_extras import CSS_FOOTPRINT, centers_to_vertices
import numpy as np
from matplotlib.path import Path
import json

register = template.Library()


@register.filter
def target_list_extra_field(target_list, name):
    """
    Returns a ``TargetListExtra`` value of the given name, if one exists.
    """
    try:
        return TargetListExtra.objects.get(target_list=target_list, key=name).value
    except TargetListExtra.DoesNotExist:
        return None

@register.filter
def islist(value):
    return isinstance(value, list) or isinstance(value, np.ndarray)
    
@register.inclusion_tag('tom_targets/partials/galaxy_table.html')
def galaxy_table(target):
    """
    Displays the most likely host galaxy matches.
    """
    te = TargetExtra.objects.filter(target=target, key='Host Galaxies')
    if te.exists():
        galaxies = json.loads(te.first().value)
    else:
        galaxies = None
    return {'galaxies': galaxies}


FIELDS = SurveyField.objects.order_by('name')
CENTERS = np.array(FIELDS.values_list('ra', 'dec'))
VERTICES = centers_to_vertices(CENTERS, CSS_FOOTPRINT)


@register.filter
def get_survey_observations(target):
    """
    Get all survey observations that contain the coordinates of a given target
    """
    matching_fields = []
    for field, vertex in zip(FIELDS, VERTICES):
        path = Path(vertex)
        if path.contains_points([[target.ra, target.dec]])[0]:
            matching_fields.append(field.name)
    return SurveyObservationRecord.objects.filter(survey_field__name__in=matching_fields)


@register.inclusion_tag('tom_targets/partials/candidates_table.html')
def candidates_table(target):
    """
    Displays a table of all the candidates and nondetections associated with a given target, including thumbnails
    """
    survey_observations = get_survey_observations(target).order_by('-scheduled_start')
    candidates = []
    for observation_record in survey_observations:
        candidate = observation_record.candidate_set.filter(target=target)
        if candidate.exists():
            candidates.append(candidate.first())
        else:
            candidates.append(Candidate(observation_record=observation_record))  # placeholder for nondetection
    return {'candidates': candidates}


@register.inclusion_tag('tom_targets/partials/recent_targets.html', takes_context=True)
def recent_confirmed_targets(context, limit=10):
    """
    Displays a list of the most recently created targets in the TOM up to the given limit, or 10 if not specified.
    """
    user = context['request'].user
    all_targets_for_user = targets_for_user(user, Target.objects.all(), 'view_target')
    confirmed_targets_for_user = all_targets_for_user.exclude(name__startswith='J')
    return {'targets': confirmed_targets_for_user.order_by('-created')[:limit]}
