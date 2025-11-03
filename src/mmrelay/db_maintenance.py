"""
Database performance optimization and maintenance utilities.

Provides database maintenance scheduling, performance monitoring,
and optimization features for the SQLite database.
"""

import asyncio
import time
from typing import Any, Dict, Optional

from mmrelay.log_utils import get_logger

logger = get_logger(name="db_maintenance")


# Performance optimization pragmas
OPTIMIZATION_PRAGMAS = {
    "journal_mode": "WAL",  # Better concurrency
    "synchronous": "NORMAL",  # Balanced safety/performance
    "cache_size": -2000,  # 2MB cache
    "temp_store": "MEMORY",  # Temporary tables in memory
    "mmap_size": 268435456,  # 256MB memory mapping
    "wal_autocheckpoint": 1000,  # WAL checkpoint interval
    "busy_timeout": 30000,  # 30 second timeout
    "optimize": "ON",  # Enable automatic optimizations
}


class DatabaseMaintenance:
    """Database maintenance and performance optimization manager."""

    def __init__(self, database_path: str):
        """
        Initialize database maintenance manager.

        Args:
            database_path: Path to SQLite database file
        """
        self.database_path = database_path
        self._maintenance_task = None
        self._stats = {
            "total_connections": 0,
            "total_queries": 0,
            "total_time": 0.0,
            "last_optimization": None,
        }

    async def apply_optimizations(self, conn):
        """
        Apply performance optimization pragmas to database connection.

        Args:
            conn: Database connection (sync or async)
        """
        try:
            # Apply optimization pragmas
            for pragma, value in OPTIMIZATION_PRAGMAS.items():
                if pragma == "optimize":
                    # Special handling for OPTIMIZE pragma
                    await conn.execute(f"PRAGMA optimize = {value}")
                else:
                    await conn.execute(f"PRAGMA {pragma} = {value}")

            logger.debug("Applied database optimization pragmas")
        except Exception as e:
            logger.warning(f"Failed to apply some optimizations: {e}")

    async def analyze_database(self, conn):
        """
        Run ANALYZE command to update query planner statistics.

        Args:
            conn: Database connection
        """
        try:
            await conn.execute("ANALYZE")
            logger.info("Database analysis completed")
        except Exception as e:
            logger.error(f"Database analysis failed: {e}")

    async def vacuum_database(self, conn):
        """
        Run VACUUM command to rebuild database file and reclaim space.

        Args:
            conn: Database connection
        """
        try:
            logger.info("Starting database vacuum...")
            start_time = time.time()

            await conn.execute("VACUUM")

            elapsed = time.time() - start_time
            logger.info(f"Database vacuum completed in {elapsed:.2f} seconds")

            self._stats["last_optimization"] = time.time()
        except Exception as e:
            logger.error(f"Database vacuum failed: {e}")

    async def checkpoint_wal(self, conn):
        """
        Manually checkpoint WAL (Write-Ahead Logging) to commit transactions.

        Args:
            conn: Database connection
        """
        try:
            await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            logger.debug("WAL checkpoint completed")
        except Exception as e:
            logger.warning(f"WAL checkpoint failed: {e}")

    async def get_database_stats(self, conn) -> Dict[str, Any]:
        """
        Get comprehensive database statistics.

        Args:
            conn: Database connection

        Returns:
            Dictionary with database statistics
        """
        try:
            stats = {}

            # Get page count and size
            cursor = await conn.execute("PRAGMA page_count")
            page_count = (await cursor.fetchone())[0]
            stats["page_count"] = page_count

            cursor = await conn.execute("PRAGMA page_size")
            page_size = (await cursor.fetchone())[0]
            stats["page_size"] = page_size

            # Calculate database size
            stats["database_size_bytes"] = page_count * page_size
            stats["database_size_mb"] = stats["database_size_bytes"] / (1024 * 1024)

            # Get WAL file size if exists
            import os

            wal_path = f"{self.database_path}-wal"
            if os.path.exists(wal_path):
                wal_size = os.path.getsize(wal_path)
                stats["wal_size_bytes"] = wal_size
                stats["wal_size_mb"] = wal_size / (1024 * 1024)
            else:
                stats["wal_size_bytes"] = 0
                stats["wal_size_mb"] = 0

            # Get journal mode
            cursor = await conn.execute("PRAGMA journal_mode")
            journal_mode = (await cursor.fetchone())[0]
            stats["journal_mode"] = journal_mode

            # Get cache size
            cursor = await conn.execute("PRAGMA cache_size")
            cache_size = (await cursor.fetchone())[0]
            stats["cache_size"] = cache_size

            return stats
        except Exception as e:
            logger.error(f"Failed to get database stats: {e}")
            return {}

    async def run_maintenance(self, conn, vacuum_threshold_mb: int = 100):
        """
        Run comprehensive database maintenance if needed.

        Args:
            conn: Database connection
            vacuum_threshold_mb: Run VACUUM if database size exceeds this threshold
        """
        try:
            stats = await self.get_database_stats(conn)

            should_vacuum = (
                stats.get("database_size_mb", 0) > vacuum_threshold_mb
                or stats.get("wal_size_mb", 0) > vacuum_threshold_mb // 2
            )

            if should_vacuum:
                logger.info(
                    "Database maintenance threshold reached, running optimization..."
                )
                await self.analyze_database(conn)
                await self.vacuum_database(conn)
                await self.checkpoint_wal(conn)
            else:
                logger.debug("Database maintenance not needed")

        except Exception as e:
            logger.error(f"Database maintenance failed: {e}")

    def start_scheduled_maintenance(self, interval_hours: int = 24):
        """
        Start scheduled database maintenance task.

        Args:
            interval_hours: Maintenance interval in hours
        """
        if self._maintenance_task and not self._maintenance_task.done():
            logger.warning("Maintenance task already running")
            return

        async def maintenance_loop():
            """Background maintenance loop."""
            while True:
                try:
                    # Import here to avoid circular imports
                    from mmrelay.async_db_utils import _get_async_db_connection

                    connection_manager = _get_async_db_connection()
                    async with connection_manager as conn:
                        await self.run_maintenance(conn)

                    # Wait for next maintenance cycle
                    await asyncio.sleep(interval_hours * 3600)

                except asyncio.CancelledError:
                    logger.info("Maintenance task cancelled")
                    break
                except Exception as e:
                    logger.error(f"Maintenance task error: {e}")
                    # Wait before retrying
                    await asyncio.sleep(300)  # 5 minutes

        self._maintenance_task = asyncio.create_task(maintenance_loop())
        logger.info(
            f"Started scheduled database maintenance (interval: {interval_hours} hours)"
        )

    async def stop_scheduled_maintenance(self):
        """Stop scheduled database maintenance task."""
        if self._maintenance_task:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
            self._maintenance_task = None
            logger.info("Stopped scheduled database maintenance")

    def get_performance_stats(self) -> Dict[str, Any]:
        """
        Get performance statistics.

        Returns:
            Dictionary with performance statistics
        """
        return self._stats.copy()

    def update_query_stats(self, query_time: float):
        """
        Update query performance statistics.

        Args:
            query_time: Time taken for query in seconds
        """
        self._stats["total_queries"] += 1
        self._stats["total_time"] += query_time


# Global maintenance manager instance
_maintenance_manager: Optional[DatabaseMaintenance] = None


def get_maintenance_manager(database_path: str) -> DatabaseMaintenance:
    """
    Get or create database maintenance manager for given database path.

    Args:
        database_path: Path to SQLite database

    Returns:
        DatabaseMaintenance instance
    """
    global _maintenance_manager
    if (
        _maintenance_manager is None
        or _maintenance_manager.database_path != database_path
    ):
        _maintenance_manager = DatabaseMaintenance(database_path)
        logger.info(f"Created database maintenance manager for {database_path}")
    return _maintenance_manager


async def optimize_database_connection(conn):
    """
    Apply optimizations to a database connection.

    This is a convenience function that applies all standard optimizations
    to a database connection.

    Args:
        conn: Database connection (sync or async)
    """
    maintenance = get_maintenance_manager("")  # Will be initialized with proper path
    await maintenance.apply_optimizations(conn)


async def run_database_maintenance(database_path: str, vacuum_threshold_mb: int = 100):
    """
    Run one-time database maintenance.

    Args:
        database_path: Path to SQLite database
        vacuum_threshold_mb: Run VACUUM if database size exceeds this threshold
    """
    maintenance = get_maintenance_manager(database_path)

    # Import here to avoid circular imports
    from mmrelay.async_db_utils import _get_async_db_connection

    connection_manager = _get_async_db_connection()
    async with connection_manager as conn:
        await maintenance.run_maintenance(conn, vacuum_threshold_mb)


def start_background_maintenance(database_path: str, interval_hours: int = 24):
    """
    Start background database maintenance task.

    Args:
        database_path: Path to SQLite database
        interval_hours: Maintenance interval in hours
    """
    maintenance = get_maintenance_manager(database_path)
    maintenance.start_scheduled_maintenance(interval_hours)


async def stop_background_maintenance():
    """Stop background database maintenance task."""
    global _maintenance_manager
    if _maintenance_manager:
        await _maintenance_manager.stop_scheduled_maintenance()
