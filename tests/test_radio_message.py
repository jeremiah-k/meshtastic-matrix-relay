"""
Tests for RadioMessage message abstraction.

Tests RadioMessage creation, validation, and field handling to ensure
it provides a clean, backend-agnostic message format.
"""

import pytest

from mmrelay.radio.message import RadioMessage


class TestRadioMessageBasics:
    """Test basic RadioMessage creation and validation."""

    def test_create_valid_message(self) -> None:
        """
        Verify that a valid RadioMessage can be created with all required fields.

        All required fields (text, sender_id, sender_name, timestamp, backend, meshnet_name)
        should be accepted without error.
        """
        message = RadioMessage(
            text="Hello world",
            sender_id="!12345678",
            sender_name="Alice",
            timestamp=1234567890.0,
            backend="meshtastic",
            meshnet_name="test-mesh",
        )

        assert message.text == "Hello world"
        assert message.sender_id == "!12345678"
        assert message.sender_name == "Alice"
        assert message.timestamp == 1234567890.0
        assert message.backend == "meshtastic"
        assert message.meshnet_name == "test-mesh"

    def test_message_with_optional_fields(self) -> None:
        """
        Verify that optional fields are properly set when provided.

        Fields like channel, destination_id, message_id, reply_to_id, location,
        and telemetry should be stored correctly.
        """
        message = RadioMessage(
            text="Test message",
            sender_id="!87654321",
            sender_name="Bob",
            timestamp=1234567890.0,
            backend="meshtastic",
            meshnet_name="test-mesh",
            channel=0,
            is_direct_message=False,
            destination_id=999999,
            message_id=123,
            reply_to_id=456,
            location={"lat": 37.7749, "lon": -122.4194},
            telemetry={"batt": 85, "temp": 25},
        )

        assert message.channel == 0
        assert message.is_direct_message is False
        assert message.destination_id == 999999
        assert message.message_id == 123
        assert message.reply_to_id == 456
        assert message.location == {"lat": 37.7749, "lon": -122.4194}
        assert message.telemetry == {"batt": 85, "temp": 25}

    def test_message_with_metadata(self) -> None:
        """
        Verify that backend-specific metadata can be stored.

        The metadata dict should accept arbitrary key-value pairs for
        backend-specific information.
        """
        message = RadioMessage(
            text="Test",
            sender_id="!12345678",
            sender_name="Alice",
            timestamp=1234567890.0,
            backend="meshtastic",
            meshnet_name="test-mesh",
            metadata={
                "portnum": 1,
                "want_ack": True,
                "hop_limit": 3,
            },
        )

        assert message.metadata == {
            "portnum": 1,
            "want_ack": True,
            "hop_limit": 3,
        }

    def test_default_optional_fields(self) -> None:
        """
        Verify that optional fields default to None when not provided.

        Channel, destination_id, message_id, reply_to_id, location,
        and telemetry should default to None.
        """
        message = RadioMessage(
            text="Hello",
            sender_id="!12345678",
            sender_name="Alice",
            timestamp=1234567890.0,
            backend="meshtastic",
            meshnet_name="test-mesh",
        )

        assert message.channel is None
        assert message.is_direct_message is False
        assert message.destination_id is None
        assert message.message_id is None
        assert message.reply_to_id is None
        assert message.location is None
        assert message.telemetry is None
        assert message.metadata == {}


class TestRadioMessageValidation:
    """Test RadioMessage field validation."""

    def test_empty_text_raises_error(self) -> None:
        """
        Verify that creating a message with empty text raises ValueError.

        Text field is required and cannot be empty.
        """
        with pytest.raises(ValueError, match="text cannot be empty"):
            RadioMessage(
                text="",
                sender_id="!12345678",
                sender_name="Alice",
                timestamp=1234567890.0,
                backend="meshtastic",
                meshnet_name="test-mesh",
            )

    def test_missing_sender_id_raises_error(self) -> None:
        """
        Verify that missing sender_id raises ValueError.

        Sender ID is required for message routing and display.
        """
        with pytest.raises(ValueError, match="sender_id is required"):
            RadioMessage(
                text="Hello",
                sender_id="",  # Empty string
                sender_name="Alice",
                timestamp=1234567890.0,
                backend="meshtastic",
                meshnet_name="test-mesh",
            )

    def test_missing_backend_raises_error(self) -> None:
        """
        Verify that missing backend raises ValueError.

        Backend field is required to identify which radio system
        the message came from.
        """
        with pytest.raises(ValueError, match="backend is required"):
            RadioMessage(
                text="Hello",
                sender_id="!12345678",
                sender_name="Alice",
                timestamp=1234567890.0,
                backend="",  # Empty string
                meshnet_name="test-mesh",
            )

    def test_missing_sender_name_uses_fallback(self) -> None:
        """
        Verify that empty sender_name uses a fallback based on sender_id.

        When sender_name is empty or missing, a default format
        "Node {truncated_id}" should be used where ID is truncated
        to 8 characters if longer than 8, or full ID if 8 or fewer chars.
        """
        # Test with long ID (should truncate to 8 chars)
        message_long = RadioMessage(
            text="Hello",
            sender_id="!123456789abcdef",  # 16 characters
            sender_name="",  # Empty name
            timestamp=1234567890.0,
            backend="meshtastic",
            meshnet_name="test-mesh",
        )
        assert message_long.sender_name == "Node !1234567"

        # Test with short ID (should use full ID)
        message_short = RadioMessage(
            text="Hello",
            sender_id="!1234567",  # 8 characters
            sender_name="",
            timestamp=1234567890.0,
            backend="meshtastic",
            meshnet_name="test-mesh",
        )
        assert message_short.sender_name == "Node !1234567"

    def test_missing_meshnet_name_uses_default(self) -> None:
        """
        Verify that missing meshnet_name defaults to 'default'.

        When meshnet_name is empty or missing, it should fall back
        to 'default' rather than raising an error.
        """
        message = RadioMessage(
            text="Hello",
            sender_id="!12345678",
            sender_name="Alice",
            timestamp=1234567890.0,
            backend="meshtastic",
            meshnet_name="",  # Empty name
        )

        assert message.meshnet_name == "default"

    def test_direct_message_true(self) -> None:
        """
        Verify that is_direct_message can be set to True.

        Direct messages (DMs) should be distinguished from
        broadcast messages.
        """
        message = RadioMessage(
            text="Direct message",
            sender_id="!12345678",
            sender_name="Alice",
            timestamp=1234567890.0,
            backend="meshtastic",
            meshnet_name="test-mesh",
            is_direct_message=True,
        )

        assert message.is_direct_message is True


class TestRadioMessageTypes:
    """Test RadioMessage type handling."""

    def test_message_id_can_be_string_or_int(self) -> None:
        """
        Verify that message_id accepts both string and integer types.

        Different backends may use different ID types (strings for some,
        integers for others).
        """
        # Integer message ID
        message1 = RadioMessage(
            text="Message with int ID",
            sender_id="!12345678",
            sender_name="Alice",
            timestamp=1234567890.0,
            backend="meshtastic",
            meshnet_name="test-mesh",
            message_id=12345,
        )
        assert message1.message_id == 12345

        # String message ID
        message2 = RadioMessage(
            text="Message with string ID",
            sender_id="!12345678",
            sender_name="Alice",
            timestamp=1234567890.0,
            backend="meshtastic",
            meshnet_name="test-mesh",
            message_id="msg-12345",
        )
        assert message2.message_id == "msg-12345"

    def test_destination_id_can_be_none_or_int(self) -> None:
        """
        Verify that destination_id accepts None or integer.

        None means broadcast, integer means direct message to specific node.
        """
        # Broadcast (None)
        message1 = RadioMessage(
            text="Broadcast",
            sender_id="!12345678",
            sender_name="Alice",
            timestamp=1234567890.0,
            backend="meshtastic",
            meshnet_name="test-mesh",
            destination_id=None,
        )
        assert message1.destination_id is None

        # Direct message (int)
        message2 = RadioMessage(
            text="Direct message",
            sender_id="!12345678",
            sender_name="Alice",
            timestamp=1234567890.0,
            backend="meshtastic",
            meshnet_name="test-mesh",
            destination_id=999999,
        )
        assert message2.destination_id == 999999

    def test_channel_can_be_none_or_int(self) -> None:
        """
        Verify that channel accepts None or integer.

        None means default channel, integer means specific channel.
        """
        # Default channel (None)
        message1 = RadioMessage(
            text="Default channel",
            sender_id="!12345678",
            sender_name="Alice",
            timestamp=1234567890.0,
            backend="meshtastic",
            meshnet_name="test-mesh",
            channel=None,
        )
        assert message1.channel is None

        # Specific channel
        message2 = RadioMessage(
            text="Channel 0",
            sender_id="!12345678",
            sender_name="Alice",
            timestamp=1234567890.0,
            backend="meshtastic",
            meshnet_name="test-mesh",
            channel=0,
        )
        assert message2.channel == 0
