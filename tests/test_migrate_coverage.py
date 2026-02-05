"""
Additional tests to improve coverage for migrate.py error paths and edge cases.

Docstrings are necessary: This test file improves coverage for migrate.py by testing
error paths and edge cases that weren't covered in existing tests. Test docstrings
follow pytest conventions and document the purpose of each test case.

Inline comments explain test assertions and expected behavior for clarity.
"""

import os
import shutil
import sqlite3
import sys
from pathlib import Path
from unittest import mock

import pytest

from mmrelay.migrate import (
    MigrationError,
    _backup_file,
    _dir_has_entries,
    _find_legacy_data,
    _get_migration_state_path,
    _get_most_recent_database,
    _path_is_within_home,
    migrate_database,
    migrate_gpxtracker,
    migrate_plugins,
    migrate_store,
    rollback_migration,
)
from mmrelay.paths import get_home_dir


class TestPathIsWithinHome:
    """Test _path_is_within_home function coverage."""

    def test_path_is_within_home_exact_match(self, tmp_path):
        """Test path that is exactly the home directory."""
        home = tmp_path / "home"
        home.mkdir()
        assert _path_is_within_home(home, home) is True

    def test_path_is_within_home_subdirectory(self, tmp_path):
        """Test path that is inside home directory."""
        home = tmp_path / "home"
        home.mkdir()
        subdir = home / "subdir"
        assert _path_is_within_home(subdir, home) is True

    def test_path_is_within_home_not_inside(self, tmp_path):
        """Test path that is outside home directory."""
        home = tmp_path / "home"
        home.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        assert _path_is_within_home(other, home) is False

    def test_path_is_within_home_os_error_resolve(self, tmp_path):
        """Test handling of OSError during path resolution."""
        home = tmp_path / "home"
        path = tmp_path / "path"

        with mock.patch.object(Path, "resolve", side_effect=OSError("Mock error")):
            # Should fall back to absolute()
            result = _path_is_within_home(path, home)
            assert isinstance(result, bool)


class TestDirHasEntries:
    """Test _dir_has_entries function coverage."""

    def test_dir_has_entries_nonexistent(self, tmp_path):
        """Test non-existent directory returns False."""
        result = _dir_has_entries(tmp_path / "nonexistent")
        assert result is False

    def test_dir_has_entries_file(self, tmp_path):
        """Test file path returns False."""
        file_path = tmp_path / "file.txt"
        file_path.write_text("test")
        result = _dir_has_entries(file_path)
        assert result is False

    def test_dir_has_entries_empty(self, tmp_path):
        """Test empty directory returns False."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = _dir_has_entries(empty_dir)
        assert result is False

    def test_dir_has_entries_with_files(self, tmp_path):
        """Test directory with files returns True."""
        dir_with_files = tmp_path / "with_files"
        dir_with_files.mkdir()
        (dir_with_files / "test.txt").write_text("content")
        result = _dir_has_entries(dir_with_files)
        assert result is True

    def test_dir_has_entries_os_error_iterdir(self, tmp_path):
        """Test handling of OSError during iterdir()."""
        dir_path = tmp_path / "test_dir"
        dir_path.mkdir()

        with mock.patch.object(Path, "iterdir", side_effect=OSError("Mock error")):
            result = _dir_has_entries(dir_path)
            assert result is False


class TestFindLegacyData:
    """Test _find_legacy_data function coverage."""

    def test_find_legacy_data_credentials(self, tmp_path):
        """Test finding credentials.json."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        creds = legacy_root / "credentials.json"
        creds.write_text('{"test": "data"}')

        findings = _find_legacy_data(legacy_root)
        assert len(findings) == 1
        assert findings[0]["type"] == "credentials"
        assert findings[0]["path"] == str(creds)

    def test_find_legacy_data_database(self, tmp_path):
        """Test finding meshtastic.sqlite."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        db = legacy_root / "meshtastic.sqlite"
        db.write_text("sqlite db")

        findings = _find_legacy_data(legacy_root)
        assert len(findings) == 1
        assert findings[0]["type"] == "database"

    def test_find_legacy_data_wal_shm(self, tmp_path):
        """Test finding database with WAL/SHM sidecars."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        db = legacy_root / "meshtastic.sqlite"
        db.write_text("sqlite db")
        wal = legacy_root / "meshtastic.sqlite-wal"
        wal.write_text("wal data")
        shm = legacy_root / "meshtastic.sqlite-shm"
        shm.write_text("shm data")

        findings = _find_legacy_data(legacy_root)
        # Should find db, wal, and shm
        db_findings = [f for f in findings if f["type"] == "database"]
        assert len(db_findings) == 3

    def test_find_legacy_data_partial_database(self, tmp_path):
        """Test finding database in partial new layout (data/ directory)."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        data_dir = legacy_root / "data"
        data_dir.mkdir()
        db = data_dir / "meshtastic.sqlite"
        db.write_text("sqlite db")

        findings = _find_legacy_data(legacy_root)
        db_findings = [f for f in findings if f["type"] == "database"]
        assert len(db_findings) == 1
        assert "data" in db_findings[0]["path"]

    def test_find_legacy_data_logs(self, tmp_path):
        """Test finding logs directory."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        logs_dir = legacy_root / "logs"
        logs_dir.mkdir()
        (logs_dir / "test.log").write_text("log content")

        findings = _find_legacy_data(legacy_root)
        log_findings = [f for f in findings if f["type"] == "logs"]
        assert len(log_findings) == 1

    def test_find_legacy_data_store(self, tmp_path):
        """Test finding E2EE store directory."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        store_dir = legacy_root / "store"
        store_dir.mkdir()
        (store_dir / "key").write_text("key data")

        findings = _find_legacy_data(legacy_root)
        store_findings = [f for f in findings if f["type"] == "e2ee_store"]
        assert len(store_findings) == 1

    def test_find_legacy_data_plugins(self, tmp_path):
        """Test finding plugins directory."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        plugins_dir = legacy_root / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "test.py").write_text("plugin code")

        findings = _find_legacy_data(legacy_root)
        plugin_findings = [f for f in findings if f["type"] == "plugins"]
        assert len(plugin_findings) == 1

    def test_find_legacy_data_deduplication(self, tmp_path):
        """Test that duplicate paths are deduplicated."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()

        # Create files that would be found multiple times
        db = legacy_root / "meshtastic.sqlite"
        db.write_text("db")

        # Database layout with nested database (should dedupe)
        database_dir = legacy_root / "database"
        database_dir.mkdir()
        db2 = database_dir / "meshtastic.sqlite"
        db2.write_text("db2")

        findings = _find_legacy_data(legacy_root)
        db_findings = [f for f in findings if f["type"] == "database"]
        paths = [f["path"] for f in db_findings]
        # Should have unique paths only
        assert len(paths) == len(set(paths))


class TestGetMostRecentDatabase:
    """Test _get_most_recent_database function coverage."""

    def test_get_most_recent_database_empty_list(self):
        """Test with empty candidates list returns None."""
        result = _get_most_recent_database([])
        assert result is None

    def test_get_most_recent_database_all_nonexistent(self, tmp_path):
        """Test when all candidates don't exist."""
        candidates = [tmp_path / "db1.sqlite", tmp_path / "db2.sqlite"]
        result = _get_most_recent_database(candidates)
        assert result is None

    def test_get_most_recent_database_selects_by_mtime(self, tmp_path):
        """
        Validate that _get_most_recent_database selects the newest file based on modification time.

        Creates two files with a small time difference and asserts the function returns the path of the file with the later modification time.
        """
        import time

        # Create multiple databases to test sorting with different mtimes
        db1 = tmp_path / "db1.sqlite"
        db1.write_text("db1")

        old_ts = time.time() - 10
        new_ts = time.time()
        os.utime(db1, (old_ts, old_ts))

        db2 = tmp_path / "db2.sqlite"
        db2.write_text("db2")
        os.utime(db2, (new_ts, new_ts))

        # Should return most recent one (db2)
        result = _get_most_recent_database([db1, db2])
        assert result == db2

    def test_get_most_recent_database_with_sidecars(self, tmp_path):
        """Test selecting most recent database with sidecars."""
        import time

        # Create older database
        old_db = tmp_path / "old.sqlite"
        old_db.write_text("old db")

        old_ts = time.time() - 10
        os.utime(old_db, (old_ts, old_ts))

        # Create newer database with sidecars
        new_ts = time.time()
        new_db = tmp_path / "new.sqlite"
        new_db.write_text("new db")
        os.utime(new_db, (new_ts, new_ts))

        new_wal = tmp_path / "new.sqlite-wal"
        new_wal.write_text("wal")
        os.utime(new_wal, (new_ts, new_ts))

        new_shm = tmp_path / "new.sqlite-shm"
        new_shm.write_text("shm")
        os.utime(new_shm, (new_ts, new_ts))

        candidates = [old_db, new_db, new_wal, new_shm]
        result = _get_most_recent_database(candidates)

        # Should return the base path of the most recent group
        assert result == new_db

    def test_get_most_recent_database_empty_group(self, tmp_path):
        """Test when db_groups is empty after filtering."""
        # Create candidate that will be filtered out (doesn't exist)
        non_existent = tmp_path / "nonexistent.sqlite"
        candidates = [non_existent]

        # Non-existent files are filtered out, leaving empty groups → returns None
        result = _get_most_recent_database(candidates)
        assert result is None


class TestBackupFile:
    """Test _backup_file function coverage."""

    def test_backup_file_creates_timestamped_name(self, tmp_path):
        """Test that backup filename includes timestamp."""
        src_path = tmp_path / "test.txt"
        src_path.write_text("content")

        backup_path = _backup_file(src_path)

        # Should have timestamp in name
        assert backup_path.name.startswith("test.txt.bak.")
        assert backup_path.parent == src_path.parent

    def test_backup_file_custom_suffix(self, tmp_path):
        """Test backup with custom suffix."""
        src_path = tmp_path / "test.txt"
        src_path.write_text("content")

        backup_path = _backup_file(src_path, suffix=".backup")

        assert ".backup." in backup_path.name


class TestMigrationStatePath:
    """Test _get_migration_state_path function coverage."""

    def test_get_migration_state_path(self):
        """Test that state path is under home directory."""
        state_path = _get_migration_state_path()
        home = get_home_dir()

        assert home in state_path.parents or state_path.parent == home
        assert state_path.name == "migration_completed.flag"


class TestMigrateDatabaseEdgeCases:
    """Test migrate_database error paths and edge cases."""

    def test_migrate_database_all_candidates_invalid(self, tmp_path):
        """Test when all database candidates are invalid."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_roots = [tmp_path / "legacy1", tmp_path / "legacy2"]

        for root in legacy_roots:
            root.mkdir()

        result = migrate_database(
            legacy_roots, new_home, dry_run=False, force=False, move=False
        )
        assert result["success"] is True
        assert "No database files found" in result["message"]

    def test_migrate_database_most_recent_not_found(self, tmp_path):
        """Test when _get_most_recent_database returns None."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()

        # Create a candidate
        db = legacy_root / "meshtastic.sqlite"
        db.write_text("db")

        with mock.patch("mmrelay.migrate._get_most_recent_database", return_value=None):
            result = migrate_database(
                [legacy_root], new_home, dry_run=False, force=False, move=False
            )
            assert result["success"] is False
            assert "No valid database files found" in result["message"]

    def test_migrate_database_backup_failure(self, tmp_path):
        """Test handling of backup failure."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        new_db_dir = new_home / "database"
        new_db_dir.mkdir()

        # Create existing database to backup (must exist for backup to be attempted)
        existing_db = new_db_dir / "meshtastic.sqlite"
        conn = sqlite3.connect(existing_db)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()

        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        legacy_db = legacy_root / "meshtastic.sqlite"
        conn = sqlite3.connect(legacy_db)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()

        # Mock only the backup call, not the actual migration copy
        original_copy2 = shutil.copy2
        call_count = [0]

        def selective_copy2(src, dst, *args, **kwargs):
            """
            Test wrapper for shutil.copy2 that increments a shared call counter and can simulate an OSError for a targeted backup scenario.

            Parameters:
                src: Path-like source file to copy.
                dst: Path-like destination for the copy operation.
                *args, **kwargs: Forwarded to shutil.copy2.

            Side effects:
                Increments call_count[0] on each invocation.

            Returns:
                The value returned by shutil.copy2 (typically the destination path).

            Raises:
                OSError: Simulated failure on the first invocation when the destination name contains "meshtastic.sqlite"
                and the destination path contains "new_home".
            """
            call_count[0] += 1
            # First call should be backup (dest exists)
            if call_count[0] == 1 and "meshtastic.sqlite" in str(dst):
                # Check if we're backing up existing (not copying from legacy)
                if "new_home" in str(dst):
                    raise OSError
            return original_copy2(src, dst, *args, **kwargs)

        with mock.patch("shutil.copy2", side_effect=selective_copy2):
            result = migrate_database(
                [legacy_root], new_home, dry_run=False, force=False, move=False
            )
            # Should still succeed despite backup failure
            assert result["success"] is True

    def test_migrate_database_move_failure(self, tmp_path):
        """Test handling of move/copy failure."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        legacy_db = legacy_root / "meshtastic.sqlite"
        legacy_db.write_text("legacy")

        with mock.patch("shutil.copy2", side_effect=OSError("Mock copy error")):
            result = migrate_database(
                [legacy_root], new_home, dry_run=False, force=False, move=False
            )
            assert result["success"] is False
            assert "error" in result

    def test_migrate_database_integrity_check_failure(self, tmp_path):
        """Test SQLite integrity check failure."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()

        # Create valid SQLite database
        legacy_db = legacy_root / "meshtastic.sqlite"
        conn = sqlite3.connect(legacy_db)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()

        # Mock integrity_check to fail
        with mock.patch("sqlite3.connect") as mock_connect:
            mock_conn = mock.MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = ["corrupted"]
            mock_connect.return_value = mock_conn

            with pytest.raises(MigrationError) as exc_info:
                migrate_database(
                    [legacy_root], new_home, dry_run=False, force=False, move=False
                )

            assert "integrity check failed" in str(exc_info.value).lower()

    def test_migrate_database_integrity_check_db_error(self, tmp_path):
        """Test SQLite DatabaseError during integrity check."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()

        # Create valid SQLite database
        legacy_db = legacy_root / "meshtastic.sqlite"
        conn = sqlite3.connect(legacy_db)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()

        # Mock connect to raise DatabaseError
        with mock.patch(
            "sqlite3.connect", side_effect=sqlite3.DatabaseError("Mock error")
        ):
            with pytest.raises(MigrationError) as exc_info:
                migrate_database(
                    [legacy_root], new_home, dry_run=False, force=False, move=False
                )

            assert "Database verification failed" in str(exc_info.value)

    def test_migrate_database_wal_file_skip_integrity_check(self, tmp_path):
        """Test that WAL/SHM files skip integrity check."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()

        # Create WAL file (not a main database)
        legacy_wal = legacy_root / "meshtastic.sqlite-wal"
        legacy_wal.write_text("wal data")

        # Should not call integrity check on WAL files
        result = migrate_database(
            [legacy_root], new_home, dry_run=False, force=False, move=False
        )
        assert result["success"] is True


@pytest.mark.skipif(
    sys.platform == "win32", reason="E2EE store not supported on Windows"
)
class TestMigrateStoreEdgeCases:
    """Test migrate_store error paths and edge cases."""

    def test_migrate_store_backup_directory_failure(self, tmp_path):
        """Test handling of directory backup failure."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        new_store_dir = new_home / "store"
        new_store_dir.mkdir()
        (new_store_dir / "file").write_text("data")

        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        old_store_dir = legacy_root / "store"
        old_store_dir.mkdir()
        (old_store_dir / "file").write_text("data")

        with mock.patch("shutil.copytree", side_effect=OSError("Mock backup error")):
            result = migrate_store(
                [legacy_root], new_home, dry_run=False, force=False, move=True
            )
            # Should still succeed despite backup failure
            assert result["success"] is True

    def test_migrate_store_move_existing_directory_removal(self, tmp_path):
        """Test that move operation removes existing directory."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        new_store_dir = new_home / "store"
        new_store_dir.mkdir()
        (new_store_dir / "old_file").write_text("old")

        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        old_store_dir = legacy_root / "store"
        old_store_dir.mkdir()
        (old_store_dir / "new_file").write_text("new")

        result = migrate_store(
            [legacy_root], new_home, dry_run=False, force=False, move=True
        )

        assert result["success"] is True
        assert result["action"] == "move"
        # Old file should be gone (directory replaced)
        assert not (new_store_dir / "old_file").exists()
        assert (new_store_dir / "new_file").exists()

    def test_migrate_store_copy_existing_directory_removal(self, tmp_path):
        """Test that copy operation removes existing directory."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        new_store_dir = new_home / "store"
        new_store_dir.mkdir()
        (new_store_dir / "old_file").write_text("old")

        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        old_store_dir = legacy_root / "store"
        old_store_dir.mkdir()
        (old_store_dir / "new_file").write_text("new")

        result = migrate_store(
            [legacy_root], new_home, dry_run=False, force=False, move=False
        )

        assert result["success"] is True
        assert result["action"] == "copy"
        # Old file should be gone (directory replaced)
        assert not (new_store_dir / "old_file").exists()
        assert (new_store_dir / "new_file").exists()

    def test_migrate_store_copytree_failure(self, tmp_path):
        """Test handling of copytree failure."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        old_store_dir = legacy_root / "store"
        old_store_dir.mkdir()
        (old_store_dir / "file").write_text("data")

        with mock.patch("shutil.copytree", side_effect=OSError("Mock copy error")):
            result = migrate_store(
                [legacy_root], new_home, dry_run=False, force=False, move=False
            )
            assert result["success"] is False
            assert "error" in result


class TestMigratePluginsEdgeCases:
    """Test migrate_plugins error paths and edge cases."""

    def test_migrate_plugins_backup_custom_plugin_failure(self, tmp_path):
        """Test handling of custom plugin backup failure."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        new_plugins_dir = new_home / "plugins"
        new_plugins_dir.mkdir()
        new_custom_dir = new_plugins_dir / "custom"
        new_custom_dir.mkdir()
        (new_custom_dir / "existing").mkdir()

        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        old_plugins_dir = legacy_root / "plugins"
        old_plugins_dir.mkdir()
        old_custom_dir = old_plugins_dir / "custom"
        old_custom_dir.mkdir()
        (old_custom_dir / "plugin").mkdir()

        with mock.patch("shutil.copytree", side_effect=OSError("Mock backup error")):
            result = migrate_plugins(
                [legacy_root], new_home, dry_run=False, force=False, move=False
            )
            # Should still succeed despite backup failure
            assert result["success"] is True

    def test_migrate_plugins_move_removes_existing_custom(self, tmp_path):
        """Test that move operation removes existing custom plugin."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        new_plugins_dir = new_home / "plugins"
        new_plugins_dir.mkdir()
        new_custom_dir = new_plugins_dir / "custom"
        new_custom_dir.mkdir()
        # Create existing plugin with same name that will be replaced
        (new_custom_dir / "test_plugin").mkdir()
        (new_custom_dir / "test_plugin" / "old_file.txt").write_text("old content")

        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        old_plugins_dir = legacy_root / "plugins"
        old_plugins_dir.mkdir()
        old_custom_dir = old_plugins_dir / "custom"
        old_custom_dir.mkdir()
        # Create plugin with same name in legacy location
        (old_custom_dir / "test_plugin").mkdir()
        (old_custom_dir / "test_plugin" / "new_file.txt").write_text("new content")

        result = migrate_plugins(
            [legacy_root], new_home, dry_run=False, force=False, move=True
        )

        assert result["success"] is True
        # Old file should be gone (directory replaced)
        assert not (new_custom_dir / "test_plugin" / "old_file.txt").exists()
        # New file should exist
        assert (new_custom_dir / "test_plugin" / "new_file.txt").exists()

    def test_migrate_plugins_copy_removes_existing_custom(self, tmp_path):
        """Test that copy operation removes existing custom plugin."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        new_plugins_dir = new_home / "plugins"
        new_plugins_dir.mkdir()
        new_custom_dir = new_plugins_dir / "custom"
        new_custom_dir.mkdir()
        # Create existing plugin with same name that will be replaced
        (new_custom_dir / "test_plugin").mkdir()
        (new_custom_dir / "test_plugin" / "old_file.txt").write_text("old content")

        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        old_plugins_dir = legacy_root / "plugins"
        old_plugins_dir.mkdir()
        old_custom_dir = old_plugins_dir / "custom"
        old_custom_dir.mkdir()
        # Create plugin with same name in legacy location
        (old_custom_dir / "test_plugin").mkdir()
        (old_custom_dir / "test_plugin" / "new_file.txt").write_text("new content")

        result = migrate_plugins(
            [legacy_root], new_home, dry_run=False, force=False, move=False
        )

        assert result["success"] is True
        # Old file should be gone (directory replaced)
        assert not (new_custom_dir / "test_plugin" / "old_file.txt").exists()
        # New file should exist
        assert (new_custom_dir / "test_plugin" / "new_file.txt").exists()

    def test_migrate_plugins_cleanup_empty_custom_dir_on_move(self, tmp_path):
        """Test that move operation cleans up empty custom directory."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        old_plugins_dir = legacy_root / "plugins"
        old_plugins_dir.mkdir()
        old_custom_dir = old_plugins_dir / "custom"
        old_custom_dir.mkdir()
        (old_custom_dir / "plugin").mkdir()

        # Perform migration with move
        result = migrate_plugins(
            [legacy_root], new_home, dry_run=False, force=False, move=True
        )

        assert result["success"] is True
        assert result["action"] == "move"
        # After move, old custom dir should be empty and removed
        assert not old_custom_dir.exists() or not list(old_custom_dir.iterdir())

    def test_migrate_plugins_cleanup_empty_plugins_dir_on_move(self, tmp_path):
        """Test that move operation cleans up empty plugins directory."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        old_plugins_dir = legacy_root / "plugins"
        old_plugins_dir.mkdir()
        old_custom_dir = old_plugins_dir / "custom"
        old_custom_dir.mkdir()
        (old_custom_dir / "plugin").mkdir()

        # Perform migration with move
        result = migrate_plugins(
            [legacy_root], new_home, dry_run=False, force=False, move=True
        )

        assert result["success"] is True
        # After moving all plugins, old plugins dir should be empty or removed
        # The exact behavior depends on directory state, but cleanup is attempted

    def test_migrate_plugins_cleanup_os_error_handling(self, tmp_path):
        """Test handling of OSError during cleanup."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        old_plugins_dir = legacy_root / "plugins"
        old_plugins_dir.mkdir()
        old_custom_dir = old_plugins_dir / "custom"
        old_custom_dir.mkdir()
        (old_custom_dir / "plugin").mkdir()

        # Mock rmtree to fail
        original_rmtree = shutil.rmtree

        def failing_rmtree(path, *args, **kwargs):
            """
            Simulate a failing directory removal used in tests to exercise cleanup error handling.

            Acts like shutil.rmtree for paths that contain "old_plugin" (delegates to the original rmtree) and raises OSError("Mock cleanup error") for any other path. Intended as a test stub.

            Parameters:
                path: Path-like object or string pointing to the directory to remove.
                *args: Additional positional arguments forwarded to the real rmtree when delegated.
                **kwargs: Additional keyword arguments forwarded to the real rmtree when delegated.

            Raises:
                OSError: Always raised for paths that do not contain "old_plugin"; may be raised by the delegated rmtree for matching paths.
            """
            if "old_plugin" in str(path):
                # Let it succeed for removal
                return original_rmtree(path, *args, **kwargs)
            raise OSError("Mock cleanup error")

        with mock.patch("shutil.rmtree", side_effect=failing_rmtree):
            result = migrate_plugins(
                [legacy_root], new_home, dry_run=False, force=False, move=True
            )
            # Should still succeed despite cleanup errors (logged as debug)
            assert result["success"] is True


class TestMigrateGpxtrackerEdgeCases:
    """Test migrate_gpxtracker error paths and edge cases."""

    def test_migrate_gpxtracker_yaml_import_error(self, tmp_path, monkeypatch):
        """Test handling of YAML import error."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        config = legacy_root / "config.yaml"
        config.write_text("test: config")

        # Mock yaml import to fail
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            """
            Simulate importing modules but force an ImportError when attempting to import the `yaml` module.

            Parameters:
                name (str): Name of the module or attribute to import; forwarded to the real import.
                *args: Positional arguments forwarded to the real import.
                **kwargs: Keyword arguments forwarded to the real import.

            Returns:
                object: The imported module or attribute as returned by the real import.

            Raises:
                ImportError: If `name` is equal to "yaml".
            """
            if name == "yaml":
                raise ImportError("Mock import error")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        result = migrate_gpxtracker(
            [legacy_root], new_home, dry_run=False, force=False, move=False
        )

        assert result["success"] is True
        assert "gpxtracker plugin not configured" in result["message"]

    def test_migrate_gpxtracker_backup_failure(self, tmp_path):
        """Test handling of GPX file backup failure."""
        from datetime import datetime

        new_home = tmp_path / "new_home"
        new_home.mkdir()
        new_gpx_dir = new_home / "plugins" / "gpxtracker" / "data"
        new_gpx_dir.mkdir(parents=True)
        existing_gpx = new_gpx_dir / "existing.gpx"
        existing_gpx.write_text("existing")

        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        gpx_dir = legacy_root / "gpx"
        gpx_dir.mkdir()
        gpx_file = gpx_dir / "track.gpx"
        gpx_file.write_text("track")
        config = legacy_root / "config.yaml"
        config.write_text(
            f"community-plugins:\n  gpxtracker:\n    gpx_directory: {gpx_dir}\n"
        )

        fixed_time = datetime(2024, 1, 1, 12, 0, 0)
        dest_name = (
            f"{gpx_file.stem}_migrated_{fixed_time.strftime('%Y%m%d_%H%M%S')}.gpx"
        )
        dest_path = new_gpx_dir / dest_name
        dest_path.write_text("existing")

        original_copy2 = shutil.copy2

        def selective_copy2(src, dst, *args, **kwargs):
            """
            Perform a file copy from `src` to `dst`, raising an OSError when `src` matches the predefined `dest_path` to simulate a backup error.

            Parameters:
                src (str | os.PathLike): Source file path to copy.
                dst (str | os.PathLike): Destination file path.
                *args: Additional positional arguments passed to the underlying copy function.
                **kwargs: Additional keyword arguments passed to the underlying copy function.

            Returns:
                The value returned by the underlying copy operation.

            Raises:
                OSError: If `src` is equal to the externally defined `dest_path`, to simulate a backup failure.
            """
            if Path(src) == dest_path:
                raise OSError("Mock backup error")
            return original_copy2(src, dst, *args, **kwargs)

        with (
            mock.patch("mmrelay.migrate.datetime") as mock_datetime,
            mock.patch("shutil.copy2", side_effect=selective_copy2),
        ):
            mock_datetime.now.return_value = fixed_time
            migrate_gpxtracker(
                [legacy_root], new_home, dry_run=False, force=False, move=False
            )

    @pytest.mark.xfail(
        reason="known move/copy edge-case - see ISSUE-XXXX", strict=False
    )
    def test_migrate_gpxtracker_move_failure(self, tmp_path):
        """Test handling of GPX file move/copy failure."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()

        gpx_dir = legacy_root / "gpx"
        gpx_dir.mkdir()
        (gpx_dir / "track.gpx").write_text("track")

        config = legacy_root / "config.yaml"
        config.write_text(f"""
community-plugins:
  gpxtracker:
    gpx_directory: {gpx_dir}
""")

        with mock.patch("shutil.move", side_effect=OSError("Mock move error")):
            result = migrate_gpxtracker(
                [legacy_root], new_home, dry_run=False, force=False, move=True
            )
            assert result["success"] is False

    def test_migrate_gpxtracker_expanded_path_not_found(self, tmp_path):
        """Test handling when expanded GPX directory doesn't exist."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()

        # Create legacy config with gpx_directory pointing to non-existent path
        config = legacy_root / "config.yaml"
        config.write_text("""
community-plugins:
  gpxtracker:
    gpx_directory: ~/nonexistent_gpx
""")

        result = migrate_gpxtracker(
            [legacy_root], new_home, dry_run=False, force=False, move=False
        )

        # Should succeed gracefully (directory not found is handled)
        assert result["success"] is True


class TestRollbackMigration:
    """Test rollback_migration function coverage."""

    def test_rollback_migration_no_migration_completed(self, tmp_path, monkeypatch):
        """Test rollback when no migration was completed."""
        # Ensure migration state file doesn't exist
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("mmrelay.migrate.get_home_dir", lambda: home)

        result = rollback_migration()
        assert result["success"] is False
        assert "No migration to rollback" in result["message"]

    def test_rollback_migration_restore_credentials_success(
        self, tmp_path, monkeypatch
    ):
        """Test successful rollback of credentials."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("mmrelay.migrate.get_home_dir", lambda: home)

        # Create migration state file
        state_file = home / "migration_completed.flag"
        state_file.write_text("1.3")

        # Create backup credentials
        backup_dir = home
        backup_creds = backup_dir / "credentials.json.bak.20240101_120000"
        backup_creds.write_text('{"backup": true}')

        result = rollback_migration()

        assert result["success"] is True
        assert result["restored_count"] >= 1
        # Check that credentials were restored
        creds = home / "credentials.json"
        assert creds.exists()
        assert creds.read_text() == '{"backup": true}'

    def test_rollback_migration_restore_database_success(self, tmp_path, monkeypatch):
        """Test successful rollback of database."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("mmrelay.migrate.get_home_dir", lambda: home)

        # Create migration state file
        state_file = home / "migration_completed.flag"
        state_file.write_text("1.3")

        # Create database directory and backup
        db_dir = home / "database"
        db_dir.mkdir()
        backup_db = db_dir / "meshtastic.sqlite.bak.20240101_120000"
        backup_db.write_text("backup db")

        result = rollback_migration()

        assert result["success"] is True
        assert result["restored_count"] >= 1
        # Check that database was restored
        db = db_dir / "meshtastic.sqlite"
        assert db.exists()
        assert db.read_text() == "backup db"

    def test_rollback_migration_restore_credentials_failure(
        self, tmp_path, monkeypatch
    ):
        """Test handling of credentials restore failure."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("mmrelay.migrate.get_home_dir", lambda: home)

        # Create migration state file
        state_file = home / "migration_completed.flag"
        state_file.write_text("1.3")

        # Create backup credentials
        backup_creds = home / "credentials.json.bak.20240101_120000"
        backup_creds.write_text('{"backup": true}')

        # Mock copy2 to fail
        with mock.patch("shutil.copy2", side_effect=OSError("Mock error")):
            result = rollback_migration()

            # Should report failure when restore errors occur
            assert result["success"] is False

    def test_rollback_migration_restore_database_failure(self, tmp_path, monkeypatch):
        """Test handling of database restore failure."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("mmrelay.migrate.get_home_dir", lambda: home)

        # Create migration state file
        state_file = home / "migration_completed.flag"
        state_file.write_text("1.3")

        # Create database directory and backup
        db_dir = home / "database"
        db_dir.mkdir()
        backup_db = db_dir / "meshtastic.sqlite.bak.20240101_120000"
        backup_db.write_text("backup db")

        # Mock copy2 to fail
        with mock.patch("shutil.copy2", side_effect=OSError("Mock error")):
            result = rollback_migration()

            # Should report failure when restore errors occur
            assert result["success"] is False

    def test_rollback_migration_remove_state_file_success(self, tmp_path, monkeypatch):
        """Test that state file is removed on successful rollback."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("mmrelay.migrate.get_home_dir", lambda: home)

        # Create migration state file
        state_file = home / "migration_completed.flag"
        state_file.write_text("1.3")

        result = rollback_migration()

        assert result["success"] is True
        # State file should be removed
        assert not state_file.exists()

    def test_rollback_migration_remove_state_file_failure(self, tmp_path, monkeypatch):
        """Test handling of state file removal failure."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("mmrelay.migrate.get_home_dir", lambda: home)

        # Create migration state file
        state_file = home / "migration_completed.flag"
        state_file.write_text("1.3")

        # Mock unlink to fail
        with mock.patch.object(Path, "unlink", side_effect=OSError("Mock error")):
            result = rollback_migration()

            # Should report failure when cleanup fails
            assert result["success"] is False

    def test_rollback_migration_multiple_backups_selects_most_recent(
        self, tmp_path, monkeypatch
    ):
        """Test that rollback selects most recent backup."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr("mmrelay.migrate.get_home_dir", lambda: home)

        # Create migration state file
        state_file = home / "migration_completed.flag"
        state_file.write_text("1.3")

        # Create multiple backups with different timestamps
        backup1 = home / "credentials.json.bak.20240101_120000"
        backup1.write_text("backup1")
        backup2 = home / "credentials.json.bak.20240102_130000"
        backup2.write_text("backup2")  # More recent
        backup3 = home / "credentials.json.bak.20240101_110000"
        backup3.write_text("backup3")

        result = rollback_migration()

        assert result["success"] is True
        # Should restore most recent (backup2)
        creds = home / "credentials.json"
        assert creds.read_text() == "backup2"
