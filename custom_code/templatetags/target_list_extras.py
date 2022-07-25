from django import template
from ..models import TargetListExtra
from django.conf import settings
from tom_targets.models import TargetExtra
from django.utils.safestring import mark_safe
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
    extras = {k['name']: target.extra_fields.get(k['name'], '') for k in settings.EXTRA_FIELDS if not k.get('hidden')}
    return {
        'target': target,
        'extras': extras
    }

@register.filter
def get_host_galaxies(target):
    te = TargetExtra.objects.filter(target=target, key='Host Galaxies')
    if te.exists():
        return json.loads(te.first().value)
