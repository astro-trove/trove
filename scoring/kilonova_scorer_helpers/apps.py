from django.apps import AppConfig


class KilonovaScorerHelpersConfig(AppConfig):
    """The grid store as its own Django app.

    An app purely so these models carry their own migration history. Both are
    unmanaged, so that migration emits no SQL and is safe to apply in any
    environment; keeping it here leaves it untangled from `scoring`'s own
    migration state.
    """

    name = "scoring.kilonova_scorer_helpers"
    label = "kilonovascorer"
    verbose_name = "KilonovaSCORER grid store"
