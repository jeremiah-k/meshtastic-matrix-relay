#!/usr/bin/env python3
"""
Test suite for database utilities in MMRelay.

Tests the SQLite database operations including:
- Database initialization and schema creation
- Node name storage and retrieval (longnames/shortnames)
- Plugin data storage and retrieval
- Message mapping for Matrix/Meshtastic correlation
- Database path resolution and caching
- Configuration-based database paths
"""

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.db_utils import (
    _parse_bool,
    _parse_int,
    _reset_db_manager,
    async_prune_message_map,
    async_store_message_map,
    clear_db_path_cache,
    delete_plugin_data,
    get_db_path,
    get_longname,
    get_message_map_by_matrix_event_id,
    get_message_map_by_meshtastic_id,
    get_plugin_data,
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


class TestDbUtils(unittest.TestCase):
    """Test cases for database utilities."""

    def setUp(self):
        """
        Prepare a temporary test environment by creating a unique directory and database file, clearing cached database paths, and patching the configuration to use the test database.
        """
        # Create a temporary directory for test database
        self.test_dir = tempfile.mkdtemp()
        self.test_db_path = os.path.join(self.test_dir, "test_meshtastic.sqlite")

        # Clear any cached database path
        clear_db_path_cache()

        # Mock the config to use our test database
        self.mock_config = {"database": {"path": self.test_db_path}}

        # Patch the config in db_utils
        import mmrelay.db_utils

        mmrelay.db_utils.config = self.mock_config

    def tearDown(self):
        """
        Cleans up the test environment by clearing the database path cache and removing temporary files and directories created during the test.
        """
        # Clear cache after each test
        clear_db_path_cache()

        # Clean up temporary files and directory
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_get_db_path_with_config(self):
        """
        Test that get_db_path() returns the database path specified in the configuration.
        """
        path = get_db_path()
        self.assertEqual(path, self.test_db_path)

    def test_get_db_path_caching(self):
        """
        Test that the database path returned by get_db_path() is cached after the first retrieval.

        Verifies that repeated calls to get_db_path() return the same path and match the expected test database path.
        """
        # First call should resolve and cache
        path1 = get_db_path()
        path2 = get_db_path()
        self.assertEqual(path1, path2)
        self.assertEqual(path1, self.test_db_path)

    @patch("mmrelay.db_utils.get_data_dir")
    def test_get_db_path_default(self, mock_get_data_dir):
        """
        Test that `get_db_path()` returns the default database path in the absence of configuration.

        Mocks the data directory to verify that the default path is constructed correctly when no configuration is set.
        """
        # Clear config to test default behavior
        import mmrelay.db_utils

        mmrelay.db_utils.config = None
        clear_db_path_cache()

        import os
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            mock_get_data_dir.return_value = temp_dir
            path = get_db_path()
            expected_path = os.path.join(temp_dir, "meshtastic.sqlite")
            self.assertEqual(path, expected_path)

    def test_get_db_path_legacy_config(self):
        """
        Test that get_db_path() returns the correct database path when using a legacy configuration with the 'db.path' key.
        """
        # Use legacy db.path format
        legacy_config = {"db": {"path": self.test_db_path}}

        import mmrelay.db_utils

        mmrelay.db_utils.config = legacy_config
        clear_db_path_cache()

        path = get_db_path()
        self.assertEqual(path, self.test_db_path)

    def test_initialize_database(self):
        """
        Verify that the database is initialized with the correct schema and required tables.

        Ensures that the database file is created, all expected tables exist, and the `message_map` table includes the `meshtastic_meshnet` column.
        """
        initialize_database()

        # Verify database file was created
        self.assertTrue(os.path.exists(self.test_db_path))

        # Verify tables were created
        with sqlite3.connect(self.test_db_path) as conn:
            cursor = conn.cursor()

            # Check longnames table
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='longnames'"
            )
            self.assertIsNotNone(cursor.fetchone())

            # Check shortnames table
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='shortnames'"
            )
            self.assertIsNotNone(cursor.fetchone())

            # Check plugin_data table
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='plugin_data'"
            )
            self.assertIsNotNone(cursor.fetchone())

            # Check message_map table
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='message_map'"
            )
            self.assertIsNotNone(cursor.fetchone())

            # Verify message_map has meshtastic_meshnet column
            cursor.execute("PRAGMA table_info(message_map)")
            columns = [row[1] for row in cursor.fetchall()]
            self.assertIn("meshtastic_meshnet", columns)

    def test_longname_operations(self):
        """
        Tests saving and retrieving longnames by Meshtastic ID, including handling of non-existent entries.
        """
        initialize_database()

        # Test saving and retrieving longname
        meshtastic_id = "!12345678"
        longname = "Test User"

        save_longname(meshtastic_id, longname)
        retrieved_longname = get_longname(meshtastic_id)

        self.assertEqual(retrieved_longname, longname)

        # Test non-existent longname
        non_existent = get_longname("!nonexistent")
        self.assertIsNone(non_existent)

    def test_shortname_operations(self):
        """
        Test saving and retrieving shortnames by Meshtastic ID, including handling of non-existent entries.
        """
        initialize_database()

        # Test saving and retrieving shortname
        meshtastic_id = "!12345678"
        shortname = "TU"

        save_shortname(meshtastic_id, shortname)
        retrieved_shortname = get_shortname(meshtastic_id)

        self.assertEqual(retrieved_shortname, shortname)

        # Test non-existent shortname
        non_existent = get_shortname("!nonexistent")
        self.assertIsNone(non_existent)

    def test_update_longnames(self):
        """
        Tests that bulk updating of longnames from a dictionary of nodes correctly stores the longnames for each Meshtastic ID.
        """
        initialize_database()

        # Mock nodes data
        nodes = {
            "!12345678": {"user": {"id": "!12345678", "longName": "Alice Smith"}},
            "!87654321": {"user": {"id": "!87654321", "longName": "Bob Jones"}},
        }

        update_longnames(nodes)

        # Verify longnames were stored
        self.assertEqual(get_longname("!12345678"), "Alice Smith")
        self.assertEqual(get_longname("!87654321"), "Bob Jones")

    def test_update_shortnames(self):
        """
        Test that bulk updating of shortnames from a nodes dictionary correctly stores shortnames for each Meshtastic ID.
        """
        initialize_database()

        # Mock nodes data
        nodes = {
            "!12345678": {"user": {"id": "!12345678", "shortName": "AS"}},
            "!87654321": {"user": {"id": "!87654321", "shortName": "BJ"}},
        }

        update_shortnames(nodes)

        # Verify shortnames were stored
        self.assertEqual(get_shortname("!12345678"), "AS")
        self.assertEqual(get_shortname("!87654321"), "BJ")

    def test_plugin_data_operations(self):
        """
        Test storing, retrieving, and deleting plugin data for specific nodes and plugins in the database.

        Verifies that plugin data can be saved for a given plugin and node, retrieved individually or in bulk, and deleted, ensuring correct data persistence and removal.
        """
        initialize_database()

        plugin_name = "test_plugin"
        meshtastic_id = "!12345678"
        test_data = {"temperature": 25.5, "humidity": 60}

        # Store plugin data
        store_plugin_data(plugin_name, meshtastic_id, test_data)

        # Retrieve plugin data for specific node
        retrieved_data = get_plugin_data_for_node(plugin_name, meshtastic_id)
        self.assertEqual(retrieved_data, test_data)

        # Retrieve all plugin data
        all_data = get_plugin_data(plugin_name)
        self.assertEqual(len(all_data), 1)
        self.assertEqual(json.loads(all_data[0][0]), test_data)

        # Delete plugin data
        delete_plugin_data(plugin_name, meshtastic_id)
        retrieved_after_delete = get_plugin_data_for_node(plugin_name, meshtastic_id)
        self.assertEqual(retrieved_after_delete, [])

    def test_message_map_operations(self):
        """
        Verifies storing and retrieving message map entries by Meshtastic ID and Matrix event ID, ensuring all fields are correctly persisted and retrieved.
        """
        initialize_database()

        # Test data
        meshtastic_id = 12345
        matrix_event_id = "$event123:matrix.org"
        matrix_room_id = "!room123:matrix.org"
        meshtastic_text = "Hello from mesh"
        meshtastic_meshnet = "test_mesh"

        # Store message map
        store_message_map(
            meshtastic_id,
            matrix_event_id,
            matrix_room_id,
            meshtastic_text,
            meshtastic_meshnet,
        )

        # Retrieve by meshtastic_id
        result = get_message_map_by_meshtastic_id(meshtastic_id)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], matrix_event_id)
        self.assertEqual(result[1], matrix_room_id)
        self.assertEqual(result[2], meshtastic_text)
        self.assertEqual(result[3], meshtastic_meshnet)

        # Retrieve by matrix_event_id
        result = get_message_map_by_matrix_event_id(matrix_event_id)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], str(meshtastic_id))
        self.assertEqual(result[1], matrix_room_id)
        self.assertEqual(result[2], meshtastic_text)
        self.assertEqual(result[3], meshtastic_meshnet)

    def test_wipe_message_map(self):
        """
        Verifies that wiping the message map removes all entries from the database.

        This test initializes the database, inserts sample message map entries, confirms their existence, performs a wipe operation, and asserts that all entries have been deleted.
        """
        initialize_database()

        # Add some test data
        store_message_map(1, "$event1:matrix.org", "!room:matrix.org", "test1")
        store_message_map(2, "$event2:matrix.org", "!room:matrix.org", "test2")

        # Verify data exists
        self.assertIsNotNone(get_message_map_by_meshtastic_id(1))
        self.assertIsNotNone(get_message_map_by_meshtastic_id(2))

        # Wipe message map
        wipe_message_map()

        # Verify data is gone
        self.assertIsNone(get_message_map_by_meshtastic_id(1))
        self.assertIsNone(get_message_map_by_meshtastic_id(2))

    def test_prune_message_map(self):
        """
        Verify that pruning the message map retains only the specified number of most recent entries.

        This test inserts multiple message map entries, prunes the table to keep only the latest five, and asserts that only those entries remain.
        """
        initialize_database()

        # Add multiple entries
        for i in range(10):
            store_message_map(
                i, f"$event{i}:matrix.org", "!room:matrix.org", f"test{i}"
            )

        # Prune to keep only 5 entries
        prune_message_map(5)

        # Verify only recent entries remain
        with sqlite3.connect(self.test_db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM message_map")
            count = cursor.fetchone()[0]
            self.assertEqual(count, 5)

            # Verify the kept entries are the most recent ones
            cursor.execute("SELECT meshtastic_id FROM message_map ORDER BY rowid")
            kept_ids = [row[0] for row in cursor.fetchall()]
            self.assertEqual(kept_ids, [5, 6, 7, 8, 9])

    def test_database_manager_reuses_connection(self):
        """
        Ensure that the database manager reuses the same SQLite connection for multiple operations within the same thread.
        """
        clear_db_path_cache()
        with patch("sqlite3.connect", wraps=sqlite3.connect) as mock_connect:
            initialize_database()
            store_plugin_data("plugin", "nodeA", {"value": 1})
            store_plugin_data("plugin", "nodeB", {"value": 2})
            # Only the initial connection should be created; subsequent calls reuse it.
            self.assertEqual(mock_connect.call_count, 1)

    def test_async_store_and_prune_message_map(self):
        """
        Validate the async helpers for storing and pruning message map entries execute without blocking.
        """
        initialize_database()

        async def exercise():
            """
            Exercise message-map helpers by storing two entries then pruning to keep the most recent one.

            Stores two message map entries and then prunes the message map to a limit of 1 so that only the latest stored entry remains.
            """
            await async_store_message_map(
                "mesh1", "$event1:matrix.org", "!room:matrix.org", "text1"
            )
            await async_store_message_map(
                "mesh2", "$event2:matrix.org", "!room:matrix.org", "text2"
            )
            await async_prune_message_map(1)

        asyncio.run(exercise())

        # Oldest entry should have been pruned
        self.assertIsNone(get_message_map_by_meshtastic_id("mesh1"))
        latest = get_message_map_by_meshtastic_id("mesh2")
        self.assertIsNotNone(latest)
        self.assertEqual(latest[0], "$event2:matrix.org")

    def test_database_manager_keyboard_interrupt(self):
        """
        Test that DatabaseManager creation re-raises KeyboardInterrupt.

        This test verifies that KeyboardInterrupt exceptions are not caught
        by the fallback exception handler and are properly re-raised.
        """
        # Reset any existing database manager
        _reset_db_manager()
        clear_db_path_cache()

        # Configure a database path
        mock_config = {"database": {"path": self.test_db_path}}
        import mmrelay.db_utils

        mmrelay.db_utils.config = mock_config

        # Mock DatabaseManager to raise KeyboardInterrupt
        with patch(
            "mmrelay.db_utils.DatabaseManager",
            side_effect=KeyboardInterrupt("User interrupt"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                from mmrelay.db_utils import _get_db_manager

                _get_db_manager()

    def test_database_manager_system_exit(self):
        """
        Test that DatabaseManager creation re-raises SystemExit.

        This test verifies that SystemExit exceptions are not caught
        by the fallback exception handler and are properly re-raised.
        """
        # Reset any existing database manager
        _reset_db_manager()
        clear_db_path_cache()

        # Configure a database path
        mock_config = {"database": {"path": self.test_db_path}}
        import mmrelay.db_utils

        mmrelay.db_utils.config = mock_config

        # Mock DatabaseManager to raise SystemExit
        with patch(
            "mmrelay.db_utils.DatabaseManager",
            side_effect=SystemExit("System shutdown"),
        ):
            with self.assertRaises(SystemExit):
                from mmrelay.db_utils import _get_db_manager

                _get_db_manager()

    def test_get_db_path_directory_creation_error(self):
        """
        Test that get_db_path() handles OSError/PermissionError when creating directories gracefully.

        This test verifies that when directory creation fails, the function logs a warning
        but continues execution, returning the configured path.
        """
        # Clear cache to ensure fresh resolution
        clear_db_path_cache()

        # Configure a path in a non-existent directory
        invalid_db_path = "/nonexistent/invalid/path/test.db"
        mock_config = {"database": {"path": invalid_db_path}}

        import mmrelay.db_utils

        mmrelay.db_utils.config = mock_config

        # Mock os.makedirs to raise PermissionError
        with patch("os.makedirs", side_effect=PermissionError("Permission denied")):
            with patch("mmrelay.db_utils.logger") as mock_logger:
                path = get_db_path()
                self.assertEqual(path, invalid_db_path)
                mock_logger.warning.assert_called_once()

    def test_get_db_path_legacy_directory_creation_error(self):
        """
        Test that get_db_path() handles OSError/PermissionError when creating directories for legacy config.

        This test verifies the same error handling for the legacy 'db.path' configuration format.
        """
        # Clear cache to ensure fresh resolution
        clear_db_path_cache()

        # Configure a legacy path in a non-existent directory
        invalid_db_path = "/nonexistent/legacy/path/test.db"
        mock_config = {"db": {"path": invalid_db_path}}

        import mmrelay.db_utils

        mmrelay.db_utils.config = mock_config

        # Mock os.makedirs to raise OSError
        with patch("os.makedirs", side_effect=OSError("No space left on device")):
            with patch("mmrelay.db_utils.logger") as mock_logger:
                path = get_db_path()
                self.assertEqual(path, invalid_db_path)
                # Should have two warnings: one for directory creation failure, one for legacy config
                self.assertEqual(mock_logger.warning.call_count, 2)

    def test_get_db_path_data_directory_creation_error(self):
        """
        Test that get_db_path() handles OSError/PermissionError when creating the default data directory.

        This test verifies error handling when the default data directory cannot be created.
        """
        # Clear cache and remove any database config to force default path
        clear_db_path_cache()
        mock_config = {}

        import mmrelay.db_utils

        mmrelay.db_utils.config = mock_config

        # Mock get_data_dir and os.makedirs to raise PermissionError
        with patch("mmrelay.db_utils.get_data_dir", return_value="/nonexistent/data"):
            with patch("os.makedirs", side_effect=PermissionError("Permission denied")):
                with patch("mmrelay.db_utils.logger") as mock_logger:
                    path = get_db_path()
                    self.assertTrue(path.endswith("meshtastic.sqlite"))
                    mock_logger.warning.assert_called_once()

    def test_database_manager_config_change_fallback(self):
        """
        Test that DatabaseManager creation falls back to old manager on config change failure.

        This test verifies that when a configuration change causes DatabaseManager
        creation to fail, the system continues using the previous working manager.
        """
        # Clear cache and create initial database manager
        clear_db_path_cache()
        mock_config = {"database": {"path": self.test_db_path}}
        import mmrelay.db_utils

        mmrelay.db_utils.config = mock_config

        # Create initial manager
        from mmrelay.db_utils import _get_db_manager

        initial_manager = _get_db_manager()
        self.assertIsNotNone(initial_manager)

        # Change config to trigger manager recreation, but make it fail
        new_db_path = os.path.join(self.test_dir, "new_test.db")
        mock_config["database"]["path"] = new_db_path

        with patch(
            "mmrelay.db_utils.DatabaseManager",
            side_effect=RuntimeError("Invalid configuration"),
        ):
            with patch("mmrelay.db_utils.logger") as mock_logger:
                # Should return the same manager (fallback)
                fallback_manager = _get_db_manager()
                self.assertEqual(initial_manager, fallback_manager)
                mock_logger.exception.assert_called_once()

    def test_database_manager_first_time_failure(self):
        """
        Test that DatabaseManager creation raises exception on first-time initialization failure.

        This test verifies that when no previous manager exists and creation fails,
        the exception is properly raised (no fallback possible).
        """
        # Reset any existing database manager
        _reset_db_manager()
        clear_db_path_cache()

        # Configure a database path
        mock_config = {"database": {"path": self.test_db_path}}
        import mmrelay.db_utils

        mmrelay.db_utils.config = mock_config

        # Mock DatabaseManager to raise RuntimeError
        with patch(
            "mmrelay.db_utils.DatabaseManager",
            side_effect=RuntimeError("Cannot create database"),
        ):
            with self.assertRaises(RuntimeError):
                from mmrelay.db_utils import _get_db_manager

                _get_db_manager()

    def test_parse_bool_function(self):
        """
        Test the _parse_bool function with various inputs.

        This test verifies that the function correctly parses boolean values
        from different input types and formats.
        """

        # Test boolean inputs
        self.assertTrue(_parse_bool(True, False))
        self.assertFalse(_parse_bool(False, True))

        # Test string inputs - true values
        self.assertTrue(_parse_bool("1", False))
        self.assertTrue(_parse_bool("true", False))
        self.assertTrue(_parse_bool("TRUE", False))
        self.assertTrue(_parse_bool("yes", False))
        self.assertTrue(_parse_bool("YES", False))
        self.assertTrue(_parse_bool("on", False))
        self.assertTrue(_parse_bool("ON", False))

        # Test string inputs - false values
        self.assertFalse(_parse_bool("0", True))
        self.assertFalse(_parse_bool("false", True))
        self.assertFalse(_parse_bool("FALSE", True))
        self.assertFalse(_parse_bool("no", True))
        self.assertFalse(_parse_bool("NO", True))
        self.assertFalse(_parse_bool("off", True))
        self.assertFalse(_parse_bool("OFF", True))

        # Test string inputs with whitespace
        self.assertTrue(_parse_bool("  true  ", False))
        self.assertFalse(_parse_bool("  false  ", True))

        # Test fallback for unrecognized values
        self.assertTrue(_parse_bool("unknown", True))
        self.assertFalse(_parse_bool("unknown", False))
        self.assertTrue(_parse_bool(None, True))
        self.assertFalse(_parse_bool(None, False))
        self.assertTrue(_parse_bool(123, True))
        self.assertFalse(_parse_bool(123, False))

    def test_parse_int_function(self):
        """
        Test the _parse_int function with various inputs.

        This test verifies that the function correctly parses integer values
        from different input types and falls back to default on failure.
        """

        # Test valid integer inputs
        self.assertEqual(_parse_int(42, 0), 42)
        self.assertEqual(_parse_int("42", 0), 42)
        self.assertEqual(_parse_int("-10", 0), -10)
        self.assertEqual(_parse_int("0", 99), 0)

        # Test invalid inputs - should return default
        self.assertEqual(_parse_int("not_a_number", 42), 42)
        self.assertEqual(_parse_int("", 99), 99)
        self.assertEqual(_parse_int(None, 10), 10)
        self.assertEqual(_parse_int([], 5), 5)
        self.assertEqual(_parse_int({}, 7), 7)

        # Test float strings - should fail and return default
        self.assertEqual(_parse_int("3.14", 0), 0)
        self.assertEqual(_parse_int("42.0", 99), 99)

    def test_initialize_database_sqlite_error(self):
        """
        Test that initialize_database() handles sqlite3.Error gracefully.

        This test verifies that when database initialization fails due to
        sqlite3.Error, the exception is logged and re-raised.
        """
        # Mock the database manager's run_sync method to raise sqlite3.Error
        with patch("mmrelay.db_utils._get_db_manager") as mock_get_manager:
            mock_manager = MagicMock()
            mock_manager.run_sync.side_effect = sqlite3.Error(
                "Database initialization failed"
            )
            mock_get_manager.return_value = mock_manager

            with patch("mmrelay.db_utils.logger") as mock_logger:
                with self.assertRaises(sqlite3.Error):
                    initialize_database()
                mock_logger.exception.assert_called_once_with(
                    "Database initialization failed"
                )

    def test_schema_upgrade_operational_errors(self):
        """
        Test that schema upgrade operations handle OperationalError gracefully.

        This test verifies that ALTER TABLE and CREATE INDEX operations
        that fail with OperationalError are ignored (safe no-op) by
        running initialize_database twice and checking that it succeeds.
        """
        # Initialize database first time - should succeed
        initialize_database()

        # Initialize database second time - should also succeed even though
        # ALTER TABLE and CREATE INDEX will fail with OperationalError
        # because the column and index already exist
        try:
            initialize_database()
            # If we get here, the OperationalError was handled correctly
        except sqlite3.OperationalError:
            self.fail("Schema upgrade should handle OperationalError gracefully")


if __name__ == "__main__":
    unittest.main()
