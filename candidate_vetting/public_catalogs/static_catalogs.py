"""
Define the static catalogs for querying
"""
from .catalog import StaticCatalog
from ..models import (
    AsassnQ3C,
    DesiSpecQ3C,
    FermiLatQ3C,
    Gaiadr3VariableQ3C,
    GladePlusQ3C,
    GwgcQ3C,
    HecateQ3C,
    LsDr10Q3C,
    MilliquasQ3C,
    Ps1Q3C,
    RomaBzcatQ3C,
    Sdss12PhotozQ3C
)

class AsassnVariableStar(StaticCatalog):
    catalog_model = AsassnQ3C

class DesiSpec(StaticCatalog):
    catalog_model = DesiSpecQ3C
    ra_colname = "target_ra"
    dec_colname = "target_dec"

class FermiLat(StaticCatalog):
    catalog_model = FermiLatQ3C

class Gaiadr3Variable(StaticCatalog):
    catalog_model = Gaiadr3VariableQ3C

class GladePlus(StaticCatalog):
    catalog_model = GladePlusQ3C

class Gwgc(StaticCatalog):
    catalog_model = GwgcQ3C

class Hecate(StaticCatalog):
    catalog_model = HecateQ3C

class LsDr10(StaticCatalog):
    catalog_model = LsDr10Q3C
    dec_colname = "declination"
    
class Milliquas(StaticCatalog):
    catalog_model = MilliquasQ3C

class Ps1(StaticCatalog):
    catalog_model = Ps1Q3C

class RomaBzcat(StaticCatalog):
    catalog_model = RomaBzcatQ3C

class Sdss12Photoz(StaticCatalog):
    catalog_model = Sdss12PhotozQ3C
