from tom_targets.base_models import TargetMatchManager


class StrictTargetMatchManager(TargetMatchManager):
    """
    Custom Match Manager for extending the built-in TargetMatchManager.
    """

    def match_name(self, name):
        """
        Returns a queryset exactly matching name that is received
        :param name: The string against which target names will be matched.
        :return: queryset containing matching Target(s).
        """
        queryset = self.match_exact_name(name)
        return queryset
