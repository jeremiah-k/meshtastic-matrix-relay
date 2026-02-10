"""
Tests for rollback error handling scenarios in migrate.py.

These tests verify graceful handling of rollback failures in the rollback_migration function.
Each test simulates different failure conditions to ensure error handling works correctly.
"""

import os
import shutil
from pathlib import Path

import pytest

from mmrelay.migrate import rollback_migration


class TestRollbackMigrationErrorHandling:
    """Test rollback_migration error handling scenarios."""

    def test_rollback_migration_restore_failure(self, tmp_path: Path) -> None:
        """Test when restore fails due to permissions."""
        # Create test structure
        new_home = tmp_path / "home"
        new_home.mkdir()

        # Create backup directory and backup file
        backup_dir = new_home / ".migration_backups"
        backup_dir.mkdir()
        backup_file = backup_dir / "credentials.json.bak.20240101_120000"
        backup_file.write_text('{"test": "backup"}')

        # Create destination file
        dest_file = new_home / "matrix" / "credentials.json"
        dest_file.parent.mkdir(parents=True)
        dest_file.write_text('{"test": "current"}')

        # Test data
        completed_steps = ["credentials"]
        migrations = [
            {
                "type": "credentials",
                "result": {
                    "new_path": str(dest_file),
                    "action": "move",
                    "success": True,
                },
            }
        ]

        # Create real error condition - make backup file inaccessible
        backup_file.chmod(0o000)  # Remove all permissions

        # Execute rollback
        result = rollback_migration(completed_steps, migrations, new_home)

        # Restore permissions for cleanup
        backup_file.chmod(0o644)

        # Verify error handling
        assert result["success"] is False
        assert len(result["errors"]) == 1
        assert result["errors"][0]["step"] == "credentials"
        assert len(result["rolled_back_steps"]) == 0

    def test_rollback_migration_missing_step_result(self, tmp_path: Path) -> None:
        """Test when step result is missing from migrations list."""
        new_home = tmp_path / "home"
        new_home.mkdir()

        # Test data - step "database" has no corresponding migration result
        completed_steps = ["database", "credentials"]
        migrations = [
            {
                "type": "credentials",
                "result": {
                    "new_path": str(new_home / "matrix" / "credentials.json"),
                    "action": "move",
                    "success": True,
                },
            }
            # Missing "database" migration result
        ]

        # Execute rollback
        result = rollback_migration(completed_steps, migrations, new_home)

        # Verify handling of missing step result
        assert (
            result["success"] is True
        )  # Missing step shouldn't fail the entire rollback
        assert (
            len(result["rolled_back_steps"]) == 1
        )  # Only credentials should be processed
        # Database step should be skipped but not cause an error
        assert len(result["errors"]) == 0

    def test_rollback_migration_oserror_during_restore(self, tmp_path: Path) -> None:
        """Test OSError handling during file restoration."""
        # Create test structure
        new_home = tmp_path / "home"
        new_home.mkdir()

        # Create backup directory and backup file
        backup_dir = new_home / ".migration_backups"
        backup_dir.mkdir()
        backup_path = backup_dir / "database.sqlite.bak.20240101_120000"
        backup_path.write_text("sqlite database content")

        # Create destination file
        dest_file = new_home / "database" / "meshtastic.sqlite"
        dest_file.parent.mkdir(parents=True)
        dest_file.write_text("current database")

        # Test data
        completed_steps = ["database"]
        migrations = [
            {
                "type": "database",
                "result": {
                    "new_path": str(dest_file.parent),
                    "action": "move",
                    "success": True,
                },
            }
        ]

        # Create real error condition - make backup directory inaccessible
        old_mode = backup_dir.stat().st_mode
        backup_dir.chmod(0o000)  # Remove all permissions

        try:
            # Execute rollback
            result = rollback_migration(completed_steps, migrations, new_home)
        finally:
            # Restore permissions
            backup_dir.chmod(old_mode)

        # Verify OSError handling
        assert result["success"] is False
        assert len(result["errors"]) == 1
        assert result["errors"][0]["step"] == "database"
        assert len(result["rolled_back_steps"]) == 0

    def test_rollback_migration_partial_success(self, tmp_path: Path) -> None:
        """Test when some steps rollback successfully but others fail."""
        # Create test structure
        new_home = tmp_path / "home"
        new_home.mkdir()

        # Create backup directories and files
        backup_dir = new_home / ".migration_backups"
        backup_dir.mkdir()

        # Successful backup - credentials
        creds_backup = backup_dir / "credentials.json.bak.20240101_120000"
        creds_backup.write_text('{"test": "backup_creds"}')

        # Failed backup - config (will be made inaccessible)
        config_backup = backup_dir / "config.yaml.bak.20240101_120000"
        config_backup.write_text("config: backup")

        # Create destination files
        creds_dest = new_home / "matrix" / "credentials.json"
        creds_dest.parent.mkdir(parents=True)
        creds_dest.write_text('{"test": "current_creds"}')

        config_dest = new_home / "config.yaml"
        config_dest.write_text("config: current")

        # Test data
        completed_steps = ["credentials", "config"]
        migrations = [
            {
                "type": "credentials",
                "result": {
                    "new_path": str(creds_dest),
                    "action": "move",
                    "success": True,
                },
            },
            {
                "type": "config",
                "result": {
                    "new_path": str(config_dest),
                    "action": "move",
                    "success": True,
                },
            },
        ]

        # Create real error condition - make config backup inaccessible
        config_backup.chmod(0o000)  # Remove permissions

        try:
            # Execute rollback
            result = rollback_migration(completed_steps, migrations, new_home)
        finally:
            # Restore permissions
            config_backup.chmod(0o644)

        # Verify partial success handling
        assert result["success"] is False  # Overall should be False due to errors
        assert len(result["errors"]) == 1
        assert result["errors"][0]["step"] == "config"

        # But credentials should have succeeded
        assert len(result["rolled_back_steps"]) == 1
        assert result["rolled_back_steps"][0]["step"] == "credentials"
        assert result["rolled_back_steps"][0]["restored_to"] == str(creds_dest)

    def test_rollback_migration_staging_cleanup_failure(self, tmp_path: Path) -> None:
        """Test when staging directory cleanup fails."""
        # Create test structure
        new_home = tmp_path / "home"
        new_home.mkdir()

        # Create staging directory that will fail to clean up
        staging_dir = new_home / ".migration_staging"
        staging_dir.mkdir()
        (staging_dir / "some_file").write_text("staging content")

        # Create backup directory and backup file
        backup_dir = new_home / ".migration_backups"
        backup_dir.mkdir()
        backup_file = backup_dir / "logs.bak.20240101_120000"
        backup_file.mkdir()  # Directory backup

        # Create destination directory
        dest_dir = new_home / "logs"
        dest_dir.mkdir()
        (dest_dir / "current.log").write_text("current log")

        # Test data
        completed_steps = ["logs"]
        migrations = [
            {
                "type": "logs",
                "result": {
                    "new_path": str(dest_dir),
                    "action": "move",
                    "success": True,
                },
            }
        ]

        # Make staging directory unwritable to cause cleanup failure
        old_mode = staging_dir.stat().st_mode
        staging_dir.chmod(0o555)  # Read and execute only

        try:
            # Execute rollback (rollback should succeed, but cleanup should fail)
            result = rollback_migration(completed_steps, migrations, new_home)
        finally:
            # Restore permissions
            staging_dir.chmod(old_mode)

        # Verify that cleanup failure doesn't affect rollback success
        # The rollback should still succeed, just log a warning about cleanup failure
        assert result["success"] is True  # Rollback itself succeeded
        assert len(result["rolled_back_steps"]) == 1
        assert result["rolled_back_steps"][0]["step"] == "logs"

        # No errors should be reported in rollback_report for cleanup failures
        # (they're just logged as warnings, not treated as rollback failures)
        assert len(result["errors"]) == 0

        # Verify staging directory still exists (cleanup failed)
        assert staging_dir.exists()

    def test_rollback_migration_shutil_error_during_restore(
        self, tmp_path: Path
    ) -> None:
        """Test shutil.Error handling during file restoration."""
        # Create test structure
        new_home = tmp_path / "home"
        new_home.mkdir()

        # Create backup directory and backup file
        backup_dir = new_home / ".migration_backups"
        backup_dir.mkdir()

        # Create backup as directory when file is expected (will cause shutil.Error)
        backup_path = backup_dir / "store.bak.20240101_120000"
        backup_path.mkdir()

        # Create destination directory
        dest_dir = new_home / "matrix" / "store"
        dest_dir.parent.mkdir(parents=True)
        dest_dir.mkdir()
        (dest_dir / "current_file").write_text("current content")

        # Test data
        completed_steps = ["store"]
        migrations = [
            {
                "type": "store",
                "result": {
                    "new_path": str(dest_dir),
                    "action": "move",
                    "success": True,
                },
            }
        ]

        # Execute rollback
        result = rollback_migration(completed_steps, migrations, new_home)

        # Verify shutil.Error handling
        assert result["success"] is False
        assert len(result["errors"]) == 1
        assert result["errors"][0]["step"] == "store"
        assert len(result["rolled_back_steps"]) == 0
