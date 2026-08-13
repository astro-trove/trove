"""
Some functions for accessing the EventCandidate table inside a django template
"""

import numpy as np
from collections import OrderedDict
from functools import partial
from django import template
from django.core.cache import cache
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
def get_agn_toggle():
    """Current value of the site-wide, cache-backed agn_toggle flag."""
    return cache.get("agn_toggle", True)


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

    try:
        target = Target.objects.get(id=target_id)
    except Target.DoesNotExist:
        return f"No target with id {target_id}"

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
    ### TODO: Potentially just add "agn" here?
    te_set = te.filter(key__in=TARGETEXTRA_KEYS).exclude(key__in=["ps_score"])
    basic_score_details.append(te_set)

    # NLE-specific scores/details
    score_details = []
    for event_candidate in target.eventcandidate_set.all():
        # TODO: show localization_id in the scoring info card being built on
        # the kilonovaSCORER-implementation branch 

        sf_set = event_candidate.scorefactor_set.exclude(
            key__in=TARGETEXTRA_KEYS
            # exclude keys in TargetExtra + exclude mpc_score, predetection_score
            + ["mpc_score", "predetection_score", "localization_id"]
        ).all()
        # Reorder them for user-friendly printing later. A ScoreFactor key that
        # is not in keymap sorts to the end rather than raising -- a new scorer
        # writing an unregistered key must not take the target page down.
        sf_set = sorted(
            sf_set,
            key=lambda sf: (order.index(sf.key) if sf.key in order else len(order),
                            sf.key),
        )
        score_details.append(sf_set)

    # for printing
    res = {}
    basic_score_key = "Basic Score Details"
    for queryset in basic_score_details:
        for te in queryset:
            if basic_score_key not in res:
                res[basic_score_key] = ""
            label, fmter = keymap.get(te.key, (te.key, _float_format))
            if te.value is None or isinstance(te.value, str):
                s = te.value
            else:
                s = _safe_format(te.value, fmter)
            res[basic_score_key] += f"&emsp;{label}: {s}\n"

    for queryset in score_details:
        for score_factor in queryset:
            nle = score_factor.event_candidate.nonlocalizedevent
            if nle not in res:
                res[nle] = ""
            label, fmter = keymap.get(score_factor.key,
                                      (score_factor.key, _float_format))
            if score_factor.value in (None, np.nan, "nan"):
                res[nle] += f"&emsp;{label}: {score_factor.value}\n"
            else:
                res[nle] += f"&emsp;{label}: {_safe_format(score_factor.value, fmter)}\n"

    out = ""
    for key, s in res.items():
        out += f"<h6>{key}</h6>"
        out += s
        out += "\n\n"

    return mark_safe(linebreaks(out))


def _safe_format(value, fmter):
    """Apply `fmter` to a ScoreFactor/TargetExtra value without ever raising.

    Values arrive as strings and may be numeric ("0.97") or free text (e.g.
    kilonova_skip_reason). The previous code decided whether to call float()
    by comparing the display label to "Host Galaxy Name", so any other
    non-numeric key crashed the whole target page. Try numeric, then raw, then
    fall back to str().
    """
    for candidate in (lambda: fmter(float(value)), lambda: fmter(value)):
        try:
            return candidate()
        except (TypeError, ValueError):
            continue
    return str(value)


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
