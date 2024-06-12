from django import template
from ..models import Candidate, TargetListExtra
from tom_targets.models import TargetExtra
from guardian.shortcuts import get_objects_for_user
import json
import numpy as np

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


@register.inclusion_tag('tom_targets/partials/candidates_table.html')
def candidates_table(target):
    """
    Displays a table of all the candidates (detections) associated with a given target, including thumbnails
    """
    candidates = Candidate.objects.filter(target=target).order_by('-observation_record__scheduled_start')
    return {'candidates': candidates}


@register.inclusion_tag('tom_targets/partials/recent_targets.html', takes_context=True)
def recent_confirmed_targets(context, limit=10):
    """
    Displays a list of the most recently created targets in the TOM up to the given limit, or 10 if not specified.
    """
    user = context['request'].user
    targets_for_user = get_objects_for_user(user, 'tom_targets.view_target')
    confirmed_targets_for_user = targets_for_user.exclude(name__startswith='J')
    return {'targets': confirmed_targets_for_user.order_by('-created')[:limit]}
