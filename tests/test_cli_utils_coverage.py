#!/usr/bin/env python3
"""
Additional test suite for uncovered lines in CLI utilities in MMRelay.

This file contains tests for previously uncovered code paths including:
- temp_client close exception in logout_matrix_bot (lines 695-700)
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestLogoutMatrixBotTempClientClose(unittest.TestCase):
    """Test cases for temp_client close exception handling in logout_matrix_bot."""

    def setUp(self):
        """Initialize test fixtures."""
        import mmrelay.cli_utils

        # Set up mock credentials
        self.mock_credentials = {
            "homeserver": "https://matrix.org",
            "user_id": "@test:matrix.org",
            "device_id": "DEVICE123",
            "access_token": "test_token",
        }

    @patch("mmrelay.cli_utils.load_credentials")
    @patch("mmrelay.cli_utils._cleanup_local_session_data", return_value=True)
    @patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
    @patch("mmrelay.cli_utils.AsyncClient")
    @patch("mmrelay.cli_utils.logger")
    def test_logout_matrix_bot_temp_client_close_exception(
        self, mock_logger, mock_async_client, mock_ssl_context, mock_cleanup, mock_load
    ):
        """Test logout_matrix_bot handles temp_client.close() exception gracefully."""
        from mmrelay.cli_utils import logout_matrix_bot

        # Set up mocks
        mock_load.return_value = self.mock_credentials

        # Mock temp_client to raise exception on close
        temp_client = MagicMock()
        temp_client.close = AsyncMock(side_effect=Exception("Close failed"))
        temp_client.whoami = AsyncMock(
            return_value=MagicMock(user_id="@test:matrix.org")
        )

        # Mock main_client for logout
        main_client = MagicMock()
        main_client.restore_login = MagicMock()
        main_client.logout = AsyncMock(
            return_value=MagicMock(transport_response=MagicMock())
        )
        main_client.close = AsyncMock()

        # Make AsyncClient return different clients
        def async_client_init(*args, **kwargs):
            if len(args) == 1 and "https" in args[0]:
                # First call creates temp_client for whoami
                return temp_client
            else:
                # Second call creates main_client for logout
                return main_client

        mock_async_client.side_effect = async_client_init

        # Run the async function
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(logout_matrix_bot("test_password"))
        finally:
            loop.close()

        # Should complete successfully despite close exception
        self.assertTrue(result)

        # Should have logged debug message about close exception
        mock_logger.debug.assert_called()
        debug_calls = [
            call
            for call in mock_logger.debug.call_args_list
            if "Ignoring error while closing temporary Matrix client" in str(call)
        ]
        self.assertEqual(len(debug_calls), 1)

    @patch("mmrelay.cli_utils.load_credentials", return_value=None)
    @patch("mmrelay.cli_utils._cleanup_local_session_data", return_value=True)
    @patch("mmrelay.cli_utils.AsyncClient")
    @patch("mmrelay.cli_utils.logger")
    def test_logout_matrix_bot_no_credentials(
        self, mock_logger, mock_async_client, mock_cleanup, mock_load
    ):
        """Test logout_matrix_bot with no credentials (doesn't create temp_client)."""
        from mmrelay.cli_utils import logout_matrix_bot

        # Run the async function
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(logout_matrix_bot("test_password"))
        finally:
            loop.close()

        # Should succeed with local cleanup only
        self.assertTrue(result)

        # Should NOT attempt to close any temp_client
        # since no credentials were found
        self.assertFalse(mock_async_client.called)

    @patch("mmrelay.cli_utils.load_credentials")
    @patch("mmrelay.cli_utils._cleanup_local_session_data", return_value=True)
    @patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
    @patch("mmrelay.cli_utils.AsyncClient")
    @patch("mmrelay.cli_utils.logger")
    def test_logout_matrix_bot_temp_client_close_multiple_exceptions(
        self, mock_logger, mock_async_client, mock_ssl_context, mock_cleanup, mock_load
    ):
        """Test logout_matrix_bot handles exceptions from both temp_client closes."""
        from mmrelay.cli_utils import logout_matrix_bot

        # Set up mocks
        mock_load.return_value = self.mock_credentials

        # Mock temp_client to raise exception on close
        temp_client = MagicMock()
        close_exception = Exception("Close failed")
        temp_client.close = AsyncMock(side_effect=close_exception)
        temp_client.whoami = AsyncMock(
            return_value=MagicMock(user_id="@test:matrix.org")
        )

        # Mock main_client
        main_client = MagicMock()
        main_client.restore_login = MagicMock()
        main_client.logout = AsyncMock(
            return_value=MagicMock(transport_response=MagicMock())
        )
        main_client.close = AsyncMock()

        # Make AsyncClient return different clients
        def async_client_init(*args, **kwargs):
            if len(args) == 1 and "https" in args[0]:
                return temp_client
            else:
                return main_client

        mock_async_client.side_effect = async_client_init

        # Run the async function
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(logout_matrix_bot("test_password"))
        finally:
            loop.close()

        # Should complete successfully
        self.assertTrue(result)

        # Should have logged debug message for temp_client close exception
        debug_calls = [
            call
            for call in mock_logger.debug.call_args_list
            if "closing temporary Matrix client" in str(call)
        ]
        self.assertEqual(len(debug_calls), 1)


if __name__ == "__main__":
    unittest.main()
