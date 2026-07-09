from django.db import models
from tom_nonlocalizedevents.models import EventCandidate


## score factors
class ScoreFactor(models.Model):
    id = models.AutoField(primary_key=True)
    event_candidate = models.ForeignKey(EventCandidate, on_delete=models.CASCADE)
    key = models.CharField(max_length=200)
    value = models.CharField(max_length=200)

    class Meta:
        unique_together = ("event_candidate", "key")

## catalog of user-provided host galaxies
class UserGalaxyQ3C(models.Model):
    id = models.AutoField(primary_key=True)
    objname = models.TextField(blank=True, null=True)
    ra = models.FloatField(blank=True, null=True)
    dec = models.FloatField(blank=True, null=True)
    z = models.FloatField(blank=True, null=True)
    z_err = models.FloatField(blank=True, null=True)
    z_pos_err = models.FloatField(blank=True, null=True)
    z_neg_err = models.FloatField(blank=True, null=True)
    z_type = models.TextField(blank=True, null=True)
    lumdist = models.FloatField(blank=True, null=True)
    lumdist_err = models.FloatField(blank=True, null=True)
    lumdist_pos_err = models.FloatField(blank=True, null=True)
    lumdist_neg_err = models.FloatField(blank=True, null=True)
    default_mag = models.FloatField(blank=True, null=True)
    source = models.TextField(blank=True, null=True)
    submitter = models.TextField(blank=True, null=True)  # submitter and original source
    og_id = models.BigIntegerField(blank=True, null=True)

    # managed = True (default, specified here for clarity)
    # --> allow Django to modify this table
    class Meta:
        managed = True
        db_table = "usergalaxy_q3c"