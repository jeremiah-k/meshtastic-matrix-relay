"""
Tests for db_runtime.py utility functions.

Covers:
- _get_sqlite_runtime_version_info: version parsing and normalization
- _probe_sqlite_json_each_support: json_each support detection
"""

import asyncio
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from mmrelay.db_runtime import (
    _get_sqlite_runtime_version_info,
    _probe_sqlite_json_each_support,
)


class TestGetSqliteRuntimeVersionInfo:
    """Test _get_sqlite_runtime_version_info function."""

    def test_valid_tuple_with_three_ints_returns_normalized(self):
        """Test when sqlite_version_info is a valid tuple with 3+ ints - returns normalized tuple."""
        with patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", (3, 45, 1)):
            result = _get_sqlite_runtime_version_info()
            assert result == (3, 45, 1)

    def test_valid_tuple_with_more_than_three_ints_returns_first_three(self):
        """Test when sqlite_version_info has more than 3 parts - returns first 3."""
        with patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", (3, 45, 1, 2, 3)):
            result = _get_sqlite_runtime_version_info()
            assert result == (3, 45, 1)

    def test_none_version_info_falls_back_to_string_parsing(self):
        """Test when sqlite_version_info is None - falls back to sqlite_version string parsing."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", None),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3.45.1"),
        ):
            result = _get_sqlite_runtime_version_info()
            assert result == (3, 45, 1)

    def test_non_tuple_version_info_falls_back_to_string_parsing(self):
        """Test when sqlite_version_info is not a tuple - falls back to string parsing."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", "3.45.1"),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3.45.1"),
        ):
            result = _get_sqlite_runtime_version_info()
            assert result == (3, 45, 1)

    def test_string_fewer_than_three_parts_pads_with_zeros(self):
        """Test when sqlite_version string has fewer than 3 parts - pads with zeros."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", None),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3.45"),
        ):
            result = _get_sqlite_runtime_version_info()
            assert result == (3, 45, 0)

    def test_string_single_part_pads_with_zeros(self):
        """Test when sqlite_version string has only 1 part - pads with zeros."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", None),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3"),
        ):
            result = _get_sqlite_runtime_version_info()
            assert result == (3, 0, 0)

    def test_string_non_numeric_parts_uses_zero(self):
        """Test when sqlite_version string has non-numeric parts - uses 0 for those parts."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", None),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3.abc.1"),
        ):
            result = _get_sqlite_runtime_version_info()
            assert result == (3, 0, 1)

    def test_string_all_non_numeric_parts_uses_zeros(self):
        """Test when sqlite_version string has all non-numeric parts."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", None),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "a.b.c"),
        ):
            result = _get_sqlite_runtime_version_info()
            assert result == (0, 0, 0)

    def test_tuple_with_non_int_parts_falls_back_to_string(self):
        """Test when sqlite_version_info tuple has non-int parts - falls back to string."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", (3, "45", 1)),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3.45.1"),
        ):
            result = _get_sqlite_runtime_version_info()
            assert result == (3, 45, 1)

    def test_tuple_with_less_than_three_parts_falls_back_to_string(self):
        """Test when sqlite_version_info tuple has fewer than 3 parts - falls back to string."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", (3, 45)),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3.45.1"),
        ):
            result = _get_sqlite_runtime_version_info()
            assert result == (3, 45, 1)


@pytest.mark.asyncio
async def test_await_submitted_future_signal_done_handles_runtime_error():
    """_signal_done callback should swallow RuntimeError when loop is shutting down."""

    class FakeEvent:
        def __init__(self):
            self._set = False

        def set(self):
            self._set = True

    fake_future = MagicMock()
    fake_future.done.return_value = False
    fake_future.result.return_value = "ok"

    manager = MagicMock()
    manager._resolve_submitted_future_result = staticmethod(lambda f: f.result())

    from concurrent.futures import Future as RealFuture

    worker = RealFuture()
    worker.set_result("done")

    import os
    import tempfile

    from mmrelay.db_runtime import DatabaseManager

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    db = DatabaseManager(tmp.name)
    try:
        closed_loop = asyncio.new_event_loop()
        closed_loop.close()

        with (
            patch(
                "mmrelay.db_runtime.asyncio.get_running_loop", return_value=closed_loop
            ),
            patch("mmrelay.db_runtime.asyncio.Event", return_value=FakeEvent()),
        ):
            result = await db._await_submitted_future(worker)
            assert result == "done"
    finally:
        db.close()
        os.unlink(tmp.name)


def test_log_write_future_error_after_cancellation_done_future():
    """When worker_future is already done, _log_future_error is called immediately."""

    class FakeFuture:
        def __init__(self, exc=None):
            self._exc = exc

        def done(self):
            return True

        def result(self):
            if self._exc:
                raise self._exc
            return None

    import os
    import tempfile

    from mmrelay.db_runtime import DatabaseManager

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    db = DatabaseManager(tmp.name)
    try:
        with patch("mmrelay.db_runtime.logger") as mock_logger:
            db._log_write_future_error_after_cancellation(
                FakeFuture(exc=RuntimeError("boom"))
            )
        mock_logger.warning.assert_called_once()
    finally:
        db.close()
        os.unlink(tmp.name)


def test_log_write_future_error_after_cancellation_done_cancelled():
    """Cancelled worker future should not trigger warning log."""
    import os
    import tempfile
    from concurrent.futures import CancelledError as ConcurrentCancelledError

    from mmrelay.db_runtime import DatabaseManager

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    db = DatabaseManager(tmp.name)
    try:

        class CancelledFuture:
            def done(self):
                return True

            def result(self):
                raise ConcurrentCancelledError()

        with patch("mmrelay.db_runtime.logger") as mock_logger:
            db._log_write_future_error_after_cancellation(CancelledFuture())
        mock_logger.warning.assert_not_called()
    finally:
        db.close()
        os.unlink(tmp.name)


class TestProbeSqliteJsonEachSupport:
    """Test _probe_sqlite_json_each_support function."""

    def test_no_such_function_json_each_raises_runtime_error(self):
        """Test when error contains 'no such function: json_each' - raises RuntimeError."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = sqlite3.OperationalError(
            "no such function: json_each"
        )
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor

        with patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", (3, 45, 1)):
            with pytest.raises(RuntimeError) as cm:
                _probe_sqlite_json_each_support(mock_conn)

        assert "json_each() support is required" in str(cm.value)
        assert "3.45.1" in str(cm.value)

    def test_no_such_table_json_each_raises_runtime_error(self):
        """Test when error contains 'no such table: json_each' - raises RuntimeError."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = sqlite3.OperationalError(
            "no such table: json_each"
        )
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor

        with patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", (3, 45, 1)):
            with pytest.raises(RuntimeError) as cm:
                _probe_sqlite_json_each_support(mock_conn)

        assert "json_each() support is required" in str(cm.value)
        assert "3.45.1" in str(cm.value)

    def test_other_sqlite_errors_reraised_unchanged(self):
        """Test other sqlite3.Errors are re-raised unchanged (not wrapped in RuntimeError)."""
        original_error = sqlite3.DatabaseError("database disk image is malformed")
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = original_error
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor

        with pytest.raises(sqlite3.DatabaseError) as cm:
            _probe_sqlite_json_each_support(mock_conn)

        assert cm.value is original_error
        assert not isinstance(cm.value, RuntimeError)

    def test_operational_error_other_message_reraised_unchanged(self):
        """Test OperationalError with unrelated message is re-raised unchanged."""
        original_error = sqlite3.OperationalError("table not found")
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = original_error
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor

        with pytest.raises(sqlite3.OperationalError) as cm:
            _probe_sqlite_json_each_support(mock_conn)

        assert cm.value is original_error

    def test_case_insensitive_error_matching(self):
        """Test that error message matching is case insensitive."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = sqlite3.OperationalError(
            "NO SUCH FUNCTION: JSON_EACH"
        )
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor

        with patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", (3, 45, 1)):
            with pytest.raises(RuntimeError) as cm:
                _probe_sqlite_json_each_support(mock_conn)

        assert "json_each() support is required" in str(cm.value)

    def test_json_each_supported_completes_without_error(self):
        """Test when json_each is supported - function completes without raising."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("probe",)]
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_cursor

        _probe_sqlite_json_each_support(mock_conn)

        mock_conn.execute.assert_called_once()
        mock_cursor.fetchall.assert_called_once()
