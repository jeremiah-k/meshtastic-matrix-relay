"""Comprehensive tests for migrate.py module covering all migration functions."""

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import mmrelay.migrate as migrate_module
import mmrelay.paths as paths_module
from mmrelay.migrate import (
    _backup_file,
    _dir_has_entries,
    _find_legacy_data,
    _get_most_recent_database,
    _is_migration_completed,
    _mark_migration_completed,
    _path_is_within_home,
    is_migration_needed,
    migrate_credentials,
    migrate_database,
    migrate_gpxtracker,
    migrate_logs,
    migrate_plugins,
    migrate_store,
    perform_migration,
    rollback_migration,
)


class TestPathIsWithinHome:
    """Tests for _path_is_within_home function (lines 72-84)."""

    def test_path_within_home(self, clean_migration_home, tmp_path):
        """Test path within home directory returns True."""
        home = clean_migration_home / "home"
        child = home / "subdir" / "file.txt"

        result = _path_is_within_home(child, home)

        assert result is True

    def test_path_is_home_itself(self, clean_migration_home, tmp_path):
        """Test path that is home directory returns True."""
        home = clean_migration_home / "home"

        result = _path_is_within_home(home, home)

        assert result is True

    def test_path_outside_home(self, clean_migration_home, tmp_path):
        """Test path outside home directory returns False."""
        home = clean_migration_home / "home"
        child = tmp_path / "other" / "file.txt"

        result = _path_is_within_home(child, home)

        assert result is False

    def test_path_resolve_uses_absolute_on_oserror(
        self, clean_migration_home, tmp_path, monkeypatch
    ):
        """Test OSError on path.resolve() falls back to absolute()."""
        home = clean_migration_home / "home"

        def mock_resolve_oserror(_self):
            raise OSError("Mock error")

        monkeypatch.setattr(Path, "resolve", mock_resolve_oserror)

        child = home / "subdir" / "file.txt"

        result = _path_is_within_home(child, home)

        # Should not raise, just use absolute()
        assert isinstance(result, bool)

    def test_home_resolve_uses_absolute_on_oserror(self, tmp_path, monkeypatch):
        """Test OSError on home.resolve() falls back to absolute()."""
        home = tmp_path / "home"

        def mock_resolve_oserror(_self):
            raise OSError("Mock error")

        monkeypatch.setattr(Path, "resolve", mock_resolve_oserror)

        child = home / "subdir"
        result = _path_is_within_home(child, home)

        # Should not raise, just use absolute()
        assert isinstance(result, bool)


class TestDirHasEntries:
    """Tests for _dir_has_entries function (lines 87-94)."""

    def test_nonexistent_directory(self, tmp_path):
        """Test non-existent directory returns False."""
        non_existent = tmp_path / "does_not_exist"

        result = _dir_has_entries(non_existent)

        assert result is False

    def test_path_exists_but_not_directory(self, tmp_path):
        """Test path that exists but is not a directory returns False."""
        file_path = tmp_path / "file.txt"
        file_path.write_text("content")

        result = _dir_has_entries(file_path)

        assert result is False

    def test_empty_directory(self, tmp_path):
        """Test empty directory returns False."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        result = _dir_has_entries(empty_dir)

        assert result is False

    def test_directory_with_files(self, tmp_path):
        """Test directory with files returns True."""
        dir_with_files = tmp_path / "has_files"
        dir_with_files.mkdir()
        (dir_with_files / "file1.txt").write_text("content1")
        (dir_with_files / "file2.txt").write_text("content2")

        result = _dir_has_entries(dir_with_files)

        assert result is True

    def test_directory_with_subdirs(self, tmp_path):
        """Test directory with subdirectories returns True."""
        dir_with_subs = tmp_path / "has_subs"
        dir_with_subs.mkdir()
        (dir_with_subs / "subdir1").mkdir()

        result = _dir_has_entries(dir_with_subs)

        assert result is True

    def test_iterdir_raises_oserror(self, tmp_path, monkeypatch):
        """Test OSError on iterdir() returns False."""
        test_dir = tmp_path / "test_dir"
        test_dir.mkdir()

        def mock_iterdir(self):
            raise OSError("Mock error")

        monkeypatch.setattr(Path, "iterdir", mock_iterdir)

        result = _dir_has_entries(test_dir)

        assert result is False


class TestFindLegacyData:
    """Tests for _find_legacy_data function (lines 97-143)."""

    def test_no_legacy_data(self, tmp_path):
        """Test legacy root with no known artifacts returns empty list."""
        empty_root = tmp_path / "legacy_empty"
        empty_root.mkdir()

        findings = _find_legacy_data(empty_root)

        assert findings == []

    def test_finds_credentials(self, tmp_path):
        """Test finds credentials.json file."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        creds = legacy_root / "credentials.json"
        creds.write_text("{}")

        findings = _find_legacy_data(legacy_root)

        assert len(findings) == 1
        assert findings[0]["type"] == "credentials"
        assert findings[0]["path"] == str(creds)

    def test_finds_database_main_file(self, tmp_path):
        """Test finds main database file."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        db = legacy_root / "meshtastic.sqlite"
        db.write_text("fake db")

        findings = _find_legacy_data(legacy_root)

        assert any(
            f["type"] == "database" and "meshtastic.sqlite" in f["path"]
            for f in findings
        )

    def test_finds_database_in_data_subdir(self, tmp_path):
        """Test finds database in data subdirectory."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        data_dir = legacy_root / "data"
        data_dir.mkdir()
        db = data_dir / "meshtastic.sqlite"
        db.write_text("fake db")

        findings = _find_legacy_data(legacy_root)

        assert any(
            f["type"] == "database" and "meshtastic.sqlite" in f["path"]
            for f in findings
        )

    def test_finds_database_wal_sidecar(self, tmp_path):
        """Test finds database WAL sidecar file."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        wal = legacy_root / "meshtastic.sqlite-wal"
        wal.write_text("fake wal")

        findings = _find_legacy_data(legacy_root)

        assert any(
            f["type"] == "database" and ".sqlite-wal" in f["path"] for f in findings
        )

    def test_finds_database_shm_sidecar(self, tmp_path):
        """Test finds database SHM sidecar file."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        shm = legacy_root / "meshtastic.sqlite-shm"
        shm.write_text("fake shm")

        findings = _find_legacy_data(legacy_root)

        assert any(
            f["type"] == "database" and ".sqlite-shm" in f["path"] for f in findings
        )

    def test_finds_logs_directory(self, tmp_path):
        """Test finds logs directory with entries."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        logs_dir = legacy_root / "logs"
        logs_dir.mkdir()
        (logs_dir / "app.log").write_text("log content")

        findings = _find_legacy_data(legacy_root)

        assert any(f["type"] == "logs" and "logs" in f["path"] for f in findings)

    def test_empty_logs_directory_not_found(self, tmp_path):
        """Test empty logs directory is not included in findings."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        logs_dir = legacy_root / "logs"
        logs_dir.mkdir()

        findings = _find_legacy_data(legacy_root)

        assert not any(f["type"] == "logs" for f in findings)

    def test_finds_store_directory(self, tmp_path):
        """Test finds E2EE store directory with entries."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        store_dir = legacy_root / "store"
        store_dir.mkdir()
        (store_dir / "store.db").write_text("store content")

        findings = _find_legacy_data(legacy_root)

        assert any(f["type"] == "e2ee_store" and "store" in f["path"] for f in findings)

    def test_empty_store_directory_not_found(self, tmp_path):
        """Test empty store directory is not included in findings."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        store_dir = legacy_root / "store"
        store_dir.mkdir()

        findings = _find_legacy_data(legacy_root)

        assert not any(f["type"] == "e2ee_store" for f in findings)

    def test_finds_plugins_directory(self, tmp_path):
        """Test finds plugins directory with entries."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        plugins_dir = legacy_root / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "custom").mkdir()

        findings = _find_legacy_data(legacy_root)

        assert any(f["type"] == "plugins" and "plugins" in f["path"] for f in findings)

    def test_empty_plugins_directory_not_found(self, tmp_path):
        """Test empty plugins directory is not included in findings."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        plugins_dir = legacy_root / "plugins"
        plugins_dir.mkdir()

        findings = _find_legacy_data(legacy_root)

        assert not any(f["type"] == "plugins" for f in findings)

    def test_multiple_artifacts_no_duplicates(self, tmp_path):
        """Test finds multiple artifacts without duplicates."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        (legacy_root / "credentials.json").write_text("{}")
        db = legacy_root / "meshtastic.sqlite"
        db.write_text("fake db")
        logs_dir = legacy_root / "logs"
        logs_dir.mkdir()
        (logs_dir / "app.log").write_text("log")

        findings = _find_legacy_data(legacy_root)

        # Count occurrences of each path
        path_counts = {}
        for finding in findings:
            path = finding["path"]
            path_counts[path] = path_counts.get(path, 0) + 1

        # No duplicates
        assert all(count == 1 for count in path_counts.values())


class TestMigrationStateFunctions:
    """Tests for migration state functions (lines 324-365)."""

    def test_is_migration_completed_no_state_file(self, tmp_path, monkeypatch):
        """Test returns False when state file doesn't exist."""
        test_home = tmp_path / "state_test_home"
        test_home.mkdir()

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: test_home)

        result = _is_migration_completed()

        assert result is False

    def test_is_migration_completed_correct_version(self, tmp_path, monkeypatch):
        """Test returns True when state file has correct version."""
        test_home = tmp_path / "completed_home"
        test_home.mkdir()
        state_file = test_home / "migration_completed.flag"
        state_file.write_text("1.3")

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: test_home)

        result = _is_migration_completed()

        assert result is True

    def test_is_migration_completed_wrong_version(self, tmp_path, monkeypatch):
        """Test returns False when state file has wrong version."""
        test_home = tmp_path / "wrong_version_home"
        test_home.mkdir()
        state_file = test_home / "migration_completed.flag"
        state_file.write_text("1.2")

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: test_home)

        result = _is_migration_completed()

        assert result is False

    def test_is_migration_completed_read_oserror(self, tmp_path, monkeypatch):
        """Test returns False and logs warning on OSError reading state file."""
        test_home = tmp_path / "read_oserror_home"
        test_home.mkdir()
        state_file = test_home / "migration_completed.flag"
        state_file.write_text("1.3")

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: test_home)

        def mock_read(*args, **kwargs):
            raise OSError("Mock read error")

        monkeypatch.setattr(paths_module, "get_home_dir", lambda: test_home)

        with patch("builtins.open", side_effect=mock_read):
            with patch("mmrelay.migrate.logger") as mock_logger:
                result = _is_migration_completed()

                assert result is False
                mock_logger.warning.assert_called_once()

    def test_mark_migration_completed_creates_file(self, tmp_path, monkeypatch):
        """Test creates state file with correct version."""
        test_home = tmp_path / "creates_home"
        test_home.mkdir()

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: test_home)

        _mark_migration_completed()

        state_file = test_home / "migration_completed.flag"
        assert state_file.exists()
        assert state_file.read_text() == "1.3"

    def test_mark_migration_completed_write_oserror(self, tmp_path, monkeypatch):
        """Test logs error on OSError writing state file."""
        test_home = tmp_path / "write_oserror_home"
        test_home.mkdir()

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: test_home)

        def mock_write(*args, **kwargs):
            raise OSError("Mock write error")

        with patch.object(Path, "write_text", side_effect=mock_write):
            with patch("mmrelay.migrate.logger") as mock_logger:
                _mark_migration_completed()

                mock_logger.error.assert_called_once()

    def test_backup_file_creates_timestamped_backup(self, tmp_path):
        """Test _backup_file creates timestamped backup path."""
        file_path = tmp_path / "test.txt"
        file_path.write_text("content")

        backup = _backup_file(file_path)

        # Backup path should have .bak. followed by timestamp
        assert ".bak." in str(backup)
        assert file_path.stem in str(backup)

    def test_backup_file_custom_suffix(self, tmp_path):
        """Test _backup_file with custom suffix."""
        file_path = tmp_path / "test.txt"
        file_path.write_text("content")

        backup = _backup_file(file_path, suffix=".backup")

        # Backup path should use custom suffix with timestamp
        assert ".backup." in str(backup)


class TestGetMostRecentDatabase:
    """Tests for _get_most_recent_database function (lines 368-413)."""

    def test_empty_candidates(self):
        """Test returns None for empty candidates."""
        result = _get_most_recent_database([])

        assert result is None

    def test_single_database(self, tmp_path):
        """Test returns single database."""
        db = tmp_path / "test.sqlite"
        db.write_text("content")

        result = _get_most_recent_database([db])

        assert result == db

    def test_multiple_databases_most_recent(self, tmp_path):
        """Test returns most recently modified database."""
        db1 = tmp_path / "old.sqlite"
        db1.write_text("old")
        import time

        old_ts = time.time() - 10
        new_ts = time.time()
        os.utime(db1, (old_ts, old_ts))
        db2 = tmp_path / "new.sqlite"
        db2.write_text("new")
        os.utime(db2, (new_ts, new_ts))

        result = _get_most_recent_database([db1, db2])

        assert result == db2

    def test_database_with_wal_sidecar(self, tmp_path):
        """Test groups database with WAL sidecar."""
        wal = tmp_path / "test.sqlite-wal"
        wal.write_text("wal content")
        db = tmp_path / "test.sqlite"
        db.write_text("db content")

        result = _get_most_recent_database([db, wal])

        # Should return main database, not sidecar
        assert result == db

    def test_database_with_wal_newer_than_db(self, tmp_path):
        """Test returns main database even if WAL is newer."""
        db = tmp_path / "test.sqlite"
        db.write_text("db content")
        wal = tmp_path / "test.sqlite-wal"
        wal.write_text("wal content")
        import time

        now = time.time()
        os.utime(db, (now - 10, now - 10))
        os.utime(wal, (now, now))

        result = _get_most_recent_database([db, wal])

        assert result == db

    def test_database_with_shm_sidecar(self, tmp_path):
        """Test groups database with SHM sidecar."""
        shm = tmp_path / "test.sqlite-shm"
        shm.write_text("shm content")
        db = tmp_path / "test.sqlite"
        db.write_text("db content")

        result = _get_most_recent_database([db, shm])

        # Should return main database, not sidecar
        assert result == db

    def test_nonexistent_database_skipped(self, tmp_path):
        """Test skips non-existent databases."""
        db = tmp_path / "exists.sqlite"
        db.write_text("content")
        missing = tmp_path / "missing.sqlite"

        result = _get_most_recent_database([db, missing])

        # Should only return existing database
        assert result == db


class TestMigrateCredentials:
    """Tests for migrate_credentials function (lines 416-494)."""

    def test_no_credentials_found(self, tmp_path, monkeypatch):
        """Test returns success when no credentials file found."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_credentials([legacy_root], new_home)

        assert result["success"] is True
        assert "No credentials file found" in result["message"]

    def test_dry_run_mode(self, tmp_path, monkeypatch):
        """Test dry run mode doesn't modify files."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        creds = legacy_root / "credentials.json"
        creds.write_text("{}")
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_credentials([legacy_root], new_home, dry_run=True)

        assert result["success"] is True
        assert result["dry_run"] is True
        assert not (new_home / "credentials.json").exists()

    def test_copy_credentials(self, tmp_path, monkeypatch):
        """Test copies credentials to new location."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        creds = legacy_root / "credentials.json"
        creds.write_text('{"token": "test"}')
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_credentials([legacy_root], new_home, move=False)

        assert result["success"] is True
        assert result["action"] == "copy"
        assert (new_home / "credentials.json").exists()
        assert creds.exists()  # Original still there
        assert (new_home / "credentials.json").read_text() == creds.read_text()

    def test_move_credentials(self, tmp_path, monkeypatch):
        """Test moves credentials to new location."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        creds = legacy_root / "credentials.json"
        creds.write_text('{"token": "test"}')
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_credentials([legacy_root], new_home, move=True)

        assert result["success"] is True
        assert result["action"] == "move"
        assert (new_home / "credentials.json").exists()
        assert not creds.exists()  # Original moved

    def test_backup_existing_credentials(self, tmp_path, monkeypatch):
        """Test backs up existing credentials."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        creds = legacy_root / "credentials.json"
        creds.write_text('{"token": "new"}')
        new_home = tmp_path / "home"
        new_home.mkdir()
        existing_creds = new_home / "credentials.json"
        existing_creds.write_text('{"token": "old"}')

        result = migrate_credentials([legacy_root], new_home, move=False)

        assert result["success"] is True
        # Backup should be created
        backups = list(new_home.glob("credentials.json.bak.*"))
        assert len(backups) == 1

    def test_force_no_backup(self, tmp_path, monkeypatch):
        """Test force mode skips backup."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        creds = legacy_root / "credentials.json"
        creds.write_text('{"token": "new"}')
        new_home = tmp_path / "home"
        new_home.mkdir()
        existing_creds = new_home / "credentials.json"
        existing_creds.write_text('{"token": "old"}')

        result = migrate_credentials([legacy_root], new_home, force=True, move=False)

        assert result["success"] is True
        # No backup should be created
        backups = list(new_home.glob("credentials.json.bak.*"))
        assert len(backups) == 0

    def test_copy_oserror(self, tmp_path, monkeypatch):
        """Test handles OSError on copy."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        creds = legacy_root / "credentials.json"
        creds.write_text("{}")
        new_home = tmp_path / "home"
        new_home.mkdir()

        with patch("shutil.copy2", side_effect=OSError("Mock error")):
            result = migrate_credentials([legacy_root], new_home, move=False)

            assert result["success"] is False
            assert "Mock error" in result["error"]

    def test_backup_oserror_logged(self, tmp_path, monkeypatch):
        """Test logs warning on backup OSError."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        creds = legacy_root / "credentials.json"
        creds.write_text('{"token": "new"}')
        new_home = tmp_path / "home"
        new_home.mkdir()
        existing_creds = new_home / "credentials.json"
        existing_creds.write_text('{"token": "old"}')

        def mock_copy_oserror(src, dst, *args, **kwargs):
            if "bak" in str(dst):
                raise OSError("Backup error")
            return None

        with patch("shutil.copy2", side_effect=mock_copy_oserror):
            with patch("mmrelay.migrate.logger") as mock_logger:
                result = migrate_credentials([legacy_root], new_home, move=False)

                # Migration should still succeed
                assert result["success"] is True
                # Warning should be logged
                mock_logger.warning.assert_called()


class TestMigrateDatabase:
    """Tests for migrate_database function (lines 497-611)."""

    def test_no_database_found(self, tmp_path, monkeypatch):
        """Test returns success when no database found."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_database([legacy_root], new_home)

        assert result["success"] is True
        assert "No database files found" in result["message"]

    def test_dry_run_mode(self, tmp_path, monkeypatch):
        """Test dry run mode doesn't modify files."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        db = legacy_root / "meshtastic.sqlite"
        db.write_text("content")
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_database([legacy_root], new_home, dry_run=True)

        assert result["success"] is True
        assert result["dry_run"] is True
        assert not (new_home / "database").exists()

    def test_copy_database_with_sidecars(self, tmp_path, monkeypatch):
        """Test copies database."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        db = legacy_root / "meshtastic.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.execute("INSERT INTO test VALUES (1)")
        conn.commit()
        conn.close()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_database([legacy_root], new_home, move=False)

        assert result["success"] is True
        assert result["action"] == "copy"
        assert (new_home / "database" / "meshtastic.sqlite").exists()

    def test_move_database(self, tmp_path, monkeypatch):
        """Test moves database."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        db = legacy_root / "meshtastic.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.execute("INSERT INTO test VALUES (1)")
        conn.commit()
        conn.close()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_database([legacy_root], new_home, move=True)

        assert result["success"] is True
        assert result["action"] == "move"
        assert (new_home / "database" / "meshtastic.sqlite").exists()
        assert not db.exists()

    def test_integrity_check_success(self, tmp_path, monkeypatch):
        """Test database integrity check passes."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        db_path = legacy_root / "meshtastic.sqlite"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.execute("INSERT INTO test VALUES (1)")
        conn.commit()
        conn.close()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_database([legacy_root], new_home, move=False)

        assert result["success"] is True

    def test_integrity_check_failure(self, tmp_path, monkeypatch):
        """Test raises MigrationError on integrity check failure."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        db_path = legacy_root / "meshtastic.sqlite"
        # Create corrupt database
        db_path.write_bytes(b"corrupt data")
        new_home = tmp_path / "home"
        new_home.mkdir()

        from mmrelay.migrate import MigrationError

        with pytest.raises(MigrationError, match="Database verification failed"):
            migrate_database([legacy_root], new_home, move=False)

    def test_most_recent_database_selected(self, tmp_path, monkeypatch):
        """Test selects most recent database from multiple candidates."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        data_dir = legacy_root / "data"
        data_dir.mkdir()

        # Create old database
        old_db = legacy_root / "meshtastic.sqlite"
        conn = sqlite3.connect(old_db)
        conn.execute("CREATE TABLE old_table (id INTEGER)")
        conn.execute("INSERT INTO old_table VALUES (1)")
        conn.commit()
        conn.close()

        # Set deterministic timestamps - newer database should be newer
        base_time = tmp_path.stat().st_mtime
        old_ts = base_time - 100  # 100 seconds older
        new_ts = base_time

        # Create newer database in data subdirectory
        new_db = data_dir / "meshtastic.sqlite"
        conn = sqlite3.connect(new_db)
        conn.execute("CREATE TABLE new_table (id INTEGER)")
        conn.execute("INSERT INTO new_table VALUES (2)")
        conn.commit()
        conn.close()

        # Set modification times deterministically
        os.utime(old_db, (old_ts, old_ts))
        os.utime(new_db, (new_ts, new_ts))

        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_database([legacy_root], new_home, move=False)

        # Should migrate the newer database
        assert result["success"] is True
        migrated_db = new_home / "database" / "meshtastic.sqlite"
        assert migrated_db.exists()
        # Verify it has the new table
        conn = sqlite3.connect(migrated_db)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor]
        conn.close()
        assert "new_table" in tables


class TestMigrateLogs:
    """Tests for migrate_logs function (lines 614-695)."""

    def test_no_logs_directory(self, tmp_path, monkeypatch):
        """Test returns success when no logs directory found."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_logs([legacy_root], new_home)

        assert result["success"] is True
        assert "No logs directory found" in result["message"]

    def test_dry_run_mode(self, tmp_path, monkeypatch):
        """Test dry run mode doesn't modify files."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        logs_dir = legacy_root / "logs"
        logs_dir.mkdir()
        (logs_dir / "app.log").write_text("log content")
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_logs([legacy_root], new_home, dry_run=True)

        assert result["success"] is True
        assert result["dry_run"] is True
        assert not (new_home / "logs").exists()

    def test_copy_log_files(self, tmp_path, monkeypatch):
        """Test copies log files with timestamped names."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        logs_dir = legacy_root / "logs"
        logs_dir.mkdir()
        (logs_dir / "app.log").write_text("log 1")
        (logs_dir / "debug.log").write_text("log 2")
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_logs([legacy_root], new_home, move=False)

        assert result["success"] is True
        assert result["action"] == "copy"
        assert result["migrated_count"] == 2

    def test_move_log_files(self, tmp_path, monkeypatch):
        """Test moves log files."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        logs_dir = legacy_root / "logs"
        logs_dir.mkdir()
        (logs_dir / "app.log").write_text("log content")
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_logs([legacy_root], new_home, move=True)

        assert result["success"] is True
        assert result["action"] == "move"
        assert (new_home / "logs").exists()
        assert not (legacy_root / "logs" / "app.log").exists()

    def test_timestamped_log_names(self, tmp_path, monkeypatch):
        """Test log files get timestamped names."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        logs_dir = legacy_root / "logs"
        logs_dir.mkdir()
        (logs_dir / "app.log").write_text("content")
        new_home = tmp_path / "home"
        new_home.mkdir()

        migrate_logs([legacy_root], new_home, move=False)

        migrated_logs = list((new_home / "logs").glob("*_migrated_*.log"))
        assert len(migrated_logs) == 1
        assert "_migrated_" in migrated_logs[0].name


class TestMigrateStore:
    """Tests for migrate_store function (lines 698-789)."""

    def test_windows_skip(self, tmp_path, monkeypatch):
        """Test skips store migration on Windows."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        store_dir = legacy_root / "store"
        store_dir.mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        with patch("sys.platform", "win32"):
            result = migrate_store([legacy_root], new_home)

            assert result["success"] is True
            assert "E2EE not supported on Windows" in result["message"]

    def test_no_store_directory(self, tmp_path, monkeypatch):
        """Test returns success when no store directory found."""
        if sys.platform == "win32":
            pytest.skip("E2EE not supported on Windows")

        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_store([legacy_root], new_home)

        assert result["success"] is True
        assert "No E2EE store directory found" in result["message"]

    def test_dry_run_mode(self, tmp_path, monkeypatch):
        """Test dry run mode doesn't modify files."""
        if sys.platform == "win32":
            pytest.skip("E2EE not supported on Windows")

        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        store_dir = legacy_root / "store"
        store_dir.mkdir()
        (store_dir / "store.db").write_text("store")
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_store([legacy_root], new_home, dry_run=True)

        assert result["success"] is True
        assert result["dry_run"] is True
        assert not (new_home / "store").exists()

    def test_copy_store_directory(self, tmp_path, monkeypatch):
        """Test copies store directory."""
        if sys.platform == "win32":
            pytest.skip("E2EE not supported on Windows")

        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        store_dir = legacy_root / "store"
        store_dir.mkdir()
        (store_dir / "store.db").write_text("store content")
        (store_dir / "keys").mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_store([legacy_root], new_home, move=False)

        assert result["success"] is True
        assert result["action"] == "copy"
        assert (new_home / "store" / "store.db").exists()
        assert (new_home / "store" / "keys").exists()

    def test_move_store_directory(self, tmp_path, monkeypatch):
        """Test moves store directory."""
        if sys.platform == "win32":
            pytest.skip("E2EE not supported on Windows")

        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        store_dir = legacy_root / "store"
        store_dir.mkdir()
        (store_dir / "store.db").write_text("store content")
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_store([legacy_root], new_home, move=True)

        assert result["success"] is True
        assert result["action"] == "move"
        assert (new_home / "store" / "store.db").exists()
        assert not store_dir.exists()


class TestMigratePlugins:
    """Tests for migrate_plugins function (lines 792-914)."""

    def test_no_plugins_directory(self, tmp_path, monkeypatch):
        """Test returns success when no plugins directory found."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_plugins([legacy_root], new_home)

        assert result["success"] is True
        assert "No plugins directory found" in result["message"]

    def test_dry_run_mode(self, tmp_path, monkeypatch):
        """Test dry run mode doesn't modify files."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        plugins_dir = legacy_root / "plugins"
        plugins_dir.mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_plugins([legacy_root], new_home, dry_run=True)

        assert result["success"] is True
        assert result["dry_run"] is True
        assert not (new_home / "plugins").exists()

    def test_migrate_custom_plugins(self, tmp_path, monkeypatch):
        """Test migrates custom plugins."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        plugins_dir = legacy_root / "plugins"
        plugins_dir.mkdir()
        custom_dir = plugins_dir / "custom"
        custom_dir.mkdir()
        (custom_dir / "plugin1").mkdir()
        (custom_dir / "plugin2").mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_plugins([legacy_root], new_home, move=False)

        assert result["success"] is True
        assert "custom" in result["migrated_types"]
        assert (new_home / "plugins" / "custom" / "plugin1").exists()
        assert (new_home / "plugins" / "custom" / "plugin2").exists()

    def test_migrate_community_plugins(self, tmp_path, monkeypatch):
        """Test migrates community plugins."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        plugins_dir = legacy_root / "plugins"
        plugins_dir.mkdir()
        community_dir = plugins_dir / "community"
        community_dir.mkdir()
        (community_dir / "plugin1").mkdir()
        (community_dir / "plugin2").mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_plugins([legacy_root], new_home, move=False)

        assert result["success"] is True
        assert "community" in result["migrated_types"]
        assert (new_home / "plugins" / "community" / "plugin1").exists()
        assert (new_home / "plugins" / "community" / "plugin2").exists()

    def test_move_plugins(self, tmp_path, monkeypatch):
        """Test moves plugins."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        plugins_dir = legacy_root / "plugins"
        plugins_dir.mkdir()
        custom_dir = plugins_dir / "custom"
        custom_dir.mkdir()
        (custom_dir / "plugin1").mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_plugins([legacy_root], new_home, move=True)

        assert result["success"] is True
        assert result["action"] == "move"
        assert (new_home / "plugins" / "custom" / "plugin1").exists()
        assert not custom_dir.exists()


class TestMigrateGpxtracker:
    """Tests for migrate_gpxtracker function (lines 917-1039)."""

    def test_no_config_file(self, tmp_path, monkeypatch):
        """Test returns success when no config file found."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_gpxtracker([legacy_root], new_home)

        assert result["success"] is True
        assert "gpx_directory, skipping migration" in result["message"]

    def test_invalid_yaml(self, tmp_path, monkeypatch):
        """Test handles invalid YAML in config."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        config = legacy_root / "config.yaml"
        config.write_text("invalid: yaml: [unclosed")
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_gpxtracker([legacy_root], new_home)

        assert result["success"] is True
        assert "gpx_directory, skipping migration" in result["message"]

    def test_no_gpx_directory_configured(self, tmp_path, monkeypatch):
        """Test returns success when gpx_directory not configured."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        config = legacy_root / "config.yaml"
        config.write_text("community-plugins:\n  other:\n    active: true")
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_gpxtracker([legacy_root], new_home)

        assert result["success"] is True
        assert "gpx_directory, skipping migration" in result["message"]

    def test_gpx_directory_doesnt_exist(self, tmp_path, monkeypatch):
        """Test handles non-existent configured gpx_directory."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        config = legacy_root / "config.yaml"
        config.write_text(
            "community-plugins:\n  gpxtracker:\n    gpx_directory: /nonexistent/path"
        )
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_gpxtracker([legacy_root], new_home)

        assert result["success"] is True
        # Should handle gracefully

    def test_dry_run_mode(self, tmp_path, monkeypatch):
        """Test dry run mode doesn't modify files."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        config = legacy_root / "config.yaml"
        config.write_text("community-plugins:\n  gpxtracker:\n    gpx_directory: ~/gpx")
        gpx_dir = tmp_path / "gpx"
        gpx_dir.mkdir()
        (gpx_dir / "track.gpx").write_text("gpx content")
        new_home = tmp_path / "home"
        new_home.mkdir()

        with patch("pathlib.Path.expanduser", return_value=gpx_dir):
            result = migrate_gpxtracker([legacy_root], new_home, dry_run=True)

            assert result["success"] is True
            assert result["dry_run"] is True
            assert not (new_home / "plugins" / "gpxtracker" / "data").exists()

    def test_copy_gpx_files(self, tmp_path, monkeypatch):
        """Test copies GPX files."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        config = legacy_root / "config.yaml"
        config.write_text("community-plugins:\n  gpxtracker:\n    gpx_directory: ~/gpx")
        gpx_dir = tmp_path / "gpx"
        gpx_dir.mkdir()
        (gpx_dir / "track1.gpx").write_text("gpx 1")
        (gpx_dir / "track2.gpx").write_text("gpx 2")
        new_home = tmp_path / "home"
        new_home.mkdir()

        with patch("pathlib.Path.expanduser", return_value=gpx_dir):
            result = migrate_gpxtracker([legacy_root], new_home, move=False)

            assert result["success"] is True
            assert result["action"] == "copy"
            assert result["migrated_count"] == 2


class TestIsMigrationNeeded:
    """Tests for is_migration_needed function (lines 1042-1047)."""

    def test_migration_completed(self, tmp_path, monkeypatch):
        """Test returns False when migration already completed."""
        home = tmp_path / "home"
        home.mkdir()
        state_file = home / "migration_completed.flag"
        state_file.write_text("1.3")

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: home)
        monkeypatch.setattr(
            migrate_module, "resolve_all_paths", lambda: {"legacy_sources": []}
        )

        result = is_migration_needed()

        assert result is False

    def test_legacy_sources_exist(self, tmp_path, monkeypatch):
        """Test returns True when legacy sources exist."""
        home = tmp_path / "home"
        home.mkdir()

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: home)
        monkeypatch.setattr(
            migrate_module,
            "resolve_all_paths",
            lambda: {"legacy_sources": [str(tmp_path / "legacy")]},
        )

        result = is_migration_needed()

        assert result is True

    def test_no_legacy_and_not_completed(self, tmp_path, monkeypatch):
        """Test returns False when no legacy and not completed."""
        home = tmp_path / "home"
        home.mkdir()

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: home)
        monkeypatch.setattr(
            migrate_module, "resolve_all_paths", lambda: {"legacy_sources": []}
        )

        result = is_migration_needed()

        assert result is False


class TestPerformMigration:
    """Tests for perform_migration function (lines 1050-1194)."""

    def test_dry_run_mode(self, tmp_path, monkeypatch):
        """Test dry run mode."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        (legacy_root / "credentials.json").write_text("{}")
        new_home = tmp_path / "home"
        new_home.mkdir()

        monkeypatch.setenv("MMRELAY_HOME", str(new_home))
        monkeypatch.setattr(
            migrate_module,
            "resolve_all_paths",
            lambda: {"home": str(new_home), "legacy_sources": [str(legacy_root)]},
        )

        result = perform_migration(dry_run=True)

        assert result["success"] is True
        assert result["dry_run"] is True

    def test_no_legacy_installation(self, tmp_path, monkeypatch):
        """Test returns success when no legacy installation."""
        new_home = tmp_path / "home"
        new_home.mkdir()
        (new_home / "credentials.json").write_text("{}")

        monkeypatch.setenv("MMRELAY_HOME", str(new_home))
        monkeypatch.setattr(
            migrate_module,
            "resolve_all_paths",
            lambda: {"home": str(new_home), "legacy_sources": []},
        )

        result = perform_migration()

        assert result["success"] is True
        assert "No legacy installation detected" in result["message"]
        assert len(result["migrations"]) == 0

    def test_full_migration_success(self, tmp_path, monkeypatch):
        """Test complete migration with all components."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        (legacy_root / "credentials.json").write_text("{}")
        db = legacy_root / "meshtastic.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.commit()
        conn.close()
        logs_dir = legacy_root / "logs"
        logs_dir.mkdir()
        (logs_dir / "app.log").write_text("log")
        new_home = tmp_path / "home"

        monkeypatch.setenv("MMRELAY_HOME", str(new_home))
        monkeypatch.setattr(
            migrate_module,
            "resolve_all_paths",
            lambda: {"home": str(new_home), "legacy_sources": [str(legacy_root)]},
        )

        result = perform_migration(move=True, force=True)

        assert result["success"] is True
        assert "Migration completed successfully" in result["message"]
        assert len(result["migrations"]) >= 3

    def test_migration_creates_home_directory(self, tmp_path, monkeypatch):
        """Test creates home directory if it doesn't exist."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        (legacy_root / "credentials.json").write_text("{}")
        new_home = tmp_path / "home"

        monkeypatch.setenv("MMRELAY_HOME", str(new_home))
        monkeypatch.setattr(
            migrate_module,
            "resolve_all_paths",
            lambda: {"home": str(new_home), "legacy_sources": [str(legacy_root)]},
        )

        result = perform_migration(move=True, force=True)

        assert result["success"] is True
        assert new_home.exists()


class TestRollbackMigration:
    """Tests for rollback_migration function (lines 1197-1246)."""

    def test_no_migration_to_rollback(self, tmp_path, monkeypatch):
        """Test returns error when migration not completed."""
        new_home = tmp_path / "home"
        new_home.mkdir()

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: new_home)

        result = rollback_migration()

        assert result["success"] is False
        assert "No migration to rollback" in result["message"]

    def test_restores_credentials(self, tmp_path, monkeypatch):
        """Test restores credentials from backup."""
        new_home = tmp_path / "home"
        new_home.mkdir()
        creds = new_home / "credentials.json"
        creds.write_text('{"token": "new"}')
        backup = new_home / "credentials.json.bak.20230101_120000"
        backup.write_text('{"token": "backup"}')

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: new_home)

        # Mark migration as completed
        state_file = new_home / "migration_completed.flag"
        state_file.write_text("1.3")

        result = rollback_migration()

        assert result["success"] is True
        assert creds.read_text() == '{"token": "backup"}'
        assert not state_file.exists()

    def test_restores_database(self, tmp_path, monkeypatch):
        """Test restores database from backup."""
        new_home = tmp_path / "home"
        new_home.mkdir()
        db_dir = new_home / "database"
        db_dir.mkdir()
        db = db_dir / "meshtastic.sqlite"
        db.write_text("current")
        backup = db_dir / "meshtastic.sqlite.bak.20230101_120000"
        backup.write_text("backup db")

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: new_home)

        # Mark migration as completed
        state_file = new_home / "migration_completed.flag"
        state_file.write_text("1.3")

        result = rollback_migration()

        assert result["success"] is True
        assert db.read_text() == "backup db"
        assert result["restored_count"] > 0

    def test_handles_multiple_backups(self, tmp_path, monkeypatch):
        """Test handles multiple backup files."""
        new_home = tmp_path / "home"
        new_home.mkdir()
        creds = new_home / "credentials.json"
        creds.write_text('{"token": "new"}')
        (new_home / "credentials.json.bak.20230101_120000").write_text(
            '{"token": "old1"}'
        )
        (new_home / "credentials.json.bak.20230102_120000").write_text(
            '{"token": "old2"}'
        )

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: new_home)

        # Mark migration as completed
        state_file = new_home / "migration_completed.flag"
        state_file.write_text("1.3")

        result = rollback_migration()

        assert result["success"] is True
        assert result["restored_count"] > 0

    def test_restore_oserror_logged(self, tmp_path, monkeypatch):
        """Test logs warning on restore OSError."""
        new_home = tmp_path / "home"
        new_home.mkdir()
        state_file = new_home / "migration_completed.flag"
        state_file.write_text("1.3")
        backup = new_home / "credentials.json.bak.20230101_120000"
        backup.write_text('{"token": "backup"}')

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: new_home)

        with patch("shutil.copy2", side_effect=OSError("Restore error")):
            with patch("mmrelay.migrate.logger") as mock_logger:
                result = rollback_migration()

                assert result["success"] is True
                mock_logger.warning.assert_called()

    def test_removes_state_file(self, tmp_path, monkeypatch):
        """Test removes migration state file on rollback."""
        new_home = tmp_path / "home"
        new_home.mkdir()
        state_file = new_home / "migration_completed.flag"
        state_file.write_text("1.3")

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: new_home)

        rollback_migration()

        assert not state_file.exists()
