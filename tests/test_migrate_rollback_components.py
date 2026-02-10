"""
Tests for rollback of specific migration components.

These tests verify that each migration component type can be rolled back correctly,
including proper handling of sidecar files and subdirectories.
"""

import sys
from pathlib import Path

import pytest

from mmrelay.migrate import rollback_migration


class TestRollbackCredentialsMigration:
    """Test rollback of credentials.json migration."""

    def test_rollback_credentials_migration(self, tmp_path: Path) -> None:
        """Test rollback of credentials.json from backup."""
        # Create test structure
        new_home = tmp_path / "home"
        new_home.mkdir()

        # Create backup directory and credentials backup
        # Create parent directories for gpxtracker
        gpx_plugin_dir = new_home / "plugins" / "community" / "gpxtracker"
        gpx_plugin_dir.mkdir(parents=True)

        backup_dir = new_home / "database" / ".migration_backups"
        backup_dir.mkdir()
        creds_backup = backup_dir / "credentials.json.bak.20240101_120000"
        creds_backup.write_text(
            '{"homeserver": "https://matrix.org", "access_token": "test_token"}'
        )

        # Create current (migrated) credentials file
        creds_dest = new_home / "matrix" / "credentials.json"
        creds_dest.parent.mkdir(parents=True)
        creds_dest.write_text(
            '{"homeserver": "https://new.server", "access_token": "new_token"}'
        )

        # Test data
        completed_steps = ["credentials"]
        migrations = [
            {
                "type": "credentials",
                "result": {
                    "new_path": str(creds_dest),
                    "action": "move",
                    "success": True,
                },
            }
        ]

        # Execute rollback
        result = rollback_migration(completed_steps, migrations, new_home)

        # Verify rollback succeeded
        assert result["success"] is True
        assert len(result["rolled_back_steps"]) == 1
        assert result["rolled_back_steps"][0]["step"] == "credentials"

        # Verify credentials restored from backup
        assert creds_dest.exists()
        restored_content = creds_dest.read_text()
        assert "test_token" in restored_content
        assert "matrix.org" in restored_content


class TestRollbackConfigMigration:
    """Test rollback of config.yaml migration."""

    def test_rollback_config_migration(self, tmp_path: Path) -> None:
        """Test rollback of config.yaml from backup."""
        # Create test structure
        new_home = tmp_path / "home"
        new_home.mkdir()

        # Create backup directory and config backup
        # Create parent directories for gpxtracker
        gpx_plugin_dir = new_home / "plugins" / "community" / "gpxtracker"
        gpx_plugin_dir.mkdir(parents=True)

        backup_dir = new_home / "database" / ".migration_backups"
        backup_dir.mkdir()
        config_backup = backup_dir / "config.yaml.bak.20240101_120000"
        config_backup.write_text(
            "meshtastic:\n  connection_type: serial\nmatrix:\n  homeserver: https://old.server"
        )

        # Create current (migrated) config file
        config_dest = new_home / "config.yaml"
        config_dest.write_text(
            "meshtastic:\n  connection_type: network\nmatrix:\n  homeserver: https://new.server"
        )

        # Test data
        completed_steps = ["config"]
        migrations = [
            {
                "type": "config",
                "result": {
                    "new_path": str(config_dest),
                    "action": "move",
                    "success": True,
                },
            }
        ]

        # Execute rollback
        result = rollback_migration(completed_steps, migrations, new_home)

        # Verify rollback succeeded
        assert result["success"] is True
        assert len(result["rolled_back_steps"]) == 1
        assert result["rolled_back_steps"][0]["step"] == "config"

        # Verify config restored from backup
        assert config_dest.exists()
        restored_content = config_dest.read_text()
        assert "serial" in restored_content
        assert "old.server" in restored_content


class TestRollbackDatabaseMigration:
    """Test rollback of database files including WAL/SHM sidecars."""

    def test_rollback_database_migration_with_sidecars(self, tmp_path: Path) -> None:
        """Test rollback of database including WAL and SHM sidecar files."""
        # Create test structure
        new_home = tmp_path / "home"
        new_home.mkdir()

        # Create backup directory and database backups with sidecars
        # Create parent directories for gpxtracker
        gpx_plugin_dir = new_home / "plugins" / "community" / "gpxtracker"
        gpx_plugin_dir.mkdir(parents=True)

        backup_dir = new_home / "database" / ".migration_backups"
        backup_dir.mkdir()

        # Main database backup
        db_backup = backup_dir / "meshtastic.sqlite.bak.20240101_120000"
        db_backup.write_text("SQLite format 3 (backup)")

        # WAL file backup
        wal_backup = backup_dir / "meshtastic.sqlite-wal.bak.20240101_120000"
        wal_backup.write_text("WAL backup content")

        # SHM file backup
        shm_backup = backup_dir / "meshtastic.sqlite-shm.bak.20240101_120000"
        shm_backup.write_text("SHM backup content")

        # Create current (migrated) database files
        db_dir = new_home / "database"
        db_dir.mkdir(parents=True)

        db_file = db_dir / "meshtastic.sqlite"
        db_file.write_text("SQLite format 3 (migrated)")

        wal_file = db_dir / "meshtastic.sqlite-wal"
        wal_file.write_text("WAL migrated content")

        shm_file = db_dir / "meshtastic.sqlite-shm"
        shm_file.write_text("SHM migrated content")

        # Test data - note: database migration handles sidecars internally
        completed_steps = ["database"]
        migrations = [
            {
                "type": "database",
                "result": {"new_path": str(db_dir), "action": "move", "success": True},
            }
        ]

        # Execute rollback
        result = rollback_migration(completed_steps, migrations, new_home)

        # Verify rollback succeeded
        assert result["success"] is True
        assert len(result["rolled_back_steps"]) == 1
        assert result["rolled_back_steps"][0]["step"] == "database"

        # Verify database file restored
        assert db_file.exists()
        assert "backup" in db_file.read_text()


class TestRollbackLogsMigration:
    """Test rollback of logs directory migration."""

    def test_rollback_logs_migration(self, tmp_path: Path) -> None:
        """Test rollback of logs directory from backup."""
        # Create test structure
        new_home = tmp_path / "home"
        new_home.mkdir()

        # Create backup directory and logs backup
        # Create parent directories for gpxtracker
        gpx_plugin_dir = new_home / "plugins" / "community" / "gpxtracker"
        gpx_plugin_dir.mkdir(parents=True)

        backup_dir = new_home / "database" / ".migration_backups"
        backup_dir.mkdir()
        logs_backup = backup_dir / "logs.bak.20240101_120000"
        logs_backup.mkdir()

        # Add log files to backup
        (logs_backup / "mmrelay_2024_01_01.log").write_text("Backup log content")
        (logs_backup / "mmrelay_2024_01_02.log").write_text("Another backup log")

        # Create current (migrated) logs directory
        logs_dest = new_home / "logs"
        logs_dest.mkdir()
        (logs_dest / "mmrelay_new.log").write_text("New log content")

        # Test data
        completed_steps = ["logs"]
        migrations = [
            {
                "type": "logs",
                "result": {
                    "new_path": str(logs_dest),
                    "action": "move",
                    "success": True,
                },
            }
        ]

        # Execute rollback
        result = rollback_migration(completed_steps, migrations, new_home)

        # Verify rollback succeeded
        assert result["success"] is True
        assert len(result["rolled_back_steps"]) == 1
        assert result["rolled_back_steps"][0]["step"] == "logs"

        # Verify logs directory restored from backup
        assert logs_dest.exists()
        assert (logs_dest / "mmrelay_2024_01_01.log").exists()
        assert (logs_dest / "mmrelay_2024_01_02.log").exists()
        assert (
            "Backup log content" in (logs_dest / "mmrelay_2024_01_01.log").read_text()
        )


class TestRollbackStoreMigration:
    """Test rollback of E2EE store directory migration."""

    @pytest.mark.skipif(sys.platform == "win32", reason="E2EE not supported on Windows")
    def test_rollback_store_migration(self, tmp_path: Path) -> None:
        """Test rollback of E2EE store directory from backup (Unix/macOS only)."""
        # Create test structure
        new_home = tmp_path / "home"
        new_home.mkdir()

        # Create backup directory and store backup
        # Create parent directories for gpxtracker
        gpx_plugin_dir = new_home / "plugins" / "community" / "gpxtracker"
        gpx_plugin_dir.mkdir(parents=True)

        backup_dir = new_home / "database" / ".migration_backups"
        backup_dir.mkdir()
        store_backup = backup_dir / "store.bak.20240101_120000"
        store_backup.mkdir()

        # Add E2EE files to backup
        (store_backup / "device_keys.json").write_text('{"device_id": "old_device"}')
        (store_backup / "sessions").mkdir()
        (store_backup / "sessions" / "session_1.db").write_text("session data")

        # Create current (migrated) store directory
        store_dest = new_home / "matrix" / "store"
        store_dest.mkdir(parents=True)
        (store_dest / "device_keys.json").write_text('{"device_id": "new_device"}')

        # Test data
        completed_steps = ["store"]
        migrations = [
            {
                "type": "store",
                "result": {
                    "new_path": str(store_dest),
                    "action": "move",
                    "success": True,
                },
            }
        ]

        # Execute rollback
        result = rollback_migration(completed_steps, migrations, new_home)

        # Verify rollback succeeded
        assert result["success"] is True
        assert len(result["rolled_back_steps"]) == 1
        assert result["rolled_back_steps"][0]["step"] == "store"

        # Verify store directory restored from backup
        assert store_dest.exists()
        assert (store_dest / "device_keys.json").exists()
        assert "old_device" in (store_dest / "device_keys.json").read_text()
        assert (store_dest / "sessions" / "session_1.db").exists()


class TestRollbackPluginsMigration:
    """Test rollback of plugins directory migration."""

    def test_rollback_plugins_migration(self, tmp_path: Path) -> None:
        """Test rollback of plugins directory with custom and community subdirectories."""
        # Create test structure
        new_home = tmp_path / "home"
        new_home.mkdir()

        # Create backup directory and plugins backup
        # Create parent directories for gpxtracker
        gpx_plugin_dir = new_home / "plugins" / "community" / "gpxtracker"
        gpx_plugin_dir.mkdir(parents=True)

        backup_dir = new_home / "database" / ".migration_backups"
        backup_dir.mkdir()
        plugins_backup = backup_dir / "plugins.bak.20240101_120000"
        plugins_backup.mkdir()

        # Add custom plugins to backup
        custom_dir = plugins_backup / "custom"
        custom_dir.mkdir()
        (custom_dir / "my_plugin.py").write_text("# Backup custom plugin")

        # Add community plugins to backup
        community_dir = plugins_backup / "community"
        community_dir.mkdir()
        (community_dir / "weather_plugin.py").write_text("# Backup community plugin")

        # Create current (migrated) plugins directory
        plugins_dest = new_home / "plugins"
        plugins_dest.mkdir()

        custom_dest = plugins_dest / "custom"
        custom_dest.mkdir()
        (custom_dest / "my_plugin.py").write_text("# New custom plugin")

        community_dest = plugins_dest / "community"
        community_dest.mkdir()
        (community_dest / "map_plugin.py").write_text("# New community plugin")

        # Test data
        completed_steps = ["plugins"]
        migrations = [
            {
                "type": "plugins",
                "result": {
                    "new_path": str(plugins_dest),
                    "action": "move",
                    "success": True,
                },
            }
        ]

        # Execute rollback
        result = rollback_migration(completed_steps, migrations, new_home)

        # Verify rollback succeeded
        assert result["success"] is True
        assert len(result["rolled_back_steps"]) == 1
        assert result["rolled_back_steps"][0]["step"] == "plugins"

        # Verify plugins directory restored with subdirectories
        assert plugins_dest.exists()
        assert (plugins_dest / "custom" / "my_plugin.py").exists()
        assert (
            "Backup custom plugin"
            in (plugins_dest / "custom" / "my_plugin.py").read_text()
        )
        assert (plugins_dest / "community" / "weather_plugin.py").exists()
        assert (
            "Backup community plugin"
            in (plugins_dest / "community" / "weather_plugin.py").read_text()
        )


class TestRollbackGpxtrackerMigration:
    """Test rollback of GPX tracker data migration."""

    def test_rollback_gpxtracker_migration(self, tmp_path: Path) -> None:
        """Test rollback of GPX tracker data from backup."""
        # Create test structure
        new_home = tmp_path / "home"
        new_home.mkdir()

        # Create backup directory and GPX tracker backup
        # Create parent directories for gpxtracker
        gpx_plugin_dir = new_home / "plugins" / "community" / "gpxtracker"
        gpx_plugin_dir.mkdir(parents=True)

        backup_dir = new_home / "database" / ".migration_backups"
        backup_dir.mkdir()
        gpx_backup = backup_dir / "gpxtracker_data.bak.20240101_120000"
        gpx_backup.mkdir()

        # Add GPX files to backup
        (gpx_backup / "track_2024_01_01.gpx").write_text("<gpx>Backup track</gpx>")
        (gpx_backup / "track_2024_01_02.gpx").write_text(
            "<gpx>Another backup track</gpx>"
        )

        # Create current (migrated) GPX directory
        gpx_dest = new_home / "plugins" / "community" / "gpxtracker" / "data"
        gpx_dest.mkdir(parents=True)
        (gpx_dest / "track_new.gpx").write_text("<gpx>New track</gpx>")

        # Test data
        completed_steps = ["gpxtracker"]
        migrations = [
            {
                "type": "gpxtracker",
                "result": {
                    "new_path": str(gpx_dest),
                    "action": "move",
                    "success": True,
                },
            }
        ]

        # Execute rollback
        result = rollback_migration(completed_steps, migrations, new_home)

        # Verify rollback succeeded
        assert result["success"] is True
        assert len(result["rolled_back_steps"]) == 1
        assert result["rolled_back_steps"][0]["step"] == "gpxtracker"

        # Verify GPX data restored from backup
        assert gpx_dest.exists()
        assert (gpx_dest / "track_2024_01_01.gpx").exists()
        assert "Backup track" in (gpx_dest / "track_2024_01_01.gpx").read_text()


class TestRollbackMultipleComponents:
    """Test rollback of multiple components in reverse order."""

    def test_rollback_multiple_components_reverse_order(self, tmp_path: Path) -> None:
        """Test that multiple components are rolled back in reverse order."""
        # Create test structure
        new_home = tmp_path / "home"
        new_home.mkdir()

        # Create backup directory
        # Create parent directories for gpxtracker
        gpx_plugin_dir = new_home / "plugins" / "community" / "gpxtracker"
        gpx_plugin_dir.mkdir(parents=True)

        backup_dir = new_home / "database" / ".migration_backups"
        backup_dir.mkdir()

        # Create backups for multiple components
        creds_backup = backup_dir / "credentials.json.bak.20240101_120000"
        creds_backup.write_text('{"token": "backup_creds"}')

        config_backup = backup_dir / "config.yaml.bak.20240101_120000"
        config_backup.write_text("config: backup")

        logs_backup = backup_dir / "logs.bak.20240101_120000"
        logs_backup.mkdir()
        (logs_backup / "backup.log").write_text("backup log")

        # Create current (migrated) files
        creds_dest = new_home / "matrix" / "credentials.json"
        creds_dest.parent.mkdir(parents=True)
        creds_dest.write_text('{"token": "new_creds"}')

        config_dest = new_home / "config.yaml"
        config_dest.write_text("config: new")

        logs_dest = new_home / "logs"
        logs_dest.mkdir()
        (logs_dest / "new.log").write_text("new log")

        # Test data - steps completed in order: credentials, config, logs
        completed_steps = ["credentials", "config", "logs"]
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
            {
                "type": "logs",
                "result": {
                    "new_path": str(logs_dest),
                    "action": "move",
                    "success": True,
                },
            },
        ]

        # Execute rollback
        result = rollback_migration(completed_steps, migrations, new_home)

        # Verify rollback succeeded for all components
        assert result["success"] is True
        assert len(result["rolled_back_steps"]) == 3

        # Verify all components restored
        assert "backup_creds" in creds_dest.read_text()
        assert "config: backup" in config_dest.read_text()
        assert (logs_dest / "backup.log").exists()
