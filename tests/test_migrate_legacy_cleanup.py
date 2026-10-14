"""Migration removes redundant database groups only after recoverable archiving."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from mmrelay import migrate


def create_database(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE probe(value INTEGER)")
        connection.execute("INSERT INTO probe VALUES (?)", (value,))
        connection.commit()


@pytest.fixture
def database_homes(tmp_path):
    source = tmp_path / "installer"
    target = tmp_path / "home"
    create_database(target / "database" / "meshtastic.sqlite", 42)
    legacy = source / "data" / "meshtastic.sqlite"
    create_database(legacy, 7)
    files = [legacy]
    for suffix in ("-wal", "-shm"):
        sidecar = legacy.with_name(legacy.name + suffix)
        sidecar.write_bytes(b"legacy sidecar")
        files.append(sidecar)
    unrelated = legacy.parent / "keep.txt"
    unrelated.write_text("not migration data", encoding="utf-8")
    return source, target, files, unrelated


def test_archive_removes_legacy_database_group_but_retains_verified_copies(
    database_homes,
):
    source, target, files, unrelated = database_homes
    originals = {path: path.read_bytes() for path in files}
    result = migrate.archive_legacy_databases([source], target)
    assert result["success"]
    assert result["migrated_count"] == 3
    assert all(not path.exists() for path in files)
    for entry in result["archived_files"]:
        assert (
            Path(entry["new_path"]).read_bytes() == originals[Path(entry["old_path"])]
        )
    assert unrelated.read_text(encoding="utf-8") == "not migration data"
    with closing(
        sqlite3.connect(target / "database" / "meshtastic.sqlite")
    ) as connection:
        assert connection.execute("SELECT value FROM probe").fetchone() == (42,)


def test_archive_preview_does_not_create_backups_or_remove_files(database_homes):
    source, target, files, _ = database_homes
    result = migrate.archive_legacy_databases([source], target, dry_run=True)
    assert result["success"] and result["dry_run"]
    assert result["migrated_count"] == 3
    assert all(path.exists() for path in files)
    assert not (target / migrate.MIGRATION_BACKUP_DIRNAME).exists()


def test_archive_refuses_unusable_active_database(database_homes):
    source, target, files, _ = database_homes
    (target / "database" / "meshtastic.sqlite").write_bytes(b"invalid database")
    with pytest.raises(migrate.MigrationError):
        migrate.archive_legacy_databases([source], target)
    assert all(path.exists() for path in files)


def test_archive_keeps_all_sources_if_backup_copy_fails(database_homes, monkeypatch):
    source, target, files, _ = database_homes
    copy = migrate.shutil.copy2

    def fail_sidecar(source_path, destination, *args, **kwargs):
        if str(source_path).endswith("-wal"):
            raise OSError("simulated backup failure")
        return copy(source_path, destination, *args, **kwargs)

    monkeypatch.setattr(migrate.shutil, "copy2", fail_sidecar)
    with pytest.raises(migrate.MigrationError):
        migrate.archive_legacy_databases([source], target)
    assert all(path.exists() for path in files)


def test_archive_keeps_all_sources_if_backup_verification_fails(
    database_homes, monkeypatch
):
    source, target, files, _ = database_homes
    copy = migrate.shutil.copy2

    def corrupt_copy(source_path, destination, *args, **kwargs):
        result = copy(source_path, destination, *args, **kwargs)
        Path(destination).write_bytes(b"corrupted backup")
        return result

    monkeypatch.setattr(migrate.shutil, "copy2", corrupt_copy)
    with pytest.raises(migrate.MigrationError, match="Archive verification failed"):
        migrate.archive_legacy_databases([source], target)
    assert all(path.exists() for path in files)


def test_archive_never_removes_active_database_or_unbacked_sources(database_homes):
    source, target, files, _ = database_homes
    active = target / "database" / "meshtastic.sqlite"
    assert (
        migrate.archive_legacy_databases([target], target)["action"] == "not_applicable"
    )
    assert active.is_file()
    active.unlink()
    assert (
        migrate.archive_legacy_databases([source], target)["action"] == "not_applicable"
    )
    assert all(path.exists() for path in files)
