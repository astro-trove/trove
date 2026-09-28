from django.core.management.base import BaseCommand

from tom_nonlocalizedevents.models import EventCandidate, NonLocalizedEvent

from scoring.vet_bbh import vet_bbh

EVENT_NAME = "S250208ad"


class Command(BaseCommand):
    help = "Smoke-test vet_bbh against real EventCandidates for a BBH event."

    def add_arguments(self, parser):
        parser.add_argument("target_id", nargs="?", type=int, default=None)
        parser.add_argument("--event", default=EVENT_NAME)
        parser.add_argument(
            "--pdb", action="store_true", help="drop into pdb after running"
        )

    def handle(self, target_id, event, **options):
        nle = NonLocalizedEvent.objects.get(event_id=event)

        if target_id is not None:
            target_ids = [target_id]
        else:
            target_ids = list(
                EventCandidate.objects.filter(nonlocalizedevent=nle).values_list(
                    "target_id", flat=True
                )
            )
            self.stdout.write(f"Running vet_bbh on {len(target_ids)} candidates for {event}")

        for tid in target_ids:
            try:
                vet_bbh(tid, event)
                self.stdout.write(self.style.SUCCESS(f"target_id={tid}: OK"))
            except Exception as exc:  # noqa: BLE001
                self.stdout.write(self.style.ERROR(f"target_id={tid}: FAILED -- {exc}"))

        if options["pdb"]:
            import pdb

            pdb.set_trace()
