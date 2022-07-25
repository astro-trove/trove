from django import template
from ..models import TargetListExtra
from tom_targets.models import TargetExtra
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
