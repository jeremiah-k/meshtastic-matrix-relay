import time
from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestDeriveDisconnectDetectionSource:
    def test_known_source_returned(self):
        from mmrelay.meshtastic.events import _derive_disconnect_detection_source

        result = _derive_disconnect_detection_source(MagicMock(), "tcp_error", None)
        assert result == "tcp_error"

    def test_interface_source_used(self):
        from mmrelay.meshtastic.events import _derive_disconnect_detection_source

        iface = MagicMock()
        iface._last_disconnect_source = "ble.disconnect"
        result = _derive_disconnect_detection_source(iface, "unknown", None)
        assert result == "disconnect"

    def test_topic_name_used(self):
        from mmrelay.meshtastic.events import _derive_disconnect_detection_source

        topic = MagicMock()
        topic.getName.return_value = "meshtastic.connection.lost"
        result = _derive_disconnect_detection_source(MagicMock(), "unknown", topic)
        assert result == "meshtastic.connection.lost"

    def test_fallback_default(self):
        from mmrelay.meshtastic.events import _derive_disconnect_detection_source

        iface = MagicMock(spec=[])
        result = _derive_disconnect_detection_source(
            iface, "unknown", mu.pub.AUTO_TOPIC
        )
        assert result == "meshtastic.connection.lost"


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestTearDownMeshtasticClientForDisconnect:
    def test_no_client_returns(self):
        from mmrelay.meshtastic.events import (
            _tear_down_meshtastic_client_for_disconnect,
        )

        mu.meshtastic_client = None
        _tear_down_meshtastic_client_for_disconnect("test")

    def test_ble_interface_disconnect(self):
        from mmrelay.meshtastic.events import (
            _tear_down_meshtastic_client_for_disconnect,
        )

        mock_iface = MagicMock()
        mu.meshtastic_client = mock_iface
        mu.meshtastic_iface = mock_iface

        with patch.object(mu, "_disconnect_ble_interface"):
            _tear_down_meshtastic_client_for_disconnect("test")
        assert mu.meshtastic_iface is None

    def test_non_ble_client_close(self):
        from mmrelay.meshtastic.events import (
            _tear_down_meshtastic_client_for_disconnect,
        )

        client = MagicMock()
        mu.meshtastic_client = client
        mu.meshtastic_iface = None

        _tear_down_meshtastic_client_for_disconnect("test")
        client.close.assert_called_once()

    def test_os_error_on_close(self):
        from mmrelay.meshtastic.events import (
            _tear_down_meshtastic_client_for_disconnect,
        )

        client = MagicMock()
        client.close.side_effect = OSError()
        client.close.side_effect.errno = mu.ERRNO_BAD_FILE_DESCRIPTOR
        mu.meshtastic_client = client
        mu.meshtastic_iface = None

        _tear_down_meshtastic_client_for_disconnect("test")


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestOnLostMeshtasticConnection:
    def test_shutdown_returns(self):
        from mmrelay.meshtastic.events import on_lost_meshtastic_connection

        mu.shutting_down = True
        on_lost_meshtastic_connection()
        mu.shutting_down = False

    def test_reconnecting_returns(self):
        from mmrelay.meshtastic.events import on_lost_meshtastic_connection

        mu.shutting_down = False
        mu.reconnecting = True
        mu.meshtastic_client = MagicMock()
        on_lost_meshtastic_connection()
        mu.reconnecting = False

    def test_stale_interface_ignored(self):
        from mmrelay.meshtastic.events import on_lost_meshtastic_connection

        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = MagicMock()
        mu._relay_active_client_id = 99999

        stale_iface = MagicMock()
        on_lost_meshtastic_connection(interface=stale_iface)

    def test_no_client_with_callbacks_tearing_down(self):
        from mmrelay.meshtastic.events import on_lost_meshtastic_connection

        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = None
        mu._relay_active_client_id = None
        mu._callbacks_tearing_down = True
        mu.subscribed_to_connection_lost = False

        iface = MagicMock()
        on_lost_meshtastic_connection(interface=iface)

    def test_triggers_reconnect(self):
        from mmrelay.meshtastic.events import on_lost_meshtastic_connection

        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = MagicMock()
        mu._relay_active_client_id = None
        mu.meshtastic_iface = None
        mu._ble_future = None
        mu._ble_future_address = None
        mu._ble_executor_degraded_addresses = set()

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        mock_loop.is_running.return_value = True
        mu.event_loop = mock_loop

        with (
            patch.object(mu, "_disconnect_ble_interface"),
            patch.object(mu, "reset_executor_degraded_state"),
        ):
            on_lost_meshtastic_connection()
        assert mu.reconnecting is True
        mu.reconnecting = False


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestOnMeshtasticMessage:
    def test_empty_packet_returns(self):
        from mmrelay.meshtastic.events import on_meshtastic_message

        on_meshtastic_message({}, MagicMock())

    def test_none_packet_returns(self):
        from mmrelay.meshtastic.events import on_meshtastic_message

        on_meshtastic_message(None, MagicMock())

    def test_shutdown_returns(self):
        from mmrelay.meshtastic.events import on_meshtastic_message

        mu.shutting_down = True
        on_meshtastic_message({"decoded": {}}, MagicMock())
        mu.shutting_down = False

    def test_no_client_returns(self):
        from mmrelay.meshtastic.events import on_meshtastic_message

        mu.meshtastic_client = None
        mu._relay_active_client_id = None
        mu._callbacks_tearing_down = True
        mu.subscribed_to_messages = False
        mu.reconnecting = False
        mu.shutting_down = False
        on_meshtastic_message({"decoded": {}}, MagicMock())

    def test_stale_interface_ignored(self):
        from mmrelay.meshtastic.events import on_meshtastic_message

        mu.meshtastic_client = MagicMock()
        mu._relay_active_client_id = 99999
        mu.shutting_down = False
        mu._callbacks_tearing_down = False
        mu.subscribed_to_messages = False
        mu.reconnecting = False

        stale_iface = MagicMock()
        on_meshtastic_message({"decoded": {}}, stale_iface)

    def test_no_config_returns(self):
        from mmrelay.meshtastic.events import on_meshtastic_message

        iface = MagicMock()
        iface.myInfo.my_node_num = 12345
        mu.meshtastic_client = iface
        mu._relay_active_client_id = id(iface)
        mu.config = None
        mu.shutting_down = False
        mu._callbacks_tearing_down = False
        mu.subscribed_to_messages = False
        mu.reconnecting = False
        mu._relay_startup_drain_deadline_monotonic_secs = None
        mu._relay_rx_time_clock_skew_secs = None

        packet = {
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "hello"},
            "fromId": "!abc",
            "to": 4294967295,
            "rxTime": 0,
        }
        on_meshtastic_message(packet, iface)


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestScheduleStartupDrainDeadlineCleanup:
    def test_schedule_with_future_deadline(self):
        from mmrelay.meshtastic.events import _schedule_startup_drain_deadline_cleanup

        deadline = time.monotonic() + 10.0
        mu._relay_startup_drain_expiry_timer = None
        mu._relay_startup_drain_deadline_monotonic_secs = deadline

        _schedule_startup_drain_deadline_cleanup(deadline)
        assert mu._relay_startup_drain_expiry_timer is not None
        mu._relay_startup_drain_expiry_timer.cancel()
        mu._relay_startup_drain_expiry_timer = None

    def test_schedule_replaces_existing_timer(self):
        from mmrelay.meshtastic.events import _schedule_startup_drain_deadline_cleanup

        old_timer = MagicMock()
        mu._relay_startup_drain_expiry_timer = old_timer

        deadline = time.monotonic() + 10.0
        with patch("threading.Timer") as MockTimer:
            mock_timer = MagicMock()
            MockTimer.return_value = mock_timer
            _schedule_startup_drain_deadline_cleanup(deadline)
            old_timer.cancel.assert_called_once()


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestReconnect:
    @pytest.mark.asyncio
    async def test_shutdown_skips(self):
        from mmrelay.meshtastic.events import reconnect

        mu.shutting_down = True
        await reconnect()
        assert mu.reconnecting is False
