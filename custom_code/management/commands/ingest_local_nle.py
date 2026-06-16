"""
Ingest a single non-localized event from local JSON + skymap files.

This runs the exact same code path exercised by ``tests/test_nle_ingestion.py``
(``custom_code.nle_ingestion.upload_local_nle``), but against whatever database the
current Django settings point at. To target the remote ``datatrove-test`` database,
set the ``POSTGRES_*`` environment variables (or ``settings_local.py``) before running.

Example (dry run -- just shows which DB and parses the files, no writes):

    python manage.py ingest_local_nle \
        tests/data/GW170817-update.json \
        tests/data/GW170817-bayestar.fits.gz \
        --dry-run

Example (real ingestion into the configured DB):

    POSTGRES_HOST=datatrove.as.arizona.edu POSTGRES_DB=<test-db> \
    POSTGRES_USER=<you> POSTGRES_PASSWORD=<...> \
    python manage.py ingest_local_nle \
        tests/data/GW170817-update.json \
        tests/data/GW170817-bayestar.fits.gz
"""
import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from custom_code.nle_ingestion import (
    attach_skymap_to_alert,
    ensure_multiorder_skymap_bytes,
    ingest_local_igwn_alert,
    load_alert_dict,
    load_skymap_bytes,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Ingest one non-localized event from a local IGWN alert JSON and a companion "
        "skymap FITS file (same path as tests/test_nle_ingestion.py). Targets whatever "
        "database the POSTGRES_* settings/env vars point at."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "alert_json",
            help="Path to the IGWN/GraceDB alert JSON (without embedded skymap bytes).",
        )
        parser.add_argument(
            "skymap_fits",
            help="Path to the companion skymap FITS file (e.g. bayestar.fits.gz or "
            "*.multiorder.fits).",
        )
        parser.add_argument(
            "--no-convert-skymap",
            action="store_true",
            help="Do not convert a classic BAYESTAR skymap to multi-order; pass the "
            "skymap through as-is (use when the file is already multi-order).",
        )
        parser.add_argument(
            "--combined",
            action="store_true",
            help="Attach the skymap as external_coinc.combined_skymap instead of "
            "event.skymap.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse the files and report the target database without writing to it.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the interactive confirmation prompt before writing.",
        )

    def _describe_db(self):
        db = settings.DATABASES["default"]
        return (
            f"{db.get('ENGINE', '?')} "
            f"name={db.get('NAME', '?')} "
            f"host={db.get('HOST', '?')} "
            f"port={db.get('PORT', '?')} "
            f"user={db.get('USER', '?')}"
        )

    def handle(self, *args, **options):
        alert_json = options["alert_json"]
        skymap_fits = options["skymap_fits"]
        convert = not options["no_convert_skymap"]
        combined = options["combined"]
        dry_run = options["dry_run"]
        assume_yes = options["yes"]

        target_db = self._describe_db()
        self.stdout.write(self.style.WARNING(f"Target database: {target_db}"))

        # Parse inputs before touching the DB so file errors fail fast.
        try:
            alert = load_alert_dict(alert_json)
            skymap_bytes = load_skymap_bytes(skymap_fits)
        except Exception as exc:
            raise CommandError(f"Failed to read input files: {exc}") from exc

        superevent_id = alert.get("superevent_id", "<unknown>")
        alert_type = alert.get("alert_type", "<unknown>")
        self.stdout.write(
            f"Alert: superevent_id={superevent_id} alert_type={alert_type} "
            f"({len(skymap_bytes)} skymap bytes)"
        )

        if convert:
            try:
                skymap_bytes = ensure_multiorder_skymap_bytes(skymap_bytes)
            except Exception as exc:
                raise CommandError(f"Skymap conversion failed: {exc}") from exc
            self.stdout.write(
                f"Skymap normalized to multi-order ({len(skymap_bytes)} bytes)."
            )

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    "Dry run complete: inputs are valid and the database was not modified."
                )
            )
            return

        # Verify we can actually reach the DB before prompting / writing.
        try:
            connection.ensure_connection()
        except Exception as exc:
            raise CommandError(
                f"Could not connect to the database ({target_db}): {exc}"
            ) from exc

        if not assume_yes:
            answer = input(
                f"About to ingest {superevent_id} into:\n  {target_db}\nProceed? [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                self.stdout.write(self.style.NOTICE("Aborted; no changes made."))
                return

        alert = attach_skymap_to_alert(alert, skymap_bytes, combined=combined)
        nle, sequence = ingest_local_igwn_alert(alert)

        if nle is None:
            self.stdout.write(
                self.style.WARNING(
                    "Handler returned no NonLocalizedEvent (alert may have been skipped, "
                    "e.g. a test/mock event)."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Ingested NonLocalizedEvent event_id={nle.event_id} "
                f"(sequence={getattr(sequence, 'sequence_id', sequence)})."
            )
        )
