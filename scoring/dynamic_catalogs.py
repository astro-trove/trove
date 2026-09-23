"""
Dynamic catalogs
"""

import logging

from astropy import units as u

from django.conf import settings

cosmo = settings.COSMO

from .models import UserGalaxyQ3C
from candidate_vetting.public_catalogs.catalog import StaticCatalog
from candidate_vetting.public_catalogs.util import RADIUS_ARCSEC

logger = logging.getLogger(__name__)


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

    def pcc_filter(self, ra, dec, radius=RADIUS_ARCSEC, pcc_max=0.5):
        # a human deliberately picked this host, so keep it whatever its chance
        # alignment probability works out to. The magnitude we recompute Pcc from
        # is whatever the original catalog reported, which for older host tables
        # can be inconsistent with the current Pcc calibration.
        return super().pcc_filter(ra, dec, radius=radius, pcc_max=1.0)

    def to_standardized_catalog(self, df):
        df = self._standardize_df(df)
        df["lumdist"] = cosmo.luminosity_distance(df.z).to(u.Mpc).value
        # propagate the redshift error through the cosmology. Taking the
        # luminosity distance *of* z_err is a different quantity: at z = 0.34
        # +/- 0.05 it gives +/- 224 Mpc where the real spread is +315/-304.
        df["lumdist_pos_err"] = (
            cosmo.luminosity_distance(df.z + df.z_pos_err).to(u.Mpc).value
            - df["lumdist"]
        )
        df["lumdist_neg_err"] = df["lumdist"] - cosmo.luminosity_distance(
            (df.z - df.z_neg_err).clip(lower=0)
        ).to(u.Mpc).value
        df["lumdist_err"] = (df["lumdist_pos_err"] + df["lumdist_neg_err"]) / 2
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

        objname = str(host_galaxy_id)

        # our own entries show up in the host galaxy table alongside the catalog
        # they came from, so picking one means correcting it. Resolve it back to
        # the row it came from instead of stacking a second entry on top.
        source = host_galaxy_source
        if source == self.name:
            previous = (
                UserGalaxyQ3C.objects.filter(objname=objname).order_by("-id").first()
            )
            if previous is None:
                # the host galaxy table names a row that no longer exists; treat
                # it as a fresh entry rather than failing the submission
                logger.warning(
                    f"No stored galaxy for {objname}, adding it as a new entry"
                )
            else:
                source = previous.source

        submitter = f"{submitter} [{source}]"

        # earlier versions keyed update_or_create on every field, so the same
        # galaxy could accumulate a row per submission. Collapse those onto the
        # newest one before updating it.
        existing = UserGalaxyQ3C.objects.filter(objname=objname, source=source)
        if existing.count() > 1:
            keep = existing.order_by("-id").first()
            logger.warning(
                f"Found {existing.count()} rows for {objname} ({source}); "
                f"keeping id {keep.id} and deleting the rest"
            )
            existing.exclude(id=keep.id).delete()

        UserGalaxyQ3C.objects.update_or_create(
            objname=objname,  # e.g, 'PSO ... ' for PS1
            source=source,
            defaults=dict(
                ra=galaxy["RA"],
                dec=galaxy["Dec"],
                z=z,
                z_err=z_err,  # same for z_err, z_pos_err, z_neg_err
                z_pos_err=z_err,
                z_neg_err=z_err,
                z_type="user spec-z",
                default_mag=galaxy["Mags"],
                submitter=submitter,  # record submitter and original catalog
                # host tables written before troveID was added don't carry one
                og_id=galaxy.get("troveID"),
            ),
        )
