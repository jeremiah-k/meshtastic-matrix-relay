"""
Targeted tests for migration rollback invariants, specifically focusing on
ensuring no residue is left after rollback and gpxtracker rollback behavior.
"""

import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import mmrelay.migrate as migrate_module
from mmrelay.migrate import (
    migrate_logs,
    migrate_store,
    rollback_migration,
    perform_migration,
)

@pytest.fixture
def test_env(tmp_path):
    """Set up a test environment with legacy and new home directories."""
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    new_home = tmp_path / "home"
    # Don't create new_home yet, migrate should do it

    return {
        "legacy_root": legacy_root,
        "new_home": new_home,
        "tmp_path": tmp_path
    }

def test_rollback_logs_no_residue(test_env, monkeypatch):
    """Test that logs rollback leaves no residue when it didn't exist before."""
    legacy_root = test_env["legacy_root"]
    new_home = test_env["new_home"]

    # Create legacy logs
    legacy_logs = legacy_root / "logs"
    legacy_logs.mkdir()
    (legacy_logs / "old.log").write_text("old log content")

    monkeypatch.setattr(migrate_module, "get_home_dir", lambda: new_home)

    # 1. Migrate logs - should create new_home/logs and an empty backup
    result = migrate_logs([legacy_root], new_home)
    assert result["success"] is True
    assert (new_home / "logs").exists()

    # Check that an empty backup was created
    backups = list(new_home.glob("logs.bak.*"))
    assert len(backups) == 1
    assert not any(backups[0].iterdir())

    # 2. Rollback logs
    rollback_result = rollback_migration(completed_steps=["logs"])
    assert rollback_result["success"] is True

    # 3. Verify no residue: logs directory should be GONE
    assert not (new_home / "logs").exists()

def test_rollback_store_no_residue(test_env, monkeypatch):
    """Test that store rollback leaves no residue when it didn't exist before."""
    if sys.platform == "win32":
        pytest.skip("E2EE store not supported on Windows")

    legacy_root = test_env["legacy_root"]
    new_home = test_env["new_home"]

    # Create legacy store
    legacy_store = legacy_root / "store"
    legacy_store.mkdir()
    (legacy_store / "key.db").write_text("key content")

    monkeypatch.setattr(migrate_module, "get_home_dir", lambda: new_home)

    # 1. Migrate store
    result = migrate_store([legacy_root], new_home)
    assert result["success"] is True
    assert (new_home / "store").exists()

    # Check empty backup
    backups = list(new_home.glob("store.bak.*"))
    assert len(backups) == 1
    assert not any(backups[0].iterdir())

    # 2. Rollback store
    rollback_result = rollback_migration(completed_steps=["store"])
    assert rollback_result["success"] is True

    # 3. Verify no residue
    assert not (new_home / "store").exists()

def test_gpxtracker_rollback_via_plugins(test_env, monkeypatch):
    """Test that gpxtracker migration is rolled back via the plugins step."""
    legacy_root = test_env["legacy_root"]
    new_home = test_env["new_home"]
    new_home.mkdir()

    # Mock config for gpxtracker
    config_content = """
community-plugins:
  gpxtracker:
    gpx_directory: ~/gpx_legacy
"""
    (legacy_root / "config.yaml").write_text(config_content)
    (legacy_root / "credentials.json").write_text("{}")

    gpx_legacy = test_env["tmp_path"] / "gpx_legacy"
    gpx_legacy.mkdir()
    (gpx_legacy / "track.gpx").write_text("gpx data")

    # Mock plugins dir (empty)
    legacy_plugins = legacy_root / "plugins"
    legacy_plugins.mkdir()

    monkeypatch.setenv("MMRELAY_HOME", str(new_home))
    monkeypatch.setattr(migrate_module, "get_home_dir", lambda: new_home)
    monkeypatch.setattr(Path, "expanduser", lambda self: gpx_legacy if "gpx_legacy" in str(self) else self)

    # Mock resolve_all_paths
    monkeypatch.setattr(migrate_module, "resolve_all_paths", lambda: {
        "home": str(new_home),
        "legacy_sources": [str(legacy_root)],
        "credentials_path": str(new_home / "credentials.json"),
        "database_dir": str(new_home / "database"),
        "logs_dir": str(new_home / "logs"),
        "plugins_dir": str(new_home / "plugins"),
        "store_dir": str(new_home / "store")
    })

    # Simulate a failure AFTER gpxtracker step
    # We'll use a mock to raise an error at the end of perform_migration

    with patch("mmrelay.migrate._mark_migration_completed", side_effect=Exception("Simulated failure")):
        with pytest.raises(Exception, match="Simulated failure"):
            perform_migration()

    # Verify that gpxtracker data is GONE because plugins was rolled back
    gpx_dest = new_home / "plugins" / "community" / "gpxtracker" / "data"
    assert not gpx_dest.exists()
    assert not (new_home / "plugins").exists()

def test_rollback_restores_non_empty_dir(test_env, monkeypatch):
    """Test that rollback restores a non-empty directory if it was non-empty before."""
    new_home = test_env["new_home"]
    new_home.mkdir()

    # Pre-existing non-empty logs
    logs_dir = new_home / "logs"
    logs_dir.mkdir()
    (logs_dir / "pre_existing.log").write_text("pre-existing")

    legacy_root = test_env["legacy_root"]
    legacy_logs = legacy_root / "logs"
    legacy_logs.mkdir()
    (legacy_logs / "new.log").write_text("new")

    monkeypatch.setattr(migrate_module, "get_home_dir", lambda: new_home)

    # 1. Migrate logs - should backup pre-existing logs
    migrate_logs([legacy_root], new_home)

    # 2. Rollback logs
    rollback_migration(completed_steps=["logs"])

    # 3. Verify pre-existing log is restored and directory still exists
    assert logs_dir.exists()
    assert (logs_dir / "pre_existing.log").exists()
    assert not any(logs_dir.glob("*_migrated_*.log"))
