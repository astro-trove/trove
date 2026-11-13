from django.db import models
from tom_nonlocalizedevents.models import EventLocalization


class CredibleRegionContour(models.Model):
    localization = models.ForeignKey(EventLocalization, related_name='credible_region_contours', on_delete=models.CASCADE)
    probability = models.FloatField()
    pixels = models.JSONField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['localization', 'probability'], name='unique_localization_probability')
        ]
