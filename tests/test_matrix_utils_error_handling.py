"""
Test cases for enhanced Matrix utilities error handling.

This module tests the improved error handling and troubleshooting guidance
added to matrix_utils.py for better user experience.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.matrix_utils import _get_detailed_sync_error_message


class TestDetailedSyncErrorMessage(unittest.TestCase):
    """Test cases for _get_detailed_sync_error_message function."""

    def test_nio_error_response_with_message(self):
        """Test handling of nio ErrorResponse with message."""
        # Mock nio ErrorResponse
        mock_response = MagicMock()
        mock_response.message = "Authentication failed"
        mock_response.status_code = 401
        
        # Mock isinstance to return True for NioErrorResponse
        with patch("mmrelay.matrix_utils.isinstance", return_value=True):
            result = _get_detailed_sync_error_message(mock_response)
        
        self.assertEqual(result, "Authentication failed")

    def test_nio_error_response_with_status_code_only(self):
        """Test handling of nio ErrorResponse with status code but no message."""
        # Mock nio ErrorResponse
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 404
        
        # Mock isinstance to return True for NioErrorResponse
        with patch("mmrelay.matrix_utils.isinstance", return_value=True):
            result = _get_detailed_sync_error_message(mock_response)
        
        self.assertEqual(result, "HTTP error 404")

    def test_nio_import_error_fallback(self):
        """Test fallback when nio is not available."""
        mock_response = MagicMock()
        mock_response.message = "Server error"
        
        # Mock nio import to fail
        with patch("builtins.__import__", side_effect=ImportError("No module named 'nio'")):
            result = _get_detailed_sync_error_message(mock_response)
        
        self.assertEqual(result, "Server error")

    def test_response_with_message_attribute(self):
        """Test handling of response with message attribute."""
        mock_response = MagicMock()
        mock_response.message = "Connection timeout"
        
        result = _get_detailed_sync_error_message(mock_response)
        
        self.assertEqual(result, "Connection timeout")

    def test_response_with_status_code_401(self):
        """Test handling of 401 status code."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 401
        
        result = _get_detailed_sync_error_message(mock_response)
        
        self.assertEqual(result, "Authentication failed - invalid or expired credentials")

    def test_response_with_status_code_403(self):
        """Test handling of 403 status code."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 403
        
        result = _get_detailed_sync_error_message(mock_response)
        
        self.assertEqual(result, "Access forbidden - check user permissions")

    def test_response_with_status_code_404(self):
        """Test handling of 404 status code."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 404
        
        result = _get_detailed_sync_error_message(mock_response)
        
        self.assertEqual(result, "Server not found - check homeserver URL")

    def test_response_with_status_code_429(self):
        """Test handling of 429 status code."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 429
        
        result = _get_detailed_sync_error_message(mock_response)
        
        self.assertEqual(result, "Rate limited - too many requests")

    def test_response_with_server_error_status_code(self):
        """Test handling of server error status codes (5xx)."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 502
        
        result = _get_detailed_sync_error_message(mock_response)
        
        self.assertEqual(result, "Server error (HTTP 502) - the Matrix server is experiencing issues")

    def test_response_with_other_status_code(self):
        """Test handling of other status codes."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 418
        
        result = _get_detailed_sync_error_message(mock_response)
        
        self.assertEqual(result, "HTTP error 418")

    def test_response_with_transport_error(self):
        """Test handling of transport errors."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = None
        mock_response.transport_response = MagicMock()
        mock_response.transport_response.status_code = 0
        
        result = _get_detailed_sync_error_message(mock_response)
        
        self.assertEqual(result, "Network connectivity issue or server unreachable")

    def test_response_with_no_attributes(self):
        """Test handling of response with no useful attributes."""
        mock_response = MagicMock()
        # Remove all attributes
        del mock_response.message
        del mock_response.status_code
        
        result = _get_detailed_sync_error_message(mock_response)
        
        self.assertEqual(result, "Network connectivity issue or server unreachable")

    def test_exception_during_processing(self):
        """Test handling of exceptions during error message extraction."""
        mock_response = MagicMock()
        # Make accessing message raise an exception
        mock_response.message = property(lambda self: 1/0)
        
        with patch("mmrelay.matrix_utils.logger") as mock_logger:
            result = _get_detailed_sync_error_message(mock_response)
        
        self.assertEqual(result, "Unable to determine specific error - likely a network connectivity issue")
        mock_logger.debug.assert_called()


class TestMatrixLoginErrorHandling(unittest.TestCase):
    """Test cases for enhanced Matrix login error handling."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock the global variables
        self.patcher_homeserver = patch("mmrelay.matrix_utils.matrix_homeserver", "https://matrix.org")
        self.mock_homeserver = self.patcher_homeserver.start()

    def tearDown(self):
        """Clean up patches."""
        self.patcher_homeserver.stop()

    @patch("mmrelay.matrix_utils.logger")
    def test_login_error_401_troubleshooting(self, mock_logger):
        """Test that 401 errors provide specific troubleshooting guidance."""
        from mmrelay.matrix_utils import login_matrix_bot
        
        # Mock response with 401 error
        mock_response = MagicMock()
        mock_response.message = "M_FORBIDDEN"
        mock_response.status_code = 401
        
        # Mock client and login response
        mock_client = MagicMock()
        mock_client.login.return_value = mock_response
        mock_client.close = MagicMock()
        
        with patch("mmrelay.matrix_utils.AsyncClient", return_value=mock_client):
            import asyncio
            result = asyncio.run(login_matrix_bot("user", "pass", "device"))
        
        # Should return False
        self.assertFalse(result)
        
        # Should log specific troubleshooting guidance
        mock_logger.error.assert_any_call("Authentication failed - invalid username or password.")
        mock_logger.error.assert_any_call("Troubleshooting steps:")
        mock_logger.error.assert_any_call("1. Verify your username and password are correct")

    @patch("mmrelay.matrix_utils.logger")
    def test_login_error_404_troubleshooting(self, mock_logger):
        """Test that 404 errors provide homeserver URL guidance."""
        from mmrelay.matrix_utils import login_matrix_bot
        
        # Mock response with 404 error
        mock_response = MagicMock()
        mock_response.message = "Not found"
        mock_response.status_code = 404
        
        # Mock client and login response
        mock_client = MagicMock()
        mock_client.login.return_value = mock_response
        mock_client.close = MagicMock()
        
        with patch("mmrelay.matrix_utils.AsyncClient", return_value=mock_client):
            import asyncio
            result = asyncio.run(login_matrix_bot("user", "pass", "device"))
        
        # Should return False
        self.assertFalse(result)
        
        # Should log homeserver URL guidance
        mock_logger.error.assert_any_call("User not found or homeserver not found.")
        mock_logger.error.assert_any_call("Check that the homeserver URL is correct: https://matrix.org")

    @patch("mmrelay.matrix_utils.logger")
    def test_login_error_429_troubleshooting(self, mock_logger):
        """Test that 429 errors provide rate limiting guidance."""
        from mmrelay.matrix_utils import login_matrix_bot
        
        # Mock response with 429 error
        mock_response = MagicMock()
        mock_response.message = "Too many requests"
        mock_response.status_code = 429
        
        # Mock client and login response
        mock_client = MagicMock()
        mock_client.login.return_value = mock_response
        mock_client.close = MagicMock()
        
        with patch("mmrelay.matrix_utils.AsyncClient", return_value=mock_client):
            import asyncio
            result = asyncio.run(login_matrix_bot("user", "pass", "device"))
        
        # Should return False
        self.assertFalse(result)
        
        # Should log rate limiting guidance
        mock_logger.error.assert_any_call("Rate limited - too many login attempts.")
        mock_logger.error.assert_any_call("Wait a few minutes before trying again.")

    @patch("mmrelay.matrix_utils.logger")
    def test_login_error_server_error_troubleshooting(self, mock_logger):
        """Test that server errors provide appropriate guidance."""
        from mmrelay.matrix_utils import login_matrix_bot
        
        # Mock response with server error
        mock_response = MagicMock()
        mock_response.message = "Internal server error"
        mock_response.status_code = 500
        
        # Mock client and login response
        mock_client = MagicMock()
        mock_client.login.return_value = mock_response
        mock_client.close = MagicMock()
        
        with patch("mmrelay.matrix_utils.AsyncClient", return_value=mock_client):
            import asyncio
            result = asyncio.run(login_matrix_bot("user", "pass", "device"))
        
        # Should return False
        self.assertFalse(result)
        
        # Should log server error guidance
        mock_logger.error.assert_any_call("Matrix server error - the server is experiencing issues.")
        mock_logger.error.assert_any_call("Try again later or contact your server administrator.")


if __name__ == "__main__":
    unittest.main()
