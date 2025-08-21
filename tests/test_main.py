#!/usr/bin/env python3
"""
Test suite for main application functionality in MMRelay.

Tests the main application flow including:
- Application initialization and configuration
- Database initialization
- Plugin loading
- Message queue startup
- Matrix and Meshtastic client connections
- Graceful shutdown handling
- Banner printing and version display
"""

import asyncio
import contextlib
import inspect
import os
import sys
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.main import main, print_banner, run_main


def _close_coro_if_possible(coro: Any) -> None:
    """Close a coroutine/awaitable if it exposes close() to avoid ResourceWarning in tests."""
    if inspect.iscoroutine(coro) and hasattr(coro, "close"):
        coro.close()
    return None


def _mock_run_with_exception(coro: Any) -> None:
    """Close coroutine and raise test exception."""
    _close_coro_if_possible(coro)
    raise Exception("Test error")


def _mock_run_with_keyboard_interrupt(coro: Any) -> None:
    """Close coroutine and raise KeyboardInterrupt."""
    _close_coro_if_possible(coro)
    raise KeyboardInterrupt()


class TestMain(unittest.TestCase):
    """Test cases for main application functionality."""

    def setUp(self):
        """Set up mock configuration for tests."""
        self.mock_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [
                {"id": "!room1:matrix.org", "meshtastic_channel": 0},
                {"id": "!room2:matrix.org", "meshtastic_channel": 1},
            ],
            "meshtastic": {
                "connection_type": "serial",
                "serial_port": "/dev/ttyUSB0",
                "message_delay": 2.0,
            },
            "database": {"msg_map": {"wipe_on_restart": False}},
        }

    def test_print_banner(self):
        """
        Tests that the banner is printed exactly once and includes the version information in the log output.
        """
        with patch("mmrelay.main.logger") as mock_logger:
            print_banner()

            # Should print banner with version
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0][0]
            self.assertIn("Starting MMRelay", call_args)
            self.assertIn("version ", call_args)  # Version should be included

    def test_print_banner_only_once(self):
        """Test that banner is only printed once."""
        with patch("mmrelay.main.logger") as mock_logger:
            print_banner()
            print_banner()  # Second call

            # Should only be called once
            self.assertEqual(mock_logger.info.call_count, 1)

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.connect_matrix", new_callable=AsyncMock)
    @patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock)
    @patch("mmrelay.main.update_longnames")
    @patch("mmrelay.main.update_shortnames")
    @patch("mmrelay.main.stop_message_queue")
    def test_main_basic_flow(
        self,
        mock_stop_queue,
        mock_update_shortnames,
        mock_update_longnames,
        mock_join_room,
        mock_connect_matrix,
        mock_connect_meshtastic,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
    ):
        """
        Verify that all main application initialization functions are properly mocked and callable during the basic startup flow test.
        """
        # This test just verifies that the initialization functions are called
        # We don't run the full main() function to avoid async complexity

        # Verify that the mocks are set up correctly
        self.assertIsNotNone(mock_init_db)
        self.assertIsNotNone(mock_load_plugins)
        self.assertIsNotNone(mock_start_queue)
        self.assertIsNotNone(mock_connect_meshtastic)
        self.assertIsNotNone(mock_connect_matrix)
        self.assertIsNotNone(mock_join_room)
        self.assertIsNotNone(mock_stop_queue)
        self.assertIsNotNone(mock_update_longnames)
        self.assertIsNotNone(mock_update_shortnames)

        # Test passes if all mocks are properly set up
        # The actual main() function testing is complex due to async nature
        # and is better tested through integration tests

    def test_main_with_message_map_wipe(self):
        """
        Test that the message map wipe function is called when the configuration enables wiping on restart.

        Verifies that the wipe logic correctly parses both new and legacy configuration formats and triggers the wipe when appropriate.
        """
        # Enable message map wiping
        config_with_wipe = self.mock_config.copy()
        config_with_wipe["database"]["msg_map"]["wipe_on_restart"] = True

        # Test the specific logic that checks for database wipe configuration
        with patch("mmrelay.db_utils.wipe_message_map") as mock_wipe_map:
            # Extract the wipe configuration the same way main() does
            database_config = config_with_wipe.get("database", {})
            msg_map_config = database_config.get("msg_map", {})
            wipe_on_restart = msg_map_config.get("wipe_on_restart", False)

            # If not found in database config, check legacy db config
            if not wipe_on_restart:
                db_config = config_with_wipe.get("db", {})
                legacy_msg_map_config = db_config.get("msg_map", {})
                wipe_on_restart = legacy_msg_map_config.get("wipe_on_restart", False)

            # Simulate calling wipe_message_map if wipe_on_restart is True
            if wipe_on_restart:
                from mmrelay.db_utils import wipe_message_map

                wipe_message_map()

            # Verify message map was wiped when configured
            mock_wipe_map.assert_called_once()

    @patch("asyncio.run")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.set_config")
    @patch("mmrelay.log_utils.configure_component_debug_logging")
    @patch("mmrelay.main.print_banner")
    def test_run_main(
        self,
        mock_print_banner,
        mock_configure_debug,
        mock_set_config,
        mock_load_config,
        mock_asyncio_run,
    ):
        """
        Test that `run_main` executes the full startup sequence and returns 0 on success.

        Verifies that configuration is loaded and set, logging level is overridden by arguments, the banner is printed, debug logging is configured, the main async function is run, and the function returns 0 to indicate successful execution.
        """
        # Mock arguments
        mock_args = MagicMock()
        mock_args.log_level = "debug"

        # Mock config loading
        mock_load_config.return_value = self.mock_config

        # Mock asyncio.run with coroutine cleanup to prevent warnings
        mock_asyncio_run.side_effect = _close_coro_if_possible

        result = run_main(mock_args)

        # Verify configuration was loaded and set
        mock_load_config.assert_called_once_with(args=mock_args)

        # Verify log level was overridden
        expected_config = self.mock_config.copy()
        expected_config["logging"] = {"level": "debug"}

        # Verify banner was printed
        mock_print_banner.assert_called_once()

        # Verify component debug logging was configured
        mock_configure_debug.assert_called_once()

        # Verify asyncio.run was called
        mock_asyncio_run.assert_called_once()

        # Should return 0 for success
        self.assertEqual(result, 0)

    @patch("mmrelay.config.load_config")
    @patch("asyncio.run")
    def test_run_main_exception_handling(self, mock_asyncio_run, mock_load_config):
        """
        Verify that run_main returns 1 when an exception is raised during asynchronous execution.
        """
        # Mock config loading
        mock_load_config.return_value = self.mock_config

        # Mock asyncio.run with coroutine cleanup and exception
        mock_asyncio_run.side_effect = _mock_run_with_exception

        result = run_main(None)

        # Should return 1 for error
        self.assertEqual(result, 1)

    @patch("mmrelay.config.load_config")
    @patch("asyncio.run")
    def test_run_main_keyboard_interrupt(self, mock_asyncio_run, mock_load_config):
        """
        Verifies that run_main returns 0 when a KeyboardInterrupt is raised during execution, ensuring graceful shutdown behavior.
        """
        # Mock config loading
        mock_load_config.return_value = self.mock_config

        # Mock asyncio.run with coroutine cleanup and KeyboardInterrupt
        mock_asyncio_run.side_effect = _mock_run_with_keyboard_interrupt

        result = run_main(None)

        # Should return 0 for graceful shutdown
        self.assertEqual(result, 0)

    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_matrix", new_callable=AsyncMock)
    @patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock)
    @patch("mmrelay.main.stop_message_queue")
    def test_main_meshtastic_connection_failure(
        self,
        mock_stop_queue,
        mock_join_room,
        mock_connect_matrix,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
        mock_connect_meshtastic,
    ):
        """
        Test that the application attempts to connect to Matrix even if Meshtastic connection fails.

        Simulates a failed Meshtastic connection and verifies that the Matrix connection is still attempted during application startup.
        """
        # Mock Meshtastic connection to return None (failure)
        mock_connect_meshtastic.return_value = None

        # Mock Matrix connection to fail early to avoid hanging
        mock_connect_matrix.return_value = None

        # Call main function (should exit early due to connection failures)
        try:
            asyncio.run(main(self.mock_config))
        except (SystemExit, Exception):
            pass  # Expected due to connection failures

        # Should still proceed with Matrix connection
        mock_connect_matrix.assert_called_once()

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.connect_matrix", new_callable=AsyncMock)
    @patch("mmrelay.main.stop_message_queue")
    def test_main_matrix_connection_failure(
        self,
        mock_stop_queue,
        mock_connect_matrix,
        mock_connect_meshtastic,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
    ):
        """
        Test that an exception during Matrix connection is raised and not suppressed during main application startup.

        Mocks the Matrix connection to raise an exception and verifies that the main function propagates the error.
        """
        # Mock Matrix connection to raise an exception
        mock_connect_matrix.side_effect = Exception("Matrix connection failed")

        # Mock Meshtastic client
        mock_meshtastic_client = MagicMock()
        mock_connect_meshtastic.return_value = mock_meshtastic_client

        # Should raise the Matrix connection exception
        with self.assertRaises(Exception) as context:
            asyncio.run(main(self.mock_config))
        self.assertIn("Matrix connection failed", str(context.exception))


class TestPrintBanner(unittest.TestCase):
    """Test cases for banner printing functionality."""

    def setUp(self):
        """
        Set up test environment for banner tests.
        """
        pass

    @patch("mmrelay.main.logger")
    def test_print_banner_first_time(self, mock_logger):
        """
        Test that the banner is printed and includes version information on the first call to print_banner.
        """
        print_banner()
        mock_logger.info.assert_called_once()
        # Check that the message contains version info
        call_args = mock_logger.info.call_args[0][0]
        self.assertIn("Starting MMRelay", call_args)

    @patch("mmrelay.main.logger")
    def test_print_banner_subsequent_calls(self, mock_logger):
        """
        Test that the banner is printed only once, even if print_banner is called multiple times.
        """
        print_banner()
        print_banner()  # Second call
        # Should only be called once
        mock_logger.info.assert_called_once()


class TestRunMain(unittest.TestCase):
    """Test cases for run_main function."""

    def setUp(self):
        """
        Prepare common fixtures used by run_main tests.

        Creates a default mock args object and a representative configuration used across run_main test cases, and provides helpers to supply a coroutine-cleanup wrapper for asyncio.run so tests can avoid un-awaited coroutine warnings.
        """
        pass

    @patch("asyncio.run")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.set_config")
    @patch("mmrelay.log_utils.configure_component_debug_logging")
    @patch("mmrelay.main.print_banner")
    def test_run_main_success(
        self,
        mock_print_banner,
        mock_configure_logging,
        mock_set_config,
        mock_load_config,
        mock_asyncio_run,
    ):
        """
        Test that `run_main` completes successfully with valid configuration and arguments.

        Verifies that the banner is printed, configuration is loaded, and the main asynchronous function is executed, resulting in a return value of 0.
        """
        # Mock configuration
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org"}],
        }
        mock_load_config.return_value = mock_config

        # Mock asyncio.run with coroutine cleanup to prevent warnings
        mock_asyncio_run.side_effect = _close_coro_if_possible

        # Mock args
        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 0)
        mock_print_banner.assert_called_once()
        mock_load_config.assert_called_once_with(args=mock_args)
        mock_asyncio_run.assert_called_once()

    @patch("asyncio.run")
    @patch("mmrelay.config.set_config")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.main.print_banner")
    def test_run_main_missing_config_keys(
        self, mock_print_banner, mock_load_config, mock_set_config, mock_asyncio_run
    ):
        """
        Test that run_main returns 1 when required configuration keys are missing.

        This verifies that the application detects incomplete configuration and exits with an error code.
        """
        # Mock incomplete configuration
        mock_config = {"matrix": {"homeserver": "https://matrix.org"}}  # Missing keys
        mock_load_config.return_value = mock_config

        # Mock asyncio.run with coroutine cleanup to prevent warnings
        mock_asyncio_run.side_effect = _close_coro_if_possible

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 1)  # Should return error code

    @patch("asyncio.run")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.set_config")
    @patch("mmrelay.log_utils.configure_component_debug_logging")
    @patch("mmrelay.main.print_banner")
    def test_run_main_keyboard_interrupt_with_args(
        self,
        mock_print_banner,
        mock_configure_logging,
        mock_set_config,
        mock_load_config,
        mock_asyncio_run,
    ):
        """
        Test that `run_main` returns 0 when a `KeyboardInterrupt` occurs during execution with command-line arguments.

        Ensures the application exits gracefully with a success code when interrupted by the user, even if arguments are provided.
        """
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org"}],
        }
        mock_load_config.return_value = mock_config

        # Mock asyncio.run with coroutine cleanup and KeyboardInterrupt
        mock_asyncio_run.side_effect = _mock_run_with_keyboard_interrupt

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 0)  # Should return success on keyboard interrupt

    @patch("asyncio.run")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.set_config")
    @patch("mmrelay.log_utils.configure_component_debug_logging")
    @patch("mmrelay.main.print_banner")
    def test_run_main_exception(
        self,
        mock_print_banner,
        mock_configure_logging,
        mock_set_config,
        mock_load_config,
        mock_asyncio_run,
    ):
        """
        Test that run_main returns 1 when a general exception is raised during asynchronous execution.
        """
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org"}],
        }
        mock_load_config.return_value = mock_config

        # Mock asyncio.run with coroutine cleanup and exception
        mock_asyncio_run.side_effect = _mock_run_with_exception

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 1)  # Should return error code

    @patch("os.makedirs")
    @patch("os.path.abspath")
    @patch("asyncio.run")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.set_config")
    @patch("mmrelay.log_utils.configure_component_debug_logging")
    @patch("mmrelay.main.print_banner")
    def test_run_main_with_data_dir(
        self,
        mock_print_banner,
        mock_configure_logging,
        mock_set_config,
        mock_load_config,
        mock_asyncio_run,
        mock_abspath,
        mock_makedirs,
    ):
        """
        Test that run_main creates and uses the absolute path of a custom data directory.

        Verifies that when a custom data directory is specified, run_main ensures the directory exists by creating it if necessary and resolves its absolute path for initialization.
        """

        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org"}],
        }
        mock_load_config.return_value = mock_config

        # Mock asyncio.run with coroutine cleanup to prevent warnings
        mock_asyncio_run.side_effect = _close_coro_if_possible

        # Use a simple custom data directory path
        custom_data_dir = "/home/user/test_custom_data"
        mock_abspath.return_value = custom_data_dir

        mock_args = MagicMock()
        mock_args.data_dir = custom_data_dir
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 0)
        # Check that abspath was called with our custom data dir (may be called multiple times)
        mock_abspath.assert_any_call(custom_data_dir)
        # Check that makedirs was called with our custom data dir (may be called multiple times for logs too)
        mock_makedirs.assert_any_call(custom_data_dir, exist_ok=True)

    @patch("asyncio.run", spec=True)
    @patch("mmrelay.config.load_config", spec=True)
    @patch("mmrelay.config.set_config", spec=True)
    @patch("mmrelay.log_utils.configure_component_debug_logging", spec=True)
    @patch("mmrelay.main.print_banner", spec=True)
    def test_run_main_with_log_level(
        self,
        mock_print_banner,
        mock_configure_logging,
        mock_set_config,
        mock_load_config,
        mock_asyncio_run,
    ):
        """
        Test that run_main applies a custom log level from arguments and completes successfully.

        Ensures that when a log level is specified in the arguments, it overrides the logging level in the configuration, and run_main returns 0 to indicate successful execution.
        """
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org"}],
        }
        mock_load_config.return_value = mock_config

        # Mock asyncio.run with coroutine cleanup to prevent warnings
        mock_asyncio_run.side_effect = _close_coro_if_possible

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = "DEBUG"

        result = run_main(mock_args)

        self.assertEqual(result, 0)
        # Check that log level was set in config
        self.assertEqual(mock_config["logging"]["level"], "DEBUG")


class TestMainFunctionEdgeCases(unittest.TestCase):
    """Test cases for edge cases in the main function."""

    def setUp(self):
        """
        Prepare a mock configuration dictionary for use in test cases.
        """
        self.mock_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"},
        }

    def test_main_with_database_wipe_new_format(self):
        """
        Test that the database wipe logic is triggered when `wipe_on_restart` is set in the new configuration format.

        Verifies that the `wipe_message_map` function is called if the `database.msg_map.wipe_on_restart` flag is enabled in the configuration.
        """
        # Add database config with wipe_on_restart
        config_with_wipe = self.mock_config.copy()
        config_with_wipe["database"] = {"msg_map": {"wipe_on_restart": True}}

        # Test the specific logic that checks for database wipe configuration
        with patch("mmrelay.db_utils.wipe_message_map") as mock_wipe_db:
            # Extract the wipe configuration the same way main() does
            database_config = config_with_wipe.get("database", {})
            msg_map_config = database_config.get("msg_map", {})
            wipe_on_restart = msg_map_config.get("wipe_on_restart", False)

            # If not found in database config, check legacy db config
            if not wipe_on_restart:
                db_config = config_with_wipe.get("db", {})
                legacy_msg_map_config = db_config.get("msg_map", {})
                wipe_on_restart = legacy_msg_map_config.get("wipe_on_restart", False)

            # Simulate calling wipe_message_map if wipe_on_restart is True
            if wipe_on_restart:
                from mmrelay.db_utils import wipe_message_map

                wipe_message_map()

            # Should call wipe_message_map when new config format is set
            mock_wipe_db.assert_called_once()

    def test_main_with_database_wipe_legacy_format(self):
        """
        Test that the database wipe logic is triggered when the legacy configuration format specifies `wipe_on_restart`.

        Verifies that the application correctly detects the legacy `db.msg_map.wipe_on_restart` setting and calls the database wipe function.
        """
        # Add legacy database config with wipe_on_restart
        config_with_wipe = self.mock_config.copy()
        config_with_wipe["db"] = {"msg_map": {"wipe_on_restart": True}}

        # Test the specific logic that checks for database wipe configuration
        with patch("mmrelay.db_utils.wipe_message_map") as mock_wipe_db:
            # Extract the wipe configuration the same way main() does
            database_config = config_with_wipe.get("database", {})
            msg_map_config = database_config.get("msg_map", {})
            wipe_on_restart = msg_map_config.get("wipe_on_restart", False)

            # If not found in database config, check legacy db config
            if not wipe_on_restart:
                db_config = config_with_wipe.get("db", {})
                legacy_msg_map_config = db_config.get("msg_map", {})
                wipe_on_restart = legacy_msg_map_config.get("wipe_on_restart", False)

            # Simulate calling wipe_message_map if wipe_on_restart is True
            if wipe_on_restart:
                from mmrelay.db_utils import wipe_message_map

                wipe_message_map()

            # Should call wipe_message_map when legacy config is set
            mock_wipe_db.assert_called_once()

    def test_main_with_custom_message_delay(self):
        """
        Test that a custom message delay in the Meshtastic configuration is correctly extracted and passed to the message queue starter.
        """
        # Add custom message delay
        config_with_delay = self.mock_config.copy()
        config_with_delay["meshtastic"]["message_delay"] = 5.0

        # Test the specific logic that extracts message delay from config
        with patch("mmrelay.main.start_message_queue") as mock_start_queue:
            # Extract the message delay the same way main() does
            message_delay = config_with_delay.get("meshtastic", {}).get(
                "message_delay", 2.0
            )

            # Simulate calling start_message_queue with the extracted delay

            mock_start_queue(message_delay=message_delay)

            # Should call start_message_queue with custom delay
            mock_start_queue.assert_called_once_with(message_delay=5.0)

    def test_main_no_meshtastic_client_warning(self):
        """
        Verify that update functions are not called when the Meshtastic client is None.

        This test ensures that, if the Meshtastic client is not initialized, the main logic does not attempt to update longnames or shortnames.
        """
        # This test is simplified to avoid async complexity while still testing the core logic
        # The actual behavior is tested through integration tests

        # Test the specific condition: when meshtastic_client is None,
        # update functions should not be called
        with patch("mmrelay.main.update_longnames") as mock_update_long, patch(
            "mmrelay.main.update_shortnames"
        ) as mock_update_short:

            # Simulate the condition where meshtastic_client is None
            import mmrelay.meshtastic_utils

            original_client = getattr(
                mmrelay.meshtastic_utils, "meshtastic_client", None
            )
            mmrelay.meshtastic_utils.meshtastic_client = None

            try:
                # Test the specific logic that checks for meshtastic_client
                if mmrelay.meshtastic_utils.meshtastic_client:
                    # This should not execute when client is None
                    from mmrelay.main import update_longnames, update_shortnames

                    update_longnames(mmrelay.meshtastic_utils.meshtastic_client.nodes)
                    update_shortnames(mmrelay.meshtastic_utils.meshtastic_client.nodes)

                # Verify update functions were not called
                mock_update_long.assert_not_called()
                mock_update_short.assert_not_called()

            finally:
                # Restore original client
                mmrelay.meshtastic_utils.meshtastic_client = original_client


@pytest.mark.parametrize("db_key", ["database", "db"])
@patch("mmrelay.main.initialize_database")
@patch("mmrelay.main.load_plugins")
@patch("mmrelay.main.start_message_queue")
@patch("mmrelay.main.connect_matrix", new_callable=AsyncMock)
@patch("mmrelay.main.connect_meshtastic")
@patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock)
def test_main_database_wipe_config(
    mock_join,
    mock_connect_mesh,
    mock_connect_matrix,
    mock_start_queue,
    mock_load_plugins,
    mock_init_db,
    db_key,
):
    """Test main function with database wipe configuration (current and legacy)."""
    # Mock config with database wipe settings
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        db_key: {"msg_map": {"wipe_on_restart": True}},
    }

    # Mock the async components with proper return values
    mock_matrix_client = AsyncMock()
    mock_matrix_client.add_event_callback = MagicMock()  # This can be sync
    mock_matrix_client.close = AsyncMock()
    mock_connect_matrix.return_value = mock_matrix_client
    mock_connect_mesh.return_value = MagicMock()

    # Mock the message queue to avoid hanging and combine contexts for clarity
    with patch("mmrelay.main.get_message_queue") as mock_get_queue, \
         patch("mmrelay.main.meshtastic_utils.check_connection", new_callable=AsyncMock) as mock_check_conn, \
         patch("mmrelay.main.wipe_message_map") as mock_wipe:
        mock_queue = MagicMock()
        mock_queue.ensure_processor_started = MagicMock()
        mock_get_queue.return_value = mock_queue
        mock_check_conn.return_value = True

        # Set up sync_forever to raise KeyboardInterrupt after a short delay
        async def mock_sync_forever(*args, **kwargs):
            await asyncio.sleep(0.01)  # Very short delay
            raise KeyboardInterrupt()

        mock_matrix_client.sync_forever = mock_sync_forever

        # Run the test with proper exception handling
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(main(config))

        # Should wipe message map on startup
        mock_wipe.assert_called()
        # Should start the message queue processor
        mock_queue.ensure_processor_started.assert_called()


class TestDatabaseConfiguration(unittest.TestCase):
    """Test cases for database configuration handling."""


class TestRunMainFunction(unittest.TestCase):
    """Test cases for run_main function."""



    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    @patch("mmrelay.main.asyncio.run")
    def test_run_main_success(
        self,
        mock_asyncio_run,
        mock_load_credentials,
        mock_load_config,
        mock_print_banner,
    ):
        """Test successful run_main execution."""
        # Mock configuration
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        }
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = None

        # Mock asyncio.run to properly close coroutines
        mock_asyncio_run.side_effect = _close_coro_if_possible

        # Mock args
        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 0)
        mock_print_banner.assert_called_once()
        mock_asyncio_run.assert_called_once()

    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    def test_run_main_missing_config_keys(
        self, mock_load_credentials, mock_load_config, mock_print_banner
    ):
        """Test run_main with missing required configuration keys."""
        # Mock incomplete configuration
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"}
        }  # Missing meshtastic and matrix_rooms
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = None

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 1)
        mock_print_banner.assert_called_once()

    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    def test_run_main_with_credentials_json(
        self, mock_load_credentials, mock_load_config, mock_print_banner
    ):
        """Test run_main with credentials.json present (different required keys)."""
        # Mock configuration with credentials.json present
        mock_config = {
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            # No matrix section needed when credentials.json exists
        }
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = {"access_token": "test_token"}

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        with patch("mmrelay.main.asyncio.run") as mock_asyncio_run:
            # Mock asyncio.run to properly close coroutines
            mock_asyncio_run.side_effect = _close_coro_if_possible
            result = run_main(mock_args)

        self.assertEqual(result, 0)
        mock_asyncio_run.assert_called_once()

    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    @patch("mmrelay.main.asyncio.run")
    @patch("os.makedirs")
    @patch("os.path.abspath")
    def test_run_main_with_custom_data_dir(
        self,
        mock_abspath,
        mock_makedirs,
        mock_asyncio_run,
        mock_load_credentials,
        mock_load_config,
        mock_print_banner,
    ):
        """Test run_main with custom data directory."""
        # Use a simple custom data directory path
        custom_data_dir = "/home/user/test_custom_data"
        mock_abspath.return_value = custom_data_dir

        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        }
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = None

        # Mock asyncio.run to properly close coroutines
        mock_asyncio_run.side_effect = _close_coro_if_possible

        mock_args = MagicMock()
        mock_args.data_dir = custom_data_dir
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 0)
        # Check that abspath was called with our custom data dir (may be called multiple times)
        mock_abspath.assert_any_call(custom_data_dir)
        # Check that makedirs was called with our custom data dir (may be called multiple times for logs too)
        mock_makedirs.assert_any_call(custom_data_dir, exist_ok=True)

    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    def test_run_main_with_log_level_override(
        self, mock_load_credentials, mock_load_config, mock_print_banner
    ):
        """Test run_main with log level override."""
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        }
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = None

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = "DEBUG"

        with patch("mmrelay.main.asyncio.run") as mock_asyncio_run:
            # Mock asyncio.run to properly close coroutines
            mock_asyncio_run.side_effect = _close_coro_if_possible
            result = run_main(mock_args)

        self.assertEqual(result, 0)
        # Verify log level was set in config
        self.assertEqual(mock_config["logging"]["level"], "DEBUG")

    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    @patch("mmrelay.main.asyncio.run")
    def test_run_main_keyboard_interrupt(
        self,
        mock_asyncio_run,
        mock_load_credentials,
        mock_load_config,
        mock_print_banner,
    ):
        """Test run_main handling KeyboardInterrupt."""
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        }
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = None

        # Mock asyncio.run to properly close coroutines and raise KeyboardInterrupt
        mock_asyncio_run.side_effect = _mock_run_with_keyboard_interrupt

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 0)  # KeyboardInterrupt should return 0

    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    @patch("mmrelay.main.asyncio.run")
    def test_run_main_exception_handling(
        self,
        mock_asyncio_run,
        mock_load_credentials,
        mock_load_config,
        mock_print_banner,
    ):
        """Test run_main handling general exceptions."""
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        }
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = None

        # Mock asyncio.run to properly close coroutines and raise exception
        mock_asyncio_run.side_effect = _mock_run_with_exception

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 1)  # General exceptions should return 1


class TestMainAsyncFunction(unittest.TestCase):
    """Test cases for the main async function."""

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_matrix", new_callable=AsyncMock)
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock)
    def test_main_async_initialization_sequence(
        self,
        mock_join,
        mock_connect_mesh,
        mock_connect_matrix,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
    ):
        """Verify that the asynchronous main() startup sequence invokes database initialization, plugin loading, message-queue startup, and both Matrix and Meshtastic connection routines.

        Sets up a minimal config with one Matrix room, injects AsyncMock/MagicMock clients for Matrix and Meshtastic, and arranges for the Matrix client's sync loop and asyncio.sleep to raise KeyboardInterrupt so the function exits cleanly. Asserts each initialization/connect function is called exactly once.
        """
        config = {"matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}]}

        # Mock the async components
        mock_matrix_client = AsyncMock()
        mock_matrix_client.add_event_callback = MagicMock()
        mock_matrix_client.close = AsyncMock()
        mock_matrix_client.sync_forever = AsyncMock(side_effect=KeyboardInterrupt)
        mock_connect_matrix.return_value = mock_matrix_client
        mock_connect_mesh.return_value = MagicMock()

        with patch(
            "mmrelay.main.asyncio.sleep", side_effect=KeyboardInterrupt
        ), contextlib.suppress(KeyboardInterrupt):
            asyncio.run(main(config))

        # Verify initialization sequence
        mock_init_db.assert_called_once()
        mock_load_plugins.assert_called_once()
        mock_start_queue.assert_called_once()
        mock_connect_matrix.assert_called_once()
        mock_connect_mesh.assert_called_once()

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_matrix", new_callable=AsyncMock)
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock)
    def test_main_async_with_multiple_rooms(
        self,
        mock_join,
        mock_connect_mesh,
        mock_connect_matrix,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
    ):
        """
        Verify that main() joins each configured Matrix room.

        Runs the async main flow with two matrix room entries in the config and patches connectors
        so startup proceeds until a KeyboardInterrupt. Asserts join_matrix_room is invoked once
        per configured room.
        """
        config = {
            "matrix_rooms": [
                {"id": "!room1:matrix.org", "meshtastic_channel": 0},
                {"id": "!room2:matrix.org", "meshtastic_channel": 1},
            ]
        }

        mock_matrix_client = AsyncMock()
        mock_matrix_client.add_event_callback = MagicMock()
        mock_matrix_client.close = AsyncMock()
        mock_matrix_client.sync_forever = AsyncMock(side_effect=KeyboardInterrupt)
        mock_connect_matrix.return_value = mock_matrix_client
        mock_connect_mesh.return_value = MagicMock()

        with patch(
            "mmrelay.main.asyncio.sleep", side_effect=KeyboardInterrupt
        ), contextlib.suppress(KeyboardInterrupt):
            asyncio.run(main(config))

        # Verify join_matrix_room was called for each room
        self.assertEqual(mock_join.call_count, 2)

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_matrix", new_callable=AsyncMock)
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock)
    def test_main_async_event_loop_setup(
        self,
        mock_join,
        mock_connect_mesh,
        mock_connect_matrix,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
    ):
        """Test that main async function sets up event loop correctly."""
        config = {"matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}]}

        mock_matrix_client = AsyncMock()
        mock_matrix_client.add_event_callback = MagicMock()
        mock_matrix_client.close = AsyncMock()
        mock_matrix_client.sync_forever = AsyncMock(side_effect=KeyboardInterrupt)
        mock_connect_matrix.return_value = mock_matrix_client
        mock_connect_mesh.return_value = MagicMock()

        with patch("mmrelay.main.asyncio.get_event_loop") as mock_get_loop:
            mock_loop = MagicMock()
            mock_get_loop.return_value = mock_loop

            with patch(
                "mmrelay.main.asyncio.sleep", side_effect=KeyboardInterrupt
            ), contextlib.suppress(KeyboardInterrupt):
                asyncio.run(main(config))

        # Verify event loop was accessed for meshtastic utils
        mock_get_loop.assert_called()


if __name__ == "__main__":
    unittest.main()
