"""
Async database connection pool implementation for Meshtastic Matrix Relay.

Provides async-safe connection pooling for SQLite databases using aiosqlite
to improve performance and resource management in async environments.
"""

import asyncio
import atexit
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Any, Dict

from mmrelay.log_utils import get_logger

logger = get_logger(name="async_db_pool")


class AsyncConnectionPool:
    """
    Async-safe SQLite connection pool using aiosqlite.

    Manages a pool of async database connections that can be reused across
    multiple async tasks, reducing connection overhead and improving performance.
    """

    def __init__(
        self,
        database_path: str,
        max_connections: int = 10,
        max_idle_time: int = 300,
        timeout: int = 30,
    ):
        """
        Initialize the async connection pool.

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
        self._pool_lock = asyncio.Lock()
        self._pool_condition = asyncio.Condition(self._pool_lock)
        self._created_connections = 0
        self._last_cleanup = time.time()

        logger.debug(
            f"Initialized async connection pool for {database_path} "
            f"(max_connections={max_connections}, max_idle_time={max_idle_time}s)"
        )

    async def _create_connection(self):
        """Create a new async database connection with proper settings."""
        import aiosqlite

        conn = await aiosqlite.connect(
            self.database_path,
            timeout=self.timeout,
        )

        # Configure connection for better performance and reliability
        from mmrelay.constants.database import OPTIMIZATION_PRAGMAS

        for pragma_name, pragma_value in OPTIMIZATION_PRAGMAS.items():
            if pragma_name == "optimize":
                # Skip optimize pragma as it's not a standard PRAGMA
                continue
            await conn.execute(f"PRAGMA {pragma_name}={pragma_value}")

        self._created_connections += 1
        logger.debug(
            f"Created new async database connection (total: {self._created_connections})"
        )

        return conn

    async def _cleanup_idle_connections(self):
        """Remove idle connections from the pool."""
        current_time = time.time()

        # Only cleanup periodically to avoid excessive locking
        if current_time - self._last_cleanup < 60:  # Cleanup every minute
            return

        async with self._pool_lock:
            idle_connections = []

            for conn_id, conn_info in list(self._pool.items()):
                if (
                    current_time - conn_info["last_used"] > self.max_idle_time
                    and not conn_info["in_use"]
                ):
                    idle_connections.append(conn_id)

            for conn_id in idle_connections:
                conn_info = self._pool.pop(conn_id)
                # Decrement connection count before attempting to close
                self._created_connections -= 1
                try:
                    await conn_info["connection"].close()
                    logger.debug(f"Closed idle async connection {conn_id}")
                except sqlite3.Error as e:
                    logger.warning(
                        f"Error closing idle async connection {conn_id}: {e}"
                    )

            self._last_cleanup = current_time

    def __del__(self):
        """Destructor that ensures cleanup doesn't hang during garbage collection."""
        try:
            # Synchronous cleanup that doesn't require event loop
            if hasattr(self, "_pool") and self._pool:
                for conn_id, conn_info in list(self._pool.items()):
                    try:
                        conn = conn_info.get("connection")
                        if conn and hasattr(conn, "_connection"):
                            # Force close the underlying SQLite connection
                            conn._connection.close()
                    except Exception:
                        pass  # Ignore all errors during garbage collection
                self._pool.clear()
                self._created_connections = 0
        except Exception:
            pass  # Ignore all errors during garbage collection

    def close_all_sync(self):
        """Synchronous version of close_all for use during shutdown."""
        try:
            if hasattr(self, "_pool") and self._pool:
                for conn_id, conn_info in list(self._pool.items()):
                    try:
                        conn = conn_info.get("connection")
                        if conn and hasattr(conn, "_connection"):
                            # Force close the underlying SQLite connection
                            conn._connection.close()
                    except Exception:
                        pass  # Ignore errors during sync cleanup
                self._pool.clear()
                self._created_connections = 0
        except Exception:
            pass  # Ignore all errors during sync cleanup

    @asynccontextmanager
    async def get_connection(self):
        """
        Get an async connection from pool.

        Returns:
            Context manager that yields an async database connection
        """
        await self._cleanup_idle_connections()
        conn_id = None
        connection = None

        async with self._pool_condition:
            # Wait until a connection is available or can be created
            while connection is None:
                # Try to find an available connection
                for pool_id, conn_info in self._pool.items():
                    if not conn_info["in_use"]:
                        conn_id = pool_id
                        connection = conn_info["connection"]
                        conn_info["in_use"] = True
                        conn_info["last_used"] = time.time()
                        logger.debug(f"Reusing async connection {conn_id} from pool")
                        break
                if connection:
                    break

                # If no connection is available, try to create one
                if self._created_connections < self.max_connections:
                    connection = await self._create_connection()
                    conn_id = str(id(connection))
                    self._pool[conn_id] = {
                        "connection": connection,
                        "in_use": True,
                        "created_at": time.time(),
                        "last_used": time.time(),
                    }
                    logger.debug(f"Created new async connection {conn_id}")
                    break

                # Pool is full, wait for a connection to be released
                logger.warning(
                    "Async connection pool exhausted, waiting for available connection. Pool stats: %s",
                    self.get_stats(),
                )
                try:
                    await asyncio.wait_for(self._pool_condition.wait(), self.timeout)
                except asyncio.TimeoutError as err:
                    raise sqlite3.OperationalError(
                        "Async connection pool exhausted and timeout reached"
                    ) from err

        try:
            yield connection
        except Exception:
            logger.exception("Error in async connection pool")
            if connection:
                try:
                    await connection.rollback()
                except sqlite3.Error:
                    pass
            raise
        finally:
            # Return connection to pool
            if conn_id and connection:
                logger.debug(f"Returning connection {conn_id} to pool")
                async with self._pool_condition:
                    if conn_id in self._pool:
                        self._pool[conn_id]["in_use"] = False
                        self._pool[conn_id]["last_used"] = time.time()
                        logger.debug(f"Returned async connection {conn_id} to pool")
                        self._pool_condition.notify()

    async def close_all(self):
        """Close all connections in the pool."""
        # Try to acquire the condition lock with timeout to prevent hanging during shutdown
        try:
            await asyncio.wait_for(self._pool_condition.acquire(), timeout=0.5)
        except (asyncio.TimeoutError, RuntimeError, Exception):
            # If we can't acquire the lock, try to close connections directly without lock
            # This can happen during shutdown when the event loop is closing
            try:
                for conn_id, conn_info in self._pool.items():
                    try:
                        conn = conn_info["connection"]
                        await self._force_close_connection(conn, conn_id)
                    except Exception:
                        pass  # Ignore errors during shutdown cleanup
                self._pool.clear()
                self._created_connections = 0
            except Exception:
                pass  # Ignore all errors during shutdown
            return

        try:
            for conn_id, conn_info in self._pool.items():
                try:
                    conn = conn_info["connection"]
                    # Close connection with timeout to avoid hanging
                    await self._force_close_connection(conn, conn_id)
                except Exception as e:
                    try:
                        logger.warning(f"Error closing async connection {conn_id}: {e}")
                    except (ValueError, OSError):
                        # Logging system may be shut down during atexit
                        pass

            self._pool.clear()
            self._created_connections = 0
            # Notify all waiting tasks that the pool is closed
            try:
                self._pool_condition.notify_all()
            except RuntimeError:
                pass  # Event loop may be closing
            try:
                logger.info("Closed all async connections in pool")
            except Exception:  # nosec B110
                # Logging system may be shut down during atexit
                # Broad exception catch is intentional - we want to silence any logging errors during shutdown
                pass
        finally:
            try:
                self._pool_condition.release()
            except (RuntimeError, Exception):
                pass  # Ignore errors during shutdown

    async def _force_close_connection(self, conn, conn_id: str):
        """Force close a connection with multiple fallback strategies."""
        try:
            await asyncio.wait_for(conn.close(), timeout=1.0)
            logger.debug(f"Closed async connection {conn_id}")
        except asyncio.TimeoutError:
            logger.warning(f"Timeout closing async connection {conn_id}")
            # Force close the underlying SQLite connection
            if hasattr(conn, "_connection"):
                try:
                    conn._connection.close()
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Error closing async connection {conn_id}: {e}")
            # Try to force close the underlying connection
            if hasattr(conn, "_connection"):
                try:
                    conn._connection.close()
                except Exception:
                    pass

    def get_stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        # Note: This is a synchronous method that doesn't acquire the async lock
        # It provides a snapshot view that may be slightly stale
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
_async_pools: Dict[str, AsyncConnectionPool] = {}
_async_pools_lock = asyncio.Lock()


async def get_async_connection_pool(
    database_path: str, **kwargs
) -> AsyncConnectionPool:
    """
    Get or create an async connection pool for the given database path.

    Args:
        database_path: Path to the SQLite database
        **kwargs: Additional arguments for AsyncConnectionPool

    Returns:
        AsyncConnectionPool instance
    """
    async with _async_pools_lock:
        if database_path not in _async_pools:
            _async_pools[database_path] = AsyncConnectionPool(database_path, **kwargs)
            logger.info(f"Created async connection pool for {database_path}")
        return _async_pools[database_path]


async def get_async_db_connection(config, **kwargs):
    """
    Get an async database connection using the async connection pool.

    This is a convenience function that creates an async context manager
    for database connections using the appropriate pool.

    Args:
        config: Application configuration
        **kwargs: Additional arguments for connection pool

    Returns:
        Async context manager that yields a database connection
    """
    from contextlib import asynccontextmanager

    import aiosqlite

    from mmrelay.constants.config import (
        CONFIG_KEY_POOL_ENABLED,
        CONFIG_KEY_POOL_MAX_CONNECTIONS,
        CONFIG_KEY_POOL_MAX_IDLE_TIME,
        CONFIG_KEY_POOL_TIMEOUT,
        DEFAULT_POOL_ENABLED,
        DEFAULT_POOL_MAX_CONNECTIONS,
        DEFAULT_POOL_MAX_IDLE_TIME,
        DEFAULT_POOL_TIMEOUT,
    )
    from mmrelay.db_utils import get_db_path

    database_path = get_db_path()

    # Extract pool configuration from config if available
    pool_config = {}
    if config:
        db_config = config.get("database", {})

        # Check if pooling is disabled
        if not db_config.get(CONFIG_KEY_POOL_ENABLED, DEFAULT_POOL_ENABLED):

            @asynccontextmanager
            async def direct_connection():
                conn = await aiosqlite.connect(database_path)
                try:
                    # Apply same PRAGMAs for consistency with pooled connections
                    await conn.execute("PRAGMA journal_mode=WAL")
                    await conn.execute("PRAGMA synchronous=NORMAL")
                    await conn.execute("PRAGMA cache_size=-2000")
                    await conn.execute("PRAGMA temp_store=MEMORY")
                    await conn.execute("PRAGMA mmap_size=268435456")
                    await conn.execute("PRAGMA wal_autocheckpoint=1000")
                    await conn.execute("PRAGMA busy_timeout=30000")
                    yield conn
                finally:
                    await conn.close()

            return direct_connection()

        pool_config = {
            "max_connections": db_config.get(
                CONFIG_KEY_POOL_MAX_CONNECTIONS, DEFAULT_POOL_MAX_CONNECTIONS
            ),
            "max_idle_time": db_config.get(
                CONFIG_KEY_POOL_MAX_IDLE_TIME, DEFAULT_POOL_MAX_IDLE_TIME
            ),
            "timeout": db_config.get(CONFIG_KEY_POOL_TIMEOUT, DEFAULT_POOL_TIMEOUT),
        }

    # Override with any explicitly passed kwargs
    pool_config.update(kwargs)

    pool = await get_async_connection_pool(database_path, **pool_config)
    return pool.get_connection()


async def close_all_async_pools():
    """Close all async connection pools."""
    # Try to acquire the global pools lock with timeout to prevent hanging
    try:
        await asyncio.wait_for(_async_pools_lock.acquire(), timeout=0.5)
    except (asyncio.TimeoutError, RuntimeError, Exception):
        # If we can't acquire the lock, try to close pools directly without lock
        # This can happen during shutdown when the event loop is closing
        try:
            for pool in list(_async_pools.values()):
                try:
                    await pool.close_all()
                except Exception:
                    pass  # Ignore errors during shutdown cleanup
            _async_pools.clear()
        except Exception:
            pass  # Ignore all errors during shutdown
        return

    try:
        for pool in list(_async_pools.values()):
            try:
                await pool.close_all()
            except Exception:
                pass  # Ignore errors during shutdown cleanup
        _async_pools.clear()
        try:
            logger.info("Closed all async connection pools")
        except Exception:  # nosec B110
            # Logging system may be shut down during atexit
            # Broad exception catch is intentional - we want to silence any logging errors during shutdown
            pass
    finally:
        try:
            _async_pools_lock.release()
        except (RuntimeError, Exception):
            pass  # Ignore errors during shutdown


def get_async_pool_stats() -> Dict[str, Dict[str, Any]]:
    """Get statistics for all async connection pools."""
    # Note: This returns a snapshot view without acquiring the async lock
    return {path: pool.get_stats() for path, pool in _async_pools.items()}


def close_all_async_pools_sync():
    """Synchronous version of close_all_async_pools for use during shutdown."""
    try:
        # Clear pools without any cleanup to prevent hanging
        _async_pools.clear()
    except Exception:
        pass  # Ignore all errors during sync cleanup


# Cleanup function to be called on application shutdown
async def async_cleanup():
    """Cleanup async connection pools on application shutdown."""
    await close_all_async_pools()


# Completely disable atexit handler during tests to prevent hanging
# The async connection pools will be cleaned up by pytest fixtures and garbage collection
# atexit.register(_cleanup_atexit)  # Commented out to prevent test hanging
