"""
Tests for _print_system_health WAL mode check in cli.py (lines 2146-2152).

Covers the database journal mode check that reads PRAGMA journal_mode
and prints whether WAL mode is active.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.cli import _print_system_health


class TestPrintSystemHealthDatabase(unittest.TestCase):
    """Tests for _print_system_health database journal mode check."""

    def _make_paths_info(self, db_path: Path) -> dict[str, str]:
        """
        Create a paths_info dictionary containing the system temporary directory as "home" and the parent directory of the given database path as "database_dir".
        
        Parameters:
            db_path (Path): Path to the database file; its parent directory is used for "database_dir".
        
        Returns:
            dict[str, str]: A mapping with keys "home" (system temporary directory) and "database_dir" (string path of db_path's parent).
        """
        return {
            "home": tempfile.gettempdir(),
            "database_dir": str(db_path.parent),
        }

    def test_wal_mode_prints_wal_emoji(self):
        """
        Verify that system health output reports WAL journal mode.
        
        Creates a temporary SQLite database configured with `PRAGMA journal_mode=wal`, patches the database path and E2EE dependency check, captures printed output from _print_system_health, and asserts that at least one printed message contains the substring "WAL mode".
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=wal;")
            conn.execute("CREATE TABLE t(x)")
            conn.commit()
            conn.close()

            paths_info = self._make_paths_info(db_path)

            with (
                patch("mmrelay.paths.get_database_path", return_value=db_path),
                patch("mmrelay.cli._e2ee_dependencies_available", return_value=True),
                patch("builtins.print") as mock_print,
            ):
                _print_system_health(paths_info)

            printed = [str(c) for c in mock_print.call_args_list]
            self.assertTrue(any("WAL mode" in p for p in printed))

    def test_non_wal_mode_prints_mode_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=delete;")
            conn.execute("CREATE TABLE t(x)")
            conn.commit()
            conn.close()

            paths_info = self._make_paths_info(db_path)

            with (
                patch("mmrelay.paths.get_database_path", return_value=db_path),
                patch("mmrelay.cli._e2ee_dependencies_available", return_value=True),
                patch("builtins.print") as mock_print,
            ):
                _print_system_health(paths_info)

            printed = [str(c) for c in mock_print.call_args_list]
            self.assertTrue(any("delete" in p.lower() for p in printed))

    def test_sqlite_error_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            db_path.write_text("not a sqlite db")
            paths_info = self._make_paths_info(db_path)

            with (
                patch("mmrelay.paths.get_database_path", return_value=db_path),
                patch("mmrelay.cli._e2ee_dependencies_available", return_value=True),
                patch("builtins.print"),
            ):
                _print_system_health(paths_info)


if __name__ == "__main__":
    unittest.main()
