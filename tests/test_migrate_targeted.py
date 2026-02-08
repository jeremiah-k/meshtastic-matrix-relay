"""
Targeted test coverage for migration functions in migrate.py.

Tests covering:
- migrate_store (lines 774-807)
- migrate_plugins (lines 877-952)
- migrate_gpxtracker (lines 1048-1081)
- perform_migration (lines 1152-1194, 1208-1239)
"""

import builtins
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.migrate import (
    migrate_gpxtracker,
    migrate_plugins,
    migrate_store,
    perform_migration,
)

_original_builtins_import = builtins.__import__


class TestMigrateStore(unittest.TestCase):
    """Tests for migrate_store function (lines 774-807)."""

    @patch("mmrelay.migrate.get_home_dir")
    @patch("sys.platform", "win32")
    def test_returns_success_on_windows(self, mock_get_home):
        """Test returns success message on Windows (E2EE not supported)."""
        mock_get_home.return_value = Path("/home")

        result = migrate_store([], Path("/home"))

        self.assertTrue(result.get("success"))
        self.assertIn("E2EE not supported on Windows", result.get("message", ""))


class TestMigratePlugins(unittest.TestCase):
    """Tests for migrate_plugins function (lines 877-952)."""

    @patch("mmrelay.paths.resolve_all_paths")
    def test_returns_success_when_no_plugins(self, mock_resolve):
        """Test returns success message when no plugins directory exists."""
        mock_resolve.return_value = {
            "home": str(Path("/home")),
            "legacy_sources": [],
        }

        result = migrate_plugins([], Path("/home"))

        self.assertTrue(result.get("success"))
        self.assertIn("No plugins directory found", result.get("message", ""))


class TestMigrateGpxtracker(unittest.TestCase):
    """Tests for migrate_gpxtracker function (lines 1048-1081)."""

    def test_returns_success_when_not_configured(self):
        """Test returns success when gpxtracker not configured."""
        result = migrate_gpxtracker([], Path("/home"))

        self.assertTrue(result.get("success"))
        self.assertIn("gpxtracker plugin not configured", result.get("message", ""))

    @patch("builtins.__import__")
    def test_returns_skip_on_yaml_import_error(self, mock_import):
        """Test skips migration when yaml import fails."""

        def side_effect_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("Mock import error")
            return _original_builtins_import(name, *args, **kwargs)

        mock_import.side_effect = side_effect_import

        result = migrate_gpxtracker([], Path("/home"))

        self.assertTrue(result.get("success"))
        self.assertIn("gpxtracker plugin not configured", result.get("message", ""))


class TestPerformMigration(unittest.TestCase):
    """Tests for perform_migration function."""

    @patch("mmrelay.migrate.migrate_store")
    @patch("mmrelay.migrate.migrate_plugins")
    @patch("mmrelay.migrate.migrate_gpxtracker")
    @patch("mmrelay.paths.get_home_dir")
    @patch("mmrelay.paths.resolve_all_paths")
    def test_runs_all_migrations(
        self,
        mock_resolve,
        mock_home,
        mock_gpx,
        mock_plugins,
        mock_store,
    ):
        """Test perform_migration runs all migration steps."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            legacy_root = Path(tmpdir) / "legacy"
            legacy_root.mkdir()
            mock_resolve.return_value = {
                "home": tmpdir,
                "legacy_sources": [str(legacy_root)],
            }
            mock_store.return_value = {"success": True, "message": "Done"}
            mock_plugins.return_value = {"success": True, "message": "Done"}
            mock_gpx.return_value = {"success": True, "message": "Done"}

            result = perform_migration(dry_run=False, force=False)

            self.assertTrue(result.get("success"))
            self.assertGreaterEqual(len(result.get("migrations", [])), 3)

    @patch("mmrelay.migrate.migrate_store")
    @patch("mmrelay.migrate.get_home_dir")
    @patch("mmrelay.paths.get_home_dir")
    @patch("mmrelay.paths.resolve_all_paths")
    def test_returns_error_on_migration_failure(
        self,
        mock_resolve,
        mock_paths_home,
        mock_migrate_home,
        mock_store,
    ):
        """Test perform_migration returns error when migration fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_paths_home.return_value = Path(tmpdir)
            mock_migrate_home.return_value = Path(tmpdir)
            legacy_root = Path(tmpdir) / "legacy"
            legacy_root.mkdir()
            mock_resolve.return_value = {
                "home": tmpdir,
                "legacy_sources": [str(legacy_root)],
            }
            mock_store.return_value = {
                "success": False,
                "error": "Test error",
            }

            result = perform_migration(dry_run=False, force=False)

            self.assertFalse(result.get("success"))
            # Error is in the migrations list, not at top level
            migrations = result.get("migrations", [])
            store_migration = [m for m in migrations if m.get("type") == "store"]
            self.assertTrue(any(store_migration))
            self.assertEqual(
                store_migration[0].get("result", {}).get("error"), "Test error"
            )

    @patch("mmrelay.migrate.migrate_store")
    @patch("mmrelay.paths.get_home_dir")
    @patch("mmrelay.paths.resolve_all_paths")
    def test_dry_run_mode(self, mock_resolve, mock_home, mock_store):
        """Test perform_migration in dry run mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            legacy_root = Path(tmpdir) / "legacy"
            legacy_root.mkdir()
            mock_resolve.return_value = {
                "home": tmpdir,
                "legacy_sources": [str(legacy_root)],
            }
            mock_store.return_value = {
                "success": True,
                "message": "Done",
                "dry_run": True,
            }

            result = perform_migration(dry_run=True, force=False)

            self.assertTrue(result.get("success"))
            self.assertGreater(len(result.get("migrations", [])), 0)

    @patch("mmrelay.migrate.migrate_database")
    @patch("mmrelay.migrate.migrate_config")
    @patch("mmrelay.migrate.migrate_credentials")
    @patch("mmrelay.migrate.get_home_dir")
    @patch("mmrelay.paths.get_home_dir")
    @patch("mmrelay.paths.resolve_all_paths")
    def test_database_failure_stops_migration(
        self,
        mock_resolve,
        mock_paths_home,
        mock_migrate_home,
        mock_creds,
        mock_config,
        mock_db,
    ):
        """Test perform_migration stops on database failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_paths_home.return_value = Path(tmpdir)
            mock_migrate_home.return_value = Path(tmpdir)
            legacy_root = Path(tmpdir) / "legacy"
            legacy_root.mkdir()
            mock_resolve.return_value = {
                "home": tmpdir,
                "legacy_sources": [str(legacy_root)],
            }
            mock_creds.return_value = {"success": True}
            mock_config.return_value = {"success": True}
            mock_db.side_effect = sqlite3.DatabaseError("Database failure")

            result = perform_migration(dry_run=False, force=False)

            self.assertFalse(result.get("success"))
            self.assertEqual(result.get("completed_steps"), ["credentials", "config"])


if __name__ == "__main__":
    unittest.main()
