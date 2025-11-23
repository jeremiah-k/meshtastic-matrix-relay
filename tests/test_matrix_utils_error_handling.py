"""
Test cases for enhanced Matrix utilities error handling.

This module tests the improved error handling and troubleshooting guidance
added to matrix_utils.py for better user experience.
"""

import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.matrix_utils import _get_detailed_matrix_error_message


class FakeNioErrorResponse:
    """Fake nio.ErrorResponse for testing."""

    def __init__(self, message=None, status_code=None):
        """
        Initialize the error-like object.

        Parameters:
            message (str|None): Human-readable error message, if available.
            status_code (int|None): HTTP status code associated with the error, if known.
        """
        self.message = message
        self.status_code = status_code


class FakeNioErrorResponseWithException:
    """Fake nio.ErrorResponse that raises exception when accessing message."""

    def __init__(self, status_code=None):
        """
        Initialize the fake nio.ErrorResponse used in tests.

        Parameters:
            status_code (int | None): Optional HTTP status code to simulate (for example, 401, 404). Stored as the instance's `status_code` attribute.
        """
        self.status_code = status_code

    @property
    def message(self):
        """
        Simulate a failing `.message` attribute by raising AttributeError when accessed.

        Used in tests as a test double that mimics an object whose `message` attribute access raises
        AttributeError("Test exception"), allowing verification of error-handling paths that must
        gracefully handle attribute access errors.
        """
        raise AttributeError("Test exception")


class TestDetailedSyncErrorMessage(unittest.TestCase):
    """Test cases for _get_detailed_matrix_error_message function."""

    def setUp(self):
        """
        Set up a fake `nio` module in sys.modules for tests that rely on isinstance checks.

        Creates a temporary module named "nio" with an `ErrorResponse` dummy class, saves any existing
        `sys.modules["nio"]` value to `self.original_nio` for restoration, and installs the fake module
        at `sys.modules["nio"]`. This allows tests to run without the real `nio` package while enabling
        `isinstance(..., nio.ErrorResponse)` checks.
        """
        # Create fake nio module to avoid isinstance patching
        self.fake_nio = types.ModuleType("nio")

        class FakeErrorResponse:
            """Fake nio.ErrorResponse for isinstance checks."""

            pass

        self.fake_nio.ErrorResponse = FakeErrorResponse

        # Store original module if it exists
        self.original_nio = sys.modules.get("nio")

        # Install fake module
        sys.modules["nio"] = self.fake_nio

    def tearDown(self):
        """Restore original nio module."""
        if self.original_nio is not None:
            sys.modules["nio"] = self.original_nio
        else:
            sys.modules.pop("nio", None)

    def test_nio_error_response_with_message(self):
        """Test handling of nio ErrorResponse with message."""
        # Create a fake nio ErrorResponse
        mock_response = FakeNioErrorResponse(
            message="Authentication failed", status_code=401
        )

        result = _get_detailed_matrix_error_message(mock_response)
        self.assertEqual(result, "Authentication failed")

    def test_nio_error_response_with_status_code_only(self):
        """Test handling of nio ErrorResponse with status code but no message."""
        # Create a fake nio ErrorResponse with no message
        mock_response = FakeNioErrorResponse(message=None, status_code=404)

        result = _get_detailed_matrix_error_message(mock_response)
        # Since our fake class doesn't pass isinstance check, it falls through to generic handling
        # which provides more specific error messages for known status codes
        self.assertEqual(result, "Server not found - check homeserver URL")

    def test_nio_import_error_fallback(self):
        """Test fallback when nio is not available."""
        mock_response = MagicMock()
        mock_response.message = "Server error"

        # Mock nio import to fail
        with patch(
            "builtins.__import__", side_effect=ImportError("No module named 'nio'")
        ):
            result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(result, "Server error")

    def test_response_with_message_attribute(self):
        """Test handling of response with message attribute."""
        mock_response = MagicMock()
        mock_response.message = "Connection timeout"

        # Use fake nio module instead of patching isinstance
        result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(result, "Connection timeout")

    def test_response_with_status_code_401(self):
        """Test handling of 401 status code."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 401

        # Use fake nio module instead of patching isinstance
        result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(
            result, "Authentication failed - invalid or expired credentials"
        )

    def test_response_with_status_code_403(self):
        """Test handling of 403 status code."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 403

        # Use fake nio module instead of patching isinstance
        result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(result, "Access forbidden - check user permissions")

    def test_response_with_status_code_404(self):
        """Test handling of 404 status code."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 404

        # Use fake nio module instead of patching isinstance
        result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(result, "Server not found - check homeserver URL")

    def test_response_with_status_code_429(self):
        """Test handling of 429 status code."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 429

        # Use fake nio module instead of patching isinstance
        result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(result, "Rate limited - too many requests")

    def test_response_with_server_error_status_code(self):
        """Test handling of server error status codes (5xx)."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 502

        # Use fake nio module instead of patching isinstance
        result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(
            result, "Server error (HTTP 502) - the Matrix server is experiencing issues"
        )

    def test_response_with_other_status_code(self):
        """Test handling of other status codes."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 418

        # Use fake nio module instead of patching isinstance
        result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(result, "HTTP error 418")

    def test_response_with_transport_error(self):
        """Test handling of transport errors."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = None
        mock_response.transport_response = MagicMock()
        mock_response.transport_response.status_code = 0

        # Use fake nio module instead of patching isinstance
        result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(result, "Transport error: HTTP 0")

    def test_response_with_no_attributes(self):
        """Test handling of response with no useful attributes."""
        mock_response = MagicMock()
        # Remove all attributes including transport_response
        del mock_response.message
        del mock_response.status_code
        del mock_response.transport_response
        # Make str() return None to trigger the fallback
        mock_response.__str__ = MagicMock(return_value="None")

        # Use fake nio module instead of patching isinstance
        result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(
            result,
            "Unable to determine specific error - likely a network connectivity issue",
        )

    def test_exception_during_processing(self):
        """Test handling of exceptions during error message extraction."""
        # Use the fake class that raises an exception when accessing message
        mock_response = FakeNioErrorResponseWithException(status_code=None)

        with patch("mmrelay.matrix_utils.logger"):
            result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(
            result,
            "Unable to determine specific error - likely a network connectivity issue",
        )

    def test_message_bytes_decode_error(self):
        """Test handling of message attribute that is bytes but fails UTF-8 decode (lines 431-432)."""
        # Create object with message that is bytes but not valid UTF-8
        mock_response = MagicMock()
        mock_response.message = b"\xff\xfe\xfd"  # Invalid UTF-8 bytes
        mock_response.status_code = None

        result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(
            result,
            "Unable to determine specific error - likely a network connectivity issue",
        )

    def test_status_code_conversion_error(self):
        """Test handling of status_code that cannot be converted to int (lines 439-440)."""
        # Create object with status_code that raises exception when converted to int
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = "not_a_number"

        result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(
            result,
            "Unable to determine specific error - likely a network connectivity issue",
        )


class TestMatrixLoginErrorHandling(unittest.TestCase):
    """Test cases for enhanced Matrix login error handling."""

    def setUp(self):
        """
        Prepare test fixtures by patching mmrelay.matrix_utils.matrix_homeserver to "https://matrix.org" and starting the patcher.

        Stores the started patcher on self.patcher_homeserver and the mock value on self.mock_homeserver for use in tests and cleanup.
        """
        # Mock the global variables
        self.patcher_homeserver = patch(
            "mmrelay.matrix_utils.matrix_homeserver", "https://matrix.org"
        )
        self.mock_homeserver = self.patcher_homeserver.start()

    def tearDown(self):
        """
        Stop and remove the homeserver patch created in setUp.

        Called after each test to stop self.patcher_homeserver and restore the original matrix_homeserver value.
        """
        self.patcher_homeserver.stop()

    @patch("mmrelay.matrix_utils.logger")
    def test_login_error_401_troubleshooting(self, mock_logger):
        """Test that 401 errors provide specific troubleshooting guidance."""
        from unittest.mock import AsyncMock

        from mmrelay.matrix_utils import login_matrix_bot

        # Mock response with 401 error - ensure it doesn't have access_token or device_id
        mock_response = MagicMock()
        mock_response.message = "M_FORBIDDEN"
        mock_response.status_code = 401
        # Remove access_token and device_id to ensure it's treated as an error
        del mock_response.access_token
        del mock_response.device_id

        # Mock client and login response - use AsyncMock for async methods
        mock_client = AsyncMock()
        mock_client.login.return_value = mock_response
        mock_client.close = AsyncMock()

        with (
            patch("mmrelay.matrix_utils.AsyncClient", return_value=mock_client),
            patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
            patch("mmrelay.matrix_utils.save_credentials"),
            patch("mmrelay.matrix_utils.load_credentials", return_value=None),
        ):
            import asyncio

            result = asyncio.run(
                login_matrix_bot("https://matrix.org", "@user:matrix.org", "pass")
            )

        # Should return False
        self.assertFalse(result)

        # Should log specific troubleshooting guidance
        mock_logger.error.assert_any_call(
            "Authentication failed - invalid username or password."
        )
        mock_logger.error.assert_any_call("Troubleshooting steps:")
        mock_logger.error.assert_any_call(
            "1. Verify your username and password are correct"
        )

    @patch("mmrelay.matrix_utils.logger")
    def test_login_error_404_troubleshooting(self, mock_logger):
        """Test that 404 errors provide homeserver URL guidance."""
        from unittest.mock import AsyncMock

        from mmrelay.matrix_utils import login_matrix_bot

        # Mock response with 404 error - ensure it doesn't have access_token
        mock_response = MagicMock()
        mock_response.message = "Not found"
        mock_response.status_code = 404
        # Remove access_token and device_id to ensure it's treated as an error
        del mock_response.access_token
        del mock_response.device_id

        # Mock client and login response - use AsyncMock for async methods
        mock_client = AsyncMock()
        mock_client.login.return_value = mock_response
        mock_client.close = AsyncMock()

        with (
            patch("mmrelay.matrix_utils.AsyncClient", return_value=mock_client),
            patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
            patch("mmrelay.matrix_utils.save_credentials"),
            patch("mmrelay.matrix_utils.load_credentials", return_value=None),
        ):
            import asyncio

            result = asyncio.run(
                login_matrix_bot("https://matrix.org", "@user:matrix.org", "pass")
            )

        # Should return False
        self.assertFalse(result)

        # Should log homeserver URL guidance
        mock_logger.error.assert_any_call("User not found or homeserver not found.")
        mock_logger.error.assert_any_call(
            "Check that the homeserver URL is correct: https://matrix.org"
        )

    @patch("mmrelay.matrix_utils.logger")
    def test_login_error_429_troubleshooting(self, mock_logger):
        """Test that 429 errors provide rate limiting guidance."""
        from unittest.mock import AsyncMock

        from mmrelay.matrix_utils import login_matrix_bot

        # Mock response with 429 error - ensure it doesn't have access_token
        mock_response = MagicMock()
        mock_response.message = "Too many requests"
        mock_response.status_code = 429
        # Remove access_token and device_id to ensure it's treated as an error
        del mock_response.access_token
        del mock_response.device_id

        # Mock client and login response - use AsyncMock for async methods
        mock_client = AsyncMock()
        mock_client.login.return_value = mock_response
        mock_client.close = AsyncMock()

        with (
            patch("mmrelay.matrix_utils.AsyncClient", return_value=mock_client),
            patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
            patch("mmrelay.matrix_utils.save_credentials"),
            patch("mmrelay.matrix_utils.load_credentials", return_value=None),
        ):
            import asyncio

            result = asyncio.run(
                login_matrix_bot("https://matrix.org", "@user:matrix.org", "pass")
            )

        # Should return False
        self.assertFalse(result)

        # Should log rate limiting guidance
        mock_logger.error.assert_any_call("Rate limited - too many login attempts.")
        mock_logger.error.assert_any_call("Wait a few minutes before trying again.")

    @patch("mmrelay.matrix_utils.logger")
    def test_login_error_server_error_troubleshooting(self, mock_logger):
        """Test that server errors provide appropriate guidance."""
        from unittest.mock import AsyncMock

        from mmrelay.matrix_utils import login_matrix_bot

        # Mock response with server error - ensure it doesn't have access_token
        mock_response = MagicMock()
        mock_response.message = "Internal server error"
        mock_response.status_code = 500
        # Remove access_token and device_id to ensure it's treated as an error
        del mock_response.access_token
        del mock_response.device_id

        # Mock client and login response - use AsyncMock for async methods
        mock_client = AsyncMock()
        mock_client.login.return_value = mock_response
        mock_client.close = AsyncMock()

        with (
            patch("mmrelay.matrix_utils.AsyncClient", return_value=mock_client),
            patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
            patch("mmrelay.matrix_utils.save_credentials"),
            patch("mmrelay.matrix_utils.load_credentials", return_value=None),
        ):
            import asyncio

            result = asyncio.run(
                login_matrix_bot("https://matrix.org", "@user:matrix.org", "pass")
            )

        # Should return False
        self.assertFalse(result)

        # Should log server error guidance
        mock_logger.error.assert_any_call(
            "Matrix server error - the server is experiencing issues."
        )
        mock_logger.error.assert_any_call(
            "Try again later or contact your server administrator."
        )


if __name__ == "__main__":
    unittest.main()
