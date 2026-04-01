from typing import List
from ninja import Router, Schema
from ninja.orm import create_schema
from tom_nonlocalizedevents.models import EventCandidate
from .util import get_event_candidate_scores

router = Router()

@router.get("/{nle_name}")
def get_scores_from_nle_name(request, nle_name:str, candidate_names:str|None=None):
    """
    Endpoint to get the score based on the nle name

    *Args*:
    - nle_name (str): The name of the poorly localized event
    - candidate_names (str): A comma separated list of candidate names (ex. "AT2025xyz,AT2026qwe")

    *Returns*:
    A list of dictionaries containing information on the candidates. This has keys
    (1) "id", (2) "target" (target info), (3) "nonlocalizedevent" (nonlocalizedevent info),
    and (4) "score" (a dictionary of the scores, with keys for the type of scoring algorithm used).

    *Example*:
    - Simple
    http://localhost:8000/api/score/S251112cm

    - With optional paramters
    http://localhost:8000/api/score/S251112cm?candidate_names="S251112cm_X78,AT2025adht"

    - For just one event
    http://localhost:8000/api/score/S251112cm?candidate_names=S251112cm_X78
    """
    
    # get the event candidates associated with this NLE
    ecs = EventCandidate.objects.filter(
        nonlocalizedevent__event_id = nle_name
    ).select_related(
        "target",
        "nonlocalizedevent"
    )

    if candidate_names is not None:
        ecs = ecs.filter(target__name__in = candidate_names.split(","))
        
    # calculate the final scores, sorted by decreasing score, and return
    ecs_with_scores = get_event_candidate_scores(ecs)

    return [
        {
            "id": ec.id,
            "target": {
                "id": ec.target.id,
                "name": ec.target.name,
                "ra":ec.target.ra,
                "dec":ec.target.dec,
                "tns_redshift":ec.target.redshift,
                "tns_classification":ec.target.classification
            },
            "nonlocalizedevent": {
                "id": ec.nonlocalizedevent.id,
                "event_id": ec.nonlocalizedevent.event_id,
                "event_type": ec.nonlocalizedevent.event_type
            },
            "score": ec.score,
        }
        for ec in ecs_with_scores
    ]
