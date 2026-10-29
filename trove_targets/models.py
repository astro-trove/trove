from django.db import models
from tom_targets.models import BaseTarget
from astropy.coordinates import SkyCoord
from healpix_alchemy.constants import HPX
from django.conf import settings

import logging
logger = logging.getLogger(__name__)


class Target(BaseTarget):
    classification = models.CharField(max_length=255, null=True, blank=True)
    redshift = models.FloatField(null=True, blank=True)
    mwebv = models.FloatField(verbose_name='Milky Way E(B-V)', null=True, blank=True)
    healpix = models.BigIntegerField(null=True, blank=True)
    healpix.hidden = True

    def save(self, *args, **kwargs):
        ra = self.ra if self.ra is not None else self.basetarget_ptr.ra
        dec = self.dec if self.dec is not None else self.basetarget_ptr.dec
        coord = SkyCoord(ra, dec, unit='deg')
        
        self.galactic_lng = coord.galactic.l.deg
        self.galactic_lat = coord.galactic.b.deg
        self.healpix = HPX.skycoord_to_healpix(coord)
        if self.mwebv is None or self.mwebv == 0:
            self.mwebv = settings.DUST_MAP(coord)
            logger.info(f"MW E(B-V) set to {self.mwebv}")
        super().save(*args, **kwargs)
