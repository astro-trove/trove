from django import template
from tom_targets.models import TargetExtra
import numpy as np
import json

register = template.Library()


@register.filter
def islist(value):
    return isinstance(value, list) or isinstance(value, np.ndarray)
    
@register.inclusion_tag('tom_targets/partials/galaxy_table.html')
def galaxy_table(target):
    """
    Displays the most likely host galaxy matches.
    """

    SOURCE_DISPLAY_NAMES = {
        "GLADE": "GLADE",
        "GladePlus": "GLADE+",

        "DESI": "DESI",
        "DesiDr1": "DESI DR1",
        "DesiSpec": "DESI Spectroscopic Catalog",

        "LsDr9North": "LS DR9 North",
        "LsDr10": "LS DR10",
        "LsDr10South": "LS DR10 South",
        "LS_DR10": "LS DR10",

        "Sdss12Photoz": "SDSS DR12 Photometric",
        "SDSS_DR12": "SDSS DR12",

        "Ps1Galaxy": "Pan-STARRS1 Galaxy Catalog",
        "PS1_STRM": "Pan-STARRS1 STRM",

        "NedLvs": "NED-LVS",

        "GWGC": "GWGC",
        "Gwgc": "GWGC",

        "Cosmicflows4": "Cosmicflows-4",

        "ExtendedVirgoClusterCatalog": "Extended Virgo Cluster Catalog (EVCC)",

        "Hecate": "HECATE",
        "HECATE": "HECATE",
        "Hecate1": "HECATE",
        "Hecate2": "HECATE",
    }

    te = TargetExtra.objects.filter(target=target, key='Host Galaxies')
    if te.exists():
        galaxies = json.loads(te.first().value)
        
        try:
            for galaxy in galaxies:
                cur_source = galaxy['Source']
                galaxy['Source'] = SOURCE_DISPLAY_NAMES[cur_source]
        except:
            print("Unable to change name")
        
    else:
        galaxies = None
    return {'galaxies': galaxies}
