"""
Dynamic catalogs
"""

from astropy import units as u

from django.conf import settings

cosmo = settings.COSMO

from .catalog import StaticCatalog
from ..models import UserGalaxyQ3C


class UserGalaxy(StaticCatalog):
    catalog_model = UserGalaxyQ3C

    def __init__(self):
        self.catalog_type = "dynamic"

        self.colmap = {
            "id": "trove_uniq",
            "objname": "name",
            "ra": "ra",
            "dec": "dec",
            "default_mag": "default_mag",
            "z": "z",
            "z_err": "z_err",
            "z_pos_err": "z_pos_err",
            "z_neg_err": "z_neg_err",
            "submitter": "submitter",
        }

        self.mag_colname = "default_mag"
        # then, of course, init the super class
        super().__init__()

    def to_standardized_catalog(self, df):
        df = self._standardize_df(df)
        df["lumdist"] = cosmo.luminosity_distance(df.z).to(u.Mpc).value
        df["lumdist_err"] = cosmo.luminosity_distance(df.z_err).to(u.Mpc).value
        df["lumdist_neg_err"] = cosmo.luminosity_distance(df.z_neg_err).to(u.Mpc).value
        df["lumdist_pos_err"] = cosmo.luminosity_distance(df.z_pos_err).to(u.Mpc).value
        df["z_type"] = "user spec-z"
        return df

    def _add_galaxy(
        self, target, galaxies, z, z_err, host_galaxy_id, host_galaxy_source, submitter
    ):
        # convenience function for quickly finding the galaxy in a dict
        def index(lst, key, value):
            for i, dic in enumerate(lst):
                if dic[key] == value:
                    return i
            return ValueError

        submitter = f"{submitter} [{host_galaxy_source}]"
        for g in galaxies:
            g["ID"] = str(g["ID"])
        idx = index(galaxies, key="ID", value=str(host_galaxy_id))

        UserGalaxyQ3C.objects.update_or_create(
            objname=host_galaxy_id,  # e.g, 'PSO ... ' for PS1
            ra=galaxies[idx]["RA"],
            dec=galaxies[idx]["Dec"],
            z=z,
            z_err=z_err,  # same for z_err, z_pos_err, z_neg_err
            z_pos_err=z_err,
            z_neg_err=z_err,
            z_type="user spec-z",
            default_mag=galaxies[idx]["Mags"],
            source=host_galaxy_source,
            submitter=submitter,  # record submitter and original catalog
            og_id=galaxies[idx]["troveID"],
        )
