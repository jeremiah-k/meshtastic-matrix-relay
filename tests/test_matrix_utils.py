import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.matrix_utils import (
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
    strip_quoted_lines,
    truncate_message,
    validate_prefix_format,
)

# Matrix utility function tests - converted from unittest.TestCase to standalone pytest functions


# Async Matrix function tests - converted from unittest.TestCase to standalone pytest functions


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


# End of file
