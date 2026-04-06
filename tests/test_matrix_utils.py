import asyncio
import importlib
import os
import ssl
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from nio import SyncError, ToDeviceError, ToDeviceResponse

import mmrelay.matrix_utils as matrix_utils_module
from mmrelay.constants.app import CREDENTIALS_FILENAME
from mmrelay.constants.domain import MATRIX_EVENT_TYPE_ROOM_MESSAGE
from mmrelay.constants.formats import (
    MATRIX_SUPPRESS_KEY,
)
from mmrelay.matrix_utils import (
    ImageUploadError,
    NioLocalTransportError,
    NioRemoteTransportError,
    _can_auto_create_credentials,
    _create_mapping_info,
    _extract_localpart_from_mxid,
    _get_detailed_matrix_error_message,
    _get_msgs_to_keep_config,
    _handle_detection_sensor_packet,
    _is_room_alias,
    _iter_room_alias_entries,
    _normalize_bot_user_id,
    _update_room_id_in_mapping,
    connect_matrix,
    get_displayname,
    get_user_display_name,
    login_matrix_bot,
    matrix_relay,
    on_decryption_failure,
    on_room_member,
    send_image,
    send_room_image,
    strip_quoted_lines,
    truncate_message,
    upload_image,
    validate_prefix_format,
)

# Matrix utility function tests - converted from unittest.TestCase to standalone pytest functions


# Async Matrix function tests - converted from unittest.TestCase to standalone pytest functions


@pytest.fixture
def matrix_config():
    """Test configuration for Matrix functions."""
    return {
        "matrix": {
            "homeserver": "https://matrix.example.com",
            "bot_user_id": "@bot:example.com",
            "password": "test_password",
            "rooms": {"!test:example.com": "Test Room"},
        },
        "meshtastic": {},
    }
    assert result == expected


# Async Matrix function tests - converted from unittest.TestCase to standalone pytest functions


@pytest.fixture
def matrix_config():
    """Test configuration for Matrix functions."""
    return {
        "matrix": {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:matrix.org",
            "prefix_enabled": True,
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }


# Matrix logout tests


class TestMatrixUtilityFunctions:
    def test_truncate_message_respects_utf8_boundaries(self):
        text = "hello😊"
        truncated = truncate_message(text, max_bytes=6)
        assert truncated == "hello"

    def test_strip_quoted_lines_removes_quoted_content(self):
        text = "Line one\n> quoted line\n Line two"
        result = strip_quoted_lines(text)
        assert result == "Line one Line two"

    def test_validate_prefix_format_success(self):
        is_valid, error = validate_prefix_format("{display}", {"display": "Alice"})
        assert is_valid is True
        assert error is None

    def test_validate_prefix_format_missing_key(self):
        is_valid, error = validate_prefix_format("{missing}", {"display": "Alice"})
        assert is_valid is False
        assert error is not None
        assert "missing" in error


class TestMatrixE2EEHasAttrChecks:
    """Test class for E2EE hasattr checks in matrix_utils.py"""

    @pytest.fixture
    def e2ee_config(self):
        """
        Create a minimal Matrix configuration dictionary with end-to-end encryption enabled for tests.

        The configuration contains a `matrix` section with homeserver, access token, bot user id, and `e2ee: {"enabled": True}`, and a `matrix_rooms` mapping with a sample room configured for `meshtastic_channel: 0`.

        Returns:
            dict: Test-ready Matrix configuration with E2EE enabled.
        """
        return {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
                "e2ee": {"enabled": True},
            },
            "matrix_rooms": {"!room:matrix.org": {"meshtastic_channel": 0}},
        }


class TestGetDetailedSyncErrorMessage:
    """Test cases for _get_detailed_matrix_error_message function."""


def test_can_auto_create_credentials_whitespace_values():
    """
    Test _can_auto_create_credentials returns False when values contain only whitespace.
    """
    config = {
        "homeserver": "   ",
        "bot_user_id": "@bot:matrix.org",
        "password": "password123",
    }

    result = _can_auto_create_credentials(config)
    assert result is False


@pytest.mark.asyncio
async def test_on_decryption_failure():
    """Test on_decryption_failure handles decryption failures with retry logic."""

    # Create mock room and event
    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # Create a response mock that satisfies isinstance(response, ToDeviceResponse)
        mock_response = MagicMock(spec=ToDeviceResponse)
        mock_client.to_device = AsyncMock(return_value=mock_response)

        # Test successful key request - should exit after first success
        await on_decryption_failure(mock_room, mock_event)

        # Verify the event was patched with room_id
        assert mock_event.room_id == "!room123:matrix.org"
        # Verify key request was created and sent (only 1 call on success)
        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        assert mock_client.to_device.await_count == 1
        mock_client.to_device.assert_awaited_once_with({"type": "m.room_key_request"})
        # Verify logging - error about decryption failure, 1 info message on success
        assert mock_logger.error.call_count == 1  # Initial decryption failure
        assert mock_logger.info.call_count == 1  # Success message
        # Verify single sleep after success with key-sharing delay.
        assert [call.args[0] for call in mock_sleep.await_args_list] == [
            matrix_utils_module.E2EE_KEY_SHARING_DELAY_SECONDS
        ]


@pytest.mark.asyncio
async def test_on_decryption_failure_without_matrix_client_logs_and_returns():
    """Test on_decryption_failure exits early when matrix_client is unavailable."""
    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"

    with (
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        await on_decryption_failure(mock_room, mock_event)

        # Initial decrypt error + unavailable matrix client error.
        assert mock_logger.error.call_count == 2
        mock_event.as_key_request.assert_not_called()
        mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_decryption_failure_missing_device_id():
    """Missing device_id should prevent key requests and log an error."""
    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = None
        mock_client.to_device = AsyncMock()

        await on_decryption_failure(mock_room, mock_event)

        mock_logger.error.assert_any_call(
            "Cannot request keys for event %s: client has no device_id",
            "$event123",
        )
        mock_client.to_device.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_decryption_failure_retry_on_exception():
    """Test on_decryption_failure retries with exponential backoff on communication exceptions."""

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        max_attempts = matrix_utils_module.E2EE_KEY_REQUEST_MAX_ATTEMPTS
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # Create a response mock that satisfies isinstance(response, ToDeviceResponse)
        mock_response = MagicMock(spec=ToDeviceResponse)
        # Fail until the final allowed attempt, then succeed.
        mock_client.to_device = AsyncMock(
            side_effect=[
                *[
                    NioRemoteTransportError("Network error")
                    for _ in range(max_attempts - 1)
                ],
                mock_response,
            ]
        )

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        # Verify all attempts were made.
        assert mock_client.to_device.await_count == max_attempts
        # Verify warnings were logged for failures before success
        assert mock_logger.warning.call_count == max_attempts - 1
        # Verify success info message (only on final successful attempt)
        assert mock_logger.info.call_count == 1
        # Compute expected sleep list: exponential backoff for first (max_attempts-1) retries, then key-sharing delay
        expected_sleep_list = []
        for attempt in range(1, max_attempts):  # attempts 1 to max_attempts-1
            delay = min(
                matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY * (2 ** (attempt - 1)),
                matrix_utils_module.E2EE_KEY_REQUEST_MAX_DELAY,
            )
            expected_sleep_list.append(delay)
        expected_sleep_list.append(matrix_utils_module.E2EE_KEY_SHARING_DELAY_SECONDS)
        assert [
            call.args[0] for call in mock_sleep.await_args_list
        ] == expected_sleep_list


@pytest.mark.asyncio
async def test_on_decryption_failure_all_retries_fail():
    """Test on_decryption_failure logs error after all retries are exhausted."""

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # All attempts fail
        mock_client.to_device = AsyncMock(
            side_effect=NioRemoteTransportError("Network error")
        )

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        # Compute expected values from constants
        max_attempts = matrix_utils_module.E2EE_KEY_REQUEST_MAX_ATTEMPTS
        # Verify all attempts were made
        assert mock_client.to_device.await_count == max_attempts
        # Verify warnings for failures before final attempt
        assert mock_logger.warning.call_count == max_attempts - 1
        # Check that the final error message was logged (f-string format)
        mock_logger.exception.assert_called_once()
        # No info messages since all requests failed
        assert mock_logger.info.call_count == 0
        # Compute expected sleep list: exponential backoff for first (max_attempts-1) retries
        # No backoff after final failure
        expected_sleep_list = []
        for attempt in range(1, max_attempts):  # attempts 1 to max_attempts-1
            delay = min(
                matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY * (2 ** (attempt - 1)),
                matrix_utils_module.E2EE_KEY_REQUEST_MAX_DELAY,
            )
            expected_sleep_list.append(delay)
        assert [
            call.args[0] for call in mock_sleep.await_args_list
        ] == expected_sleep_list


@pytest.mark.asyncio
async def test_on_decryption_failure_to_device_error():
    """Test on_decryption_failure handles ToDeviceError (server-side error) with retry."""

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # First returns ToDeviceError (server error), second returns ToDeviceResponse (success)
        mock_error = MagicMock(spec=ToDeviceError)
        mock_response = MagicMock(spec=ToDeviceResponse)
        mock_client.to_device = AsyncMock(
            side_effect=[
                mock_error,  # Server error on first attempt
                mock_response,  # Success on second attempt
            ]
        )

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        # Verify 2 attempts were made (error on first, success on second)
        assert mock_client.to_device.await_count == 2
        # Verify warning for the server error
        assert mock_logger.warning.call_count == 1
        # Verify success info message
        assert mock_logger.info.call_count == 1
        # Verify backoff after error and success key-sharing delay.
        expected_sleep_list = [
            matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY,
            matrix_utils_module.E2EE_KEY_SHARING_DELAY_SECONDS,
        ]
        assert [
            call.args[0] for call in mock_sleep.await_args_list
        ] == expected_sleep_list


@pytest.mark.asyncio
async def test_on_decryption_failure_backoff_caps_at_max_delay():
    """
    Verify exponential backoff caps at the configured maximum delay when repeated to-device failures occur.

    This test simulates repeated ToDeviceError responses from the Matrix client's to_device call and asserts that on_decryption_failure schedules retries with asyncio.sleep delays that respect E2EE_KEY_REQUEST_BASE_DELAY and do not exceed E2EE_KEY_REQUEST_MAX_DELAY (expected sleep calls: 20, then capped 30.0). It also verifies the key request is created for the configured bot user and device.
    """

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch("mmrelay.matrix_utils.E2EE_KEY_REQUEST_MAX_ATTEMPTS", 3),
        patch("mmrelay.matrix_utils.E2EE_KEY_REQUEST_BASE_DELAY", 20),
        patch("mmrelay.matrix_utils.E2EE_KEY_REQUEST_MAX_DELAY", 30.0),
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # Keep returning ToDeviceError so retries continue until max attempts.
        mock_error = MagicMock(spec=ToDeviceError)
        mock_client.to_device = AsyncMock(
            side_effect=[mock_error, mock_error, mock_error]
        )

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        assert (
            mock_client.to_device.await_count
            == matrix_utils_module.E2EE_KEY_REQUEST_MAX_ATTEMPTS
        )
        assert [call.args[0] for call in mock_sleep.await_args_list] == [
            matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY,
            min(
                matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY * 2,
                matrix_utils_module.E2EE_KEY_REQUEST_MAX_DELAY,
            ),
        ]
        # Initial decryption error + terminal retry exhaustion error.
        assert mock_logger.error.call_count == 2
        assert mock_logger.info.call_count == 0


@pytest.mark.asyncio
async def test_on_decryption_failure_unexpected_response_type():
    """Unexpected to_device response types should be logged and retried with backoff."""

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # Return an unexpected type for all attempts to exercise fallback handling.
        max_attempts = matrix_utils_module.E2EE_KEY_REQUEST_MAX_ATTEMPTS
        mock_client.to_device = AsyncMock(side_effect=[object()] * max_attempts)

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        assert mock_client.to_device.await_count == max_attempts
        # Compute expected sleep list: exponential backoff for first (max_attempts-1) retries
        # No backoff after final failure
        expected_sleep_list = []
        for attempt in range(1, max_attempts):  # attempts 1 to max_attempts-1
            delay = min(
                matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY * (2 ** (attempt - 1)),
                matrix_utils_module.E2EE_KEY_REQUEST_MAX_DELAY,
            )
            expected_sleep_list.append(delay)
        assert [
            call.args[0] for call in mock_sleep.await_args_list
        ] == expected_sleep_list
        # Warning for each unexpected response
        assert mock_logger.warning.call_count == max_attempts
        assert (
            mock_logger.error.call_count == 2
        )  # initial decryption + final retry failure


@pytest.mark.asyncio
async def test_on_decryption_failure_timeout_on_to_device():
    """Test on_decryption_failure handles asyncio.TimeoutError from the to-device wrapper."""

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
        patch("mmrelay.matrix_utils.asyncio.wait_for") as mock_wait_for,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        mock_client.to_device = AsyncMock(return_value=MagicMock(spec=ToDeviceResponse))

        async def mock_wait_for_impl(coro, timeout):
            assert timeout == matrix_utils_module.MATRIX_TO_DEVICE_TIMEOUT
            await coro
            raise asyncio.TimeoutError()

        mock_wait_for.side_effect = mock_wait_for_impl

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )

        # Compute expected values from constants
        max_attempts = matrix_utils_module.E2EE_KEY_REQUEST_MAX_ATTEMPTS
        assert mock_wait_for.await_count == max_attempts
        # Verify all attempts were made via to_device calls
        assert mock_client.to_device.await_count == max_attempts
        # Verify warnings for failures before final attempt
        assert mock_logger.warning.call_count == max_attempts - 1
        # Verify final exception was logged
        mock_logger.exception.assert_called_once()
        # No info messages since all requests failed
        assert mock_logger.info.call_count == 0
        # Compute expected sleep list: exponential backoff for first (max_attempts-1) retries
        # No backoff after final failure
        expected_sleep_list = []
        for attempt in range(1, max_attempts):  # attempts 1 to max_attempts-1
            delay = min(
                matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY * (2 ** (attempt - 1)),
                matrix_utils_module.E2EE_KEY_REQUEST_MAX_DELAY,
            )
            expected_sleep_list.append(delay)
        assert [
            call.args[0] for call in mock_sleep.await_args_list
        ] == expected_sleep_list


@pytest.mark.asyncio
async def test_on_room_member():
    """Test on_room_member handles room member events."""

    # Create mock room and event
    mock_room = MagicMock()
    mock_event = MagicMock()

    # The function just passes, so we just test it can be called
    await on_room_member(mock_room, mock_event)


class TestUncoveredMatrixUtils(unittest.TestCase):
    """Test cases for uncovered functions and edge cases in matrix_utils.py."""

    @patch("mmrelay.matrix_utils.logger")
    def test_is_room_alias_with_various_inputs(self, mock_logger):
        """Test _is_room_alias function with different input types."""

        # Test with valid alias
        self.assertTrue(_is_room_alias("#room:example.com"))

        # Test with room ID
        self.assertFalse(_is_room_alias("!room:example.com"))

        # Test with non-string types
        self.assertFalse(_is_room_alias(None))
        self.assertFalse(_is_room_alias(123))
        self.assertFalse(_is_room_alias([]))

    @patch("mmrelay.matrix_utils.logger")
    def test_iter_room_alias_entries_list_format(self, _mock_logger):
        """Test _iter_room_alias_entries with list format."""

        # Test with list of strings
        mapping = ["#room1:example.com", "#room2:example.com"]
        entries = list(_iter_room_alias_entries(mapping))

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][0], "#room1:example.com")
        self.assertEqual(entries[1][0], "#room2:example.com")

        # Test that setters work
        entries[0][1]("!newroom:example.com")
        self.assertEqual(mapping[0], "!newroom:example.com")

    @patch("mmrelay.matrix_utils.logger")
    def test_iter_room_alias_entries_dict_format(self, _mock_logger):
        """Test _iter_room_alias_entries with dict format."""
        mapping = {
            "one": "#room1:example.com",
            "two": {"id": "#room2:example.com"},
        }
        entries = list(_iter_room_alias_entries(mapping))

        self.assertEqual(len(entries), 2)
        entries[0][1]("!new1:example.com")
        entries[1][1]("!new2:example.com")

        self.assertEqual(mapping["one"], "!new1:example.com")
        self.assertEqual(mapping["two"]["id"], "!new2:example.com")

    @patch("mmrelay.matrix_utils.logger")
    def test_can_auto_create_credentials_missing_fields(self, mock_logger):
        """Test _can_auto_create_credentials with missing fields."""
        from mmrelay.matrix_utils import _can_auto_create_credentials

        # Test missing homeserver
        config1 = {"bot_user_id": "@bot:example.com", "password": "secret123"}
        self.assertFalse(_can_auto_create_credentials(config1))

        # Test missing user_id
        config2 = {"homeserver": "https://example.com", "password": "secret123"}
        self.assertFalse(_can_auto_create_credentials(config2))

        # Test empty strings
        config3 = {
            "homeserver": "",
            "bot_user_id": "@bot:example.com",
            "password": "secret123",
        }
        self.assertFalse(_can_auto_create_credentials(config3))

    @patch("mmrelay.matrix_utils.logger")
    def test_normalize_bot_user_id_various_formats(self, mock_logger):
        """Test _normalize_bot_user_id with different input formats."""

        # Test with full MXID
        result1 = _normalize_bot_user_id("example.com", "@user:example.com")
        self.assertEqual(result1, "@user:example.com")

        # Test with localpart only
        result2 = _normalize_bot_user_id("example.com", "user")
        self.assertEqual(result2, "@user:example.com")

        # Test with already formatted ID
        result3 = _normalize_bot_user_id("example.com", "user:example.com")
        self.assertEqual(result3, "@user:example.com")

        # Test with falsy input
        result4 = _normalize_bot_user_id("example.com", "")
        self.assertEqual(result4, "")

    @patch("mmrelay.matrix_utils.logger")
    def test_normalize_bot_user_id_ipv6_and_ports(self, mock_logger):
        """Test _normalize_bot_user_id with IPv6 hosts and ports."""

        result1 = _normalize_bot_user_id("https://[2001:db8::1]:8448/path", "alice")
        self.assertEqual(result1, "@alice:[2001:db8::1]")

        result2 = _normalize_bot_user_id("example.com", "@bob:[2001:db8::1]:8448")
        self.assertEqual(result2, "@bob:[2001:db8::1]")

        result3 = _normalize_bot_user_id("[::1]:8448", "carol")
        self.assertEqual(result3, "@carol:[::1]")

    def test_extract_localpart_from_mxid(self):
        """Test _extract_localpart_from_mxid with different input formats."""

        # Test with full MXID
        result1 = _extract_localpart_from_mxid("@user:example.com")
        self.assertEqual(result1, "user")

        # Test with MXID using different server
        result2 = _extract_localpart_from_mxid("@bot:tchncs.de")
        self.assertEqual(result2, "bot")

        # Test with localpart only
        result3 = _extract_localpart_from_mxid("alice")
        self.assertEqual(result3, "alice")

        # Test with empty string
        result4 = _extract_localpart_from_mxid("")
        self.assertEqual(result4, "")

        # Test with None
        result5 = _extract_localpart_from_mxid(None)
        self.assertIsNone(result5)

        # Test with MXID containing special characters
        result6 = _extract_localpart_from_mxid("@user_123:example.com")
        self.assertEqual(result6, "user_123")

    def test_normalize_bot_user_id_preserves_existing_server_part(self):
        """Test that _normalize_bot_user_id preserves existing server part in MXID."""

        # Test with full MXID - should preserve server part
        result1 = _normalize_bot_user_id("https://matrix.tchncs.de", "@bot:tchncs.de")
        self.assertEqual(result1, "@bot:tchncs.de")

        # Test with localpart only - should use provided homeserver
        result2 = _normalize_bot_user_id("https://tchncs.de", "bot")
        self.assertEqual(result2, "@bot:tchncs.de")

        # Test with already formatted ID without @
        result3 = _normalize_bot_user_id("https://example.com", "bot:example.com")
        self.assertEqual(result3, "@bot:example.com")

    @patch("mmrelay.matrix_utils.logger")
    def test_get_detailed_matrix_error_message_bytes(self, mock_logger):
        """Test _get_detailed_matrix_error_message with bytes input."""

        # Test with valid UTF-8 bytes
        result = _get_detailed_matrix_error_message(b"Error message")
        self.assertEqual(result, "Error message")

        # Test with invalid UTF-8 bytes
        result = _get_detailed_matrix_error_message(b"\xff\xfe\xfd")
        self.assertEqual(
            result, "Network connectivity issue or server unreachable (binary data)"
        )

    @patch("mmrelay.matrix_utils.logger")
    def test_get_detailed_matrix_error_message_object_attributes(self, mock_logger):
        """Test _get_detailed_matrix_error_message with object having attributes."""

        # Test with message attribute
        mock_response = MagicMock()
        mock_response.message = "Custom error message"
        result = _get_detailed_matrix_error_message(mock_response)
        self.assertEqual(result, "Custom error message")

        # Test with status_code attribute only (no message)
        mock_response2 = MagicMock()
        mock_response2.message = None  # No message
        mock_response2.status_code = 404
        result = _get_detailed_matrix_error_message(mock_response2)
        self.assertEqual(result, "Server not found - check homeserver URL")

        # Test with status_code 429 only
        mock_response3 = MagicMock()
        mock_response3.message = None  # No message
        mock_response3.status_code = 429
        result = _get_detailed_matrix_error_message(mock_response3)
        self.assertEqual(result, "Rate limited - too many requests")

    def test_get_detailed_matrix_error_message_transport_status_non_int(self):
        """Test transport_response with non-int status_code."""

        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = None
        mock_response.transport_response = SimpleNamespace(status_code="bad")

        result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(result, "Network connectivity issue or server unreachable")

    def test_get_detailed_matrix_error_message_attribute_error(self):
        """Test fallback for unexpected attribute errors."""

        class ExplodingResponse:
            def __getattr__(self, _name):
                raise ValueError("boom")

        result = _get_detailed_matrix_error_message(ExplodingResponse())
        self.assertEqual(
            result,
            "Unable to determine specific error - likely a network connectivity issue",
        )

    @patch("mmrelay.matrix_utils.logger")
    def test_update_room_id_in_mapping_unsupported_type(self, mock_logger):
        """Test _update_room_id_in_mapping with unsupported mapping type."""

        mapping = "not a list or dict"
        result = _update_room_id_in_mapping(
            mapping, "#old:example.com", "!new:example.com"
        )

        self.assertFalse(result)


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_broadcast_disabled():
    """Test _handle_detection_sensor_packet when broadcast is disabled."""
    config = {"meshtastic": {"broadcast_enabled": False}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    with patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config:
        mock_get_config.return_value = False  # broadcast_enabled

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        # Should not attempt to connect or send
        mock_get_config.assert_called()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_detection_disabled():
    """Test _handle_detection_sensor_packet when detection is disabled."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": False}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    with patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config:
        mock_get_config.side_effect = [
            True,
            False,
        ]  # broadcast_enabled, detection_sensor

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        # Should not attempt to connect or send
        assert mock_get_config.call_count == 2


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_connect_fail():
    """Test _handle_detection_sensor_packet when Meshtastic connection fails."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch("mmrelay.matrix_utils._connect_meshtastic") as mock_connect,
    ):
        mock_get_config.side_effect = [
            True,
            True,
        ]  # broadcast_enabled, detection_sensor
        mock_connect.return_value = None  # Connection fails

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_connect.assert_called_once()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_missing_channel():
    """Test _handle_detection_sensor_packet when meshtastic_channel is missing."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {}  # No meshtastic_channel
    full_display_name = "Test User"
    text = "Test message"

    mock_interface = MagicMock()

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch("mmrelay.matrix_utils._connect_meshtastic") as mock_connect,
    ):
        mock_get_config.side_effect = [True, True]
        mock_connect.return_value = mock_interface

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_connect.assert_called_once()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_invalid_channel():
    """Test _handle_detection_sensor_packet when meshtastic_channel is invalid."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {"meshtastic_channel": -1}  # Invalid channel
    full_display_name = "Test User"
    text = "Test message"

    mock_interface = MagicMock()

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch("mmrelay.matrix_utils._connect_meshtastic") as mock_connect,
    ):
        mock_get_config.side_effect = [True, True]
        mock_connect.return_value = mock_interface

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_connect.assert_called_once()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_success():
    """Test _handle_detection_sensor_packet successful relay."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    mock_interface = MagicMock()
    mock_queue = MagicMock()
    mock_queue.get_queue_size.return_value = 1

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch(
            "mmrelay.matrix_utils._connect_meshtastic", new_callable=AsyncMock
        ) as mock_connect,
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
        patch("mmrelay.matrix_utils.get_message_queue") as mock_get_queue,
    ):
        mock_get_config.side_effect = [True, True]
        mock_connect.return_value = mock_interface
        mock_queue_message.return_value = True
        mock_get_queue.return_value = mock_queue

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_queue_message.assert_called_once()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_queue_size_gt_one():
    """Test _handle_detection_sensor_packet logs when queue has multiple entries."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    mock_interface = MagicMock()
    mock_queue = MagicMock()
    mock_queue.get_queue_size.return_value = 3

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            new_callable=AsyncMock,
        ) as mock_get_iface,
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
        patch("mmrelay.matrix_utils.get_message_queue") as mock_get_queue,
        patch("mmrelay.meshtastic_utils.logger") as mock_mesh_logger,
    ):
        mock_get_config.side_effect = [True, True]
        mock_get_iface.return_value = (mock_interface, 0)
        mock_queue_message.return_value = True
        mock_get_queue.return_value = mock_queue

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_mesh_logger.info.assert_called()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_queue_fail():
    """Test _handle_detection_sensor_packet when queue_message fails."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    mock_interface = MagicMock()

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch("mmrelay.matrix_utils._connect_meshtastic") as mock_connect,
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
    ):
        mock_get_config.side_effect = [True, True]
        mock_connect.return_value = mock_interface
        mock_queue_message.return_value = False  # Queue fails

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_queue_message.assert_called_once()


def test_matrix_utils_imports_nio_exceptions_when_available(monkeypatch):
    """Exercise the nio exception import branch for coverage."""
    import mmrelay.matrix_utils as mu

    # reload is intentional to exercise the import-time wiring of nio exceptions.

    original_values = {
        "NioLocalProtocolError": mu.NioLocalProtocolError,
        "NioLocalTransportError": mu.NioLocalTransportError,
        "NioRemoteProtocolError": mu.NioRemoteProtocolError,
        "NioRemoteTransportError": mu.NioRemoteTransportError,
        "NioLoginError": mu.NioLoginError,
        "NioLogoutError": mu.NioLogoutError,
        "NIO_COMM_EXCEPTIONS": mu.NIO_COMM_EXCEPTIONS,
        "config": mu.config,
        "matrix_client": mu.matrix_client,
        "matrix_rooms": mu.matrix_rooms,
        "bot_user_id": mu.bot_user_id,
        "matrix_access_token": mu.matrix_access_token,
        "matrix_homeserver": mu.matrix_homeserver,
        "bot_user_name": mu.bot_user_name,
        "bot_start_time": mu.bot_start_time,
    }

    exc_mod = types.ModuleType("nio.exceptions")
    resp_mod = types.ModuleType("nio.responses")

    class LocalProtocolError(Exception):
        pass

    class LocalTransportError(Exception):
        pass

    class RemoteProtocolError(Exception):
        pass

    class RemoteTransportError(Exception):
        pass

    class LoginError(Exception):
        pass

    class LogoutError(Exception):
        pass

    exc_mod.LocalProtocolError = LocalProtocolError  # type: ignore[attr-defined]
    exc_mod.LocalTransportError = LocalTransportError  # type: ignore[attr-defined]
    exc_mod.RemoteProtocolError = RemoteProtocolError  # type: ignore[attr-defined]
    exc_mod.RemoteTransportError = RemoteTransportError  # type: ignore[attr-defined]
    resp_mod.LoginError = LoginError  # type: ignore[attr-defined]
    resp_mod.LogoutError = LogoutError  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "nio.exceptions", exc_mod)
    monkeypatch.setitem(sys.modules, "nio.responses", resp_mod)
    # monkeypatch restores sys.modules entries after the test to avoid side effects.

    importlib.reload(mu)

    assert mu.NioLocalProtocolError is LocalProtocolError
    assert mu.NioLoginError is LoginError

    # Restore original exception classes so other tests using imports remain consistent.
    mu.NioLocalProtocolError = original_values["NioLocalProtocolError"]
    mu.NioLocalTransportError = original_values["NioLocalTransportError"]
    mu.NioRemoteProtocolError = original_values["NioRemoteProtocolError"]
    mu.NioRemoteTransportError = original_values["NioRemoteTransportError"]
    mu.NioLoginError = original_values["NioLoginError"]
    mu.NioLogoutError = original_values["NioLogoutError"]
    mu.NIO_COMM_EXCEPTIONS = original_values["NIO_COMM_EXCEPTIONS"]
    mu.config = original_values["config"]
    mu.matrix_client = original_values["matrix_client"]
    mu.matrix_rooms = original_values["matrix_rooms"]
    mu.bot_user_id = original_values["bot_user_id"]
    mu.matrix_access_token = original_values["matrix_access_token"]
    mu.matrix_homeserver = original_values["matrix_homeserver"]
    mu.bot_user_name = original_values["bot_user_name"]
    mu.bot_start_time = original_values["bot_start_time"]


@pytest.mark.asyncio
async def test_matrix_relay_logs_unexpected_exception():
    """Unexpected errors in matrix_relay should be logged and not raised."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.room_send = AsyncMock()

    config = {
        "meshtastic": {"meshnet_name": "TestMesh"},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.config", config),
        patch("mmrelay.matrix_utils.connect_matrix", return_value=mock_client),
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": False},
        ),
        patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False),
        patch(
            "mmrelay.matrix_utils.join_matrix_room",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="Hello",
            longname="Alice",
            shortname="A",
            meshnet_name="TestMesh",
            portnum=1,
        )

    mock_logger.exception.assert_called_once_with(
        "Error sending radio message to matrix room !room:matrix.org"
    )
