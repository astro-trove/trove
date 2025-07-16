from django.db import models
from tom_targets.models import BaseTarget
from astropy.coordinates import SkyCoord
from healpix_alchemy.constants import HPX


class Target(BaseTarget):
    healpix = models.BigIntegerField()
    healpix.hidden = True

    def save(self, *args, **kwargs):
        coord = SkyCoord(self.ra, self.dec, unit='deg')
        self.galactic_lng = coord.galactic.l.deg
        self.galactic_lat = coord.galactic.b.deg
        self.healpix = HPX.skycoord_to_healpix(coord)
        super().save(*args, **kwargs)
