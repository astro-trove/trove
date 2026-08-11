# =========================================================================
# KilonovaScorer — simulation-based scoring for kilonova candidates.
#
# Single-scorer package (ISSUE #15 consolidation, IMPROVEMENTS.md §15):
# core.py is the SOLE scoring module.  The former kilonovascorer_v1 has been
# removed; kilonovascorer_v3 (paper-aligned _KNe column names) is the only
# scorer.
#
# Imports below are EXPLICIT -- no `from .core import *` wildcard -- so name
# resolution is visible in the source rather than import-order dependent.
#
# Keep this module cheap. Python executes it on every submodule import, so
# anything imported here lands in the Django server process even though TROVE
# only ever imports submodules directly (`from .KilonovaScorer.core import X`).
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
#     import scoring.KilonovaScorer.simulation as sim
#     sim.simulate_kilonova(...)

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
