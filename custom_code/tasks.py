from django.conf import settings
from django_tasks import task
from trove_targets.models import Target
from .hooks import target_post_save


@task(queue_name="target_post_save", priority=settings.PRIORITY_HIGH)
def async_target_post_save(target_id, *args, **kwargs):
    target = Target.objects.get(id=target_id)
    target_post_save(target, *args, **kwargs)
