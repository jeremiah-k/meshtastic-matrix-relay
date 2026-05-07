"""
SQLite connection provenance tracking for test leak detection.

Provides _ConnectionProvenance which intercepts sqlite3.connect calls and
records creation metadata so leaked connections are reported on test failure.
"""

import sqlite3
import threading
import traceback
import weakref
from typing import Any


class _ConnectionProvenance:
    """Track every sqlite3.connect() call with creation metadata."""

    def __init__(self) -> None:
        """
        Initialize the connection provenance tracker state.

        Sets up internal registry and bookkeeping used to record metadata for sqlite3 connections:
        - _registry: mapping from connection id to metadata dict (db path, creation stack, thread info, etc.).
        - _current_nodeid: test node identifier used when reporting leaked connections.
        - _real_connect: reference to the original sqlite3.connect function before patching.
        - _patched: boolean flag indicating whether sqlite3.connect has been replaced.
        """
        self._registry: dict[int, dict[str, Any]] = {}
        self._registry_lock = threading.RLock()
        self._current_nodeid: str = ""
        self._real_connect = sqlite3.connect
        self._patched: bool = False

    def set_nodeid(self, nodeid: str) -> None:
        """Set the current test node identifier for provenance tracking."""
        with self._registry_lock:
            self._current_nodeid = nodeid

    def clear_by_nodeid(self, nodeid: str) -> None:
        """Remove all provenance entries whose test_nodeid matches *nodeid*."""
        with self._registry_lock:
            stale_ids = [
                cid
                for cid, meta in self._registry.items()
                if meta.get("test_nodeid") == nodeid
            ]
            for cid in stale_ids:
                self._registry.pop(cid, None)

    def install(self) -> None:
        """
        Install a connection tracker that intercepts sqlite3.connect and records provenance for each new connection.

        Replaces the module-level sqlite3.connect with a tracked wrapper (no-op if already installed). Each call to the tracked connect stores a metadata dictionary in self._registry keyed by id(connection) containing: "conn_id", "db_path", "test_nodeid", "thread_name", "thread_id", and "creation_stack".
        """
        # Build closures before taking the lock so the critical section is minimal.
        real_connect = self._real_connect
        registry = self._registry
        tracker = self

        _class_cache: dict[type[sqlite3.Connection], type[sqlite3.Connection]] = {}

        def _make_tracked_class(
            base: type[sqlite3.Connection],
        ) -> type[sqlite3.Connection]:
            if base in _class_cache:
                return _class_cache[base]

            class _TrackedConnection(base):
                def close(self) -> None:
                    try:
                        super().close()
                    finally:
                        with tracker._registry_lock:
                            registry.pop(id(self), None)

            _class_cache[base] = _TrackedConnection
            return _TrackedConnection

        def _tracked_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            """
            Proxy for sqlite3.connect that records provenance metadata for each created connection.

            Records metadata (connection id, database path, current test nodeid, thread name/id, and creation stack) in the tracker registry keyed by the connection object's id.

            Parameters:
                *args: Positional arguments forwarded to sqlite3.connect (first positional arg is the database path).
                **kwargs: Keyword arguments forwarded to sqlite3.connect (may include "database").

            Returns:
                sqlite3.Connection: The connection object returned by the underlying sqlite3.connect call.
            """
            caller_factory = kwargs.get("factory")
            if caller_factory is None:
                kwargs["factory"] = _make_tracked_class(sqlite3.Connection)
            elif isinstance(caller_factory, type) and issubclass(
                caller_factory, sqlite3.Connection
            ):
                kwargs["factory"] = _make_tracked_class(caller_factory)
            # Note: Non-type callable factories cannot be wrapped; they will still
            # be tracked in the registry but won't auto-deregister on close().

            conn = real_connect(*args, **kwargs)
            db_path = args[0] if args else kwargs.get("database", "?")
            conn_id = id(conn)
            with tracker._registry_lock:
                registry[conn_id] = {
                    "conn_id": conn_id,
                    "db_path": str(db_path),
                    "test_nodeid": tracker._current_nodeid,
                    "thread_name": threading.current_thread().name,
                    "thread_id": threading.current_thread().ident,
                    "creation_stack": "".join(traceback.format_stack()[:-2]),
                }

            # Guard against id() reuse: if the connection is GC'd without close(),
            # the finalizer removes the stale registry entry before its address is reused.
            def _finalizer(cid: int = conn_id) -> None:
                with tracker._registry_lock:
                    registry.pop(cid, None)

            weakref.finalize(conn, _finalizer)
            return conn

        with self._registry_lock:
            if self._patched:
                return
            sqlite3.connect = _tracked_connect
            self._patched = True

    def remove(self, conn: sqlite3.Connection) -> None:
        """
        Stop tracking the given SQLite connection by removing its provenance entry from the internal registry.

        Parameters:
            conn (sqlite3.Connection): The connection object to remove from tracking.
        """
        with self._registry_lock:
            self._registry.pop(id(conn), None)

    def report_open(self) -> list[dict[str, Any]]:
        """
        Retrieve metadata for all currently tracked SQLite connections.

        Returns:
            list[dict[str, Any]]: A list of metadata dictionaries for each connection currently recorded in the provenance registry. Each dictionary contains the provenance information captured when the connection was created.
        """
        with self._registry_lock:
            return [dict(v) for v in self._registry.values()]

    def clear(self) -> None:
        """
        Remove all recorded sqlite3 connection provenance entries.

        This clears the internal registry of tracked connection metadata so subsequent
        calls will behave as if no connections have been recorded.
        """
        with self._registry_lock:
            self._registry.clear()

    def uninstall(self) -> None:
        """
        Restore the original sqlite3.connect function and clear the registry of tracked connections.

        If the provenance patch is not currently installed, this is a no-op. After calling this, the object is marked as unpatched and any stored connection metadata is removed.
        """
        with self._registry_lock:
            if not self._patched:
                return
            sqlite3.connect = self._real_connect
            self._patched = False
            self._registry.clear()


_conn_provenance = _ConnectionProvenance()
