import time
from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestExtractPacketRequestId:
    def test_non_dict_returns_none(self):
        from mmrelay.meshtastic.health import _extract_packet_request_id

        assert _extract_packet_request_id("not_a_dict") is None

    def test_dict_with_request_id(self):
        from mmrelay.meshtastic.health import _extract_packet_request_id

        assert _extract_packet_request_id({"requestId": 42}) == 42

    def test_dict_with_decoded_request_id(self):
        from mmrelay.meshtastic.health import _extract_packet_request_id

        assert _extract_packet_request_id({"decoded": {"requestId": 99}}) == 99

    def test_dict_with_no_request_id(self):
        from mmrelay.meshtastic.health import _extract_packet_request_id

        assert _extract_packet_request_id({}) is None

    def test_dict_with_zero_request_id(self):
        from mmrelay.meshtastic.health import _extract_packet_request_id

        assert _extract_packet_request_id({"requestId": 0}) is None

    def test_dict_with_negative_request_id(self):
        from mmrelay.meshtastic.health import _extract_packet_request_id

        assert _extract_packet_request_id({"requestId": -1}) is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestPruneHealthProbeTracking:
    def test_prunes_expired(self):
        from mmrelay.meshtastic.health import _prune_health_probe_tracking

        mu._health_probe_request_deadlines = {1: 100.0, 2: 200.0}
        _prune_health_probe_tracking(now=150.0)
        assert 1 not in mu._health_probe_request_deadlines
        assert 2 in mu._health_probe_request_deadlines


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestTrackHealthProbeRequestId:
    def test_returns_none_for_invalid_id(self):
        from mmrelay.meshtastic.health import _track_health_probe_request_id

        result = _track_health_probe_request_id("invalid", 10.0)
        assert result is None

    def test_tracks_valid_id(self):
        from mmrelay.meshtastic.health import _track_health_probe_request_id

        result = _track_health_probe_request_id(42, 10.0)
        assert result == 42
        assert 42 in mu._health_probe_request_deadlines


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestSeedConnectTimeSkew:
    def test_zero_rx_time_returns_false(self):
        from mmrelay.meshtastic.health import _seed_connect_time_skew

        assert _seed_connect_time_skew(0) is False

    def test_already_calibrated_returns_false(self):
        from mmrelay.meshtastic.health import _seed_connect_time_skew

        mu._relay_rx_time_clock_skew_secs = 1.0
        assert _seed_connect_time_skew(time.time()) is False

    def test_calibrates_from_post_start_packet(self):
        from mmrelay.meshtastic.health import _seed_connect_time_skew

        mu._relay_rx_time_clock_skew_secs = None
        mu.RELAY_START_TIME = time.time() - 10
        mu._relay_connection_started_monotonic_secs = time.monotonic() - 1
        mu._relay_startup_drain_deadline_monotonic_secs = time.monotonic() + 10
        mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = None

        rx_time = time.time()
        result = _seed_connect_time_skew(rx_time)
        assert result is True
        assert mu._relay_rx_time_clock_skew_secs is not None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestIsHealthProbeResponsePacket:
    def test_no_request_id(self):
        from mmrelay.meshtastic.health import _is_health_probe_response_packet

        assert _is_health_probe_response_packet({}, MagicMock()) is False

    def test_different_sender(self):
        from mmrelay.meshtastic.health import _is_health_probe_response_packet

        packet = {"requestId": 42, "from": 9999}
        interface = MagicMock()
        interface.myInfo.my_node_num = 1234
        mu._health_probe_request_deadlines = {42: time.monotonic() + 60}
        assert _is_health_probe_response_packet(packet, interface) is False

    def test_valid_response(self):
        from mmrelay.meshtastic.health import _is_health_probe_response_packet

        packet = {"requestId": 42, "from": 1234}
        interface = MagicMock()
        interface.myInfo.my_node_num = 1234
        mu._health_probe_request_deadlines = {42: time.monotonic() + 60}
        assert _is_health_probe_response_packet(packet, interface) is True


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestClaimHealthProbeResponseAndMaybeCalibrate:
    def test_no_request_id(self):
        from mmrelay.meshtastic.health import (
            _claim_health_probe_response_and_maybe_calibrate,
        )

        assert (
            _claim_health_probe_response_and_maybe_calibrate({}, MagicMock(), 0)
            is False
        )

    def test_claims_and_calibrates(self):
        from mmrelay.meshtastic.health import (
            _claim_health_probe_response_and_maybe_calibrate,
        )

        mu._relay_rx_time_clock_skew_secs = None
        mu._health_probe_request_deadlines = {42: time.monotonic() + 60}
        packet = {"requestId": 42, "from": 1234}
        interface = MagicMock()
        interface.myInfo.my_node_num = 1234

        result = _claim_health_probe_response_and_maybe_calibrate(
            packet, interface, time.time()
        )
        assert result is True
        assert 42 not in mu._health_probe_request_deadlines


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestSetProbeAckFlagFromPacket:
    def test_no_iface(self):
        from mmrelay.meshtastic.health import _set_probe_ack_flag_from_packet

        local_node = MagicMock()
        local_node.iface = None
        result = _set_probe_ack_flag_from_packet(local_node, {})
        assert result is False

    def test_no_ack_state(self):
        from mmrelay.meshtastic.health import _set_probe_ack_flag_from_packet

        local_node = MagicMock()
        local_node.iface._acknowledgment = None
        result = _set_probe_ack_flag_from_packet(local_node, {})
        assert result is False

    def test_sets_received_impl_ack(self):
        from mmrelay.meshtastic.health import _set_probe_ack_flag_from_packet

        local_node = MagicMock()
        iface = local_node.iface
        iface.localNode.nodeNum = 1234
        iface._acknowledgment.receivedImplAck = False
        result = _set_probe_ack_flag_from_packet(local_node, {"from": 1234})
        assert result is True

    def test_sets_received_ack(self):
        from mmrelay.meshtastic.health import _set_probe_ack_flag_from_packet

        local_node = MagicMock()
        iface = local_node.iface
        del iface.localNode
        iface._acknowledgment.receivedAck = False
        result = _set_probe_ack_flag_from_packet(local_node, {})
        assert result is True


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestResetProbeAckState:
    def test_calls_reset_when_available(self):
        from mmrelay.meshtastic.health import _reset_probe_ack_state

        ack = MagicMock()
        _reset_probe_ack_state(ack)
        ack.reset.assert_called_once()

    def test_manually_clears_flags(self):
        from mmrelay.meshtastic.health import _reset_probe_ack_state

        ack = MagicMock(spec=[])
        ack.receivedAck = True
        ack.receivedNak = True
        ack.receivedImplAck = True
        _reset_probe_ack_state(ack)
        assert ack.receivedAck is False
        assert ack.receivedNak is False
        assert ack.receivedImplAck is False


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestHandleProbeAckCallback:
    def test_no_ack_state_raises(self):
        from mmrelay.meshtastic.health import _handle_probe_ack_callback

        local_node = MagicMock()
        local_node.iface._acknowledgment = None
        with pytest.raises(RuntimeError):
            _handle_probe_ack_callback(local_node, {})

    def test_nak_with_error_reason(self):
        from mmrelay.meshtastic.health import _handle_probe_ack_callback

        local_node = MagicMock()
        ack = local_node.iface._acknowledgment
        packet = {"decoded": {"routing": {"errorReason": "NO_ROUTE"}}}
        _handle_probe_ack_callback(local_node, packet)
        assert ack.receivedNak is True

    def test_no_routing_uses_fallback(self):
        from mmrelay.meshtastic.health import _handle_probe_ack_callback

        local_node = MagicMock()
        ack = local_node.iface._acknowledgment
        local_node.iface.localNode.nodeNum = 1234
        _handle_probe_ack_callback(local_node, {"decoded": {}, "from": 1234})
        assert ack.receivedImplAck is True

    def test_routing_error_reason_none_falls_through(self):
        from mmrelay.meshtastic.health import _handle_probe_ack_callback

        local_node = MagicMock()
        ack = local_node.iface._acknowledgment
        ack.receivedNak = False
        local_node.iface.localNode.nodeNum = 1234
        packet = {"decoded": {"routing": {}}, "from": 1234}
        _handle_probe_ack_callback(local_node, packet)
        assert ack.receivedNak is False
        assert ack.receivedImplAck is True

    def test_routing_error_reason_none_string_falls_through(self):
        from mmrelay.meshtastic.health import _handle_probe_ack_callback

        local_node = MagicMock()
        ack = local_node.iface._acknowledgment
        ack.receivedNak = False
        local_node.iface.localNode.nodeNum = 1234
        packet = {"decoded": {"routing": {"errorReason": "NONE"}}, "from": 1234}
        _handle_probe_ack_callback(local_node, packet)
        assert ack.receivedNak is False
        assert ack.receivedImplAck is True


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestWaitForProbeAck:
    def test_none_raises(self):
        from mmrelay.meshtastic.health import _wait_for_probe_ack

        with pytest.raises(RuntimeError, match="missing acknowledgment"):
            _wait_for_probe_ack(None, 1.0)

    def test_ack_already_set(self):
        from mmrelay.meshtastic.health import _wait_for_probe_ack

        ack = MagicMock()
        ack.receivedAck = True
        ack.receivedNak = False
        ack.receivedImplAck = False
        _wait_for_probe_ack(ack, 1.0)
        ack.reset.assert_called_once()

    def test_timeout_raises(self):
        from mmrelay.meshtastic.health import _wait_for_probe_ack

        ack = MagicMock()
        ack.receivedAck = False
        ack.receivedNak = False
        ack.receivedImplAck = False
        with patch.object(mu.time, "sleep"):
            with pytest.raises(TimeoutError):
                _wait_for_probe_ack(ack, 0.01)


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestRequiresContinuousHealthMonitor:
    def test_ble_returns_false(self):
        from mmrelay.meshtastic.health import requires_continuous_health_monitor

        config = {"meshtastic": {"connection_type": "ble"}}
        assert requires_continuous_health_monitor(config) is False

    def test_disabled_returns_false(self):
        from mmrelay.meshtastic.health import requires_continuous_health_monitor

        config = {
            "meshtastic": {
                "connection_type": "tcp",
                "health_check": {"enabled": False},
            }
        }
        assert requires_continuous_health_monitor(config) is False

    def test_tcp_enabled_returns_true(self):
        from mmrelay.meshtastic.health import requires_continuous_health_monitor

        config = {
            "meshtastic": {
                "connection_type": "tcp",
                "health_check": {"enabled": True},
            }
        }
        assert requires_continuous_health_monitor(config) is True


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestParseHealthCheckConfig:
    def test_returns_none_when_disabled(self):
        from mmrelay.meshtastic.health import _parse_health_check_config

        config = {
            "meshtastic": {
                "connection_type": "tcp",
                "health_check": {"enabled": False},
            }
        }
        assert _parse_health_check_config(config) is None

    def test_returns_parsed_when_enabled(self):
        from mmrelay.meshtastic.health import _parse_health_check_config

        config = {
            "meshtastic": {
                "connection_type": "tcp",
                "health_check": {"enabled": True},
            }
        }
        result = _parse_health_check_config(config)
        assert result is not None
        assert result[0] == "tcp"

    def test_invalid_meshtastic_section(self):
        from mmrelay.meshtastic.health import _parse_health_check_config

        config = {"meshtastic": "not_a_dict"}
        result = _parse_health_check_config(config)
        assert result is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestProbeDeviceConnection:
    def test_no_local_node_raises(self):
        from mmrelay.meshtastic.health import _probe_device_connection

        client = MagicMock()
        client.localNode = None
        with pytest.raises(RuntimeError):
            _probe_device_connection(client)

    def test_no_send_data_raises(self):
        from mmrelay.meshtastic.health import _probe_device_connection

        client = MagicMock()
        client.localNode = MagicMock()
        client.sendData = None
        with pytest.raises(RuntimeError):
            _probe_device_connection(client)


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestCheckConnection:
    @pytest.mark.asyncio
    async def test_no_config_returns(self):
        from mmrelay.meshtastic.health import check_connection

        mu.config = None
        await check_connection()

    @pytest.mark.asyncio
    async def test_ble_returns_early(self):
        from mmrelay.meshtastic.health import check_connection

        mu.config = {"meshtastic": {"connection_type": "ble"}}
        await check_connection()
