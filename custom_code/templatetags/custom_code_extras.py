"""
Custom template tags for TROVE. Includes safe overrides for tags that can raise
when ContentType or permissions are not yet set up (e.g. fresh DB).
"""
from django import template
from django.contrib.contenttypes.models import ContentType
from django.db.models import IntegerField
from django.db.models.functions import Cast
from django_comments.models import Comment
from datetime import datetime
from guardian.shortcuts import get_objects_for_user

from tom_targets.models import Target

register = template.Library()


@register.inclusion_tag('comments/list.html', takes_context=True)
def recent_comments(context, limit=10):
    """
    Displays recent comments for targets the user can view. Safe when the
    ContentType for the project's Target model does not exist yet (e.g. after
    a fresh migrate or before guardian is fully synced).
    """
    user = context['request'].user
    try:
        targets_for_user = get_objects_for_user(
            user, f'{Target._meta.app_label}.view_target'
        )
    except ContentType.DoesNotExist:
        return {'comment_list': []}

    return {
        'comment_list': Comment.objects.annotate(
            object_pk_as_int=Cast('object_pk', output_field=IntegerField())
        ).filter(
            object_pk_as_int__in=targets_for_user
        ).order_by('-submit_date')[:limit]
    }


@register.inclusion_tag("custom_code/partials/countdown.html", takes_context=True)
def countdown_IR1(context):
    request = context["request"]

    name = "IR1"
    flavortext = 'Assuming start on 31 October 2026'
    event_date = datetime.fromisoformat("2026-10-31T00:00:00")
    time_remaining = event_date - datetime.now()
    days = time_remaining.days
    hours = time_remaining.seconds // 3600
    minutes = (time_remaining.seconds % 3600) // 60
    seconds = time_remaining.seconds % 60

    data = {
        'name': name,
        'flavortext': flavortext,
        'days': days,
        'hours': hours,
        'minutes': minutes,
        'seconds': seconds
    }

    return {"data": data}

@register.inclusion_tag("custom_code/partials/countdown.html", takes_context=True)
def countdown_IR1_end(context):
    request = context["request"]

    name = "end of IR1"
    flavortext = 'Assuming end on 30 June 2027'
    event_date = datetime.fromisoformat("2027-06-30T00:00:00")
    time_remaining = event_date - datetime.now()
    days = time_remaining.days
    hours = time_remaining.seconds // 3600
    minutes = (time_remaining.seconds % 3600) // 60
    seconds = time_remaining.seconds % 60

    data = {
        'name': name,
        'flavortext': flavortext,
        'days': days,
        'hours': hours,
        'minutes': minutes,
        'seconds': seconds
    }

    return {"data": data}

@register.inclusion_tag("custom_code/partials/countdown.html", takes_context=True)
def countdown_O5(context):
    request = context["request"]

    name = "O5"
    flavortext = 'Assuming start on 1 January 2029'
    event_date = datetime.fromisoformat("2029-01-01T00:00:00")
    time_remaining = event_date - datetime.now()
    days = time_remaining.days
    hours = time_remaining.seconds // 3600
    minutes = (time_remaining.seconds % 3600) // 60
    seconds = time_remaining.seconds % 60

    data = {
        'name': name,
        'flavortext': flavortext,
        'days': days,
        'hours': hours,
        'minutes': minutes,
        'seconds': seconds
    }

    return {"data": data}