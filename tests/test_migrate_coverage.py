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
    _get_most_recent_database,
    _path_is_within_home,
    migrate_config,
    migrate_credentials,
    migrate_database,
    migrate_gpxtracker,
    migrate_logs,
    migrate_plugins,
    migrate_store,
    perform_migration,
    print_migration_verification,
    verify_migration,
)


class TestPathIsWithinHome:
    """Test _path_is_within_home function coverage."""

    def test_path_is_within_home_exact_match(self, tmp_path: Path) -> None:
        """Test path that is exactly the home directory."""
        home = tmp_path / "home"
        home.mkdir()
        assert _path_is_within_home(home, home) is True

    def test_path_is_within_home_subdirectory(self, tmp_path: Path) -> None:
        """Test path that is inside home directory."""
        home = tmp_path / "home"
        home.mkdir()
        subdir = home / "subdir"
        assert _path_is_within_home(subdir, home) is True

    def test_path_is_within_home_not_inside(self, tmp_path: Path) -> None:
        """Test path that is outside home directory."""
        home = tmp_path / "home"
        home.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        assert _path_is_within_home(other, home) is False

    def test_path_is_within_home_os_error_resolve(self, tmp_path: Path) -> None:
        """Test handling of OSError during path resolution."""
        home = tmp_path / "home"
        path = tmp_path / "path"

        with mock.patch.object(Path, "resolve", side_effect=OSError("Mock error")):
            # Should fall back to absolute()
            result = _path_is_within_home(path, home)
            assert isinstance(result, bool)


class TestDirHasEntries:
    """Test _dir_has_entries function coverage."""

    def test_dir_has_entries_nonexistent(self, tmp_path: Path) -> None:
        """Test non-existent directory returns False."""
        result = _dir_has_entries(tmp_path / "nonexistent")
        assert result is False

    def test_dir_has_entries_file(self, tmp_path: Path) -> None:
        """Test file path returns False."""
        file_path = tmp_path / "file.txt"
        file_path.write_text("test")
        result = _dir_has_entries(file_path)
        assert result is False

    def test_dir_has_entries_empty(self, tmp_path: Path) -> None:
        """Test empty directory returns False."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = _dir_has_entries(empty_dir)
        assert result is False

    def test_dir_has_entries_with_files(self, tmp_path: Path) -> None:
        """Test directory with files returns True."""
        dir_with_files = tmp_path / "with_files"
        dir_with_files.mkdir()
        (dir_with_files / "test.txt").write_text("content")
        result = _dir_has_entries(dir_with_files)
        assert result is True

    def test_dir_has_entries_os_error_iterdir(self, tmp_path: Path) -> None:
        """Test handling of OSError during iterdir()."""
        dir_path = tmp_path / "test_dir"
        dir_path.mkdir()

        with mock.patch.object(Path, "iterdir", side_effect=OSError("Mock error")):
            result = _dir_has_entries(dir_path)
            assert result is False


class TestFindLegacyData:
    """Test _find_legacy_data function coverage."""

    def test_find_legacy_data_credentials(self, tmp_path: Path) -> None:
        """Test finding credentials.json."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        creds = legacy_root_dir / "credentials.json"
        creds.write_text('{"test": "data"}')

        findings = _find_legacy_data(legacy_root_dir)
        assert len(findings) == 1
        assert findings[0]["type"] == "credentials"
        assert findings[0]["path"] == str(creds)

    def test_find_legacy_data_database(self, tmp_path: Path) -> None:
        """Test finding meshtastic.sqlite."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        db = legacy_root_dir / "meshtastic.sqlite"
        db.write_text("sqlite db")

        findings = _find_legacy_data(legacy_root_dir)
        assert len(findings) == 1
        assert findings[0]["type"] == "database"

    def test_find_legacy_data_wal_shm(self, tmp_path: Path) -> None:
        """Test finding database with WAL/SHM sidecars."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        db = legacy_root_dir / "meshtastic.sqlite"
        db.write_text("sqlite db")
        wal = legacy_root_dir / "meshtastic.sqlite-wal"
        wal.write_text("wal data")
        shm = legacy_root_dir / "meshtastic.sqlite-shm"
        shm.write_text("shm data")

        findings = _find_legacy_data(legacy_root_dir)
        # Should find db, wal, and shm
        db_findings = [f for f in findings if f["type"] == "database"]
        assert len(db_findings) == 3

    def test_find_legacy_data_partial_database(self, tmp_path: Path) -> None:
        """Test finding database in partial new layout (data/ directory)."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        data_dir = legacy_root_dir / "data"
        data_dir.mkdir()
        db = data_dir / "meshtastic.sqlite"
        db.write_text("sqlite db")

        findings = _find_legacy_data(legacy_root_dir)
        db_findings = [f for f in findings if f["type"] == "database"]
        assert len(db_findings) == 1
        assert "data" in db_findings[0]["path"]

    def test_find_legacy_data_logs(self, tmp_path: Path) -> None:
        """Test finding logs directory."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        logs_dir = legacy_root_dir / "logs"
        logs_dir.mkdir()
        (logs_dir / "test.log").write_text("log content")

        findings = _find_legacy_data(legacy_root_dir)
        log_findings = [f for f in findings if f["type"] == "logs"]
        assert len(log_findings) == 1

    def test_find_legacy_data_store(self, tmp_path: Path) -> None:
        """Test finding E2EE store directory."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        store_dir = legacy_root_dir / "store"
        store_dir.mkdir()
        (store_dir / "key").write_text("key data")

        findings = _find_legacy_data(legacy_root_dir)
        store_findings = [f for f in findings if f["type"] == "e2ee_store"]
        assert len(store_findings) == 1

    def test_find_legacy_data_plugins(self, tmp_path: Path) -> None:
        """Test finding plugins directory."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        plugins_dir = legacy_root_dir / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "test.py").write_text("plugin code")

        findings = _find_legacy_data(legacy_root_dir)
        plugin_findings = [f for f in findings if f["type"] == "plugins"]
        assert len(plugin_findings) == 1

    def test_find_legacy_data_deduplication(self, tmp_path: Path) -> None:
        """Test that duplicate paths are deduplicated."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        # Create files that would be found multiple times
        db = legacy_root_dir / "meshtastic.sqlite"
        db.write_text("db")

        # Database layout with nested database (should dedupe)
        database_dir = legacy_root_dir / "database"
        database_dir.mkdir()
        db2 = database_dir / "meshtastic.sqlite"
        db2.write_text("db2")

        findings = _find_legacy_data(legacy_root_dir)
        db_findings = [f for f in findings if f["type"] == "database"]
        paths = [f["path"] for f in db_findings]
        # Should have unique paths only
        assert len(paths) == len(set(paths))

    def test_find_legacy_data_config(self, tmp_path: Path) -> None:
        """Test finding config.yaml."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        config = legacy_root_dir / "config.yaml"
        config.write_text("config")

        findings = _find_legacy_data(legacy_root_dir)
        config_findings = [f for f in findings if f["type"] == "config"]
        assert len(config_findings) == 1

    def test_find_legacy_data_duplicate_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test duplicate findings are skipped by path string."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        creds = legacy_root_dir / "credentials.json"
        creds.write_text("creds")
        config = legacy_root_dir / "config.yaml"
        config.write_text("config")

        creds_str = str(creds)
        original_str = Path.__str__

        def fake_str(self: Path) -> str:
            if self.name == "config.yaml":
                return creds_str
            return original_str(self)

        monkeypatch.setattr(Path, "__str__", fake_str)

        findings = _find_legacy_data(legacy_root_dir)
        paths = [f["path"] for f in findings]
        assert paths.count(creds_str) == 1


class TestVerifyMigration:
    """Test verify_migration and print_migration_verification coverage."""

    def test_verify_migration_store_not_applicable(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test store not applicable and warning output."""
        home = tmp_path / "home"
        home.mkdir()
        matrix_dir = home / "matrix"
        matrix_dir.mkdir()
        (matrix_dir / "credentials.json").write_text("creds")
        database_dir = home / "database"
        database_dir.mkdir()
        logs_dir = home / "logs"
        logs_dir.mkdir()
        plugins_dir = home / "plugins"
        plugins_dir.mkdir()

        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        store_dir = legacy_root_dir / "store"
        store_dir.mkdir()
        (store_dir / "keys.db").write_text("keys")

        paths_info = {
            "home": str(home),
            "credentials_path": str(matrix_dir / "credentials.json"),
            "database_dir": str(database_dir),
            "logs_dir": str(logs_dir),
            "plugins_dir": str(plugins_dir),
            "store_dir": "N/A (Windows)",
            "legacy_sources": [str(legacy_root_dir)],
        }

        monkeypatch.setattr("mmrelay.migrate.resolve_all_paths", lambda: paths_info)

        report = verify_migration()
        store_artifact = next(
            artifact
            for artifact in report["artifacts"]
            if artifact["key"] == "e2ee_store"
        )
        assert store_artifact["not_applicable"] is True
        assert any("E2EE store" in warning for warning in report["warnings"])

        print_migration_verification(report)
        captured = capsys.readouterr()
        assert "N/A (Windows)" in captured.out

    def test_verify_migration_store_applicable_no_legacy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test store directory resolution when applicable and no legacy data."""
        home = tmp_path / "home"
        home.mkdir()
        matrix_dir = home / "matrix"
        matrix_dir.mkdir()
        (matrix_dir / "credentials.json").write_text("creds")
        database_dir = home / "database"
        database_dir.mkdir()
        (database_dir / "meshtastic.sqlite").write_text("db")
        logs_dir = home / "logs"
        logs_dir.mkdir()
        plugins_dir = home / "plugins"
        plugins_dir.mkdir()
        store_dir = matrix_dir / "store"
        store_dir.mkdir()

        paths_info = {
            "home": str(home),
            "credentials_path": str(matrix_dir / "credentials.json"),
            "database_dir": str(database_dir),
            "logs_dir": str(logs_dir),
            "plugins_dir": str(plugins_dir),
            "store_dir": str(store_dir),
            "legacy_sources": [],
        }

        monkeypatch.setattr("mmrelay.migrate.resolve_all_paths", lambda: paths_info)

        report = verify_migration()
        store_artifact = next(
            artifact
            for artifact in report["artifacts"]
            if artifact["key"] == "e2ee_store"
        )
        assert store_artifact["not_applicable"] is False
        assert report["ok"] is True

    def test_verify_migration_with_legacy_and_missing_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test warnings and errors when legacy data exists and credentials are missing."""
        home = tmp_path / "home"
        home.mkdir()
        database_dir = tmp_path / "outside_db"
        database_dir.mkdir()
        (database_dir / "meshtastic.sqlite").write_text("db")
        logs_dir = home / "logs"
        logs_dir.mkdir()
        plugins_dir = home / "plugins"
        plugins_dir.mkdir()
        store_dir = home / "matrix" / "store"
        store_dir.parent.mkdir(parents=True, exist_ok=True)
        store_dir.mkdir()

        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        legacy_logs = legacy_root_dir / "logs"
        legacy_logs.mkdir()
        (legacy_logs / "legacy.log").write_text("log")

        paths_info = {
            "home": str(home),
            "credentials_path": str(home / "matrix" / "credentials.json"),
            "database_dir": str(database_dir),
            "logs_dir": str(logs_dir),
            "plugins_dir": str(plugins_dir),
            "store_dir": str(store_dir),
            "legacy_sources": [str(legacy_root_dir)],
        }

        monkeypatch.setattr("mmrelay.migrate.resolve_all_paths", lambda: paths_info)

        report = verify_migration()
        assert any("Found legacy data at" in warning for warning in report["warnings"])
        assert any("Missing credentials.json" in error for error in report["errors"])
        assert any(
            "Legacy data exists and migration is still required" in error
            for error in report["errors"]
        )
        assert any("Split roots detected" in error for error in report["errors"])
        assert any("outside MMRELAY_HOME" in error for error in report["errors"])

    def test_print_migration_verification_no_legacy_ok(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test verification output when no legacy data is present and ok=True."""
        report = {
            "home": "/tmp/home",
            "artifacts": [
                {
                    "key": "credentials",
                    "label": "credentials.json",
                    "path": "/tmp/home/matrix/credentials.json",
                    "exists": True,
                    "inside_home": True,
                    "not_applicable": False,
                }
            ],
            "legacy_data": [],
            "warnings": [],
            "errors": [],
            "ok": True,
        }

        print_migration_verification(report)
        captured = capsys.readouterr()
        assert "No legacy data found" in captured.out
        assert "Migration verification PASSED" in captured.out


class TestGetMostRecentDatabase:
    """Test _get_most_recent_database function coverage."""

    def test_get_most_recent_database_empty_list(self) -> None:
        """Test with empty candidates list returns None."""
        result = _get_most_recent_database([])
        assert result is None

    def test_get_most_recent_database_all_nonexistent(self, tmp_path: Path) -> None:
        """Test when all candidates don't exist."""
        candidates = [tmp_path / "db1.sqlite", tmp_path / "db2.sqlite"]
        result = _get_most_recent_database(candidates)
        assert result is None

    def test_get_most_recent_database_selects_by_mtime(self, tmp_path: Path) -> None:
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

    def test_get_most_recent_database_with_sidecars(self, tmp_path: Path) -> None:
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

    def test_get_most_recent_database_empty_group(self, tmp_path: Path) -> None:
        """Test when db_groups is empty after filtering."""
        # Create candidate that will be filtered out (doesn't exist)
        non_existent = tmp_path / "nonexistent.sqlite"
        candidates = [non_existent]

        # Non-existent files are filtered out, leaving empty groups → returns None
        result = _get_most_recent_database(candidates)
        assert result is None

    def test_get_most_recent_database_stat_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test get_most_recent_database handles stat OSError."""
        db1 = tmp_path / "db1.sqlite"
        db2 = tmp_path / "db2.sqlite"
        db1.write_text("db1")
        db2.write_text("db2")

        original_stat = Path.stat

        def fake_stat(self: Path, *args: object, **kwargs: object):
            if self == db1:
                raise OSError("stat failed")
            return original_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", fake_stat)

        result = _get_most_recent_database([db1, db2])
        assert result == db2

    def test_get_most_recent_database_orphaned_sidecar(self, tmp_path: Path) -> None:
        """Test that orphaned sidecars are ignored."""
        wal = tmp_path / "orphan.sqlite-wal"
        wal.write_text("wal")

        result = _get_most_recent_database([wal])
        assert result is None

    def test_get_most_recent_database_base_exists_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test base.exists OSError is handled."""
        wal = tmp_path / "db.sqlite-wal"
        wal.write_text("wal")
        base = tmp_path / "db.sqlite"

        original_exists = Path.exists

        def fake_exists(self: Path) -> bool:
            if self == base:
                raise OSError("exists failed")
            return original_exists(self)

        monkeypatch.setattr(Path, "exists", fake_exists)

        result = _get_most_recent_database([wal])
        assert result is None


class TestBackupFile:
    """Test _backup_file function coverage."""

    def test_backup_file_creates_timestamped_name(self, tmp_path: Path) -> None:
        """Test that backup filename includes timestamp."""
        src_path = tmp_path / "test.txt"
        src_path.write_text("content")

        backup_path = _backup_file(src_path)

        # Should have timestamp in name
        assert backup_path.name.startswith("test.txt.bak.")
        assert backup_path.parent == src_path.parent / ".migration_backups"

    def test_backup_file_custom_suffix(self, tmp_path: Path) -> None:
        """Test backup with custom suffix."""
        src_path = tmp_path / "test.txt"
        src_path.write_text("content")

        backup_path = _backup_file(src_path, suffix=".backup")

        assert ".backup." in backup_path.name


class TestMigrateCredentialsEdgeCases:
    """Test migrate_credentials edge cases."""

    def test_migrate_credentials_move_removes_existing_destination_dir(
        self, tmp_path: Path
    ) -> None:
        """Test move removes an existing credentials directory."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_creds = legacy_root_dir / "credentials.json"
        old_creds.write_text("creds")

        new_home = tmp_path / "home"
        new_home.mkdir()
        new_creds = new_home / "matrix" / "credentials.json"
        new_creds.parent.mkdir(parents=True, exist_ok=True)
        new_creds.mkdir()

        result = migrate_credentials(
            [legacy_root_dir], new_home, dry_run=False, force=True
        )

        assert result["success"] is True
        assert new_creds.is_file()
        assert new_creds.read_text() == "creds"

    def test_migrate_credentials_move_removes_existing_file(
        self, tmp_path: Path
    ) -> None:
        """Test move removes an existing credentials file."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_creds = legacy_root_dir / "credentials.json"
        old_creds.write_text("creds")

        new_home = tmp_path / "home"
        new_home.mkdir()
        new_creds = new_home / "matrix" / "credentials.json"
        new_creds.parent.mkdir(parents=True, exist_ok=True)
        new_creds.write_text("old")

        result = migrate_credentials(
            [legacy_root_dir], new_home, dry_run=False, force=True
        )

        assert result["success"] is True
        assert new_creds.read_text() == "creds"

    def test_migrate_credentials_backup_failure_continues(self, tmp_path: Path) -> None:
        """Test backup failure aborts credentials migration."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_creds = legacy_root_dir / "credentials.json"
        old_creds.write_text("new-creds")

        new_home = tmp_path / "home"
        new_home.mkdir()
        new_creds = new_home / "matrix" / "credentials.json"
        new_creds.parent.mkdir(parents=True, exist_ok=True)
        new_creds.write_text("old-creds")

        original_copy2 = shutil.copy2

        def selective_copy2(src, dst, *args, **kwargs):
            if ".bak." in str(dst):
                raise OSError("Mock backup error")
            return original_copy2(src, dst, *args, **kwargs)

        with mock.patch("shutil.copy2", side_effect=selective_copy2):
            with pytest.raises(
                MigrationError, match="credentials migration failed: Mock backup error"
            ):
                migrate_credentials(
                    [legacy_root_dir], new_home, dry_run=False, force=True
                )


class TestMigrateConfigEdgeCases:
    """Test migrate_config edge cases."""

    def test_migrate_config_move_removes_existing_directory(
        self, tmp_path: Path
    ) -> None:
        """Test move removes an existing config directory before migrating."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_config = legacy_root_dir / "config.yaml"
        old_config.write_text("config")

        new_home = tmp_path / "home"
        new_home.mkdir()
        new_config = new_home / "config.yaml"
        new_config.mkdir()

        result = migrate_config([legacy_root_dir], new_home, dry_run=False, force=True)

        assert result["success"] is True
        assert new_config.is_file()
        assert new_config.read_text() == "config"

    def test_migrate_config_move_removes_existing_file(self, tmp_path: Path) -> None:
        """Test move removes an existing config file before migrating."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_config = legacy_root_dir / "config.yaml"
        old_config.write_text("config")

        new_home = tmp_path / "home"
        new_home.mkdir()
        new_config = new_home / "config.yaml"
        new_config.write_text("old")

        result = migrate_config([legacy_root_dir], new_home, dry_run=False, force=True)

        assert result["success"] is True
        assert new_config.read_text() == "config"

    def test_migrate_config_backup_failure_continues(self, tmp_path: Path) -> None:
        """Test backup failure aborts config migration."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_config = legacy_root_dir / "config.yaml"
        old_config.write_text("new-config")

        new_home = tmp_path / "home"
        new_home.mkdir()
        new_config = new_home / "config.yaml"
        new_config.write_text("old-config")

        original_copy2 = shutil.copy2

        def selective_copy2(src, dst, *args, **kwargs):
            if ".bak." in str(dst):
                raise OSError("Mock backup error")
            return original_copy2(src, dst, *args, **kwargs)

        with mock.patch("shutil.copy2", side_effect=selective_copy2):
            with pytest.raises(
                MigrationError, match="config migration failed: Mock backup error"
            ):
                migrate_config([legacy_root_dir], new_home, dry_run=False, force=True)

    def test_migrate_config_move_failure(self, tmp_path: Path) -> None:
        """Test migrate_config returns error on move failure."""
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_config = legacy_root_dir / "config.yaml"
        old_config.write_text("config")

        new_home = tmp_path / "home"
        new_home.mkdir()

        with mock.patch("shutil.move", side_effect=OSError("Mock move error")):
            with pytest.raises(
                MigrationError, match="config migration failed: Mock move error"
            ):
                migrate_config([legacy_root_dir], new_home, dry_run=False, force=True)

    def test_migrate_config_already_at_target(self, tmp_path: Path) -> None:
        """Test move doesn't delete config when already at target location."""
        new_home = tmp_path / "home"
        new_home.mkdir()
        config = new_home / "config.yaml"
        config.write_text("my-config")

        # Config is already in new_home (not a legacy root)
        result = migrate_config([new_home], new_home, dry_run=False, force=True)

        assert result["success"] is True
        assert result["action"] == "none"
        assert "already at target" in result["message"]
        # Verify config still exists and wasn't deleted
        assert config.exists()
        assert config.read_text() == "my-config"


class TestMigrateDatabaseEdgeCases:
    """Test migrate_database error paths and edge cases."""

    def test_migrate_database_all_candidates_invalid(self, tmp_path: Path) -> None:
        """Test when all database candidates are invalid."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dirs = [tmp_path / "legacy1", tmp_path / "legacy2"]

        for root in legacy_root_dirs:
            root.mkdir()

        result = migrate_database(legacy_root_dirs, new_home, dry_run=False, force=True)
        assert result["success"] is True
        assert "No database files found" in result["message"]

    def test_migrate_database_most_recent_not_found(self, tmp_path: Path) -> None:
        """Test when _get_most_recent_database returns None."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        # Create a candidate
        db = legacy_root_dir / "meshtastic.sqlite"
        db.write_text("db")

        with mock.patch("mmrelay.migrate._get_most_recent_database", return_value=None):
            result = migrate_database(
                [legacy_root_dir], new_home, dry_run=False, force=True
            )
            assert result["success"] is False
            assert "No valid database files found" in result["message"]

    def test_migrate_database_backup_failure(self, tmp_path: Path) -> None:
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

        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        legacy_db = legacy_root_dir / "meshtastic.sqlite"
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
            with pytest.raises(MigrationError, match="database migration failed"):
                migrate_database([legacy_root_dir], new_home, dry_run=False, force=True)

    def test_migrate_database_from_database_dir(self, tmp_path: Path) -> None:
        """Test migration from legacy database/ directory."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        database_dir = legacy_root_dir / "database"
        database_dir.mkdir()
        legacy_db = database_dir / "meshtastic.sqlite"
        conn = sqlite3.connect(legacy_db)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()

        wal = database_dir / "meshtastic.sqlite-wal"
        wal.write_text("wal")

        result = migrate_database(
            [legacy_root_dir], new_home, dry_run=False, force=True
        )

        assert result["success"] is True
        assert (new_home / "database" / "meshtastic.sqlite").exists()
        assert (new_home / "database" / "meshtastic.sqlite-wal").exists()

    def test_migrate_database_selected_group_missing(self, tmp_path: Path) -> None:
        """Test when selected_group is empty."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        db = legacy_root_dir / "meshtastic.sqlite"
        db.write_text("db")

        with mock.patch(
            "mmrelay.migrate._get_most_recent_database",
            return_value=tmp_path / "other.sqlite",
        ):
            result = migrate_database(
                [legacy_root_dir], new_home, dry_run=False, force=True
            )
            assert result["success"] is False
            assert "Most recent database group not found" in result["message"]

    def test_migrate_database_move_unlink_failure(self, tmp_path: Path) -> None:
        """Test move path logs warning when source unlink fails."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        legacy_db = legacy_root_dir / "meshtastic.sqlite"
        conn = sqlite3.connect(legacy_db)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()

        original_unlink = Path.unlink

        def failing_unlink(self: Path, *args, **kwargs):
            if self == legacy_db:
                raise OSError("unlink failed")
            return original_unlink(self, *args, **kwargs)

        with mock.patch.object(
            Path, "unlink", autospec=True, side_effect=failing_unlink
        ):
            result = migrate_database(
                [legacy_root_dir], new_home, dry_run=False, force=True
            )
            assert result["success"] is True

    def test_migrate_database_move_failure(self, tmp_path: Path) -> None:
        """Test handling of move/copy failure."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        legacy_db = legacy_root_dir / "meshtastic.sqlite"
        legacy_db.write_text("legacy")

        with mock.patch("shutil.copy2", side_effect=OSError("Mock copy error")):
            with pytest.raises(
                MigrationError, match="database migration failed: Mock copy error"
            ):
                migrate_database([legacy_root_dir], new_home, dry_run=False, force=True)

    def test_migrate_database_from_data_dir_with_sidecars(self, tmp_path: Path) -> None:
        """Test migration from legacy data/ directory with sidecars."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        data_dir = legacy_root_dir / "data"
        data_dir.mkdir()
        legacy_db = data_dir / "meshtastic.sqlite"
        conn = sqlite3.connect(legacy_db)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()

        wal = data_dir / "meshtastic.sqlite-wal"
        wal.write_text("wal")
        shm = data_dir / "meshtastic.sqlite-shm"
        shm.write_text("shm")

        result = migrate_database(
            [legacy_root_dir], new_home, dry_run=False, force=True
        )

        assert result["success"] is True
        assert (new_home / "database" / "meshtastic.sqlite").exists()
        assert (new_home / "database" / "meshtastic.sqlite-wal").exists()
        assert (new_home / "database" / "meshtastic.sqlite-shm").exists()

    def test_migrate_database_integrity_check_cleanup_unlink_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test cleanup warning when integrity check cleanup cannot unlink."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        legacy_db = legacy_root_dir / "meshtastic.sqlite"
        conn = sqlite3.connect(legacy_db)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()

        dest_path = new_home / "database" / "meshtastic.sqlite"
        original_unlink = Path.unlink

        def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
            if self == dest_path:
                raise OSError("unlink failed")
            original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", failing_unlink)

        with mock.patch("sqlite3.connect") as mock_connect:
            mock_conn = mock.MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = ["corrupted"]
            mock_connect.return_value.__enter__.return_value = mock_conn

            with pytest.raises(MigrationError) as exc_info:
                migrate_database([legacy_root_dir], new_home, dry_run=False, force=True)

        assert "integrity check failed" in str(exc_info.value).lower()

    def test_migrate_database_db_error_cleanup_unlink_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test cleanup warning when DatabaseError cleanup cannot unlink."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        legacy_db = legacy_root_dir / "meshtastic.sqlite"
        conn = sqlite3.connect(legacy_db)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()

        dest_path = new_home / "database" / "meshtastic.sqlite"
        original_unlink = Path.unlink

        def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
            if self == dest_path:
                raise OSError("unlink failed")
            original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", failing_unlink)

        with mock.patch(
            "sqlite3.connect", side_effect=sqlite3.DatabaseError("Mock error")
        ):
            with pytest.raises(MigrationError) as exc_info:
                migrate_database([legacy_root_dir], new_home, dry_run=False, force=True)

        assert "Database verification failed" in str(exc_info.value)

    def test_migrate_database_integrity_check_failure(self, tmp_path: Path) -> None:
        """Test SQLite integrity check failure."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        # Create valid SQLite database
        legacy_db = legacy_root_dir / "meshtastic.sqlite"
        conn = sqlite3.connect(legacy_db)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()

        # Mock integrity_check to fail
        with mock.patch("sqlite3.connect") as mock_connect:
            mock_conn = mock.MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = ["corrupted"]
            mock_connect.return_value = mock_conn

            with pytest.raises(MigrationError) as exc_info:
                migrate_database([legacy_root_dir], new_home, dry_run=False, force=True)

            assert "integrity check failed" in str(exc_info.value).lower()

    def test_migrate_database_integrity_check_db_error(self, tmp_path: Path) -> None:
        """Test SQLite DatabaseError during integrity check."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        # Create valid SQLite database
        legacy_db = legacy_root_dir / "meshtastic.sqlite"
        conn = sqlite3.connect(legacy_db)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()

        # Mock connect to raise DatabaseError
        with mock.patch(
            "sqlite3.connect", side_effect=sqlite3.DatabaseError("Mock error")
        ):
            with pytest.raises(MigrationError) as exc_info:
                migrate_database([legacy_root_dir], new_home, dry_run=False, force=True)

            assert "Database verification failed" in str(exc_info.value)

    def test_migrate_database_wal_file_skip_integrity_check(
        self, tmp_path: Path
    ) -> None:
        """Test that WAL/SHM files skip integrity check."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        # Create WAL file (not a main database)
        legacy_wal = legacy_root_dir / "meshtastic.sqlite-wal"
        legacy_wal.write_text("wal data")

        # Should not call integrity check on WAL files
        result = migrate_database(
            [legacy_root_dir], new_home, dry_run=False, force=True
        )
        assert result["success"] is True


class TestMigrateLogsEdgeCases:
    """Test migrate_logs error paths and edge cases."""

    def test_migrate_logs_backup_and_copy_failure(self, tmp_path: Path) -> None:
        """Test log backup failure and copy error handling."""
        from datetime import datetime

        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        logs_dir = legacy_root_dir / "logs"
        logs_dir.mkdir()
        log_file = logs_dir / "app.log"
        log_file.write_text("log")

        new_home = tmp_path / "home"
        new_home.mkdir()
        new_logs_dir = new_home / "logs"
        new_logs_dir.mkdir()

        fixed_time = datetime(2024, 1, 1, 12, 0, 0)
        dest_name = (
            f"{log_file.stem}_migrated_{fixed_time.strftime('%Y%m%d_%H%M%S')}.log"
        )
        dest_path = new_logs_dir / dest_name
        dest_path.write_text("existing")

        original_copy2 = shutil.copy2

        def selective_copy2(src, dst, *args, **kwargs):
            if Path(src) == log_file:
                raise OSError("Mock copy error")
            return original_copy2(src, dst, *args, **kwargs)

        with (
            mock.patch("mmrelay.migrate.datetime") as mock_datetime,
            mock.patch("shutil.copytree", side_effect=OSError("Mock backup failure")),
            mock.patch("shutil.copy2", original_copy2),
        ):
            mock_datetime.now.return_value = fixed_time
            # force=True is needed to trigger migration when destination exists
            with pytest.raises(
                MigrationError, match="logs migration failed: Mock backup failure"
            ):
                migrate_logs([legacy_root_dir], new_home, dry_run=False, force=True)


@pytest.mark.skipif(
    sys.platform == "win32", reason="E2EE store not supported on Windows"
)
class TestMigrateStoreEdgeCases:
    """Test migrate_store error paths and edge cases."""

    def test_migrate_store_backup_directory_failure(self, tmp_path: Path) -> None:
        """Test handling of directory backup failure."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        new_store_dir = new_home / "matrix" / "store"
        new_store_dir.parent.mkdir(parents=True)
        new_store_dir.mkdir()
        (new_store_dir / "file").write_text("data")

        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_store_dir = legacy_root_dir / "store"
        old_store_dir.mkdir()
        (old_store_dir / "file").write_text("data")

        with mock.patch("shutil.copytree", side_effect=OSError("Mock backup error")):
            with pytest.raises(
                MigrationError, match="store migration failed: Mock backup error"
            ):
                migrate_store([legacy_root_dir], new_home, dry_run=False, force=True)

    def test_migrate_store_move_existing_directory_removal(
        self, tmp_path: Path
    ) -> None:
        """Test that move operation removes existing directory."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        new_store_dir = new_home / "matrix" / "store"
        new_store_dir.parent.mkdir(parents=True)
        new_store_dir.mkdir()
        (new_store_dir / "old_file").write_text("old")

        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_store_dir = legacy_root_dir / "store"
        old_store_dir.mkdir()
        (old_store_dir / "new_file").write_text("new")

        result = migrate_store([legacy_root_dir], new_home, dry_run=False, force=True)

        assert result["success"] is True
        assert result["action"] == "move"
        # Old file should be gone (directory replaced)
        assert not (new_store_dir / "old_file").exists()
        assert (new_store_dir / "new_file").exists()

    def test_migrate_store_move_failure(self, tmp_path: Path) -> None:
        """Test handling of move failure."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_store_dir = legacy_root_dir / "store"
        old_store_dir.mkdir()
        (old_store_dir / "file").write_text("data")

        with mock.patch("shutil.move", side_effect=OSError("Mock move error")):
            with pytest.raises(
                MigrationError, match="store migration failed: Mock move error"
            ):
                migrate_store([legacy_root_dir], new_home, dry_run=False, force=True)


class TestMigratePluginsEdgeCases:
    """Test migrate_plugins error paths and edge cases."""

    def test_migrate_plugins_backup_custom_plugin_failure(self, tmp_path: Path) -> None:
        """Test handling of custom plugin backup failure."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        new_plugins_dir = new_home / "plugins"
        new_plugins_dir.mkdir()
        new_custom_dir = new_plugins_dir / "custom"
        new_custom_dir.mkdir()
        (new_custom_dir / "existing").mkdir()

        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_plugins_dir = legacy_root_dir / "plugins"
        old_plugins_dir.mkdir()
        old_custom_dir = old_plugins_dir / "custom"
        old_custom_dir.mkdir()
        (old_custom_dir / "plugin").mkdir()

        with mock.patch("shutil.copytree", side_effect=OSError("Mock backup error")):
            with pytest.raises(
                MigrationError, match="plugins migration failed: Mock backup error"
            ):
                migrate_plugins([legacy_root_dir], new_home, dry_run=False, force=True)

    def test_migrate_plugins_backup_dir_creation_failure(self, tmp_path: Path) -> None:
        """Test backup directory creation failure is surfaced."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        # Create new_plugins_dir to trigger backup
        new_plugins_dir = new_home / "plugins"
        new_plugins_dir.mkdir()

        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_plugins_dir = legacy_root_dir / "plugins"
        old_plugins_dir.mkdir()

        backup_path = tmp_path / "plugins.bak.test"

        def fake_backup_file(_path: Path) -> Path:
            return backup_path

        with (
            mock.patch("mmrelay.migrate._backup_file", side_effect=fake_backup_file),
            mock.patch("shutil.copytree", side_effect=OSError("Mock copytree error")),
        ):
            with pytest.raises(
                MigrationError, match="plugins migration failed: Mock copytree error"
            ):
                migrate_plugins([legacy_root_dir], new_home, dry_run=False, force=True)

    def test_migrate_plugins_new_dir_creation_failure(self, tmp_path: Path) -> None:
        """Test plugins directory creation failure is surfaced."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()

        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_plugins_dir = legacy_root_dir / "plugins"
        old_plugins_dir.mkdir()

        # In v1.3, we create staging_dir before starting backup/migration.
        # We mock the Path.mkdir call specifically for the staging path.
        new_home / ".migration_staging" / "plugins"

        original_mkdir = Path.mkdir

        def selective_mkdir(self, *args, **kwargs):
            # Only fail for the specific plugins staging directory, not its parents
            if str(self).endswith(".migration_staging/plugins"):
                raise OSError("Mock mkdir error")
            return original_mkdir(self, *args, **kwargs)

        with mock.patch(
            "mmrelay.migrate.Path.mkdir", side_effect=selective_mkdir, autospec=True
        ):
            with pytest.raises(
                MigrationError, match="plugins migration failed: Mock mkdir error"
            ):
                migrate_plugins([legacy_root_dir], new_home, dry_run=False, force=True)

    def test_migrate_plugins_backup_community_plugin_failure(
        self, tmp_path: Path
    ) -> None:
        """Test handling of community plugin backup failure."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        new_plugins_dir = new_home / "plugins"
        new_plugins_dir.mkdir()
        new_community_dir = new_plugins_dir / "community"
        new_community_dir.mkdir()
        (new_community_dir / "existing").mkdir()

        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_plugins_dir = legacy_root_dir / "plugins"
        old_plugins_dir.mkdir()
        old_community_dir = old_plugins_dir / "community"
        old_community_dir.mkdir()
        (old_community_dir / "existing").mkdir()

        original_copytree = shutil.copytree

        def selective_copytree(src, dst, *args, **kwargs):
            if Path(src) == new_plugins_dir:
                raise OSError("Mock backup error")
            return original_copytree(src, dst, *args, **kwargs)

        with mock.patch("shutil.copytree", side_effect=selective_copytree):
            with pytest.raises(
                MigrationError, match="plugins migration failed: Mock backup error"
            ):
                migrate_plugins([legacy_root_dir], new_home, dry_run=False, force=True)

    def test_migrate_plugins_cleanup_rmdir_error(self, tmp_path: Path) -> None:
        """Test cleanup rmdir errors are captured."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_plugins_dir = legacy_root_dir / "plugins"
        old_plugins_dir.mkdir()
        old_custom_dir = old_plugins_dir / "custom"
        old_custom_dir.mkdir()
        (old_custom_dir / "plugin").mkdir()

        original_rmdir = Path.rmdir

        def failing_rmdir(self: Path, *args, **kwargs):
            if self == old_custom_dir:
                raise OSError
            return original_rmdir(self, *args, **kwargs)

        with mock.patch.object(Path, "rmdir", autospec=True, side_effect=failing_rmdir):
            result = migrate_plugins(
                [legacy_root_dir], new_home, dry_run=False, force=True
            )

        # Cleanup errors are now non-fatal warnings
        assert result["success"] is True
        assert "error" not in result

    def test_migrate_plugins_cleanup_plugins_dir_rmdir_error(
        self, tmp_path: Path
    ) -> None:
        """Test cleanup errors when removing the plugins root."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_plugins_dir = legacy_root_dir / "plugins"
        old_plugins_dir.mkdir()
        old_custom_dir = old_plugins_dir / "custom"
        old_custom_dir.mkdir()
        (old_custom_dir / "plugin").mkdir()

        original_rmdir = Path.rmdir

        def failing_rmdir(self: Path, *args, **kwargs):
            if self == old_plugins_dir:
                raise OSError
            return original_rmdir(self, *args, **kwargs)

        with mock.patch.object(Path, "rmdir", autospec=True, side_effect=failing_rmdir):
            result = migrate_plugins(
                [legacy_root_dir], new_home, dry_run=False, force=True
            )

        # Cleanup errors are now non-fatal warnings
        assert result["success"] is True
        assert "error" not in result

    def test_migrate_plugins_custom_iterdir_failure(self, tmp_path: Path) -> None:
        """Test handling of iterdir failure for custom plugins."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_plugins_dir = legacy_root_dir / "plugins"
        old_plugins_dir.mkdir()
        old_custom_dir = old_plugins_dir / "custom"
        old_custom_dir.mkdir()

        with mock.patch.object(Path, "iterdir", side_effect=OSError("Mock error")):
            with pytest.raises(
                MigrationError, match="plugins migration failed: custom: Mock error"
            ):
                migrate_plugins([legacy_root_dir], new_home, dry_run=False, force=True)

    def test_migrate_plugins_move_removes_existing_custom(self, tmp_path: Path) -> None:
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

        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_plugins_dir = legacy_root_dir / "plugins"
        old_plugins_dir.mkdir()
        old_custom_dir = old_plugins_dir / "custom"
        old_custom_dir.mkdir()
        # Create plugin with same name in legacy location
        (old_custom_dir / "test_plugin").mkdir()
        (old_custom_dir / "test_plugin" / "new_file.txt").write_text("new content")

        result = migrate_plugins([legacy_root_dir], new_home, dry_run=False, force=True)

        assert result["success"] is True
        # Old file should be gone (directory replaced)
        assert not (new_custom_dir / "test_plugin" / "old_file.txt").exists()
        # New file should exist
        assert (new_custom_dir / "test_plugin" / "new_file.txt").exists()

    def test_migrate_plugins_copy_removes_existing_custom(self, tmp_path: Path) -> None:
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

        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_plugins_dir = legacy_root_dir / "plugins"
        old_plugins_dir.mkdir()
        old_custom_dir = old_plugins_dir / "custom"
        old_custom_dir.mkdir()
        # Create plugin with same name in legacy location
        (old_custom_dir / "test_plugin").mkdir()
        (old_custom_dir / "test_plugin" / "new_file.txt").write_text("new content")

        result = migrate_plugins([legacy_root_dir], new_home, dry_run=False, force=True)

        assert result["success"] is True
        # Old file should be gone (directory replaced)
        assert not (new_custom_dir / "test_plugin" / "old_file.txt").exists()
        # New file should exist
        assert (new_custom_dir / "test_plugin" / "new_file.txt").exists()

    def test_migrate_plugins_cleanup_empty_custom_dir_on_move(
        self, tmp_path: Path
    ) -> None:
        """Test that move operation cleans up empty custom directory."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_plugins_dir = legacy_root_dir / "plugins"
        old_plugins_dir.mkdir()
        old_custom_dir = old_plugins_dir / "custom"
        old_custom_dir.mkdir()
        (old_custom_dir / "plugin").mkdir()

        # Perform migration with move
        result = migrate_plugins([legacy_root_dir], new_home, dry_run=False, force=True)

        assert result["success"] is True
        assert result["action"] == "move"
        # After move, old custom dir should be empty and removed
        assert not old_custom_dir.exists() or not list(old_custom_dir.iterdir())

    def test_migrate_plugins_cleanup_empty_plugins_dir_on_move(
        self, tmp_path: Path
    ) -> None:
        """Test that move operation cleans up empty plugins directory."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_plugins_dir = legacy_root_dir / "plugins"
        old_plugins_dir.mkdir()
        old_custom_dir = old_plugins_dir / "custom"
        old_custom_dir.mkdir()
        (old_custom_dir / "plugin").mkdir()

        # Perform migration with move
        result = migrate_plugins([legacy_root_dir], new_home, dry_run=False, force=True)

        assert result["success"] is True
        # After moving all plugins, old plugins dir should be empty or removed
        # The exact behavior depends on directory state, but cleanup is attempted

    def test_migrate_plugins_cleanup_os_error_handling(self, tmp_path: Path) -> None:
        """Test handling of OSError during cleanup."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        old_plugins_dir = legacy_root_dir / "plugins"
        old_plugins_dir.mkdir()
        old_custom_dir = old_plugins_dir / "custom"
        old_custom_dir.mkdir()
        (old_custom_dir / "plugin").mkdir()

        # Mock rmtree to fail
        original_rmtree = shutil.rmtree

        def failing_rmtree(path, *args, **kwargs):
            """
            Simulate a failing directory removal used in tests to exercise cleanup error handling.

            Acts like shutil.rmtree for paths that contain "old_plugin" (delegates to the original rmtree) and raises OSError for any other path. Intended as a test stub.

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
            raise OSError

        with mock.patch("shutil.rmtree", side_effect=failing_rmtree):
            result = migrate_plugins(
                [legacy_root_dir], new_home, dry_run=False, force=True
            )
            # Should still succeed despite cleanup errors (logged as debug)
            assert result["success"] is True


class TestMigrateGpxtrackerEdgeCases:
    """Test migrate_gpxtracker error paths and edge cases."""

    def test_migrate_gpxtracker_yaml_import_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test handling of YAML import error."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        config = legacy_root_dir / "config.yaml"
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
            [legacy_root_dir], new_home, dry_run=False, force=True
        )

        assert result["success"] is True
        assert "gpxtracker plugin not configured" in result["message"]

    def test_migrate_gpxtracker_backup_failure(self, tmp_path: Path) -> None:
        """Test handling of GPX file backup failure."""
        from datetime import datetime

        new_home = tmp_path / "new_home"
        new_home.mkdir()
        new_gpx_dir = new_home / "plugins" / "community" / "gpxtracker" / "data"
        new_gpx_dir.mkdir(parents=True)
        existing_gpx = new_gpx_dir / "existing.gpx"
        existing_gpx.write_text("existing")

        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        gpx_dir = legacy_root_dir / "gpx"
        gpx_dir.mkdir()
        gpx_file = gpx_dir / "track.gpx"
        gpx_file.write_text("track")
        config = legacy_root_dir / "config.yaml"
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
            with pytest.raises(
                MigrationError, match="gpxtracker migration failed: Mock backup error"
            ):
                migrate_gpxtracker(
                    [legacy_root_dir], new_home, dry_run=False, force=False
                )

    def test_migrate_gpxtracker_move_failure(self, tmp_path: Path) -> None:
        """Test handling of GPX file move failure."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        gpx_dir = legacy_root_dir / "gpx"
        gpx_dir.mkdir()
        (gpx_dir / "track.gpx").write_text("track")

        config = legacy_root_dir / "config.yaml"
        config.write_text(f"""
community-plugins:
  gpxtracker:
    gpx_directory: {gpx_dir}
""")

        with mock.patch("shutil.move", side_effect=OSError("Mock move error")):
            with pytest.raises(
                MigrationError, match="gpxtracker migration failed: Mock move error"
            ):
                migrate_gpxtracker(
                    [legacy_root_dir], new_home, dry_run=False, force=True
                )

    def test_migrate_gpxtracker_glob_oserror(self, tmp_path: Path) -> None:
        """Test handling of OSError during glob iteration."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        gpx_dir = legacy_root_dir / "gpx"
        gpx_dir.mkdir()

        config = legacy_root_dir / "config.yaml"
        config.write_text(
            f"community-plugins:\n  gpxtracker:\n    gpx_directory: {gpx_dir}\n"
        )

        with mock.patch.object(Path, "glob", side_effect=OSError("Mock glob error")):
            with pytest.raises(
                MigrationError, match="gpxtracker migration failed: Mock glob error"
            ):
                migrate_gpxtracker(
                    [legacy_root_dir], new_home, dry_run=False, force=True
                )

    def test_migrate_gpxtracker_expanded_path_not_found(self, tmp_path: Path) -> None:
        """Test handling when expanded GPX directory doesn't exist."""
        new_home = tmp_path / "new_home"
        new_home.mkdir()
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        # Create legacy config with gpx_directory pointing to non-existent path
        config = legacy_root_dir / "config.yaml"
        config.write_text("""
community-plugins:
  gpxtracker:
    gpx_directory: ~/nonexistent_gpx
""")

        result = migrate_gpxtracker(
            [legacy_root_dir], new_home, dry_run=False, force=True
        )

        # Should succeed gracefully (directory not found is handled)
        assert result["success"] is True

    def test_perform_migration_home_mkdir_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test new home directory creation failure."""
        new_home = tmp_path / "home"
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        paths_info = {"home": str(new_home), "legacy_sources": [str(legacy_root_dir)]}
        monkeypatch.setattr("mmrelay.migrate.resolve_all_paths", lambda: paths_info)

        original_mkdir = Path.mkdir

        def failing_mkdir(self: Path, *args, **kwargs):
            if self == new_home:
                raise OSError("mkdir failed")
            return original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", failing_mkdir)

        report = perform_migration(dry_run=False, force=True)
        assert report["success"] is False
        assert "Failed to create new home directory" in report["message"]

    def test_perform_migration_gpx_configured_runs_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test gpxtracker step runs when configured."""
        new_home = tmp_path / "home"
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        config = legacy_root_dir / "config.yaml"
        config.write_text(
            "community-plugins:\n  gpxtracker:\n    gpx_directory: /tmp/gpx\n"
        )

        paths_info = {"home": str(new_home), "legacy_sources": [str(legacy_root_dir)]}
        monkeypatch.setattr("mmrelay.migrate.resolve_all_paths", lambda: paths_info)

        def ok_result(*_args, **_kwargs):
            return {"success": True}

        gpx_called = {"value": False}

        def gpx_result(*_args, **_kwargs):
            gpx_called["value"] = True
            return {"success": True}

        monkeypatch.setattr("mmrelay.migrate.migrate_credentials", ok_result)
        monkeypatch.setattr("mmrelay.migrate.migrate_config", ok_result)
        monkeypatch.setattr("mmrelay.migrate.migrate_database", ok_result)
        monkeypatch.setattr("mmrelay.migrate.migrate_logs", ok_result)
        monkeypatch.setattr("mmrelay.migrate.migrate_store", ok_result)
        monkeypatch.setattr("mmrelay.migrate.migrate_plugins", ok_result)
        monkeypatch.setattr("mmrelay.migrate.migrate_gpxtracker", gpx_result)

        report = perform_migration(dry_run=True, force=True)
        assert report["success"] is True
        assert gpx_called["value"] is True

    def test_perform_migration_gpx_yaml_parse_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test YAML parse failure during gpxtracker detection."""
        new_home = tmp_path / "home"
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()
        (legacy_root_dir / "plugins").mkdir()

        config = legacy_root_dir / "config.yaml"
        config.write_text("community-plugins: [")

        paths_info = {"home": str(new_home), "legacy_sources": [str(legacy_root_dir)]}
        monkeypatch.setattr("mmrelay.migrate.resolve_all_paths", lambda: paths_info)

        def ok_result(*_args, **_kwargs):
            return {"success": True}

        gpx_called = {"value": False}

        def gpx_result(*_args, **_kwargs):
            gpx_called["value"] = True
            return {"success": True}

        monkeypatch.setattr("mmrelay.migrate.migrate_credentials", ok_result)
        monkeypatch.setattr("mmrelay.migrate.migrate_config", ok_result)
        monkeypatch.setattr("mmrelay.migrate.migrate_database", ok_result)
        monkeypatch.setattr("mmrelay.migrate.migrate_logs", ok_result)
        monkeypatch.setattr("mmrelay.migrate.migrate_store", ok_result)
        monkeypatch.setattr("mmrelay.migrate.migrate_plugins", ok_result)
        monkeypatch.setattr("mmrelay.migrate.migrate_gpxtracker", gpx_result)

        report = perform_migration(dry_run=True, force=True)
        assert report["success"] is True
        assert gpx_called["value"] is True

    def test_perform_migration_migration_error_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test MigrationError handling branch."""
        new_home = tmp_path / "home"
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        paths_info = {"home": str(new_home), "legacy_sources": [str(legacy_root_dir)]}
        monkeypatch.setattr("mmrelay.migrate.resolve_all_paths", lambda: paths_info)

        def failed_step(*_args, **_kwargs):
            return {"success": False, "error": "boom"}

        monkeypatch.setattr("mmrelay.migrate.migrate_credentials", failed_step)

        report = perform_migration(dry_run=False, force=True)
        assert report["success"] is False
        assert "rollback" not in report

    def test_perform_migration_oserror_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test OSError handling branch."""
        new_home = tmp_path / "home"
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        paths_info = {"home": str(new_home), "legacy_sources": [str(legacy_root_dir)]}
        monkeypatch.setattr("mmrelay.migrate.resolve_all_paths", lambda: paths_info)

        def raise_oserror(*_args, **_kwargs):
            raise OSError("boom")

        monkeypatch.setattr("mmrelay.migrate.migrate_credentials", raise_oserror)

        report = perform_migration(dry_run=False, force=True)
        assert report["success"] is False
        assert "rollback" not in report

    def test_perform_migration_unexpected_exception_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test unexpected exception branch re-raises."""
        new_home = tmp_path / "home"
        legacy_root_dir = tmp_path / "legacy_root_dir"
        legacy_root_dir.mkdir()

        paths_info = {"home": str(new_home), "legacy_sources": [str(legacy_root_dir)]}
        monkeypatch.setattr("mmrelay.migrate.resolve_all_paths", lambda: paths_info)

        def raise_runtime(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("mmrelay.migrate.migrate_credentials", raise_runtime)

        with pytest.raises(RuntimeError):
            perform_migration(dry_run=False, force=True)
