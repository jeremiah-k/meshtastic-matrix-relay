"""Tests for automatic rollback triggering in perform_migration function."""

from pathlib import Path
from unittest.mock import patch

import pytest

from mmrelay.migrate import MigrationError, perform_migration


class TestMigrationAutomaticRollback:
    """Tests for automatic rollback functionality when migration fails."""

    def test_perform_migration_triggers_rollback_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify rollback is called when migration step fails."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        # Create credentials file to ensure first step succeeds
        (legacy_root / "credentials.json").write_text('{"token": "test"}')
        new_home = tmp_path / "home"

        # Mock path resolution
        monkeypatch.setenv("MMRELAY_HOME", str(new_home))
        monkeypatch.setattr(
            "mmrelay.migrate.resolve_all_paths",
            lambda: {
                "home": str(new_home),
                "legacy_sources": [str(legacy_root)],
                "credentials_path": str(new_home / "matrix" / "credentials.json"),
                "database_dir": str(new_home / "database"),
                "logs_dir": str(new_home / "logs"),
                "plugins_dir": str(new_home / "plugins"),
                "store_dir": str(new_home / "matrix" / "store"),
            },
        )

        # Mock rollback_migration to verify it gets called
        with patch("mmrelay.migrate.rollback_migration") as mock_rollback:
            mock_rollback.return_value = {
                "success": True,
                "timestamp": "2023-01-01T00:00:00",
                "rolled_back_steps": ["credentials"],
                "errors": [],
            }

            # Mock config migration to fail
            with patch("mmrelay.migrate.migrate_config") as mock_config:
                mock_config.side_effect = MigrationError.step_failed(
                    "config", "Config migration failed"
                )

                result = perform_migration(force=True)

        # Verify migration failed
        assert result["success"] is False
        assert "config migration failed" in result["error"]

        # Verify rollback was called
        mock_rollback.assert_called_once()
        call_args = mock_rollback.call_args
        assert call_args[1]["completed_steps"] == ["credentials"]
        assert call_args[1]["new_home"] == new_home

        # Verify rollback report is included in main report
        assert "rollback" in result
        assert result["rollback"]["success"] is True
        assert result["rollback"]["rolled_back_steps"] == ["credentials"]

    def test_perform_migration_no_rollback_on_dry_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify rollback is NOT triggered during dry-run."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        (legacy_root / "credentials.json").write_text('{"token": "test"}')
        new_home = tmp_path / "home"

        monkeypatch.setenv("MMRELAY_HOME", str(new_home))
        monkeypatch.setattr(
            "mmrelay.migrate.resolve_all_paths",
            lambda: {
                "home": str(new_home),
                "legacy_sources": [str(legacy_root)],
                "credentials_path": str(new_home / "matrix" / "credentials.json"),
                "database_dir": str(new_home / "database"),
                "logs_dir": str(new_home / "logs"),
                "plugins_dir": str(new_home / "plugins"),
                "store_dir": str(new_home / "matrix" / "store"),
            },
        )

        # Mock rollback_migration - it should NOT be called
        with patch("mmrelay.migrate.rollback_migration") as mock_rollback:
            # Mock config migration to fail
            with patch("mmrelay.migrate.migrate_config") as mock_config:
                mock_config.side_effect = MigrationError.step_failed(
                    "config", "Config migration failed"
                )

                result = perform_migration(dry_run=True, force=True)

        # Verify migration failed
        assert result["success"] is False
        assert result["dry_run"] is True
        assert "config migration failed" in result["error"]

        # Verify rollback was NOT called during dry run
        mock_rollback.assert_not_called()

        # Verify no rollback report in main report
        assert "rollback" not in result

    def test_perform_migration_no_rollback_when_no_steps_completed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify rollback only triggers if steps were completed."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        # Don't create any files so first step (credentials) fails immediately
        new_home = tmp_path / "home"

        monkeypatch.setenv("MMRELAY_HOME", str(new_home))
        monkeypatch.setattr(
            "mmrelay.migrate.resolve_all_paths",
            lambda: {
                "home": str(new_home),
                "legacy_sources": [str(legacy_root)],
                "credentials_path": str(new_home / "matrix" / "credentials.json"),
                "database_dir": str(new_home / "database"),
                "logs_dir": str(new_home / "logs"),
                "plugins_dir": str(new_home / "plugins"),
                "store_dir": str(new_home / "matrix" / "store"),
            },
        )

        # Mock rollback_migration - it should NOT be called
        with patch("mmrelay.migrate.rollback_migration") as mock_rollback:
            result = perform_migration(force=True)

        # Verify migration succeeds with no-op actions when nothing exists
        assert result["success"] is True

        # Verify no steps were completed
        assert len(result["completed_steps"]) == 7

        # Verify rollback was NOT called when no steps completed
        mock_rollback.assert_not_called()

        # Verify no rollback report in main report
        assert "rollback" not in result

    def test_perform_migration_database_failure_rolls_back_creds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that database failure triggers rollback of completed credentials step."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        # Create credentials and config files so first two steps succeed
        (legacy_root / "credentials.json").write_text('{"token": "test"}')
        (legacy_root / "config.yaml").write_text("test: config")
        new_home = tmp_path / "home"

        monkeypatch.setenv("MMRELAY_HOME", str(new_home))
        monkeypatch.setattr(
            "mmrelay.migrate.resolve_all_paths",
            lambda: {
                "home": str(new_home),
                "legacy_sources": [str(legacy_root)],
                "credentials_path": str(new_home / "matrix" / "credentials.json"),
                "database_dir": str(new_home / "database"),
                "logs_dir": str(new_home / "logs"),
                "plugins_dir": str(new_home / "plugins"),
                "store_dir": str(new_home / "matrix" / "store"),
            },
        )

        # Mock rollback_migration to verify it gets called
        with patch("mmrelay.migrate.rollback_migration") as mock_rollback:
            mock_rollback.return_value = {
                "success": True,
                "timestamp": "2023-01-01T00:00:00",
                "rolled_back_steps": ["credentials", "config"],
                "errors": [],
            }

            # Mock database migration to fail after credentials and config succeed
            with patch("mmrelay.migrate.migrate_database") as mock_database:
                mock_database.side_effect = MigrationError.step_failed(
                    "database", "Database migration failed"
                )

                result = perform_migration(force=True)

        # Verify migration failed
        assert result["success"] is False
        assert "database migration failed" in result["error"]

        # Verify completed steps include credentials and config
        assert "credentials" in result["completed_steps"]
        assert "config" in result["completed_steps"]
        assert "database" not in result["completed_steps"]

        # Verify rollback was called with correct completed steps
        mock_rollback.assert_called_once()
        call_args = mock_rollback.call_args
        expected_completed = ["credentials", "config"]
        assert call_args[1]["completed_steps"] == expected_completed
        assert call_args[1]["new_home"] == new_home
        assert "migrations" in call_args[1]

        # Verify rollback report is included in main report
        assert "rollback" in result
        assert result["rollback"]["success"] is True
        assert set(result["rollback"]["rolled_back_steps"]) == set(expected_completed)

    def test_perform_migration_partial_failure_rollback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test rollback when migration fails partway through multiple steps."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        # Create files for first 3 steps to succeed
        (legacy_root / "credentials.json").write_text('{"token": "test"}')
        (legacy_root / "config.yaml").write_text("test: config")
        (legacy_root / "nodes.db").write_text("sqlite database")
        new_home = tmp_path / "home"

        monkeypatch.setenv("MMRELAY_HOME", str(new_home))
        monkeypatch.setattr(
            "mmrelay.migrate.resolve_all_paths",
            lambda: {
                "home": str(new_home),
                "legacy_sources": [str(legacy_root)],
                "credentials_path": str(new_home / "matrix" / "credentials.json"),
                "database_dir": str(new_home / "database"),
                "logs_dir": str(new_home / "logs"),
                "plugins_dir": str(new_home / "plugins"),
                "store_dir": str(new_home / "matrix" / "store"),
            },
        )

        # Mock rollback_migration to return successful rollback
        with patch("mmrelay.migrate.rollback_migration") as mock_rollback:
            mock_rollback.return_value = {
                "success": True,
                "timestamp": "2023-01-01T00:00:00",
                "rolled_back_steps": ["credentials", "config", "database"],
                "errors": [],
            }

            # Mock logs migration to fail after first 3 steps succeed
            with patch("mmrelay.migrate.migrate_logs") as mock_logs:
                mock_logs.side_effect = MigrationError.step_failed(
                    "logs", "Logs migration failed"
                )

                result = perform_migration(force=True)

        # Verify migration failed
        assert result["success"] is False
        assert "logs migration failed" in result["error"]

        # Verify first 3 steps were completed
        expected_completed = ["credentials", "config", "database"]
        for step in expected_completed:
            assert step in result["completed_steps"]

        # Verify later steps were not completed
        not_completed = ["logs", "store", "plugins", "gpxtracker"]
        for step in not_completed:
            assert step not in result["completed_steps"]

        # Verify rollback was called with correct completed steps
        mock_rollback.assert_called_once()
        call_args = mock_rollback.call_args
        assert call_args[1]["completed_steps"] == expected_completed
        assert call_args[1]["new_home"] == new_home

        # Verify migrations list was passed to rollback
        migrations_arg = call_args[1]["migrations"]
        assert isinstance(migrations_arg, list)
        assert len(migrations_arg) >= 3  # At least the 3 completed steps

        # Verify rollback report is included in main report
        assert "rollback" in result
        assert result["rollback"]["success"] is True
        assert set(result["rollback"]["rolled_back_steps"]) == set(expected_completed)

    def test_perform_migration_rollback_failure_still_in_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that rollback failure is still recorded in the report."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        (legacy_root / "credentials.json").write_text('{"token": "test"}')
        new_home = tmp_path / "home"

        monkeypatch.setenv("MMRELAY_HOME", str(new_home))
        monkeypatch.setattr(
            "mmrelay.migrate.resolve_all_paths",
            lambda: {
                "home": str(new_home),
                "legacy_sources": [str(legacy_root)],
                "credentials_path": str(new_home / "matrix" / "credentials.json"),
                "database_dir": str(new_home / "database"),
                "logs_dir": str(new_home / "logs"),
                "plugins_dir": str(new_home / "plugins"),
                "store_dir": str(new_home / "matrix" / "store"),
            },
        )

        # Mock rollback_migration to fail
        with patch("mmrelay.migrate.rollback_migration") as mock_rollback:
            mock_rollback.return_value = {
                "success": False,
                "timestamp": "2023-01-01T00:00:00",
                "rolled_back_steps": [],
                "errors": ["Failed to restore backup"],
            }

            # Mock config migration to fail
            with patch("mmrelay.migrate.migrate_config") as mock_config:
                mock_config.side_effect = MigrationError.step_failed(
                    "config", "Config migration failed"
                )

                result = perform_migration(force=True)

        # Verify migration failed
        assert result["success"] is False
        assert "config migration failed" in result["error"]

        # Verify rollback was called and failed
        mock_rollback.assert_called_once()

        # Verify rollback failure is recorded in main report
        assert "rollback" in result
        assert result["rollback"]["success"] is False
        assert "Failed to restore backup" in result["rollback"]["errors"]
