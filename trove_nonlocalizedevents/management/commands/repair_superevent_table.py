"""
One-off repair for SQLite DBs where tom_nonlocalizedevents_superevent was never
created (e.g. migration 0002 was faked or state was corrupted). Creates the
missing table so migration 0009 (RenameModel to NonLocalizedEvent) can run.

Use only if you see: no such table: tom_nonlocalizedevents_superevent
Recommended: use PostgreSQL and a fresh DB instead.
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = (
        "Create missing tom_nonlocalizedevents_superevent table so "
        "tom_nonlocalizedevents.0009 can run (SQLite repair only)."
    )

    def handle(self, **options):
        vendor = connection.vendor
        table = "tom_nonlocalizedevents_superevent"

        if vendor != "sqlite":
            self.stdout.write(
                self.style.WARNING(
                    f"Database is {vendor!r}; this command is for SQLite only. "
                    "Run migrate normally."
                )
            )
            return

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=%s",
                [table],
            )
            if cursor.fetchone():
                self.stdout.write(self.style.WARNING(f"Table {table!r} already exists; nothing to do."))
                return

        # Schema after tom_nonlocalizedevents.0005 (before 0009 rename)
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

        self.stdout.write(self.style.SUCCESS(f"Created table {table!r}. You can now run: python manage.py migrate"))
