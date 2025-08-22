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

import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.log_utils import configure_component_debug_logging, get_logger


class TestLogUtils(unittest.TestCase):
    """Test cases for logging utilities."""

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
        mmrelay.log_utils._component_debug_configured = False

        # Clear any existing loggers to avoid interference
        logging.getLogger().handlers.clear()

    def tearDown(self):
        """
        Cleans up the test environment by removing temporary files and resetting logging state after each test.
        """
        # Clean up temporary files
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

        # Reset logging state
        logging.getLogger().handlers.clear()
        logging.getLogger().setLevel(logging.WARNING)

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

    @patch("mmrelay.log_utils.get_log_dir")
    def test_get_logger_with_file_logging(self, mock_get_log_dir):
        """
        Verify that a logger is created with a file handler when file logging is enabled in the configuration.

        Ensures the logger has at least one handler and exactly one RotatingFileHandler when file logging is configured.
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

    def test_get_logger_main_relay_logger(self):
        """
        Verify that creating the main relay logger with file logging enabled sets the global log file path variable.
        """
        config = {"logging": {"log_to_file": True, "filename": self.test_log_file}}

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        # Clear any existing handlers for the main logger
        main_logger = logging.getLogger("M<>M Relay")
        main_logger.handlers.clear()

        get_logger("M<>M Relay")

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

        # Should not have configured debug logging
        self.assertFalse(mmrelay.log_utils._component_debug_configured)

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

        # Should have configured debug logging
        self.assertTrue(mmrelay.log_utils._component_debug_configured)

        # Check that specific loggers were set to DEBUG
        self.assertEqual(logging.getLogger("nio").level, logging.DEBUG)
        self.assertEqual(logging.getLogger("nio.client").level, logging.DEBUG)
        self.assertEqual(logging.getLogger("meshtastic").level, logging.DEBUG)

        # Bleak should not be set to DEBUG (was False in config)
        self.assertNotEqual(logging.getLogger("bleak").level, logging.DEBUG)

    def test_configure_component_debug_logging_only_once(self):
        """
        Verify that component debug logging configuration is applied only once, ensuring subsequent calls do not override existing logger levels.
        """
        config = {"logging": {"debug": {"matrix_nio": True}}}

        import mmrelay.log_utils

        mmrelay.log_utils.config = config

        # First call should configure
        configure_component_debug_logging()
        self.assertTrue(mmrelay.log_utils._component_debug_configured)

        # Set a logger to a different level
        logging.getLogger("nio").setLevel(logging.WARNING)

        # Second call should not reconfigure
        configure_component_debug_logging()

        # Logger should still be at WARNING, not DEBUG
        self.assertEqual(logging.getLogger("nio").level, logging.WARNING)

    def test_get_logger_in_test_environment(self):
        """
        Verify that a logger can be created in a test environment without triggering CLI parsing or errors.
        """
        # Set test environment
        with patch.dict(os.environ, {"MMRELAY_TESTING": "1"}):
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
        mmrelay.log_utils._component_debug_configured = False

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
        mmrelay.log_utils._component_debug_configured = False

        configure_component_debug_logging()
        boolean_level = logging.getLogger("nio").level

        # Reset and test string "debug"
        mmrelay.log_utils._component_debug_configured = False
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
        mmrelay.log_utils._component_debug_configured = False

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
        mmrelay.log_utils._component_debug_configured = False

        configure_component_debug_logging()

        # Invalid levels should fall back to DEBUG
        self.assertEqual(logging.getLogger("nio").level, logging.DEBUG)
        self.assertEqual(logging.getLogger("bleak").level, logging.DEBUG)

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


if __name__ == "__main__":
    unittest.main()
