"""
Tests for async database connection pool implementation.

Tests the async-safe connection pool functionality including
connection reuse, cleanup, error handling, and configuration.
"""

import asyncio
import os
import sqlite3

# Set PYTHONPATH for imports
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.async_db_pool import (
    AsyncConnectionPool,
    close_all_async_pools,
    get_async_connection_pool,
    get_async_db_connection,
    get_async_pool_stats,
)


@pytest.mark.usefixtures("mock_event_loop")
class TestAsyncConnectionPool:
    """Test cases for AsyncConnectionPool class."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Set up test environment."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name
        self.pool = AsyncConnectionPool(
            self.db_path, max_connections=3, max_idle_time=1
        )

        yield

        # Clean up test environment.
        # Note: We need to run the async cleanup
        import asyncio

        asyncio.run(self.pool.close_all())
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_pool_initialization(self):
        """Test that pool initializes correctly."""
        assert self.pool.database_path == self.db_path
        assert self.pool.max_connections == 3
        assert self.pool.max_idle_time == 1
        assert self.pool._created_connections == 0
        assert len(self.pool._pool) == 0

    @pytest.mark.asyncio
    async def test_single_connection_usage(self):
        """Test using a single connection from pool."""
        async with self.pool.get_connection() as conn:
            assert conn is not None
            # Test that connection works
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, value TEXT)"
            )
            await conn.execute("INSERT INTO test (value) VALUES (?)", ("test",))
            await conn.commit()

            # Verify data was inserted
            cursor = await conn.execute(
                "SELECT value FROM test WHERE value = ?", ("test",)
            )
            result = await cursor.fetchone()
            assert result[0] == "test"

    @pytest.mark.asyncio
    async def test_connection_reuse(self):
        """Test that connections are reused from pool."""
        conn1_id = None
        conn2_id = None

        async with self.pool.get_connection() as conn1:
            conn1_id = id(conn1)

        async with self.pool.get_connection() as conn2:
            conn2_id = id(conn2)

        # Should reuse the same connection (pool size is 1 for reuse)
        assert conn1_id == conn2_id

    @pytest.mark.asyncio
    async def test_multiple_connections(self):
        """Test using multiple connections simultaneously."""
        connections = []

        async def get_connection():
            async with self.pool.get_connection() as conn:
                connections.append(id(conn))
                await asyncio.sleep(0.1)  # Simulate some work

        # Run multiple connection tasks concurrently
        tasks = [get_connection() for _ in range(3)]
        await asyncio.gather(*tasks)

        # Should have 3 different connections
        assert len(set(connections)) == 3

    @pytest.mark.asyncio
    async def test_connection_limit(self):
        """Test that pool respects connection limit."""
        active_count = 0
        max_active_seen = 0

        async def get_and_hold_connection():
            nonlocal active_count, max_active_seen
            async with self.pool.get_connection() as conn:
                active_count += 1
                max_active_seen = max(max_active_seen, active_count)
                await asyncio.sleep(0.1)  # Hold connection
                active_count -= 1

        # Start 4 tasks (pool limit is 3)
        tasks = [asyncio.create_task(get_and_hold_connection()) for _ in range(4)]

        # Wait for all to complete
        await asyncio.gather(*tasks)

        # Should have seen at most 3 active connections
        assert max_active_seen == 3

    @pytest.mark.asyncio
    async def test_connection_rollback_on_error(self):
        """Test that connection rolls back on error."""
        async with self.pool.get_connection() as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS test_rollback (id INTEGER PRIMARY KEY, value TEXT)"
            )
            await conn.execute(
                "INSERT INTO test_rollback (value) VALUES (?)", ("before_error",)
            )
            await conn.commit()

            # Simulate an error
            with pytest.raises(Exception):
                async with conn:
                    await conn.execute(
                        "INSERT INTO test_rollback (value) VALUES (?)", ("after_error",)
                    )
                    raise Exception("Simulated error")

            # Verify only first insert was committed
            cursor = await conn.execute("SELECT COUNT(*) FROM test_rollback")
            count = (await cursor.fetchone())[0]
            assert count == 1

    @pytest.mark.asyncio
    async def test_idle_connection_cleanup(self):
        """Test cleanup of idle connections."""
        # Create a connection and let it become idle
        async with self.pool.get_connection() as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS test_idle (id INTEGER PRIMARY KEY)"
            )
            await conn.commit()

        # Wait for connection to become idle (max_idle_time=1)
        await asyncio.sleep(1.5)

        # Force cleanup by resetting last cleanup time
        self.pool._last_cleanup = 0

        # Trigger cleanup
        await self.pool._cleanup_idle_connections()

        # Pool should be empty after cleanup
        assert len(self.pool._pool) == 0

    @pytest.mark.asyncio
    async def test_pool_statistics(self):
        """Test pool statistics reporting."""
        stats = self.pool.get_stats()

        # Initially empty
        assert stats["total_connections"] == 0
        assert stats["active_connections"] == 0
        assert stats["idle_connections"] == 0
        assert stats["created_connections"] == 0
        assert stats["max_connections"] == 3

        # Use a connection
        async with self.pool.get_connection() as conn:
            stats = self.pool.get_stats()
            assert stats["total_connections"] == 1
            assert stats["active_connections"] == 1
            assert stats["idle_connections"] == 0
            assert stats["created_connections"] == 1


@pytest.mark.usefixtures("mock_event_loop")
class TestAsyncConnectionPoolIntegration:
    """Integration tests for async connection pool management."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Set up test environment."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

        yield

        # Clean up test environment.
        import asyncio

        asyncio.run(close_all_async_pools())
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    @pytest.mark.asyncio
    async def test_get_async_connection_pool_singleton(self):
        """Test that get_async_connection_pool returns singleton instances."""
        pool1 = await get_async_connection_pool(self.db_path)
        pool2 = await get_async_connection_pool(self.db_path)

        assert pool1 is pool2

    @pytest.mark.asyncio
    async def test_get_async_db_connection_with_config(self):
        """Test get_async_db_connection with configuration."""
        config = {
            "database": {
                "pool_enabled": True,
                "pool_max_connections": 5,
                "pool_max_idle_time": 60,
                "pool_timeout": 10,
            }
        }

        async with await get_async_db_connection(config) as conn:
            assert conn is not None
            # Test that connection works
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS test_config (id INTEGER PRIMARY KEY)"
            )
            await conn.commit()

    @pytest.mark.asyncio
    async def test_get_async_db_connection_fallback_config(self):
        """Test get_async_db_connection with pooling disabled."""
        config = {"database": {"pool_enabled": False}}

        async with await get_async_db_connection(config) as conn:
            assert conn is not None
            # Test that connection works
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS test_fallback (id INTEGER PRIMARY KEY)"
            )
            await conn.commit()

    @pytest.mark.asyncio
    async def test_pool_stats_function(self):
        """Test get_async_pool_stats function."""
        from mmrelay.db_utils import get_db_path

        # Mock get_db_path to return our test db path
        original_get_db_path = get_db_path
        import mmrelay.db_utils

        mmrelay.db_utils.get_db_path = lambda: self.db_path

        try:
            # Create a pool by getting a connection
            config = {"database": {"pool_enabled": True}}
            async with await get_async_db_connection(config) as conn:
                await conn.execute(
                    "CREATE TABLE IF NOT EXISTS test_stats (id INTEGER PRIMARY KEY)"
                )
                await conn.commit()

            stats = get_async_pool_stats()
            assert isinstance(stats, dict)
            assert self.db_path in stats
        finally:
            # Restore original function
            mmrelay.db_utils.get_db_path = original_get_db_path


@pytest.mark.usefixtures("mock_event_loop")
class TestAsyncConnectionPoolThreadSafety:
    """Test thread safety of async connection pool."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Set up test environment."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name
        self.pool = AsyncConnectionPool(self.db_path, max_connections=5)

        yield

        # Clean up test environment.
        import asyncio

        asyncio.run(self.pool.close_all())
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    @pytest.mark.asyncio
    async def test_concurrent_connection_usage(self):
        """Test concurrent connection usage from multiple tasks."""
        connection_ids = []
        errors = []

        async def worker_task(worker_id):
            try:
                async with self.pool.get_connection() as conn:
                    connection_ids.append(id(conn))
                    await conn.execute(
                        "CREATE TABLE IF NOT EXISTS test_worker (id INTEGER, worker_id INTEGER)"
                    )
                    await conn.execute(
                        "INSERT INTO test_worker (worker_id) VALUES (?)", (worker_id,)
                    )
                    await conn.commit()
                    await asyncio.sleep(0.1)  # Simulate work
            except Exception as e:
                errors.append(e)

        # Run multiple worker tasks concurrently
        tasks = [worker_task(i) for i in range(10)]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Verify no errors occurred
        assert len(errors) == 0

        # Verify all workers got connections
        assert len(connection_ids) == 10

        # Verify data integrity
        async with self.pool.get_connection() as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM test_worker")
            count = (await cursor.fetchone())[0]
            assert count == 10

    @pytest.mark.asyncio
    async def test_pool_statistics_thread_safety(self):
        """Test that pool statistics are thread-safe."""
        stats_list = []

        async def stats_worker():
            async with self.pool.get_connection() as conn:
                await conn.execute(
                    "CREATE TABLE IF NOT EXISTS test_stats_safe (id INTEGER PRIMARY KEY)"
                )
                await conn.commit()
                stats = self.pool.get_stats()
                stats_list.append(stats)
                await asyncio.sleep(0.05)

        # Run multiple stats collection tasks
        tasks = [stats_worker() for _ in range(5)]
        await asyncio.gather(*tasks)

        # All stats should be valid
        for stats in stats_list:
            assert isinstance(stats, dict)
            assert "total_connections" in stats
            assert "active_connections" in stats
            assert "idle_connections" in stats


if __name__ == "__main__":
    unittest.main()
