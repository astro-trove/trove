from django.db import models
from tom_targets.models import BaseTarget
from astropy.coordinates import SkyCoord
from healpix_alchemy.constants import HPX
from django.conf import settings


class Target(BaseTarget):
    classification = models.CharField(null=True, blank=True)
    redshift = models.FloatField(null=True, blank=True)
    mwebv = models.FloatField(verbose_name='Milky Way E(B-V)', null=True, blank=True)
    healpix = models.BigIntegerField()
    healpix.hidden = True

    def save(self, *args, **kwargs):
        ra = self.ra if self.ra is not None else self.basetarget_ptr.ra
        dec = self.dec if self.dec is not None else self.basetarget_ptr.dec
        try:
            coord = SkyCoord(ra, dec, unit='deg')
        except:
            import pdb; pdb.set_trace()
        
        self.galactic_lng = coord.galactic.l.deg
        self.galactic_lat = coord.galactic.b.deg
        self.healpix = HPX.skycoord_to_healpix(coord)
        self.mwebv = settings.DUST_MAP(coord)
        super().save(*args, **kwargs)
