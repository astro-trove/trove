from django.db import models

GRID_DTYPE = "<f4"

# This sets the meta data for the actual light curves
# For compression, instead of storing the time bins for every single grid,
# the magnitudes are stored in time order and this just exists to reference 
# what index position corresponds to what time for each grid
class KnGridAxis(models.Model):
    grid = models.TextField(primary_key=True)
    time_axis = models.BinaryField()
    distance_mpc = models.FloatField()
    n_samples = models.IntegerField()
    n_time = models.IntegerField()

    class Meta:
        managed = False
        db_table = "kn_grid_axis"
        ordering = ["distance_mpc"]

    # For compression, this was stored as bytea, so this just reverts back
    @property
    def epochs(self):
        import numpy as np
        return np.frombuffer(bytes(self.time_axis), dtype=GRID_DTYPE)

class KnGridLightcurve(models.Model):
    # Each absmag is keyed by all 3 to uniquely define exactly what model, distance, and band
    # the absmag is describing
    pk = models.CompositePrimaryKey("grid", "band", "sample_id")

    grid = models.TextField()
    band = models.TextField()
    sample_id = models.IntegerField()
    absmag = models.BinaryField()

    class Meta:
        managed = False
        db_table = "kn_grid_lightcurve"

    # For compression purposes, when the simulation was generated, 
    # the magnitudes were stored as bytea, so this just converts back
    @property
    def magnitudes(self):
        import numpy as np
        return np.frombuffer(bytes(self.absmag), dtype=GRID_DTYPE)
