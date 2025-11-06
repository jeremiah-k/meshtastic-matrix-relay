"""
Tests for async functionality and DatabaseManager integration in db_utils.py.

This test module covers:
- DatabaseManager caching and lifecycle management
- Async helper functions (async_store_message_map, async_prune_message_map)
- Configuration parsing and validation
- Database path resolution and caching
- Error handling and edge cases
"""

import asyncio
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import mmrelay.db_utils
from mmrelay.db_utils import (
    _get_db_manager,
    _parse_bool,
    _parse_int,
    _reset_db_manager,
    _resolve_database_options,
    async_prune_message_map,
    async_store_message_map,
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


class TestDatabaseManagerIntegration(unittest.TestCase):
    """Test DatabaseManager integration and caching."""

    def setUp(self):
        """
        Prepare a temporary SQLite database file and reset global DatabaseManager state before each test.

        Creates a temporary file with a .db suffix, stores its path on self.db_path, closes the file descriptor, resets the cached DatabaseManager instance, and clears the database path cache so each test starts with a fresh database environment.
        """
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

        # Clear any existing manager and cache
        _reset_db_manager()
        clear_db_path_cache()

    def tearDown(self):
        """
        Tear down test fixtures and remove the temporary database file.

        Resets the global DatabaseManager cache, clears the database path cache, and deletes the test database file if present.
        """
        _reset_db_manager()
        clear_db_path_cache()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    @patch("mmrelay.db_utils.get_data_dir")
    @patch("os.makedirs")
    def test_get_db_manager_with_config(self, mock_makedirs, mock_get_data_dir):
        """Test _get_db_manager with custom configuration."""
        mock_get_data_dir.return_value = "/mock/data/dir"

        # Mock config with proper structure
        test_config = {
            "database": {
                "path": "/test/custom/path.db",
                "enable_wal": False,
                "busy_timeout_ms": 2000,
                "pragmas": {"synchronous": "OFF", "cache_size": 5000},
            }
        }

        with patch.object(mmrelay.db_utils, "config", test_config):
            # Reset manager to ensure fresh creation
            _reset_db_manager()

            manager = _get_db_manager()

            self.assertIsNotNone(manager)
            self.assertEqual(manager._path, "/test/custom/path.db")
            self.assertFalse(manager._enable_wal)
            self.assertEqual(manager._busy_timeout_ms, 2000)
            self.assertEqual(
                manager._extra_pragmas,
                {"synchronous": "OFF", "temp_store": "MEMORY", "cache_size": 5000},
            )

            # Verify directory creation was attempted
            mock_makedirs.assert_called_with("/test/custom", exist_ok=True)

    @patch("mmrelay.db_utils.get_data_dir")
    @patch("os.makedirs")
    def test_get_db_manager_legacy_config(self, _mock_makedirs, mock_get_data_dir):
        """Test _get_db_manager with legacy configuration format."""
        mock_get_data_dir.return_value = "/mock/data/dir"

        # Mock config with legacy format
        test_config = {
            "db": {  # Legacy format
                "path": "/test/legacy/path.db",
                "enable_wal": True,
                "busy_timeout_ms": 3000,
                "pragmas": {"temp_store": "MEMORY"},
            }
        }

        with patch.object(mmrelay.db_utils, "config", test_config):
            # Reset manager to ensure fresh creation
            _reset_db_manager()

            manager = _get_db_manager()

            self.assertIsNotNone(manager)
            self.assertEqual(manager._path, "/test/legacy/path.db")
            self.assertTrue(manager._enable_wal)
            self.assertEqual(manager._busy_timeout_ms, 3000)
            self.assertEqual(
                manager._extra_pragmas,
                {"temp_store": "MEMORY", "synchronous": "NORMAL"},
            )

    @patch("mmrelay.db_utils.get_data_dir")
    def test_get_db_manager_default_config(self, mock_get_data_dir):
        """Test _get_db_manager with default configuration."""
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir)
        mock_get_data_dir.return_value = temp_dir

        # Mock config as empty dict
        with patch.object(mmrelay.db_utils, "config", {}):
            # Reset manager to ensure fresh creation
            _reset_db_manager()

            manager = _get_db_manager()

            self.assertIsNotNone(manager)
            self.assertEqual(manager._path, os.path.join(temp_dir, "meshtastic.sqlite"))
        self.assertTrue(manager._enable_wal)
        self.assertEqual(manager._busy_timeout_ms, 5000)
        self.assertEqual(
            manager._extra_pragmas, {"synchronous": "NORMAL", "temp_store": "MEMORY"}
        )

    def test_db_manager_caching(self):
        """Test that DatabaseManager instances are properly cached."""
        with patch("mmrelay.db_utils.get_db_path", return_value=self.db_path):
            # First call should create manager
            manager1 = _get_db_manager()
            self.assertIsNotNone(manager1)

            # Second call should return cached manager
            manager2 = _get_db_manager()
            self.assertIs(manager1, manager2)

            # Reset should clear cache
            _reset_db_manager()
            manager3 = _get_db_manager()
            self.assertIsNotNone(manager3)
            self.assertIsNot(manager1, manager3)

    def test_db_manager_signature_change_recreates(self):
        """Test that configuration changes trigger manager recreation."""
        with patch("mmrelay.db_utils.get_db_path", return_value=self.db_path):
            # Create initial manager
            manager1 = _get_db_manager()

            # Mock different configuration
            with patch(
                "mmrelay.db_utils._resolve_database_options",
                return_value=(False, 1000, {"test": "value"}),
            ):
                manager2 = _get_db_manager()
                self.assertIsNot(manager1, manager2)
                self.assertFalse(manager2._enable_wal)
                self.assertEqual(manager2._busy_timeout_ms, 1000)


class TestAsyncHelpers(unittest.TestCase):
    """Test async helper functions."""

    def setUp(self):
        """
        Prepare a temporary SQLite database file and reset global DatabaseManager state before each test.

        Creates a temporary file with a .db suffix, stores its path on self.db_path, closes the file descriptor, resets the cached DatabaseManager instance, and clears the database path cache so each test starts with a fresh database environment.
        """
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

        # Clear any existing manager and cache
        _reset_db_manager()
        clear_db_path_cache()

    def tearDown(self):
        """
        Tear down test fixtures and remove the temporary database file.

        Resets the global DatabaseManager cache, clears the database path cache, and deletes the test database file if present.
        """
        _reset_db_manager()
        clear_db_path_cache()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    @patch("mmrelay.db_utils._get_db_manager")
    def test_async_store_message_map_success(self, mock_get_manager):
        """Test async_store_message_map successful execution."""
        from unittest.mock import AsyncMock

        # Mock manager and its run_async method
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_async = AsyncMock(return_value=None)

        # Call the async function
        asyncio.run(
            async_store_message_map(
                meshtastic_id=123,
                matrix_event_id="$event123",
                matrix_room_id="!room123",
                meshtastic_text="Test message",
                meshtastic_meshnet="testnet",
            )
        )

        # Verify run_async was called correctly
        mock_manager.run_async.assert_called_once()
        call_args = mock_manager.run_async.call_args
        self.assertEqual(call_args[1]["write"], True)

        # Verify the function passed to run_async
        func = call_args[0][0]
        mock_cursor = MagicMock()
        func(mock_cursor)

        # Verify SQL execution
        expected_sql = "INSERT OR REPLACE INTO message_map (meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) VALUES (?, ?, ?, ?, ?)"
        expected_params = (123, "$event123", "!room123", "Test message", "testnet")
        mock_cursor.execute.assert_called_once_with(expected_sql, expected_params)

    @patch("mmrelay.db_utils._get_db_manager")
    def test_async_store_message_map_error(self, mock_get_manager):
        """
        Verify that async_store_message_map logs an exception and does not raise when the database operation fails.

        Calls async_store_message_map with a mocked DatabaseManager whose run_async raises sqlite3.Error and asserts that logger.exception is called once with a message containing "Database error storing message map".
        """
        from unittest.mock import AsyncMock

        # Mock manager that raises an exception
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_async = AsyncMock(side_effect=sqlite3.Error("Database error"))

        with patch("mmrelay.db_utils.logger") as mock_logger:
            # Call the async function - should not raise
            asyncio.run(
                async_store_message_map(
                    meshtastic_id=123,
                    matrix_event_id="$event123",
                    matrix_room_id="!room123",
                    meshtastic_text="Test message",
                )
            )

            # Verify error was logged
            mock_logger.exception.assert_called_once()
            self.assertIn(
                "Database error storing message map",
                mock_logger.exception.call_args[0][0],
            )

    @patch("mmrelay.db_utils._get_db_manager")
    def test_async_prune_message_map_success(self, mock_get_manager):
        """Test async_prune_message_map successful execution."""
        from unittest.mock import AsyncMock

        # Mock manager and its run_async method
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_async = AsyncMock(return_value=5)  # 5 messages pruned

        # Call the async function
        asyncio.run(async_prune_message_map(msgs_to_keep=100))

        # Verify run_async was called correctly
        mock_manager.run_async.assert_called_once()
        call_args = mock_manager.run_async.call_args
        self.assertEqual(call_args[1]["write"], True)

        # Verify the function passed to run_async
        func = call_args[0][0]
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = [150]  # Total count before pruning
        result = func(mock_cursor)

        self.assertEqual(result, 50)  # Should prune 50 messages (150 - 100)

        # Verify SQL executions
        self.assertEqual(mock_cursor.execute.call_count, 2)

        # First call: count messages
        count_call = mock_cursor.execute.call_args_list[0]
        self.assertEqual(count_call[0][0], "SELECT COUNT(*) FROM message_map")

        # Second call: delete old messages
        delete_call = mock_cursor.execute.call_args_list[1]
        self.assertIn("DELETE FROM message_map", delete_call[0][0])
        self.assertEqual(delete_call[0][1], (50,))

    @patch("mmrelay.db_utils._get_db_manager")
    def test_async_prune_message_map_no_pruning_needed(self, mock_get_manager):
        """Test async_prune_message_map when no pruning is needed."""
        from unittest.mock import AsyncMock

        # Mock manager and its run_async method
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_async = AsyncMock(return_value=0)  # No messages pruned

        # Call the async function
        asyncio.run(async_prune_message_map(msgs_to_keep=100))

        # Verify run_async was called
        mock_manager.run_async.assert_called_once()

        # Verify the function passed to run_async
        func = mock_manager.run_async.call_args[0][0]
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = [50]  # Total count less than limit
        result = func(mock_cursor)

        self.assertEqual(result, 0)  # Should prune 0 messages

        # Verify only count query was executed (no delete)
        self.assertEqual(mock_cursor.execute.call_count, 1)
        mock_cursor.execute.assert_called_with("SELECT COUNT(*) FROM message_map")

    @patch("mmrelay.db_utils._get_db_manager")
    def test_async_prune_message_map_error(self, mock_get_manager):
        """Test async_prune_message_map error handling."""
        from unittest.mock import AsyncMock

        # Mock manager that raises an exception
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_async = AsyncMock(side_effect=sqlite3.Error("Database error"))

        with patch("mmrelay.db_utils.logger") as mock_logger:
            # Call the async function - should not raise
            asyncio.run(async_prune_message_map(msgs_to_keep=100))

            # Verify error was logged
            mock_logger.exception.assert_called_once()
            self.assertIn(
                "Database error pruning message_map",
                mock_logger.exception.call_args[0][0],
            )


class TestConfigurationParsing(unittest.TestCase):
    """Test configuration parsing functions."""

    def test_parse_bool(self):
        """Test _parse_bool function."""
        # Test boolean inputs
        self.assertTrue(_parse_bool(True, False))
        self.assertFalse(_parse_bool(False, True))

        # Test string inputs
        self.assertTrue(_parse_bool("1", False))
        self.assertTrue(_parse_bool("true", False))
        self.assertTrue(_parse_bool("TRUE", False))
        self.assertTrue(_parse_bool("yes", False))
        self.assertTrue(_parse_bool("YES", False))
        self.assertTrue(_parse_bool("on", False))
        self.assertTrue(_parse_bool("ON", False))

        self.assertFalse(_parse_bool("0", True))
        self.assertFalse(_parse_bool("false", True))
        self.assertFalse(_parse_bool("FALSE", True))
        self.assertFalse(_parse_bool("no", True))
        self.assertFalse(_parse_bool("NO", True))
        self.assertFalse(_parse_bool("off", True))
        self.assertFalse(_parse_bool("OFF", True))

        # Test invalid inputs (should return default)
        self.assertTrue(_parse_bool("invalid", True))
        self.assertFalse(_parse_bool("invalid", False))
        self.assertTrue(_parse_bool(None, True))
        self.assertFalse(_parse_bool(None, False))
        self.assertTrue(_parse_bool(123, True))
        self.assertFalse(_parse_bool([], False))

    def test_parse_int(self):
        """Test _parse_int function."""
        # Test valid integers
        self.assertEqual(_parse_int("123", 0), 123)
        self.assertEqual(_parse_int("-456", 0), -456)
        self.assertEqual(_parse_int("0", 999), 0)
        self.assertEqual(_parse_int(789, 0), 789)
        self.assertEqual(_parse_int(-789, 0), -789)

        # Test invalid inputs (should return default)
        self.assertEqual(_parse_int("invalid", 999), 999)
        self.assertEqual(_parse_int("12.34", 999), 999)
        self.assertEqual(_parse_int(None, 999), 999)
        self.assertEqual(_parse_int([], 999), 999)
        self.assertEqual(_parse_int({}, 999), 999)

    def test_resolve_database_options_invalid_values(self):
        """
        Validate that parsing helpers fall back to provided defaults for invalid inputs and parse valid integer strings.

        Asserts that _parse_bool returns the supplied default for an unrecognized string input and that _parse_int returns the supplied default for an invalid string but correctly parses a valid numeric string.
        """
        # Test with _parse_bool and _parse_int functions directly
        self.assertTrue(_parse_bool("invalid", True))  # Should return default
        self.assertFalse(_parse_bool("invalid", False))  # Should return default
        self.assertEqual(_parse_int("invalid", 999), 999)  # Should return default
        self.assertEqual(_parse_int("123", 0), 123)  # Should parse valid int

    def test_resolve_database_options_with_invalid_values(self):
        """Test _resolve_database_options with invalid values (should use defaults)."""
        # Mock config with invalid values
        test_config = {
            "database": {
                "enable_wal": "invalid",
                "busy_timeout_ms": "invalid",
                "pragmas": {"synchronous": "OFF"},
            }
        }

        with patch.object(mmrelay.db_utils, "config", test_config):
            enable_wal, busy_timeout, pragmas = _resolve_database_options()

        self.assertTrue(enable_wal)  # Default
        self.assertEqual(busy_timeout, 5000)  # Default
        expected_pragmas = {
            "synchronous": "OFF",  # Valid override
            "temp_store": "MEMORY",  # Default
        }
        self.assertEqual(pragmas, expected_pragmas)

    def test_resolve_database_options_no_config(self):
        """Test _resolve_database_options with no configuration."""
        # Clear config to None for this test
        original_config = getattr(mmrelay.db_utils, "config", None)
        mmrelay.db_utils.config = None

        try:
            enable_wal, busy_timeout, pragmas = _resolve_database_options()

            self.assertTrue(enable_wal)  # Default
            self.assertEqual(busy_timeout, 5000)  # Default
            expected_pragmas = {"synchronous": "NORMAL", "temp_store": "MEMORY"}
            self.assertEqual(pragmas, expected_pragmas)
        finally:
            # Restore original config
            mmrelay.db_utils.config = original_config

    def test_resolve_database_options_empty_pragmas_dict(self):
        """Test _resolve_database_options with empty pragmas dictionary."""
        # Mock config with empty pragmas dict
        test_config = {
            "database": {
                "enable_wal": True,
                "busy_timeout_ms": 3000,
                "pragmas": {},  # Empty dict should be respected
            }
        }

        with patch.object(mmrelay.db_utils, "config", test_config):
            enable_wal, busy_timeout, pragmas = _resolve_database_options()

        self.assertTrue(enable_wal)
        self.assertEqual(busy_timeout, 3000)
        expected_pragmas = {
            "synchronous": "NORMAL",
            "temp_store": "MEMORY",
        }  # Default pragmas only
        self.assertEqual(pragmas, expected_pragmas)


class TestDatabasePathCaching(unittest.TestCase):
    """Test database path resolution and caching."""

    def setUp(self):
        """Set up test fixtures."""
        clear_db_path_cache()

    def tearDown(self):
        """
        Clear the module-level cached database path used by tests.

        This is called after each test to ensure subsequent tests compute and use a fresh database path.
        """
        clear_db_path_cache()

    @patch("mmrelay.db_utils.get_data_dir")
    @patch("os.makedirs")
    def test_get_db_path_custom_path(self, mock_makedirs, mock_get_data_dir):
        """Test get_db_path with custom path from config."""
        mock_get_data_dir.return_value = "/default/data/dir"

        # Mock config with custom path
        test_config = {"database": {"path": "/custom/path.db"}}

        with patch.object(mmrelay.db_utils, "config", test_config):
            # First call
            path1 = get_db_path()
            self.assertEqual(path1, "/custom/path.db")

            # Second call should use cache
            path2 = get_db_path()
            self.assertEqual(path2, "/custom/path.db")
            self.assertIs(path1, path2)  # Should be same cached object

        # Verify directory creation was attempted
        mock_makedirs.assert_called_with("/custom", exist_ok=True)

    @patch("mmrelay.db_utils.get_data_dir")
    @patch("os.makedirs")
    @patch("mmrelay.db_utils.logger")
    def test_get_db_path_legacy_format(
        self, mock_logger, mock_makedirs, mock_get_data_dir
    ):
        """Test get_db_path with legacy config format."""
        mock_get_data_dir.return_value = "/default/data/dir"

        # Mock config with legacy format
        test_config = {"db": {"path": "/legacy/path.db"}}  # Legacy format

        with patch.object(mmrelay.db_utils, "config", test_config):
            path = get_db_path()
            self.assertEqual(path, "/legacy/path.db")

            # Verify deprecation warning was logged
            mock_logger.warning.assert_called_once()
            self.assertIn("legacy", mock_logger.warning.call_args[0][0])

    @patch("mmrelay.db_utils.get_data_dir")
    @patch("os.makedirs")
    def test_get_db_path_default(self, mock_makedirs, mock_get_data_dir):
        """Test get_db_path with default configuration."""
        mock_get_data_dir.return_value = "/default/data/dir"

        # Mock config as empty
        with patch.object(mmrelay.db_utils, "config", {}):
            path = get_db_path()
            self.assertEqual(path, "/default/data/dir/meshtastic.sqlite")

            # Verify data directory creation was attempted
            mock_makedirs.assert_called_with("/default/data/dir", exist_ok=True)

    def test_clear_db_path_cache(self):
        """Test clear_db_path_cache function."""
        # Mock initial config
        initial_config = {"database": {"path": "/test/path.db"}}
        with patch.object(mmrelay.db_utils, "config", initial_config):
            # Get path to populate cache
            path1 = get_db_path()

            # Clear cache
            clear_db_path_cache()

            # Change config and get path again
            new_config = {"database": {"path": "/new/path.db"}}
            with patch.object(mmrelay.db_utils, "config", new_config):
                path2 = get_db_path()
                self.assertNotEqual(path1, path2)
                self.assertEqual(path2, "/new/path.db")


class TestDatabaseManagerReset(unittest.TestCase):
    """Test DatabaseManager reset functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

        _reset_db_manager()
        clear_db_path_cache()

    def tearDown(self):
        """
        Tear down test fixtures and remove the temporary database file.

        Resets the global DatabaseManager cache, clears the database path cache, and deletes the test database file if present.
        """
        _reset_db_manager()
        clear_db_path_cache()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_reset_db_manager_closes_connections(self):
        """Test that _reset_db_manager properly closes connections."""
        with patch("mmrelay.db_utils.get_db_path", return_value=self.db_path):
            # Create manager and get a connection
            manager = _get_db_manager()
            conn = manager._get_connection()

            # Verify connection exists
            self.assertIsNotNone(conn)
            self.assertIn(conn, manager._connections)

            # Reset manager
            _reset_db_manager()

            # Verify manager was reset
            new_manager = _get_db_manager()
            self.assertIsNot(new_manager, manager)

    def test_reset_db_manager_handles_errors(self):
        """Test that _reset_db_manager handles close errors gracefully."""
        with patch("mmrelay.db_utils.get_db_path", return_value=self.db_path):
            # Create manager
            manager = _get_db_manager()

            # Mock close to raise an exception
            with patch.object(manager, "close", side_effect=Exception("Close error")):
                # Should not raise exception
                _reset_db_manager()

            # Verify manager was still reset
            new_manager = _get_db_manager()
            self.assertIsNot(new_manager, manager)

    def test_get_db_manager_runtime_error_on_init_failure(self):
        """Test that _get_db_manager raises RuntimeError when DatabaseManager initialization fails."""
        # Reset manager to None first
        _reset_db_manager()

        with patch("mmrelay.db_utils.get_db_path", return_value=self.db_path):
            # Mock DatabaseManager to return None (simulating failed initialization)
            with patch("mmrelay.db_utils.DatabaseManager", return_value=None):
                with self.assertRaises(RuntimeError) as cm:
                    _get_db_manager()

                self.assertIn(
                    "Database manager initialization failed", str(cm.exception)
                )


class TestInitializeDatabaseErrors(unittest.TestCase):
    """Test error handling in initialize_database function."""

    def setUp(self):
        """
        Create a temporary SQLite database file and initialize attributes for tests.

        Creates a temporary file with a `.db` suffix (left on disk), closes the file handle, and sets `self.temp_db` and `self.db_path` for use by the test.
        """
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

    def tearDown(self):
        """
        Remove the temporary database file created for the test.

        Ignores OSError exceptions raised while attempting to unlink the file (e.g., if the file does not exist or cannot be removed).
        """
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    @patch("mmrelay.db_utils._get_db_manager")
    def test_initialize_database_operational_error_on_index_creation(
        self, mock_get_manager
    ):
        """Test initialize_database handles OperationalError during index creation gracefully."""
        from unittest.mock import MagicMock

        # Mock manager and cursor
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager

        # Mock cursor to raise OperationalError on specific execute calls
        mock_cursor = MagicMock()
        mock_manager.run_sync.side_effect = lambda func, write=True: func(mock_cursor)

        def execute_side_effect(sql, *args, **kwargs):
            # Raise OperationalError for index creation calls only
            """
            Simulates executing a SQL statement, failing for index/column creation and succeeding otherwise.

            Parameters:
                sql (str): The SQL statement to simulate executing. `args` and `kwargs` are accepted for compatibility and ignored.

            Returns:
                None: Indicates the statement succeeded.

            Raises:
                sqlite3.OperationalError: If `sql` contains "CREATE INDEX" or "ALTER TABLE", simulating an index/column already existing.
            """
            if "CREATE INDEX" in sql or "ALTER TABLE" in sql:
                raise sqlite3.OperationalError("Index/column already exists")
            return None  # Table creation succeeds

        mock_cursor.execute.side_effect = execute_side_effect

        # Should not raise exception - should handle OperationalError gracefully
        try:
            initialize_database()
        except Exception as e:
            self.fail(
                f"initialize_database() raised {e} unexpectedly! Should handle OperationalError gracefully."
            )

        # Verify that execute was called multiple times
        self.assertGreater(mock_cursor.execute.call_count, 5)

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    def test_initialize_database_sqlite_error_propagated(
        self, mock_logger, mock_get_manager
    ):
        """Test initialize_database logs and re-raises sqlite3.Error."""
        from unittest.mock import MagicMock

        # Mock manager to raise sqlite3.Error
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.side_effect = sqlite3.Error("Database connection failed")

        # Should raise sqlite3.Error
        with self.assertRaises(sqlite3.Error) as cm:
            initialize_database()

        self.assertIn("Database connection failed", str(cm.exception))

        # Verify error was logged with exception
        mock_logger.exception.assert_called_once_with("Database initialization failed")


class TestPluginDataErrors(unittest.TestCase):
    """Test error handling in plugin data functions."""

    def setUp(self):
        """
        Create a temporary SQLite database file and initialize attributes for tests.

        Creates a temporary file with a `.db` suffix (left on disk), closes the file handle, and sets `self.temp_db` and `self.db_path` for use by the test.
        """
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

    def tearDown(self):
        """
        Remove the temporary database file created for the test.

        Ignores OSError exceptions raised while attempting to unlink the file (e.g., if the file does not exist or cannot be removed).
        """
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    def test_store_plugin_data_database_error(self, mock_logger, mock_get_manager):
        """Test store_plugin_data handles database errors gracefully."""
        from unittest.mock import MagicMock

        # Mock manager to raise sqlite3.Error
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.side_effect = sqlite3.Error("Database locked")

        # Should not raise exception - should handle error gracefully
        try:
            store_plugin_data("test_plugin", "node123", {"key": "value"})
        except Exception as e:
            self.fail(
                f"store_plugin_data() raised {e} unexpectedly! Should handle database errors gracefully."
            )

        # Verify error was logged
        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0]
        self.assertIn("Database error storing plugin data", call_args[0])
        self.assertEqual(call_args[1], "test_plugin")
        self.assertEqual(call_args[2], "node123")

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    def test_delete_plugin_data_database_error(self, mock_logger, mock_get_manager):
        """Test delete_plugin_data handles database errors gracefully."""
        from unittest.mock import MagicMock

        # Mock manager to raise sqlite3.Error
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.side_effect = sqlite3.Error("Database locked")

        # Should not raise exception - should handle error gracefully
        try:
            delete_plugin_data("test_plugin", "node123")
        except Exception as e:
            self.fail(
                f"delete_plugin_data() raised {e} unexpectedly! Should handle database errors gracefully."
            )

        # Verify error was logged
        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0]
        self.assertIn("Database error deleting plugin data", call_args[0])
        self.assertEqual(call_args[1], "test_plugin")
        self.assertEqual(call_args[2], "node123")

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    @patch("json.loads")
    def test_get_plugin_data_for_node_type_error(
        self, mock_json_loads, mock_logger, mock_get_manager
    ):
        """Test get_plugin_data_for_node handles TypeError gracefully."""
        from unittest.mock import MagicMock

        # Mock manager to return valid result
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.return_value = ('{"key": "value"}',)

        # Mock json.loads to raise TypeError
        mock_json_loads.side_effect = TypeError("Not serializable")

        # Should return empty list and log error
        result = get_plugin_data_for_node("test_plugin", "node123")

        self.assertEqual(result, [])
        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0]
        self.assertIn("Failed to decode JSON data", call_args[0])
        self.assertEqual(call_args[1], "test_plugin")
        self.assertEqual(call_args[2], "node123")


class TestMessageMapErrors(unittest.TestCase):
    """Test error handling in message map functions."""

    def setUp(self):
        """
        Create a temporary SQLite database file and initialize attributes for tests.

        Creates a temporary file with a `.db` suffix (left on disk), closes the file handle, and sets `self.temp_db` and `self.db_path` for use by the test.
        """
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

    def tearDown(self):
        """
        Remove the temporary database file created for the test.

        Ignores OSError exceptions raised while attempting to unlink the file (e.g., if the file does not exist or cannot be removed).
        """
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    def test_store_message_map_database_error(self, mock_logger, mock_get_manager):
        """Test store_message_map handles database errors gracefully."""
        from unittest.mock import MagicMock

        # Mock manager to raise sqlite3.Error
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.side_effect = sqlite3.Error("Database locked")

        # Should not raise exception - should handle error gracefully
        try:
            store_message_map(123, "$event123", "!room123", "test message", "testnet")
        except Exception as e:
            self.fail(
                f"store_message_map() raised {e} unexpectedly! Should handle database errors gracefully."
            )

        # Verify error was logged
        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0]
        self.assertIn("Database error storing message map", call_args[0])
        self.assertEqual(call_args[1], "$event123")

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    def test_get_message_map_by_meshtastic_id_malformed_data(
        self, mock_logger, mock_get_manager
    ):
        """Test get_message_map_by_meshtastic_id handles malformed data gracefully."""
        from unittest.mock import MagicMock

        # Mock manager to return malformed data (not enough elements)
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.return_value = (
            "$event123",
            "!room123",
        )  # Missing 2 elements

        # Should return None and log error
        result = get_message_map_by_meshtastic_id(123)

        self.assertIsNone(result)
        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0]
        self.assertIn("Malformed data in message_map", call_args[0])
        self.assertEqual(call_args[1], 123)

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    def test_get_message_map_by_matrix_event_id_database_error(
        self, mock_logger, mock_get_manager
    ):
        """Test get_message_map_by_matrix_event_id handles database errors gracefully."""
        from unittest.mock import MagicMock

        # Mock manager to raise sqlite3.Error
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.side_effect = sqlite3.Error("Connection lost")

        # Should return None and log error
        result = get_message_map_by_matrix_event_id("$event123")

        self.assertIsNone(result)
        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0]
        self.assertIn("Database error retrieving message map", call_args[0])
        self.assertEqual(call_args[1], "$event123")

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    def test_wipe_message_map_database_error(self, mock_logger, mock_get_manager):
        """Test wipe_message_map handles database errors gracefully."""
        from unittest.mock import MagicMock

        # Mock manager to raise sqlite3.Error
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.side_effect = sqlite3.Error("Database locked")

        # Should not raise exception - should handle error gracefully
        try:
            wipe_message_map()
        except Exception as e:
            self.fail(
                f"wipe_message_map() raised {e} unexpectedly! Should handle database errors gracefully."
            )

        # Verify error was logged
        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0]
        self.assertIn("Failed to wipe message_map", call_args[0])


class TestLongnameShortnameErrors(unittest.TestCase):
    """Test error handling in longname/shortname operations."""

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    def test_get_longname_database_error(self, mock_logger, mock_get_manager):
        """Test get_longname handles database errors gracefully."""
        from unittest.mock import MagicMock

        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.side_effect = sqlite3.Error("Connection lost")

        result = get_longname("!testid")

        self.assertIsNone(result)
        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0]
        self.assertIn("Database error retrieving longname for", call_args[0])
        self.assertEqual(call_args[1], "!testid")

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    def test_save_longname_database_error(self, mock_logger, mock_get_manager):
        """Test save_longname handles database errors gracefully."""
        from unittest.mock import MagicMock

        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.side_effect = sqlite3.Error("Disk full")

        save_longname("!testid", "Test Long Name")

        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0]
        self.assertIn("Database error saving longname for", call_args[0])
        self.assertEqual(call_args[1], "!testid")

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    def test_update_longnames_database_error(self, mock_logger, mock_get_manager):
        """Test update_longnames handles database errors gracefully."""
        from unittest.mock import MagicMock

        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.side_effect = sqlite3.Error("Database locked")

        # update_longnames expects a dict with .values(), and user needs an "id" field
        nodes = {"1": {"num": 1, "user": {"id": "!testid", "longName": "Test1"}}}
        update_longnames(nodes)

        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0]
        self.assertIn("Database error saving longname for", call_args[0])
        self.assertEqual(call_args[1], "!testid")

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    def test_get_shortname_database_error(self, mock_logger, mock_get_manager):
        """Test get_shortname handles database errors gracefully."""
        from unittest.mock import MagicMock

        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.side_effect = sqlite3.Error("Connection lost")

        result = get_shortname("!testid")

        self.assertIsNone(result)
        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0]
        self.assertIn("Database error retrieving shortname", call_args[0])

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    def test_save_shortname_database_error(self, mock_logger, mock_get_manager):
        """Test save_shortname handles database errors gracefully."""
        from unittest.mock import MagicMock

        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.side_effect = sqlite3.Error("Disk full")

        save_shortname("!testid", "TN")

        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0]
        self.assertIn("Database error saving shortname for", call_args[0])
        self.assertEqual(call_args[1], "!testid")

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    def test_update_shortnames_database_error(self, mock_logger, mock_get_manager):
        """Test update_shortnames handles database errors gracefully."""
        from unittest.mock import MagicMock

        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.side_effect = sqlite3.Error("Database locked")

        # update_shortnames expects a dict with .values(), and user needs an "id" field
        nodes = {"1": {"num": 1, "user": {"id": "!testid", "shortName": "T1"}}}
        update_shortnames(nodes)

        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0]
        self.assertIn("Database error saving shortname for", call_args[0])
        self.assertEqual(call_args[1], "!testid")


class TestIntegrationWithRealDatabase(unittest.TestCase):
    """Integration tests with a real SQLite database."""

    def setUp(self):
        """
        Create a temporary SQLite database file and configure the test environment to use it.

        Creates a temporary .db file, closes it, resets the global DatabaseManager and database-path cache, and patches mmrelay.db_utils.config so the module uses the temporary database path for the duration of the test.
        """
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

        _reset_db_manager()
        clear_db_path_cache()

        # Mock config to use our test database
        test_config = {"database": {"path": self.db_path}}
        self.config_patcher = patch.object(mmrelay.db_utils, "config", test_config)
        self.config_patcher.start()

    def tearDown(self):
        """Clean up test fixtures."""
        self.config_patcher.stop()
        _reset_db_manager()
        clear_db_path_cache()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_initialize_database_with_manager(self):
        """Test initialize_database works with DatabaseManager."""
        # Initialize database
        initialize_database()

        # Verify tables were created
        manager = _get_db_manager()
        with manager.read() as cursor:
            # Check tables exist
            tables = cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [row[0] for row in tables]

            expected_tables = ["longnames", "shortnames", "plugin_data", "message_map"]
            for table in expected_tables:
                self.assertIn(table, table_names)

    def test_store_and_get_longname_with_manager(self):
        """Test save_longname and get_longname work with DatabaseManager."""
        # Initialize database
        initialize_database()

        # Save longname
        save_longname("!testid", "Test Long Name")

        # Retrieve longname
        result = get_longname("!testid")
        self.assertEqual(result, "Test Long Name")

        # Test non-existent ID
        result = get_longname("!nonexistent")
        self.assertIsNone(result)

    def test_store_and_get_shortname_with_manager(self):
        """Test save_shortname and get_shortname work with DatabaseManager."""
        # Initialize database
        initialize_database()

        # Save shortname
        save_shortname("!testid", "TN")

        # Retrieve shortname
        result = get_shortname("!testid")
        self.assertEqual(result, "TN")

        # Test non-existent ID
        result = get_shortname("!nonexistent")
        self.assertIsNone(result)

    def test_initialize_database_creates_new_db(self):
        """Test initialize_database creates new database when none exists."""
        import os
        import tempfile

        # Create a temporary database path that doesn't exist
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_new.db")

            # Ensure database doesn't exist
            self.assertFalse(os.path.exists(db_path))

            # Mock config to use our temporary path
            with patch("mmrelay.db_utils.get_db_path", return_value=db_path):
                # Initialize database - should create new one
                initialize_database()

                # Database should now exist
                self.assertTrue(os.path.exists(db_path))

    def test_delete_plugin_data_success(self):
        """
        Verifies that delete_plugin_data removes stored plugin data for a given plugin and node id.

        Stores plugin data, confirms it can be retrieved, calls delete_plugin_data for the same plugin and node id, and asserts that subsequent retrieval returns an empty list.
        """
        # Initialize database
        initialize_database()

        # Store some plugin data first
        store_plugin_data("test_plugin", "!testid", {"key": "value"})

        # Verify data exists
        data = get_plugin_data_for_node("test_plugin", "!testid")
        self.assertIsNotNone(data)
        self.assertEqual(data, {"key": "value"})

        # Delete the plugin data
        delete_plugin_data("test_plugin", "!testid")

        # Verify data is gone
        data = get_plugin_data_for_node("test_plugin", "!testid")
        self.assertEqual(data, [])

    def test_store_and_get_plugin_data_with_manager(self):
        """Test plugin data functions work with DatabaseManager."""
        # Initialize database
        initialize_database()

        # Store plugin data
        test_data = {"key1": "value1", "key2": [1, 2, 3]}
        store_plugin_data("test_plugin", "!testid", test_data)

        # Retrieve plugin data
        result = get_plugin_data_for_node("test_plugin", "!testid")
        self.assertEqual(result, test_data)

        # Test non-existent data
        result = get_plugin_data_for_node("nonexistent_plugin", "!testid")
        self.assertEqual(result, [])

    def test_store_and_get_message_map_with_manager(self):
        """Test message map functions work with DatabaseManager."""
        # Initialize database
        initialize_database()

        # Store message map
        store_message_map(
            meshtastic_id=123,
            matrix_event_id="$event123",
            matrix_room_id="!room123",
            meshtastic_text="Test message",
            meshtastic_meshnet="testnet",
        )

        # Retrieve by meshtastic ID
        result = get_message_map_by_meshtastic_id(123)
        self.assertIsNotNone(result)
        matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet = result
        self.assertEqual(matrix_event_id, "$event123")
        self.assertEqual(matrix_room_id, "!room123")
        self.assertEqual(meshtastic_text, "Test message")
        self.assertEqual(meshtastic_meshnet, "testnet")

    def test_wipe_and_prune_message_map_with_manager(self):
        """Test wipe and prune functions work with DatabaseManager."""
        # Initialize database
        initialize_database()

        # Store multiple messages
        for i in range(10):
            store_message_map(
                meshtastic_id=i,
                matrix_event_id=f"$event{i}",
                matrix_room_id="!room123",
                meshtastic_text=f"Message {i}",
            )

        # Verify messages exist
        manager = _get_db_manager()
        with manager.read() as cursor:
            count = cursor.execute("SELECT COUNT(*) FROM message_map").fetchone()[0]
            self.assertEqual(count, 10)

        # Prune to keep only 5
        prune_message_map(5)

        # Verify only 5 remain
        with manager.read() as cursor:
            count = cursor.execute("SELECT COUNT(*) FROM message_map").fetchone()[0]
            self.assertEqual(count, 5)

        # Wipe remaining messages
        wipe_message_map()

        # Verify all messages are gone
        with manager.read() as cursor:
            count = cursor.execute("SELECT COUNT(*) FROM message_map").fetchone()[0]
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
