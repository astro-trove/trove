"""
Asynchronous tasks for querying public services that takes a long time
"""
from django_tasks import task
from django.conf import settings

from .public_catalogs.phot_catalogs import ATLAS_Forced_Phot
from trove_targets.models import Target

@task()
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
