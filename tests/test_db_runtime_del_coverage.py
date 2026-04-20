"""
Tests for DatabaseManager.__del__ in db_runtime.py (lines 553-555).

Covers the best-effort fallback destructor that catches exceptions
to avoid surfacing errors during interpreter shutdown.
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from mmrelay.db_runtime import DatabaseManager


class TestDatabaseManagerDel:
    """Tests for DatabaseManager.__del__ fallback."""

    def test_del_calls_close(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        mgr = DatabaseManager(db_path)
        with patch.object(mgr, "close") as mock_close:
            mgr.__del__()
            mock_close.assert_called_once()

    def test_del_suppresses_close_exception(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        mgr = DatabaseManager(db_path)
        with patch.object(mgr, "close", side_effect=RuntimeError("boom")):
            mgr.__del__()

    def test_del_suppresses_sqlite_error(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        mgr = DatabaseManager(db_path)
        with patch.object(mgr, "close", side_effect=sqlite3.Error("shutdown error")):
            mgr.__del__()

    def test_del_suppresses_generic_exception(self, tmp_path):
        """
        Verifies that DatabaseManager.__del__ swallows any generic Exception raised by its close() method.
        
        Patches the instance's close method to raise Exception("any error") and invokes __del__; the test succeeds if no exception is propagated.
        
        Parameters:
            tmp_path (pathlib.Path): pytest-provided temporary directory for creating a test database file.
        """
        db_path = str(tmp_path / "test.db")
        mgr = DatabaseManager(db_path)
        with patch.object(mgr, "close", side_effect=Exception("any error")):
            mgr.__del__()
