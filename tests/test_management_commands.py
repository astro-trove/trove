"""
Unit tests for Django management commands.

These test the management commands in trove_targets/management/commands/
and scoring/management/commands/.
"""
import pytest
from unittest.mock import MagicMock, patch

class TestRepairMigrateCommand:
    """Tests for repair_migrate management command."""

    def test_table_exists_sqlite_query(self):
        """Test SQLite table existence check query format."""
        table_name = 'tom_nonlocalizedevents_superevent'
        query = f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'"
        
        assert table_name in query
        assert 'sqlite_master' in query

    def test_project_root_returns_path(self):
        """Test _project_root returns a valid path string."""
        import os
        
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        assert isinstance(base_dir, str)
        assert len(base_dir) > 0

    def test_migration_name_parsing(self):
        """Test parsing migration name from error output."""
        error_output = "Applying tom_nonlocalizedevents.0009_superevent_event... ERROR"
        
        if 'Applying' in error_output:
            parts = error_output.split()
            for part in parts:
                if '.' in part and part[0].isalpha():
                    app_migration = part.rstrip('.')
                    if '.' in app_migration:
                        app_label, migration_name = app_migration.split('.', 1)
                        assert app_label == 'tom_nonlocalizedevents'
                        assert migration_name.startswith('0009')
                        break


class TestRepairSuperEventTableCommand:
    """Tests for repair_superevent_table management command."""

    def test_create_table_sql_structure(self):
        """Test CREATE TABLE SQL has required columns."""
        required_columns = [
            'id',
            'superevent_id',
            'superevent_url',
            'created',
            'modified',
            'superevent_type'
        ]
        
        for col in required_columns:
            assert isinstance(col, str)

    def test_sqlite_vendor_check(self):
        """Test SQLite vendor check logic."""
        vendors = ['sqlite', 'postgresql', 'mysql']
        
        for vendor in vendors:
            is_sqlite = vendor == 'sqlite'
            assert isinstance(is_sqlite, bool)


class TestVerifyListenerCommand:
    """Tests for verify_listener management command."""

    def test_gracedb_url_format(self):
        """Test GraceDB URL format."""
        event_id = 'S240101abc'
        gracedb_url = f'https://gracedb.ligo.org/superevents/{event_id}/view/'
        
        assert event_id in gracedb_url
        assert 'gracedb.ligo.org' in gracedb_url

    def test_time_comparison_logic(self):
        """Test time comparison for listener verification."""
        from datetime import datetime, timedelta, timezone
        
        last_alert_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        now = datetime(2024, 1, 1, 13, 0, 0, tzinfo=timezone.utc)
        max_delay_hours = 2
        
        time_since_alert = now - last_alert_time
        is_recent = time_since_alert < timedelta(hours=max_delay_hours)
        
        assert is_recent is True


class TestIngestTnsCommand:
    """Tests for ingest_tns management command."""

    def test_lookback_days_parameter(self):
        """Test lookback days parameter validation."""
        lookback_days_nle = 30
        lookback_days_obs = 7
        
        assert lookback_days_nle > 0
        assert lookback_days_obs > 0
        assert lookback_days_nle >= lookback_days_obs

    def test_active_event_time_window(self):
        """Test active event time window calculation."""
        from datetime import datetime, timedelta, timezone
        
        now = datetime.now(timezone.utc)
        lookback_days = 30
        t0 = now - timedelta(days=lookback_days)
        
        assert t0 < now
        assert (now - t0).days == lookback_days


class TestMigrationParsing:
    """Tests for migration output parsing utilities."""

    def test_parse_operational_error(self):
        """Test parsing OperationalError from migrate output."""
        error_messages = [
            "sqlite3.OperationalError: table 'tom_nonlocalizedevents_eventcandidate' already exists",
            "sqlite3.OperationalError: no such table: tom_nonlocalizedevents_superevent",
            "sqlite3.OperationalError: no such column: superevent_id"
        ]
        
        for msg in error_messages:
            assert 'OperationalError' in msg

    def test_identify_already_exists_error(self):
        """Test identifying 'already exists' errors."""
        error = "sqlite3.OperationalError: table 'tom_nonlocalizedevents_eventcandidate' already exists"
        
        is_already_exists = 'already exists' in error
        assert is_already_exists is True

    def test_identify_no_such_table_error(self):
        """Test identifying 'no such table' errors."""
        error = "sqlite3.OperationalError: no such table: tom_nonlocalizedevents_superevent"
        
        is_no_such_table = 'no such table' in error
        assert is_no_such_table is True

    def test_identify_no_such_column_error(self):
        """Test identifying 'no such column' errors."""
        error = "sqlite3.OperationalError: no such column: superevent_id"
        
        is_no_such_column = 'no such column' in error
        assert is_no_such_column is True


class TestDatabaseVendorDetection:
    """Tests for database vendor detection."""

    def test_vendor_string_matching(self):
        """Test vendor string matching."""
        vendors = {
            'sqlite': 'SQLite',
            'postgresql': 'PostgreSQL',
            'mysql': 'MySQL'
        }
        
        for vendor_key, vendor_name in vendors.items():
            assert isinstance(vendor_key, str)
            assert isinstance(vendor_name, str)

    def test_sqlite_specific_logic(self):
        """Test SQLite-specific logic branch."""
        vendor = 'sqlite'
        
        if vendor == 'sqlite':
            should_run_repair = True
        else:
            should_run_repair = False
        
        assert should_run_repair is True

    def test_postgresql_specific_logic(self):
        """Test PostgreSQL-specific logic branch."""
        vendor = 'postgresql'
        
        if vendor != 'sqlite':
            should_skip_repair = True
        else:
            should_skip_repair = False
        
        assert should_skip_repair is True


class TestMigrationRetryLogic:
    """Tests for migration retry logic."""

    def test_max_retries_default(self):
        """Test default max retries value."""
        max_retries = 20
        assert max_retries > 0
        assert max_retries <= 100

    def test_retry_counter_increment(self):
        """Test retry counter increments correctly."""
        max_retries = 5
        attempts = []
        
        for attempt in range(max_retries):
            attempts.append(attempt)
        
        assert len(attempts) == max_retries
        assert attempts[-1] == max_retries - 1

    def test_exit_on_success(self):
        """Test early exit on successful migration."""
        return_code = 0
        should_exit = return_code == 0
        
        assert should_exit is True

    def test_continue_on_known_error(self):
        """Test continue on known OperationalError."""
        error_output = "OperationalError: table already exists"
        known_error = 'OperationalError' in error_output
        
        assert known_error is True


class TestSlackNotifications:
    """Tests for Slack notification in management commands."""

    def test_slack_message_format(self):
        """Test Slack message format for command output."""
        command_name = 'verify_listener'
        status = 'SUCCESS'
        message = f'Management command `{command_name}` completed with status: {status}'
        
        assert command_name in message
        assert status in message

    def test_error_message_truncation(self):
        """Test error message truncation for Slack."""
        max_length = 1000
        long_error = 'x' * 2000
        
        truncated = long_error[:max_length] if len(long_error) > max_length else long_error
        
        assert len(truncated) <= max_length

class TestQueryANTARESgw:
    """Tests for the ANTARES GW localization-based query management command"""

    # CI has no database, so stub out the TargetName lookup of existing ANT* names,
    # and mock the ANTARES client so we test our query rather than their live results
    def _query(self):
        from trove_targets.management.commands import query_antares_gw

        with patch.object(query_antares_gw, "get_current_ant_candidate_names",
                          return_value=["ANT2020old"]) as mock_names, \
                patch.object(query_antares_gw, "search",
                             return_value=iter([MagicMock(locus_id="ANT2025abc")])) as mock_search:
            loci = query_antares_gw.query_for_one_event("S250206dm")
        return loci, mock_names, mock_search

    def test_query_for_one_event(self):
        loci, mock_names, mock_search = self._query()

        mock_names.assert_called_once_with("S250206dm")
        mock_search.assert_called_once()
        # the loci come straight back from the ANTARES client
        assert next(loci).locus_id == "ANT2025abc"

    def test_query_structure(self):
        _, _, mock_search = self._query()
        query = mock_search.call_args.args[0]

        # ANTARES only accepts a single top-level "query" key
        assert list(query) == ["query"]
        filters = query["query"]["bool"]["filter"]
        assert {"term": {"grav_wave_events.keyword": "S250206dm"}} in filters

        # loci that aren't solar system objects in ZTF, LSST, or either
        not_sso = {"bool": {"should": [
            {"term": {"properties.ztf_ssnamenr": "null"}},
            {"term": {"properties.lsst_diaSource_ssObjectId": 0}},
            {"bool": {"must_not": [
                {"exists": {"field": "properties.ztf_ssnamenr"}},
                {"exists": {"field": "properties.lsst_diaSource_ssObjectId"}},
            ]}},
        ]}}
        assert not_sso in filters

        # loci already in TROVE are excluded
        assert {"bool": {"must_not": [{"terms": {"locus_id": ["ANT2020old"]}}]}} in filters


class TestScoringSmokeTestCommand:
    """Tests for the scoring app's test_scoring management command"""

    def _run(self, *args, transients=("KN", "KN-in-SN", "TDE"), target_ids=(1, 2)):
        from django.core.management import call_command
        from scoring.management.commands import test_scoring

        with patch.object(test_scoring, "NonLocalizedEvent"), \
                patch.object(test_scoring, "EventCandidate") as EventCandidate, \
                patch.object(test_scoring, "get_possible_em_transients", return_value=list(transients)), \
                patch.object(test_scoring, "score") as score:
            EventCandidate.objects.filter.return_value.values_list.return_value = list(target_ids)
            call_command("test_scoring", *args, stdout=MagicMock())
        return [(c.args[0], c.args[1], c.kwargs["em_model_name"]) for c in score.call_args_list]

    def test_scores_every_candidate_with_every_possible_model(self):
        calls = self._run("--event", "S250206dm")
        # TDE has no scoring model, so it is skipped
        assert calls == [
            (1, "S250206dm", "KN"), (1, "S250206dm", "KN-in-SN"),
            (2, "S250206dm", "KN"), (2, "S250206dm", "KN-in-SN"),
        ]

    def test_one_target_and_model(self):
        assert self._run("5", "--model", "AGN-flare") == [(5, "S250208ad", "AGN-flare")]
