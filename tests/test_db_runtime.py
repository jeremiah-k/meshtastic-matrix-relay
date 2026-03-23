"""
Tests for db_runtime.py utility functions.

Covers:
- _get_sqlite_runtime_version_info: version parsing and normalization
- _probe_sqlite_json_each_support: json_each support detection
"""

import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from mmrelay.db_runtime import (
    _get_sqlite_runtime_version_info,
    _probe_sqlite_json_each_support,
)


class TestGetSqliteRuntimeVersionInfo(unittest.TestCase):
    """Test _get_sqlite_runtime_version_info function."""

    def test_valid_tuple_with_three_ints_returns_normalized(self):
        """Test when sqlite_version_info is a valid tuple with 3+ ints - returns normalized tuple."""
        with patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", (3, 45, 1)):
            result = _get_sqlite_runtime_version_info()
            self.assertEqual(result, (3, 45, 1))

    def test_valid_tuple_with_more_than_three_ints_returns_first_three(self):
        """Test when sqlite_version_info has more than 3 parts - returns first 3."""
        with patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", (3, 45, 1, 2, 3)):
            result = _get_sqlite_runtime_version_info()
            self.assertEqual(result, (3, 45, 1))

    def test_none_version_info_falls_back_to_string_parsing(self):
        """Test when sqlite_version_info is None - falls back to sqlite_version string parsing."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", None),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3.45.1"),
        ):
            result = _get_sqlite_runtime_version_info()
            self.assertEqual(result, (3, 45, 1))

    def test_non_tuple_version_info_falls_back_to_string_parsing(self):
        """Test when sqlite_version_info is not a tuple - falls back to string parsing."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", "3.45.1"),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3.45.1"),
        ):
            result = _get_sqlite_runtime_version_info()
            self.assertEqual(result, (3, 45, 1))

    def test_string_fewer_than_three_parts_pads_with_zeros(self):
        """Test when sqlite_version string has fewer than 3 parts - pads with zeros."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", None),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3.45"),
        ):
            result = _get_sqlite_runtime_version_info()
            self.assertEqual(result, (3, 45, 0))

    def test_string_single_part_pads_with_zeros(self):
        """Test when sqlite_version string has only 1 part - pads with zeros."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", None),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3"),
        ):
            result = _get_sqlite_runtime_version_info()
            self.assertEqual(result, (3, 0, 0))

    def test_string_non_numeric_parts_uses_zero(self):
        """Test when sqlite_version string has non-numeric parts - uses 0 for those parts."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", None),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3.abc.1"),
        ):
            result = _get_sqlite_runtime_version_info()
            self.assertEqual(result, (3, 0, 1))

    def test_string_all_non_numeric_parts_uses_zeros(self):
        """Test when sqlite_version string has all non-numeric parts."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", None),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "a.b.c"),
        ):
            result = _get_sqlite_runtime_version_info()
            self.assertEqual(result, (0, 0, 0))

    def test_tuple_with_non_int_parts_falls_back_to_string(self):
        """Test when sqlite_version_info tuple has non-int parts - falls back to string."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", (3, "45", 1)),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3.45.1"),
        ):
            result = _get_sqlite_runtime_version_info()
            self.assertEqual(result, (3, 45, 1))

    def test_tuple_with_less_than_three_parts_falls_back_to_string(self):
        """Test when sqlite_version_info tuple has fewer than 3 parts - falls back to string."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", (3, 45)),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3.45.1"),
        ):
            result = _get_sqlite_runtime_version_info()
            self.assertEqual(result, (3, 45, 1))


class TestProbeSqliteJsonEachSupport(unittest.TestCase):
    """Test _probe_sqlite_json_each_support function."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

    def tearDown(self):
        """Clean up test fixtures."""
        import os

        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_no_such_function_json_each_raises_runtime_error(self):
        """Test when error contains 'no such function: json_each' - raises RuntimeError."""
        conn = sqlite3.connect(self.db_path)
        try:
            mock_cursor = MagicMock()
            mock_cursor.fetchall.side_effect = sqlite3.OperationalError(
                "no such function: json_each"
            )
            mock_conn = MagicMock()
            mock_conn.execute.return_value = mock_cursor

            with patch(
                "mmrelay.db_runtime._get_sqlite_runtime_version_info",
                return_value=(3, 45, 1),
            ):
                with self.assertRaises(RuntimeError) as cm:
                    _probe_sqlite_json_each_support(mock_conn)

            self.assertIn("json_each() support is required", str(cm.exception))
            self.assertIn("3.45.1", str(cm.exception))
        finally:
            conn.close()

    def test_no_such_table_json_each_raises_runtime_error(self):
        """Test when error contains 'no such table: json_each' - raises RuntimeError."""
        conn = sqlite3.connect(self.db_path)
        try:
            mock_cursor = MagicMock()
            mock_cursor.fetchall.side_effect = sqlite3.OperationalError(
                "no such table: json_each"
            )
            mock_conn = MagicMock()
            mock_conn.execute.return_value = mock_cursor

            with patch(
                "mmrelay.db_runtime._get_sqlite_runtime_version_info",
                return_value=(3, 45, 1),
            ):
                with self.assertRaises(RuntimeError) as cm:
                    _probe_sqlite_json_each_support(mock_conn)

            self.assertIn("json_each() support is required", str(cm.exception))
            self.assertIn("3.45.1", str(cm.exception))
        finally:
            conn.close()

    def test_other_sqlite_errors_reraised_unchanged(self):
        """Test other sqlite3.Errors are re-raised unchanged (not wrapped in RuntimeError)."""
        conn = sqlite3.connect(self.db_path)
        try:
            original_error = sqlite3.DatabaseError("database disk image is malformed")
            mock_cursor = MagicMock()
            mock_cursor.fetchall.side_effect = original_error
            mock_conn = MagicMock()
            mock_conn.execute.return_value = mock_cursor

            with self.assertRaises(sqlite3.DatabaseError) as cm:
                _probe_sqlite_json_each_support(mock_conn)

            self.assertIs(cm.exception, original_error)
            self.assertNotIsInstance(cm.exception, RuntimeError)
        finally:
            conn.close()

    def test_operational_error_other_message_reraised_unchanged(self):
        """Test OperationalError with unrelated message is re-raised unchanged."""
        conn = sqlite3.connect(self.db_path)
        try:
            original_error = sqlite3.OperationalError("table not found")
            mock_cursor = MagicMock()
            mock_cursor.fetchall.side_effect = original_error
            mock_conn = MagicMock()
            mock_conn.execute.return_value = mock_cursor

            with self.assertRaises(sqlite3.OperationalError) as cm:
                _probe_sqlite_json_each_support(mock_conn)

            self.assertIs(cm.exception, original_error)
        finally:
            conn.close()

    def test_case_insensitive_error_matching(self):
        """Test that error message matching is case insensitive."""
        conn = sqlite3.connect(self.db_path)
        try:
            mock_cursor = MagicMock()
            mock_cursor.fetchall.side_effect = sqlite3.OperationalError(
                "NO SUCH FUNCTION: JSON_EACH"
            )
            mock_conn = MagicMock()
            mock_conn.execute.return_value = mock_cursor

            with patch(
                "mmrelay.db_runtime._get_sqlite_runtime_version_info",
                return_value=(3, 45, 1),
            ):
                with self.assertRaises(RuntimeError) as cm:
                    _probe_sqlite_json_each_support(mock_conn)

            self.assertIn("json_each() support is required", str(cm.exception))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
