from typing import List
from ninja import Router, Schema
from ninja.orm import create_schema
from tom_nonlocalizedevents.models import EventCandidate
from .util import get_event_candidate_scores

router = Router()

EventCandidateSchema = create_schema(
    EventCandidate,
    depth = 1,
    fields = ["target", "nonlocalizedevent"],
    custom_fields = [("score", dict, ""),]
)

@router.get("/{nle_name}", response=List[EventCandidateSchema])
def get_scores_from_nle_name(request, nle_name):

    # get the event candidates associated with this NLE
    ecs = EventCandidate.objects.filter(nonlocalizedevent__event_id = nle_name)

    # calculate the final scores, sorted by decreasing score, and return
    return get_event_candidate_scores(ecs)
    
    
