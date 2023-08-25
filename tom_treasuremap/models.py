from django.db import models
from tom_nonlocalizedevents.models import NonLocalizedEvent
from tom_surveys.models import SurveyObservationRecord


class TreasureMapPointing(models.Model):
    treasuremap_id = models.IntegerField(null=True, unique=True)
    nonlocalizedevent = models.ForeignKey(NonLocalizedEvent, related_name='treasuremap_pointings',
                                          on_delete=models.CASCADE)
    observation_record = models.ForeignKey(SurveyObservationRecord, related_name='treasuremap_pointings',
                                           on_delete=models.CASCADE)
    status = models.CharField(max_length=200)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['nonlocalizedevent', 'observation_record'],
                name='unique_nonlocalizedevent_observation_record',
            )
        ]

    def __str__(self):
        return str(self.treasuremap_id)
