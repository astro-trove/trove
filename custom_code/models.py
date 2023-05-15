from datetime import datetime
from dateutil.parser import parse

from django.db import models
from tom_targets.models import Target, TargetList
from tom_nonlocalizedevents.models import EventLocalization


def _target_list_save(self, *args, **kwargs):
    """
    Saves Target model data to the database, including extra fields. After saving to the database, also runs the
    hook ``target_post_save``. The hook run is the one specified in ``settings.py``.

    :Keyword Arguments:
        * extras (`dict`): dictionary of key/value pairs representing target attributes
    """
    extras = kwargs.pop('extras', {})
    print(extras)

    created = False if self.id else True
    models.Model.save(self, *args, **kwargs)

    for k, v in extras.items():
        target_list_extra, _ = TargetListExtra.objects.get_or_create(target=self, key=k)
        target_list_extra.value = v
        target_list_extra.save()


TargetList.save = _target_list_save


class TargetListExtra(models.Model):
    """
    Class representing a list of targets in a TOM.

    :param target_list: The ``TargetList`` object this ``TargetListExtra`` is associated with.

    :param key: Denotation of the value represented by this ``TargetListExtra`` object.
    :type key: str

    :param value: Value of the field stored in this object.
    :type value: str

    :param float_value: Float representation of the ``value`` field for this object, if applicable.
    :type float_value: float

    :param bool_value: Boolean representation of the ``value`` field for this object, if applicable.
    :type bool_value: bool

    :param time_value: Datetime representation of the ``value`` field for this object, if applicable.
    :type time_value: datetime
    """
    target_list = models.ForeignKey(TargetList, on_delete=models.CASCADE)
    key = models.CharField(max_length=200)
    value = models.TextField(blank=True, default='')
    float_value = models.FloatField(null=True, blank=True)
    bool_value = models.BooleanField(null=True, blank=True)
    time_value = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ['target_list', 'key']

    def __str__(self):
        return f'{self.key}: {self.value}'

    def save(self, *args, **kwargs):
        """
        Saves TargetListExtra model data to the database. In the process, converts the string value of the
        ``TargetListExtra`` to the appropriate type, and stores it in the corresponding field as well.
        """
        try:
            self.float_value = float(self.value)
        except (TypeError, ValueError, OverflowError):
            self.float_value = None
        try:
            self.bool_value = bool(self.value)
        except (TypeError, ValueError, OverflowError):
            self.bool_value = None
        try:
            if isinstance(self.value, datetime):
                self.time_value = self.value
            else:
                self.time_value = parse(self.value)
        except (TypeError, ValueError, OverflowError):
            self.time_value = None

        super().save(*args, **kwargs)

    def typed_value(self, type_val):
        """
        Returns the value of this ``TargetListExtra`` in the corresponding type provided by the caller. If the type is
        invalid, returns the string representation.

        :param type_val: Requested type of the ``TargetListExtra`` ``value`` field
        :type type_val: str

        :returns: Requested typed value field of this object
        :rtype: float, boolean, datetime, or str
        """
        if type_val == 'number':
            return self.float_value
        if type_val == 'boolean':
            return self.bool_value
        if type_val == 'datetime':
            return self.time_value

        return self.value


class CSSField(models.Model):
    name = models.CharField(max_length=6, primary_key=True)
    ra = models.FloatField()
    dec = models.FloatField()
    ecliptic_lng = models.FloatField()
    ecliptic_lat = models.FloatField()
    galactic_lng = models.FloatField()
    galactic_lat = models.FloatField()
    healpix = models.BigIntegerField()
    adjacent = models.ManyToManyField('self')

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class Candidate(models.Model):
    candidatenumber = models.IntegerField(null=True)
    filename = models.CharField(max_length=128, null=True)
    elongation = models.FloatField(null=True)
    ra = models.FloatField(null=True)
    dec = models.FloatField(null=True)
    fwhm = models.FloatField(null=True)
    snr = models.FloatField(null=True)
    mag = models.FloatField(null=True)
    magerr = models.FloatField(null=True)
    rawfilename = models.CharField(max_length=128, null=True)
    obsdate = models.DateTimeField(null=False, default=datetime.now)
    field = models.ForeignKey(CSSField, null=True, on_delete=models.SET_NULL, db_column='field')
    classification = models.IntegerField(null=True)
    cx = models.FloatField(null=True)
    cy = models.FloatField(null=True)
    cz = models.FloatField(null=True)
    htm16id = models.BigIntegerField(null=True)
    target = models.ForeignKey(Target, null=True, on_delete=models.SET_NULL, db_column='targetid')
    mjdmid = models.FloatField(null=True)
    mlscore = models.FloatField(null=True)
    mlscore_real = models.FloatField(null=True)
    mlscore_bogus = models.FloatField(null=True)
    ncombine = models.IntegerField(null=True)
    gladeid = models.IntegerField(null=True)
    exclude = models.IntegerField(null=True)

    class Meta:
        db_table = 'candidates'
        ordering = ['-obsdate', '-candidatenumber']


class CSSFieldCredibleRegion(models.Model):
    localization = models.ForeignKey(EventLocalization, related_name='css_field_credible_regions', on_delete=models.CASCADE)
    css_field = models.ForeignKey(CSSField, related_name='css_field_credible_regions', on_delete=models.CASCADE)

    smallest_percent = models.IntegerField(
        default=100,
        help_text='Smallest percent credible region this field falls into for this localization.'
    )
    probability_contained = models.FloatField(null=True)
    group = models.IntegerField(null=True)
    rank_in_group = models.IntegerField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['localization', 'css_field'], name='unique_localization_css_field')
        ]
        ordering = ['-probability_contained']
