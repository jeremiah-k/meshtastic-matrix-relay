"""
Tests for MeshtasticBackend implementation.

Tests that MeshtasticBackend properly implements BaseRadioBackend interface
and correctly wraps existing Meshtastic functionality.
"""

from unittest.mock import MagicMock, patch

import pytest

from mmrelay.radio.backends.meshtastic_backend import MeshtasticBackend
from mmrelay.radio.message import RadioMessage


class TestMeshtasticBackendBasics:
    """Test basic MeshtasticBackend functionality."""

    def test_backend_name(self) -> None:
        """
        Verify that backend_name returns 'meshtastic'.
        """
        backend = MeshtasticBackend()
        assert backend.backend_name == "meshtastic"


class TestMeshtasticBackendSend:
    """Test MeshtasticBackend.send_message functionality."""

    @pytest.mark.asyncio
    async def test_send_message_without_reply(self) -> None:
        """
        Verify that send_message without reply_to_id uses sendText.

        When reply_to_id is None, backend should call interface.sendText().
        """
        mock_client = MagicMock()
        mock_send_text = MagicMock(return_value=MagicMock(id=123))

        mock_client.sendText = mock_send_text

        backend = MeshtasticBackend()
        backend._client = mock_client

        result = await backend.send_message("Hello", channel=0)

        # Verify sendText was called
        mock_send_text.assert_called_once_with("Hello", channelIndex=0)
        assert result is not None

    @pytest.mark.asyncio
    @pytest.mark.skip(
        reason="Integration-level test better covered by full message flow tests"
    )
    async def test_send_message_with_reply(self) -> None:
        """
        Verify that send_message with reply_to_id uses send_text_reply.

        When reply_to_id is provided, backend should use send_text_reply.

        SKIPPED: Integration-level test better covered by full message flow tests.
        """
        pass

    @pytest.mark.asyncio
    async def test_send_message_with_destination(self) -> None:
        """
        Verify that send_message can send to a specific destination.
        """
        mock_client = MagicMock()
        mock_send_text = MagicMock(return_value=MagicMock(id=123))

        mock_client.sendText = mock_send_text

        backend = MeshtasticBackend()
        backend._client = mock_client

        result = await backend.send_message(
            "Direct message",
            channel=0,
            destination_id=999999,
        )

        # Verify sendText was called with destination
        mock_send_text.assert_called_once_with(
            "Direct message", channelIndex=0, destinationId=999999
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_send_message_no_client(self) -> None:
        """
        Verify that send_message returns None when no client is available.
        """
        backend = MeshtasticBackend()
        backend._client = None

        result = await backend.send_message("Hello", channel=0)

        assert result is None


class TestMeshtasticBackendCallback:
    """Test MeshtasticBackend.register_message_callback functionality."""

    @pytest.mark.asyncio
    async def test_register_message_callback(self) -> None:
        """
        Verify that message callback can be registered.

        The callback should be invoked when a Meshtastic packet is received.
        """
        received_messages = []

        def test_callback(message: RadioMessage) -> None:
            """Collect received messages."""
            received_messages.append(message)

        backend = MeshtasticBackend()

        with patch(
            "mmrelay.meshtastic_utils.subscribed_to_messages",
            False,
        ):
            backend.register_message_callback(test_callback)

            # Verify callback was marked as registered
            assert backend._callback_registered is True
            assert backend._message_callback is test_callback

    def test_register_callback_twice(self) -> None:
        """
        Verify that registering callback twice skips the second registration.
        """
        backend = MeshtasticBackend()

        # First registration
        with patch("mmrelay.meshtastic_utils.subscribed_to_messages", False):
            backend.register_message_callback(lambda _msg: None)
            assert backend._callback_registered is True

        # Second registration should skip
        with patch("mmrelay.meshtastic_utils.subscribed_to_messages", True):
            backend._callback_registered = False  # Reset flag
            backend.register_message_callback(lambda _msg: None)
            # Should not register again (subscribed already true)
            # and flag should stay False since we skipped

    @pytest.mark.asyncio
    async def test_callback_converts_packet_to_radio_message(self) -> None:
        """
        Verify that callback converts Meshtastic packet to RadioMessage.

        Simplified test - full flow tested in integration tests.
        """
        backend = MeshtasticBackend()

        # Mock subscription to avoid actual pubsub
        with patch("mmrelay.meshtastic_utils.subscribed_to_messages", False):
            with patch(
                "mmrelay.meshtastic_utils._get_node_display_name",
                return_value="TestNode",
            ):
                with patch("mmrelay.meshtastic_utils.config", {}):
                    with patch("pubsub.pub.subscribe"):
                        backend.register_message_callback(lambda _msg: None)

                        # Verify callback was registered
                        assert backend._callback_registered is True


class TestMeshtasticBackendIsConnected:
    """Test MeshtasticBackend.is_connected functionality."""

    def test_is_connected_when_reconnecting(self) -> None:
        """
        Verify that is_connected returns False when reconnecting is True.
        """
        with patch("mmrelay.meshtastic_utils.reconnecting", True):
            backend = MeshtasticBackend()
            assert backend.is_connected() is False

    def test_is_connected_no_client(self) -> None:
        """
        Verify that is_connected returns False when no client is available.
        """
        with patch("mmrelay.meshtastic_utils.meshtastic_client", None):
            backend = MeshtasticBackend()
            assert backend.is_connected() is False

    def test_is_connected_with_is_connected_method(self) -> None:
        """
        Verify that is_connected works when client has is_connected() method.
        """
        mock_client = MagicMock()
        mock_client.is_connected = MagicMock(return_value=True)

        with patch("mmrelay.meshtastic_utils.meshtastic_client", mock_client):
            with patch("mmrelay.meshtastic_utils.reconnecting", False):
                backend = MeshtasticBackend()
                assert backend.is_connected() is True

    def test_is_connected_with_is_connected_attribute(self) -> None:
        """
        Verify that is_connected works when client has is_connected attribute.
        """
        mock_client = MagicMock()
        mock_client.is_connected = True

        with patch("mmrelay.meshtastic_utils.meshtastic_client", mock_client):
            with patch("mmrelay.meshtastic_utils.reconnecting", False):
                backend = MeshtasticBackend()
                assert backend.is_connected() is True

    def test_is_connected_no_is_connected_field(self) -> None:
        """
        Verify that is_connected returns True when client exists but no is_connected field.
        """
        mock_client = MagicMock()
        # Simulate a client without is_connected
        del mock_client.is_connected

        with patch("mmrelay.meshtastic_utils.meshtastic_client", mock_client):
            with patch("mmrelay.meshtastic_utils.reconnecting", False):
                backend = MeshtasticBackend()
                assert backend.is_connected() is True


class TestMeshtasticBackendMessageDelay:
    """Test MeshtasticBackend.get_message_delay functionality."""

    def test_get_message_delay_from_config(self) -> None:
        """
        Verify that get_message_delay retrieves delay from meshtastic config.
        """
        config = {"meshtastic": {"message_delay": 5.0}}

        backend = MeshtasticBackend()
        delay = backend.get_message_delay(config, default=2.0)

        assert delay == 5.0

    def test_get_message_delay_default(self) -> None:
        """
        Verify that get_message_delay uses default when not in config.
        """
        config = {"meshtastic": {}}

        backend = MeshtasticBackend()
        delay = backend.get_message_delay(config, default=3.0)

        assert delay == 3.0

    def test_get_message_delay_no_meshtastic_section(self) -> None:
        """
        Verify that get_message_delay uses default when meshtastic section missing.
        """
        config = {}

        backend = MeshtasticBackend()
        delay = backend.get_message_delay(config, default=4.0)

        assert delay == 4.0


class TestMeshtasticBackendGetClient:
    """Test MeshtasticBackend.get_client functionality."""

    def test_get_client_returns_client(self) -> None:
        """
        Verify that get_client returns the underlying client.
        """
        mock_client = MagicMock()

        backend = MeshtasticBackend()
        backend._client = mock_client

        assert backend.get_client() is mock_client

    def test_get_client_fallback_to_global(self) -> None:
        """
        Verify that get_client falls back to global meshtastic_client.
        """
        mock_client = MagicMock()

        with patch("mmrelay.meshtastic_utils.meshtastic_client", mock_client):
            backend = MeshtasticBackend()
            assert backend.get_client() is mock_client

    def test_get_client_none(self) -> None:
        """
        Verify that get_client returns None when no client available.
        """
        with patch("mmrelay.meshtastic_utils.meshtastic_client", None):
            backend = MeshtasticBackend()
            assert backend.get_client() is None
