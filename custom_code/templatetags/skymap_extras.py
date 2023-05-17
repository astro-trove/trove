from django import template
from tom_nonlocalizedevents.models import NonLocalizedEvent

register = template.Library()


@register.inclusion_tag('tom_nonlocalizedevents/partials/skymap.html')
def skymap(event_id):
    nle = NonLocalizedEvent.objects.get(event_id=event_id)
    seq = nle.sequences.last()
    if seq and seq.localization:
        return {'credible_region': seq.localization.credible_region_contours.get(probability=0.9).pixels}
