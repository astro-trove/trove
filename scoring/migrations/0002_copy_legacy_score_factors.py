from django.db import migrations

LEGACY_TABLE = "candidate_vetting_scorefactor"


def copy_legacy_score_factors(apps, schema_editor):
    """Copy vetting results from the legacy candidate_vetting_scorefactor table.

    The ScoreFactor model moved from the candidate_vetting app to the scoring
    app, and 0001_initial created a fresh, empty scoring_scorefactor table.
    Existing deployments still hold their historical vetting results in the
    legacy table; without this copy those candidates all get the empty-product
    default score of 1.0.
    """
    connection = schema_editor.connection
    if LEGACY_TABLE not in connection.introspection.table_names():
        return  # fresh install; nothing to copy

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO scoring_scorefactor (event_candidate_id, key, value)
            SELECT legacy.event_candidate_id, legacy.key, legacy.value
            FROM {LEGACY_TABLE} legacy
            JOIN tom_nonlocalizedevents_eventcandidate ec
                ON ec.id = legacy.event_candidate_id
            ON CONFLICT (event_candidate_id, key) DO NOTHING
            """
        )


class Migration(migrations.Migration):

    dependencies = [
        ("scoring", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(copy_legacy_score_factors, migrations.RunPython.noop),
    ]
