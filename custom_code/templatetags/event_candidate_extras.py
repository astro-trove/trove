"""
Some functions for accessing the EventCandidate table inside a django template
"""

import numpy as np
from collections import OrderedDict
from functools import partial
from django import template
from django.template.defaultfilters import linebreaks
from django.utils.safestring import mark_safe
from trove_targets.models import Target
from tom_targets.models import TargetExtra
from scoring.util import (
    get_event_candidate_scores as _get_event_candidate_scores,
    get_target_score as _get_target_score,
    TARGETEXTRA_KEYS,
)

register = template.Library()


@register.simple_tag
def get_event_candidate_scores(*args, **kwargs):
    """A wrapper on the imported _get_event_candidate_scores, but registered as a tag"""
    return _get_event_candidate_scores(*args, **kwargs)


@register.simple_tag
def get_target_score(*args, **kwargs):
    """A wrapper on the imported _get_target_score, but registered as a tag"""
    return _get_target_score(*args, **kwargs)


@register.simple_tag
def display_score_details(target_id):

    if target_id is None:
        return "Target ID is None!"

    target = Target.objects.get(id=target_id)

    keymap = OrderedDict(
        ps_score=("Point Source Score (1 or 0)", _bool_format),
        mpc_score=("Minor Planet Center Score (1 or 0)", _bool_format),
        mpc_match_name=("MPC Match Name", _str_format),
        mpc_match_date=("MPC Match Date", _str_format),
        mpc_match_sep=('MPC Match Separation (")', _float_format),
        skymap_score=("2D Localization Score", _float_format),
        host_distance_score=("3D Association Score", _float_format),
        host_name=("Host Galaxy Name", _str_int_format),
        agn_score=("AGN Score (1 or 0)", _bool_format),
        phot_peak_lum=("Maximum Luminosity", partial(_sci_format, unit="erg/s")),
        phot_peak_time=(
            "Time of Maximum Light Curve",
            partial(_float_format, unit="days"),
        ),
        phot_decay_rate=(
            "Light Curve Slope (positive is brightening)",
            partial(_float_format, unit="mag/day"),
        ),
    )
    order = list(keymap.keys())

    # basic scores/details
    basic_score_details = []
    te = TargetExtra.objects.filter(target_id=target_id)
    basic_score_details.append(te.filter(key="ps_score"))  # first, PS score
    for (
        event_candidate
    ) in target.eventcandidate_set.all():  # add MPC score from scorefactor, if present
        sf_set = event_candidate.scorefactor_set.filter(key="mpc_score")
        basic_score_details.append(sf_set)
    # Potentially just add "agn" here?
    te_set = te.filter(key__in=TARGETEXTRA_KEYS).exclude(key__in=["ps_score"])
    basic_score_details.append(te_set)

    # NLE-specific scores/details
    score_details = []
    for event_candidate in target.eventcandidate_set.all():
        sf_set = event_candidate.scorefactor_set.exclude(
            key__in=TARGETEXTRA_KEYS
            + ["mpc_score", "predetection_score"]  # exclude keys in TargetExtra + exclude mpc_score, predetection_score
        ).all()
        # reorder them for user-friendly printing later
        sf_set = sorted(sf_set, key=lambda sf: order.index(sf.key))
        score_details.append(sf_set)

    # for printing
    res = {}
    basic_score_key = "Basic Score Details"
    for queryset in basic_score_details:
        for te in queryset:
            if basic_score_key not in res:
                res[basic_score_key] = ""
            if te.key in keymap:
                label, fmter = keymap[te.key]
            else:
                label = te.key
                fmter = _float_format
            if te.value is None or isinstance(te.value, str):
                s = te.value
            else:
                s = fmter(float(te.value))
            res[basic_score_key] += f"&emsp;{label}: {s}\n"

    for queryset in score_details:
        for score_factor in queryset:
            nle = score_factor.event_candidate.nonlocalizedevent
            if nle not in res:
                res[nle] = ""
            if score_factor.key in keymap:
                label, fmter = keymap[score_factor.key]
            else:
                label = score_factor.key
                fmter = _float_format
            if score_factor.value in (None, np.nan, "nan"):
                res[nle] += f"&emsp;{label}: {score_factor.value}\n"
            else:
                res[nle] += (
                    f"&emsp;{label}: {fmter(score_factor.value)}\n"
                    if label == "Host Galaxy Name"
                    else f"&emsp;{label}: {fmter(float(score_factor.value))}\n"
                )

    out = ""
    for key, s in res.items():
        out += f"<h6>{key}</h6>"
        out += s
        out += "\n\n"

    return mark_safe(linebreaks(out))


def _float_format(flt, unit=""):
    return f"{flt:.2f} {unit}"


def _sci_format(flt, unit=""):
    prefactor, power = f"{flt:.2e}".split("e")
    if power[0] == "+":
        power = power[1:]
    return f"{prefactor} x 10<sup>{power}</sup> {unit}"


def _bool_format(flt):
    return int(flt)


def _str_int_format(s):
    try:
        return str(int(s))
    except ValueError:
        return str(s)


def _str_format(s):
    return str(s)
