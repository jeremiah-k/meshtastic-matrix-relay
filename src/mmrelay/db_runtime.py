"""
Runtime utilities for managing SQLite connections used by MMRelay.

Provides a DatabaseManager that centralizes connection creation,
applies consistent pragmas, and exposes both synchronous context
managers and async helpers for executing read/write operations.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import partial
from typing import Any, Generator, Optional

from mmrelay.constants.database import (
    MIN_SQLITE_VERSION_JSON_EACH,
    SQLITE_JSON_EACH_PROBE_PAYLOAD,
    SQLITE_JSON_EACH_PROBE_SQL,
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


def _validate_sqlite_json_each_support() -> None:
    """
    Ensure runtime SQLite supports json_each() usage in name-state queries.

    Uses a capability probe instead of version checking, since some builds
    may have json_each() available even on older SQLite versions.
    """
    conn = sqlite3.Connection(":memory:")
    try:
        _probe_sqlite_json_each_support(conn)
    except RuntimeError:
        current_version = _get_sqlite_runtime_version_info()
        raise RuntimeError(
            f"SQLite json_each() support is required. "
            f"Detected SQLite version: {current_version[0]}.{current_version[1]}.{current_version[2]}"
        ) from None
    finally:
        conn.close()


def _probe_sqlite_json_each_support(conn: sqlite3.Connection) -> None:
    """
    Verify json_each() support on an active SQLite connection.

    Only translate explicit "missing json_each support" failures to a
    RuntimeError. Other sqlite failures (for example, corrupted database files)
    are re-raised unchanged so callers can handle the underlying database error.
    """
    current_version = _get_sqlite_runtime_version_info()
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
    _executor_lock: threading.Lock
    _closing: bool

    def __init__(
        self,
        path: str,
        *,
        enable_wal: bool = True,
        busy_timeout_ms: int = 5000,
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
        _validate_sqlite_json_each_support()

        self._path = path
        self._enable_wal = enable_wal
        self._busy_timeout_ms = busy_timeout_ms
        self._extra_pragmas = extra_pragmas or {}

        self._thread_local = threading.local()
        self._write_lock = threading.RLock()
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.RLock()
        self._executor_lock = threading.Lock()
        self._async_executor = ThreadPoolExecutor(max_workers=1)
        self._closing = False

        # Fail fast before publishing a manager that cannot create a usable
        # SQLite connection for the configured path/PRAGMA set.
        self._thread_local.connection = self._create_connection()

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
                    conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                _probe_sqlite_json_each_support(conn)
                for pragma, value in self._extra_pragmas.items():
                    # Validate pragma name to prevent injection.
                    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", pragma):
                        raise ValueError(f"Invalid pragma name provided: {pragma}")
                    # Validate and sanitize value to prevent injection
                    if isinstance(value, str):
                        # Security: Restrict pragma string values to safe characters only.
                        # This regex allows alphanumeric, underscore, hyphen, space, comma, period, and backslash.
                        # We deliberately exclude forward slash and colon to prevent path injection attacks.
                        # Backslash is allowed but trailing backslashes are blocked to prevent escape sequences.
                        #
                        # Security assumption: Configuration sources are trusted, but we validate defensively
                        # to prevent accidental or malicious injection through compromised config files.
                        # This balances security with practical SQLite pragma value requirements.
                        if not re.fullmatch(
                            r"[a-zA-Z0-9_\-\s,.\\\\]+", value
                        ) or value.endswith("\\"):
                            raise ValueError(
                                f"Invalid or unsafe pragma value provided: {value}"
                            )
                        conn.execute(f"PRAGMA {pragma} = '{value}'")
                    elif isinstance(value, bool):
                        # Convert boolean values to ON/OFF for SQLite pragmas
                        conn.execute(f"PRAGMA {pragma} = {'ON' if value else 'OFF'}")
                    elif isinstance(value, (int, float)):
                        # For numeric values, ensure they're actually numeric
                        conn.execute(f"PRAGMA {pragma} = {value}")
                    else:
                        raise TypeError(f"Invalid pragma value type: {type(value)}")
        except (sqlite3.Error, RuntimeError, ValueError, TypeError):
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
            RuntimeError: If the manager is closing and cannot create new connections.
        """
        with self._connections_lock:
            if self._closing:
                raise RuntimeError(
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
        conn = self._get_connection()
        cursor = conn.cursor()
        with self._write_lock:
            try:
                yield cursor
                conn.commit()
            except Exception:
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
        executor_func = partial(self.run_sync, func, write=write)
        with self._executor_lock:
            if self._closing:
                raise RuntimeError("DatabaseManager is closing, cannot submit new work")
            worker_future = self._async_executor.submit(executor_func)
        try:
            return await asyncio.wrap_future(worker_future)
        except asyncio.CancelledError:
            worker_future.cancel()
            raise

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        """
        Close and clean up all tracked SQLite connections.

        Removes every connection from the manager's internal registry, attempts to close each connection (suppressing sqlite3.Error), and clears the current thread's stored connection reference.
        """
        with self._executor_lock:
            self._closing = True
            self._async_executor.shutdown(wait=True)

        with self._connections_lock:
            self._closing = True
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
AsyncDbCallable = Callable[[sqlite3.Cursor], Awaitable[Any]]
