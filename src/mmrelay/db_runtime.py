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
        conn = sqlite3.connect(self._path, check_same_thread=False)
        try:
            # Serialize PRAGMA setup to avoid concurrent WAL initialization races
            with self._write_lock:
                if self._busy_timeout_ms:
                    conn.execute(f"PRAGMA busy_timeout={int(self._busy_timeout_ms)}")
                if self._enable_wal:
                    # journal_mode pragma returns the applied mode; ignore result
                    conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                for pragma, value in self._extra_pragmas.items():
                    # Validate pragma name to prevent injection.
                    if not re.fullmatch(r"[a-zA-Z_]+", pragma):
                        raise ValueError(f"Invalid pragma name provided: {pragma}")
                    # Validate and sanitize value to prevent injection
                    if isinstance(value, str):
                        # Allow only alphanumeric, spaces, and common punctuation for string values
                        if not re.fullmatch(r"[a-zA-Z0-9_\-\s,\.]+", value):
                            raise ValueError(f"Invalid pragma value provided: {value}")
                        conn.execute(f"PRAGMA {pragma}='{value}'")
                    else:
                        # For numeric values, ensure they're actually numeric
                        if isinstance(value, (int, float)):
                            conn.execute(f"PRAGMA {pragma}={value}")
                        else:
                            raise TypeError(f"Invalid pragma value type: {type(value)}")
        except sqlite3.Error:
            # Ensure partially configured connection does not leak
            conn.close()
            raise

        with self._connections_lock:
            self._connections.add(conn)
        return conn

    def _get_connection(self) -> sqlite3.Connection:
        conn = getattr(self._thread_local, "connection", None)
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
        Yield a cursor for read operations without committing.
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
        Yield a cursor for write operations, committing on success.
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
        Execute `func` with a managed cursor and return the result.
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
        Execute `func` in the default executor and return its result.
        """
        loop = loop or asyncio.get_running_loop()
        executor_func = partial(self.run_sync, func, write=write)
        return await loop.run_in_executor(None, executor_func)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        """
        Close all tracked SQLite connections.
        """
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()

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
