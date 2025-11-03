"""
Database connection pool implementation for Meshtastic Matrix Relay.

Provides thread-safe connection pooling for SQLite databases to improve
performance and resource management in multi-threaded environments.
"""

import atexit
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict

from mmrelay.log_utils import get_logger

logger = get_logger(name="db_pool")


class ConnectionPool:
    """
    Thread-safe SQLite connection pool.

    Manages a pool of database connections that can be reused across
    multiple threads, reducing connection overhead and improving performance.
    """

    def __init__(
        self,
        database_path: str,
        max_connections: int = 10,
        max_idle_time: int = 300,
        timeout: int = 30,
    ):
        """
        Initialize the connection pool.

        Args:
            database_path: Path to the SQLite database file
            max_connections: Maximum number of connections in the pool
            max_idle_time: Maximum time (seconds) a connection can be idle before closing
            timeout: Connection timeout in seconds
        """
        self.database_path = database_path
        self.max_connections = max_connections
        self.max_idle_time = max_idle_time
        self.timeout = timeout

        self._pool: Dict[str, Dict[str, Any]] = {}
        self._pool_lock = threading.RLock()
        self._created_connections = 0
        self._last_cleanup = time.time()

        logger.debug(
            f"Initialized connection pool for {database_path} "
            f"(max_connections={max_connections}, max_idle_time={max_idle_time}s)"
        )

    def _create_connection(self) -> sqlite3.Connection:
        """Create a new database connection with proper settings."""
        conn = sqlite3.connect(
            self.database_path,
            timeout=self.timeout,
            check_same_thread=False,  # Allow sharing across threads
        )

        # Configure connection for better performance and reliability
        conn.execute(
            "PRAGMA journal_mode=WAL"
        )  # Write-Ahead Logging for better concurrency
        conn.execute(
            "PRAGMA synchronous=NORMAL"
        )  # Balance between safety and performance
        conn.execute("PRAGMA cache_size=10000")  # Increase cache size
        conn.execute("PRAGMA temp_store=MEMORY")  # Store temporary tables in memory
        conn.execute("PRAGMA busy_timeout=30000")  # 30 second timeout

        self._created_connections += 1
        logger.debug(
            f"Created new database connection (total: {self._created_connections})"
        )

        return conn

    def _cleanup_idle_connections(self):
        """Remove idle connections from the pool."""
        current_time = time.time()

        # Only cleanup periodically to avoid excessive locking
        if current_time - self._last_cleanup < 60:  # Cleanup every minute
            return

        with self._pool_lock:
            idle_connections = []

            for conn_id, conn_info in list(self._pool.items()):
                if (
                    current_time - conn_info["last_used"] > self.max_idle_time
                    and not conn_info["in_use"]
                ):
                    idle_connections.append(conn_id)

            for conn_id in idle_connections:
                conn_info = self._pool.pop(conn_id)
                try:
                    conn_info["connection"].close()
                    self._created_connections -= 1
                    logger.debug(f"Closed idle connection {conn_id}")
                except sqlite3.Error as e:
                    logger.warning(f"Error closing idle connection {conn_id}: {e}")

            self._last_cleanup = current_time

    @contextmanager
    def get_connection(self):
        """
        Get a connection from the pool.

        Returns:
            Context manager that yields a database connection
        """
        conn_id = None
        connection = None

        try:
            # Cleanup idle connections periodically
            self._cleanup_idle_connections()

            with self._pool_lock:
                # Look for available connection in pool
                for pool_id, conn_info in self._pool.items():
                    if not conn_info["in_use"]:
                        conn_id = pool_id
                        connection = conn_info["connection"]
                        conn_info["in_use"] = True
                        conn_info["last_used"] = time.time()
                        logger.debug(f"Reusing connection {conn_id} from pool")
                        break

                # If no available connection and we can create more, create new one
                if (
                    connection is None
                    and self._created_connections < self.max_connections
                ):
                    connection = self._create_connection()
                    conn_id = (
                        f"conn_{int(time.time() * 1000000)}_{self._created_connections}"
                    )
                    self._pool[conn_id] = {
                        "connection": connection,
                        "in_use": True,
                        "created_at": time.time(),
                        "last_used": time.time(),
                    }
                    logger.debug(f"Created new connection {conn_id}")

                # If we still don't have a connection, wait for one to become available
                if connection is None:
                    logger.warning(
                        "Connection pool exhausted, waiting for available connection"
                    )
                    # Simple retry mechanism - in production, you might want a more sophisticated approach
                    time.sleep(0.1)
                    # Try again (recursive call with depth limit)
                    for _ in range(50):  # Max 5 second wait
                        with self._pool_lock:
                            for pool_id, conn_info in self._pool.items():
                                if not conn_info["in_use"]:
                                    conn_id = pool_id
                                    connection = conn_info["connection"]
                                    conn_info["in_use"] = True
                                    conn_info["last_used"] = time.time()
                                    break
                        if connection is not None:
                            break
                        time.sleep(0.1)

                    if connection is None:
                        raise sqlite3.OperationalError(
                            "Connection pool exhausted and timeout reached"
                        )

            yield connection

        except Exception as e:
            logger.error(f"Error in connection pool: {e}")
            if connection:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
            raise
        finally:
            # Return connection to pool
            if conn_id and connection:
                with self._pool_lock:
                    if conn_id in self._pool:
                        self._pool[conn_id]["in_use"] = False
                        self._pool[conn_id]["last_used"] = time.time()
                        logger.debug(f"Returned connection {conn_id} to pool")

    def close_all(self):
        """Close all connections in the pool."""
        with self._pool_lock:
            for conn_id, conn_info in self._pool.items():
                try:
                    conn_info["connection"].close()
                    logger.debug(f"Closed connection {conn_id}")
                except sqlite3.Error as e:
                    logger.warning(f"Error closing connection {conn_id}: {e}")

            self._pool.clear()
            self._created_connections = 0
            logger.info("Closed all connections in pool")

    def get_stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        with self._pool_lock:
            active = sum(1 for conn_info in self._pool.values() if conn_info["in_use"])
            idle = len(self._pool) - active

            return {
                "total_connections": len(self._pool),
                "active_connections": active,
                "idle_connections": idle,
                "created_connections": self._created_connections,
                "max_connections": self.max_connections,
            }


# Global pool instances (one per database path)
_pools: Dict[str, ConnectionPool] = {}
_pools_lock = threading.Lock()


def get_connection_pool(database_path: str, **kwargs) -> ConnectionPool:
    """
    Get or create a connection pool for the given database path.

    Args:
        database_path: Path to the SQLite database
        **kwargs: Additional arguments for ConnectionPool

    Returns:
        ConnectionPool instance
    """
    with _pools_lock:
        if database_path not in _pools:
            _pools[database_path] = ConnectionPool(database_path, **kwargs)
            logger.info(f"Created connection pool for {database_path}")
        return _pools[database_path]


def get_db_connection(config, **kwargs):
    """
    Get a database connection using the connection pool.

    This is a convenience function that creates a context manager
    for database connections using the appropriate pool.

    Args:
        config: Application configuration
        **kwargs: Additional arguments for connection pool

    Returns:
        Context manager that yields a database connection
    """
    # Import here to avoid circular imports
    from mmrelay.db_utils import get_db_path

    database_path = get_db_path()

    # Extract pool configuration from config if available
    pool_config = {}
    if config:
        db_config = config.get("database", {})
        pool_config = {
            "max_connections": db_config.get("pool_max_connections", 10),
            "max_idle_time": db_config.get("pool_max_idle_time", 300),
            "timeout": db_config.get("pool_timeout", 30),
        }

    # Override with any explicitly passed kwargs
    pool_config.update(kwargs)

    pool = get_connection_pool(database_path, **pool_config)
    return pool.get_connection()


def close_all_pools():
    """Close all connection pools."""
    with _pools_lock:
        for pool in _pools.values():
            pool.close_all()
        _pools.clear()
        logger.info("Closed all connection pools")


def get_pool_stats() -> Dict[str, Dict[str, Any]]:
    """Get statistics for all connection pools."""
    with _pools_lock:
        return {path: pool.get_stats() for path, pool in _pools.items()}


# Cleanup function to be called on application shutdown
def cleanup():
    """Cleanup connection pools on application shutdown."""
    close_all_pools()


# Register cleanup function to be called when the module is garbage collected
atexit.register(cleanup)
