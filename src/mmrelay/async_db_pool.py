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
        await conn.execute(
            "PRAGMA journal_mode=WAL"
        )  # Write-Ahead Logging for better concurrency
        await conn.execute(
            "PRAGMA synchronous=NORMAL"
        )  # Balance between safety and performance
        await conn.execute(
            "PRAGMA cache_size=-2000"
        )  # 2MB cache for better performance
        await conn.execute(
            "PRAGMA temp_store=MEMORY"
        )  # Store temporary tables in memory
        await conn.execute("PRAGMA mmap_size=268435456")  # 256MB memory mapping
        await conn.execute("PRAGMA wal_autocheckpoint=1000")  # WAL checkpoint interval
        await conn.execute("PRAGMA busy_timeout=30000")  # 30 second timeout

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
        async with self._pool_condition:
            for conn_id, conn_info in self._pool.items():
                try:
                    conn = conn_info["connection"]
                    # Close connection with timeout to avoid hanging
                    try:
                        await asyncio.wait_for(conn.close(), timeout=1.0)
                        logger.debug(f"Closed async connection {conn_id}")
                    except asyncio.TimeoutError:
                        logger.warning(f"Timeout closing async connection {conn_id}")
                        # Force close the underlying SQLite connection
                        if hasattr(conn, "_connection"):
                            conn._connection.close()
                    except Exception as e:
                        logger.warning(f"Error closing async connection {conn_id}: {e}")
                        # Try to force close the underlying connection
                        if hasattr(conn, "_connection"):
                            try:
                                conn._connection.close()
                            except Exception:
                                pass
                except Exception as e:
                    try:
                        logger.warning(f"Error closing async connection {conn_id}: {e}")
                    except (ValueError, OSError):
                        # Logging system may be shut down during atexit
                        pass

            self._pool.clear()
            self._created_connections = 0
            # Notify all waiting tasks that the pool is closed
            self._pool_condition.notify_all()
            try:
                logger.info("Closed all async connections in pool")
            except Exception:  # nosec B110
                # Logging system may be shut down during atexit
                # Broad exception catch is intentional - we want to silence any logging errors during shutdown
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
    async with _async_pools_lock:
        for pool in _async_pools.values():
            await pool.close_all()
        _async_pools.clear()
        try:
            logger.info("Closed all async connection pools")
        except Exception:  # nosec B110
            # Logging system may be shut down during atexit
            # Broad exception catch is intentional - we want to silence any logging errors during shutdown
            pass


def get_async_pool_stats() -> Dict[str, Dict[str, Any]]:
    """Get statistics for all async connection pools."""
    # Note: This returns a snapshot view without acquiring the async lock
    return {path: pool.get_stats() for path, pool in _async_pools.items()}


# Cleanup function to be called on application shutdown
async def async_cleanup():
    """Cleanup async connection pools on application shutdown."""
    await close_all_async_pools()


# Emergency fallback cleanup handler - only used if normal shutdown fails
# Normal shutdown should call async_cleanup() from main() for proper cleanup
# During testing, this handler is disabled to prevent hanging during pytest session cleanup
def _cleanup_atexit():
    """Emergency cleanup function for atexit - unreliable for async operations."""
    # Skip cleanup during testing to prevent pytest session hanging
    import os

    if os.getenv("PYTEST_CURRENT_TEST"):
        return

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If loop is running, create a fire-and-forget task (may not complete before shutdown)
            # This is unreliable - normal shutdown should use await async_cleanup() in main()
            asyncio.create_task(async_cleanup())
        else:
            # If loop is not running, run the coroutine directly
            loop.run_until_complete(async_cleanup())
    except RuntimeError:
        # No event loop available, skip cleanup
        pass


atexit.register(_cleanup_atexit)
