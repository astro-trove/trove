from django import template
from tom_targets.models import Target, TargetExtra
from tom_targets.permissions import targets_for_user
import numpy as np
import json

register = template.Library()


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


@register.inclusion_tag('tom_targets/partials/recent_targets.html', takes_context=True)
def recent_confirmed_targets(context, limit=10):
    """
    Displays a list of the most recently created targets in the TOM up to the given limit, or 10 if not specified.
    """
    user = context['request'].user
    all_targets_for_user = targets_for_user(user, Target.objects.all(), 'view_target')
    confirmed_targets_for_user = all_targets_for_user.exclude(name__startswith='J')
    return {'targets': confirmed_targets_for_user.order_by('-created')[:limit]}
