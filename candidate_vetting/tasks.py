"""
Asynchronous tasks for (1) querying public services that takes a long time or 
(2) vetting all candidates
"""
from django_tasks import task
from django.conf import settings

from .public_catalogs.phot_catalogs import ATLAS_Forced_Phot
from trove_targets.models import Target

@task(queue_name="atlas_query", priority=settings.PRIORITY_MID)
def async_atlas_query(
        target_id:int,
        *args, **kwargs
) -> None:
    t = Target.objects.get(id=target_id)
    ATLAS_Forced_Phot("atlas").query(
        t,
        token=settings.ATLAS_API_KEY,
        *args, **kwargs
    )
    
@task(queue_name="vet_all", priority=settings.PRIORITY_HIGH)
def async_vet(
        target_ids:list,
        nle_event_id:str,
        vetting_mode:str,
        *args, **kwargs
) -> None:
    from .config import FORM_CHOICE_FUNC_MAP # import within function to avoid circular import error
    
    if vetting_mode == "basic":
        for ti in target_ids:
            FORM_CHOICE_FUNC_MAP[vetting_mode](
                target_id=ti)
    else:
        for ti in target_ids:
            FORM_CHOICE_FUNC_MAP[vetting_mode](
                target_id=ti,
                nonlocalized_event_name=nle_event_id)
