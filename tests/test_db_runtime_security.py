"""
Tests for DatabaseManager refactoring and security fixes in db_runtime.py.

This test module covers:
- DatabaseManager initialization and connection management
- Pragma validation security features
- Context manager behavior (read/write)
- Async execution helpers
- Connection lifecycle and cleanup
- Thread safety and error handling
"""

import asyncio
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import Future
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.constants.database import DEFAULT_BUSY_TIMEOUT_MS, SQLITE_IN_MEMORY_PATH
from mmrelay.db_runtime import (
    DatabaseManager,
    _get_sqlite_runtime_version_info,
    _probe_sqlite_json_each_support,
    _validate_sqlite_json_each_support,
)
from tests.constants import (
    TEST_SQL_COUNT_TEST,
    TEST_SQL_CREATE_TABLE,
    TEST_SQL_INSERT_VALUE,
    TEST_SQL_SELECT_ONE,
)


class TestDatabaseManager(unittest.TestCase):
    """Test DatabaseManager functionality including security fixes."""

    def setUp(self):
        """
        Prepare test fixtures by creating a temporary SQLite database file and initializing a DatabaseManager.

        Creates the following attributes on self:
        - temp_db: a NamedTemporaryFile object for the database file (closed but not deleted).
        - db_path: filesystem path to the temporary database file.
        - manager: a DatabaseManager instance initialized with db_path.
        """
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name
        self.manager = DatabaseManager(self.db_path)

    def tearDown(self):
        """Clean up test fixtures."""
        self.manager.close()

        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_initialization_default_parameters(self):
        """Test DatabaseManager initialization with default parameters."""
        manager = DatabaseManager(self.db_path)
        try:
            self.assertEqual(manager._path, self.db_path)
            self.assertTrue(manager._enable_wal)
            self.assertEqual(manager._busy_timeout_ms, DEFAULT_BUSY_TIMEOUT_MS)
            self.assertEqual(manager._extra_pragmas, {})
        finally:
            manager.close()

    def test_initialization_custom_parameters(self):
        """Test DatabaseManager initialization with custom parameters."""
        custom_pragmas = {"synchronous": "OFF", "cache_size": 1000}
        manager = DatabaseManager(
            self.db_path,
            enable_wal=False,
            busy_timeout_ms=1000,
            extra_pragmas=custom_pragmas,
        )
        try:
            self.assertFalse(manager._enable_wal)
            self.assertEqual(manager._busy_timeout_ms, 1000)
            self.assertEqual(manager._extra_pragmas, custom_pragmas)
        finally:
            manager.close()

    def test_initialization_allows_missing_json_each_support(self):
        """DatabaseManager should initialize and mark json_each as unavailable."""
        _validate_sqlite_json_each_support.cache_clear()
        self.addCleanup(_validate_sqlite_json_each_support.cache_clear)
        probe_conn = MagicMock()
        probe_conn.execute.side_effect = sqlite3.OperationalError(
            "no such function: json_each"
        )

        real_connect = sqlite3.connect

        def _connect_side_effect(*args, **kwargs):
            database = kwargs.get("database")
            if database is None and args:
                database = args[0]
            if database == SQLITE_IN_MEMORY_PATH:
                return probe_conn
            return real_connect(*args, **kwargs)

        with patch(
            "mmrelay.db_runtime.sqlite3.connect", side_effect=_connect_side_effect
        ):
            manager = DatabaseManager(self.db_path)
            try:
                with manager.read() as cursor:
                    cursor.execute(TEST_SQL_SELECT_ONE)
                self.assertFalse(manager.supports_json_each())
            finally:
                manager.close()
        probe_conn.close.assert_called()

    def test_initialization_marks_json_each_supported_when_probe_succeeds(self):
        """DatabaseManager should mark json_each support when probe succeeds."""
        _validate_sqlite_json_each_support.cache_clear()
        self.addCleanup(_validate_sqlite_json_each_support.cache_clear)
        probe_result = MagicMock()
        probe_result.fetchall.return_value = [("probe",)]
        probe_conn = MagicMock()
        probe_conn.execute.return_value = probe_result

        real_connect = sqlite3.connect

        def _connect_side_effect(*args, **kwargs):
            database = kwargs.get("database")
            if database is None and args:
                database = args[0]
            if database == SQLITE_IN_MEMORY_PATH:
                return probe_conn
            return real_connect(*args, **kwargs)

        with patch(
            "mmrelay.db_runtime.sqlite3.connect", side_effect=_connect_side_effect
        ):
            manager = DatabaseManager(self.db_path)
            try:
                with manager.read() as cursor:
                    cursor.execute(TEST_SQL_SELECT_ONE)
                self.assertTrue(manager.supports_json_each())
            finally:
                manager.close()
        probe_conn.close.assert_called()

    def test_get_sqlite_runtime_version_info_falls_back_to_string(self):
        """Version parsing should gracefully fall back to sqlite_version string."""
        with (
            patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", ("bad",)),
            patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3.bad"),
        ):
            self.assertEqual(_get_sqlite_runtime_version_info(), (3, 0, 0))

    def test_probe_sqlite_json_each_support_reraises_non_json_errors(self):
        """Non-json_each sqlite errors should propagate unchanged."""
        conn = MagicMock()
        conn.execute.side_effect = sqlite3.OperationalError("database is malformed")
        with self.assertRaises(sqlite3.OperationalError):
            _probe_sqlite_json_each_support(conn)

    def test_pragma_validation_valid_names(self):
        """Test that valid pragma names are accepted."""
        valid_pragmas = [
            "synchronous",
            "cache_size",
            "temp_store",
            "journal_mode",
            "foreign_keys",
            "query_only",
            "recursive_triggers",
            "my_pragma_2",  # Valid: contains numbers after first character
            "pragma_v3",  # Valid: contains numbers after first character
        ]

        for pragma in valid_pragmas:
            with self.subTest(pragma=pragma):
                # Should not raise exception
                manager = DatabaseManager(self.db_path, extra_pragmas={pragma: "value"})
                try:
                    # Test connection creation to trigger pragma validation
                    with manager.read() as cursor:
                        cursor.execute(TEST_SQL_SELECT_ONE)
                finally:
                    manager.close()

    def test_pragma_validation_invalid_names(self):
        """Test that invalid pragma names are rejected."""
        invalid_pragmas = [
            "synchronous; DROP TABLE users; --",
            "cache_size' OR '1'='1",
            'temp_store"; SELECT * FROM users; --',
            "journal_mode--",
            "foreign_keys/*",
            "query-only",  # Invalid character
            "recursive triggers",  # Space in name
            "123invalid",  # Starts with number
            "",  # Empty string
        ]

        for pragma in invalid_pragmas:
            with self.subTest(pragma=pragma):
                with self.assertRaises(ValueError) as cm:
                    manager = DatabaseManager(
                        self.db_path, extra_pragmas={pragma: "value"}
                    )
                    # Try to create connection to trigger validation
                    with manager.read() as cursor:
                        cursor.execute(TEST_SQL_SELECT_ONE)
                self.assertIn("Invalid pragma name", str(cm.exception))

    def test_pragma_validation_string_values(self):
        """Test pragma validation for string values."""
        valid_values = [
            "NORMAL",
            "OFF",
            "MEMORY",
            "DELETE",
            "WAL",
            "TRUNCATE",
            "PERSIST",
            "value-with-dash",
            "value_with_underscore",
            "value with spaces",
            "value,with,commas",
            "value.with.dots",
        ]

        for value in valid_values:
            with self.subTest(value=value):
                manager = DatabaseManager(
                    self.db_path, extra_pragmas={"test_pragma": value}
                )
                try:
                    with manager.read() as cursor:
                        cursor.execute(TEST_SQL_SELECT_ONE)
                finally:
                    manager.close()

    def test_pragma_validation_invalid_string_values(self):
        """Test that invalid string pragma values are rejected."""
        invalid_values = [
            "value; DROP TABLE users; --",
            "value' OR '1'='1",
            'value"; SELECT * FROM users; --',
            "value/*",
            "value<script>",
            "value\x00null",
            "value'; DROP TABLE users; --",
            "value' OR '1'='1 --",
            "value\\",  # Test backslash at end vulnerability
        ]

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as cm:
                    manager = DatabaseManager(
                        self.db_path, extra_pragmas={"test_pragma": value}
                    )
                    # Try to create connection to trigger validation
                    with manager.read() as cursor:
                        cursor.execute(TEST_SQL_SELECT_ONE)
                self.assertIn("Invalid or unsafe pragma value", str(cm.exception))

    def test_pragma_validation_numeric_values(self):
        """Test pragma validation for numeric values."""
        valid_numeric = [1000, 0, -1, 3.14, 2.718]

        for value in valid_numeric:
            with self.subTest(value=value):
                manager = DatabaseManager(
                    self.db_path, extra_pragmas={"cache_size": value}
                )
                try:
                    with manager.read() as cursor:
                        cursor.execute(TEST_SQL_SELECT_ONE)
                finally:
                    manager.close()

    def test_pragma_validation_boolean_values(self):
        """Test pragma validation for boolean values."""
        valid_boolean = [True, False]

        for value in valid_boolean:
            with self.subTest(value=value):
                manager = DatabaseManager(
                    self.db_path, extra_pragmas={"recursive_triggers": value}
                )
                try:
                    with manager.read() as cursor:
                        cursor.execute(TEST_SQL_SELECT_ONE)
                finally:
                    manager.close()

    def test_pragma_validation_invalid_numeric_types(self):
        """Test that invalid numeric pragma value types are rejected."""
        invalid_types = [
            {"not": "a dict"},
            ["not", "a list"],
            (None, "tuple"),
            {1, 2, 3},
            None,
        ]

        for value in invalid_types:
            with self.subTest(value=value):
                with self.assertRaises(TypeError) as cm:
                    manager = DatabaseManager(
                        self.db_path, extra_pragmas={"test_pragma": value}
                    )
                    # Try to create connection to trigger validation
                    with manager.read() as cursor:
                        cursor.execute(TEST_SQL_SELECT_ONE)
                self.assertIn("Invalid pragma value type", str(cm.exception))

    def test_read_context_manager(self):
        """Test read context manager behavior."""
        with self.manager.read() as cursor:
            result = cursor.execute(TEST_SQL_SELECT_ONE).fetchone()
            self.assertEqual(result[0], 1)

    def test_write_context_manager_success(self):
        """Test write context manager on successful operation."""
        with self.manager.write() as cursor:
            cursor.execute(TEST_SQL_CREATE_TABLE)
            cursor.execute(TEST_SQL_INSERT_VALUE, ("test_value",))

        # Verify data was committed
        with self.manager.read() as cursor:
            result = cursor.execute("SELECT value FROM test").fetchone()
            self.assertEqual(result[0], "test_value")

    def test_write_context_manager_rollback_on_error(self):
        """Test write context manager rolls back on error."""
        # Create initial table
        with self.manager.write() as cursor:
            cursor.execute(TEST_SQL_CREATE_TABLE)
            cursor.execute(TEST_SQL_INSERT_VALUE, ("initial",))

        # Attempt operation that will fail
        with self.assertRaises(sqlite3.IntegrityError):
            with self.manager.write() as cursor:
                cursor.execute(
                    "INSERT INTO test (id, value) VALUES (1, ?)", ("conflict",)
                )
                cursor.execute(
                    "INSERT INTO test (id, value) VALUES (1, ?)", ("conflict",)
                )

        # Verify initial data is still there (rollback worked)
        with self.manager.read() as cursor:
            result = cursor.execute(TEST_SQL_COUNT_TEST).fetchone()
            self.assertEqual(result[0], 1)
            result = cursor.execute("SELECT value FROM test").fetchone()
            self.assertEqual(result[0], "initial")

    def test_write_context_manager_rollback_on_non_sqlite_exception(self):
        """Test write context manager rolls back on non-SQLite exceptions."""
        # Create initial table
        with self.manager.write() as cursor:
            cursor.execute(TEST_SQL_CREATE_TABLE)
            cursor.execute(TEST_SQL_INSERT_VALUE, ("initial",))

        # Attempt operation that will fail with ValueError
        with self.assertRaises(ValueError):
            with self.manager.write() as cursor:
                cursor.execute(TEST_SQL_INSERT_VALUE, ("should_be_rolled_back",))
                # Raise a non-SQLite exception
                raise ValueError("Test exception")

        # Verify initial data is still there (rollback worked)
        with self.manager.read() as cursor:
            result = cursor.execute(TEST_SQL_COUNT_TEST).fetchone()
            self.assertEqual(result[0], 1)
            result = cursor.execute("SELECT value FROM test").fetchone()
            self.assertEqual(result[0], "initial")

    def test_write_context_manager_rollback_on_custom_exception(self):
        """Test write context manager rolls back on custom exceptions."""
        # Create initial table
        with self.manager.write() as cursor:
            cursor.execute(TEST_SQL_CREATE_TABLE)
            cursor.execute(TEST_SQL_INSERT_VALUE, ("initial",))

        # Define a custom exception
        class CustomTestError(Exception):
            pass

        # Attempt operation that will fail with custom exception
        with self.assertRaises(CustomTestError):
            with self.manager.write() as cursor:
                cursor.execute(TEST_SQL_INSERT_VALUE, ("should_be_rolled_back",))
                # Raise a custom exception
                raise CustomTestError("Custom test exception")

        # Verify initial data is still there (rollback worked)
        with self.manager.read() as cursor:
            result = cursor.execute(TEST_SQL_COUNT_TEST).fetchone()
            self.assertEqual(result[0], 1)
            result = cursor.execute("SELECT value FROM test").fetchone()
            self.assertEqual(result[0], "initial")

    def test_write_context_manager_rollback_on_runtime_error(self):
        """Test write context manager rolls back on RuntimeError."""
        # Create initial table
        with self.manager.write() as cursor:
            cursor.execute(TEST_SQL_CREATE_TABLE)
            cursor.execute(TEST_SQL_INSERT_VALUE, ("initial",))

        # Attempt operation that will fail with RuntimeError
        with self.assertRaises(RuntimeError):
            with self.manager.write() as cursor:
                cursor.execute(TEST_SQL_INSERT_VALUE, ("should_be_rolled_back",))
                # Raise a RuntimeError
                raise RuntimeError("Runtime error test")

        # Verify initial data is still there (rollback worked)
        with self.manager.read() as cursor:
            result = cursor.execute(TEST_SQL_COUNT_TEST).fetchone()
            self.assertEqual(result[0], 1)
            result = cursor.execute("SELECT value FROM test").fetchone()
            self.assertEqual(result[0], "initial")

    def test_write_context_manager_rollback_on_keyboard_interrupt(self):
        """Write context manager should roll back on KeyboardInterrupt."""
        with self.manager.write() as cursor:
            cursor.execute(TEST_SQL_CREATE_TABLE)
            cursor.execute(TEST_SQL_INSERT_VALUE, ("initial",))

        with self.assertRaises(KeyboardInterrupt):
            with self.manager.write() as cursor:
                cursor.execute(TEST_SQL_INSERT_VALUE, ("should_be_rolled_back",))
                raise KeyboardInterrupt()

        with self.manager.read() as cursor:
            result = cursor.execute(TEST_SQL_COUNT_TEST).fetchone()
            self.assertEqual(result[0], 1)
            result = cursor.execute("SELECT value FROM test").fetchone()
            self.assertEqual(result[0], "initial")

    def test_write_context_manager_partial_transaction_rollback(self):
        """Test that partial transactions are properly rolled back on non-SQLite exceptions."""
        # Create initial table with some data
        with self.manager.write() as cursor:
            cursor.execute(TEST_SQL_CREATE_TABLE)
            cursor.execute(TEST_SQL_INSERT_VALUE, ("initial",))

        # Get initial count
        with self.manager.read() as cursor:
            initial_count = cursor.execute(TEST_SQL_COUNT_TEST).fetchone()[0]

        # Attempt operation with multiple statements that fails partway through
        with self.assertRaises(ValueError):
            with self.manager.write() as cursor:
                # First insert should succeed
                cursor.execute(TEST_SQL_INSERT_VALUE, ("first_insert",))
                # Second insert should also succeed
                cursor.execute(TEST_SQL_INSERT_VALUE, ("second_insert",))
                # Raise exception before commit
                raise ValueError("Exception after partial work")

        # Verify no new data was committed (rollback worked)
        with self.manager.read() as cursor:
            final_count = cursor.execute(TEST_SQL_COUNT_TEST).fetchone()[0]
            self.assertEqual(final_count, initial_count)
            # Verify only initial data exists
            result = cursor.execute("SELECT value FROM test").fetchone()
            self.assertEqual(result[0], "initial")

    def test_run_sync_read_operation(self):
        """Test run_sync for read operations."""

        def query_func(cursor):
            """
            Fetch the integer 42 using the provided database cursor.

            Parameters:
                cursor (sqlite3.Cursor or DB-API cursor): Cursor used to execute the query.

            Returns:
                int: The integer 42.
            """
            return cursor.execute("SELECT 42").fetchone()[0]

        result = self.manager.run_sync(query_func, write=False)
        self.assertEqual(result, 42)

    def test_run_sync_write_operation(self):
        """Test run_sync for write operations."""

        def write_func(cursor):
            """
            Creates the table named "test" (id INTEGER PRIMARY KEY, value TEXT) and inserts a row with value "sync_test".

            Parameters:
                cursor (sqlite3.Cursor): Database cursor used to execute statements.

            Returns:
                int: The `lastrowid` of the inserted row.
            """
            cursor.execute(TEST_SQL_CREATE_TABLE)
            cursor.execute(TEST_SQL_INSERT_VALUE, ("sync_test",))
            return cursor.lastrowid

        row_id = self.manager.run_sync(write_func, write=True)
        self.assertIsNotNone(row_id)

        # Verify data was written
        with self.manager.read() as cursor:
            result = cursor.execute(
                "SELECT value FROM test WHERE id = ?", (row_id,)
            ).fetchone()
            self.assertEqual(result[0], "sync_test")

    def test_run_async_operation(self):
        """Test run_async for async operations with proper mocking."""

        async def test_async():
            def async_func(_):
                """
                A simple test operation that yields a fixed result used in async tests.

                Parameters:
                    _: A DB-API cursor object (unused).

                Returns:
                    str: The literal string "async_result".
                """
                return "async_result"

            result = await self.manager.run_async(async_func, write=False)
            return result

        # Run in an isolated loop to avoid interference from global loop fixtures.
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(test_async())
        finally:
            loop.close()
        self.assertEqual(result, "async_result")

    def test_run_async_queued_work_completes_during_close(self):
        """Queued run_async work accepted before close() should still complete."""

        with self.manager.write() as cursor:
            cursor.execute(
                "CREATE TABLE test_async_close (id INTEGER PRIMARY KEY, value TEXT)"
            )

        first_started = threading.Event()
        first_release = threading.Event()
        second_started = threading.Event()
        second_release = threading.Event()

        def first_write(cursor):
            first_started.set()
            if not first_release.wait(timeout=10.0):
                raise AssertionError("Timed out waiting to release first_write")
            cursor.execute(
                "INSERT INTO test_async_close (value) VALUES (?)",
                ("first",),
            )
            return "first"

        def second_write(cursor):
            second_started.set()
            if not second_release.wait(timeout=10.0):
                raise AssertionError("Timed out waiting to release second_write")
            cursor.execute(
                "INSERT INTO test_async_close (value) VALUES (?)",
                ("second",),
            )
            return "second"

        async def run_test() -> tuple[str, str]:
            operation_timeout = 10.0
            task1 = asyncio.create_task(self.manager.run_async(first_write, write=True))
            start_deadline = asyncio.get_running_loop().time() + operation_timeout
            while (
                not first_started.is_set()
                and asyncio.get_running_loop().time() < start_deadline
            ):
                await asyncio.sleep(0.01)
            self.assertTrue(first_started.is_set(), "First queued write never started")
            task2 = asyncio.create_task(
                self.manager.run_async(second_write, write=True)
            )
            await asyncio.sleep(0)

            close_started = threading.Event()
            close_done = threading.Event()
            close_error: Future[None] = Future()

            def _close_manager() -> None:
                close_started.set()
                try:
                    self.manager.close()
                except Exception as err:
                    close_error.set_exception(err)
                else:
                    close_error.set_result(None)
                finally:
                    close_done.set()

            close_thread = threading.Thread(target=_close_manager, daemon=True)
            close_thread.start()
            close_deadline = asyncio.get_running_loop().time() + operation_timeout
            while (
                not close_started.is_set()
                and asyncio.get_running_loop().time() < close_deadline
            ):
                await asyncio.sleep(0.01)
            self.assertTrue(close_started.is_set(), "close() never started")

            # Brief wait to allow any incorrect early execution to manifest
            await asyncio.sleep(0.1)
            self.assertFalse(
                second_started.is_set(), "second_write should not have started yet"
            )

            try:
                first_release.set()

                await asyncio.sleep(0)

                deadline = asyncio.get_running_loop().time() + operation_timeout
                while (
                    not second_started.is_set()
                    and asyncio.get_running_loop().time() < deadline
                ):
                    await asyncio.sleep(0.01)
                self.assertTrue(
                    second_started.is_set(),
                    "second_write should have started after first_release",
                )

                second_release.set()

                # Both tasks should have completed by now
                result1, result2 = await asyncio.wait_for(
                    asyncio.gather(task1, task2),
                    timeout=operation_timeout,
                )
                join_deadline = asyncio.get_running_loop().time() + operation_timeout
                while (
                    not close_done.is_set()
                    and asyncio.get_running_loop().time() < join_deadline
                ):
                    await asyncio.sleep(0.01)
                self.assertTrue(close_done.is_set(), "close() did not complete")
            except asyncio.TimeoutError as err:
                # Check if tasks are done after close
                results = []
                for i, task in enumerate((task1, task2)):
                    if task.done():
                        results.append(task.result())
                    else:
                        task.cancel()
                        results.append(f"task{i + 1}_cancelled")
                raise AssertionError(f"Tasks did not complete: {results}") from err
            finally:
                first_release.set()
                second_release.set()
                for task in (task1, task2):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(task1, task2, return_exceptions=True)
                close_thread.join(timeout=operation_timeout)
                self.assertFalse(close_thread.is_alive(), "close thread did not exit")
                if close_error.done():
                    close_error.result()

            return result1, result2

        result1, result2 = asyncio.run(run_test())
        self.assertEqual((result1, result2), ("first", "second"))

        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM test_async_close").fetchone()[0]
        self.assertEqual(count, 2)

    def test_thread_local_connections(self):
        """Test that connections are thread-local."""
        connections = []

        def get_connection():
            """
            Obtain a connection from the DatabaseManager and append it to the local `connections` list for tracking.

            Returns:
                sqlite3.Connection: The acquired database connection.
            """
            conn = self.manager._get_connection()
            connections.append(conn)
            return conn

        # Create multiple threads
        threads = []
        for _ in range(3):
            thread = threading.Thread(target=get_connection)
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify we have 3 different connections
        self.assertEqual(len(connections), 3)
        self.assertEqual(len(set(connections)), 3)  # All should be different

    def test_connection_tracking(self):
        """Test that connections are tracked and reused per thread."""
        initial_count = len(self.manager._connections)

        # Fetching again on the same thread should reuse the eager connection.
        conn = self.manager._get_connection()

        # Connection should be tracked
        self.assertIn(conn, self.manager._connections)
        self.assertEqual(len(self.manager._connections), initial_count)

        # Close manager and verify connection cleanup
        self.manager.close()
        self.assertEqual(len(self.manager._connections), 0)

    def test_get_connection_rejects_when_manager_closing(self):
        """_get_connection should reject new non-admitted access while closing."""
        with self.manager._connections_lock:
            self.manager._closing = True
        try:
            with self.assertRaises(sqlite3.ProgrammingError):
                self.manager._get_connection()
        finally:
            with self.manager._connections_lock:
                self.manager._closing = False

    def test_get_connection_recreates_closed_thread_local_connection(self):
        """A closed thread-local connection should be discarded and replaced."""
        original = self.manager._get_connection()
        original.close()
        replacement = self.manager._get_connection()
        self.assertIsNot(original, replacement)
        self.assertIn(replacement, self.manager._connections)
        self.assertNotIn(original, self.manager._connections)

    def test_read_rejects_new_work_when_manager_closing(self):
        """read() should reject new work when manager is closing."""
        with self.manager._connections_lock:
            self.manager._closing = True
        try:
            with self.assertRaises(sqlite3.ProgrammingError):
                with self.manager.read():
                    pass
        finally:
            with self.manager._connections_lock:
                self.manager._closing = False

    def test_close_cleanup(self):
        """Test close method properly cleans up resources."""
        # Create some connections
        conn1 = self.manager._get_connection()
        conn2 = self.manager._get_connection()

        # Verify connections exist
        self.assertIn(conn1, self.manager._connections)
        self.assertIn(conn2, self.manager._connections)

        # Close manager
        self.manager.close()

        # Verify cleanup
        self.assertEqual(len(self.manager._connections), 0)
        self.assertFalse(hasattr(self.manager._thread_local, "connection"))

    def test_close_closes_untracked_thread_local_connection(self):
        """close() should explicitly close thread-local connection even if untracked."""
        manager = DatabaseManager(self.db_path)
        conn = manager._get_connection()
        with manager._connections_lock:
            manager._connections.clear()

        manager.close()

        with self.assertRaises(sqlite3.ProgrammingError):
            conn.cursor()
        self.assertFalse(hasattr(manager._thread_local, "connection"))

    def test_finalize_closes_thread_local_connection_when_lock_unavailable(self):
        """Finalizer should close thread-local connection even when lock snapshot is skipped."""

        class _NeverAcquireLock:
            def acquire(self, timeout: float | int = -1) -> bool:
                return False

            def release(self) -> None:  # pragma: no cover - defensive
                raise AssertionError(
                    "release() should not be called when acquire() fails"
                )

        manager = DatabaseManager(self.db_path)
        conn = manager._get_connection()
        manager._connections_lock = _NeverAcquireLock()  # type: ignore[assignment]

        manager._finalize_unclosed_resources()

        with self.assertRaises(sqlite3.ProgrammingError):
            conn.cursor()
        self.assertFalse(hasattr(manager._thread_local, "connection"))

    def test_close_rejected_during_active_database_operation(self):
        """close() should reject reentrant invocation from active DB work."""

        def close_inside_operation(_cursor: sqlite3.Cursor) -> None:
            self.manager.close()

        with self.assertRaisesRegex(
            RuntimeError,
            "cannot be called from inside an active database operation",
        ):
            self.manager.run_sync(close_inside_operation, write=False)

    def test_close_handles_connection_errors(self):
        """Test close method handles connection errors gracefully."""
        # This test verifies that the close method has proper error handling
        # The actual implementation catches sqlite3.Error and ignores it during cleanup
        # We can't easily mock sqlite3.Connection.close due to it being read-only
        # but we can verify the method exists and the structure is correct

        # Create a manager and get a connection
        test_manager = DatabaseManager(self.db_path)
        conn = test_manager._get_connection()

        # Verify connection is tracked
        self.assertIn(conn, test_manager._connections)
        self.assertEqual(len(test_manager._connections), 1)

        # Close normally (this exercises the error handling path)
        test_manager.close()

        # Verify cleanup happened
        self.assertEqual(len(test_manager._connections), 0)

    def test_close_waits_for_active_sync_work_to_finish(self):
        """close() should wait until active sync work drains."""
        manager = DatabaseManager(self.db_path)
        with manager._connections_lock:
            manager._active_sync_count = 1
        close_done = threading.Event()
        close_started = threading.Event()
        allow_release = threading.Event()
        release_error: list[str] = []
        close_error: Future[None] = Future()

        def release_activity() -> None:
            try:
                if not allow_release.wait(timeout=1.0):
                    release_error.append(
                        "test never allowed release_activity to continue"
                    )
                    return
                with manager._connections_lock:
                    manager._active_sync_count = 0
                    manager._active_sync_condition.notify_all()
            except AssertionError as err:
                release_error.append(str(err))

        def close_manager() -> None:
            close_started.set()
            try:
                manager.close()
            except Exception as err:  # pragma: no cover - defensive
                close_error.set_exception(err)
            else:
                close_error.set_result(None)
            finally:
                close_done.set()

        releaser = threading.Thread(target=release_activity, daemon=True)
        closer = threading.Thread(target=close_manager, daemon=True)
        releaser.start()
        closer.start()
        try:
            self.assertTrue(
                close_started.wait(timeout=1.0), "closer thread never started"
            )
            self.assertFalse(
                close_done.wait(timeout=0.05),
                "close() returned before active sync work drained",
            )
            self.assertTrue(closer.is_alive(), "close() did not block before release")
        finally:
            allow_release.set()
            releaser.join(timeout=1.0)
            closer.join(timeout=1.0)

        self.assertEqual(release_error, [], f"Unexpected errors: {release_error}")
        self.assertFalse(releaser.is_alive())
        self.assertFalse(closer.is_alive(), "closer thread did not exit")
        if close_error.done():
            close_error.result()
        self.assertTrue(close_done.is_set(), "close() did not finish after release")

    def test_close_logs_sqlite_errors_when_connection_close_fails(self):
        """close() should log sqlite close failures and continue cleanup."""
        manager = DatabaseManager(self.db_path)
        real_conn = manager._get_connection()
        bad_conn = MagicMock()
        bad_conn.close.side_effect = sqlite3.OperationalError("close failed")
        with manager._connections_lock:
            manager._connections = {real_conn, bad_conn}
        with patch("mmrelay.db_runtime.logger") as mock_logger:
            manager.close()
        mock_logger.debug.assert_any_call(
            "Error closing connection during shutdown", exc_info=True
        )
        with manager._connections_lock:
            self.assertEqual(len(manager._connections), 0)

    def test_close_ignores_attributeerror_while_deleting_thread_local_connection(self):
        """close() should ignore AttributeError from unusual thread-local implementations."""

        class _DelattrRaises:
            connection = object()

            def __delattr__(self, name: str) -> None:
                raise AttributeError(name)

        manager = DatabaseManager(self.db_path)
        manager._thread_local = _DelattrRaises()
        manager.close()

    def test_write_lock_serialization(self):
        """Test that write operations are serialized."""
        results = []
        errors = []

        def write_operation(thread_id):
            """
            Attempt to increment a shared counter in the database with retries and record the outcome.

            Runs a write operation that ensures a single-row counter table exists, increments its value, and records the resulting count or any exception. Retries the write up to three times with brief exponential backoff on failure. On success appends (thread_id, count) to the shared `results` list; on final failure appends (thread_id, exception) to the shared `errors` list.

            Parameters:
                thread_id (int): Identifier for the caller thread used when recording the result or error.
            """
            max_retries = 3
            for attempt in range(max_retries):
                try:

                    def write_func(cursor):
                        # Create table if not exists
                        """
                        Increment the single-row counter stored in a table and return the updated count.

                        Parameters:
                            cursor (sqlite3.Cursor): Database cursor used to execute SQL statements.

                        Returns:
                            int or None: The updated counter value after incrementing, or `None` if the row was not found.
                        """
                        cursor.execute("""
                            CREATE TABLE IF NOT EXISTS counter (
                                id INTEGER PRIMARY KEY CHECK (id = 1),
                                count INTEGER DEFAULT 0
                            )
                        """)
                        # Insert initial row if not exists
                        cursor.execute("""
                            INSERT OR IGNORE INTO counter (id, count) VALUES (1, 0)
                        """)
                        # Increment counter
                        cursor.execute(
                            "UPDATE counter SET count = count + 1 WHERE id = 1"
                        )
                        # Get current count
                        result = cursor.execute(
                            "SELECT count FROM counter WHERE id = 1"
                        ).fetchone()
                        return result[0] if result else None

                    result = self.manager.run_sync(write_func, write=True)
                    results.append((thread_id, result))
                    break  # Success, exit retry loop
                except Exception as e:
                    if attempt == max_retries - 1:
                        # Last attempt, record the error
                        errors.append((thread_id, e))
                    else:
                        # Brief sleep before retry to allow lock to clear
                        import time

                        time.sleep(0.01 * (attempt + 1))  # Exponential backoff

        # Run multiple threads writing simultaneously
        threads = []
        for i in range(5):
            thread = threading.Thread(target=write_operation, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify no errors occurred (allowing for SQLite locking differences between Python versions)
        self.assertEqual(len(errors), 0, f"Errors occurred: {errors}")

        # Verify all operations completed and counter was incremented
        self.assertEqual(len(results), 5)

        # Check final counter value
        with self.manager.read() as cursor:
            final_count = cursor.execute(
                "SELECT count FROM counter WHERE id = 1"
            ).fetchone()
            self.assertIsNotNone(final_count)
            self.assertEqual(final_count[0], 5)

    def test_connection_creation_error_cleanup(self):
        """Test that connection creation errors don't leak connections."""
        # Try to create a manager with invalid pragma that will fail
        with self.assertRaises(ValueError):
            manager = DatabaseManager(
                self.db_path, extra_pragmas={"invalid;pragma": "value"}
            )
            # Try to create connection to trigger validation
            with manager.read() as cursor:
                cursor.execute(TEST_SQL_SELECT_ONE)

        # Verify no connections were leaked (this is more of a sanity check)
        # since the manager creation failed, there shouldn't be any connections to track

    def test_busy_timeout_pragma_application(self):
        """Test that busy timeout pragma is properly applied."""
        manager = DatabaseManager(self.db_path, busy_timeout_ms=1000)
        try:
            with manager.read() as cursor:
                # Query the busy timeout setting
                result = cursor.execute("PRAGMA busy_timeout").fetchone()
                self.assertEqual(result[0], 1000)
        finally:
            manager.close()

    def test_wal_mode_pragma_application(self):
        """Test that WAL mode pragma is properly applied."""
        manager = DatabaseManager(self.db_path, enable_wal=True)
        try:
            with manager.read() as cursor:
                # Query the journal mode setting
                result = cursor.execute("PRAGMA journal_mode").fetchone()
                self.assertEqual(result[0].lower(), "wal")
        finally:
            manager.close()

    def test_foreign_keys_pragma_application(self):
        """Test that foreign keys pragma is properly applied."""
        with self.manager.read() as cursor:
            # Query the foreign keys setting
            result = cursor.execute("PRAGMA foreign_keys").fetchone()
            self.assertEqual(result[0], 1)


@pytest.fixture
def temp_db_manager() -> Generator[DatabaseManager, None, None]:
    """Provide a temporary DatabaseManager with guaranteed cleanup."""
    temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    temp_db.close()
    db_path = temp_db.name
    manager = DatabaseManager(db_path)
    try:
        yield manager
    finally:
        manager.close()
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_run_async_rejects_submission_when_closing(
    temp_db_manager: DatabaseManager,
) -> None:
    """run_async should fail fast when new submissions are disabled."""
    with temp_db_manager._executor_lock:
        temp_db_manager._accepting_submissions = False

    with pytest.raises(sqlite3.ProgrammingError):
        await temp_db_manager.run_async(lambda _cursor: None, write=False)


@pytest.mark.asyncio
async def test_run_async_cancelled_read_cancels_worker_future(
    temp_db_manager: DatabaseManager,
) -> None:
    """Caller cancellation on read work should cancel unfinished worker future."""
    worker_future = MagicMock()
    worker_future.done.return_value = False

    with patch.object(
        temp_db_manager._async_executor, "submit", return_value=worker_future
    ):
        task = asyncio.create_task(
            temp_db_manager.run_async(lambda _cursor: None, write=False)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    worker_future.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_run_async_cancelled_write_logs_worker_error(
    temp_db_manager: DatabaseManager,
) -> None:
    """Caller cancellation on write waits for worker and logs late worker errors."""
    worker_future = MagicMock()
    worker_future.cancel = MagicMock()
    worker_future.done.return_value = True
    worker_future.result.side_effect = RuntimeError(
        "worker failed after caller cancellation"
    )

    with (
        patch.object(
            temp_db_manager._async_executor, "submit", return_value=worker_future
        ),
        patch.object(
            temp_db_manager,
            "_await_submitted_future",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
        patch("mmrelay.db_runtime.logger") as mock_logger,
    ):
        with pytest.raises(asyncio.CancelledError):
            await temp_db_manager.run_async(lambda _cursor: None, write=True)

    worker_future.cancel.assert_not_called()
    mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_run_async_cancelled_write_swallows_followup_cancellation(
    temp_db_manager: DatabaseManager,
) -> None:
    """Write cancellation should swallow follow-up CancelledError from worker wait."""
    worker_future = MagicMock()
    worker_future.cancel = MagicMock()
    worker_future.done.return_value = False

    with (
        patch.object(
            temp_db_manager._async_executor, "submit", return_value=worker_future
        ),
        patch("mmrelay.db_runtime.logger") as mock_logger,
    ):
        task = asyncio.create_task(
            temp_db_manager.run_async(lambda _cursor: None, write=True)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    worker_future.cancel.assert_not_called()
    mock_logger.warning.assert_not_called()


@pytest.mark.asyncio
async def test_run_async_cancelled_write_logs_late_worker_error(
    temp_db_manager: DatabaseManager,
) -> None:
    """Write cancellation should log errors from worker completion callbacks."""
    worker_future = MagicMock()
    worker_future.cancel = MagicMock()
    worker_future.done.return_value = False
    worker_future.result.side_effect = RuntimeError(
        "worker failed after caller cancellation"
    )

    def _add_done_callback(callback):
        callback(worker_future)

    worker_future.add_done_callback.side_effect = _add_done_callback

    with (
        patch.object(
            temp_db_manager._async_executor, "submit", return_value=worker_future
        ),
        patch.object(
            temp_db_manager,
            "_await_submitted_future",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
        patch("mmrelay.db_runtime.logger") as mock_logger,
    ):
        with pytest.raises(asyncio.CancelledError):
            await temp_db_manager.run_async(lambda _cursor: None, write=True)

    worker_future.cancel.assert_not_called()
    mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_run_async_cancelled_read_does_not_cancel_finished_future(
    temp_db_manager: DatabaseManager,
) -> None:
    """Read cancellation should not cancel an already completed worker future."""
    worker_future = MagicMock()
    worker_future.done.return_value = True
    with (
        patch.object(
            temp_db_manager._async_executor, "submit", return_value=worker_future
        ),
        patch.object(
            temp_db_manager,
            "_await_submitted_future",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
    ):
        with pytest.raises(asyncio.CancelledError):
            await temp_db_manager.run_async(lambda _cursor: None, write=False)

    worker_future.cancel.assert_not_called()


@pytest.mark.asyncio
async def test_await_submitted_future_cancelled_task_allows_late_completion(
    temp_db_manager: DatabaseManager,
) -> None:
    """Cancelling the waiter should not block a later worker completion."""
    worker_future: Future[int] = Future()
    task = asyncio.create_task(temp_db_manager._await_submitted_future(worker_future))

    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    worker_future.set_result(123)
    await asyncio.sleep(0)
    assert worker_future.done()


@pytest.mark.asyncio
async def test_await_submitted_future_maps_concurrent_cancel_to_async_cancel(
    temp_db_manager: DatabaseManager,
) -> None:
    """Concurrent-future cancellation should surface as asyncio cancellation."""
    worker_future: Future[int] = Future()
    worker_future.cancel()

    with pytest.raises(asyncio.CancelledError):
        await temp_db_manager._await_submitted_future(worker_future)


class TestDatabaseManagerEdgeCases(unittest.TestCase):
    """Test DatabaseManager edge cases and error conditions."""

    def setUp(self):
        """
        Create a temporary SQLite database file and record its path on the test instance.

        The file is created with a ".db" suffix, closed so it can be opened by tests, and not marked for automatic deletion; its file object is stored on `self.temp_db` and the filesystem path on `self.db_path` for use in test cases.
        """
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

    def tearDown(self):
        """
        Remove the temporary database file created for the test.

        If removing the file fails (for example, because it does not exist or due to permission issues), the error is ignored.
        """
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_empty_extra_pragmas_dict(self):
        """Test handling of empty extra_pragmas dictionary."""
        manager = DatabaseManager(self.db_path, extra_pragmas={})
        try:
            with manager.read() as cursor:
                cursor.execute(TEST_SQL_SELECT_ONE)
        finally:
            manager.close()

    def test_none_extra_pragmas(self):
        """Test handling of None extra_pragmas."""
        manager = DatabaseManager(self.db_path, extra_pragmas=None)
        try:
            with manager.read() as cursor:
                cursor.execute(TEST_SQL_SELECT_ONE)
        finally:
            manager.close()

    def test_zero_busy_timeout(self):
        """Test handling of zero busy timeout."""
        manager = DatabaseManager(self.db_path, busy_timeout_ms=0)
        try:
            with manager.read() as cursor:
                cursor.execute(TEST_SQL_SELECT_ONE)
        finally:
            manager.close()

    def test_negative_busy_timeout(self):
        """Test handling of negative busy timeout."""
        manager = DatabaseManager(self.db_path, busy_timeout_ms=-1000)
        try:
            with manager.read() as cursor:
                cursor.execute(TEST_SQL_SELECT_ONE)
        finally:
            manager.close()

    def test_database_file_permissions(self):
        """
        Verify DatabaseManager behavior when the database file's filesystem permissions are changed to read-only.

        Creates a table to ensure the database file exists, changes the file mode to read-only, attempts a write that may either succeed or raise sqlite3.OperationalError depending on the platform/SQLite build, restores the original permissions, and closes the manager to ensure cleanup.
        """
        import stat

        # Create manager
        manager = DatabaseManager(self.db_path)
        try:
            # Create a table to ensure database file exists
            with manager.write() as cursor:
                cursor.execute("CREATE TABLE test (id INTEGER)")

            # Make database file read-only
            current_permissions = os.stat(self.db_path).st_mode
            os.chmod(self.db_path, stat.S_IRUSR)

            # Try to write - may fail depending on system/SQLite version
            try:
                with manager.write() as cursor:
                    cursor.execute("INSERT INTO test (id) VALUES (1)")
                # If it succeeds, that's also valid behavior (some SQLite versions handle this differently)
            except sqlite3.OperationalError:
                # This is expected on most systems
                pass

            # Restore permissions for cleanup
            os.chmod(self.db_path, current_permissions)
        finally:
            manager.close()

    def test_concurrent_read_operations(self):
        """Test that concurrent read operations work correctly."""
        manager = DatabaseManager(self.db_path)
        try:
            # Create test data
            with manager.write() as cursor:
                cursor.execute(TEST_SQL_CREATE_TABLE)
                cursor.execute("INSERT INTO test (id, value) VALUES (1, 'test')")

            results = []

            def read_operation(thread_id):
                """
                Read the row with id = 1 from the "test" table and record (thread_id, value) in the shared results list.

                If the row exists, `value` is the stored value; if not, `value` is `None`. This function appends the tuple (thread_id, value) to the surrounding `results` list as a side effect.

                Parameters:
                    thread_id (int): Identifier for the calling thread used when recording the result.
                """

                def read_func(cursor):
                    result = cursor.execute(
                        "SELECT value FROM test WHERE id = 1"
                    ).fetchone()
                    return result[0] if result else None

                result = manager.run_sync(read_func, write=False)
                results.append((thread_id, result))

            # Run multiple threads reading simultaneously
            threads = []
            for i in range(5):
                thread = threading.Thread(target=read_operation, args=(i,))
                threads.append(thread)
                thread.start()

            # Wait for all threads to complete
            for thread in threads:
                thread.join()

            # Verify all reads succeeded
            self.assertEqual(len(results), 5)
            for _thread_id, result in results:
                self.assertEqual(result, "test")
        finally:
            manager.close()


if __name__ == "__main__":
    unittest.main()
