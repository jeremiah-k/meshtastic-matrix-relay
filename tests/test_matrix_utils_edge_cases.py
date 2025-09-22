#!/usr/bin/env python3
"""
Test suite for Matrix utilities edge cases and error handling in MMRelay.

Tests edge cases and error handling including:
- SSL context creation failures
- Connection timeout scenarios
- Invalid configuration handling
- Message formatting with malformed data
- Room joining failures and retries
- Prefix formatting with invalid templates
- Unicode handling in message truncation
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.matrix_utils import (
    connect_matrix,
    get_matrix_prefix,
    get_meshtastic_prefix,
    join_matrix_room,
    truncate_message,
    validate_prefix_format,
)


class TestMatrixUtilsEdgeCases(unittest.TestCase):
    """Test cases for Matrix utilities edge cases and error handling."""

    def setUp(self):
        """
        Resets global state in the mmrelay.matrix_utils module before each test.

        Ensures that the matrix_client and config variables are cleared to prevent test interference.
        """
        # Reset global state
        import mmrelay.matrix_utils

        mmrelay.matrix_utils.matrix_client = None
        mmrelay.matrix_utils.config = None

    def test_truncate_message_with_unicode(self):
        """
        Tests that the truncate_message function correctly handles Unicode strings with multi-byte characters, ensuring the result is a string and does not exceed the specified byte limit, even with very small limits.
        """
        # Test with emoji and multi-byte characters
        unicode_text = "Hello 🌍 世界 🚀 Testing"

        # Test normal truncation
        result = truncate_message(unicode_text, max_bytes=20)
        self.assertIsInstance(result, str)
        self.assertTrue(len(result.encode("utf-8")) <= 20)

        # Test truncation that might split a multi-byte character
        result = truncate_message(unicode_text, max_bytes=10)
        self.assertIsInstance(result, str)
        self.assertTrue(len(result.encode("utf-8")) <= 10)

        # Test with very small byte limit
        result = truncate_message(unicode_text, max_bytes=1)
        self.assertIsInstance(result, str)
        self.assertTrue(len(result.encode("utf-8")) <= 1)

    def test_truncate_message_edge_cases(self):
        """
        Tests the truncate_message function with empty strings, short strings, zero, and negative byte limits to ensure correct handling and no crashes.
        """
        # Empty string
        result = truncate_message("", max_bytes=100)
        self.assertEqual(result, "")

        # String shorter than limit
        short_text = "Short"
        result = truncate_message(short_text, max_bytes=100)
        self.assertEqual(result, short_text)

        # Zero byte limit
        result = truncate_message("Hello", max_bytes=0)
        self.assertEqual(result, "")

        # Negative byte limit - the function may not handle this gracefully
        # so we'll just check it doesn't crash
        result = truncate_message("Hello", max_bytes=-1)
        self.assertIsInstance(result, str)  # Just ensure it returns a string

    def test_validate_prefix_format_edge_cases(self):
        """
        Tests the validate_prefix_format function with various edge cases, including valid and invalid formats, missing variables, invalid syntax, empty strings, and static text without variables.
        """
        # Valid format
        is_valid, error = validate_prefix_format("{display}: ", {"display": "Test"})
        self.assertTrue(is_valid)
        self.assertIsNone(error)

        # Missing variable
        is_valid, error = validate_prefix_format("{missing}: ", {"display": "Test"})
        self.assertFalse(is_valid)
        self.assertIsNotNone(error)

        # Invalid format syntax
        is_valid, error = validate_prefix_format("{display: ", {"display": "Test"})
        self.assertFalse(is_valid)
        self.assertIsNotNone(error)

        # Empty format string
        is_valid, error = validate_prefix_format("", {"display": "Test"})
        self.assertTrue(is_valid)  # Empty string is valid
        self.assertIsNone(error)

        # Format with no variables
        is_valid, error = validate_prefix_format("Static text: ", {"display": "Test"})
        self.assertTrue(is_valid)
        self.assertIsNone(error)

    def test_get_matrix_prefix_with_invalid_format(self):
        """
        Test that get_matrix_prefix falls back to the default format when given an invalid prefix format in the configuration.

        Verifies that the generated prefix includes the username and mesh name when the custom format string is invalid.
        """
        config = {
            "matrix": {"prefix_enabled": True, "prefix_format": "{invalid_var}: "}
        }

        # Should fall back to default format when custom format is invalid
        result = get_matrix_prefix(config, "TestUser", "TU", "TestMesh")
        # Should use default format as fallback
        self.assertIn("TestUser", result)
        self.assertIn("TestMesh", result)

    def test_get_meshtastic_prefix_with_invalid_format(self):
        """
        Test that get_meshtastic_prefix falls back to the default format when given an invalid prefix format string.

        Verifies that when the custom prefix format is invalid, the generated prefix uses the default format based on the display name.
        """
        config = {
            "meshtastic": {"prefix_enabled": True, "prefix_format": "{invalid_var}: "}
        }

        # Should fall back to default format when custom format is invalid
        result = get_meshtastic_prefix(config, "TestUser")
        # Should use default format as fallback
        self.assertIn("TestU", result)  # Default uses display5

    def test_get_matrix_prefix_with_none_values(self):
        """
        Test that get_matrix_prefix returns a string when provided with None or empty string values for user and mesh names.
        """
        config = {"matrix": {"prefix_enabled": True}}

        # Test with None values
        result = get_matrix_prefix(config, None, None, None)
        self.assertIsInstance(result, str)

        # Test with empty strings
        result = get_matrix_prefix(config, "", "", "")
        self.assertIsInstance(result, str)

    def test_get_meshtastic_prefix_with_malformed_user_id(self):
        """
        Test that meshtastic prefix generation returns a string when provided with malformed, empty, or None user IDs.

        Verifies that the prefix function handles user IDs missing expected delimiters or values without raising errors.
        """
        config = {"meshtastic": {"prefix_enabled": True}}

        # Test with malformed user ID (no @)
        result = get_meshtastic_prefix(config, "TestUser", "malformed_id")
        self.assertIsInstance(result, str)

        # Test with malformed user ID (no :)
        result = get_meshtastic_prefix(config, "TestUser", "@malformed")
        self.assertIsInstance(result, str)

        # Test with empty user ID
        result = get_meshtastic_prefix(config, "TestUser", "")
        self.assertIsInstance(result, str)

        # Test with None user ID
        result = get_meshtastic_prefix(config, "TestUser", None)
        self.assertIsInstance(result, str)

    @patch("mmrelay.matrix_utils.logger")
    def test_connect_matrix_with_no_config(self, mock_logger):
        """
        Test that connect_matrix returns None and logs an error when called without configuration.
        """

        async def run_test():
            """
            Asynchronously tests that connect_matrix returns None and logs an error when called with no configuration.
            """
            result = await connect_matrix(None)
            self.assertIsNone(result)
            mock_logger.error.assert_called_with(
                "No configuration available. Cannot connect to Matrix."
            )

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.logger")
    @patch("mmrelay.matrix_utils.AsyncClient")
    @patch("mmrelay.matrix_utils._create_ssl_context")
    def test_connect_matrix_ssl_context_failure(
        self, mock_ssl_context, mock_async_client, mock_logger
    ):
        """
        Test that connect_matrix continues when SSL context creation fails.
        
        Simulates _create_ssl_context returning None, mocks an AsyncClient, and verifies that
        connect_matrix still returns a client object and emits a warning about the SSL context failure.
        """
        mock_ssl_context.return_value = None  # Simulate SSL context creation failure

        # Mock AsyncClient to prevent MagicMock await issues
        mock_client = AsyncMock()
        mock_client.sync = AsyncMock(return_value=MagicMock())
        # Mock rooms as a regular dict to prevent AsyncMock issues
        mock_client.rooms = {}
        mock_async_client.return_value = mock_client

        config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@test:matrix.org",
            },
            "matrix_rooms": [],
        }

        async def run_test():
            # This should handle SSL context failure gracefully and return a client
            """
            Asynchronously runs a test to verify that connect_matrix handles SSL context creation failures gracefully.
            """
            result = await connect_matrix(config)
            # Should return a client despite SSL context failure
            self.assertIsNotNone(result)
            # Should log warning about SSL context failure
            mock_logger.warning.assert_called()

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.logger")
    def test_join_matrix_room_with_join_failure(self, mock_logger):
        """
        Tests that an error is logged when attempting to join a Matrix room fails.
        """
        # Create a mock client - use MagicMock to prevent coroutine warnings
        mock_client = MagicMock()
        mock_client.rooms = {}  # Bot is not in the room
        mock_client.join = AsyncMock()
        mock_response = MagicMock()
        # Simulate a failed join response - no room_id but response exists
        del mock_response.room_id  # Remove the room_id attribute entirely
        mock_response.message = "Room not found"

        mock_client.join.return_value = mock_response

        # Run the test directly with asyncio.run
        async def test_coroutine():
            """
            Asynchronously attempts to join a Matrix room with a room ID that fails to join.
            """
            await join_matrix_room(mock_client, "!invalid:matrix.org")

        # Execute the test
        asyncio.run(test_coroutine())

        # Verify the error was logged
        mock_logger.error.assert_called()

    @patch("mmrelay.matrix_utils.logger")
    def test_join_matrix_room_exception_handling(self, mock_logger):
        """
        Verify join_matrix_room logs the raised exception via logger.exception when room join raises an exception.

        This test mocks a Matrix client whose join coroutine raises an Exception, calls join_matrix_room with that client and a room ID, and asserts that mock_logger.exception was invoked.
        """
        # Use MagicMock to prevent coroutine warnings
        mock_client = MagicMock()
        mock_client.rooms = {}  # Bot is not in the room
        mock_client.join = AsyncMock()
        mock_client.join.side_effect = Exception("Network error")

        async def run_test():
            """
            Asynchronously tests joining a Matrix room with a room ID that raises an exception and verifies that an error is logged.
            """
            await join_matrix_room(mock_client, "!test:matrix.org")
            mock_logger.exception.assert_called()

        asyncio.run(run_test())

    def test_prefix_generation_with_extreme_lengths(self):
        """
        Test that prefix generation functions handle extremely long input strings without errors.

        Verifies that both Matrix and Meshtastic prefix generation return string outputs when provided with input values of excessive length.
        """
        config = {"matrix": {"prefix_enabled": True}}

        # Very long names
        very_long_name = "A" * 1000
        result = get_matrix_prefix(config, very_long_name, "short", "mesh")
        self.assertIsInstance(result, str)

        # Test meshtastic prefix with very long display name
        config = {"meshtastic": {"prefix_enabled": True}}
        result = get_meshtastic_prefix(config, very_long_name)
        self.assertIsInstance(result, str)

    def test_prefix_disabled_scenarios(self):
        """
        Test that prefix generation functions return an empty string when prefixing is disabled in the configuration for both Matrix and Meshtastic.
        """
        # Matrix prefix disabled
        config = {"matrix": {"prefix_enabled": False}}
        result = get_matrix_prefix(config, "User", "U", "Mesh")
        self.assertEqual(result, "")

        # Meshtastic prefix disabled
        config = {"meshtastic": {"prefix_enabled": False}}
        result = get_meshtastic_prefix(config, "User")
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
