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
from types import SimpleNamespace
from unittest.mock import patch

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
                patch("builtins.print") as mock_print,
            ):
                _print_system_health(paths_info)
                assert mock_print.called


class TestPrintSystemHealthWindows(unittest.TestCase):
    """Tests for _print_system_health on Windows (capability-driven)."""

    def test_windows_platform_prints_capability_status(self):
        """On Windows, _print_system_health reports E2EE from capability state."""
        db_path = Path(tempfile.gettempdir()) / "health_test.db"
        paths_info = {
            "home": tempfile.gettempdir(),
            "database_dir": str(db_path.parent),
        }

        with (
            patch("mmrelay.cli.sys.platform", "win32"),
            patch("mmrelay.paths.get_database_path", return_value=db_path),
            patch("mmrelay.cli._e2ee_dependencies_available", return_value=True),
            patch("builtins.print") as mock_print,
        ):
            _print_system_health(paths_info)

        printed = [str(c) for c in mock_print.call_args_list]
        self.assertTrue(any("E2EE crypto libraries available" in p for p in printed))
        self.assertFalse(any("Not supported on Windows" in p for p in printed))


class TestPrintSystemHealthE2EEStore(unittest.TestCase):
    """Tests for _print_system_health E2EE reporting when the store cannot open."""

    @staticmethod
    def _run_health(store_failure: str | None) -> list[str]:
        """Run _print_system_health with unavailable E2EE deps and captured output."""
        db_path = Path(tempfile.gettempdir()) / "health_test.db"
        paths_info = {
            "home": tempfile.gettempdir(),
            "database_dir": str(db_path.parent),
        }

        with (
            patch("mmrelay.paths.get_database_path", return_value=db_path),
            patch("mmrelay.cli._e2ee_dependencies_available", return_value=False),
            patch(
                "mmrelay.matrix.compat.get_matrix_capabilities",
                return_value=SimpleNamespace(
                    encryption_available=False,
                    sqlite_store_available=True,
                    sqlite_store_openable=False,
                    store_failure=store_failure,
                    both_known_providers_installed=False,
                    provider_distribution="mindroom-nio",
                ),
            ),
            patch("builtins.print") as mock_print,
        ):
            _print_system_health(paths_info)

        return [str(c.args[0]) for c in mock_print.call_args_list if c.args]

    def test_missing_libraries_keeps_install_framing_when_store_failure_is_none(self):
        """Without a store failure, missing dependencies keep the install framing."""
        printed = self._run_health(store_failure=None)

        self.assertTrue(any("Missing E2EE crypto libraries" in p for p in printed))
        self.assertTrue(any(p.startswith("       Install with: ") for p in printed))

    def test_store_failure_reports_store_problem_not_missing_libraries(self):
        """A store failure names the store problem and keeps the guidance line."""
        for store_failure in ("fcntl", "other"):
            with self.subTest(store_failure=store_failure):
                printed = self._run_health(store_failure)

                self.assertTrue(any("crypto store cannot open" in p for p in printed))
                self.assertFalse(
                    any("Missing E2EE crypto libraries" in p for p in printed)
                )
                self.assertFalse(
                    any(p.startswith("       Install with: ") for p in printed)
                )
                guidance_fragment = (
                    "No package install enables E2EE"
                    if store_failure == "fcntl"
                    else "crypto store could not open"
                )
                self.assertTrue(
                    any(guidance_fragment in p for p in printed),
                    f"missing {guidance_fragment!r} guidance in {printed}",
                )


if __name__ == "__main__":
    unittest.main()
