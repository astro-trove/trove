"""
Some config variables for vetting that are used in multiple places
"""
from .vet_basic import vet_basic
from .vet_bns import vet_bns
from .vet_kn_in_sn import vet_kn_in_sn
from .vet_super_kn import vet_super_kn

VETTING_FORM_CHOICES = { # these tuples are (value to save, value to show)
    "": # if NLE most likely class not known, everything goes
        [("basic", "Basic Vetting"),
         ("KN", "Transient: Kilonova"),
         ("KN-in-SN", "Transient: Kilonova-in-Supernova"),
         ("super-KN", "Transient: Super-Kilonova"),
         #("AGN-flare", "Transient: BBH-induced AGN Flare"),
        ],
    "BNS":
        [("basic", "Basic Vetting"),
         ("KN", "Transient: Kilonova")
        ],
    "NSBH":
        [("basic", "Basic Vetting"),
         ("KN", "Transient: Kilonova")
        ],
    "SSM":
        [("basic", "Basic Vetting"),
         ("KN", "Transient: Kilonova"),
         ("KN-in-SN", "Transient: Kilonova-in-Supernova"),
         ("super-KN", "Transient: Super-Kilonova"),
         #("AGN-flare", "Transient: BBH-induced AGN Flare"),
        ],
    "BBH":
        [("basic", "Basic Vetting"),
         #("AGN-flare", "Transient: BBH-induced AGN Flare"),
        ]
    }
        
FORM_CHOICE_FUNC_MAP = { # this should have the same keys as the first value in the tuples in the VETTING_FORM_CHOICES variable!
    "basic":vet_basic,
    "KN":vet_bns,
    "KN-in-SN":vet_kn_in_sn,
    "super-KN":vet_super_kn,
    # "AGN-flare":???,
}
