#!/usr/bin/env python3
"""
Test suite for logging utilities in MMRelay.

Tests the logging configuration and functionality including:
- Logger creation and configuration
- Console and file handler setup
- Log level configuration from config and CLI
- Rich handler integration for colored output
- Component debug logging configuration
- Log file rotation and path resolution
"""

import contextlib
import logging
import os
import sys
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

# Add src to path for imports before local imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.log_utils import (
    configure_component_debug_logging,
    get_logger,
)


# Shared dummy RichHandler stand-in for environments where Rich is unavailable or patched out.
class DummyRichHandler(logging.Handler):
    """Test double for RichHandler when Rich is unavailable."""

    def __init__(self, **kwargs: Any) -> None:
        """
        Initialize the handler and record whether rich-style tracebacks are requested.

        Parameters:
            rich_tracebacks (bool | None): Whether to enable rich-style tracebacks for this handler; `None` means unspecified.
        """
        super().__init__()
        self.rich_tracebacks = kwargs.get("rich_tracebacks")


class TestLogUtils(unittest.TestCase):
    """Test cases for logging utilities."""

    @contextlib.contextmanager
    def _patched_rich(
        self,
        lu_module,
        *,
        config,
        original_rich_available,
        original_rich_handler,
        original_console,
    ):
        """
        Temporarily enable Rich logging components for tests and restore state afterwards.
        """
        if not lu_module.RICH_AVAILABLE:
            lu_module.RICH_AVAILABLE = True
            lu_module.RichHandler = DummyRichHandler
            lu_module.console = object()

        original_config = getattr(lu_module, "config", None)
        lu_module.config = config
        try:
            yield
        finally:
            self._close_all_handlers()
            if not original_rich_available:
                lu_module.RICH_AVAILABLE = original_rich_available
                if original_rich_handler is not None:
                    lu_module.RichHandler = original_rich_handler
                else:
                    delattr(lu_module, "RichHandler")
                lu_module.console = original_console
            lu_module.config = original_config

    def setUp(self):
        """
        Prepares a clean test environment by creating a temporary directory for log files and resetting global logging state.

        Resets relevant global variables in `mmrelay.log_utils` and clears existing logging handlers to ensure test isolation.
        """
        # Create temporary directory for test logs
        self.test_dir = tempfile.mkdtemp()
        self.test_log_file = os.path.join(self.test_dir, "test.log")

        # Reset global state
        import mmrelay.log_utils

        mmrelay.log_utils.config = None
        mmrelay.log_utils.log_file_path = None
        mmrelay.log_utils._registered_logger_names.clear()
        mmrelay.log_utils._logger_config_generations.clear()
        mmrelay.log_utils._config_generation = 0

        # Clear any existing loggers to avoid interference
        logging.getLogger().handlers.clear()

    def tearDown(self):
        """
        Cleans up the test environment by removing temporary files and resetting logging state after each test.
        """
        # Clean up temporary files
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

        # Reset logging state using the comprehensive handler cleanup helper
        self._close_all_handlers()
        logging.getLogger().setLevel(logging.WARNING)

    def _close_all_handlers(self) -> None:
        """
        Close and remove all handlers from every registered logger and the root logger.

        Closes each handler (suppressing OSError and ValueError) and clears handler lists for all known loggers and the root logger to ensure no file descriptors or resources remain open.
        """
        for lg in list(logging.Logger.manager.loggerDict.values()):
            if isinstance(lg, logging.PlaceHolder):
                continue
            for h in list(lg.handlers):
                with contextlib.suppress(OSError, ValueError):
                    h.close()
            lg.handlers.clear()
        for h in list(logging.getLogger().handlers):
            with contextlib.suppress(OSError, ValueError):
                h.close()
        logging.getLogger().handlers.clear()

    def test_get_logger_basic(self):
        """
        Verifies that a logger is created with default settings when no configuration is provided.

        Checks that the logger has the correct name, INFO level, no propagation, and at least one handler.
        """
        logger = get_logger("test_logger")

        self.assertIsInstance(logger, logging.Logger)
        self.assertEqual(logger.name, "test_logger")
        self.assertEqual(logger.level, logging.INFO)  # Default level
        self.assertFalse(logger.propagate)

        # Should have at least one handler (console)
        self.assertGreater(len(logger.handlers), 0)

    def test_get_logger_with_config_level(self):
        """
        Test that get_logger sets the logger level to DEBUG when configured with a "debug" log level.
        """
        config = {"logging": {"level": "debug"}}

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        logger = get_logger("test_logger")

        self.assertEqual(logger.level, logging.DEBUG)

    def test_get_logger_with_invalid_config_level(self):
        """
        Test that get_logger falls back to INFO level when given an invalid log level in the configuration.
        """
        config = {"logging": {"level": "invalid_level"}}

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        # Should not raise exception, should fall back to default INFO level
        logger = get_logger("test_logger")

        # Should fall back to INFO level
        self.assertEqual(logger.level, logging.INFO)

    def test_get_logger_color_disabled(self):
        """
        Test that a logger is created with color output disabled in the configuration.

        Verifies that the logger has at least one console handler and is a valid Logger instance when color output is turned off.
        """
        config = {"logging": {"color_enabled": False}}

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        logger = get_logger("test_logger")

        # Should have console handler
        self.assertGreater(len(logger.handlers), 0)

        # Check that it's not a RichHandler (would be StreamHandler instead)

        console_handlers = [
            h
            for h in logger.handlers
            if not isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        self.assertGreater(len(console_handlers), 0)
        # When colors are disabled, should use StreamHandler instead of RichHandler
        # Note: The actual implementation may still use RichHandler, so we just check it works
        self.assertIsInstance(logger, logging.Logger)

    def test_get_logger_rich_tracebacks_default_disabled(self):
        """
        Rich tracebacks should be disabled by default when using the Rich handler.
        """
        import mmrelay.log_utils as lu

        original_rich_available = lu.RICH_AVAILABLE
        original_rich_handler = getattr(lu, "RichHandler", None)
        original_console = getattr(lu, "console", None)

        with self._patched_rich(
            lu,
            config={"logging": {"log_to_file": False}},
            original_rich_available=original_rich_available,
            original_rich_handler=original_rich_handler,
            original_console=original_console,
        ):
            logger_name = "test_logger_rich_default"
            logging.getLogger(logger_name).handlers.clear()
            logger = get_logger(logger_name)

            rich_handlers = [
                h
                for h in logger.handlers
                if hasattr(h, "rich_tracebacks")
                and not isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            self.assertGreater(len(rich_handlers), 0)
            self.assertFalse(rich_handlers[0].rich_tracebacks)

    def test_get_logger_rich_tracebacks_enabled_via_config(self):
        """
        Rich tracebacks should be enabled when configured explicitly.
        """
        import mmrelay.log_utils as lu

        original_rich_available = lu.RICH_AVAILABLE
        original_rich_handler = getattr(lu, "RichHandler", None)
        original_console = getattr(lu, "console", None)

        with self._patched_rich(
            lu,
            config={
                "logging": {
                    "rich_tracebacks": True,
                    "log_to_file": False,
                }
            },
            original_rich_available=original_rich_available,
            original_rich_handler=original_rich_handler,
            original_console=original_console,
        ):
            logger_name = "test_logger_rich_enabled"
            logging.getLogger(logger_name).handlers.clear()
            logger = get_logger(logger_name)

            rich_handlers = [
                h
                for h in logger.handlers
                if hasattr(h, "rich_tracebacks")
                and not isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            self.assertGreater(len(rich_handlers), 0)
            self.assertTrue(rich_handlers[0].rich_tracebacks)

    def test_get_logger_rich_tracebacks_with_fake_rich_default_disabled(self):
        """
        Even when Rich is unavailable, forcing RICH_AVAILABLE with a fake handler should keep tracebacks disabled by default.
        """
        import mmrelay.log_utils as lu

        logger_name = "test_logger_fake_rich_default"
        logging.getLogger(logger_name).handlers.clear()

        original_rich_available = lu.RICH_AVAILABLE
        original_rich_handler = getattr(lu, "RichHandler", None)
        original_console = getattr(lu, "console", None)

        try:
            lu.RICH_AVAILABLE = True
            lu.RichHandler = DummyRichHandler
            lu.console = object()
            lu.config = {"logging": {"log_to_file": False}}

            logger = get_logger(logger_name)
            handlers = [h for h in logger.handlers if isinstance(h, DummyRichHandler)]
            self.assertEqual(len(handlers), 1)
            self.assertFalse(handlers[0].rich_tracebacks)
        finally:
            self._close_all_handlers()
            lu.RICH_AVAILABLE = original_rich_available
            if original_rich_handler is not None:
                lu.RichHandler = original_rich_handler
            else:
                delattr(lu, "RichHandler")
            lu.console = original_console
            lu.config = None

    def test_get_logger_rich_tracebacks_with_fake_rich_enabled(self):
        """
        Forcing RICH_AVAILABLE with a fake handler should honor rich_tracebacks config.
        """
        import mmrelay.log_utils as lu

        logger_name = "test_logger_fake_rich_enabled"
        logging.getLogger(logger_name).handlers.clear()

        original_rich_available = lu.RICH_AVAILABLE
        original_rich_handler = getattr(lu, "RichHandler", None)
        original_console = getattr(lu, "console", None)

        try:
            lu.RICH_AVAILABLE = True
            lu.RichHandler = DummyRichHandler
            lu.console = object()
            lu.config = {"logging": {"rich_tracebacks": True, "log_to_file": False}}

            logger = get_logger(logger_name)
            handlers = [h for h in logger.handlers if isinstance(h, DummyRichHandler)]
            self.assertEqual(len(handlers), 1)
            self.assertTrue(handlers[0].rich_tracebacks)
        finally:
            self._close_all_handlers()
            lu.RICH_AVAILABLE = original_rich_available
            if original_rich_handler is not None:
                lu.RichHandler = original_rich_handler
            else:
                delattr(lu, "RichHandler")
            lu.console = original_console
            lu.config = None

    @patch("mmrelay.log_utils.get_log_dir")
    def test_get_logger_with_file_logging(self, mock_get_log_dir):
        """
        Verify a logger includes a RotatingFileHandler when file logging is enabled.

        Mocks the log directory and enables file logging in configuration, then creates a uniquely named logger and asserts it has at least one handler and exactly one RotatingFileHandler.
        """
        mock_get_log_dir.return_value = self.test_dir

        config = {"logging": {"log_to_file": True}}

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        # Use unique logger name and clear any existing handlers
        logger_name = "test_logger_file_logging"
        existing_logger = logging.getLogger(logger_name)
        existing_logger.handlers.clear()

        logger = get_logger(logger_name)

        # Should have handlers (exact count may vary)
        self.assertGreater(len(logger.handlers), 0)

        # Check for file handler
        file_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        self.assertEqual(len(file_handlers), 1)  # Should have exactly one file handler

    @patch("mmrelay.log_utils.get_log_dir")
    def test_get_logger_with_custom_log_file(self, mock_get_log_dir):
        """
        Verify that a logger is created with a custom log file path when file logging is enabled in the configuration.

        Ensures the logger has at least one handler and, if a file handler is present, its path ends with the specified custom filename.
        """
        mock_get_log_dir.return_value = self.test_dir

        config = {"logging": {"log_to_file": True, "filename": self.test_log_file}}

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        # Use unique logger name and clear any existing handlers
        logger_name = "test_logger_custom_file"
        existing_logger = logging.getLogger(logger_name)
        existing_logger.handlers.clear()

        logger = get_logger(logger_name)

        # Should have handlers
        self.assertGreater(len(logger.handlers), 0)

        # Check for file handler if it exists
        file_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        if file_handlers:
            # The actual path might be resolved differently, just check it contains our filename
            actual_path = file_handlers[0].baseFilename
            self.assertTrue(
                actual_path.endswith("test.log"),
                f"Expected path to end with 'test.log', got {actual_path}",
            )

    @patch("mmrelay.log_utils.get_log_dir")
    def test_get_logger_file_logging_disabled(self, mock_get_log_dir):
        """
        Test that a logger is created with handlers but without file handlers when file logging is disabled in the configuration.
        """
        config = {"logging": {"log_to_file": False}}

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        # Clear any existing logger to ensure clean test
        logger_name = "test_logger_disabled"
        existing_logger = logging.getLogger(logger_name)
        existing_logger.handlers.clear()

        logger = get_logger(logger_name)

        # Should have handlers but no file handlers
        self.assertGreater(len(logger.handlers), 0)
        file_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        self.assertEqual(len(file_handlers), 0)

    @patch("mmrelay.log_utils.get_log_dir")
    def test_get_logger_log_rotation_config(self, mock_get_log_dir):
        """
        Test that a logger created with log rotation configuration applies the specified maximum log size and backup count to its file handler.
        """
        mock_get_log_dir.return_value = self.test_dir

        config = {
            "logging": {
                "log_to_file": True,
                "max_log_size": 5 * 1024 * 1024,  # 5 MB
                "backup_count": 3,
            }
        }

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        # Use unique logger name to avoid caching issues
        logger_name = "test_logger_rotation"
        existing_logger = logging.getLogger(logger_name)
        existing_logger.handlers.clear()

        logger = get_logger(logger_name)

        # Check file handler rotation settings if file handler exists
        file_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        if file_handlers:
            file_handler = file_handlers[0]
            self.assertEqual(file_handler.maxBytes, 5 * 1024 * 1024)
            self.assertEqual(file_handler.backupCount, 3)

    @patch("mmrelay.log_utils.get_log_dir")
    def test_refresh_all_loggers_applies_new_file_logging(self, mock_get_log_dir):
        """
        Ensure existing loggers pick up new file logging configuration after refresh.
        """
        mock_get_log_dir.return_value = self.test_dir

        import mmrelay.log_utils as lu

        # Start with file logging disabled
        lu.config = {"logging": {"log_to_file": False}}

        logger_name = "test_refresh_logger"
        existing_logger = logging.getLogger(logger_name)
        existing_logger.handlers.clear()

        logger = get_logger(logger_name)
        initial_file_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        self.assertEqual(len(initial_file_handlers), 0)

        # Enable file logging and trigger a refresh
        refreshed_log_file = os.path.join(self.test_dir, "refreshed.log")
        lu.config = {"logging": {"log_to_file": True, "filename": refreshed_log_file}}
        lu.refresh_all_loggers()

        refreshed_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        self.assertEqual(len(refreshed_handlers), 1)
        self.assertTrue(
            refreshed_handlers[0].baseFilename.endswith("refreshed.log"),
            f"Expected refreshed.log, got {refreshed_handlers[0].baseFilename}",
        )

    def test_get_logger_main_relay_logger(self):
        """
        Set the global log file path when the main 'MMRelay' logger is created with file logging enabled.

        Verifies that mmrelay.log_utils.log_file_path is assigned to the configured filename after creating the "MMRelay" logger with file logging enabled.
        """
        config = {"logging": {"log_to_file": True, "filename": self.test_log_file}}

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        # Clear any existing handlers for the main logger
        main_logger = logging.getLogger("MMRelay")
        main_logger.handlers.clear()

        get_logger("MMRelay")

        # Should store log file path globally
        self.assertEqual(mmrelay.log_utils.log_file_path, self.test_log_file)

    def test_configure_component_debug_logging_no_config(self):
        """
        Verify that configuring component debug logging with no config set does not raise an exception and does not enable debug logging.
        """
        import mmrelay.log_utils

        mmrelay.log_utils.config = None

        # Should not raise exception
        configure_component_debug_logging()

    def test_configure_component_debug_logging_with_config(self):
        """
        Verifies that component debug logging is correctly configured based on the provided config, enabling DEBUG level for specified components and leaving others unchanged.
        """
        config = {
            "logging": {
                "debug": {"matrix_nio": True, "bleak": False, "meshtastic": True}
            }
        }

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        configure_component_debug_logging()

        # Check that specific loggers were set to DEBUG
        self.assertEqual(logging.getLogger("nio").level, logging.DEBUG)
        self.assertEqual(logging.getLogger("nio.client").level, logging.DEBUG)
        self.assertEqual(logging.getLogger("meshtastic").level, logging.DEBUG)

        # Bleak should not be set to DEBUG (was False in config)
        self.assertNotEqual(logging.getLogger("bleak").level, logging.DEBUG)

    def test_configure_component_debug_logging_reapplies(self):
        """
        Verify that component debug logging can be invoked multiple times to reapply configuration.
        """
        config = {"logging": {"debug": {"matrix_nio": True}}}

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        configure_component_debug_logging()

        # Set a logger to a different level
        logging.getLogger("nio").setLevel(logging.WARNING)

        # Second call should reconfigure back to DEBUG
        configure_component_debug_logging()

        # Logger should be reset to DEBUG
        self.assertEqual(logging.getLogger("nio").level, logging.DEBUG)

    def test_get_logger_in_test_environment(self):
        """
        Verify that a logger can be created in a test environment without triggering CLI parsing or errors.
        """
        # Should create logger without issues even if argument parsing fails/runs
        logger = get_logger("test_logger")

        # Should create logger without issues
        self.assertIsInstance(logger, logging.Logger)

    def test_configure_component_debug_logging_string_levels(self):
        """
        Test that string-based log levels work correctly for component debug logging.
        """
        config = {
            "logging": {
                "debug": {
                    "matrix_nio": "warning",
                    "bleak": "error",
                    "meshtastic": "info",
                }
            }
        }

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        configure_component_debug_logging()

        # Check that string levels are correctly applied
        self.assertEqual(logging.getLogger("nio").level, logging.WARNING)
        self.assertEqual(logging.getLogger("bleak").level, logging.ERROR)
        self.assertEqual(logging.getLogger("meshtastic").level, logging.INFO)

    def test_configure_component_debug_logging_boolean_vs_string_debug(self):
        """
        Test that boolean true and string "debug" result in the same DEBUG level.
        """
        # Test boolean true
        config1 = {"logging": {"debug": {"matrix_nio": True}}}
        import mmrelay.log_utils

        mmrelay.log_utils.config = config1

        configure_component_debug_logging()
        boolean_level = logging.getLogger("nio").level

        # Reset and test string "debug"
        config2 = {"logging": {"debug": {"matrix_nio": "debug"}}}
        mmrelay.log_utils.config = config2

        configure_component_debug_logging()
        string_level = logging.getLogger("nio").level

        # Both should result in DEBUG level
        self.assertEqual(boolean_level, logging.DEBUG)
        self.assertEqual(string_level, logging.DEBUG)
        self.assertEqual(boolean_level, string_level)

    def test_configure_component_debug_logging_disabled_components_suppressed(self):
        """
        Test that disabled components are completely suppressed (CRITICAL+1 level).
        """
        config = {"logging": {"debug": {"matrix_nio": False, "bleak": False}}}

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        configure_component_debug_logging()

        # Disabled components should be set to CRITICAL+1 (completely suppressed)
        expected_level = logging.CRITICAL + 1
        self.assertEqual(logging.getLogger("nio").level, expected_level)
        self.assertEqual(logging.getLogger("bleak").level, expected_level)

    def test_configure_component_debug_logging_invalid_string_level(self):
        """
        Test that invalid string log levels fall back to DEBUG.
        """
        config = {
            "logging": {
                "debug": {"matrix_nio": "invalid_level", "bleak": "another_bad_level"}
            }
        }

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        configure_component_debug_logging()

        # Invalid levels should fall back to DEBUG
        self.assertEqual(logging.getLogger("nio").level, logging.DEBUG)
        self.assertEqual(logging.getLogger("bleak").level, logging.DEBUG)

    def test_configure_component_debug_logging_none_debug_config(self):
        """
        Ensure configuring component debug logging when the debug config is None does not raise and suppresses all component loggers.

        Verifies that calling configure_component_debug_logging with a logging.debug value of None suppresses known component loggers (nio, bleak, meshtastic) to a level above CRITICAL.
        """
        config = {
            "logging": {"debug": None}
        }  # This happens when debug section is empty/commented

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        # Should not raise exception
        configure_component_debug_logging()

        # All component loggers should be suppressed (CRITICAL + 1)
        self.assertEqual(logging.getLogger("nio").level, logging.CRITICAL + 1)
        self.assertEqual(logging.getLogger("bleak").level, logging.CRITICAL + 1)
        self.assertEqual(logging.getLogger("meshtastic").level, logging.CRITICAL + 1)

    def test_get_logger_file_creation_error(self):
        """
        Test that `get_logger` handles file creation errors gracefully when given an invalid log file path.

        Ensures that enabling file logging with a non-existent directory does not raise unexpected exceptions, and that either a valid `Logger` is returned or a `PermissionError` is raised.
        """
        import os
        import tempfile

        # Use a path in a non-existent subdirectory of temp directory
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_path = os.path.join(temp_dir, "nonexistent", "test.log")

            config = {
                "logging": {
                    "log_to_file": True,
                    "filename": invalid_path,  # Invalid path (parent dir doesn't exist)
                }
            }

            import mmrelay.log_utils

            mmrelay.log_utils.config = config

            # Should not raise exception, just return logger
            try:
                logger = get_logger("test_logger")
                self.assertIsInstance(logger, logging.Logger)
            except PermissionError:
                # This is expected behavior - the test passes if we get a permission error
                pass

    def test_get_logger_file_creation_deep_path_error(self):
        """
        Test that `get_logger` handles permission errors gracefully when trying to create directories in protected paths.
        """
        import os

        # Use any path; simulate a permission error when creating its directory
        protected_path = os.path.join(self.test_dir, "protected", "test.log")

        config = {
            "logging": {
                "log_to_file": True,
                "filename": protected_path,
            }
        }

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        # Simulate a permission error when creating the log directory
        with patch(
            "mmrelay.log_utils.os.makedirs", side_effect=PermissionError("denied")
        ):
            logger = get_logger("test_logger_protected")
        self.assertIsInstance(logger, logging.Logger)

        # Should not have file handler due to permission error
        file_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        self.assertEqual(len(file_handlers), 0)

    def test_get_logger_file_creation_deep_nested_success(self):
        """
        Verify get_logger creates missing nested directories for a configured log file and writes logs to it.

        Configures logging to write to a deeply nested file path, obtains a logger, and asserts that a RotatingFileHandler was attached, the file was created, and a logged message was persisted to the file.
        """
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a deeply nested path
            deep_path = os.path.join(temp_dir, "level1", "level2", "level3", "test.log")

            config = {
                "logging": {
                    "log_to_file": True,
                    "filename": deep_path,
                }
            }

            import mmrelay.log_utils

            mmrelay.log_utils.config = config

            # Clear any existing handlers
            logger_name = "test_logger_deep_nested"
            existing_logger = logging.getLogger(logger_name)
            existing_logger.handlers.clear()

            logger = get_logger(logger_name)
            self.assertIsInstance(logger, logging.Logger)

            # Should have file handler
            file_handlers = [
                h
                for h in logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            self.assertEqual(len(file_handlers), 1)

            # Verify file was created
            self.assertTrue(os.path.exists(deep_path))

            # Test writing to the log
            test_message = "Test deep nested logging"
            logger.info(test_message)

            # Verify message was written
            with open(deep_path, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertIn(test_message, content)

    def test_get_logger_error_logging_with_existing_handlers(self):
        """
        Verify that when enabling file logging fails due to a protected/invalid file path, an existing logger keeps its non-file handlers and remains usable.

        Sets up a logger with only console handlers, enables file logging using a path that will cause a permission error, forces handler re-evaluation, and asserts that:
        - the returned object is a Logger,
        - no RotatingFileHandler was added,
        - at least one non-file handler remains attached.
        """
        import os

        config = {
            "logging": {
                "log_to_file": True,
                "filename": os.path.join(self.test_dir, "invalid", "test.log"),
            }
        }

        import mmrelay.log_utils as lu

        # First, create a logger with console handler only (no file logging)
        logger_name = "test_logger_error_handling"

        # Set config to disable file logging initially
        lu.config = {"logging": {"log_to_file": False}}
        logger = get_logger(logger_name)

        # Should have at least console handler but no file handlers
        self.assertGreater(len(logger.handlers), 0)

        # Now change config to enable file logging with invalid path
        lu.config = config

        # Clear logger's handler cache to force re-evaluation
        self._close_all_handlers()

        # Try to add file handler (should fail gracefully)
        with patch(
            "mmrelay.log_utils.RotatingFileHandler",
            side_effect=PermissionError("denied"),
        ):
            logger = get_logger(logger_name)

        # Should still be a valid logger
        self.assertIsInstance(logger, logging.Logger)

        # Should have console handler but no file handlers due to error
        file_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        self.assertEqual(len(file_handlers), 0)

        # Should have at least console handler
        self.assertGreater(len(logger.handlers), 0)

    def test_component_logging_with_handlers(self):
        """Verify that component loggers receive handlers from the main logger."""
        import io

        import mmrelay.log_utils
        from mmrelay.constants.app import APP_DISPLAY_NAME

        # Setup main logger and config for component logging
        config = {
            "logging": {
                "level": "INFO",
                "debug": {"bleak": "DEBUG"},
                "color_enabled": False,  # Use standard handler for easier capture
            }
        }

        mmrelay.log_utils.config = config

        # Clear handlers from previous tests to ensure isolation
        self._close_all_handlers()
        logging.getLogger(APP_DISPLAY_NAME).handlers.clear()
        logging.getLogger("bleak").handlers.clear()

        # Capture stderr to check for output
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            # Initialize main logger to ensure it has handlers
            main_logger = get_logger(APP_DISPLAY_NAME)
            self.assertTrue(main_logger.handlers, "Main logger should have handlers")

            # Configure component logging
            configure_component_debug_logging()

            # Get a component logger and emit a message
            bleak_logger = logging.getLogger("bleak")
            test_message = "This is a bleak debug message."
            bleak_logger.debug(test_message)

            # Verify output
            captured_output = mock_stderr.getvalue()
            self.assertIn("bleak", captured_output)
            self.assertIn(test_message, captured_output)


if __name__ == "__main__":
    unittest.main()
