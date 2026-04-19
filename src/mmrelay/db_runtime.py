"""
Runtime utilities for managing SQLite connections used by MMRelay.

Provides a DatabaseManager that centralizes connection creation,
applies consistent pragmas, and exposes both synchronous context
managers and async helpers for executing read/write operations.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import CancelledError as ConcurrentCancelledError
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager, suppress
from functools import lru_cache
from typing import Any, Generator, Optional

from mmrelay.constants.database import (
    DB_EXECUTOR_MAX_WORKERS,
    DEFAULT_BUSY_TIMEOUT_MS,
    PRAGMA_FOREIGN_KEYS_ON,
    PRAGMA_JOURNAL_MODE_WAL,
    SQLITE_IN_MEMORY_PATH,
    SQLITE_JSON_EACH_PROBE_PAYLOAD,
    SQLITE_JSON_EACH_PROBE_SQL,
    SQLITE_PRAGMA_BOOL_OFF,
    SQLITE_PRAGMA_BOOL_ON,
    SQLITE_PRAGMA_NAME_PATTERN,
    SQLITE_PRAGMA_SAFE_STRING_VALUE_PATTERN,
)
from mmrelay.log_utils import get_logger

logger = get_logger(__name__)


def _get_sqlite_runtime_version_info() -> tuple[int, int, int]:
    """
    Return the runtime SQLite version as a normalized 3-int tuple.
    """
    version_info = getattr(sqlite3, "sqlite_version_info", None)
    if (
        isinstance(version_info, tuple)
        and len(version_info) >= 3
        and all(isinstance(part, int) for part in version_info[:3])
    ):
        return (version_info[0], version_info[1], version_info[2])

    version_str = str(getattr(sqlite3, "sqlite_version", "0.0.0"))
    raw_parts = version_str.split(".")
    numeric_parts: list[int] = []
    for raw_part in raw_parts[:3]:
        try:
            numeric_parts.append(int(raw_part))
        except ValueError:
            numeric_parts.append(0)
    while len(numeric_parts) < 3:
        numeric_parts.append(0)
    return (numeric_parts[0], numeric_parts[1], numeric_parts[2])


@lru_cache(maxsize=1)
def _validate_sqlite_json_each_support() -> bool:
    """
    Detect runtime json_each() capability for optional name-state optimizations.

    Returns:
        bool: True when json_each() is available, False otherwise.
    """
    conn = sqlite3.connect(SQLITE_IN_MEMORY_PATH)
    try:
        _probe_sqlite_json_each_support(conn)
        return True
    except RuntimeError:
        current_version = _get_sqlite_runtime_version_info()
        logger.warning(
            "SQLite json_each() is unavailable (runtime %s.%s.%s); "
            "falling back to non-JSON1 node-name query paths.",
            current_version[0],
            current_version[1],
            current_version[2],
        )
        return False
    finally:
        conn.close()


def _probe_sqlite_json_each_support(conn: sqlite3.Connection) -> None:
    """
    Verify json_each() support on an active SQLite connection.

    Only translate explicit "missing json_each support" failures to a
    RuntimeError. Other sqlite failures (for example, corrupted database files)
    are re-raised unchanged so callers can handle the underlying database error.
    """
    try:
        conn.execute(
            SQLITE_JSON_EACH_PROBE_SQL, (SQLITE_JSON_EACH_PROBE_PAYLOAD,)
        ).fetchall()
    except sqlite3.Error as exc:
        error_message = str(exc).lower()
        if (
            "no such function: json_each" in error_message
            or "no such table: json_each" in error_message
        ):
            current_version = _get_sqlite_runtime_version_info()
            raise RuntimeError(
                "SQLite json_each() support is required for node-name queries. "
                f"Detected SQLite version: {current_version[0]}.{current_version[1]}.{current_version[2]}. "
                "Ensure SQLite is built with JSON support."
            ) from exc
        raise


class DatabaseManager:
    """
    Manage SQLite connections with shared pragmas and helper execution APIs.

    A separate connection is maintained per thread via thread-local storage
    (created with `check_same_thread=False`). Write operations are serialized
    via an RLock to ensure only one writer executes at a time. Connections are
    tracked so they can be closed when the manager is reset.
    """

    _path: str
    _enable_wal: bool
    _busy_timeout_ms: int
    _extra_pragmas: dict[str, Any]
    _thread_local: threading.local
    _write_lock: threading.RLock
    _connections: set[sqlite3.Connection]
    _connections_lock: threading.RLock
    _active_sync_condition: threading.Condition
    _active_sync_count: int
    _executor_lock: threading.Lock
    _accepting_submissions: bool
    _closing: bool
    _supports_json_each: bool
    _async_executor: ThreadPoolExecutor

    def __init__(
        self,
        path: str,
        *,
        enable_wal: bool = True,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        extra_pragmas: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Create a DatabaseManager configured for the given SQLite file path.

        Parameters:
            path (str): Filesystem path to the SQLite database file.
            enable_wal (bool): If true, connections will be configured to use Write-Ahead Logging (WAL) mode.
            busy_timeout_ms (int): Milliseconds to wait for the database when it is busy before raising an error.
            extra_pragmas (Optional[dict[str, Any]]): Additional PRAGMA directives to apply to each connection.
                Keys are pragma names and values are either numeric or string pragma values. Invalid pragma
                names or values will raise when a connection is created.

        Notes:
            Construction eagerly creates and validates the first SQLite connection
            so path and PRAGMA misconfiguration fail before a manager instance is
            published.
        """
        self._supports_json_each = _validate_sqlite_json_each_support()

        self._path = path
        self._enable_wal = enable_wal
        self._busy_timeout_ms = busy_timeout_ms
        self._extra_pragmas = extra_pragmas or {}

        self._thread_local = threading.local()
        self._write_lock = threading.RLock()
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.RLock()
        self._active_sync_condition = threading.Condition(self._connections_lock)
        self._active_sync_count = 0
        self._executor_lock = threading.Lock()
        self._async_executor = ThreadPoolExecutor(max_workers=DB_EXECUTOR_MAX_WORKERS)
        self._accepting_submissions = True
        self._closing = False

        # Fail fast before publishing a manager that cannot create a usable
        # SQLite connection for the configured path/PRAGMA set.
        try:
            self._thread_local.connection = self._create_connection()
        except BaseException:
            self._async_executor.shutdown(wait=False)
            raise

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _create_connection(self) -> sqlite3.Connection:
        """
        Create and configure a new sqlite3.Connection for this manager, apply configured PRAGMA directives, and register the connection for later cleanup.

        Returns:
            sqlite3.Connection: A connection configured with the manager's pragmas and tracked by the manager.

        Raises:
            sqlite3.Error: If an SQLite error occurs during connection creation or PRAGMA setup (the partially configured connection is closed before the error is propagated).
            ValueError: If an extra PRAGMA name or string value fails validation.
            TypeError: If an extra PRAGMA value has an unsupported type.
        """
        conn = sqlite3.connect(self._path, check_same_thread=False)
        try:
            # Serialize PRAGMA setup to avoid concurrent WAL initialization races
            with self._write_lock:
                if self._busy_timeout_ms:
                    conn.execute(f"PRAGMA busy_timeout = {int(self._busy_timeout_ms)}")
                if self._enable_wal:
                    # journal_mode pragma returns the applied mode; ignore result
                    conn.execute(PRAGMA_JOURNAL_MODE_WAL)
                conn.execute(PRAGMA_FOREIGN_KEYS_ON)
                for pragma, value in self._extra_pragmas.items():
                    # Validate pragma name to prevent injection.
                    if not SQLITE_PRAGMA_NAME_PATTERN.fullmatch(pragma):
                        raise ValueError(f"Invalid pragma name provided: {pragma}")
                    # Validate and sanitize value to prevent injection
                    if isinstance(value, str):
                        # Security: Restrict pragma string values to safe characters only.
                        # This regex allows alphanumeric, underscore, hyphen, space, comma, period, and backslash.
                        # It also rejects "--" to block SQL-comment marker injection.
                        # We deliberately exclude forward slash and colon to prevent path injection attacks.
                        # Backslash is allowed but trailing backslashes are blocked to prevent escape sequences.
                        #
                        # Security assumption: Configuration sources are trusted, but we validate defensively
                        # to prevent accidental or malicious injection through compromised config files.
                        # This balances security with practical SQLite pragma value requirements.
                        if not SQLITE_PRAGMA_SAFE_STRING_VALUE_PATTERN.fullmatch(
                            value
                        ) or value.endswith("\\"):
                            raise ValueError(
                                f"Invalid or unsafe pragma value provided: {value}"
                            )
                        conn.execute(f"PRAGMA {pragma} = '{value}'")
                    elif isinstance(value, bool):
                        # Convert boolean values to ON/OFF for SQLite pragmas
                        conn.execute(
                            f"PRAGMA {pragma} = {SQLITE_PRAGMA_BOOL_ON if value else SQLITE_PRAGMA_BOOL_OFF}"
                        )
                    elif isinstance(value, (int, float)):
                        # For numeric values, ensure they're actually numeric
                        conn.execute(f"PRAGMA {pragma} = {value}")
                    else:
                        raise TypeError(f"Invalid pragma value type: {type(value)}")
        except BaseException:
            # Ensure partially configured connection does not leak
            conn.close()
            raise

        with self._connections_lock:
            self._connections.add(conn)
        return conn

    def _get_connection(self) -> sqlite3.Connection:
        """
        Get the thread-local SQLite connection, creating and storing a new connection if none exists.

        Returns:
            sqlite3.Connection: The per-thread SQLite connection.

        Raises:
            sqlite3.ProgrammingError: If the manager is closing and cannot create new connections.
        """
        with self._connections_lock:
            if self._closing and not self._is_admitted_during_close():
                raise sqlite3.ProgrammingError(
                    "DatabaseManager is closing, cannot create new connections"
                )
            conn = getattr(self._thread_local, "connection", None)
            if conn is not None:
                try:
                    conn.cursor().close()
                except sqlite3.ProgrammingError:
                    self._connections.discard(conn)
                    conn = None

            if conn is None:
                conn = self._create_connection()
                self._thread_local.connection = conn
            return conn

    @contextmanager
    def _sync_activity(self) -> Generator[None, None, None]:
        """
        Track active synchronous DB usage and block new work while closing.
        """
        previously_admitted = bool(
            getattr(self._thread_local, "_allow_during_close", False)
        )
        with self._connections_lock:
            if self._closing and not previously_admitted:
                raise sqlite3.ProgrammingError(
                    "DatabaseManager is closing, cannot submit new work"
                )
            self._thread_local._allow_during_close = True
            self._active_sync_count += 1
        try:
            yield
        finally:
            with self._connections_lock:
                self._active_sync_count -= 1
                self._thread_local._allow_during_close = previously_admitted
                if self._active_sync_count == 0:
                    self._active_sync_condition.notify_all()

    def _is_admitted_during_close(self) -> bool:
        """
        Return True when the current thread is admitted to keep working during close().
        """
        return bool(getattr(self._thread_local, "_allow_during_close", False))

    def supports_json_each(self) -> bool:
        """
        Report whether json_each() optimization paths are available at runtime.
        """
        return self._supports_json_each

    # ------------------------------------------------------------------ #
    # Context managers
    # ------------------------------------------------------------------ #

    @contextmanager
    def read(self) -> Generator[sqlite3.Cursor, None, None]:
        """
        Provide a cursor for performing read-only database operations.

        The cursor is obtained from the per-thread connection and is guaranteed to be closed when the context exits. This context does not commit or roll back any transactions; it is intended for queries that do not modify persistent state.

        Returns:
            sqlite3.Cursor: A cursor tied to the manager's per-thread connection.
        """
        with self._sync_activity():
            conn = self._get_connection()
            cursor = conn.cursor()
            try:
                yield cursor
            finally:
                cursor.close()

    @contextmanager
    def write(self) -> Generator[sqlite3.Cursor, None, None]:
        """
        Provide a context manager that yields a cursor for transactional write operations.

        The yielded cursor is intended for executing modifying statements. The transaction is committed when the context exits normally and rolled back if an exception is raised. Write operations are serialized across threads using the manager's write lock, and the cursor is closed on exit.

        Returns:
            cursor (sqlite3.Cursor): Cursor for executing write statements; committed on success, rolled back on exception.
        """
        with self._sync_activity():
            conn = self._get_connection()
            cursor = conn.cursor()
            with self._write_lock:
                try:
                    yield cursor
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                finally:
                    cursor.close()

    # ------------------------------------------------------------------ #
    # Execution helpers
    # ------------------------------------------------------------------ #

    def run_sync(
        self,
        func: Callable[[sqlite3.Cursor], Any],
        *,
        write: bool = False,
    ) -> Any:
        """
        Execute a callable with a managed SQLite cursor.

        Run `func` with a cursor provided by the manager; when `write` is True, the callable is executed inside a write transaction that will be committed on success and rolled back on exception.

        Parameters:
            func (Callable[[sqlite3.Cursor], Any]): A callable that receives a `sqlite3.Cursor` and returns a result.
            write (bool): If True, execute `func` in a transactional write context; otherwise use a read-only cursor. Defaults to False.

        Returns:
            Any: The value returned by `func`.
        """
        context = self.write if write else self.read
        with context() as cursor:
            return func(cursor)

    async def run_async(
        self,
        func: Callable[[sqlite3.Cursor], Any],
        *,
        write: bool = False,
    ) -> Any:
        """
        Run a database callable asynchronously and return its result.

        Parameters:
            func (Callable[[sqlite3.Cursor], Any]): Callable that will be invoked with a managed SQLite cursor.
            write (bool, optional): If true, the callable receives a cursor from a transactional write context; otherwise a read-only context is used. Defaults to False.

        Returns:
            Any: The value returned by `func` when invoked with the cursor.
        """

        def executor_func() -> Any:
            self._thread_local._allow_during_close = True
            try:
                return self.run_sync(func, write=write)
            finally:
                self._thread_local._allow_during_close = False

        with self._executor_lock:
            if not self._accepting_submissions:
                raise sqlite3.ProgrammingError(
                    "DatabaseManager is closing, cannot submit new work"
                )
            worker_future = self._async_executor.submit(executor_func)
        try:
            return await self._await_submitted_future(worker_future)
        except asyncio.CancelledError:
            if write:
                self._log_write_future_error_after_cancellation(worker_future)
            elif not worker_future.done():
                worker_future.cancel()
            raise

    async def _await_submitted_future(self, worker_future: Future[Any]) -> Any:
        """
        Await a submitted executor future without polling.

        Cancellation semantics:
        - No internal timeout is applied here; callers own timeout policy.
        - Caller cancellation should propagate immediately.
        - `run_async()` decides post-cancel behavior (cancel read futures, observe
          write-future errors).
        """
        if worker_future.done():
            return self._resolve_submitted_future_result(worker_future)

        loop = asyncio.get_running_loop()
        done_event = asyncio.Event()

        def _signal_done(_resolved_future: Future[Any]) -> None:
            try:
                loop.call_soon_threadsafe(done_event.set)
            except RuntimeError:
                # Event loop is shutting down; caller task cancellation will handle unwind.
                return

        worker_future.add_done_callback(_signal_done)
        await done_event.wait()
        return self._resolve_submitted_future_result(worker_future)

    @staticmethod
    def _resolve_submitted_future_result(worker_future: Future[Any]) -> Any:
        """
        Return worker future result with normalized cancellation semantics.
        """
        try:
            return worker_future.result()
        except ConcurrentCancelledError as exc:
            raise asyncio.CancelledError() from exc

    def _log_write_future_error_after_cancellation(
        self, worker_future: Future[Any]
    ) -> None:
        """
        Surface write worker failures that complete after caller cancellation.
        """

        def _log_future_error(resolved_future: Future[Any]) -> None:
            try:
                resolved_future.result()
            except (asyncio.CancelledError, ConcurrentCancelledError):
                pass
            except Exception:
                logger.warning(
                    "Write future finished with an error after caller cancellation",
                    exc_info=True,
                )

        if worker_future.done():
            _log_future_error(worker_future)
            return
        worker_future.add_done_callback(_log_future_error)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def _finalize_unclosed_resources(self) -> None:
        """
        Best-effort cleanup path for leaked managers during garbage collection.

        This path intentionally avoids blocking waits so object finalization cannot
        deadlock test teardown or interpreter shutdown.
        """
        with suppress(Exception):
            with self._executor_lock:
                self._accepting_submissions = False
                self._closing = True

        executor = getattr(self, "_async_executor", None)
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                with suppress(Exception):
                    executor.shutdown(wait=False)

        connections: list[sqlite3.Connection] = []
        with suppress(Exception):
            with self._connections_lock:
                connections = list(self._connections)
                self._connections.clear()

        for conn in connections:
            with suppress(sqlite3.Error):
                conn.close()

        thread_local = getattr(self, "_thread_local", None)
        if thread_local is not None and hasattr(thread_local, "connection"):
            with suppress(AttributeError):
                del thread_local.connection

    def __del__(self) -> None:
        """
        Ensure leaked connections/executors are cleaned up if close() was skipped.
        """
        if not hasattr(self, "_thread_local"):
            return
        with suppress(Exception):
            self._finalize_unclosed_resources()

    def close(self) -> None:
        """
        Close and clean up all tracked SQLite connections.

        Removes every connection from the manager's internal registry, attempts to close each connection (suppressing sqlite3.Error), and clears the current thread's stored connection reference.
        """
        with self._executor_lock:
            with self._connections_lock:
                if self._is_admitted_during_close():
                    raise RuntimeError(
                        "DatabaseManager.close() cannot be called from inside an active database operation"
                    )
                self._accepting_submissions = False
                self._closing = True

        with self._executor_lock:
            self._async_executor.shutdown(wait=True)

        with self._connections_lock:
            while self._active_sync_count > 0:
                self._active_sync_condition.wait()
            connections = list(self._connections)
            self._connections.clear()
            for conn in connections:
                try:
                    conn.close()
                except sqlite3.Error:
                    logger.debug(
                        "Error closing connection during shutdown", exc_info=True
                    )

        if hasattr(self._thread_local, "connection"):
            try:
                del self._thread_local.connection
            except AttributeError:
                pass


# Convenience alias for type hints
DbCallable = Callable[[sqlite3.Cursor], Any]
