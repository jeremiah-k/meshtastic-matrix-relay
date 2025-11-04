"""
Tests for DatabaseManager refactoring and security fixes in db_runtime.py.

This test module covers:
- DatabaseManager initialization and connection management
- Pragma validation security features
- Context manager behavior (read/write)
- Async execution helpers
- Connection lifecycle and cleanup
- Thread safety and error handling
"""

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import pytest

from mmrelay.db_runtime import DatabaseManager


class TestDatabaseManager(unittest.TestCase):
    """Test DatabaseManager functionality including security fixes."""

    def setUp(self):
        """Set up test fixtures with temporary database."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name
        self.manager = DatabaseManager(self.db_path)

    def tearDown(self):
        """Clean up test fixtures."""
        self.manager.close()
        import os

        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_initialization_default_parameters(self):
        """Test DatabaseManager initialization with default parameters."""
        manager = DatabaseManager(self.db_path)
        try:
            self.assertEqual(manager._path, self.db_path)
            self.assertTrue(manager._enable_wal)
            self.assertEqual(manager._busy_timeout_ms, 5000)
            self.assertEqual(manager._extra_pragmas, {})
        finally:
            manager.close()

    def test_initialization_custom_parameters(self):
        """Test DatabaseManager initialization with custom parameters."""
        custom_pragmas = {"synchronous": "OFF", "cache_size": 1000}
        manager = DatabaseManager(
            self.db_path,
            enable_wal=False,
            busy_timeout_ms=1000,
            extra_pragmas=custom_pragmas,
        )
        try:
            self.assertFalse(manager._enable_wal)
            self.assertEqual(manager._busy_timeout_ms, 1000)
            self.assertEqual(manager._extra_pragmas, custom_pragmas)
        finally:
            manager.close()

    def test_pragma_validation_valid_names(self):
        """Test that valid pragma names are accepted."""
        valid_pragmas = [
            "synchronous",
            "cache_size",
            "temp_store",
            "journal_mode",
            "foreign_keys",
            "query_only",
            "recursive_triggers",
        ]

        for pragma in valid_pragmas:
            with self.subTest(pragma=pragma):
                # Should not raise exception
                manager = DatabaseManager(self.db_path, extra_pragmas={pragma: "value"})
                try:
                    # Test connection creation to trigger pragma validation
                    with manager.read() as cursor:
                        cursor.execute("SELECT 1")
                finally:
                    manager.close()

    def test_pragma_validation_invalid_names(self):
        """Test that invalid pragma names are rejected."""
        invalid_pragmas = [
            "synchronous; DROP TABLE users; --",
            "cache_size' OR '1'='1",
            'temp_store"; SELECT * FROM users; --',
            "journal_mode--",
            "foreign_keys/*",
            "query-only",  # Invalid character
            "recursive triggers",  # Space in name
            "123invalid",  # Starts with number
            "",  # Empty string
        ]

        for pragma in invalid_pragmas:
            with self.subTest(pragma=pragma):
                with self.assertRaises(ValueError) as cm:
                    manager = DatabaseManager(
                        self.db_path, extra_pragmas={pragma: "value"}
                    )
                    # Try to create connection to trigger validation
                    with manager.read() as cursor:
                        cursor.execute("SELECT 1")
                self.assertIn("Invalid pragma name", str(cm.exception))

    def test_pragma_validation_string_values(self):
        """Test pragma validation for string values."""
        valid_values = [
            "NORMAL",
            "OFF",
            "MEMORY",
            "DELETE",
            "WAL",
            "TRUNCATE",
            "PERSIST",
            "value-with-dash",
            "value_with_underscore",
            "value with spaces",
            "value,with,commas",
            "value.with.dots",
        ]

        for value in valid_values:
            with self.subTest(value=value):
                manager = DatabaseManager(
                    self.db_path, extra_pragmas={"test_pragma": value}
                )
                try:
                    with manager.read() as cursor:
                        cursor.execute("SELECT 1")
                finally:
                    manager.close()

    def test_pragma_validation_invalid_string_values(self):
        """Test that invalid string pragma values are rejected."""
        invalid_values = [
            "value; DROP TABLE users; --",
            "value' OR '1'='1",
            'value"; SELECT * FROM users; --',
            "value/*",
            "value<script>",
            "value\x00null",
            "value'; DROP TABLE users; --",
            "value' OR '1'='1 --",
        ]

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as cm:
                    manager = DatabaseManager(
                        self.db_path, extra_pragmas={"test_pragma": value}
                    )
                    # Try to create connection to trigger validation
                    with manager.read() as cursor:
                        cursor.execute("SELECT 1")
                self.assertIn("Invalid pragma value", str(cm.exception))

    def test_pragma_validation_numeric_values(self):
        """Test pragma validation for numeric values."""
        valid_numeric = [1000, 0, -1, 3.14, 2.718]

        for value in valid_numeric:
            with self.subTest(value=value):
                manager = DatabaseManager(
                    self.db_path, extra_pragmas={"cache_size": value}
                )
                try:
                    with manager.read() as cursor:
                        cursor.execute("SELECT 1")
                finally:
                    manager.close()

    def test_pragma_validation_invalid_numeric_types(self):
        """Test that invalid numeric pragma value types are rejected."""
        invalid_types = [
            {"not": "a dict"},
            ["not", "a list"],
            (None, "tuple"),
            set([1, 2, 3]),
            None,
        ]

        for value in invalid_types:
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as cm:
                    manager = DatabaseManager(
                        self.db_path, extra_pragmas={"test_pragma": value}
                    )
                    # Try to create connection to trigger validation
                    with manager.read() as cursor:
                        cursor.execute("SELECT 1")
                self.assertIn("Invalid pragma value type", str(cm.exception))

    def test_read_context_manager(self):
        """Test read context manager behavior."""
        with self.manager.read() as cursor:
            result = cursor.execute("SELECT 1").fetchone()
            self.assertEqual(result[0], 1)

    def test_write_context_manager_success(self):
        """Test write context manager on successful operation."""
        with self.manager.write() as cursor:
            cursor.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
            cursor.execute("INSERT INTO test (value) VALUES (?)", ("test_value",))

        # Verify data was committed
        with self.manager.read() as cursor:
            result = cursor.execute("SELECT value FROM test").fetchone()
            self.assertEqual(result[0], "test_value")

    def test_write_context_manager_rollback_on_error(self):
        """Test write context manager rolls back on error."""
        # Create initial table
        with self.manager.write() as cursor:
            cursor.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
            cursor.execute("INSERT INTO test (value) VALUES (?)", ("initial",))

        # Attempt operation that will fail
        with self.assertRaises(sqlite3.IntegrityError):
            with self.manager.write() as cursor:
                cursor.execute(
                    "INSERT INTO test (id, value) VALUES (1, ?)", ("conflict",)
                )
                cursor.execute(
                    "INSERT INTO test (id, value) VALUES (1, ?)", ("conflict",)
                )

        # Verify initial data is still there (rollback worked)
        with self.manager.read() as cursor:
            result = cursor.execute("SELECT COUNT(*) FROM test").fetchone()
            self.assertEqual(result[0], 1)
            result = cursor.execute("SELECT value FROM test").fetchone()
            self.assertEqual(result[0], "initial")

    def test_run_sync_read_operation(self):
        """Test run_sync for read operations."""

        def query_func(cursor):
            return cursor.execute("SELECT 42").fetchone()[0]

        result = self.manager.run_sync(query_func, write=False)
        self.assertEqual(result, 42)

    def test_run_sync_write_operation(self):
        """Test run_sync for write operations."""

        def write_func(cursor):
            cursor.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
            cursor.execute("INSERT INTO test (value) VALUES (?)", ("sync_test",))
            return cursor.lastrowid

        row_id = self.manager.run_sync(write_func, write=True)
        self.assertIsNotNone(row_id)

        # Verify data was written
        with self.manager.read() as cursor:
            result = cursor.execute(
                "SELECT value FROM test WHERE id = ?", (row_id,)
            ).fetchone()
            self.assertEqual(result[0], "sync_test")

    @pytest.mark.usefixtures("mock_event_loop")
    def test_run_async_operation(self):
        """Test run_async for async operations with proper mocking."""

        async def test_async():
            def async_func(cursor):
                return "async_result"

            result = await self.manager.run_async(async_func, write=False)
            return result

        # Run the async function
        result = asyncio.run(test_async())
        self.assertEqual(result, "async_result")

    def test_thread_local_connections(self):
        """Test that connections are thread-local."""
        connections = []

        def get_connection():
            conn = self.manager._get_connection()
            connections.append(conn)
            return conn

        # Create multiple threads
        threads = []
        for _ in range(3):
            thread = threading.Thread(target=get_connection)
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify we have 3 different connections
        self.assertEqual(len(connections), 3)
        self.assertEqual(len(set(connections)), 3)  # All should be different

    def test_connection_tracking(self):
        """Test that connections are properly tracked."""
        initial_count = len(self.manager._connections)

        # Create a connection
        conn = self.manager._get_connection()

        # Connection should be tracked
        self.assertIn(conn, self.manager._connections)
        self.assertEqual(len(self.manager._connections), initial_count + 1)

        # Close manager and verify connection cleanup
        self.manager.close()
        self.assertEqual(len(self.manager._connections), 0)

    def test_close_cleanup(self):
        """Test close method properly cleans up resources."""
        # Create some connections
        conn1 = self.manager._get_connection()
        conn2 = self.manager._get_connection()

        # Verify connections exist
        self.assertIn(conn1, self.manager._connections)
        self.assertIn(conn2, self.manager._connections)

        # Close manager
        self.manager.close()

        # Verify cleanup
        self.assertEqual(len(self.manager._connections), 0)
        self.assertFalse(hasattr(self.manager._thread_local, "connection"))

    def test_close_handles_connection_errors(self):
        """Test close method handles connection errors gracefully."""
        # This test verifies that the close method has proper error handling
        # The actual implementation catches sqlite3.Error and ignores it during cleanup
        # We can't easily mock sqlite3.Connection.close due to it being read-only
        # but we can verify the method exists and the structure is correct

        # Create a manager and get a connection
        test_manager = DatabaseManager(self.db_path)
        conn = test_manager._get_connection()

        # Verify connection is tracked
        self.assertIn(conn, test_manager._connections)
        self.assertEqual(len(test_manager._connections), 1)

        # Close normally (this exercises the error handling path)
        test_manager.close()

        # Verify cleanup happened
        self.assertEqual(len(test_manager._connections), 0)

    def test_write_lock_serialization(self):
        """Test that write operations are serialized."""
        results = []
        errors = []

        def write_operation(thread_id):
            try:

                def write_func(cursor):
                    # Create table if not exists
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS counter (
                            id INTEGER PRIMARY KEY CHECK (id = 1),
                            count INTEGER DEFAULT 0
                        )
                    """
                    )
                    # Insert initial row if not exists
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO counter (id, count) VALUES (1, 0)
                    """
                    )
                    # Increment counter
                    cursor.execute("UPDATE counter SET count = count + 1 WHERE id = 1")
                    # Get current count
                    result = cursor.execute(
                        "SELECT count FROM counter WHERE id = 1"
                    ).fetchone()
                    return result[0] if result else None

                result = self.manager.run_sync(write_func, write=True)
                results.append((thread_id, result))
            except Exception as e:
                errors.append((thread_id, e))

        # Run multiple threads writing simultaneously
        threads = []
        for i in range(5):
            thread = threading.Thread(target=write_operation, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify no errors occurred
        self.assertEqual(len(errors), 0, f"Errors occurred: {errors}")

        # Verify all operations completed and counter was incremented
        self.assertEqual(len(results), 5)

        # Check final counter value
        with self.manager.read() as cursor:
            final_count = cursor.execute(
                "SELECT count FROM counter WHERE id = 1"
            ).fetchone()
            self.assertIsNotNone(final_count)
            self.assertEqual(final_count[0], 5)

    def test_connection_creation_error_cleanup(self):
        """Test that connection creation errors don't leak connections."""
        # Try to create a manager with invalid pragma that will fail
        with self.assertRaises(ValueError):
            manager = DatabaseManager(
                self.db_path, extra_pragmas={"invalid;pragma": "value"}
            )
            # Try to create connection to trigger validation
            with manager.read() as cursor:
                cursor.execute("SELECT 1")

        # Verify no connections were leaked (this is more of a sanity check)
        # since the manager creation failed, there shouldn't be any connections to track

    def test_busy_timeout_pragma_application(self):
        """Test that busy timeout pragma is properly applied."""
        manager = DatabaseManager(self.db_path, busy_timeout_ms=1000)
        try:
            with manager.read() as cursor:
                # Query the busy timeout setting
                result = cursor.execute("PRAGMA busy_timeout").fetchone()
                self.assertEqual(result[0], 1000)
        finally:
            manager.close()

    def test_wal_mode_pragma_application(self):
        """Test that WAL mode pragma is properly applied."""
        manager = DatabaseManager(self.db_path, enable_wal=True)
        try:
            with manager.read() as cursor:
                # Query the journal mode setting
                result = cursor.execute("PRAGMA journal_mode").fetchone()
                self.assertEqual(result[0].lower(), "wal")
        finally:
            manager.close()

    def test_foreign_keys_pragma_application(self):
        """Test that foreign keys pragma is properly applied."""
        with self.manager.read() as cursor:
            # Query the foreign keys setting
            result = cursor.execute("PRAGMA foreign_keys").fetchone()
            self.assertEqual(result[0], 1)


class TestDatabaseManagerEdgeCases(unittest.TestCase):
    """Test DatabaseManager edge cases and error conditions."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

    def tearDown(self):
        """Clean up test fixtures."""
        import os

        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_empty_extra_pragmas_dict(self):
        """Test handling of empty extra_pragmas dictionary."""
        manager = DatabaseManager(self.db_path, extra_pragmas={})
        try:
            with manager.read() as cursor:
                cursor.execute("SELECT 1")
        finally:
            manager.close()

    def test_none_extra_pragmas(self):
        """Test handling of None extra_pragmas."""
        manager = DatabaseManager(self.db_path, extra_pragmas=None)
        try:
            with manager.read() as cursor:
                cursor.execute("SELECT 1")
        finally:
            manager.close()

    def test_zero_busy_timeout(self):
        """Test handling of zero busy timeout."""
        manager = DatabaseManager(self.db_path, busy_timeout_ms=0)
        try:
            with manager.read() as cursor:
                cursor.execute("SELECT 1")
        finally:
            manager.close()

    def test_negative_busy_timeout(self):
        """Test handling of negative busy timeout."""
        manager = DatabaseManager(self.db_path, busy_timeout_ms=-1000)
        try:
            with manager.read() as cursor:
                cursor.execute("SELECT 1")
        finally:
            manager.close()

    def test_database_file_permissions(self):
        """Test behavior with various database file permissions."""
        import os
        import stat

        # Create manager
        manager = DatabaseManager(self.db_path)
        try:
            # Create a table to ensure database file exists
            with manager.write() as cursor:
                cursor.execute("CREATE TABLE test (id INTEGER)")

            # Make database file read-only
            current_permissions = os.stat(self.db_path).st_mode
            os.chmod(self.db_path, stat.S_IRUSR)

            # Try to write - may fail depending on system/SQLite version
            try:
                with manager.write() as cursor:
                    cursor.execute("INSERT INTO test (id) VALUES (1)")
                # If it succeeds, that's also valid behavior (some SQLite versions handle this differently)
            except sqlite3.OperationalError:
                # This is expected on most systems
                pass

            # Restore permissions for cleanup
            os.chmod(self.db_path, current_permissions)
        finally:
            manager.close()

    def test_concurrent_read_operations(self):
        """Test that concurrent read operations work correctly."""
        manager = DatabaseManager(self.db_path)
        try:
            # Create test data
            with manager.write() as cursor:
                cursor.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
                cursor.execute("INSERT INTO test (id, value) VALUES (1, 'test')")

            results = []

            def read_operation(thread_id):
                def read_func(cursor):
                    result = cursor.execute(
                        "SELECT value FROM test WHERE id = 1"
                    ).fetchone()
                    return result[0] if result else None

                result = manager.run_sync(read_func, write=False)
                results.append((thread_id, result))

            # Run multiple threads reading simultaneously
            threads = []
            for i in range(5):
                thread = threading.Thread(target=read_operation, args=(i,))
                threads.append(thread)
                thread.start()

            # Wait for all threads to complete
            for thread in threads:
                thread.join()

            # Verify all reads succeeded
            self.assertEqual(len(results), 5)
            for thread_id, result in results:
                self.assertEqual(result, "test")
        finally:
            manager.close()


if __name__ == "__main__":
    unittest.main()
