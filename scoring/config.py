"""
Some config variables for vetting that are used in multiple places
"""
from .em_models.util import MODEL_BY_NAME

VETTING_FORM_CHOICES = { # these tuples are (value to save, value to show)
    "basic":("basic", "Basic Vetting"),
    "KN":("KN", "Transient: Kilonova"),
    "KN-in-SN":("KN-in-SN", "Transient: Kilonova-in-Supernova"),
    "super-KN":("super-KN", "Transient: Super-Kilonova"),
    "AGN-flare":("AGN-flare", "Transient: BBH-induced AGN Flare")
}

VETTING_FORM_INITIALS = {
    "": ("basic", "Basic Vetting"),
    "BNS":("KN", "Transient: Kilonova"),
    "NSBH":("KN", "Transient: Kilonova"),
    "SSM":("KN-in-SN", "Transient: Kilonova-in-Supernova"), # not a statement on reality of KNe-in-SNe, just to distinguish from BNS/NSBH
    "BBH":("AGN-flare", "Transient: BBH-induced AGN Flare"),
    }

DETECTION_HORIZON_DEFAULTS = { # time horizon (min, max) for first detections, dependent on NLE
    "": (0, 10), # if NLE most likely class not known, 0, 10
    "BNS": MODEL_BY_NAME["KN"].detection_horizon,
    "NSBH": MODEL_BY_NAME["KN"].detection_horizon,
    "SSM": MODEL_BY_NAME["KN-in-SN"].detection_horizon,
    "BBH": MODEL_BY_NAME["AGN-flare"].detection_horizon,
}
