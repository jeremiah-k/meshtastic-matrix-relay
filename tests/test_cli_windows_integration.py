"""
Test cases for CLI Windows integration functionality.

This module tests the Windows-specific enhancements added to the CLI,
including console setup, error handling, and Windows-specific guidance.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.cli import generate_sample_config, main


class TestCLIWindowsConsoleSetup(unittest.TestCase):
    """Test cases for Windows console setup in CLI main function."""

    @patch("sys.platform", "win32")
    @patch("os.name", "nt")  # Also mock os.name to make is_windows() return True
    @patch("mmrelay.windows_utils.setup_windows_console")
    @patch("mmrelay.cli.parse_arguments")
    def test_main_calls_windows_console_setup(
        self, mock_parse_args, mock_setup_console
    ):
        """Test that main() calls Windows console setup on Windows."""
        # Mock parse_arguments to return minimal args and exit early
        mock_args = MagicMock()
        mock_args.command = None
        mock_args.generate_config = False
        mock_args.check_config = False
        mock_args.install_service = (
            False  # Ensure service installation is not triggered
        )
        mock_args.auth = None
        mock_args.version = False
        mock_parse_args.return_value = mock_args

        # Mock run_main to avoid full execution
        with patch("mmrelay.main.run_main", return_value=0):
            result = main()

        # Should call Windows console setup
        mock_setup_console.assert_called_once()
        self.assertEqual(result, 0)

    @patch("sys.platform", "linux")
    @patch("mmrelay.cli.parse_arguments")
    def test_main_skips_windows_console_setup_on_linux(self, mock_parse_args):
        """Test that main() skips Windows console setup on non-Windows platforms."""
        # Mock parse_arguments to return minimal args and exit early
        mock_args = MagicMock()
        mock_args.command = None
        mock_args.generate_config = False
        mock_args.check_config = False
        mock_args.install_service = (
            False  # Ensure service installation is not triggered
        )
        mock_args.auth = None
        mock_args.version = False
        mock_parse_args.return_value = mock_args

        # Mock run_main to avoid full execution
        with patch("mmrelay.main.run_main", return_value=0):
            result = main()

        # Should not attempt to import windows_utils
        # (This is implicit - if it tried to import, it would succeed but not call setup)
        self.assertEqual(result, 0)

    @patch("sys.platform", "win32")
    @patch("mmrelay.cli.parse_arguments")
    def test_main_handles_windows_utils_import_error(self, mock_parse_args):
        """Test that main() handles ImportError when windows_utils is not available."""
        # Mock parse_arguments to return minimal args and exit early
        mock_args = MagicMock()
        mock_args.command = None
        mock_args.generate_config = False
        mock_args.check_config = False
        mock_args.install_service = (
            False  # Ensure service installation is not triggered
        )
        mock_args.auth = None
        mock_args.version = False
        mock_parse_args.return_value = mock_args

        # Mock windows_utils import to fail specifically in the main function
        with patch.dict("sys.modules", {"mmrelay.windows_utils": None}):
            with patch("mmrelay.main.run_main", return_value=0):
                result = main()

        # Should continue without error
        self.assertEqual(result, 0)


class TestCLIWindowsErrorHandling(unittest.TestCase):
    """Test cases for Windows-specific error handling in CLI functions."""

    @patch("sys.platform", "win32")
    @patch("mmrelay.windows_utils.is_windows", return_value=True)
    @patch("mmrelay.windows_utils.get_windows_error_message")
    @patch("builtins.print")
    def test_generate_config_windows_error_handling(
        self, mock_print, mock_get_error, mock_is_windows
    ):
        """Test Windows-specific error handling in generate_sample_config."""
        mock_get_error.return_value = "Windows-specific error guidance"

        # Mock file operations to fail with OSError
        with patch(
            "mmrelay.cli.get_config_paths", return_value=["/test/config.yaml"]
        ), patch("os.path.isfile", return_value=False), patch(
            "mmrelay.tools.get_sample_config_path", side_effect=OSError("Access denied")
        ):

            result = generate_sample_config()

        # Should fail gracefully
        self.assertFalse(result)

        # Should call Windows error message handler
        mock_get_error.assert_called()

    @patch("sys.platform", "win32")
    @patch("mmrelay.windows_utils.is_windows", return_value=True)
    @patch("builtins.print")
    def test_generate_config_windows_troubleshooting_guidance(
        self, mock_print, _mock_is_windows
    ):
        """Test that generate_sample_config provides Windows troubleshooting guidance."""
        # Mock all config generation methods to fail
        with patch(
            "mmrelay.cli.get_config_paths", return_value=["/test/config.yaml"]
        ), patch("os.path.isfile", return_value=False), patch(
            "mmrelay.cli.get_sample_config_path",
            return_value="/nonexistent/sample_config.yaml",
        ), patch(
            "os.path.exists", return_value=False
        ), patch(
            "importlib.resources.files", side_effect=ImportError()
        ), patch(
            "mmrelay.cli._get_minimal_config_template", side_effect=OSError()
        ):

            result = generate_sample_config()

        # Should fail
        self.assertFalse(result)

        # Should print Windows troubleshooting guidance
        printed_messages = [
            call.args[0] for call in mock_print.call_args_list if call.args
        ]
        guidance_printed = any(
            "Windows Troubleshooting:" in str(msg) for msg in printed_messages
        )
        self.assertTrue(guidance_printed)

    @patch("sys.platform", "linux")
    @patch("builtins.print")
    def test_generate_config_no_windows_guidance_on_linux(self, mock_print):
        """Test that generate_sample_config doesn't provide Windows guidance on Linux."""
        # Mock all config generation methods to fail
        with patch(
            "mmrelay.cli.get_config_paths", return_value=["/test/config.yaml"]
        ), patch("os.path.isfile", return_value=False), patch(
            "mmrelay.cli.get_sample_config_path",
            return_value="/nonexistent/sample_config.yaml",
        ), patch(
            "os.path.exists", return_value=False
        ), patch(
            "importlib.resources.files", side_effect=ImportError()
        ), patch(
            "mmrelay.cli._get_minimal_config_template", side_effect=OSError()
        ):

            result = generate_sample_config()

        # Should fail
        self.assertFalse(result)

        # Should NOT print Windows troubleshooting guidance
        printed_messages = [
            call.args[0] for call in mock_print.call_args_list if call.args
        ]
        guidance_printed = any(
            "Windows Troubleshooting:" in str(msg) for msg in printed_messages
        )
        self.assertFalse(guidance_printed)


class TestCLIAuthLoginEnhancements(unittest.TestCase):
    """Test cases for enhanced auth login functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_args = MagicMock()
        self.mock_args.homeserver = None
        self.mock_args.username = None
        self.mock_args.password = None

    @patch("mmrelay.config.check_e2ee_enabled_silently")
    @patch("mmrelay.matrix_utils.login_matrix_bot")
    @patch("builtins.print")
    def test_auth_login_e2ee_enabled_banner(
        self, mock_print, mock_login, mock_check_e2ee
    ):
        """Test that auth login shows E2EE banner when E2EE is enabled."""
        from mmrelay.cli import handle_auth_login

        # Mock E2EE enabled
        mock_check_e2ee.return_value = True

        # Mock the login function following the testing guide pattern
        mock_login.return_value = True

        handle_auth_login(self.mock_args)

        # Should print E2EE banner
        printed_messages = [
            call.args[0] for call in mock_print.call_args_list if call.args
        ]
        e2ee_banner = any(
            "Matrix Bot Authentication for E2EE" in str(msg) for msg in printed_messages
        )
        self.assertTrue(e2ee_banner)

    @patch("mmrelay.config.check_e2ee_enabled_silently")
    @patch("mmrelay.matrix_utils.login_matrix_bot")
    @patch("builtins.print")
    def test_auth_login_no_e2ee_banner_when_disabled(
        self, mock_print, mock_login, mock_check_e2ee
    ):
        """Test that auth login doesn't show E2EE banner when E2EE is disabled."""
        from mmrelay.cli import handle_auth_login

        # Mock E2EE disabled
        mock_check_e2ee.return_value = False

        # Mock the login function following the testing guide pattern
        mock_login.return_value = True

        handle_auth_login(self.mock_args)

        # Should print standard banner
        printed_messages = [
            call.args[0] for call in mock_print.call_args_list if call.args
        ]
        standard_banner = any(
            "Matrix Bot Authentication" in str(msg) and "E2EE" not in str(msg)
            for msg in printed_messages
        )
        self.assertTrue(standard_banner)

    @patch("mmrelay.config.load_config")
    @patch("mmrelay.matrix_utils.login_matrix_bot")
    @patch("builtins.print")
    def test_auth_login_handles_config_load_error(
        self, mock_print, mock_login, mock_load_config
    ):
        """Test that auth login handles config loading errors gracefully."""
        from mmrelay.cli import handle_auth_login

        # Mock config loading to fail
        mock_load_config.side_effect = Exception("Config load failed")

        # Mock the login function to return a regular value (not a coroutine)
        # Following the testing guide pattern for async functions called via asyncio.run()
        mock_login.return_value = True

        handle_auth_login(self.mock_args)

        # Should still show standard banner (fallback behavior)
        printed_messages = [
            call.args[0] for call in mock_print.call_args_list if call.args
        ]
        banner_shown = any(
            "Matrix Bot Authentication" in str(msg) for msg in printed_messages
        )
        self.assertTrue(banner_shown)


class TestCLIE2EEValidation(unittest.TestCase):
    """Test cases for E2EE dependency validation improvements."""

    @patch("builtins.print")
    def test_e2ee_validation_improved_error_message(self, mock_print):
        """Test that E2EE validation shows improved error messages."""
        from mmrelay.cli import _validate_e2ee_dependencies

        # Mock import to fail
        with patch(
            "builtins.__import__", side_effect=ImportError("No module named 'olm'")
        ):
            result = _validate_e2ee_dependencies()

        # Should return False
        self.assertFalse(result)

        # Should print improved error messages
        printed_messages = [
            call.args[0] for call in mock_print.call_args_list if call.args
        ]

        # Check for specific improved messages
        error_msg_found = any(
            "E2EE dependencies not installed" in str(msg) for msg in printed_messages
        )
        guidance_found = any(
            "End-to-end encryption features require additional dependencies" in str(msg)
            for msg in printed_messages
        )
        install_cmd_found = any(
            "pipx install 'mmrelay[e2e]'" in str(msg) for msg in printed_messages
        )

        self.assertTrue(error_msg_found)
        self.assertTrue(guidance_found)
        self.assertTrue(install_cmd_found)


if __name__ == "__main__":
    unittest.main()
