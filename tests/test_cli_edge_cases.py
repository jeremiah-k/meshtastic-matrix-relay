#!/usr/bin/env python3
"""
Test suite for CLI module edge cases and error handling in MMRelay.

Tests edge cases and error handling including:
- Invalid argument combinations
- File system permission errors
- Missing dependencies and import failures
- Windows-specific argument handling
- Configuration validation edge cases
- Service installation failures
- Sample config generation edge cases
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, mock_open, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.cli import (
    check_config,
    generate_sample_config,
    handle_cli_commands,
    main,
    parse_arguments,
)


class TestCLIEdgeCases(unittest.TestCase):
    """Test cases for CLI module edge cases and error handling."""

    def setUp(self):
        """
        Saves the current state of sys.argv before each test to allow restoration after test execution.
        """
        # Store original sys.argv to restore later
        self.original_argv = sys.argv.copy()

    def tearDown(self):
        """
        Restores the original sys.argv after each test to maintain test isolation.
        """
        # Restore original sys.argv
        sys.argv = self.original_argv

    def test_parse_arguments_invalid_log_level(self):
        """
        Test that providing an invalid log level argument causes argument parsing to exit with SystemExit.
        """
        with patch("sys.argv", ["mmrelay", "--log-level", "invalid"]):
            with self.assertRaises(SystemExit):
                parse_arguments()

    def test_parse_arguments_pytest_environment(self):
        """
        Verify that unknown CLI arguments do not trigger warnings when running in a pytest environment.
        """
        with patch("sys.argv", ["pytest", "--unknown-arg"]):
            with patch("builtins.print") as mock_print:
                parse_arguments()
                # Should not print warning in test environment
                mock_print.assert_not_called()

    def test_check_config_file_permission_error(self):
        """
        Test that check_config returns False and prints an error when a file permission error occurs while opening the config file.
        """
        with patch("mmrelay.cli.parse_arguments") as mock_parse_args:
            args = MagicMock()
            args.config = None
            mock_parse_args.return_value = args
            with patch("mmrelay.cli.get_config_paths") as mock_get_paths:
                mock_get_paths.return_value = ["/test/config.yaml"]
                with patch("os.path.isfile", return_value=True):
                    with patch(
                        "builtins.open",
                        side_effect=PermissionError("Permission denied"),
                    ):
                        with patch("builtins.print") as mock_print:
                            result = check_config()
                            self.assertFalse(result)
                            mock_print.assert_called()

    def test_check_config_yaml_syntax_error(self):
        """
        Test that check_config returns False and prints an error when the config file contains invalid YAML syntax.
        """
        with patch("mmrelay.cli.parse_arguments") as mock_parse_args:
            args = MagicMock()
            args.config = None
            mock_parse_args.return_value = args
            with patch("mmrelay.cli.get_config_paths") as mock_get_paths:
                mock_get_paths.return_value = ["/test/config.yaml"]
                with patch("os.path.isfile", return_value=True):
                    with patch(
                        "builtins.open", mock_open(read_data="invalid: yaml: [")
                    ):
                        with patch("builtins.print") as mock_print:
                            result = check_config()
                            self.assertFalse(result)
                            # Should print YAML error
                            mock_print.assert_called()

    def test_check_config_empty_file(self):
        """
        Test that check_config returns False and prints an error when the configuration file is empty.
        """
        with patch("mmrelay.cli.parse_arguments") as mock_parse_args:
            args = MagicMock()
            args.config = None
            mock_parse_args.return_value = args
            with patch("mmrelay.cli.get_config_paths") as mock_get_paths:
                mock_get_paths.return_value = ["/test/config.yaml"]
                with patch("os.path.isfile", return_value=True):
                    with patch("builtins.open", mock_open(read_data="")):
                        with patch("yaml.load", return_value=None):
                            with patch("builtins.print") as mock_print:
                                result = check_config()
                                self.assertFalse(result)
                                mock_print.assert_called()

    def test_check_config_missing_required_sections(self):
        """
        Test that check_config returns False when required configuration sections or fields are missing.

        Verifies that various incomplete or malformed configuration dictionaries cause check_config to fail validation.
        """
        invalid_configs = [
            {},  # Empty config
            {"matrix": {}},  # Missing matrix fields
            {
                "matrix": {"homeserver": "test"},
                "meshtastic": {},
            },  # Missing matrix_rooms
            {
                "matrix": {
                    "homeserver": "test",
                    "access_token": "test",
                    "bot_user_id": "test",
                }
            },  # Missing meshtastic
        ]

        for config in invalid_configs:
            with self.subTest(config=config):
                with patch("mmrelay.cli.parse_arguments") as mock_parse_args:
                    args = MagicMock()
                    args.config = None
                    mock_parse_args.return_value = args
                    with patch("mmrelay.cli.get_config_paths") as mock_get_paths:
                        mock_get_paths.return_value = ["/test/config.yaml"]
                        with patch("os.path.isfile", return_value=True):
                            with patch("builtins.open", mock_open()):
                                with patch("yaml.load", return_value=config):
                                    with patch("builtins.print"):
                                        result = check_config()
                                        self.assertFalse(result)

    def test_check_config_invalid_connection_types(self):
        """
        Test that check_config returns False and prints an error when the Meshtastic connection type is invalid.
        """
        config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@test:matrix.org",
            },
            "matrix_rooms": [{"id": "!test:matrix.org"}],
            "meshtastic": {"connection_type": "invalid_type"},
        }

        with patch("mmrelay.cli.parse_arguments") as mock_parse_args:
            args = MagicMock()
            args.config = None
            mock_parse_args.return_value = args
            with patch("mmrelay.cli.get_config_paths") as mock_get_paths:
                mock_get_paths.return_value = ["/test/config.yaml"]
                with patch("os.path.isfile", return_value=True):
                    with patch("builtins.open", mock_open()):
                        with patch("yaml.load", return_value=config):
                            with patch("builtins.print") as mock_print:
                                result = check_config()
                                self.assertFalse(result)
                                mock_print.assert_called()

    def test_check_config_missing_connection_specific_fields(self):
        """
        Test that `check_config` returns False when required connection-specific fields are missing from the Meshtastic configuration.

        Verifies that the function correctly identifies missing fields for each connection type (serial, tcp, ble) and fails validation as expected.
        """
        test_cases = [
            {"connection_type": "serial"},  # Missing serial_port
            {"connection_type": "tcp"},  # Missing host
            {"connection_type": "ble"},  # Missing ble_address
        ]

        for meshtastic_config in test_cases:
            with self.subTest(meshtastic_config=meshtastic_config):
                config = {
                    "matrix": {
                        "homeserver": "https://matrix.org",
                        "access_token": "test_token",
                        "bot_user_id": "@test:matrix.org",
                    },
                    "matrix_rooms": [{"id": "!test:matrix.org"}],
                    "meshtastic": meshtastic_config,
                }

                with patch("mmrelay.cli.parse_arguments") as mock_parse_args:
                    args = MagicMock()
                    args.config = None
                    mock_parse_args.return_value = args
                    with patch("mmrelay.cli.get_config_paths") as mock_get_paths:
                        mock_get_paths.return_value = ["/test/config.yaml"]
                        with patch("os.path.isfile", return_value=True):
                            with patch("builtins.open", mock_open()):
                                with patch("yaml.load", return_value=config):
                                    with patch("builtins.print"):
                                        result = check_config()
                                        self.assertFalse(result)

    def test_generate_sample_config_existing_file(self):
        """
        Test that generate_sample_config returns False and prints an error when the config file already exists.
        """
        with patch("mmrelay.cli.get_config_paths") as mock_get_paths:
            mock_get_paths.return_value = ["/test/config.yaml"]
            with patch("os.path.isfile", return_value=True):
                with patch("builtins.print") as mock_print:
                    result = generate_sample_config()
                    self.assertFalse(result)
                    mock_print.assert_called()

    def test_generate_sample_config_directory_creation_failure(self):
        """
        Test that generate_sample_config returns False and prints an error when directory creation fails due to permission errors.
        """
        with patch("mmrelay.cli.get_config_paths") as mock_get_paths:
            mock_get_paths.return_value = ["/readonly/config.yaml"]
            with patch("os.path.isfile", return_value=False):
                with patch(
                    "os.makedirs", side_effect=PermissionError("Permission denied")
                ):
                    with patch("builtins.print") as mock_print:
                        result = generate_sample_config()
                        self.assertFalse(result)
                        mock_print.assert_called()

    def test_generate_sample_config_missing_sample_file(self):
        """
        Test that generate_sample_config returns False and prints an error when the sample config resource file is missing.
        """
        with patch("mmrelay.cli.get_config_paths") as mock_get_paths:
            mock_get_paths.return_value = ["/test/config.yaml"]
            with patch("os.path.isfile", return_value=False):
                with patch("os.makedirs"):
                    with patch(
                        "mmrelay.tools.get_sample_config_path"
                    ) as mock_get_sample:
                        mock_get_sample.return_value = "/nonexistent/sample.yaml"
                        with patch("mmrelay.cli.os.path.exists", return_value=False):
                            with patch("importlib.resources.files") as mock_resources:
                                mock_file = MagicMock()
                                mock_file.joinpath.return_value.read_text.side_effect = FileNotFoundError(
                                    "Resource not found"
                                )
                                mock_resources.return_value = mock_file
                                with patch("builtins.print") as mock_print:
                                    result = generate_sample_config()
                                    self.assertFalse(result)
                                    mock_print.assert_called()

    def test_generate_sample_config_copy_failure(self):
        """
        Test that generate_sample_config returns False and prints an error when file copying fails due to an IOError.
        """
        with patch("mmrelay.cli.get_config_paths") as mock_get_paths:
            mock_get_paths.return_value = ["/test/config.yaml"]
            with patch("os.path.isfile", return_value=False):
                with patch("os.makedirs"):
                    with patch(
                        "mmrelay.tools.get_sample_config_path"
                    ) as mock_get_sample:
                        mock_get_sample.return_value = "/sample/config.yaml"
                        with patch("os.path.exists", return_value=True):
                            with patch(
                                "shutil.copy2", side_effect=IOError("Copy failed")
                            ):
                                with patch("builtins.print") as mock_print:
                                    result = generate_sample_config()
                                    self.assertFalse(result)
                                    mock_print.assert_called()

    def test_main_import_error_handling(self):
        """
        Test that the main function returns exit code 1 when an ImportError occurs during module import.
        """
        with patch("mmrelay.cli.parse_arguments") as mock_parse:
            args = MagicMock()
            args.check_config = False
            args.install_service = True
            args.generate_config = False
            args.version = False
            mock_parse.return_value = args

            # Mock import failure
            with patch(
                "builtins.__import__", side_effect=ImportError("Module not found")
            ):
                result = main()
                self.assertEqual(result, 1)

    def test_handle_cli_commands_service_installation_failure(self):
        """
        Test that handle_cli_commands returns code 1 when service installation fails.

        Simulates a failure in the service installation process and verifies that the CLI handler returns the appropriate error code.
        """
        args = MagicMock()
        args.version = False
        args.install_service = True
        args.generate_config = False
        args.check_config = False

        with patch("mmrelay.setup_utils.install_service", return_value=False):
            result = handle_cli_commands(args)
            self.assertEqual(result, 1)

    def test_handle_cli_commands_config_generation_failure(self):
        """
        Test that handle_cli_commands returns code 1 when configuration generation fails.

        Simulates a failure in generate_sample_config and verifies that handle_cli_commands returns 1.
        """
        args = MagicMock()
        args.version = False
        args.install_service = False
        args.generate_config = True
        args.check_config = False

        with patch("mmrelay.cli.generate_sample_config", return_value=False):
            result = handle_cli_commands(args)
            self.assertEqual(result, 1)

    def test_handle_cli_commands_with_base_dir(self):
        """Test that --base-dir argument sets custom_base_dir and creates directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = os.path.join(temp_dir, "custom_base")

            args = MagicMock()
            args.version = False
            args.install_service = False
            args.generate_config = False
            args.check_config = False
            args.base_dir = base_dir
            args.data_dir = None
            args.command = None

            # Reset the global config variables
            import mmrelay.config

            original_base = mmrelay.config.custom_base_dir
            original_data = mmrelay.config.custom_data_dir

            try:
                mmrelay.config.custom_base_dir = None
                mmrelay.config.custom_data_dir = None

                with patch("mmrelay.cli.logger"):
                    result = handle_cli_commands(args)

                # Verify the base directory was set and created
                self.assertIsNone(result)
                self.assertEqual(
                    mmrelay.config.custom_base_dir, os.path.abspath(base_dir)
                )
                self.assertTrue(os.path.exists(base_dir))
            finally:
                mmrelay.config.custom_base_dir = original_base
                mmrelay.config.custom_data_dir = original_data

    def test_handle_cli_commands_with_data_dir(self):
        """Test that deprecated --data-dir argument sets custom_data_dir and creates directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = os.path.join(temp_dir, "custom_data")

            args = MagicMock()
            args.version = False
            args.install_service = False
            args.generate_config = False
            args.check_config = False
            args.base_dir = None
            args.data_dir = data_dir
            args.command = None

            # Reset the global config variables
            import mmrelay.config

            original_base = mmrelay.config.custom_base_dir
            original_data = mmrelay.config.custom_data_dir

            try:
                mmrelay.config.custom_base_dir = None
                mmrelay.config.custom_data_dir = None

                with patch("mmrelay.cli.logger"):
                    with patch("sys.stderr"):  # Suppress deprecation warning
                        result = handle_cli_commands(args)

                # Verify the data directory was set and created
                self.assertIsNone(result)
                self.assertEqual(
                    mmrelay.config.custom_data_dir, os.path.abspath(data_dir)
                )
                self.assertTrue(os.path.exists(data_dir))
            finally:
                mmrelay.config.custom_base_dir = original_base
                mmrelay.config.custom_data_dir = original_data


if __name__ == "__main__":
    unittest.main()
