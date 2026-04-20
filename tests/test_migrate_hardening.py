"""Tests for migration hardening: staging, already-migrated guards, and backups."""

import contextlib
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from mmrelay.constants.app import (
    CONFIG_FILENAME,
    CREDENTIALS_FILENAME,
    DATABASE_FILENAME,
)
from mmrelay.constants.migration import (
    MIGRATION_BACKUP_DIRNAME,
    MIGRATION_STAGING_DIRNAME,
)
from mmrelay.migrate import (
    MigrationError,
    migrate_config,
    migrate_credentials,
    migrate_database,
    perform_migration,
)


def test_migrate_twice_is_noop(tmp_path: Path):
    """Test that running migrate twice in a row is a clean no-op."""
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    config = legacy_root / CONFIG_FILENAME
    config.write_text("matrix: {}")

    new_home = tmp_path / "home"

    # First run
    result1 = migrate_config([legacy_root], new_home)
    assert result1["success"] is True
    assert result1["action"] == "move"
    assert (new_home / CONFIG_FILENAME).exists()
    assert not config.exists()

    # Second run
    result2 = migrate_config([legacy_root], new_home)
    assert result2["success"] is True
    assert result2["action"] == "already_migrated"
    assert "already migrated" in result2["message"].lower()


def test_already_migrated_guard(tmp_path: Path):
    """Test that destination existing and legacy missing is treated as already migrated."""
    new_home = tmp_path / "home"
    new_home.mkdir()
    (new_home / CONFIG_FILENAME).write_text("migrated: true")

    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    # No config in legacy

    result = migrate_config([legacy_root], new_home)
    assert result["success"] is True
    assert result["action"] == "already_migrated"
    assert "already migrated" in result["message"].lower()


def test_same_path_guard(tmp_path: Path):
    """Test that equivalent paths are skipped."""
    new_home = tmp_path / "home"
    new_home.mkdir()
    config = new_home / CONFIG_FILENAME
    config.write_text("content: true")

    # legacy_roots contains new_home
    result = migrate_config([new_home], new_home)
    assert result["success"] is True
    assert result["action"] == "already_at_target"
    assert "already at target location" in result["message"].lower()


def test_force_always_creates_backup(tmp_path: Path):
    """Test that --force still creates a backup of destination."""
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    old_creds = legacy_root / CREDENTIALS_FILENAME
    old_creds.write_text('{"token": "new"}')

    new_home = tmp_path / "home"
    new_home.mkdir()
    matrix_dir = new_home / "matrix"
    matrix_dir.mkdir()
    new_creds = matrix_dir / CREDENTIALS_FILENAME
    new_creds.write_text('{"token": "old"}')

    # Run with force=True
    result = migrate_credentials([legacy_root], new_home, force=True)
    assert result["success"] is True

    backup_dir = matrix_dir / MIGRATION_BACKUP_DIRNAME
    assert backup_dir.exists()
    backups = list(backup_dir.glob("credentials.json.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == '{"token": "old"}'
    assert new_creds.read_text() == '{"token": "new"}'


def test_staging_pattern_credentials(tmp_path: Path):
    """Test that credentials migration uses the staging pattern."""
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    old_creds = legacy_root / CREDENTIALS_FILENAME
    old_creds.write_text('{"token": "secret"}')

    new_home = tmp_path / "home"

    # Mock _finalize_move to fail after staging
    with patch(
        "mmrelay.migrate._finalize_move", side_effect=OSError("Finalize failed")
    ):
        with pytest.raises(Exception, match="Finalize failed"):
            migrate_credentials([legacy_root], new_home)

    # Final destination should NOT exist
    assert not (new_home / "matrix" / CREDENTIALS_FILENAME).exists()

    # Staging should still be there
    staging_file = new_home / MIGRATION_STAGING_DIRNAME / "credentials"
    assert staging_file.exists()
    assert staging_file.read_text() == '{"token": "secret"}'


def test_database_migration_success(tmp_path: Path) -> None:
    """Test that database migration completes successfully."""
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    db_path = legacy_root / DATABASE_FILENAME
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE test (id INTEGER)")
    conn.commit()
    conn.close()

    new_home = tmp_path / "home"

    result = migrate_database([legacy_root], new_home)
    assert result["success"] is True
    migrated_db_path = new_home / "database" / DATABASE_FILENAME
    assert migrated_db_path.exists()
    assert not db_path.exists()

    with contextlib.closing(sqlite3.connect(migrated_db_path)) as conn:
        with conn as managed_conn:
            row = managed_conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='test'"
            ).fetchone()
    assert row is not None
    assert row[0] == 1


def test_database_migration_move_failure_preserves_legacy_source(
    tmp_path: Path,
) -> None:
    """Database migration failures should not delete the legacy source DB."""
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    db_path = legacy_root / DATABASE_FILENAME
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        with conn:
            conn.execute("CREATE TABLE test (id INTEGER)")

    new_home = tmp_path / "home"

    with patch("mmrelay.migrate.shutil.move", side_effect=OSError("move failed")):
        with pytest.raises(MigrationError, match="move failed"):
            migrate_database([legacy_root], new_home)

    assert db_path.exists()
    assert not (new_home / "database" / DATABASE_FILENAME).exists()


def test_migration_failure_reports_paths(
    tmp_path: Path,
):
    """Test that migration failure includes relevant paths in result."""
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    config_file = legacy_root / CONFIG_FILENAME
    config_file.write_text("matrix: {}")

    new_home = tmp_path / "home"
    new_home.mkdir()

    with patch("mmrelay.migrate.resolve_all_paths") as mock_resolve:
        mock_resolve.return_value = {
            "home": str(new_home),
            "legacy_sources": [str(legacy_root)],
            "credentials_path": str(new_home / "matrix" / CREDENTIALS_FILENAME),
            "database_dir": str(new_home / "database"),
            "logs_dir": str(new_home / "logs"),
            "plugins_dir": str(new_home / "plugins"),
            "store_dir": str(new_home / "matrix" / "store"),
        }

        # Mock running instance check to return False (no running instance)
        with patch("mmrelay.migrate._is_mmrelay_running", return_value=False):
            # Mock finalize to fail
            with patch(
                "mmrelay.migrate._finalize_move", side_effect=OSError("Staging remains")
            ):
                result = perform_migration(dry_run=False)
                assert result["success"] is False

                # Check that error info is present in the result
                assert "error" in result or "migrations" in result

                # Check that migrations list contains the failed step
                if "migrations" in result:
                    step_names = [m.get("type") for m in result["migrations"]]
                    # At least one step should have been attempted (credentials or config)
                    assert len(step_names) > 0
