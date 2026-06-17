from django.db import models
from tom_nonlocalizedevents.models import EventCandidate


class ScoreFactor(models.Model):
    id = models.AutoField(primary_key=True)
    event_candidate = models.ForeignKey(EventCandidate, on_delete=models.CASCADE)
    key = models.CharField(max_length=200)
    value = models.CharField(max_length=200)

    class Meta:
        unique_together = ("event_candidate", "key")
