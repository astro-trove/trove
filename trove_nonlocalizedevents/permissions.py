# from guardian.shortcuts import get_objects_for_user
# from tom_nonlocalizedevents.models import NonLocalizedEvent

def nonlocalizedevents_for_user(user, qs):
    """
    Analogous to tom_targets.permissions.targets_for_user, but for 
    nonlocalized events.
    
    :param user: The user for whom to retrieve nonlocalized events.
    :type user: User

    :param qs: The queryset of nonlocalizedevents to filter.
    :type qs: QuerySet

    :returns: The filtered queryset of nonlocalizedevents.
    """

    ## TODO: Add further (more complicated) constraints on which NLEs the 
    ## user can see, like in tom_targets_permissions.targets_for_user?

    return qs




def candidates_for_user(user, qs):
    """
    The EventCandidates whose target this user is allowed to see.

    The candidate list and the skymap both draw the same candidates, so both
    have to hide the same ones -- see `targets_for_user` for the rules.
    """
    from tom_targets.permissions import targets_for_user

    from trove_targets.models import Target

    return qs.filter(
        target__in=targets_for_user(user, Target.objects.all(), "view_target")
    )
