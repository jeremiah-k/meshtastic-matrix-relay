import sqlite3
import sys
from types import SimpleNamespace

from mmrelay import paths as paths_module
from mmrelay.cli import handle_doctor_command, handle_verify_migration_command
from mmrelay.migrate import perform_migration, verify_migration
from mmrelay.paths import resolve_all_paths


def test_verify_migration_after_move(tmp_path, monkeypatch):
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()

    (legacy_root / "credentials.json").write_text("{}", encoding="utf-8")
    legacy_db_path = legacy_root / "meshtastic.sqlite"
    conn = sqlite3.connect(legacy_db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS migration_test (id INTEGER)")
    conn.commit()
    conn.close()

    logs_dir = legacy_root / "logs"
    logs_dir.mkdir()
    (logs_dir / "mmrelay.log").write_text("log", encoding="utf-8")

    if sys.platform != "win32":
        store_dir = legacy_root / "store"
        store_dir.mkdir()
        (store_dir / "store.db").write_text("store", encoding="utf-8")

    new_home = tmp_path / "home"

    monkeypatch.setenv("MMRELAY_HOME", str(new_home))
    monkeypatch.setattr(paths_module, "_home_override", None)
    monkeypatch.setattr(paths_module, "_home_override_source", None)
    monkeypatch.setattr(paths_module, "get_legacy_dirs", lambda: [legacy_root])

    paths_info = resolve_all_paths()
    assert paths_info["home"] == str(new_home)
    assert paths_info["legacy_sources"] == [str(legacy_root)]

    report = perform_migration(move=True, force=True)
    assert report["success"] is True

    verification = verify_migration()
    assert verification["ok"] is True
    assert verification["legacy_data_found"] is False
    assert verification["credentials_missing"] is False
    assert (new_home / "credentials.json").exists()


def test_verify_migration_missing_credentials(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()

    monkeypatch.setenv("MMRELAY_HOME", str(home))
    monkeypatch.setattr(paths_module, "_home_override", None)
    monkeypatch.setattr(paths_module, "_home_override_source", None)
    monkeypatch.setattr(paths_module, "get_legacy_dirs", lambda: [])

    verification = verify_migration()
    assert verification["ok"] is False
    assert verification["credentials_missing"] is True

    exit_code = handle_verify_migration_command(SimpleNamespace())
    assert exit_code == 1


def test_verify_migration_cli_exit_code_clean(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "credentials.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("MMRELAY_HOME", str(home))
    monkeypatch.setattr(paths_module, "_home_override", None)
    monkeypatch.setattr(paths_module, "_home_override_source", None)
    monkeypatch.setattr(paths_module, "get_legacy_dirs", lambda: [])

    exit_code = handle_verify_migration_command(SimpleNamespace())
    assert exit_code == 0


def test_verify_migration_legacy_only(tmp_path, monkeypatch):
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (legacy_root / "credentials.json").write_text("{}", encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir()

    monkeypatch.setenv("MMRELAY_HOME", str(home))
    monkeypatch.setattr(paths_module, "_home_override", None)
    monkeypatch.setattr(paths_module, "_home_override_source", None)
    monkeypatch.setattr(paths_module, "get_legacy_dirs", lambda: [legacy_root])

    verification = verify_migration()
    assert verification["legacy_data_found"] is True
    assert verification["ok"] is False

    exit_code = handle_verify_migration_command(SimpleNamespace())
    assert exit_code == 1


def test_verify_migration_split_roots(tmp_path, monkeypatch):
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    logs_dir = legacy_root / "logs"
    logs_dir.mkdir()
    (logs_dir / "mmrelay.log").write_text("log", encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir()
    (home / "credentials.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("MMRELAY_HOME", str(home))
    monkeypatch.setattr(paths_module, "_home_override", None)
    monkeypatch.setattr(paths_module, "_home_override_source", None)
    monkeypatch.setattr(paths_module, "get_legacy_dirs", lambda: [legacy_root])

    verification = verify_migration()
    assert verification["legacy_data_found"] is True
    assert verification["split_roots"] is True
    assert verification["ok"] is False

    exit_code = handle_verify_migration_command(SimpleNamespace())
    assert exit_code == 1


def test_doctor_migration_exit_codes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "credentials.json").write_text("{}", encoding="utf-8")

    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (legacy_root / "credentials.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("MMRELAY_HOME", str(home))
    monkeypatch.setattr(paths_module, "_home_override", None)
    monkeypatch.setattr(paths_module, "_home_override_source", None)

    # Clean state -> exit code 0
    monkeypatch.setattr(paths_module, "get_legacy_dirs", lambda: [])
    clean_exit = handle_doctor_command(SimpleNamespace(migration=True))
    assert clean_exit == 0

    # Legacy data present -> exit code 1
    monkeypatch.setattr(paths_module, "get_legacy_dirs", lambda: [legacy_root])
    legacy_exit = handle_doctor_command(SimpleNamespace(migration=True))
    assert legacy_exit == 1


def test_verify_migration_plugins_outside_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "credentials.json").write_text("{}", encoding="utf-8")

    outside_plugins = tmp_path / "plugins"
    outside_plugins.mkdir()

    paths_payload = {
        "home": str(home),
        "legacy_sources": [],
        "credentials_path": str(home / "credentials.json"),
        "database_dir": str(home / "database"),
        "store_dir": (
            "N/A (Windows)" if sys.platform == "win32" else str(home / "store")
        ),
        "logs_dir": str(home / "logs"),
        "plugins_dir": str(outside_plugins),
    }

    import mmrelay.migrate as migrate_module

    monkeypatch.setattr(migrate_module, "resolve_all_paths", lambda: paths_payload)

    verification = verify_migration()
    assert verification["ok"] is False
    assert any("outside MMRELAY_HOME" in error for error in verification["errors"])
