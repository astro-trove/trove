"""
Dynamic catalogs
"""

from astropy import units as u

from django.conf import settings

cosmo = settings.COSMO

from .models import UserGalaxyQ3C
from candidate_vetting.public_catalogs.catalog import StaticCatalog


def find_galaxy(galaxies, host_galaxy_id, host_galaxy_source):
    """Find the row of the host galaxy table that the user picked.

    IDs are only unique within a catalog, so both the ID and the source are
    needed to identify a row. Returns None if the pair isn't in the table.
    """
    for galaxy in galaxies or []:
        if str(galaxy.get("ID")) == str(host_galaxy_id) and str(
            galaxy.get("Source")
        ) == str(host_galaxy_source):
            return galaxy
    return None


class UserGalaxy(StaticCatalog):
    name = "user-submitted"
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
        galaxy = find_galaxy(galaxies, host_galaxy_id, host_galaxy_source)
        if galaxy is None:
            raise ValueError(
                f"{host_galaxy_id} is not listed under {host_galaxy_source} in "
                f"the host galaxy table for {target.name}"
            )

        submitter = f"{submitter} [{host_galaxy_source}]"

        UserGalaxyQ3C.objects.update_or_create(
            objname=host_galaxy_id,  # e.g, 'PSO ... ' for PS1
            ra=galaxy["RA"],
            dec=galaxy["Dec"],
            z=z,
            z_err=z_err,  # same for z_err, z_pos_err, z_neg_err
            z_pos_err=z_err,
            z_neg_err=z_err,
            z_type="user spec-z",
            default_mag=galaxy["Mags"],
            source=host_galaxy_source,
            submitter=submitter,  # record submitter and original catalog
            # host tables written before troveID was added don't carry one
            og_id=galaxy.get("troveID"),
        )
