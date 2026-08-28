"""
Invalidate the saved host galaxy associations so the next vetting run redoes
the galaxy catalog cone searches.

vet_basic notices an ingest on its own, from each catalog's write counters in
pg_stat_all_tables. What it cannot notice is those counters being lost -- a
pg_stat_reset(), a crash, a restore into a fresh cluster -- which returns them
to where they started and can make a stale cache look current. Run this after
any of those, and after any ingest that bypassed the statistics collector:

    python manage.py invalidate_host_galaxies

It drops the fingerprints, not the "Host Galaxies" rows, so target pages keep
rendering hosts until each target is next vetted.
"""

import logging

from django.core.management.base import BaseCommand

from tom_targets.models import TargetExtra

from scoring.vet_basic import HOST_GALAXY_CACHE_KEY, invalidate_host_galaxy_cache

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Invalidate saved host galaxy associations so the next vetting run "
        "re-queries the galaxy catalogs. Run this after re-ingesting a catalog."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--target-id",
            type=int,
            nargs="+",
            help="Only invalidate these targets. Default is every target.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many would be invalidated without deleting them.",
        )

    def handle(self, *args, **options):
        target_ids = options["target_id"]

        if options["dry_run"]:
            fingerprints = TargetExtra.objects.filter(key=HOST_GALAXY_CACHE_KEY)
            if target_ids:
                fingerprints = fingerprints.filter(target_id__in=target_ids)
            count = fingerprints.count()
            self.stdout.write(
                f"Would invalidate {count} saved host galaxy association(s)."
                if count
                else "No saved host galaxy associations to invalidate."
            )
            return

        count = invalidate_host_galaxy_cache(target_ids=target_ids)
        if not count:
            self.stdout.write("No saved host galaxy associations to invalidate.")
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Invalidated {count} saved host galaxy association(s). The next "
                + "vetting run will re-query the galaxy catalogs for them."
            )
        )
