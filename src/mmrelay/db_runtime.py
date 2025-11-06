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
from contextlib import contextmanager
from functools import partial
from typing import Any, Optional


class DatabaseManager:
    """
    Manage SQLite connections with shared pragmas and helper execution APIs.

    A separate connection is maintained per thread via thread-local storage
    (created with `check_same_thread=False`). Write operations are serialized
    via an RLock to ensure only one writer executes at a time. Connections are
    tracked so they can be closed when the manager is reset.
    """

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
        """
        self._path = path
        self._enable_wal = enable_wal
        self._busy_timeout_ms = busy_timeout_ms
        self._extra_pragmas = extra_pragmas or {}

        self._thread_local = threading.local()
        self._write_lock = threading.RLock()
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.Lock()

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
                for pragma, value in self._extra_pragmas.items():
                    # Validate pragma name to prevent injection.
                    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", pragma):
                        raise ValueError(f"Invalid pragma name provided: {pragma}")
                    # Validate and sanitize value to prevent injection
                    if isinstance(value, str):
                        # Restrict to safe characters for pragma values
                        if not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
                            raise ValueError(f"Invalid pragma value provided: {value}")
                        conn.execute(f"PRAGMA {pragma} = '{value}'")
                    elif isinstance(value, bool):
                        # Convert boolean values to ON/OFF for SQLite pragmas
                        conn.execute(f"PRAGMA {pragma} = {'ON' if value else 'OFF'}")
                    elif isinstance(value, (int, float)):
                        # For numeric values, ensure they're actually numeric
                        conn.execute(f"PRAGMA {pragma} = {value}")
                    else:
                        raise TypeError(f"Invalid pragma value type: {type(value)}")
        except (sqlite3.Error, ValueError, TypeError):
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
        """
        conn = getattr(self._thread_local, "connection", None)
        if conn is not None:
            try:
                # Using cursor() is a lightweight way to check if the connection is open.
                conn.cursor().close()
            except sqlite3.ProgrammingError:
                # Connection is closed, so we'll create a new one.
                with self._connections_lock:
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
    def read(self) -> sqlite3.Cursor:
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
    def write(self) -> sqlite3.Cursor:
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
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> Any:
        """
        Run a database callable in the event loop's executor and return its result.

        Parameters:
            func (Callable[[sqlite3.Cursor], Any]): Callable that will be invoked with a managed SQLite cursor.
            write (bool, optional): If true, the callable receives a cursor from a transactional write context; otherwise a read-only context is used. Defaults to False.
            loop (asyncio.AbstractEventLoop, optional): Event loop whose executor will run the callable. If omitted, the running event loop is used.

        Returns:
            Any: The value returned by `func` when invoked with the cursor.
        """
        loop = loop or asyncio.get_running_loop()
        executor_func = partial(self.run_sync, func, write=write)
        return await loop.run_in_executor(None, executor_func)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        """
        Close and clean up all tracked SQLite connections.

        Removes every connection from the manager's internal registry, attempts to close each connection (suppressing sqlite3.Error), and clears the current thread's stored connection reference.
        """
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
            # Close connections while holding the lock to prevent race conditions
            # where new connections might be created and missed during cleanup.
            for conn in connections:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass

        # Clear thread-local references in the current thread
        if hasattr(self._thread_local, "connection"):
            try:
                del self._thread_local.connection
            except AttributeError:
                pass


# Convenience alias for type hints
DbCallable = Callable[[sqlite3.Cursor], Any]
AsyncDbCallable = Callable[[sqlite3.Cursor], Awaitable[Any]]
