"""Tests for CLI exception logging improvements."""

import unittest
from unittest.mock import MagicMock, patch

from mmrelay.cli import handle_auth_login


class TestCLIExceptionLogging(unittest.TestCase):
    """Test CLI exception logging improvements."""

    @patch("mmrelay.cli.logger")
    @patch("mmrelay.config.check_e2ee_enabled_silently")
    @patch("builtins.print")
    def test_silent_e2ee_check_exception_logging(self, mock_print, mock_check_e2ee, mock_logger):
        """Test that exceptions in silent E2EE check are logged."""
        # Mock the silent E2EE check to raise an exception
        mock_check_e2ee.side_effect = RuntimeError("Test exception")
        
        # Mock args
        mock_args = MagicMock()
        mock_args.homeserver = "https://matrix.example.com"
        mock_args.username = "test"
        mock_args.password = "test"
        
        # Mock the login function to avoid actual login
        with patch("mmrelay.cli.asyncio.run") as mock_run:
            mock_run.return_value = True
            handle_auth_login(mock_args)
        
        # Should log the exception
        mock_logger.debug.assert_called_with("Failed to silently check E2EE status: Test exception")
        
        # Should still print the authentication header
        mock_print.assert_any_call("\nMatrix Bot Authentication")
        mock_print.assert_any_call("=========================")

    @patch("mmrelay.cli.logger")
    @patch("mmrelay.config.check_e2ee_enabled_silently")
    @patch("builtins.print")
    def test_silent_e2ee_check_success_no_logging(self, mock_print, mock_check_e2ee, mock_logger):
        """Test that successful silent E2EE check doesn't log errors."""
        # Mock successful E2EE check
        mock_check_e2ee.return_value = True
        
        # Mock args
        mock_args = MagicMock()
        mock_args.homeserver = "https://matrix.example.com"
        mock_args.username = "test"
        mock_args.password = "test"
        
        # Mock the login function to avoid actual login
        with patch("mmrelay.cli.asyncio.run") as mock_run:
            mock_run.return_value = True
            handle_auth_login(mock_args)
        
        # Should not log any debug messages about failures
        mock_logger.debug.assert_not_called()


if __name__ == "__main__":
    unittest.main()
