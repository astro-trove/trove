from django import template
from django.utils.safestring import mark_safe
from tom_targets.models import TargetExtra
import math
import numpy as np
import json

register = template.Library()

MIN_REDSHIFT_DECIMALS = 2
MAX_REDSHIFT_DECIMALS = 7


def _round_to_one_sig_fig(value):
    value = float(value)
    if value == 0 or math.isnan(value):
        return value
    magnitude = 10 ** math.floor(math.log10(abs(value)))
    return round(value / magnitude) * magnitude


def _decimal_places_for_rounded_value(value):
    rounded = _round_to_one_sig_fig(value)
    if rounded == 0 or math.isnan(rounded):
        return None
    if rounded >= 1:
        return 0
    text = f"{rounded:.15f}".rstrip("0")
    if "." not in text:
        return 0
    return len(text.split(".")[1])


def _format_fixed(value, decimal_places):
    return f"{float(value):.{decimal_places}f}"


def _z_decimal_places(err_decimal_places):
    if err_decimal_places is None:
        return MIN_REDSHIFT_DECIMALS
    if err_decimal_places > MIN_REDSHIFT_DECIMALS:
        return min(err_decimal_places + 1, MAX_REDSHIFT_DECIMALS + 1)
    return MIN_REDSHIFT_DECIMALS


def _format_redshift_error(err):
    err_dp = _decimal_places_for_rounded_value(err)
    if err_dp is None:
        return None
    err_dp = min(err_dp, MAX_REDSHIFT_DECIMALS)
    return _format_fixed(_round_to_one_sig_fig(abs(err)), err_dp)


def format_redshift_parts(z, z_err):
    """
    Format redshift and uncertainty for the host galaxy table.

    When the uncertainty is finer than hundredths, z is shown with one extra decimal
    place beyond the quoted error (e.g. 0.03921 +/- 0.0001).
    """
    try:
        z = float(z)
    except TypeError:
        return {"empty": True}

    if math.isnan(z):
        return {"empty": True}

    if z_err is None or (isinstance(z_err, (float, np.floating)) and math.isnan(z_err)):
        return {"z": _format_fixed(z, MIN_REDSHIFT_DECIMALS), "no_err": True}

    if isinstance(z_err, (list, tuple, np.ndarray)):
        z_neg = float(z_err[0])
        z_pos = float(z_err[1])
        ref_err = max(abs(z_neg), abs(z_pos))
        err_dp = _decimal_places_for_rounded_value(ref_err)
        z_dp = _z_decimal_places(err_dp)
        neg = _format_redshift_error(z_neg)
        pos = _format_redshift_error(z_pos)
        return {
            "z": _format_fixed(z, z_dp),
            "neg": neg if neg is not None else _format_fixed(abs(z_neg), MIN_REDSHIFT_DECIMALS),
            "pos": pos if pos is not None else _format_fixed(abs(z_pos), MIN_REDSHIFT_DECIMALS),
        }

    z_err = float(z_err)
    if math.isnan(z_err):
        return {"z": _format_fixed(z, MIN_REDSHIFT_DECIMALS), "no_err": True}

    err = _format_redshift_error(z_err)
    if err is None:
        return {"z": _format_fixed(z, MIN_REDSHIFT_DECIMALS), "no_err": True}

    err_dp = _decimal_places_for_rounded_value(z_err)
    z_dp = _z_decimal_places(err_dp)
    return {"z": _format_fixed(z, z_dp), "err": err}


def _apply_redshift_formatting(galaxy):
    """Mutate galaxy z / zErr in place using format_redshift_parts."""
    parts = format_redshift_parts(galaxy.get("z"), galaxy.get("zErr"))
    if parts.get("empty"):
        return
    galaxy["z"] = parts["z"]
    if "neg" in parts:
        galaxy["zErr"] = [parts["neg"], parts["pos"]]
    elif parts.get("no_err"):
        galaxy["zErr"] = float("nan")
    elif "err" in parts:
        galaxy["zErr"] = parts["err"]


@register.filter
def islist(value):
    return isinstance(value, list) or isinstance(value, np.ndarray)


@register.simple_tag
def redshift_cell(z, z_err):
    parts = format_redshift_parts(z, z_err)
    if parts.get("empty"):
        return ""
    if "neg" in parts:
        return mark_safe(
            f'{parts["z"]}<span class="supsubz">'
            f'<sup>+{parts["pos"]}</sup><sub>-{parts["neg"]}</sub></span>'
        )
    if parts.get("no_err"):
        return mark_safe(f'{parts["z"]} (no err.)')
    if "err" in parts:
        return mark_safe(f'{parts["z"]}&nbsp;&plusmn;&nbsp;{parts["err"]}')
    return mark_safe(parts["z"])


@register.inclusion_tag("tom_targets/partials/galaxy_table.html")
def galaxy_table(target):
    """
    Displays the most likely host galaxy matches.
    """
    te = TargetExtra.objects.filter(target=target, key="Host Galaxies")
    if te.exists():
        galaxies = json.loads(te.first().value)
        for galaxy in galaxies:
            _apply_redshift_formatting(galaxy)
    else:
        galaxies = None
    return {"galaxies": galaxies}
