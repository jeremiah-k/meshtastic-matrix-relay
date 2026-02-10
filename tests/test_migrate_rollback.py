"""Tests for rollback_migration() function in migrate.py.

These tests verify the critical safety functionality that restores files from
.migration_backups/ directory when migration fails, ensuring data is not lost.
"""

import shutil
from pathlib import Path

from mmrelay.migrate import BACKUP_DIRNAME, rollback_migration


class TestRollbackMigration:
    """Test rollback_migration() function for restoring files from backups."""

    def test_rollback_migration_no_completed_steps(
        self, clean_migration_home: Path
    ) -> None:
        """Test rollback with empty completed_steps list."""
        new_home = clean_migration_home
        completed_steps: list[str] = []
        migrations: list[dict[str, any]] = []

        # Call rollback_migration with no completed steps
        result = rollback_migration(completed_steps, migrations, new_home)

        # Verify rollback report structure
        assert result["success"] is True
        assert "timestamp" in result
        assert result["rolled_back_steps"] == []
        assert result["errors"] == []

    def test_rollback_migration_single_step_success(
        self, clean_migration_home: Path, tmp_path: Path
    ) -> None:
        """Test rollback of a single completed step."""
        new_home = clean_migration_home
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()

        # Create test file in legacy location
        test_file = legacy_root / "config.yaml"
        test_file.write_text("matrix: {}\nmeshtastic: {}")

        # Create destination and backup directories
        matrix_dir = new_home / "matrix"
        matrix_dir.mkdir(parents=True)
        backup_dir = matrix_dir / BACKUP_DIRNAME
        backup_dir.mkdir(parents=True)

        # Simulate migration - move file and create backup
        dest_file = matrix_dir / "config.yaml"
        shutil.move(str(test_file), str(dest_file))

        # Create backup file (simulate _backup_file behavior)
        backup_file = backup_dir / "config.yaml.bak.1234567890"
        shutil.copy2(str(dest_file), str(backup_file))

        # Set up migration result
        completed_steps = ["migrate_config"]
        migrations = [
            {
                "type": "migrate_config",
                "result": {
                    "action": "move",
                    "new_path": str(dest_file),
                    "old_path": str(test_file),
                },
            }
        ]

        # Call rollback_migration
        result = rollback_migration(completed_steps, migrations, new_home)

        # Verify rollback succeeded
        assert result["success"] is True
        assert len(result["rolled_back_steps"]) == 1
        assert result["rolled_back_steps"][0]["step"] == "migrate_config"
        assert result["rolled_back_steps"][0]["restored_to"] == str(dest_file)
        assert result["rolled_back_steps"][0]["restored_from"] == str(backup_file)
        assert result["errors"] == []

        # Verify file was restored from backup
        assert dest_file.exists()
        assert dest_file.read_text() == "matrix: {}\nmeshtastic: {}"

    def test_rollback_migration_multiple_steps_success(
        self, clean_migration_home: Path, tmp_path: Path
    ) -> None:
        """Test rollback of multiple completed steps in reverse order."""
        new_home = clean_migration_home
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()

        # Create test files in legacy location
        config_file = legacy_root / "config.yaml"
        config_file.write_text("matrix: {}\nmeshtastic: {}")

        # Create credentials directory and file
        creds_dir = legacy_root / "credentials"
        creds_dir.mkdir()
        creds_file = creds_dir / "credentials.json"
        creds_file.write_text('{"access_token": "test_token"}')

        # Create destination directories
        matrix_dir = new_home / "matrix"
        matrix_dir.mkdir(parents=True)
        database_dir = new_home / "database"
        database_dir.mkdir(parents=True)

        # Create backup directories
        config_backup_dir = matrix_dir / BACKUP_DIRNAME
        config_backup_dir.mkdir(parents=True)
        db_backup_dir = database_dir / BACKUP_DIRNAME
        db_backup_dir.mkdir(parents=True)

        # Simulate migration - move files and create backups
        dest_config = matrix_dir / "config.yaml"
        dest_creds = matrix_dir / "credentials.json"
        shutil.move(str(config_file), str(dest_config))
        shutil.move(str(creds_file), str(dest_creds))

        # Create backup files
        config_backup = config_backup_dir / "config.yaml.bak.1234567890"
        creds_backup = config_backup_dir / "credentials.json.bak.1234567891"
        shutil.copy2(str(dest_config), str(config_backup))
        shutil.copy2(str(dest_creds), str(creds_backup))

        # Set up migration results (note: config migrated first, then credentials)
        completed_steps = ["migrate_config", "migrate_credentials"]
        migrations = [
            {
                "type": "migrate_config",
                "result": {
                    "action": "move",
                    "new_path": str(dest_config),
                    "old_path": str(config_file),
                },
            },
            {
                "type": "migrate_credentials",
                "result": {
                    "action": "move",
                    "new_path": str(dest_creds),
                    "old_path": str(creds_file),
                },
            },
        ]

        # Call rollback_migration
        result = rollback_migration(completed_steps, migrations, new_home)

        # Verify rollback succeeded and processed in reverse order
        assert result["success"] is True
        assert len(result["rolled_back_steps"]) == 2

        # Verify steps were processed in reverse order (credentials first, then config)
        assert result["rolled_back_steps"][0]["step"] == "migrate_credentials"
        assert result["rolled_back_steps"][1]["step"] == "migrate_config"

        # Verify both files were restored
        assert dest_config.exists()
        assert dest_creds.exists()
        assert dest_config.read_text() == "matrix: {}\nmeshtastic: {}"
        assert dest_creds.read_text() == '{"access_token": "test_token"}'
        assert result["errors"] == []

    def test_rollback_migration_skips_no_action_steps(
        self, clean_migration_home: Path, tmp_path: Path
    ) -> None:
        """Test that steps with action='none' or 'already_migrated' are skipped."""
        new_home = clean_migration_home
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()

        # Create test file
        test_file = legacy_root / "config.yaml"
        test_file.write_text("matrix: {}")

        # Create destination and backup directories
        matrix_dir = new_home / "matrix"
        matrix_dir.mkdir(parents=True)
        backup_dir = matrix_dir / BACKUP_DIRNAME
        backup_dir.mkdir(parents=True)

        # Simulate migration
        dest_file = matrix_dir / "config.yaml"
        shutil.move(str(test_file), str(dest_file))

        # Create backup
        backup_file = backup_dir / "config.yaml.bak.1234567890"
        shutil.copy2(str(dest_file), str(backup_file))

        # Set up migration results with mixed actions
        completed_steps = [
            "migrate_config_none",
            "migrate_config",
            "migrate_config_already",
            "migrate_credentials_not_found",
        ]
        migrations = [
            {
                "type": "migrate_config_none",
                "result": {
                    "action": "none",
                    "new_path": str(dest_file),
                    "old_path": str(test_file),
                },
            },
            {
                "type": "migrate_config",
                "result": {
                    "action": "move",
                    "new_path": str(dest_file),
                    "old_path": str(test_file),
                },
            },
            {
                "type": "migrate_config_already",
                "result": {
                    "action": "already_migrated",
                    "new_path": str(dest_file),
                    "old_path": str(test_file),
                },
            },
            {
                "type": "migrate_credentials_not_found",
                "result": {
                    "action": "not_found",
                    "new_path": "/nonexistent/path",
                    "old_path": "/old/nonexistent",
                },
            },
        ]

        # Call rollback_migration
        result = rollback_migration(completed_steps, migrations, new_home)

        # Verify only the 'move' action was processed
        assert result["success"] is True
        assert len(result["rolled_back_steps"]) == 1
        assert result["rolled_back_steps"][0]["step"] == "migrate_config"
        assert result["errors"] == []

        # Verify file was restored
        assert dest_file.exists()

    def test_rollback_migration_missing_backup(
        self, clean_migration_home: Path, tmp_path: Path
    ) -> None:
        """Test behavior when backup is missing (should skip with warning)."""
        new_home = clean_migration_home
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()

        # Create test file in legacy location
        test_file = legacy_root / "config.yaml"
        test_file.write_text("matrix: {}")

        # Create destination directory but NO backup directory
        matrix_dir = new_home / "matrix"
        matrix_dir.mkdir(parents=True)

        # Simulate migration - move file but don't create backup
        dest_file = matrix_dir / "config.yaml"
        shutil.move(str(test_file), str(dest_file))

        # Set up migration result
        completed_steps = ["migrate_config"]
        migrations = [
            {
                "type": "migrate_config",
                "result": {
                    "action": "move",
                    "new_path": str(dest_file),
                    "old_path": str(test_file),
                },
            }
        ]

        # Call rollback_migration
        result = rollback_migration(completed_steps, migrations, new_home)

        # Verify rollback succeeded but skipped due to missing backup
        assert result["success"] is True
        assert len(result["rolled_back_steps"]) == 1
        assert result["rolled_back_steps"][0]["step"] == "migrate_config"
        assert result["rolled_back_steps"][0]["skipped"] is True
        assert result["rolled_back_steps"][0]["reason"] == "no backup, data preserved"
        assert result["rolled_back_steps"][0]["path"] == str(dest_file)
        assert result["errors"] == []

        # Verify original file still exists (data preserved)
        assert dest_file.exists()
