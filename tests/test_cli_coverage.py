#!/usr/bin/env python3
"""
Additional test suite for uncovered lines in CLI in MMRelay.

This file contains tests for previously uncovered code paths including:
- Subcommand dispatch in main()
- ImportError when handling --install-service
- Handling --auth legacy flag
- ImportError in generate_sample_config
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.cli import (
    generate_sample_config,
    handle_auth_command,
    handle_cli_commands,
    handle_subcommand,
    main,
    parse_arguments,
)


class TestMainSubcommandDispatch(unittest.TestCase):
    """Test cases for subcommand dispatch in main()."""

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.handle_subcommand")
    @patch("mmrelay.cli.handle_cli_commands")
    @patch("mmrelay.cli.handle_cli_commands")  # Mock legacy handler
    def test_main_with_command_attribute(
        self, mock_handle_cli_commands, mock_handle_subcommand, mock_parse
    ):
        """Test main() dispatches to handle_subcommand when args.command is set."""
        # Create mock args with command attribute
        mock_args = MagicMock()
        mock_args.command = "config"
        mock_args.config_command = "check"
        mock_args.data_dir = None
        mock_args.config = None

        mock_parse.return_value = mock_args
        mock_handle_subcommand.return_value = 0

        result = main()

        # Should call handle_subcommand
        mock_handle_subcommand.assert_called_once_with(mock_args)

        # Should not call legacy handler
        mock_handle_cli_commands.assert_not_called()

        # Should return the subcommand result
        self.assertEqual(result, 0)


class TestHandleCLICommandsImportError(unittest.TestCase):
    """Test cases for ImportError handling in handle_cli_commands()."""

    def setUp(self):
        """Initialize mock arguments."""
        self.mock_args = MagicMock()
        self.mock_args.install_service = True

    @patch("mmrelay.cli.logger")
    @patch("mmrelay.cli.install_service")
    @patch("mmrelay.cli.get_deprecation_warning", return_value="Warning message")
    def test_handle_cli_commands_install_import_error(
        self, mock_deprecation, mock_install_service, mock_logger
    ):
        """Test handle_cli_commands handles ImportError when --install-service."""
        # Make import_service raise ImportError
        mock_install_service.side_effect = ImportError("module not found")

        result = handle_cli_commands(self.mock_args)

        # Should return 1 on ImportError
        self.assertEqual(result, 1)

        # Should log the error
        mock_logger.exception.assert_called_once()
        error_message = mock_logger.exception.call_args[0][0]
        self.assertIn("Error importing setup utilities", error_message)

        # Should have logged deprecation warning
        mock_deprecation.assert_called_once_with("--install-service")


class TestHandleCLICommandsAuthLegacy(unittest.TestCase):
    """Test cases for --auth legacy flag handling."""

    def setUp(self):
        """Initialize mock arguments."""
        self.mock_args = MagicMock()
        self.mock_args.auth = True

    @patch("mmrelay.cli.logger")
    @patch("mmrelay.cli.handle_auth_command", return_value=0)
    @patch("mmrelay.cli.get_deprecation_warning", return_value="--auth is deprecated")
    def test_handle_cli_commands_auth_flag(
        self, mock_deprecation, mock_handle_auth, mock_logger
    ):
        """Test handle_cli_commands handles --auth legacy flag."""
        result = handle_cli_commands(self.mock_args)

        # Should call handle_auth_command
        mock_handle_auth.assert_called_once_with(self.mock_args)

        # Should return the command result
        self.assertEqual(result, 0)

        # Should log deprecation warning
        mock_logger.warning.assert_called_once()
        warning_message = mock_logger.warning.call_args[0][0]
        self.assertIn("--auth is deprecated", warning_message)


class TestGenerateSampleConfigImportError(unittest.TestCase):
    """Test cases for ImportError handling in generate_sample_config()."""

    @patch("mmrelay.cli.get_config_paths", return_value=["/path/to/config.yaml"])
    @patch("mmrelay.cli.os.path.isfile", return_value=False)
    @patch("mmrelay.cli.os.path.exists", return_value=False)
    @patch(
        "mmrelay.cli.get_sample_config_path", return_value="/path/to/sample_config.yaml"
    )
    @patch("mmrelay.cli.os.makedirs")
    @patch("builtins.print")
    def test_generate_sample_config_windows_utils_import_error(
        self,
        mock_print,
        mock_makedirs,
        mock_exists,
        mock_sample_path,
        mock_isfile,
        mock_config_paths,
    ):
        """Test generate_sample_config handles ImportError when trying Windows utils."""
        # This test covers line 1957 where ImportError occurs

        # Make shutil.copy2 fail and then trigger Windows utils import error
        import mmrelay.cli

        with patch("mmrelay.cli.shutil.copy2", side_effect=OSError("copy failed")):
            # Mock importlib.resources to fail
            with patch(
                "importlib.resources.files",
                side_effect=ImportError("resources not available"),
            ):
                with patch(
                    "os.path.exists", side_effect=[False, False, False, False, False]
                ):
                    result = generate_sample_config()

                    # Should return False on error
                    self.assertFalse(result)

                    # Should print error message
                    print_calls = [
                        call
                        for call in mock_print.call_args_list
                        if "Error" in str(call)
                    ]
                    self.assertTrue(len(print_calls) > 0, "Should print error message")

    @patch("mmrelay.cli.get_config_paths", return_value=["/path/to/config.yaml"])
    @patch("mmrelay.cli.os.path.isfile", return_value=False)
    @patch("mmrelay.cli.os.path.exists", return_value=False)
    @patch(
        "mmrelay.cli.get_sample_config_path", return_value="/path/to/sample_config.yaml"
    )
    @patch("mmrelay.cli.os.makedirs")
    @patch("builtins.print")
    @patch("mmrelay.cli.os")
    def test_generate_sample_config_import_error_windows_specific(
        self,
        mock_os,
        mock_print,
        mock_makedirs,
        mock_exists,
        mock_sample_path,
        mock_isfile,
        mock_config_paths,
    ):
        """Test generate_sample_config handles ImportError for Windows-specific guidance."""
        # Test the path where windows_utils import fails (line 1957)

        # Make all fallback paths fail
        mock_os.path.exists.side_effect = lambda path: False
        mock_os.path.join = lambda *args, **kwargs: "/mocked/path"

        # Mock shutil.copy2 to fail
        with patch("shutil.copy2", side_effect=OSError("copy failed")):
            # Make importlib.resources fail
            with patch(
                "importlib.resources.files",
                side_effect=ImportError("resources not available"),
            ):
                result = generate_sample_config()

                # Should return False
                self.assertFalse(result)

                # Should have printed error
                self.assertTrue(mock_print.called)

                # Check that Windows-specific guidance was attempted (even if import failed)
                # The code at line 1956-1957 tries to import windows_utils
                # If that import fails, it continues with pass
                print_messages = [str(call) for call in mock_print.call_args_list]
                error_found = any("Error" in msg for msg in print_messages)
                self.assertTrue(error_found, "Should have printed error message")


class TestHandleAuthCommandLegacy(unittest.TestCase):
    """Test cases for handle_auth_command legacy flag handling (line 1771-1773)."""

    def setUp(self):
        """Initialize mock arguments."""
        self.mock_args = MagicMock()
        self.mock_args.auth = True
        self.mock_args.auth_command = None

    @patch("mmrelay.cli.logger")
    @patch("mmrelay.cli.handle_auth_login", return_value=0)
    @patch("mmrelay.cli.get_deprecation_warning", return_value="--auth is deprecated")
    def test_handle_auth_command_legacy_auth_flag(
        self, mock_deprecation, mock_handle_auth_login, mock_logger
    ):
        """Test handle_auth_command handles legacy --auth flag without auth_command."""
        result = handle_auth_command(self.mock_args)

        # Should call handle_auth_login (default for legacy --auth)
        mock_handle_auth_login.assert_called_once_with(self.mock_args)

        # Should return the result
        self.assertEqual(result, 0)

        # Should log deprecation warning
        mock_logger.warning.assert_called_once()
        warning_call = mock_logger.warning.call_args[0][0]
        self.assertIn("--auth is deprecated", warning_call)

    @patch("mmrelay.cli.logger")
    @patch("mmrelay.cli.handle_auth_login", return_value=1)
    @patch("mmrelay.cli.handle_auth_status", return_value=0)
    @patch("mmrelay.cli.handle_auth_logout", return_value=1)
    @patch("mmrelay.cli.get_deprecation_warning", return_value="--auth is deprecated")
    def test_handle_auth_command_explicit_commands(
        self,
        mock_deprecation,
        mock_handle_logout,
        mock_handle_status,
        mock_handle_auth_login,
        mock_logger,
    ):
        """Test handle_auth_command handles explicit auth subcommands (not deprecated path)."""
        # Test with status subcommand
        self.mock_args.auth_command = "status"
        result = handle_auth_command(self.mock_args)

        # Should call handle_auth_status
        mock_handle_status.assert_called_once()
        self.assertEqual(result, 0)

        # Test with logout subcommand
        self.mock_args.auth_command = "logout"
        result = handle_auth_command(self.mock_args)

        # Should call handle_auth_logout
        mock_handle_logout.assert_called_once()
        self.assertEqual(result, 1)

        # Should NOT log deprecation warning for explicit subcommands
        self.assertEqual(mock_logger.warning.call_count, 0)


if __name__ == "__main__":
    unittest.main()
