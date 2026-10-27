"""
Ingest a single non-localized event from GraceDB or from local JSON + skymap files.

This runs the exact same code path exercised by ``tests/test_nle_ingestion.py``
(``custom_code.nle_ingestion``), but against whatever database the current Django
settings point at. To target the remote ``datatrove-test`` database, set the
``POSTGRES_*`` environment variables (or ``settings_local.py``) before running.

Three data sources are supported:

  * ``--gracedb-all``: enumerate superevents from GraceDB and ingest each one. By default
    only *significant* production events are ingested (query ``category: Production label:
    GCN_PRELIM_SENT``, which spans O3 and O4); pass ``--include-low-significance`` for all
    production events, or ``--gracedb-query`` to supply a custom search. Events already in
    the target database are skipped.
  * ``--gracedb <SUPEREVENT_ID>``: fetch the latest VOEvent XML and its GW_SKYMAP from
    https://gracedb.ligo.org, reformat them into an IGWN alert packet, and ingest it.
  * positional ``<alert.json> <skymap.fits>``: read an IGWN alert JSON and companion
    skymap FITS from disk.

Before writing, the command checks whether a NonLocalizedEvent with the alert's
``superevent_id`` already exists in the target database and reports it. Pass
``--skip-existing`` to abort instead of adding another sequence to an existing event
(in ``--gracedb-all`` mode existing events are always skipped).

Example (ingest all significant GraceDB events, preview first):

    python manage.py ingest_local_nle --gracedb-all --dry-run
    python manage.py ingest_local_nle --gracedb-all --yes

Example (single GraceDB event, dry run):

    python manage.py ingest_local_nle --gracedb S190814bv --dry-run

Example (local files, dry run):

    python manage.py ingest_local_nle \
        tests/data/GW170817-update.json \
        tests/data/GW170817-bayestar.fits.gz \
        --dry-run

Example (real ingestion from GraceDB into the configured DB):

    POSTGRES_HOST=datatrove.as.arizona.edu POSTGRES_DB=<test-db> \
    POSTGRES_USER=<you> POSTGRES_PASSWORD=<...> \
    python manage.py ingest_local_nle --gracedb S190814bv
"""
import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from tom_nonlocalizedevents.models import NonLocalizedEvent

from custom_code.nle_ingestion import (
    GRACEDB_BASE_URL,
    GRACEDB_PRODUCTION_QUERY,
    GRACEDB_SIGNIFICANT_QUERY,
    attach_skymap_to_alert,
    ensure_multiorder_skymap_bytes,
    fetch_gracedb_alert,
    ingest_local_igwn_alert,
    iter_gracedb_superevents,
    load_alert_dict,
    load_skymap_bytes,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Ingest one non-localized event from GraceDB (--gracedb SUPEREVENT_ID) or from a "
        "local IGWN alert JSON + companion skymap FITS file. Targets whatever database the "
        "POSTGRES_* settings/env vars point at."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "alert_json",
            nargs="?",
            default=None,
            help="Path to the IGWN/GraceDB alert JSON (without embedded skymap bytes). "
            "Omit when using --gracedb.",
        )
        parser.add_argument(
            "skymap_fits",
            nargs="?",
            default=None,
            help="Path to the companion skymap FITS file (e.g. bayestar.fits.gz or "
            "*.multiorder.fits). Omit when using --gracedb.",
        )
        parser.add_argument(
            "--gracedb",
            metavar="SUPEREVENT_ID",
            default=None,
            help="Fetch the latest VOEvent + GW_SKYMAP for this GraceDB superevent "
            "(e.g. S190814bv) instead of reading local files.",
        )
        parser.add_argument(
            "--gracedb-all",
            action="store_true",
            help="Enumerate superevents from GraceDB and ingest each. By default only "
            "significant production events are ingested; events already present in the "
            "target database are skipped.",
        )
        parser.add_argument(
            "--include-low-significance",
            action="store_true",
            help="With --gracedb-all, also ingest low-significance production events "
            "(default: significant events only).",
        )
        parser.add_argument(
            "--gracedb-query",
            default=None,
            help="With --gracedb-all, override the GraceDB search query "
            f"(default significant: {GRACEDB_SIGNIFICANT_QUERY!r}).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="With --gracedb-all, ingest at most N superevents (useful for testing).",
        )
        parser.add_argument(
            "--gracedb-url",
            default=GRACEDB_BASE_URL,
            help=f"Base URL of the GraceDB server (default: {GRACEDB_BASE_URL}).",
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
            "--skip-existing",
            action="store_true",
            help="Abort without writing if a NonLocalizedEvent with this superevent_id "
            "already exists in the target database.",
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

    def _find_existing_nle(self, superevent_id):
        """Return the existing NonLocalizedEvent for this superevent_id, or None."""
        if not superevent_id or superevent_id == "<unknown>":
            return None
        return NonLocalizedEvent.objects.filter(event_id=superevent_id).first()

    def _ingest_gracedb_all(self, options):
        """Enumerate superevents from GraceDB and ingest each, skipping existing events."""
        gracedb_url = options["gracedb_url"]
        convert = not options["no_convert_skymap"]
        dry_run = options["dry_run"]
        assume_yes = options["yes"]
        limit = options["limit"]
        if options["gracedb_query"]:
            query = options["gracedb_query"]
        elif options["include_low_significance"]:
            query = GRACEDB_PRODUCTION_QUERY
        else:
            query = GRACEDB_SIGNIFICANT_QUERY

        target_db = self._describe_db()
        self.stdout.write(self.style.WARNING(f"Target database: {target_db}"))
        self.stdout.write(
            f"GraceDB bulk mode ({gracedb_url}): query={query!r} "
            f"limit={limit if limit is not None else 'none'}"
        )

        # A real run must be able to reach the DB (to skip existing events and write new
        # ones); a dry run tolerates an unreachable DB but then cannot skip duplicates.
        db_reachable = True
        try:
            connection.ensure_connection()
        except Exception as exc:
            db_reachable = False
            if not dry_run:
                raise CommandError(
                    f"Could not connect to the database ({target_db}): {exc}"
                ) from exc
            self.stdout.write(
                self.style.WARNING(
                    f"Could not connect to the database; existing events will not be "
                    f"skipped in this dry run: {exc}"
                )
            )

        try:
            superevents = list(
                iter_gracedb_superevents(query, base_url=gracedb_url, limit=limit)
            )
        except Exception as exc:
            raise CommandError(f"Failed to list superevents from GraceDB: {exc}") from exc

        self.stdout.write(f"GraceDB returned {len(superevents)} candidate superevent(s).")
        if not superevents:
            return

        if not dry_run and not assume_yes:
            answer = input(
                f"About to ingest up to {len(superevents)} superevent(s) into:\n"
                f"  {target_db}\n(existing events are skipped). Proceed? [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                self.stdout.write(self.style.NOTICE("Aborted; no changes made."))
                return

        ingested = skipped_existing = skipped_handler = failed = would_ingest = 0
        for superevent in superevents:
            superevent_id = superevent.get("superevent_id")
            if not superevent_id:
                continue

            if db_reachable:
                try:
                    if self._find_existing_nle(superevent_id) is not None:
                        skipped_existing += 1
                        self.stdout.write(f"  {superevent_id}: already in DB; skipping.")
                        continue
                except Exception as exc:
                    raise CommandError(
                        f"Failed to query for existing NonLocalizedEvent "
                        f"'{superevent_id}' (is the target database migrated?): {exc}"
                    ) from exc

            if dry_run:
                would_ingest += 1
                self.stdout.write(f"  {superevent_id}: would ingest.")
                continue

            try:
                alert, skymap_bytes, voevent_name = fetch_gracedb_alert(
                    superevent_id, base_url=gracedb_url
                )
                if convert and skymap_bytes is not None:
                    skymap_bytes = ensure_multiorder_skymap_bytes(skymap_bytes)
                if skymap_bytes is not None:
                    alert = attach_skymap_to_alert(alert, skymap_bytes)
                nle, sequence = ingest_local_igwn_alert(alert)
            except Exception as exc:
                failed += 1
                self.stderr.write(
                    self.style.ERROR(f"  {superevent_id}: FAILED: {exc}")
                )
                logger.exception("Failed to ingest %s from GraceDB", superevent_id)
                continue

            if nle is None:
                skipped_handler += 1
                self.stdout.write(
                    f"  {superevent_id}: skipped by handler (test/mock event)."
                )
            else:
                ingested += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  {superevent_id}: ingested (from {voevent_name}, "
                        f"sequence={getattr(sequence, 'sequence_id', sequence)})."
                    )
                )

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Dry run complete: {would_ingest} would be ingested, "
                    f"{skipped_existing} already in DB; database not modified."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done: {ingested} ingested, {skipped_existing} already existed, "
                    f"{skipped_handler} skipped by handler, {failed} failed."
                )
            )

    def handle(self, *args, **options):
        alert_json = options["alert_json"]
        skymap_fits = options["skymap_fits"]
        gracedb_id = options["gracedb"]
        gracedb_all = options["gracedb_all"]
        gracedb_url = options["gracedb_url"]
        convert = not options["no_convert_skymap"]
        combined = options["combined"]
        dry_run = options["dry_run"]
        skip_existing = options["skip_existing"]
        assume_yes = options["yes"]

        # --gracedb-all is a distinct, non-interactive bulk path.
        if gracedb_all:
            if gracedb_id or alert_json or skymap_fits:
                raise CommandError(
                    "--gracedb-all cannot be combined with --gracedb or local "
                    "alert_json/skymap_fits positionals."
                )
            return self._ingest_gracedb_all(options)

        # Exactly one source: GraceDB or a local JSON+FITS pair.
        if gracedb_id:
            if alert_json or skymap_fits:
                raise CommandError(
                    "Provide either --gracedb SUPEREVENT_ID or local alert_json/skymap_fits "
                    "positionals, not both."
                )
        elif not (alert_json and skymap_fits):
            raise CommandError(
                "Provide a source: --gracedb SUPEREVENT_ID, or both the alert_json and "
                "skymap_fits positional arguments."
            )

        target_db = self._describe_db()
        self.stdout.write(self.style.WARNING(f"Target database: {target_db}"))

        # Build the alert (+ raw skymap bytes) from the chosen source before touching the
        # DB so source/parse errors fail fast.
        if gracedb_id:
            self.stdout.write(f"Fetching {gracedb_id} from GraceDB ({gracedb_url}) ...")
            try:
                alert, skymap_bytes, voevent_name = fetch_gracedb_alert(
                    gracedb_id, base_url=gracedb_url
                )
            except Exception as exc:
                raise CommandError(f"Failed to fetch {gracedb_id} from GraceDB: {exc}") from exc
            self.stdout.write(f"Using GraceDB VOEvent: {voevent_name}")
        else:
            try:
                alert = load_alert_dict(alert_json)
                skymap_bytes = load_skymap_bytes(skymap_fits)
            except Exception as exc:
                raise CommandError(f"Failed to read input files: {exc}") from exc

        superevent_id = alert.get("superevent_id", "<unknown>")
        alert_type = alert.get("alert_type", "<unknown>")
        skymap_desc = f"{len(skymap_bytes)} skymap bytes" if skymap_bytes else "no skymap"
        self.stdout.write(
            f"Alert: superevent_id={superevent_id} alert_type={alert_type} ({skymap_desc})"
        )

        if convert and skymap_bytes is not None:
            try:
                skymap_bytes = ensure_multiorder_skymap_bytes(skymap_bytes)
            except Exception as exc:
                raise CommandError(f"Skymap conversion failed: {exc}") from exc
            self.stdout.write(
                f"Skymap normalized to multi-order ({len(skymap_bytes)} bytes)."
            )

        # Connect to the DB so we can check whether this event already exists.
        # A read-only existence check is safe even for --dry-run; if the DB is
        # unreachable during a dry run we just skip the check rather than fail.
        db_reachable = True
        try:
            connection.ensure_connection()
        except Exception as exc:
            db_reachable = False
            if not dry_run:
                raise CommandError(
                    f"Could not connect to the database ({target_db}): {exc}"
                ) from exc
            self.stdout.write(
                self.style.WARNING(
                    f"Could not connect to the database to check for an existing event: {exc}"
                )
            )

        existing = None
        existence_checked = False
        if db_reachable:
            try:
                existing = self._find_existing_nle(superevent_id)
                existence_checked = True
            except Exception as exc:
                if not dry_run:
                    raise CommandError(
                        f"Failed to query for existing NonLocalizedEvent '{superevent_id}' "
                        f"(is the target database migrated?): {exc}"
                    ) from exc
                self.stdout.write(
                    self.style.WARNING(
                        f"Could not check for an existing event: {exc}"
                    )
                )

        if existing is not None:
            self.stdout.write(
                self.style.WARNING(
                    f"NonLocalizedEvent '{superevent_id}' already exists "
                    f"(id={existing.id}, {existing.sequences.count()} sequence(s)). "
                    "Ingesting will add a new sequence/localization to it."
                )
            )
            if skip_existing:
                self.stdout.write(
                    self.style.NOTICE(
                        "--skip-existing set; aborting without changes."
                    )
                )
                return
        elif existence_checked and superevent_id not in (None, "", "<unknown>"):
            self.stdout.write(
                f"No existing NonLocalizedEvent '{superevent_id}'; a new one will be created."
            )

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    "Dry run complete: inputs are valid and the database was not modified."
                )
            )
            return

        if not assume_yes:
            action = "update existing" if existing is not None else "create new"
            answer = input(
                f"About to {action} NonLocalizedEvent {superevent_id} in:\n  {target_db}\n"
                "Proceed? [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                self.stdout.write(self.style.NOTICE("Aborted; no changes made."))
                return

        if skymap_bytes is not None:
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
