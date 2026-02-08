"""Comprehensive tests for migrate.py module covering all migration functions."""

import os
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import mmrelay.migrate as migrate_module
from mmrelay.migrate import (
    _backup_file,
    _dir_has_entries,
    _find_legacy_data,
    _get_most_recent_database,
    _path_is_within_home,
    is_migration_needed,
    migrate_config,
    migrate_credentials,
    migrate_database,
    migrate_gpxtracker,
    migrate_logs,
    migrate_plugins,
    migrate_store,
    perform_migration,
)


class TestPathIsWithinHome:
    """Tests for _path_is_within_home function (lines 72-84)."""

    def test_path_within_home(self, clean_migration_home: Path) -> None:
        """Test path within home directory returns True."""
        home = clean_migration_home / "home"
        child = home / "subdir" / "file.txt"

        result = _path_is_within_home(child, home)

        assert result is True

    def test_path_is_home_itself(self, clean_migration_home: Path) -> None:
        """Test path that is home directory returns True."""
        home = clean_migration_home / "home"

        result = _path_is_within_home(home, home)

        assert result is True

    def test_path_outside_home(
        self, clean_migration_home: Path, tmp_path: Path
    ) -> None:
        """Test path outside home directory returns False."""
        home = clean_migration_home / "home"
        child = tmp_path / "other" / "file.txt"

        result = _path_is_within_home(child, home)

        assert result is False

    def test_path_resolve_uses_absolute_on_oserror(
        self, clean_migration_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test OSError on path.resolve() falls back to absolute()."""
        home = clean_migration_home / "home"

        def mock_resolve_oserror(_self):
            """
            Simulate a Path.resolve failure by always raising OSError.

            Raises:
                OSError: Always raised with the message "Mock error".
            """
            raise OSError("Mock error")

        monkeypatch.setattr(Path, "resolve", mock_resolve_oserror)

        child = home / "subdir" / "file.txt"

        result = _path_is_within_home(child, home)

        # Should not raise, just use absolute()
        assert isinstance(result, bool)

    def test_home_resolve_uses_absolute_on_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test OSError on home.resolve() falls back to absolute()."""
        home = tmp_path / "home"

        def mock_resolve_oserror(_self):
            """
            Simulate a Path.resolve failure by always raising OSError.

            Raises:
                OSError: Always raised with the message "Mock error".
            """
            raise OSError("Mock error")

        monkeypatch.setattr(Path, "resolve", mock_resolve_oserror)

        child = home / "subdir"
        result = _path_is_within_home(child, home)

        # Should not raise, just use absolute()
        assert isinstance(result, bool)


class TestDirHasEntries:
    """Tests for _dir_has_entries function (lines 87-94)."""

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        """Test non-existent directory returns False."""
        non_existent = tmp_path / "does_not_exist"

        result = _dir_has_entries(non_existent)

        assert result is False

    def test_path_exists_but_not_directory(self, tmp_path: Path) -> None:
        """Test path that exists but is not a directory returns False."""
        file_path = tmp_path / "file.txt"
        file_path.write_text("content")

        result = _dir_has_entries(file_path)

        assert result is False

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Test empty directory returns False."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        result = _dir_has_entries(empty_dir)

        assert result is False

    def test_directory_with_files(self, tmp_path: Path) -> None:
        """Test directory with files returns True."""
        dir_with_files = tmp_path / "has_files"
        dir_with_files.mkdir()
        (dir_with_files / "file1.txt").write_text("content1")
        (dir_with_files / "file2.txt").write_text("content2")

        result = _dir_has_entries(dir_with_files)

        assert result is True

    def test_directory_with_subdirs(self, tmp_path: Path) -> None:
        """Test directory with subdirectories returns True."""
        dir_with_subs = tmp_path / "has_subs"
        dir_with_subs.mkdir()
        (dir_with_subs / "subdir1").mkdir()

        result = _dir_has_entries(dir_with_subs)

        assert result is True

    def test_iterdir_raises_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test OSError on iterdir() returns False."""
        test_dir = tmp_path / "test_dir"
        test_dir.mkdir()

        def mock_iterdir(self):
            """
            Simulate a failing Path.iterdir by raising an OSError.

            Raises:
                OSError: Always raised to emulate an iterdir failure in tests.
            """
            raise OSError("Mock error")

        monkeypatch.setattr(Path, "iterdir", mock_iterdir)

        result = _dir_has_entries(test_dir)

        assert result is False


class TestFindLegacyData:
    """Tests for _find_legacy_data function (lines 97-143)."""

    def test_no_legacy_data(self, tmp_path: Path) -> None:
        """Test legacy root with no known artifacts returns empty list."""
        empty_root = tmp_path / "legacy_empty"
        empty_root.mkdir()

        findings = _find_legacy_data(empty_root)

        assert findings == []

    def test_finds_credentials(self, tmp_path: Path) -> None:
        """Test finds credentials.json file."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        creds = legacy_root / "credentials.json"
        creds.write_text("{}")

        findings = _find_legacy_data(legacy_root)

        assert len(findings) == 1
        assert findings[0]["type"] == "credentials"
        assert findings[0]["path"] == str(creds)

    def test_finds_database_main_file(self, tmp_path: Path) -> None:
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

    def test_finds_database_in_data_subdir(self, tmp_path: Path) -> None:
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

    def test_finds_database_wal_sidecar(self, tmp_path: Path) -> None:
        """Test finds database WAL sidecar file."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        wal = legacy_root / "meshtastic.sqlite-wal"
        wal.write_text("fake wal")

        findings = _find_legacy_data(legacy_root)

        assert any(
            f["type"] == "database" and ".sqlite-wal" in f["path"] for f in findings
        )

    def test_finds_database_shm_sidecar(self, tmp_path: Path) -> None:
        """Test finds database SHM sidecar file."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        shm = legacy_root / "meshtastic.sqlite-shm"
        shm.write_text("fake shm")

        findings = _find_legacy_data(legacy_root)

        assert any(
            f["type"] == "database" and ".sqlite-shm" in f["path"] for f in findings
        )

    def test_finds_logs_directory(self, tmp_path: Path) -> None:
        """Test finds logs directory with entries."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        logs_dir = legacy_root / "logs"
        logs_dir.mkdir()
        (logs_dir / "app.log").write_text("log content")

        findings = _find_legacy_data(legacy_root)

        assert any(f["type"] == "logs" and "logs" in f["path"] for f in findings)

    def test_empty_logs_directory_not_found(self, tmp_path: Path) -> None:
        """Test empty logs directory is not included in findings."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        logs_dir = legacy_root / "logs"
        logs_dir.mkdir()

        findings = _find_legacy_data(legacy_root)

        assert not any(f["type"] == "logs" for f in findings)

    def test_finds_store_directory(self, tmp_path: Path) -> None:
        """Test finds E2EE store directory with entries."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        store_dir = legacy_root / "store"
        store_dir.mkdir()
        (store_dir / "store.db").write_text("store content")

        findings = _find_legacy_data(legacy_root)

        assert any(f["type"] == "e2ee_store" and "store" in f["path"] for f in findings)

    def test_empty_store_directory_not_found(self, tmp_path: Path) -> None:
        """Test empty store directory is not included in findings."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        store_dir = legacy_root / "store"
        store_dir.mkdir()

        findings = _find_legacy_data(legacy_root)

        assert not any(f["type"] == "e2ee_store" for f in findings)

    def test_finds_plugins_directory(self, tmp_path: Path) -> None:
        """Test finds plugins directory with entries."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        plugins_dir = legacy_root / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "custom").mkdir()

        findings = _find_legacy_data(legacy_root)

        assert any(f["type"] == "plugins" and "plugins" in f["path"] for f in findings)

    def test_empty_plugins_directory_not_found(self, tmp_path: Path) -> None:
        """Test empty plugins directory is not included in findings."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        plugins_dir = legacy_root / "plugins"
        plugins_dir.mkdir()

        findings = _find_legacy_data(legacy_root)

        assert not any(f["type"] == "plugins" for f in findings)

    def test_multiple_artifacts_no_duplicates(self, tmp_path: Path) -> None:
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

    def test_backup_file_creates_timestamped_backup(self, tmp_path: Path) -> None:
        """Test _backup_file creates timestamped backup path."""
        file_path = tmp_path / "test.txt"
        file_path.write_text("content")

        backup = _backup_file(file_path)

        # Backup path should have .bak. followed by timestamp
        assert ".bak." in str(backup)
        assert file_path.stem in str(backup)

    def test_backup_file_custom_suffix(self, tmp_path: Path) -> None:
        """Test _backup_file with custom suffix."""
        file_path = tmp_path / "test.txt"
        file_path.write_text("content")

        backup = _backup_file(file_path, suffix=".backup")

        # Backup path should use custom suffix with timestamp
        assert ".backup." in str(backup)


class TestGetMostRecentDatabase:
    """Tests for _get_most_recent_database function (lines 368-413)."""

    def test_empty_candidates(self) -> None:
        """Test returns None for empty candidates."""
        result = _get_most_recent_database([])

        assert result is None

    def test_single_database(self, tmp_path: Path) -> None:
        """Test returns single database."""
        db = tmp_path / "test.sqlite"
        db.write_text("content")

        result = _get_most_recent_database([db])

        assert result == db

    def test_multiple_databases_most_recent(self, tmp_path: Path) -> None:
        """Test returns most recently modified database."""
        db1 = tmp_path / "old.sqlite"
        db1.write_text("old")

        old_ts = time.time() - 10
        new_ts = time.time()
        os.utime(db1, (old_ts, old_ts))
        db2 = tmp_path / "new.sqlite"
        db2.write_text("new")
        os.utime(db2, (new_ts, new_ts))

        result = _get_most_recent_database([db1, db2])

        assert result == db2

    def test_database_with_wal_sidecar(self, tmp_path: Path) -> None:
        """Test groups database with WAL sidecar."""
        wal = tmp_path / "test.sqlite-wal"
        wal.write_text("wal content")
        db = tmp_path / "test.sqlite"
        db.write_text("db content")

        result = _get_most_recent_database([db, wal])

        # Should return main database, not sidecar
        assert result == db

    def test_database_with_wal_newer_than_db(self, tmp_path: Path) -> None:
        """Test returns main database even if WAL is newer."""
        db = tmp_path / "test.sqlite"
        db.write_text("db content")
        wal = tmp_path / "test.sqlite-wal"
        wal.write_text("wal content")

        now = time.time()
        os.utime(db, (now - 10, now - 10))
        os.utime(wal, (now, now))

        result = _get_most_recent_database([db, wal])

        assert result == db

    def test_database_with_shm_sidecar(self, tmp_path: Path) -> None:
        """Test groups database with SHM sidecar."""
        shm = tmp_path / "test.sqlite-shm"
        shm.write_text("shm content")
        db = tmp_path / "test.sqlite"
        db.write_text("db content")

        result = _get_most_recent_database([db, shm])

        # Should return main database, not sidecar
        assert result == db

    def test_nonexistent_database_skipped(self, tmp_path: Path) -> None:
        """Test skips non-existent databases."""
        db = tmp_path / "exists.sqlite"
        db.write_text("content")
        missing = tmp_path / "missing.sqlite"

        result = _get_most_recent_database([db, missing])

        # Should only return existing database
        assert result == db


class TestMigrateCredentials:
    """Tests for migrate_credentials function (lines 416-494)."""

    def test_no_credentials_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test returns success when no credentials file found."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_credentials([legacy_root], new_home)

        assert result["success"] is True
        assert "No credentials file found" in result["message"]

    def test_dry_run_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
        assert not (new_home / "matrix" / "credentials.json").exists()

    def test_migrate_credentials_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test moves credentials to new location."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        creds = legacy_root / "credentials.json"
        creds.write_text('{"token": "test"}')
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_credentials([legacy_root], new_home)

        assert result["success"] is True
        assert result["action"] == "move"
        assert (new_home / "matrix" / "credentials.json").exists()
        assert not creds.exists()  # Original moved
        assert (
            new_home / "matrix" / "credentials.json"
        ).read_text() == '{"token": "test"}'

    def test_backup_existing_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test backs up existing credentials."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        creds = legacy_root / "credentials.json"
        creds.write_text('{"token": "new"}')
        new_home = tmp_path / "home"
        new_home.mkdir()
        matrix_dir = new_home / "matrix"
        matrix_dir.mkdir()
        existing_creds = matrix_dir / "credentials.json"
        existing_creds.write_text('{"token": "old"}')

        result = migrate_credentials([legacy_root], new_home)

        assert result["success"] is True
        # Backup should be created
        backups = list((new_home / "matrix").glob("credentials.json.bak.*"))
        assert len(backups) == 1

    def test_force_no_backup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test force mode skips backup."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        creds = legacy_root / "credentials.json"
        creds.write_text('{"token": "new"}')
        new_home = tmp_path / "home"
        new_home.mkdir()
        matrix_dir = new_home / "matrix"
        matrix_dir.mkdir()
        existing_creds = matrix_dir / "credentials.json"
        existing_creds.write_text('{"token": "old"}')

        result = migrate_credentials([legacy_root], new_home, force=True)

        assert result["success"] is True
        # No backup should be created
        backups = list((new_home / "matrix").glob("credentials.json.bak.*"))
        assert len(backups) == 0

    def test_move_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test handles OSError on move."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        creds = legacy_root / "credentials.json"
        creds.write_text("{}")
        new_home = tmp_path / "home"
        new_home.mkdir()

        with patch("shutil.move", side_effect=OSError("Mock error")):
            result = migrate_credentials([legacy_root], new_home)

            assert result["success"] is False
            assert "Mock error" in result["error"]

    def test_backup_oserror_logged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test logs warning on backup OSError."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        creds = legacy_root / "credentials.json"
        creds.write_text('{"token": "new"}')
        new_home = tmp_path / "home"
        new_home.mkdir()
        matrix_dir = new_home / "matrix"
        matrix_dir.mkdir()
        existing_creds = matrix_dir / "credentials.json"
        existing_creds.write_text('{"token": "old"}')

        def mock_copy_oserror(src, dst, *args, **kwargs):
            """
            Simulates a file copy that fails with an OSError when the destination path contains "bak".

            Parameters:
                src: Source path (ignored by this mock).
                dst: Destination path; if `str(dst)` contains "bak", the function raises an OSError.

            Raises:
                OSError: if "bak" appears in the destination path to simulate a backup-related failure.
            """
            if "bak" in str(dst):
                raise OSError("Backup error")
            return None

        with patch("shutil.copy2", side_effect=mock_copy_oserror):
            with patch("mmrelay.migrate.logger") as mock_logger:
                result = migrate_credentials([legacy_root], new_home)

                # Migration should fail if backup fails (safety first)
                assert result["success"] is False
                assert "Failed to backup credentials" in result["error"]
                # Error should be logged
                mock_logger.exception.assert_called()


class TestMigrateConfig:
    """Tests for migrate_config function."""

    def test_no_config_found(self, tmp_path: Path) -> None:
        """Test returns success when no config.yaml found."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_config([legacy_root], new_home)

        assert result["success"] is True
        assert "No config.yaml found" in result["message"]

    def test_dry_run_mode(self, tmp_path: Path) -> None:
        """Test dry run mode doesn't modify files."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        config_file = legacy_root / "config.yaml"
        config_file.write_text("matrix: {}")
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_config([legacy_root], new_home, dry_run=True)

        assert result["success"] is True
        assert result["dry_run"] is True
        assert not (new_home / "config.yaml").exists()

    def test_migrate_config_success(self, tmp_path: Path) -> None:
        """Test moves config.yaml to new location."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        config_file = legacy_root / "config.yaml"
        config_file.write_text("matrix: {}")
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_config([legacy_root], new_home)

        assert result["success"] is True
        assert result["action"] == "move"
        assert (new_home / "config.yaml").exists()
        assert not config_file.exists()

    def test_backup_existing_config(self, tmp_path: Path) -> None:
        """Test backs up existing config.yaml."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        config_file = legacy_root / "config.yaml"
        config_file.write_text("matrix: {}")
        new_home = tmp_path / "home"
        new_home.mkdir()
        existing_config = new_home / "config.yaml"
        existing_config.write_text("old: true")

        result = migrate_config([legacy_root], new_home)

        assert result["success"] is True
        backups = list(new_home.glob("config.yaml.bak.*"))
        assert len(backups) == 1


class TestMigrateDatabase:
    """Tests for migrate_database function (lines 497-611)."""

    def test_no_database_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test returns success when no database found."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_database([legacy_root], new_home)

        assert result["success"] is True
        assert "No database files found" in result["message"]

    def test_dry_run_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_migrate_database_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        result = migrate_database([legacy_root], new_home)

        assert result["success"] is True
        assert result["action"] == "move"
        assert (new_home / "database" / "meshtastic.sqlite").exists()
        assert not db.exists()

    def test_integrity_check_failure_prevents_data_loss(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that migration failure on integrity check failure prevents data loss."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        db_path = legacy_root / "meshtastic.sqlite"
        # Create corrupt database
        db_path.write_bytes(b"corrupt data")
        new_home = tmp_path / "home"
        new_home.mkdir()

        from mmrelay.migrate import MigrationError

        with pytest.raises(MigrationError, match="Database verification failed"):
            migrate_database([legacy_root], new_home)

        assert db_path.exists(), "Source database was deleted - data loss!"
        dest_db = new_home / "database" / "meshtastic.sqlite"
        assert not dest_db.exists(), "Corrupted database was left at destination"

    def test_integrity_check_corrupted_prevents_data_loss(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that integrity check returning corrupted prevents data loss."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        db_path = legacy_root / "meshtastic.sqlite"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.commit()
        conn.close()
        new_home = tmp_path / "home"
        new_home.mkdir()

        from mmrelay.migrate import MigrationError

        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = ["corrupted"]
            mock_connect.return_value = mock_conn

            with pytest.raises(MigrationError, match="integrity check failed"):
                migrate_database([legacy_root], new_home)

        assert db_path.exists(), "Source database was deleted - data loss!"
        dest_db = new_home / "database" / "meshtastic.sqlite"
        assert not dest_db.exists(), "Corrupted database was left at destination"

    def test_most_recent_database_selected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        result = migrate_database([legacy_root], new_home)

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

    def test_already_at_target_location(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test database already at target location returns success."""
        new_home = tmp_path / "home"
        new_home.mkdir()
        db_dir = new_home / "database"
        db_dir.mkdir()
        db_path = db_dir / "meshtastic.sqlite"
        db_path.write_text("content")

        result = migrate_database([new_home], new_home)

        assert result["success"] is True
        assert result["action"] == "none"
        assert "already at target location" in result["message"]

    def test_target_exists_skips_without_force(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test skips database migration if target exists and force=False."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        (legacy_root / "meshtastic.sqlite").write_text("legacy")

        new_home = tmp_path / "home"
        new_home.mkdir()
        db_dir = new_home / "database"
        db_dir.mkdir()
        db_path = db_dir / "meshtastic.sqlite"
        db_path.write_text("existing")

        result = migrate_database([legacy_root], new_home, force=False)

        assert result["success"] is True
        assert result["action"] == "none"
        assert "already exists at destination" in result["message"]
        assert db_path.read_text() == "existing"


class TestMigrateLogs:
    """Tests for migrate_logs function (lines 614-695)."""

    def test_no_logs_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test returns success when no logs directory found."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_logs([legacy_root], new_home)

        assert result["success"] is True
        assert "No logs directory found" in result["message"]

    def test_dry_run_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_migrate_logs_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test moves log files with timestamped names."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        logs_dir = legacy_root / "logs"
        logs_dir.mkdir()
        (logs_dir / "app.log").write_text("log 1")
        (logs_dir / "debug.log").write_text("log 2")
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_logs([legacy_root], new_home)

        assert result["success"] is True
        assert result["action"] == "move"
        assert result["migrated_count"] == 2
        assert not (logs_dir / "app.log").exists()
        assert not (logs_dir / "debug.log").exists()

    def test_already_at_target_location(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test logs already at target location returns success."""
        new_home = tmp_path / "home"
        new_home.mkdir()
        logs_dir = new_home / "logs"
        logs_dir.mkdir()
        (logs_dir / "app.log").write_text("content")

        result = migrate_logs([new_home], new_home)

        assert result["success"] is True
        assert result["action"] == "none"
        assert "already at target location" in result["message"]

    def test_target_exists_skips_without_force(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test skips logs migration if target exists and force=False."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        old_logs = legacy_root / "logs"
        old_logs.mkdir()
        (old_logs / "app.log").write_text("legacy")

        new_home = tmp_path / "home"
        new_home.mkdir()
        new_logs = new_home / "logs"
        new_logs.mkdir()
        (new_logs / "existing.log").write_text("existing")

        result = migrate_logs([legacy_root], new_home, force=False)

        assert result["success"] is True
        assert result["action"] == "none"
        assert "already exists at destination" in result["message"]
        assert (new_logs / "existing.log").read_text() == "existing"

    def test_timestamped_log_names(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test log files get timestamped names."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        logs_dir = legacy_root / "logs"
        logs_dir.mkdir()
        (logs_dir / "app.log").write_text("content")
        new_home = tmp_path / "home"
        new_home.mkdir()

        migrate_logs([legacy_root], new_home)

        migrated_logs = list((new_home / "logs").glob("*_migrated_*.log"))
        assert len(migrated_logs) == 1
        assert "_migrated_" in migrated_logs[0].name


class TestMigrateStore:
    """Tests for migrate_store function (lines 698-789)."""

    def test_windows_skip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_no_store_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_dry_run_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
        assert not (new_home / "matrix" / "store").exists()

    def test_migrate_store_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test moves store directory."""
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

        result = migrate_store([legacy_root], new_home)

        assert result["success"] is True
        assert result["action"] == "move"
        assert (new_home / "matrix" / "store" / "store.db").exists()
        assert (new_home / "matrix" / "store" / "keys").exists()
        assert not store_dir.exists()


class TestMigratePlugins:
    """Tests for migrate_plugins function (lines 792-914)."""

    def test_no_plugins_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test returns success when no plugins directory found."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_plugins([legacy_root], new_home)

        assert result["success"] is True
        assert "No plugins directory found" in result["message"]

    def test_dry_run_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_migrate_custom_plugins_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        result = migrate_plugins([legacy_root], new_home)

        assert result["success"] is True
        assert result["action"] == "move"
        assert "custom" in result["migrated_types"]
        assert (new_home / "plugins" / "custom" / "plugin1").exists()
        assert (new_home / "plugins" / "custom" / "plugin2").exists()
        assert not custom_dir.exists()
        assert not plugins_dir.exists()

    def test_migrate_community_plugins_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        result = migrate_plugins([legacy_root], new_home)

        assert result["success"] is True
        assert result["action"] == "move"
        assert "community" in result["migrated_types"]
        assert (new_home / "plugins" / "community" / "plugin1").exists()
        assert (new_home / "plugins" / "community" / "plugin2").exists()
        assert not community_dir.exists()
        assert not plugins_dir.exists()

    def test_already_at_target_location(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test plugins already at target location returns success."""
        new_home = tmp_path / "home"
        new_home.mkdir()
        plugins_dir = new_home / "plugins"
        plugins_dir.mkdir()

        result = migrate_plugins([new_home], new_home)

        assert result["success"] is True
        assert result["action"] == "none"
        assert "already at target location" in result["message"]

    def test_target_exists_skips_without_force(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test skips plugins migration if target exists and force=False."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        old_plugins = legacy_root / "plugins"
        old_plugins.mkdir()
        (old_plugins / "custom").mkdir()

        new_home = tmp_path / "home"
        new_home.mkdir()
        new_plugins = new_home / "plugins"
        new_plugins.mkdir()

        result = migrate_plugins([legacy_root], new_home, force=False)

        assert result["success"] is True
        assert result["action"] == "none"
        assert "already exists at destination" in result["message"]


class TestMigrateGpxtracker:
    """Tests for migrate_gpxtracker function (lines 917-1039)."""

    def test_no_config_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test returns success when no config file found."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_gpxtracker([legacy_root], new_home)

        assert result["success"] is True
        assert "gpx_directory, skipping migration" in result["message"]

    def test_invalid_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_no_gpx_directory_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_gpx_directory_doesnt_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_dry_run_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
            assert not (
                new_home / "plugins" / "community" / "gpxtracker" / "data"
            ).exists()

    def test_migrate_gpx_files_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test moves GPX files."""
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
            result = migrate_gpxtracker([legacy_root], new_home)

            assert result["success"] is True
            assert result["action"] == "move"
            assert result["migrated_count"] == 2
            expected_dir = new_home / "plugins" / "community" / "gpxtracker" / "data"
            assert result["new_path"] == str(expected_dir)
            assert expected_dir.exists()
            # Original files should be moved (deleted from source)
            assert not (gpx_dir / "track1.gpx").exists()
            assert not (gpx_dir / "track2.gpx").exists()


class TestIsMigrationNeeded:
    """Tests for is_migration_needed function (lines 1042-1047)."""

    def test_migration_completed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Verify that is_migration_needed returns False when a migration completion state file with the current version exists in the home directory.
        """
        home = tmp_path / "home"
        home.mkdir()

        # In v1.3, is_migration_needed uses verify_migration instead of a flag file
        # We simulate a "completed" migration by having no legacy data and credentials present
        (home / "matrix").mkdir(parents=True)
        (home / "matrix" / "credentials.json").write_text("{}")

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: home)
        monkeypatch.setattr(
            migrate_module,
            "resolve_all_paths",
            lambda: {
                "home": str(home),
                "legacy_sources": [],
                "credentials_path": str(home / "matrix" / "credentials.json"),
                "database_dir": str(home / "database"),
                "logs_dir": str(home / "logs"),
                "plugins_dir": str(home / "plugins"),
                "store_dir": str(home / "matrix" / "store"),
            },
        )

        result = is_migration_needed()

        assert result is False

    def test_legacy_sources_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test returns True when legacy sources exist."""
        home = tmp_path / "home"
        home.mkdir()
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        (legacy_root / "credentials.json").write_text("{}")

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: home)
        monkeypatch.setattr(
            migrate_module,
            "resolve_all_paths",
            lambda: {
                "home": str(home),
                "legacy_sources": [str(legacy_root)],
                "credentials_path": str(home / "matrix" / "credentials.json"),
                "database_dir": str(home / "database"),
                "logs_dir": str(home / "logs"),
                "plugins_dir": str(home / "plugins"),
                "store_dir": str(home / "matrix" / "store"),
            },
        )

        result = is_migration_needed()

        assert result is True

    def test_no_legacy_and_not_completed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test returns False when no legacy and not completed."""
        home = tmp_path / "home"
        home.mkdir()
        # Credentials must exist for verify_migration to report 'ok'
        (home / "matrix").mkdir(parents=True)
        (home / "matrix" / "credentials.json").write_text("{}")

        monkeypatch.setattr(migrate_module, "get_home_dir", lambda: home)
        monkeypatch.setattr(
            migrate_module,
            "resolve_all_paths",
            lambda: {
                "home": str(home),
                "legacy_sources": [],
                "credentials_path": str(home / "matrix" / "credentials.json"),
                "database_dir": str(home / "database"),
                "logs_dir": str(home / "logs"),
                "plugins_dir": str(home / "plugins"),
                "store_dir": str(home / "matrix" / "store"),
            },
        )

        result = is_migration_needed()

        assert result is False


class TestPerformMigration:
    """Tests for perform_migration function (lines 1050-1194)."""

    def test_dry_run_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        result = perform_migration(dry_run=True)

        assert result["success"] is True
        assert result["dry_run"] is True

    def test_no_legacy_installation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test returns success when no legacy installation."""
        new_home = tmp_path / "home"
        new_home.mkdir()
        (new_home / "matrix").mkdir()
        (new_home / "matrix" / "credentials.json").write_text("{}")

        monkeypatch.setenv("MMRELAY_HOME", str(new_home))
        monkeypatch.setattr(
            migrate_module,
            "resolve_all_paths",
            lambda: {
                "home": str(new_home),
                "legacy_sources": [],
                "credentials_path": str(new_home / "matrix" / "credentials.json"),
                "database_dir": str(new_home / "database"),
                "logs_dir": str(new_home / "logs"),
                "plugins_dir": str(new_home / "plugins"),
                "store_dir": str(new_home / "matrix" / "store"),
            },
        )

        result = perform_migration()

        assert result["success"] is True
        assert "No legacy installation detected" in result["message"]
        assert len(result["migrations"]) == 0

    def test_full_migration_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        result = perform_migration(force=True)

        assert result["success"] is True
        assert "Migration completed successfully" in result["message"]
        assert len(result["migrations"]) >= 3

    def test_migration_creates_home_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test creates home directory if it doesn't exist."""
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        (legacy_root / "credentials.json").write_text("{}")
        new_home = tmp_path / "home"

        monkeypatch.setenv("MMRELAY_HOME", str(new_home))
        monkeypatch.setattr(
            migrate_module,
            "resolve_all_paths",
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

        result = perform_migration(force=True)

        assert result["success"] is True
        assert new_home.exists()
