from django.db import models


class StrictTargetMatchManager(models.Manager):
    """
    Return Queryset for target with name matching string.
    """

    def check_for_fuzzy_match(self, name):
        """
        Returns a queryset exactly matching name that is received
        :param name: The string against which target names and aliases will be matched.
        :return: queryset containing matching Targets.
        """
        queryset = super().get_queryset().filter(name=name)
        return queryset
