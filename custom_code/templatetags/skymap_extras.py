from django import template
from tom_nonlocalizedevents.models import NonLocalizedEvent

register = template.Library()


@register.inclusion_tag('tom_nonlocalizedevents/partials/skymap.html')
def skymap(event_id):
    nle = NonLocalizedEvent.objects.get(event_id=event_id)
    seq = nle.sequences.last()
    if seq and seq.localization:
        contour = seq.localization.credible_region_contours.filter(probability=0.9)
        if contour.exists():
            return {'credible_region': contour.last().pixels}
        else:
            return {'credible_region': []}
