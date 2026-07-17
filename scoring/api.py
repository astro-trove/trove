from typing import List
from ninja import Router, Schema
from ninja.orm import create_schema
from tom_nonlocalizedevents.models import EventCandidate
from tom_targets.utils import cone_search_filter
from trove_targets.models import Target
from .util import get_event_candidate_scores

router = Router()

def _compute_scores(ecs):
    # calculate the final scores, sorted by decreasing score, and return
    # Add agn scoring potentially here
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
    ```
    curl -X 'GET' \
    'http:/localhost:8000/api/score/GW190814' \
    -H 'accept: */*' \
    -u <username>:<password>
    ```

    - With optional paramaters
    
    ```
    curl -X 'GET' \
    'http://localhost:8000/api/score/S251112cm?candidate_names=S251112cm_X78,AT2025adht' \
    -H 'accept: */*' \
    -u <username>:<password>
    ```

    - For just one event
    ```
    curl -X 'GET' \
    'http://localhost:8000/api/score/S251112cm?candidate_names=S251112cm_X78' \
    -H 'accept: */*' \
    -u <username>:<password>
    ```

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
        
    return _compute_scores(ecs) 

@router.get("/{nle_name}/cone_search")
def get_scores_from_cone_search(request, nle_name:str, ra:float, dec:float, radius:float=2):
    """
    Endpoint to get the scores of all candidates within a cone search

    *Args*:
    - ra (float): The central RA of the cone, in degrees
    - dec (float): The central declination of the cone, in degrees
    - radius (float): The radius of the cone, in arcseconds. Default is 2"

    *Returns*:
    A list of dictionaries containing information on the candidates. This has keys
    (1) "id", (2) "target" (target info), (3) "nonlocalizedevent" (nonlocalizedevent info),
    and (4) "score" (a dictionary of the scores, with keys for the type of scoring algorithm used).

    *Example*:
    ```
    curl -X 'GET' \
    'http://localhost:8000/api/score/S251112cm/cone_search?ra=147.47566&dec=0.616566666' \
    -H 'accept: */*' \
    -u <username>:<password>
    ```
    Or, to provide a search radius
    ```
    curl -X 'GET' \
    'http://localhost:8000/api/score/S251112cm/cone_search?ra=147.475&dec=0.6165&radius=10' \
    -H 'accept: */*' \
    -u <username>:<password>
    ```
    """
    
    # convert the radius to degrees
    radius_deg = radius / 3600.

    # get the event candidates associated with this NLE
    ecs = EventCandidate.objects.filter(
        nonlocalizedevent__event_id = nle_name
    ).select_related(
        "target",
        "nonlocalizedevent"
    )

    # do the cone search
    targets = Target.objects.filter(id__in = ecs.values_list("target", flat=True))
    targets = cone_search_filter(targets, ra, dec, radius_deg)

    # get the eventcandidates associated with these targets
    ecs = ecs.filter(target_id__in = targets.values_list("id", flat=True))

    # compute the score and return the data to the user
    return _compute_scores(ecs)
