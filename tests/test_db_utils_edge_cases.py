#!/usr/bin/env python3
"""
Test suite for Database utilities edge cases and error handling in MMRelay.

Tests edge cases and error handling including:
- Database connection failures
- Corrupted database handling
- Concurrent access issues
- File permission errors
- Database migration edge cases
- Transaction rollback scenarios
- Memory constraints and large datasets
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.db_utils import (
    clear_db_path_cache,
    delete_plugin_data,
    get_db_path,
    get_longname,
    get_message_map_by_matrix_event_id,
    get_message_map_by_meshtastic_id,
    get_plugin_data_for_node,
    get_shortname,
    initialize_database,
    prune_message_map,
    save_longname,
    save_shortname,
    store_message_map,
    store_plugin_data,
    update_longnames,
    update_shortnames,
    wipe_message_map,
)


class TestDBUtilsEdgeCases(unittest.TestCase):
    """Test cases for Database utilities edge cases and error handling."""

    def setUp(self):
        """
        Prepares the test environment before each test by clearing the cached database path and resetting the global configuration.
        """
        # Clear any cached database path
        clear_db_path_cache()
        # Reset global config
        import mmrelay.db_utils

        mmrelay.db_utils.config = None

    def tearDown(self):
        """
        Cleans up test environment after each test by clearing the cached database path.
        """
        clear_db_path_cache()

    def test_get_db_path_permission_error(self):
        """
        Test that get_db_path returns a valid database path even if directory creation fails due to permission errors.
        """
        with patch("mmrelay.db_utils.get_data_dir", return_value="/readonly/data"):
            with patch("os.makedirs", side_effect=PermissionError("Permission denied")):
                # Should still return a path even if directory creation fails
                result = get_db_path()
                self.assertIn("meshtastic.sqlite", result)

    def test_get_db_path_custom_config_invalid_path(self):
        """
        Test that get_db_path returns a string path when a custom config specifies an invalid database path and directory creation fails.

        Simulates an OSError during directory creation and verifies that get_db_path handles the error gracefully by still returning a string path.
        """
        import mmrelay.db_utils

        mmrelay.db_utils.config = {
            "database": {"path": "/nonexistent/invalid/path/db.sqlite"}
        }

        with patch("os.makedirs", side_effect=OSError("Cannot create directory")):
            # Should handle the error gracefully
            result = get_db_path()
            self.assertIsInstance(result, str)

    def test_initialize_database_connection_failure(self):
        """
        Test that initialize_database raises an exception and logs an error when the database connection fails.
        """
        with patch("sqlite3.connect", side_effect=sqlite3.Error("Connection failed")):
            with patch("mmrelay.db_utils.logger") as mock_logger:
                # Should raise exception on connection failure (fail fast)
                with self.assertRaises(sqlite3.Error):
                    initialize_database()
                mock_logger.exception.assert_called()

    def test_initialize_database_corrupted_database(self):
        """
        Test that initialize_database raises sqlite3.DatabaseError when attempting to initialize a corrupted database file.
        """
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as temp_db:
            # Write invalid data to simulate corruption
            temp_db.write(b"corrupted database content")
            temp_db_path = temp_db.name

        try:
            with patch("mmrelay.db_utils.get_db_path", return_value=temp_db_path):
                with patch("mmrelay.db_utils.logger"):
                    # Should raise exception on corrupted database (fail fast)
                    with self.assertRaises(sqlite3.DatabaseError):
                        initialize_database()
        finally:
            os.unlink(temp_db_path)

    def test_save_longname_database_locked(self):
        """
        Test that save_longname handles a locked database by simulating a database lock error.

        Verifies that the function does not crash or raise unhandled exceptions when an OperationalError indicating "database is locked" occurs during execution.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.execute.side_effect = (
                sqlite3.OperationalError("database is locked")
            )
            mock_connect.return_value.__enter__.return_value = mock_conn

            # Should handle database lock gracefully
            save_longname("test_id", "test_name")

    def test_save_shortname_constraint_violation(self):
        """
        Test that save_shortname handles database constraint violations gracefully.

        Simulates a constraint violation during the save_shortname operation and verifies that the function does not raise an exception.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.execute.side_effect = sqlite3.IntegrityError(
                "constraint violation"
            )
            mock_connect.return_value.__enter__.return_value = mock_conn

            # Should handle constraint violation gracefully
            save_shortname("test_id", "test_name")

    def test_get_longname_connection_error(self):
        """
        Test that get_longname returns None when a database connection error occurs.
        """
        with patch("sqlite3.connect", side_effect=sqlite3.Error("Connection failed")):
            result = get_longname("test_id")
            self.assertIsNone(result)

    def test_get_shortname_table_not_exists(self):
        """
        Test that get_shortname returns None when the database table does not exist.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.execute.side_effect = (
                sqlite3.OperationalError("no such table")
            )
            mock_connect.return_value.__enter__.return_value = mock_conn

            result = get_shortname("test_id")
            self.assertIsNone(result)

    def test_store_message_map_disk_full(self):
        """
        Test that store_message_map handles disk full (disk I/O error) conditions gracefully.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.execute.side_effect = (
                sqlite3.OperationalError("disk I/O error")
            )
            mock_connect.return_value.__enter__.return_value = mock_conn

            # Should handle disk full error gracefully
            store_message_map("mesh_id", "matrix_id", "room_id", "text")

    def test_get_message_map_by_meshtastic_id_malformed_data(self):
        """
        Test that get_message_map_by_meshtastic_id returns None when the database returns malformed or incomplete data.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = mock_conn.cursor.return_value
            # Return malformed data (missing columns)
            mock_cursor.fetchone.return_value = ("incomplete_data",)
            mock_connect.return_value.__enter__.return_value = mock_conn

            result = get_message_map_by_meshtastic_id("test_id")
            # Should handle malformed data gracefully by returning None
            self.assertIsNone(result)

    def test_get_message_map_by_matrix_event_id_unicode_error(self):
        """
        Test that get_message_map_by_matrix_event_id returns None when a UnicodeDecodeError occurs during database query execution.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = mock_conn.cursor.return_value
            # Simulate unicode error
            mock_cursor.execute.side_effect = UnicodeDecodeError(
                "utf-8", b"", 0, 1, "invalid"
            )
            mock_connect.return_value.__enter__.return_value = mock_conn

            result = get_message_map_by_matrix_event_id("test_id")
            self.assertIsNone(result)

    def test_prune_message_map_large_dataset(self):
        """
        Test that prune_message_map can handle pruning operations when the database contains a very large number of records.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = mock_conn.cursor.return_value
            # Simulate large dataset by making count very high
            mock_cursor.fetchone.return_value = (1000000,)
            mock_connect.return_value.__enter__.return_value = mock_conn

            # Should handle large datasets
            prune_message_map(100)

    def test_wipe_message_map_transaction_rollback(self):
        """
        Test that wipe_message_map properly handles transaction rollback when a database error occurs during execution.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.execute.side_effect = [
                None,
                sqlite3.Error("Transaction failed"),
            ]
            mock_connect.return_value.__enter__.return_value = mock_conn

            # Should handle transaction rollback
            wipe_message_map()

    def test_store_plugin_data_concurrent_access(self):
        """
        Test that store_plugin_data handles database locking due to concurrent access without crashing.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.execute.side_effect = (
                sqlite3.OperationalError("database is locked")
            )
            mock_connect.return_value.__enter__.return_value = mock_conn

            # Should handle concurrent access gracefully
            store_plugin_data("test_plugin", "test_node", {"key": "value"})

    def test_get_plugin_data_json_decode_error(self):
        """
        Test that get_plugin_data_for_node returns an empty list when JSON decoding of plugin data fails.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = mock_conn.cursor.return_value
            # Return invalid JSON
            mock_cursor.fetchone.return_value = ("invalid json {",)
            mock_connect.return_value.__enter__.return_value = mock_conn

            result = get_plugin_data_for_node("test_plugin", "test_node")
            self.assertEqual(result, [])

    def test_get_plugin_data_for_node_memory_error(self):
        """
        Test that get_plugin_data_for_node returns an empty list when a MemoryError occurs during data retrieval.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.fetchone.side_effect = MemoryError(
                "Out of memory"
            )
            mock_connect.return_value.__enter__.return_value = mock_conn

            result = get_plugin_data_for_node("test_plugin", "test_node")
            self.assertEqual(result, [])

    def test_delete_plugin_data_foreign_key_constraint(self):
        """
        Test that delete_plugin_data handles foreign key constraint violations gracefully when deleting plugin data.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.execute.side_effect = sqlite3.IntegrityError(
                "foreign key constraint"
            )
            mock_connect.return_value.__enter__.return_value = mock_conn

            # Should handle foreign key constraint gracefully
            delete_plugin_data("test_plugin", "test_node")

    def test_update_longnames_empty_nodes(self):
        """
        Test that update_longnames handles None and empty list inputs without error.
        """
        # Should handle None gracefully
        update_longnames(None)

        # Should handle empty list gracefully
        update_longnames([])

    def test_update_shortnames_malformed_node_data(self):
        """
        Test that update_shortnames handles malformed node data without raising exceptions.

        This test verifies that update_shortnames can process node data with missing or None fields gracefully, ensuring robustness against incomplete or invalid input.
        """
        malformed_nodes = MagicMock()
        malformed_nodes.values.return_value = [
            {"user": {}},  # Missing 'id' in user
            {"user": {"id": "test_id"}},  # Missing 'shortName'
            {"user": {"id": "test_id2", "shortName": None}},  # None shortName
        ]

        # Should handle malformed data gracefully
        update_shortnames(malformed_nodes)

    def test_database_path_caching_race_condition(self):
        """
        Verify that database path caching remains consistent and robust when the cache is cleared between calls, simulating a race condition.
        """
        import mmrelay.db_utils

        # Simulate race condition by clearing cache between calls
        def side_effect_clear_cache(*args, **kwargs):
            """
            Clears the cached database path and returns a test database path.

            Returns:
                str: The test database path "/test/path/meshtastic.sqlite".
            """
            mmrelay.db_utils._cached_db_path = None
            return "/test/path/meshtastic.sqlite"

        with patch(
            "mmrelay.db_utils.get_data_dir", side_effect=side_effect_clear_cache
        ):
            path1 = get_db_path()
            path2 = get_db_path()
            # Should handle race condition gracefully
            self.assertIsInstance(path1, str)
            self.assertIsInstance(path2, str)

    def test_database_initialization_partial_failure(self):
        """
        Test that `initialize_database` raises an exception if a table creation fails during initialization.

        Simulates partial failure by causing one table creation to raise an error, and asserts that the function fails fast by raising the exception.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = mock_conn.cursor.return_value
            # First table creation succeeds, second fails, rest succeed
            mock_cursor.execute.side_effect = [
                None,  # longnames table succeeds
                sqlite3.Error("Table creation failed"),  # shortnames table fails
                None,  # plugin_data table succeeds
                None,  # message_map table succeeds
                sqlite3.OperationalError(
                    "Column already exists"
                ),  # ALTER TABLE (expected)
            ]
            mock_connect.return_value.__enter__.return_value = mock_conn

            # Should raise exception on table creation failure (fail fast)
            with self.assertRaises(sqlite3.Error):
                initialize_database()


if __name__ == "__main__":
    unittest.main()
