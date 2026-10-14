"""Windows upgrades preserve runtime state and resolve the data home quietly."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mmrelay import migrate, paths


@pytest.fixture
def windows_homes(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    installer = local / "Programs" / "MM Relay"
    installer.mkdir(parents=True)
    platform = local / "mmrelay" / "mmrelay"
    platform.mkdir(parents=True)
    (installer / "config.yaml").write_text("meshtastic: {}\n", encoding="utf-8")
    (installer / "matrix").mkdir()
    (installer / "matrix" / "credentials.json").write_text(
        '{"homeserver":"https://example.org","access_token":"test-token"}',
        encoding="utf-8",
    )
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setattr(Path, "home", lambda: user)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    for name in ("MMRELAY_HOME", "MMRELAY_BASE_DIR", "MMRELAY_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    # Filesystem migration tests must not inspect unrelated host processes.
    monkeypatch.setattr(migrate.psutil, "process_iter", lambda *args: iter(()))
    monkeypatch.setattr(
        paths.platformdirs,
        "user_data_dir",
        lambda *a, **kw: str(
            local / "mmrelay" if kw.get("appauthor") is False else platform
        ),
    )
    paths.reset_home_override()
    yield installer, local / "mmrelay"
    paths.reset_home_override()


def test_windows_home_selection_is_logged_once(windows_homes, monkeypatch):
    installer, _ = windows_homes
    logger = Mock()
    monkeypatch.setattr(paths, "logger", logger)
    for _ in range(10):
        assert paths.get_home_dir() == installer
    assert logger.info.call_count == 1


def test_windows_migration_offer_detects_existing_home_without_writing(windows_homes):
    installer, target = windows_homes
    before = sorted(str(path) for path in target.parent.rglob("*"))
    plan = migrate.plan_windows_home_migration()
    assert plan["success"] and plan["needed"]
    assert Path(plan["source_home"]) == installer
    assert Path(plan["target_home"]) == target
    assert sorted(str(path) for path in target.parent.rglob("*")) == before
    assert paths.get_home_dir() == installer


@pytest.mark.parametrize("layout", ["fresh", "migrated", "override", "conflict"])
def test_windows_migration_offer_is_hidden_when_inapplicable(
    windows_homes, monkeypatch, layout
):
    installer, target = windows_homes
    if layout == "fresh":
        (installer / "config.yaml").unlink()
        (installer / "matrix" / "credentials.json").unlink()
        (installer / "mmrelay.exe").touch()
        (target / "plugins" / "core" / "weather" / "data").mkdir(parents=True)
    elif layout == "migrated":
        (installer / "config.yaml").rename(target / "config.yaml")
        (target / "matrix").mkdir()
        (installer / "matrix" / "credentials.json").rename(
            target / "matrix" / "credentials.json"
        )
    elif layout == "override":
        monkeypatch.setenv("MMRELAY_HOME", str(installer))
    else:
        (target / "config.yaml").write_text("other: true\n", encoding="utf-8")
    assert not migrate.plan_windows_home_migration()["needed"]


@pytest.mark.integration
def test_windows_migration_offer_cli_uses_the_same_read_only_plan(
    windows_homes, monkeypatch
):
    from mmrelay import cli, log_utils

    # Match a long-lived process where earlier startup enabled file logging.
    monkeypatch.setattr(log_utils, "config", {"logging": {"log_to_file": True}})

    installer, target = windows_homes
    monkeypatch.setattr(
        paths.sys,
        "argv",
        ["mmrelay", "migrate", "--to-default-home", "--check-needed"],
    )
    args = cli.parse_arguments()
    assert args.check_needed
    before = sorted(str(path) for path in target.parent.rglob("*"))
    assert cli.main() == 0
    assert sorted(str(path) for path in target.parent.rglob("*")) == before
    assert (installer / "config.yaml").exists()
    assert not (target / "config.yaml").exists()
    explicit = target / "unused-explicit-home"
    monkeypatch.setattr(
        paths.sys,
        "argv",
        [
            "mmrelay",
            "--home",
            str(explicit),
            "migrate",
            "--to-default-home",
            "--check-needed",
        ],
    )
    assert cli.main() == 1
    assert not explicit.exists()
    args.to_default_home = False
    assert cli.handle_migrate_command(args) == 1
    args.to_default_home = True
    args.force = True
    assert cli.handle_migrate_command(args) == 1
    args.force = False
    (installer / "config.yaml").unlink()
    (installer / "matrix" / "credentials.json").unlink()
    assert cli.handle_migrate_command(args) == 1


def test_relocate_credentials_from_installer_matrix_directory(windows_homes, tmp_path):
    installer, _ = windows_homes
    target = tmp_path / "new-home"
    source = installer / "matrix" / "credentials.json"
    content = source.read_bytes()
    result = migrate.migrate_credentials([installer], target)
    assert result["success"]
    assert (target / "matrix" / "credentials.json").read_bytes() == content
    assert not source.exists()


def test_plugin_migration_ignores_nested_empty_destination_scaffolding(tmp_path):
    source = tmp_path / "old-installer"
    target = tmp_path / "new-home"
    plugin_file = source / "plugins" / "core" / "weather" / "data" / "state.json"
    plugin_file.parent.mkdir(parents=True)
    plugin_file.write_text('{"value":42}', encoding="utf-8")
    dependency = source / "plugins" / "deps" / "cpython-314-win-amd64" / "module.py"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("VALUE = 42\n", encoding="utf-8")

    # Startup creates *nested* directories, not installed plugin files.
    for tier in (
        "core/weather/data",
        "deps/cpython-314-win-amd64",
        "custom",
        "community",
    ):
        (target / "plugins" / tier).mkdir(parents=True, exist_ok=True)

    result = migrate.migrate_plugins([source], target)
    assert result["success"] and result["action"] == "move", result
    relocated = target / "plugins" / "core" / "weather" / "data" / "state.json"
    assert relocated.read_text(encoding="utf-8") == '{"value":42}'
    installed_dependency = (
        target / "plugins" / "deps" / "cpython-314-win-amd64" / "module.py"
    )
    assert installed_dependency.is_file()
    assert not plugin_file.exists()


def test_relocate_core_plugin_data_and_installed_dependencies(tmp_path):
    source = tmp_path / "installer"
    target = tmp_path / "data-home"
    plugin_data = source / "plugins" / "core" / "weather" / "data"
    plugin_data.mkdir(parents=True)
    (plugin_data / "state.json").write_text('{"value":42}', encoding="utf-8")
    dependencies = source / "plugins" / "deps" / "cpython-314-win-amd64"
    dependencies.mkdir(parents=True)
    (dependencies / "installed.py").write_text("VALUE = 42\n", encoding="utf-8")
    custom = source / "plugins" / "custom"
    custom.mkdir()
    (custom / "standalone.py").write_text("VALUE = 42\n", encoding="utf-8")
    result = migrate.migrate_plugins([source], target)
    assert result["success"]
    assert (target / "plugins" / "core" / "weather" / "data" / "state.json").is_file()
    assert (
        target / "plugins" / "deps" / "cpython-314-win-amd64" / "installed.py"
    ).is_file()
    assert (target / "plugins" / "custom" / "standalone.py").is_file()


@pytest.mark.integration
def test_guided_windows_migration_previews_then_moves_one_home(windows_homes):
    installer, target = windows_homes
    database = installer / "database" / "meshtastic.sqlite"
    database.parent.mkdir()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE probe(value INTEGER)")
        connection.execute("INSERT INTO probe VALUES (42)")
        connection.commit()
    legacy = installer / "data" / "meshtastic.sqlite"
    legacy.parent.mkdir()
    with closing(sqlite3.connect(legacy)) as connection:
        connection.execute("CREATE TABLE probe(value INTEGER)")
        connection.execute("INSERT INTO probe VALUES (7)")
        connection.commit()
    leftovers = [legacy]
    for suffix in ("-wal", "-shm"):
        sidecar = legacy.with_name(legacy.name + suffix)
        sidecar.write_bytes(b"legacy sidecar")
        leftovers.append(sidecar)
    for path in leftovers:
        os.utime(path, (1, 1))
    # A frozen startup may already have populated nested empty directories at
    # the default home before migration begins.
    for tier in ("core/weather/data", "deps/cpython-314-win-amd64"):
        (target / "plugins" / tier).mkdir(parents=True, exist_ok=True)
    preview = migrate.migrate_windows_home(dry_run=True)
    assert preview["success"]
    assert Path(preview["target_home"]) == target
    assert (installer / "matrix" / "credentials.json").exists()
    assert not (target / "config.yaml").exists()
    assert all(path.exists() for path in leftovers)

    result = migrate.migrate_windows_home()
    assert result["success"], result
    assert (target / "matrix" / "credentials.json").is_file()
    assert (target / "config.yaml").is_file()
    assert not (installer / "config.yaml").exists()
    with closing(
        sqlite3.connect(target / "database" / "meshtastic.sqlite")
    ) as connection:
        assert connection.execute("SELECT value FROM probe").fetchone() == (42,)
    assert paths.get_home_dir() == target
    assert not migrate.plan_windows_home_migration()["needed"]
    assert all(not path.exists() for path in leftovers)
    cleanup = next(
        step["result"]
        for step in result["migrations"]
        if step["type"] == "legacy_database_cleanup"
    )
    assert cleanup["migrated_count"] == 3
    assert installer not in paths.get_legacy_dirs(with_data_only=True)
    assert migrate.verify_migration()["ok"]
    assert migrate.migrate_windows_home()["success"]


@pytest.mark.parametrize("guided", [False, True])
def test_migration_cleans_database_leftovers_after_home_relocation(
    windows_homes, guided
):
    installer, target = windows_homes
    assert migrate.migrate_windows_home()["success"]
    for directory, value in ((target / "database", 42), (installer / "data", 7)):
        directory.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(directory / "meshtastic.sqlite")) as connection:
            connection.execute("CREATE TABLE probe(value INTEGER)")
            connection.execute("INSERT INTO probe VALUES (?)", (value,))
            connection.commit()
    legacy = installer / "data" / "meshtastic.sqlite"
    assert paths.get_home_dir() == target
    run_migration = (
        migrate.migrate_windows_home if guided else migrate.perform_migration
    )
    preview = run_migration(dry_run=True)
    assert preview["success"], preview
    assert legacy.is_file()

    result = run_migration()

    assert result["success"], result
    assert not legacy.exists()
    assert installer not in paths.get_legacy_dirs(with_data_only=True)
    with closing(
        sqlite3.connect(target / "database" / "meshtastic.sqlite")
    ) as connection:
        assert connection.execute("SELECT value FROM probe").fetchone() == (42,)
    assert migrate.verify_migration()["ok"]


@pytest.mark.parametrize("blocked", ["running", "lock", "disk"])
def test_guided_cleanup_preserves_sources_when_blocked(
    windows_homes, monkeypatch, blocked
):
    installer, target = windows_homes
    assert migrate.migrate_windows_home()["success"]
    for root in (target / "database", installer / "data"):
        root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(root / "meshtastic.sqlite")) as connection:
            connection.execute("CREATE TABLE probe(value INTEGER)")
            connection.commit()
    lock = target / migrate.MIGRATION_LOCK_FILENAME
    if blocked == "running":
        monkeypatch.setattr(
            migrate.psutil,
            "process_iter",
            lambda *args: iter(
                [
                    SimpleNamespace(
                        info={"pid": -1, "name": "mmrelay.exe", "cmdline": []}
                    )
                ]
            ),
        )
    elif blocked == "lock":
        lock.touch()
    else:
        monkeypatch.setattr(migrate, "_check_disk_space", lambda _: (False, 0))

    result = migrate.migrate_windows_home()

    assert not result["success"], result
    assert (installer / "data" / "meshtastic.sqlite").is_file()
    assert lock.exists() is (blocked == "lock")


def test_guided_windows_migration_refuses_destination_conflicts(windows_homes):
    installer, target = windows_homes
    (target / "config.yaml").write_text("other: true\n", encoding="utf-8")
    result = migrate.migrate_windows_home()
    assert not result["success"]
    assert "no files were moved" in result["error"]
    assert (installer / "matrix" / "credentials.json").exists()
    assert (target / "config.yaml").read_text(encoding="utf-8") == "other: true\n"


def test_guided_windows_migration_respects_explicit_home(windows_homes, monkeypatch):
    installer, _ = windows_homes
    monkeypatch.setenv("MMRELAY_HOME", str(installer))
    result = migrate.migrate_windows_home()
    assert not result["success"]
    assert "explicit home" in result["error"]
    assert paths.get_home_dir() == installer


def test_guided_windows_migration_preserves_another_migrations_lock(windows_homes):
    installer, target = windows_homes
    lock = target / migrate.MIGRATION_LOCK_FILENAME
    lock.write_text("owned by another migration", encoding="utf-8")
    result = migrate.migrate_windows_home()
    assert not result["success"]
    assert "migration lock" in result["error"]
    assert lock.read_text(encoding="utf-8") == "owned by another migration"
    assert (installer / "config.yaml").exists()
    assert not (target / migrate.MIGRATION_BACKUP_DIRNAME).exists()


def test_guided_windows_migration_holds_lock_through_source_recovery(
    windows_homes, monkeypatch
):
    installer, target = windows_homes
    lock = target / migrate.MIGRATION_LOCK_FILENAME
    copytree = migrate.shutil.copytree
    move = migrate.shutil.move

    def check_lock(source, destination, *args, **kwargs):
        assert lock.exists()
        return copytree(source, destination, *args, **kwargs)

    monkeypatch.setattr(migrate.shutil, "copytree", check_lock)

    def fail_config_move(source, destination, *args, **kwargs):
        if Path(source) == installer / "config.yaml":
            (installer / "matrix").mkdir(exist_ok=True)
            (installer / "matrix" / "after-snapshot.txt").write_text(
                "preserve for recovery", encoding="utf-8"
            )
            raise OSError("simulated config move failure")
        return move(source, destination, *args, **kwargs)

    monkeypatch.setattr(migrate.shutil, "move", fail_config_move)
    result = migrate.migrate_windows_home()
    assert not result["success"]
    assert result["source_restore_errors"] == []
    assert (installer / "matrix" / "credentials.json").exists()
    assert not (installer / "matrix" / "after-snapshot.txt").exists()
    assert (
        Path(result["source_backup"])
        / "partial-source"
        / "matrix"
        / "after-snapshot.txt"
    ).read_text(encoding="utf-8") == "preserve for recovery"
    assert not lock.exists()


def test_empty_plugin_scaffolding_is_not_legacy_data(windows_homes):
    _, target = windows_homes
    nested = target / "mmrelay"
    for tier in ("core", "community", "custom", "deps/cpython-314-win-amd64"):
        (nested / "plugins" / tier).mkdir(parents=True, exist_ok=True)
    assert nested not in paths.get_legacy_dirs(with_data_only=True)
    assert not migrate._find_legacy_data(nested)


@pytest.mark.parametrize("failure", ["running", "disk-space", "backup"])
def test_guided_windows_migration_refuses_before_moving_on_preflight_failure(
    windows_homes, monkeypatch, failure
):
    installer, target = windows_homes
    if failure == "running":
        monkeypatch.setattr(migrate, "_is_mmrelay_running", lambda: True)
    elif failure == "disk-space":
        monkeypatch.setattr(migrate, "_check_disk_space", lambda _: (False, 0))
    else:
        monkeypatch.setattr(
            migrate.shutil, "copy2", Mock(side_effect=OSError("unwritable backup"))
        )
    result = migrate.migrate_windows_home()
    assert not result["success"]
    assert (installer / "config.yaml").is_file()
    assert (installer / "matrix" / "credentials.json").is_file()
    assert not (target / "config.yaml").exists()
    assert not (target / migrate.MIGRATION_LOCK_FILENAME).exists()


@pytest.mark.parametrize(
    "error", [migrate.psutil.AccessDenied, migrate.psutil.NoSuchProcess]
)
def test_windows_process_inspection_failures_refuse_migration(
    windows_homes, monkeypatch, error
):
    monkeypatch.setattr(migrate.psutil, "Process", Mock(side_effect=error(123)))
    assert migrate._is_mmrelay_running()


@pytest.mark.integration
def test_failed_guided_migration_restores_active_source(windows_homes, monkeypatch):
    installer, target = windows_homes
    database = installer / "database" / "meshtastic.sqlite"
    database.parent.mkdir()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE probe(value INTEGER)")
        connection.commit()
    connect = sqlite3.connect

    def fail_staged_database(path, *args, **kwargs):
        if ".migration_staging" in str(path):
            raise sqlite3.DatabaseError("simulated validation failure")
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", fail_staged_database)
    result = migrate.migrate_windows_home()
    assert not result["success"]
    assert result["source_restore_errors"] == []
    assert (installer / "config.yaml").is_file()
    assert (installer / "matrix" / "credentials.json").is_file()
    assert database.is_file()
    assert not (target / "config.yaml").exists()
    assert not (target / "matrix" / "credentials.json").exists()
    assert Path(result["source_backup"]).is_dir()
    assert paths.get_home_dir() == installer


@pytest.mark.parametrize(
    "name,arguments,expected",
    [
        ("mmrelay.exe", ["mmrelay.exe"], True),
        ("python.exe", ["python.exe", "-m", "mmrelay"], True),
        ("python.exe", ["python.exe", "unrelated.py"], False),
    ],
)
def test_windows_running_instance_detection(
    windows_homes, monkeypatch, name, arguments, expected
):
    current = Mock()
    current.exe.return_value = "mmrelay.exe"
    current.cmdline.return_value = ["mmrelay.exe", "migrate", "--to-default-home"]
    parent = Mock(pid=123)
    parent.exe.return_value = current.exe.return_value
    parent.cmdline.return_value = current.cmdline.return_value
    current.parent.return_value = parent
    monkeypatch.setattr(migrate.psutil, "Process", lambda _: current)
    processes = [
        SimpleNamespace(
            info={
                "pid": os.getpid(),
                "name": "mmrelay.exe",
                "cmdline": current.cmdline.return_value,
            }
        ),
        SimpleNamespace(
            info={
                "pid": 123,
                "name": "mmrelay.exe",
                "cmdline": parent.cmdline.return_value,
            }
        ),
        SimpleNamespace(info={"pid": 456, "name": name, "cmdline": arguments}),
    ]
    monkeypatch.setattr(migrate.psutil, "process_iter", lambda _: iter(processes))
    assert migrate._is_mmrelay_running() is expected


def test_windows_config_shortcut_opens_active_home(windows_homes, monkeypatch):
    import argparse

    from mmrelay import cli

    installer, _ = windows_homes
    startfile = Mock()
    monkeypatch.setattr(os, "startfile", startfile, raising=False)
    args = argparse.Namespace(config_command="edit", config=None)
    assert cli.handle_config_command(args) == 0
    assert startfile.call_args.args[2] == f'"{installer / "config.yaml"}"'
    args.config = str(installer / "missing.yaml")
    startfile.reset_mock()
    assert cli.handle_config_command(args) == 1
    startfile.assert_not_called()


def test_plugin_stage_copy_failure_keeps_legacy_state(tmp_path, monkeypatch):
    """Even a partially copied deps tier must not remove the only source copy."""
    source = tmp_path / "legacy"
    target = tmp_path / "data"
    for name in (
        "custom/hello.py",
        "community/hello.py",
        "core/weather/state.json",
        "deps/cpython-314-win-amd64/module.py",
    ):
        file = source / "plugins" / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"source {name}", encoding="utf-8")
    original_copytree = migrate.shutil.copytree

    def copy_partial_deps(src, dst, *args, **kwargs):
        if str(src).endswith("plugins/deps"):
            dest = Path(dst)
            dest.mkdir(parents=True)
            (dest / "partial.txt").write_text("partial", encoding="utf-8")
            raise OSError("simulated cross-volume copy failure")
        return original_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(migrate.shutil, "copytree", copy_partial_deps)
    with pytest.raises(migrate.MigrationError, match="inspect incomplete staging"):
        migrate.migrate_plugins([source], target)
    for name in (
        "custom/hello.py",
        "community/hello.py",
        "core/weather/state.json",
        "deps/cpython-314-win-amd64/module.py",
    ):
        assert (source / "plugins" / name).read_text(
            encoding="utf-8"
        ) == f"source {name}"
    assert (
        target / ".migration_staging" / "plugins" / "deps" / "partial.txt"
    ).is_file()
    assert not (target / "plugins").is_dir()
    with pytest.raises(migrate.MigrationError, match="Previous staged plugin data"):
        migrate.migrate_plugins([source], target)


def test_plugin_staging_collision_does_not_discard_previous_data(tmp_path):
    source = tmp_path / "legacy"
    (source / "plugins" / "custom").mkdir(parents=True)
    target = tmp_path / "data"
    staged = target / ".migration_staging" / "plugins" / "core" / "state.json"
    staged.parent.mkdir(parents=True)
    staged.write_text("recovery data", encoding="utf-8")
    with pytest.raises(migrate.MigrationError, match="Previous staged plugin data"):
        migrate.migrate_plugins([source], target)
    assert staged.read_text(encoding="utf-8") == "recovery data"


def test_rollback_keeps_failed_step_staging_for_recovery(tmp_path):
    staged = tmp_path / ".migration_staging" / "plugins" / "core" / "state.json"
    staged.parent.mkdir(parents=True)
    staged.write_text("only copy", encoding="utf-8")
    report = migrate.rollback_migration([], [], tmp_path)
    assert report["success"]
    assert report["staging_dir"] == str(tmp_path / ".migration_staging")
    assert staged.read_text(encoding="utf-8") == "only copy"
