"""
Run repairs for known SQLite migration issues, then migrate. Use this when
"python manage.py migrate" fails due to out-of-sync schema (e.g. missing
superevent table, "table already exists", or "no such column"). For PostgreSQL,
just run "python manage.py migrate" (and the 0025 fake on first run per README).
"""
import re
import subprocess
import sys

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection


def table_exists(cursor, name):
    if connection.vendor == "sqlite":
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=%s",
            [name],
        )
    else:
        cursor.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = %s LIMIT 1",
            [name],
        )
    return cursor.fetchone() is not None


class Command(BaseCommand):
    help = "Apply SQLite migration repairs (missing/duplicate tables) then run migrate."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only run repairs, do not run migrate.",
        )
        parser.add_argument(
            "--max-fake-retries",
            type=int,
            default=25,
            metavar="N",
            help="Max number of migrate retries after faking a failed migration (default 25).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        max_retries = options["max_fake_retries"]
        vendor = connection.vendor

        if vendor != "sqlite":
            self.stdout.write(
                self.style.WARNING("Database is not SQLite; running migrate only.")
            )
            if not dry_run:
                call_command("migrate", *args, **options)
            return

        with connection.cursor() as cursor:
            has_superevent = table_exists(cursor, "tom_nonlocalizedevents_superevent")
            has_nonlocalized = table_exists(
                cursor, "tom_nonlocalizedevents_nonlocalizedevent"
            )

        # Repair 1: missing superevent table (0009 will try to rename it)
        if not has_superevent and not has_nonlocalized:
            self.stdout.write("Creating missing tom_nonlocalizedevents_superevent table...")
            sql = """
            CREATE TABLE IF NOT EXISTS tom_nonlocalizedevents_superevent (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                superevent_id VARCHAR(64) NOT NULL,
                superevent_url VARCHAR(200) NOT NULL,
                created DATETIME NOT NULL,
                modified DATETIME NOT NULL,
                superevent_type VARCHAR(3) NOT NULL DEFAULT 'GW'
            );
            """
            with connection.cursor() as cursor:
                cursor.execute(sql)
            self.stdout.write(self.style.SUCCESS("Created tom_nonlocalizedevents_superevent."))

        # Repair 2: nonlocalizedevent already exists (0009 rename would fail)
        # Only fake if 0009 is not already applied (otherwise we'd unapply later migrations)
        from django.db.migrations.recorder import MigrationRecorder
        recorder = MigrationRecorder(connection)
        applied = recorder.applied_migrations() if recorder.has_table() else {}
        if has_nonlocalized and ("tom_nonlocalizedevents", "0009_rename_superevent_nonlocalizedevent") not in applied:
            self.stdout.write(
                "Table tom_nonlocalizedevents_nonlocalizedevent already exists; "
                "faking migration 0009..."
            )
            call_command("migrate", "tom_nonlocalizedevents", "0009", "--fake")
            self.stdout.write(self.style.SUCCESS("Faked tom_nonlocalizedevents.0009."))
            applied = recorder.applied_migrations() if recorder.has_table() else {}
        if has_nonlocalized and ("tom_nonlocalizedevents", "0010_rename_more_superevent_fields") not in applied:
            with connection.cursor() as c:
                if connection.vendor == "sqlite":
                    c.execute("PRAGMA table_info(tom_nonlocalizedevents_nonlocalizedevent)")
                    cols = [row[1] for row in c.fetchall()]
                    if "event_id" in cols and "superevent_id" not in cols:
                        call_command("migrate", "tom_nonlocalizedevents", "0010", "--fake")
                        self.stdout.write(self.style.SUCCESS("Faked tom_nonlocalizedevents.0010."))

        if dry_run:
            self.stdout.write("Dry run: skipping migrate.")
            return

        # Retry loop: run migrate; on OperationalError (already exists / no such column), fake that migration and retry
        pattern = re.compile(r"Applying ([a-z_]+)\.(\d+_[a-z0-9_]+)\.\.\.")
        for attempt in range(max_retries):
            self.stdout.write("Running migrate...")
            proc = subprocess.run(
                [sys.executable, "manage.py", "migrate"] + list(args),
                capture_output=True,
                text=True,
                timeout=300,
                cwd=self._project_root(),
            )
            out = proc.stdout + "\n" + proc.stderr
            if proc.returncode == 0:
                self.stdout.write(proc.stdout)
                return
            if "OperationalError" not in out and "sqlite3.OperationalError" not in out:
                self.stdout.write(proc.stdout)
                if proc.stderr:
                    self.stdout.write(self.style.ERROR(proc.stderr))
                raise SystemExit(proc.returncode)
            # Find the migration that was being applied when it failed
            applied_lines = [m for m in pattern.finditer(out)]
            if not applied_lines:
                self.stdout.write(proc.stdout)
                if proc.stderr:
                    self.stdout.write(self.style.ERROR(proc.stderr))
                raise SystemExit(proc.returncode)
            last_applying = applied_lines[-1]
            app_label, migration_name = last_applying.group(1), last_applying.group(2)
            self.stdout.write(
                self.style.WARNING(
                    f"Faking {app_label}.{migration_name} (schema already in sync) and retrying..."
                )
            )
            call_command("migrate", app_label, migration_name, "--fake")
        self.stdout.write(self.style.ERROR(f"Still failing after {max_retries} retries."))
        self.stdout.write(proc.stdout)
        if proc.stderr:
            self.stdout.write(self.style.ERROR(proc.stderr))
        raise SystemExit(proc.returncode)

    def _project_root(self):
        from django.conf import settings
        return str(settings.BASE_DIR)
