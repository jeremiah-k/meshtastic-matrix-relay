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
import contextlib
import os
import sqlite3
import stat
import tempfile
import threading
import time
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

# ========================================================================
# Fixtures
# ========================================================================


@pytest.fixture
def db_path():
    """Create a temporary SQLite database file path. Guarantees cleanup on teardown."""
    temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    temp_db.close()
    path = temp_db.name
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def db_manager(db_path):
    """
    Create a DatabaseManager on a temporary database path,
    and yield both for use in tests. Guarantees cleanup on teardown.

    Yields:
        tuple[DatabaseManager, str]: (manager, db_path)
    """
    manager = DatabaseManager(db_path)
    yield manager, db_path
    manager.close()


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


# ========================================================================
# Initialization Tests
# ========================================================================


def test_initialization_default_parameters(db_manager):
    """Test DatabaseManager initialization with default parameters."""
    manager, db_path = db_manager
    try:
        assert manager._path == db_path
        assert manager._enable_wal is True
        assert manager._busy_timeout_ms == DEFAULT_BUSY_TIMEOUT_MS
        assert manager._extra_pragmas == {}
    finally:
        manager.close()


def test_initialization_custom_parameters(db_path):
    """Test DatabaseManager initialization with custom parameters."""
    custom_pragmas = {"synchronous": "OFF", "cache_size": 1000}
    manager = DatabaseManager(
        db_path,
        enable_wal=False,
        busy_timeout_ms=1000,
        extra_pragmas=custom_pragmas,
    )
    try:
        assert manager._enable_wal is False
        assert manager._busy_timeout_ms == 1000
        assert manager._extra_pragmas == custom_pragmas
    finally:
        manager.close()


def test_initialization_allows_missing_json_each_support(db_path):
    """DatabaseManager should initialize and mark json_each as unavailable."""
    _validate_sqlite_json_each_support.cache_clear()
    try:
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
            manager = DatabaseManager(db_path)
            try:
                with manager.read() as cursor:
                    cursor.execute(TEST_SQL_SELECT_ONE)
                assert not manager.supports_json_each()
            finally:
                manager.close()
        probe_conn.close.assert_called()
    finally:
        _validate_sqlite_json_each_support.cache_clear()


def test_initialization_marks_json_each_supported_when_probe_succeeds(db_path):
    """DatabaseManager should mark json_each support when probe succeeds."""
    _validate_sqlite_json_each_support.cache_clear()
    try:
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
            manager = DatabaseManager(db_path)
            try:
                with manager.read() as cursor:
                    cursor.execute(TEST_SQL_SELECT_ONE)
                assert manager.supports_json_each()
            finally:
                manager.close()
        probe_conn.close.assert_called()
    finally:
        _validate_sqlite_json_each_support.cache_clear()


def test_get_sqlite_runtime_version_info_falls_back_to_string():
    """Version parsing should gracefully fall back to sqlite_version string."""
    with (
        patch("mmrelay.db_runtime.sqlite3.sqlite_version_info", ("bad",)),
        patch("mmrelay.db_runtime.sqlite3.sqlite_version", "3.bad"),
    ):
        assert _get_sqlite_runtime_version_info() == (3, 0, 0)


def test_probe_sqlite_json_each_support_reraises_non_json_errors():
    """Non-json_each sqlite errors should propagate unchanged."""
    conn = MagicMock()
    conn.execute.side_effect = sqlite3.OperationalError("database is malformed")
    with pytest.raises(sqlite3.OperationalError):
        _probe_sqlite_json_each_support(conn)


# ========================================================================
# Pragma Validation Tests
# ========================================================================


@pytest.mark.parametrize(
    "pragma",
    [
        "synchronous",
        "cache_size",
        "temp_store",
        "journal_mode",
        "foreign_keys",
        "query_only",
        "recursive_triggers",
        "my_pragma_2",  # Valid: contains numbers after first character
        "pragma_v3",  # Valid: contains numbers after first character
    ],
)
def test_pragma_validation_valid_names(pragma, db_path):
    """Test that valid pragma names are accepted."""
    manager = DatabaseManager(db_path, extra_pragmas={pragma: "value"})
    try:
        with manager.read() as cursor:
            cursor.execute(TEST_SQL_SELECT_ONE)
    finally:
        manager.close()


@pytest.mark.parametrize(
    "pragma",
    [
        "synchronous; DROP TABLE users; --",
        "cache_size' OR '1'='1",
        'temp_store"; SELECT * FROM users; --',
        "journal_mode--",
        "foreign_keys/*",
        "query-only",  # Invalid character
        "recursive triggers",  # Space in name
        "123invalid",  # Starts with number
        "",  # Empty string
    ],
)
def test_pragma_validation_invalid_names(pragma, db_path):
    """Test that invalid pragma names are rejected."""
    with pytest.raises(ValueError) as cm:
        manager = DatabaseManager(db_path, extra_pragmas={pragma: "value"})
        with manager.read() as cursor:
            cursor.execute(TEST_SQL_SELECT_ONE)
    assert "Invalid pragma name" in str(cm.value)


@pytest.mark.parametrize(
    "value",
    [
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
    ],
)
def test_pragma_validation_string_values(value, db_path):
    """Test pragma validation for string values."""
    manager = DatabaseManager(db_path, extra_pragmas={"test_pragma": value})
    try:
        with manager.read() as cursor:
            cursor.execute(TEST_SQL_SELECT_ONE)
    finally:
        manager.close()


@pytest.mark.parametrize(
    "value",
    [
        "value; DROP TABLE users; --",
        "value' OR '1'='1",
        'value"; SELECT * FROM users; --',
        "value/*",
        "value<script>",
        "value\x00null",
        "value'; DROP TABLE users; --",
        "value' OR '1'='1 --",
        "value\\",  # Test backslash at end vulnerability
    ],
)
def test_pragma_validation_invalid_string_values(value, db_path):
    """Test that invalid string pragma values are rejected."""
    with pytest.raises(ValueError) as cm:
        manager = DatabaseManager(db_path, extra_pragmas={"test_pragma": value})
        with manager.read() as cursor:
            cursor.execute(TEST_SQL_SELECT_ONE)
    assert "Invalid or unsafe pragma value" in str(cm.value)


@pytest.mark.parametrize("value", [1000, 0, -1, 3.14, 2.718])
def test_pragma_validation_numeric_values(value, db_path):
    """Test pragma validation for numeric values."""
    manager = DatabaseManager(db_path, extra_pragmas={"cache_size": value})
    try:
        with manager.read() as cursor:
            cursor.execute(TEST_SQL_SELECT_ONE)
    finally:
        manager.close()


@pytest.mark.parametrize("value", [True, False])
def test_pragma_validation_boolean_values(value, db_path):
    """Test pragma validation for boolean values."""
    manager = DatabaseManager(db_path, extra_pragmas={"recursive_triggers": value})
    try:
        with manager.read() as cursor:
            cursor.execute(TEST_SQL_SELECT_ONE)
    finally:
        manager.close()


@pytest.mark.parametrize(
    "value",
    [
        {"not": "a dict"},
        ["not", "a list"],
        (None, "tuple"),
        {1, 2, 3},
        None,
    ],
)
def test_pragma_validation_invalid_numeric_types(value, db_path):
    """Test that invalid numeric pragma value types are rejected."""
    with pytest.raises(TypeError) as cm:
        manager = DatabaseManager(db_path, extra_pragmas={"test_pragma": value})
        with manager.read() as cursor:
            cursor.execute(TEST_SQL_SELECT_ONE)
    assert "Invalid pragma value type" in str(cm.value)


# ========================================================================
# Read Context Manager Tests
# ========================================================================


def test_read_context_manager(db_manager):
    """Test read context manager behavior."""
    manager, _ = db_manager
    with manager.read() as cursor:
        result = cursor.execute(TEST_SQL_SELECT_ONE).fetchone()
        assert result[0] == 1


# ========================================================================
# Write Context Manager Tests
# ========================================================================


def test_write_context_manager_success(db_manager):
    """Test write context manager on successful operation."""
    manager, _ = db_manager
    with manager.write() as cursor:
        cursor.execute(TEST_SQL_CREATE_TABLE)
        cursor.execute(TEST_SQL_INSERT_VALUE, ("test_value",))

    # Verify data was committed
    with manager.read() as cursor:
        result = cursor.execute("SELECT value FROM test").fetchone()
        assert result[0] == "test_value"


def test_write_context_manager_rollback_on_error(db_manager):
    """Test write context manager rolls back on error."""
    manager, _ = db_manager
    # Create initial table
    with manager.write() as cursor:
        cursor.execute(TEST_SQL_CREATE_TABLE)
        cursor.execute(TEST_SQL_INSERT_VALUE, ("initial",))

    # Attempt operation that will fail
    with pytest.raises(sqlite3.IntegrityError):
        with manager.write() as cursor:
            cursor.execute("INSERT INTO test (id, value) VALUES (1, ?)", ("conflict",))
            cursor.execute("INSERT INTO test (id, value) VALUES (1, ?)", ("conflict",))

    # Verify initial data is still there (rollback worked)
    with manager.read() as cursor:
        result = cursor.execute(TEST_SQL_COUNT_TEST).fetchone()
        assert result[0] == 1
        result = cursor.execute("SELECT value FROM test").fetchone()
        assert result[0] == "initial"


def test_write_context_manager_rollback_on_non_sqlite_exception(db_manager):
    """Test write context manager rolls back on non-SQLite exceptions."""
    manager, _ = db_manager
    # Create initial table
    with manager.write() as cursor:
        cursor.execute(TEST_SQL_CREATE_TABLE)
        cursor.execute(TEST_SQL_INSERT_VALUE, ("initial",))

    # Attempt operation that will fail with ValueError
    with pytest.raises(ValueError):
        with manager.write() as cursor:
            cursor.execute(TEST_SQL_INSERT_VALUE, ("should_be_rolled_back",))
            raise ValueError("Test exception")

    # Verify initial data is still there (rollback worked)
    with manager.read() as cursor:
        result = cursor.execute(TEST_SQL_COUNT_TEST).fetchone()
        assert result[0] == 1
        result = cursor.execute("SELECT value FROM test").fetchone()
        assert result[0] == "initial"


def test_write_context_manager_rollback_on_custom_exception(db_manager):
    """Test write context manager rolls back on custom exceptions."""
    manager, _ = db_manager
    # Create initial table
    with manager.write() as cursor:
        cursor.execute(TEST_SQL_CREATE_TABLE)
        cursor.execute(TEST_SQL_INSERT_VALUE, ("initial",))

    # Define a custom exception
    class CustomTestError(Exception):
        pass

    # Attempt operation that will fail with custom exception
    with pytest.raises(CustomTestError):
        with manager.write() as cursor:
            cursor.execute(TEST_SQL_INSERT_VALUE, ("should_be_rolled_back",))
            raise CustomTestError("Custom test exception")

    # Verify initial data is still there (rollback worked)
    with manager.read() as cursor:
        result = cursor.execute(TEST_SQL_COUNT_TEST).fetchone()
        assert result[0] == 1
        result = cursor.execute("SELECT value FROM test").fetchone()
        assert result[0] == "initial"


def test_write_context_manager_rollback_on_runtime_error(db_manager):
    """Test write context manager rolls back on RuntimeError."""
    manager, _ = db_manager
    # Create initial table
    with manager.write() as cursor:
        cursor.execute(TEST_SQL_CREATE_TABLE)
        cursor.execute(TEST_SQL_INSERT_VALUE, ("initial",))

    # Attempt operation that will fail with RuntimeError
    with pytest.raises(RuntimeError):
        with manager.write() as cursor:
            cursor.execute(TEST_SQL_INSERT_VALUE, ("should_be_rolled_back",))
            raise RuntimeError("Runtime error test")

    # Verify initial data is still there (rollback worked)
    with manager.read() as cursor:
        result = cursor.execute(TEST_SQL_COUNT_TEST).fetchone()
        assert result[0] == 1
        result = cursor.execute("SELECT value FROM test").fetchone()
        assert result[0] == "initial"


def test_write_context_manager_rollback_on_keyboard_interrupt(db_manager):
    """Write context manager should roll back on KeyboardInterrupt."""
    manager, _ = db_manager
    with manager.write() as cursor:
        cursor.execute(TEST_SQL_CREATE_TABLE)
        cursor.execute(TEST_SQL_INSERT_VALUE, ("initial",))

    with pytest.raises(KeyboardInterrupt):
        with manager.write() as cursor:
            cursor.execute(TEST_SQL_INSERT_VALUE, ("should_be_rolled_back",))
            raise KeyboardInterrupt()

    with manager.read() as cursor:
        result = cursor.execute(TEST_SQL_COUNT_TEST).fetchone()
        assert result[0] == 1
        result = cursor.execute("SELECT value FROM test").fetchone()
        assert result[0] == "initial"


def test_write_context_manager_partial_transaction_rollback(db_manager):
    """Test that partial transactions are properly rolled back on non-SQLite exceptions."""
    manager, _ = db_manager
    # Create initial table with some data
    with manager.write() as cursor:
        cursor.execute(TEST_SQL_CREATE_TABLE)
        cursor.execute(TEST_SQL_INSERT_VALUE, ("initial",))

    # Get initial count
    with manager.read() as cursor:
        initial_count = cursor.execute(TEST_SQL_COUNT_TEST).fetchone()[0]

    # Attempt operation with multiple statements that fails partway through
    with pytest.raises(ValueError):
        with manager.write() as cursor:
            # First insert should succeed
            cursor.execute(TEST_SQL_INSERT_VALUE, ("first_insert",))
            # Second insert should also succeed
            cursor.execute(TEST_SQL_INSERT_VALUE, ("second_insert",))
            # Raise exception before commit
            raise ValueError("Exception after partial work")

    # Verify no new data was committed (rollback worked)
    with manager.read() as cursor:
        final_count = cursor.execute(TEST_SQL_COUNT_TEST).fetchone()[0]
        assert final_count == initial_count
        # Verify only initial data exists
        result = cursor.execute("SELECT value FROM test").fetchone()
        assert result[0] == "initial"


# ========================================================================
# Async / Sync Operation Tests
# ========================================================================


def test_run_sync_read_operation(db_manager):
    """Test run_sync for read operations."""
    manager, _ = db_manager

    def query_func(cursor):
        """
        Fetch the integer 42 using the provided database cursor.

        Parameters:
            cursor (sqlite3.Cursor or DB-API cursor): Cursor used to execute the query.

        Returns:
            int: The integer 42.
        """
        return cursor.execute("SELECT 42").fetchone()[0]

    result = manager.run_sync(query_func, write=False)
    assert result == 42


def test_run_sync_write_operation(db_manager):
    """Test run_sync for write operations."""
    manager, _ = db_manager

    def write_func(cursor):
        r"""
        Creates the table named \"test\" (id INTEGER PRIMARY KEY, value TEXT) and inserts a row with value "sync_test".

        Parameters:
            cursor (sqlite3.Cursor): Database cursor used to execute statements.

        Returns:
            int: The `lastrowid` of the inserted row.
        """
        cursor.execute(TEST_SQL_CREATE_TABLE)
        cursor.execute(TEST_SQL_INSERT_VALUE, ("sync_test",))
        return cursor.lastrowid

    row_id = manager.run_sync(write_func, write=True)
    assert row_id is not None

    # Verify data was written
    with manager.read() as cursor:
        result = cursor.execute(
            "SELECT value FROM test WHERE id = ?", (row_id,)
        ).fetchone()
        assert result[0] == "sync_test"


def test_run_async_operation(db_manager):
    """Test run_async for async operations with proper mocking."""
    manager, _ = db_manager

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

        result = await manager.run_async(async_func, write=False)
        return result

    # Run in an isolated loop to avoid interference from global loop fixtures.
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(test_async())
    finally:
        loop.close()
    assert result == "async_result"


def test_run_async_queued_work_completes_during_close(db_manager):
    """Queued run_async work accepted before close() should still complete."""
    manager, db_path = db_manager

    with manager.write() as cursor:
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
        task1 = asyncio.create_task(manager.run_async(first_write, write=True))
        start_deadline = asyncio.get_running_loop().time() + operation_timeout
        while (
            not first_started.is_set()
            and asyncio.get_running_loop().time() < start_deadline
        ):
            await asyncio.sleep(0.01)
        assert first_started.is_set(), "First queued write never started"
        task2 = asyncio.create_task(manager.run_async(second_write, write=True))
        await asyncio.sleep(0)

        close_started = threading.Event()
        close_done = threading.Event()
        close_error: Future[None] = Future()

        def _close_manager() -> None:
            close_started.set()
            try:
                manager.close()
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
        assert close_started.is_set(), "close() never started"

        # Brief wait to allow any incorrect early execution to manifest
        await asyncio.sleep(0.1)
        assert not second_started.is_set(), "second_write should not have started yet"

        try:
            first_release.set()

            await asyncio.sleep(0)

            deadline = asyncio.get_running_loop().time() + operation_timeout
            while (
                not second_started.is_set()
                and asyncio.get_running_loop().time() < deadline
            ):
                await asyncio.sleep(0.01)
            assert (
                second_started.is_set()
            ), "second_write should have started after first_release"

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
            assert close_done.is_set(), "close() did not complete"
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
            assert not close_thread.is_alive(), "close thread did not exit"
            if close_error.done():
                close_error.result()

        return result1, result2

    result1, result2 = asyncio.run(run_test())
    assert (result1, result2) == ("first", "second")

    with (
        contextlib.closing(sqlite3.connect(db_path)) as conn,
        conn as managed_conn,
    ):
        count = managed_conn.execute(
            "SELECT COUNT(*) FROM test_async_close"
        ).fetchone()[0]
    assert count == 2


# ========================================================================
# Thread Safety Tests
# ========================================================================


def test_thread_local_connections(db_manager):
    """Test that connections are thread-local."""
    manager, _ = db_manager
    connections = []

    def get_connection():
        """
        Obtain a connection from the DatabaseManager and append it to the local `connections` list for tracking.

        Returns:
            sqlite3.Connection: The acquired database connection.
        """
        conn = manager._get_connection()
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
    assert len(connections) == 3
    assert len(set(connections)) == 3  # All should be different


def test_connection_tracking(db_manager):
    """Test that connections are tracked and reused per thread."""
    manager, _ = db_manager
    initial_count = len(manager._connections)

    # Fetching again on the same thread should reuse the eager connection.
    conn = manager._get_connection()

    # Connection should be tracked
    assert conn in manager._connections
    assert len(manager._connections) == initial_count

    # Close manager and verify connection cleanup
    manager.close()
    assert len(manager._connections) == 0


def test_get_connection_rejects_when_manager_closing(db_manager):
    """_get_connection should reject new non-admitted access while closing."""
    manager, _ = db_manager
    with manager._connections_lock:
        manager._closing = True
    try:
        with pytest.raises(sqlite3.ProgrammingError):
            manager._get_connection()
    finally:
        with manager._connections_lock:
            manager._closing = False


def test_get_connection_recreates_closed_thread_local_connection(db_manager):
    """A closed thread-local connection should be discarded and replaced."""
    manager, _ = db_manager
    original = manager._get_connection()
    original.close()
    replacement = manager._get_connection()
    assert replacement is not original
    assert replacement in manager._connections
    assert original not in manager._connections


def test_read_rejects_new_work_when_manager_closing(db_manager):
    """read() should reject new work when manager is closing."""
    manager, _ = db_manager
    with manager._connections_lock:
        manager._closing = True
    try:
        with pytest.raises(sqlite3.ProgrammingError):
            with manager.read():
                pass
    finally:
        with manager._connections_lock:
            manager._closing = False


# ========================================================================
# Close / Cleanup Tests
# ========================================================================


def test_close_cleanup(db_manager):
    """Test close method properly cleans up resources."""
    manager, _ = db_manager
    # Create some connections
    conn1 = manager._get_connection()
    conn2 = manager._get_connection()

    # Verify connections exist
    assert conn1 in manager._connections
    assert conn2 in manager._connections

    # Close manager
    manager.close()

    # Verify cleanup
    assert len(manager._connections) == 0
    assert not hasattr(manager._thread_local, "connection")


def test_close_rejected_during_active_database_operation(db_manager):
    """close() should reject reentrant invocation from active DB work."""
    manager, _ = db_manager

    def close_inside_operation(_cursor: sqlite3.Cursor) -> None:
        manager.close()

    with pytest.raises(
        RuntimeError,
        match="cannot be called from inside an active database operation",
    ):
        manager.run_sync(close_inside_operation, write=False)


def test_close_handles_connection_errors(db_path):
    """Test close method handles connection errors gracefully."""
    # Create a manager and get a connection
    test_manager = DatabaseManager(db_path)
    conn = test_manager._get_connection()

    # Verify connection is tracked
    assert conn in test_manager._connections
    assert len(test_manager._connections) == 1

    # Close normally (this exercises the error handling path)
    test_manager.close()

    # Verify cleanup happened
    assert len(test_manager._connections) == 0


def test_close_waits_for_active_sync_work_to_finish(db_path):
    """close() should wait until active sync work drains."""
    manager = DatabaseManager(db_path)
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
                release_error.append("test never allowed release_activity to continue")
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
        assert close_started.wait(timeout=1.0), "closer thread never started"
        assert not close_done.wait(
            timeout=0.05
        ), "close() returned before active sync work drained"
        assert closer.is_alive(), "close() did not block before release"
    finally:
        allow_release.set()
        releaser.join(timeout=1.0)
        closer.join(timeout=1.0)

    assert release_error == [], f"Unexpected errors: {release_error}"
    assert not releaser.is_alive()
    assert not closer.is_alive(), "closer thread did not exit"
    if close_error.done():
        close_error.result()
    assert close_done.is_set(), "close() did not finish after release"


def test_close_logs_sqlite_errors_when_connection_close_fails(db_path):
    """close() should log sqlite close failures and continue cleanup."""
    manager = DatabaseManager(db_path)
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
        assert len(manager._connections) == 0


def test_close_ignores_attributeerror_while_deleting_thread_local_connection(
    db_path,
):
    """close() should ignore AttributeError from unusual thread-local implementations."""

    class _DelattrRaises:
        connection = object()

        def __delattr__(self, name: str) -> None:
            raise AttributeError(name)

    manager = DatabaseManager(db_path)
    manager._thread_local = _DelattrRaises()
    manager.close()


# ========================================================================
# Write Lock Serialization Test
# ========================================================================


def test_write_lock_serialization(db_manager):
    """Test that write operations are serialized."""
    manager, _ = db_manager
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
                    cursor.execute("UPDATE counter SET count = count + 1 WHERE id = 1")
                    # Get current count
                    result = cursor.execute(
                        "SELECT count FROM counter WHERE id = 1"
                    ).fetchone()
                    return result[0] if result else None

                result = manager.run_sync(write_func, write=True)
                results.append((thread_id, result))
                break  # Success, exit retry loop
            except Exception as e:
                if attempt == max_retries - 1:
                    # Last attempt, record the error
                    errors.append((thread_id, e))
                else:
                    # Brief sleep before retry to allow lock to clear
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
    assert len(errors) == 0, f"Errors occurred: {errors}"

    # Verify all operations completed and counter was incremented
    assert len(results) == 5

    # Check final counter value
    with manager.read() as cursor:
        final_count = cursor.execute(
            "SELECT count FROM counter WHERE id = 1"
        ).fetchone()
        assert final_count is not None
        assert final_count[0] == 5


# ========================================================================
# Connection Error / Pragma Application Tests
# ========================================================================


def test_connection_creation_error_cleanup(db_path):
    """Test that connection creation errors don't leak connections."""
    # Try to create a manager with invalid pragma that will fail
    with pytest.raises(ValueError):
        manager = DatabaseManager(db_path, extra_pragmas={"invalid;pragma": "value"})
        with manager.read() as cursor:
            cursor.execute(TEST_SQL_SELECT_ONE)

    # Verify no connections were leaked (this is more of a sanity check)
    # since the manager creation failed, there shouldn't be any connections to track


def test_busy_timeout_pragma_application(db_path):
    """Test that busy timeout pragma is properly applied."""
    manager = DatabaseManager(db_path, busy_timeout_ms=1000)
    try:
        with manager.read() as cursor:
            # Query the busy timeout setting
            result = cursor.execute("PRAGMA busy_timeout").fetchone()
            assert result[0] == 1000
    finally:
        manager.close()


def test_wal_mode_pragma_application(db_path):
    """Test that WAL mode pragma is properly applied."""
    manager = DatabaseManager(db_path, enable_wal=True)
    try:
        with manager.read() as cursor:
            # Query the journal mode setting
            result = cursor.execute("PRAGMA journal_mode").fetchone()
            assert result[0].lower() == "wal"
    finally:
        manager.close()


def test_foreign_keys_pragma_application(db_manager):
    """Test that foreign keys pragma is properly applied."""
    manager, _ = db_manager
    with manager.read() as cursor:
        # Query the foreign keys setting
        result = cursor.execute("PRAGMA foreign_keys").fetchone()
        assert result[0] == 1


# ========================================================================
# Standalone async tests (already pytest style — unchanged)
# ========================================================================


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


# ========================================================================
# Edge Cases Tests (converted from TestDatabaseManagerEdgeCases)
# ========================================================================


def test_empty_extra_pragmas_dict(db_path):
    """Test handling of empty extra_pragmas dictionary."""
    manager = DatabaseManager(db_path, extra_pragmas={})
    try:
        with manager.read() as cursor:
            cursor.execute(TEST_SQL_SELECT_ONE)
    finally:
        manager.close()


def test_none_extra_pragmas(db_path):
    """Test handling of None extra_pragmas."""
    manager = DatabaseManager(db_path, extra_pragmas=None)
    try:
        with manager.read() as cursor:
            cursor.execute(TEST_SQL_SELECT_ONE)
    finally:
        manager.close()


def test_zero_busy_timeout(db_path):
    """Test handling of zero busy timeout."""
    manager = DatabaseManager(db_path, busy_timeout_ms=0)
    try:
        with manager.read() as cursor:
            cursor.execute(TEST_SQL_SELECT_ONE)
    finally:
        manager.close()


def test_negative_busy_timeout(db_path):
    """Test handling of negative busy timeout."""
    manager = DatabaseManager(db_path, busy_timeout_ms=-1000)
    try:
        with manager.read() as cursor:
            cursor.execute(TEST_SQL_SELECT_ONE)
    finally:
        manager.close()


def test_database_file_permissions(db_path):
    """
    Verify DatabaseManager behavior when the database file's filesystem permissions are changed to read-only.

    Creates a table to ensure the database file exists, changes the file mode to read-only, attempts a write that may either succeed or raise sqlite3.OperationalError depending on the platform/SQLite build, restores the original permissions, and closes the manager to ensure cleanup.
    """

    # Create manager
    manager = DatabaseManager(db_path)
    try:
        # Create a table to ensure database file exists
        with manager.write() as cursor:
            cursor.execute("CREATE TABLE test (id INTEGER)")

        # Make database file read-only
        current_permissions = os.stat(db_path).st_mode
        os.chmod(db_path, stat.S_IRUSR)

        # Try to write - may fail depending on system/SQLite version
        try:
            with manager.write() as cursor:
                cursor.execute("INSERT INTO test (id) VALUES (1)")
            # If it succeeds, that's also valid behavior (some SQLite versions handle this differently)
        except sqlite3.OperationalError:
            # This is expected on most systems
            pass

        # Restore permissions for cleanup
        os.chmod(db_path, current_permissions)
    finally:
        manager.close()


def test_concurrent_read_operations(db_path):
    """Test that concurrent read operations work correctly."""
    manager = DatabaseManager(db_path)
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
        assert len(results) == 5
        for _thread_id, result in results:
            assert result == "test"
    finally:
        manager.close()
