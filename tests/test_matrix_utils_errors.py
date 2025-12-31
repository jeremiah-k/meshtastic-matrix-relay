from unittest.mock import MagicMock, patch

from mmrelay.matrix_utils import (
    _get_detailed_matrix_error_message,
    _get_e2ee_error_message,
)


class TestGetDetailedSyncErrorMessage:
    """Test cases for _get_detailed_matrix_error_message function."""

    def test_sync_error_with_message_string(self):
        """Test error response with string message."""
        mock_response = MagicMock()
        mock_response.message = "Connection failed"

        result = _get_detailed_matrix_error_message(mock_response)
        assert result == "Connection failed"

    def test_sync_error_with_status_code_401(self):
        """Test error response with 401 status code."""
        mock_response = MagicMock()
        # Configure without a usable message attribute to test status code path
        mock_response.message = None
        mock_response.status_code = 401

        result = _get_detailed_matrix_error_message(mock_response)
        assert result == "Authentication failed - invalid or expired credentials"

    def test_sync_error_with_status_code_403(self):
        """Test error response with 403 status code."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 403

        result = _get_detailed_matrix_error_message(mock_response)
        assert result == "Access forbidden - check user permissions"

    def test_sync_error_with_status_code_404(self):
        """Test error response with 404 status code."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 404

        result = _get_detailed_matrix_error_message(mock_response)
        assert result == "Server not found - check homeserver URL"

    def test_sync_error_with_status_code_429(self):
        """Test error response with 429 status code."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 429

        result = _get_detailed_matrix_error_message(mock_response)
        assert result == "Rate limited - too many requests"

    def test_sync_error_with_status_code_500(self):
        """Test error response with 500 status code."""
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = 500

        result = _get_detailed_matrix_error_message(mock_response)
        assert (
            result
            == "Server error (HTTP 500) - the Matrix server is experiencing issues"
        )

    def test_sync_error_with_bytes_response(self):
        """Test error response as raw bytes."""
        response_bytes = b"Server error"

        result = _get_detailed_matrix_error_message(response_bytes)
        assert result == "Server error"

    def test_sync_error_with_bytes_invalid_utf8(self):
        """Test error response as invalid UTF-8 bytes."""
        response_bytes = b"\xff\xfe\xfd"

        result = _get_detailed_matrix_error_message(response_bytes)
        assert (
            result == "Network connectivity issue or server unreachable (binary data)"
        )

    def test_sync_error_with_bytearray_response(self):
        """Test error response as bytearray."""
        response_bytes = bytearray(b"Server error")

        result = _get_detailed_matrix_error_message(response_bytes)
        assert result == "Server error"

    def test_sync_error_fallback_generic(self):
        """Test generic fallback when no specific info can be extracted."""
        mock_response = MagicMock()
        # Remove all attributes and make string representation fail
        mock_response.message = None
        mock_response.status_code = None
        mock_response.transport_response = None
        mock_response.__str__ = MagicMock(
            side_effect=Exception("String conversion failed")
        )

        result = _get_detailed_matrix_error_message(mock_response)
        assert result == "Network connectivity issue or server unreachable"

    def test_get_detailed_matrix_error_message_transport_response(self):
        """Test _get_detailed_matrix_error_message with transport_response."""
        # Test with transport_response having status_code
        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = None
        mock_response.transport_response = MagicMock()
        mock_response.transport_response.status_code = 502

        result = _get_detailed_matrix_error_message(mock_response)
        assert result == "Transport error: HTTP 502"

    def test_get_detailed_matrix_error_message_string_fallback(self):
        """Test _get_detailed_matrix_error_message string fallback."""
        # Test with string that has object repr
        result = _get_detailed_matrix_error_message("<object at 0x123>")
        assert result == "Network connectivity issue or server unreachable"

        # Test with HTML-like content
        result = _get_detailed_matrix_error_message("<html>Error</html>")
        assert result == "Network connectivity issue or server unreachable"

        # Test with "unknown error"
        result = _get_detailed_matrix_error_message("Unknown error occurred")
        assert result == "Network connectivity issue or server unreachable"

        # Test with normal string
        result = _get_detailed_matrix_error_message("Some error message")
        assert result == "Some error message"


def test_get_e2ee_error_message():
    """Test _get_e2ee_error_message returns appropriate error message."""
    with (
        patch("mmrelay.matrix_utils.config", {"test": "config"}),
        patch("mmrelay.config.config_path", "/test/path"),
        patch("mmrelay.e2ee_utils.get_e2ee_status") as mock_get_status,
        patch("mmrelay.e2ee_utils.get_e2ee_error_message") as mock_get_error,
    ):
        mock_get_status.return_value = {"status": "test"}
        mock_get_error.return_value = "Test E2EE error message"

        result = _get_e2ee_error_message()

        assert result == "Test E2EE error message"
        mock_get_status.assert_called_once_with({"test": "config"}, "/test/path")
        mock_get_error.assert_called_once_with({"status": "test"})
