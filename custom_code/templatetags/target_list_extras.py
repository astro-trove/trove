from django import template
from tom_targets.models import TargetExtra
import numpy as np
import json
import math

register = template.Library()


@register.filter
def islist(value):
    return isinstance(value, list) or isinstance(value, np.ndarray)
    
@register.inclusion_tag('tom_targets/partials/galaxy_table.html')
def galaxy_table(target):
    """
    Displays the most likely host galaxy matches.
    """

    te = TargetExtra.objects.filter(target=target, key='Host Galaxies')
    if te.exists():
        galaxies = json.loads(te.first().value)
        for galaxy in galaxies:
            z = galaxy['z']
            z_err = galaxy['zErr']

            try:
                # If zErr is a float
                places = -math.floor(math.log10(z_err))
                galaxy['z'] = f"{z:.{places}f}"
                galaxy['zErr'] = f'{z_err:.{places}f}'
            except:
                # If zErr is a list of upper and lower error
                places_l = []
                rounded_bounds = []
                try:
                    for z_err_bound in z_err:
                        place = -math.floor(math.log10(z_err_bound))
                        places_l.append(place)
                        rounded_bounds.append(f'{z_err_bound:.{place}f}')
                    galaxy['z'] = f"{z:.{min(places_l)}f}"
                    galaxy['zErr'] = rounded_bounds
                except:
                    # Considers case when NaN, does nothing
                    pass

    else:
        galaxies = None
    return {'galaxies': galaxies}
