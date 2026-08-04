# =========================================================================
# KilonovaScorer — simulation-based scoring for kilonova candidates.
#
# Single-scorer package (ISSUE #15 consolidation, IMPROVEMENTS.md §15):
# core2.py is the SOLE scoring module.  The former core.py / kilonovascorer_v1
# has been removed; kilonovascorer_v3 (paper-aligned _KNe column names) is the
# only scorer, and plotting.py / the notebook use those names.
#
# Imports below are EXPLICIT -- no `from .core2 import *` wildcard -- so name
# resolution is visible in the source rather than import-order dependent.
# =========================================================================
from .core import (
    load_observations,
    parse_json_photometry,
    preprocess_lsst_like,
    predictive_tail_kde,
    compute_consistent_ids_anyhit,
    overlap_chain,
    binned_stats_cumulative_ptail,
    kilonovascorer_v3,
)

# Grid generation stays opt-in: `simulation.py` pulls in the heavy
# redback / bilby / astropy / lal stack, so it is NOT imported here.  Import
# it explicitly only when generating a grid:
#     import KilonovaScorer.simulation as sim
#     sim.simulate_kilonova(...)
#from .simulation import simulate_kilonova

import pandas as pd
import numpy as np
import json
from pathlib import Path
import matplotlib as mpl

mpl.rcParams["text.usetex"] = False

__all__ = [
    "load_observations",
    "parse_json_photometry",
    "preprocess_lsst_like",
    "predictive_tail_kde",
    "compute_consistent_ids_anyhit",
    "overlap_chain",
    "binned_stats_cumulative_ptail",
    "kilonovascorer_v3",
]
