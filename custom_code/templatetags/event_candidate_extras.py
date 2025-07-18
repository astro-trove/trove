"""
Some functions for accessing the EventCandidate table inside a django template
"""
from urllib.parse import urlparse
from django import template
from tom_nonlocalizedevents.models import EventCandidate, NonLocalizedEvent
from trove_targets.models import Target

register = template.Library()

#@register.inclusion_tag('tom_targets/partials/target_data.html', takes_context=True)
@register.simple_tag
def get_candidate_event_score(target_id):

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
