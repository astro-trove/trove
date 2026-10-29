from django.core.management.base import BaseCommand

from tom_nonlocalizedevents.models import EventCandidate, NonLocalizedEvent

from scoring.em_models.util import MODELS_BY_NAME
from scoring.unified_scorer import score
from scoring.util import get_possible_em_transients

EVENT_NAME = "S250208ad"


class Command(BaseCommand):
    help = "Smoke-test the unified scorer against real EventCandidates for an event."

    def add_arguments(self, parser):
        parser.add_argument("target_id", nargs="?", type=int, default=None)
        parser.add_argument("--event", default=EVENT_NAME)
        parser.add_argument(
            "--model",
            choices=sorted(MODELS_BY_NAME),
            nargs="+",
            default=None,
            help="EM model(s) to score with. Default is every model possible for the event.",
        )
        parser.add_argument(
            "--pdb", action="store_true", help="drop into pdb after running"
        )

    def handle(self, target_id, event, model, **options):
        nle = NonLocalizedEvent.objects.get(event_id=event)

        if model is None:
            model = [m for m in get_possible_em_transients(event) if m in MODELS_BY_NAME]

        if target_id is not None:
            target_ids = [target_id]
        else:
            target_ids = list(
                EventCandidate.objects.filter(nonlocalizedevent=nle).values_list(
                    "target_id", flat=True
                )
            )
            self.stdout.write(
                f"Scoring {len(target_ids)} candidates for {event} with {', '.join(model)}"
            )

        for tid in target_ids:
            for em_model in model:
                try:
                    score(tid, event, em_model_name=em_model)
                    self.stdout.write(self.style.SUCCESS(f"target_id={tid} ({em_model}): OK"))
                except Exception as exc:  # noqa: BLE001
                    self.stdout.write(
                        self.style.ERROR(f"target_id={tid} ({em_model}): FAILED -- {exc}")
                    )

        if options["pdb"]:
            import pdb

            pdb.set_trace()
