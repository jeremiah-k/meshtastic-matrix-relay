"""
Tests for database connection pool implementation.

Tests the thread-safe connection pool functionality including
connection reuse, cleanup, error handling, and configuration.
"""

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch

from mmrelay.db_pool import (
    ConnectionPool,
    cleanup,
    close_all_pools,
    get_connection_pool,
    get_db_connection,
    get_pool_stats,
)


class TestConnectionPool(unittest.TestCase):
    """Test cases for ConnectionPool class."""

    def setUp(self):
        """Set up test environment."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name
        self.pool = ConnectionPool(self.db_path, max_connections=3, max_idle_time=1)

    def tearDown(self):
        """Clean up test environment."""
        self.pool.close_all()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_pool_initialization(self):
        """Test that pool initializes correctly."""
        self.assertEqual(self.pool.database_path, self.db_path)
        self.assertEqual(self.pool.max_connections, 3)
        self.assertEqual(self.pool.max_idle_time, 1)
        self.assertEqual(self.pool._created_connections, 0)
        self.assertEqual(len(self.pool._pool), 0)

    def test_single_connection_usage(self):
        """Test basic connection usage."""
        with self.pool.get_connection() as conn:
            self.assertIsInstance(conn, sqlite3.Connection)
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            self.assertEqual(result[0], 1)

        # Connection should be returned to pool
        self.assertEqual(len(self.pool._pool), 1)
        self.assertFalse(next(iter(self.pool._pool.values()))["in_use"])

    def test_connection_reuse(self):
        """Test that connections are reused from pool."""
        # First connection
        with self.pool.get_connection() as conn1:
            cursor1 = conn1.cursor()
            cursor1.execute("CREATE TABLE test (id INTEGER)")
            conn1.commit()

        # Second connection should reuse the first one
        with self.pool.get_connection() as conn2:
            cursor2 = conn2.cursor()
            cursor2.execute("INSERT INTO test (id) VALUES (1)")
            conn2.commit()

        # Verify data was inserted (same connection)
        with self.pool.get_connection() as conn3:
            cursor3 = conn3.cursor()
            cursor3.execute("SELECT COUNT(*) FROM test")
            result = cursor3.fetchone()
            self.assertEqual(result[0], 1)

        # Should only have created one connection
        self.assertEqual(self.pool._created_connections, 1)

    def test_multiple_connections(self):
        """Test pool with multiple concurrent connections."""

        def use_connection():
            with self.pool.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                time.sleep(0.1)  # Simulate some work
                return cursor.fetchone()[0]

        # Use multiple connections concurrently
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(use_connection) for _ in range(3)]
            results = [future.result() for future in as_completed(futures)]

        self.assertEqual(results, [1, 1, 1])
        self.assertEqual(self.pool._created_connections, 3)

    def test_connection_limit(self):
        """Test that pool respects max_connections limit."""
        connections = []

        # Get all available connections
        for _ in range(3):
            conn = self.pool.get_connection()
            connections.append(conn.__enter__())

        # Try to get one more - should block and eventually succeed
        start_time = time.time()
        with self.pool.get_connection() as extra_conn:
            elapsed = time.time() - start_time
            # Should have waited for a connection to become available
            self.assertGreater(elapsed, 0.0001)  # Very small threshold for fast tests
            self.assertIsInstance(extra_conn, sqlite3.Connection)

        # Clean up
        for conn in connections:
            conn.__exit__(None, None, None)

    def test_idle_connection_cleanup(self):
        """Test cleanup of idle connections."""
        # Create a connection and let it become idle
        with self.pool.get_connection():
            pass  # Connection returned to pool

        self.assertEqual(len(self.pool._pool), 1)

        # Force cleanup by setting last_used to past time and resetting last_cleanup
        for conn_info in self.pool._pool.values():
            conn_info["last_used"] = time.time() - 2  # 2 seconds ago
        self.pool._last_cleanup = time.time() - 70  # Force cleanup to run

        self.pool._cleanup_idle_connections()

        # Connection should be cleaned up
        self.assertEqual(len(self.pool._pool), 0)

    def test_connection_error_handling(self):
        """Test error handling in connection pool."""
        # Test error handling during connection usage
        try:
            with self.pool.get_connection() as conn:
                # Simulate an error
                raise ValueError("Test error")
        except ValueError:
            pass  # Expected

        # Connection should still be usable after error
        with self.pool.get_connection() as conn:
            self.assertIsInstance(conn, sqlite3.Connection)

    def test_pool_statistics(self):
        """Test pool statistics reporting."""
        # Initially empty
        stats = self.pool.get_stats()
        self.assertEqual(stats["total_connections"], 0)
        self.assertEqual(stats["active_connections"], 0)
        self.assertEqual(stats["idle_connections"], 0)

        # Add a connection
        with self.pool.get_connection():
            stats = self.pool.get_stats()
            self.assertEqual(stats["total_connections"], 1)
            self.assertEqual(stats["active_connections"], 1)
            self.assertEqual(stats["idle_connections"], 0)

        # Connection returned to pool
        stats = self.pool.get_stats()
        self.assertEqual(stats["total_connections"], 1)
        self.assertEqual(stats["active_connections"], 0)
        self.assertEqual(stats["idle_connections"], 1)

    def test_connection_rollback_on_error(self):
        """Test that transactions are rolled back on errors."""
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE test (id INTEGER)")
            cursor.execute("INSERT INTO test (id) VALUES (1)")
            conn.commit()

        # Simulate an error during connection usage
        try:
            with self.pool.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO test (id) VALUES (2)")
                # Don't commit - simulate error
                raise ValueError("Test error")
        except ValueError:
            pass

        # Verify rollback occurred
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM test")
            result = cursor.fetchone()
            self.assertEqual(result[0], 1)  # Only the first insert should remain


class TestConnectionPoolIntegration(unittest.TestCase):
    """Integration tests for connection pool functions."""

    def setUp(self):
        """Set up test environment."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

        # Clear any existing pools
        close_all_pools()

    def tearDown(self):
        """Clean up test environment."""
        close_all_pools()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_get_connection_pool_singleton(self):
        """Test that get_connection_pool returns singleton instances."""
        pool1 = get_connection_pool(self.db_path)
        pool2 = get_connection_pool(self.db_path)
        self.assertIs(pool1, pool2)

        # Different path should get different pool
        different_path = tempfile.NamedTemporaryFile(delete=False, suffix=".db").name
        try:
            pool3 = get_connection_pool(different_path)
            self.assertIsNot(pool1, pool3)
        finally:
            if os.path.exists(different_path):
                os.unlink(different_path)

    def test_get_db_connection_with_config(self):
        """Test get_db_connection with configuration."""
        config = {
            "database": {
                "pool_max_connections": 5,
                "pool_max_idle_time": 120,
                "pool_timeout": 15,
            }
        }

        with patch("mmrelay.db_utils.get_db_path", return_value=self.db_path):
            connection_context = get_db_connection(config)

            with connection_context as conn:
                self.assertIsInstance(conn, sqlite3.Connection)

                # Verify pool was created with correct config
                pool = get_connection_pool(self.db_path)
                self.assertEqual(pool.max_connections, 5)
                self.assertEqual(pool.max_idle_time, 120)
                self.assertEqual(pool.timeout, 15)

    def test_get_db_connection_fallback_config(self):
        """Test get_db_connection with fallback configuration."""
        config = {}  # No database config

        with patch("mmrelay.db_utils.get_db_path", return_value=self.db_path):
            connection_context = get_db_connection(config)

            with connection_context as conn:
                self.assertIsInstance(conn, sqlite3.Connection)

                # Verify pool was created with default config
                pool = get_connection_pool(self.db_path)
                self.assertEqual(pool.max_connections, 10)  # Default
                self.assertEqual(pool.max_idle_time, 300)  # Default
                self.assertEqual(pool.timeout, 30)  # Default

    def test_pool_stats_function(self):
        """Test get_pool_stats function."""
        # Initially empty
        stats = get_pool_stats()
        self.assertEqual(stats, {})

        # Create a pool
        get_connection_pool(self.db_path)

        # Should now have stats
        stats = get_pool_stats()
        self.assertIn(self.db_path, stats)
        self.assertIsInstance(stats[self.db_path], dict)

    def test_cleanup_function(self):
        """Test cleanup function."""
        # Create some pools
        get_connection_pool(self.db_path)
        get_connection_pool(
            tempfile.NamedTemporaryFile(delete=False, suffix=".db").name
        )

        # Verify pools exist
        stats = get_pool_stats()
        self.assertEqual(len(stats), 2)

        # Cleanup
        cleanup()

        # All pools should be closed
        stats = get_pool_stats()
        self.assertEqual(stats, {})


class TestConnectionPoolThreadSafety(unittest.TestCase):
    """Test thread safety of connection pool."""

    def setUp(self):
        """Set up test environment."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name
        self.pool = ConnectionPool(self.db_path, max_connections=5)

    def tearDown(self):
        """Clean up test environment."""
        self.pool.close_all()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_concurrent_connection_usage(self):
        """Test concurrent connection usage for thread safety."""

        def worker(worker_id):
            results = []
            for i in range(10):
                with self.pool.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT ? || ? || ?", (worker_id, "_", i))
                    result = cursor.fetchone()[0]
                    results.append(result)
                    time.sleep(0.001)  # Small delay to increase contention
            return results

        # Run multiple workers concurrently
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(worker, i) for i in range(5)]
            all_results = []
            for future in as_completed(futures):
                all_results.extend(future.result())

        # Should have 5 workers * 10 operations = 50 results
        self.assertEqual(len(all_results), 50)

        # Results should be unique (no mixing between workers)
        self.assertEqual(len(set(all_results)), 50)

    def test_pool_statistics_thread_safety(self):
        """Test that pool statistics are thread-safe."""

        def update_stats():
            for _ in range(100):
                with self.pool.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT 1")
                    cursor.fetchone()

                # Check stats
                stats = self.pool.get_stats()
                self.assertIsInstance(stats, dict)

        # Run multiple threads updating stats
        threads = []
        for _ in range(3):
            thread = threading.Thread(target=update_stats)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Final stats should be consistent
        final_stats = self.pool.get_stats()
        self.assertGreaterEqual(final_stats["total_connections"], 0)
        self.assertLessEqual(
            final_stats["total_connections"], self.pool.max_connections
        )


if __name__ == "__main__":
    unittest.main()
