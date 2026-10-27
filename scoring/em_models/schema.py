"""
A pydantic schema for the model jsons
"""

from pydantic import BaseModel, model_validator, field_validator
from typing import Optional, Union, List
from pathlib import Path

class SubScoreControlSchema(BaseModel):
    match: float
    no_match: float

class ScoreControlSchema(BaseModel):
    agn: SubScoreControlSchema
    ps: SubScoreControlSchema
    mpc: SubScoreControlSchema
    phot: SubScoreControlSchema

class PhotMethodSchema(BaseModel):
    knscorer: bool
    metric: bool
    agn_flare: bool
    lum_max: bool
    decay_rate: bool
    rise_time: bool
    predets_filter: bool

class PhotParameterSchema(BaseModel):
    lum_max: Optional[List[float]] = None
    peak_time: Optional[List[float]] = None
    decay_rate: Optional[List[float]] = None
    max_predets: Optional[int] = None
    t_pre: int
    t_post: int
    phot_score_snr_min: int
    min_time_separation: Optional[Union[float,int]] = None
    min_baseline_pts: Optional[Union[float,int]] = None
    flare_sigma_thresh: Optional[Union[float,int]] = None
    flare_pair_max_gap_days: Optional[Union[float,int]] = None
    flare_corroboration_window_days: Optional[Union[float,int]] = None
    flare_score_center_frac: Optional[Union[float,int]] = None
    flare_score_width_frac: Optional[Union[float,int]] = None

class TransientModelSchema(BaseModel):
    name: str
    ps_crossmatch: bool
    query_phot: bool
    mpc_crossmatch: bool
    agn_crossmatch: bool
    host_association: bool
    localization_score: bool
    distance_score: bool
    knscorer_allowed: bool
    score_controls: ScoreControlSchema
    phot_method: PhotMethodSchema
    phot_parameter: PhotParameterSchema

    @staticmethod
    def from_json(jsonpath):
        return TransientModelSchema.model_validate_json(
            Path(jsonpath).read_text()
        )
