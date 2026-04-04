from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.meshtastic_utils import (
    on_lost_meshtastic_connection,
    on_meshtastic_message,
)


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestOnLostConnectionNoActiveClient:
    def test_ignores_when_no_active_client_and_subscribed(self):
        mu.meshtastic_client = None
        mu.subscribed_to_connection_lost = True
        mu.shutting_down = False

        mock_interface = MagicMock()

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_lost_meshtastic_connection(interface=mock_interface)

        assert mu.reconnecting is False
        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any(
            "Ignoring connection-lost event because no Meshtastic interface is currently active"
            in c
            for c in debug_calls
        )


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestOnLostConnectionStaleInterface:
    def test_ignores_stale_interface_with_relay_active_client_id(self):
        active_client = MagicMock()
        stale_interface = MagicMock()
        mu.meshtastic_client = active_client
        mu._relay_active_client_id = id(active_client)
        mu.reconnecting = False
        mu.shutting_down = False

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_lost_meshtastic_connection(
                interface=stale_interface, detection_source="test"
            )

        assert mu.reconnecting is False
        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any("stale Meshtastic interface" in c for c in debug_calls)

    def test_ignores_stale_interface_without_relay_active_client_id(self):
        active_client = MagicMock()
        stale_interface = MagicMock()
        mu.meshtastic_client = active_client
        mu._relay_active_client_id = None
        mu.reconnecting = False
        mu.shutting_down = False

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_lost_meshtastic_connection(
                interface=stale_interface, detection_source="test"
            )

        assert mu.reconnecting is False
        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any("stale Meshtastic interface" in c for c in debug_calls)


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestOnMeshtasticMessageNoActiveClient:
    def test_ignores_packet_when_subscribed_to_messages(self):
        mu.meshtastic_client = None
        mu.subscribed_to_messages = True

        mock_interface = MagicMock()
        packet = {"decoded": {"text": "hello"}, "fromId": "!abc", "to": 4294967295}

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_meshtastic_message(packet, mock_interface)

        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any(
            "Ignoring packet because no Meshtastic interface is currently active" in c
            for c in debug_calls
        )

    def test_ignores_packet_when_reconnecting(self):
        mu.meshtastic_client = None
        mu.reconnecting = True

        mock_interface = MagicMock()
        packet = {"decoded": {"text": "hello"}, "fromId": "!abc", "to": 4294967295}

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_meshtastic_message(packet, mock_interface)

        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any(
            "Ignoring packet because no Meshtastic interface is currently active" in c
            for c in debug_calls
        )

    def test_ignores_packet_when_shutting_down(self):
        mu.meshtastic_client = None
        mu.shutting_down = True

        mock_interface = MagicMock()
        packet = {"decoded": {"text": "hello"}, "fromId": "!abc", "to": 4294967295}

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_meshtastic_message(packet, mock_interface)

        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any(
            "Shutdown in progress. Ignoring incoming messages." in c
            for c in debug_calls
        )
        assert not any(
            "Ignoring packet because no Meshtastic interface is currently active" in c
            for c in debug_calls
        )

    def test_ignores_packet_when_active_client_id_set(self):
        mu.meshtastic_client = None
        mu._relay_active_client_id = 12345

        mock_interface = MagicMock()
        packet = {"decoded": {"text": "hello"}, "fromId": "!abc", "to": 4294967295}

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_meshtastic_message(packet, mock_interface)

        error_calls = [str(c) for c in mock_logger.error.call_args_list]
        assert any(
            "Inconsistent relay state: active_client is None but active_client_id=" in c
            for c in error_calls
        )
