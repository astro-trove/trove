from .models import ScoreFactor


def update_score_factor(event_candidate, key, value):
    ScoreFactor.objects.update_or_create(
        event_candidate=event_candidate, key=key, defaults=dict(value=value)
    )


def delete_score_factor(event_candidate, key):
    """This is basically only used since we are updating various scores
    and may want to delete some, rather than update them, in the process"""
    # first get any score factors that match this event candidate and key
    matches = ScoreFactor.objects.filter(event_candidate=event_candidate, key=key)

    if matches.count():
        matches.delete()
