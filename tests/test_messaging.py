from unittest.mock import MagicMock, patch

import pytest

from mmrelay.meshtastic.messaging import (
    _get_node_display_name,
    _get_packet_details,
    _normalize_room_channel,
    send_text_reply,
)


class TestNormalizeRoomChannel:
    def test_valid_integer_channel(self):
        assert _normalize_room_channel({"meshtastic_channel": 0}) == 0

    def test_valid_string_channel(self):
        assert _normalize_room_channel({"meshtastic_channel": "2"}) == 2

    def test_missing_channel_returns_none(self):
        assert _normalize_room_channel({}) is None

    def test_none_channel_returns_none(self):
        assert _normalize_room_channel({"meshtastic_channel": None}) is None

    def test_invalid_string_channel_returns_none(self):
        with patch("mmrelay.meshtastic.messaging.facade.logger") as mock_logger:
            result = _normalize_room_channel(
                {"meshtastic_channel": "abc", "id": "!room:test"}
            )
            assert result is None
            mock_logger.warning.assert_called_once()

    def test_invalid_type_channel_returns_none(self):
        with patch("mmrelay.meshtastic.messaging.facade.logger"):
            result = _normalize_room_channel(
                {"meshtastic_channel": [1], "id": "!room:test"}
            )
            assert result is None

    def test_float_channel_truncated(self):
        assert _normalize_room_channel({"meshtastic_channel": 2.7}) == 2


class TestGetPacketDetails:
    def test_telemetry_device_metrics(self):
        decoded = {
            "telemetry": {"deviceMetrics": {"batteryLevel": 85, "voltage": 3.756}}
        }
        packet = {}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        assert result["batt"] == "85%"
        assert result["voltage"] == "3.76V"

    def test_telemetry_environment_metrics(self):
        decoded = {
            "telemetry": {
                "environmentMetrics": {
                    "temperature": 22.5,
                    "relativeHumidity": 60,
                }
            }
        }
        packet = {}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        assert result["temp"] == "22.5\u00b0C"
        assert result["humidity"] == "60%"

    def test_telemetry_both_metrics_prefers_device(self):
        decoded = {
            "telemetry": {
                "deviceMetrics": {"batteryLevel": 90},
                "environmentMetrics": {"temperature": 20},
            }
        }
        packet = {}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        assert "batt" in result
        assert "temp" not in result

    def test_telemetry_no_metrics_keys(self):
        decoded = {"telemetry": {}}
        packet = {}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        assert "batt" not in result

    def test_non_telemetry_portnum(self):
        decoded = {"some": "data"}
        packet = {}
        result = _get_packet_details(decoded, packet, "TEXT_MESSAGE_APP")
        assert "batt" not in result

    def test_none_decoded(self):
        result = _get_packet_details(None, {}, "TELEMETRY_APP")
        assert "batt" not in result

    def test_signal_info_rssi(self):
        packet = {"rxRssi": -70}
        result = _get_packet_details({}, packet, "TEXT_MESSAGE_APP")
        assert result["signal"] == "RSSI:-70"

    def test_signal_info_snr(self):
        packet = {"rxSnr": 7.5}
        result = _get_packet_details({}, packet, "TEXT_MESSAGE_APP")
        assert result["signal"] == "SNR:7.5"

    def test_signal_info_both(self):
        packet = {"rxRssi": -70, "rxSnr": 7.5}
        result = _get_packet_details({}, packet, "TEXT_MESSAGE_APP")
        assert result["signal"] == "RSSI:-70 SNR:7.5"

    def test_relay_info(self):
        packet = {"relayNode": 42}
        result = _get_packet_details({}, packet, "TEXT_MESSAGE_APP")
        assert result["relayed"] == "via 42"

    def test_relay_info_zero_excluded(self):
        packet = {"relayNode": 0}
        result = _get_packet_details({}, packet, "TEXT_MESSAGE_APP")
        assert "relayed" not in result

    def test_priority_info(self):
        packet = {"priority": "HIGH"}
        result = _get_packet_details({}, packet, "TEXT_MESSAGE_APP")
        assert result["priority"] == "HIGH"

    def test_priority_normal_excluded(self):
        packet = {"priority": "NORMAL"}
        result = _get_packet_details({}, packet, "TEXT_MESSAGE_APP")
        assert "priority" not in result

    def test_telemetry_battery_none(self):
        decoded = {"telemetry": {"deviceMetrics": {"voltage": 3.7}}}
        packet = {}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        assert "batt" not in result
        assert result["voltage"] == "3.70V"

    def test_telemetry_voltage_none(self):
        decoded = {"telemetry": {"deviceMetrics": {"batteryLevel": 50}}}
        packet = {}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        assert result["batt"] == "50%"
        assert "voltage" not in result

    def test_telemetry_temp_none(self):
        decoded = {"telemetry": {"environmentMetrics": {"relativeHumidity": 65}}}
        packet = {}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        assert "temp" not in result
        assert result["humidity"] == "65%"

    def test_telemetry_humidity_none(self):
        decoded = {"telemetry": {"environmentMetrics": {"temperature": 25.0}}}
        packet = {}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        assert result["temp"] == "25.0\u00b0C"
        assert "humidity" not in result

    def test_decoded_not_dict(self):
        result = _get_packet_details("not a dict", {}, "TELEMETRY_APP")
        assert "batt" not in result


class TestGetNodeDisplayName:
    def test_short_name_from_interface(self):
        interface = MagicMock()
        interface.nodes = {"123": {"user": {"shortName": "SN", "longName": "LongName"}}}
        result = _get_node_display_name(123, interface)
        assert result == "SN"

    def test_short_name_from_interface_string_id(self):
        interface = MagicMock()
        interface.nodes = {"456": {"user": {"shortName": "SN2"}}}
        result = _get_node_display_name("456", interface)
        assert result == "SN2"

    def test_no_interface_falls_back_to_db(self):
        with (
            patch("mmrelay.db_utils.get_shortname", return_value="DBShort"),
            patch("mmrelay.db_utils.get_longname", return_value="DBLong"),
        ):
            result = _get_node_display_name(123, None)
            assert result == "DBShort"

    def test_db_shortname_over_longname(self):
        interface = MagicMock()
        interface.nodes = {}
        with (
            patch("mmrelay.db_utils.get_shortname", return_value="DBShort"),
            patch("mmrelay.db_utils.get_longname", return_value="DBLong"),
        ):
            result = _get_node_display_name(123, interface)
            assert result == "DBShort"

    def test_fallback_to_longname(self):
        interface = MagicMock()
        interface.nodes = {}
        with (
            patch("mmrelay.db_utils.get_shortname", return_value=None),
            patch("mmrelay.db_utils.get_longname", return_value="DBLong"),
        ):
            result = _get_node_display_name(123, interface)
            assert result == "DBLong"

    def test_fallback_to_node_id(self):
        interface = MagicMock()
        interface.nodes = {}
        with (
            patch("mmrelay.db_utils.get_shortname", return_value=None),
            patch("mmrelay.db_utils.get_longname", return_value=None),
        ):
            result = _get_node_display_name(123, interface)
            assert result == "123"

    def test_explicit_fallback(self):
        interface = MagicMock()
        interface.nodes = {}
        with (
            patch("mmrelay.db_utils.get_shortname", return_value=None),
            patch("mmrelay.db_utils.get_longname", return_value=None),
        ):
            result = _get_node_display_name(123, interface, fallback="Unknown")
            assert result == "Unknown"

    def test_interface_nodes_not_dict(self):
        interface = MagicMock()
        interface.nodes = None
        with (
            patch("mmrelay.db_utils.get_shortname", return_value="S"),
            patch("mmrelay.db_utils.get_longname", return_value="L"),
        ):
            result = _get_node_display_name(123, interface)
            assert result == "S"

    def test_interface_nodes_empty_dict(self):
        interface = MagicMock()
        interface.nodes = {}
        with (
            patch("mmrelay.db_utils.get_shortname", return_value="S"),
            patch("mmrelay.db_utils.get_longname", return_value="L"),
        ):
            result = _get_node_display_name(123, interface)
            assert result == "S"

    def test_node_not_in_interface_nodes(self):
        interface = MagicMock()
        interface.nodes = {"999": {}}
        with (
            patch("mmrelay.db_utils.get_shortname", return_value="S"),
            patch("mmrelay.db_utils.get_longname", return_value="L"),
        ):
            result = _get_node_display_name(123, interface)
            assert result == "S"

    def test_node_user_missing_short_name(self):
        interface = MagicMock()
        interface.nodes = {"123": {"user": {"longName": "LN"}}}
        with (
            patch("mmrelay.db_utils.get_shortname", return_value="S"),
            patch("mmrelay.db_utils.get_longname", return_value="L"),
        ):
            result = _get_node_display_name(123, interface)
            assert result == "S"


class TestSendTextReply:
    def test_send_text_reply_success(self):
        interface = MagicMock()
        interface._generatePacketId.return_value = 42
        interface._sendPacket.return_value = "sent"
        result = send_text_reply(interface, "Hello", 100)
        assert result == "sent"
        interface._sendPacket.assert_called_once()

    def test_send_text_reply_none_interface(self):
        with patch("mmrelay.meshtastic.messaging.facade.logger") as mock_logger:
            result = send_text_reply(None, "Hello", 100)
            assert result is None
            mock_logger.error.assert_called_once()

    def test_send_text_reply_send_raises_os_error(self):
        interface = MagicMock()
        interface._generatePacketId.return_value = 42
        interface._sendPacket.side_effect = OSError("send failed")
        with patch("mmrelay.meshtastic.messaging.facade.logger"):
            result = send_text_reply(interface, "Hello", 100)
            assert result is None

    def test_send_text_reply_send_raises_runtime_error(self):
        interface = MagicMock()
        interface._generatePacketId.return_value = 42
        interface._sendPacket.side_effect = RuntimeError("runtime error")
        with patch("mmrelay.meshtastic.messaging.facade.logger"):
            result = send_text_reply(interface, "Hello", 100)
            assert result is None

    def test_send_text_reply_send_raises_attribute_error(self):
        interface = MagicMock()
        interface._generatePacketId.return_value = 42
        interface._sendPacket.side_effect = AttributeError("no method")
        with patch("mmrelay.meshtastic.messaging.facade.logger"):
            result = send_text_reply(interface, "Hello", 100)
            assert result is None

    def test_send_text_reply_send_raises_type_error(self):
        interface = MagicMock()
        interface._generatePacketId.return_value = 42
        interface._sendPacket.side_effect = TypeError("type error")
        with patch("mmrelay.meshtastic.messaging.facade.logger"):
            result = send_text_reply(interface, "Hello", 100)
            assert result is None

    def test_send_text_reply_send_raises_value_error(self):
        interface = MagicMock()
        interface._generatePacketId.return_value = 42
        interface._sendPacket.side_effect = ValueError("value error")
        with patch("mmrelay.meshtastic.messaging.facade.logger"):
            result = send_text_reply(interface, "Hello", 100)
            assert result is None

    def test_send_text_reply_system_exit_propagates(self):
        interface = MagicMock()
        interface._generatePacketId.return_value = 42
        interface._sendPacket.side_effect = SystemExit(0)
        with (
            patch("mmrelay.meshtastic.messaging.facade.logger"),
            pytest.raises(SystemExit),
        ):
            send_text_reply(interface, "Hello", 100)

    def test_send_text_reply_custom_params(self):
        interface = MagicMock()
        interface._generatePacketId.return_value = 42
        interface._sendPacket.return_value = "sent"
        result = send_text_reply(
            interface,
            "Reply",
            200,
            destinationId="^all",
            wantAck=True,
            channelIndex=3,
        )
        assert result == "sent"
        call_kwargs = interface._sendPacket.call_args
        assert call_kwargs[1]["destinationId"] == "^all"
        assert call_kwargs[1]["wantAck"] is True

    def test_send_text_reply_alias(self):
        from mmrelay.meshtastic.messaging import sendTextReply

        assert sendTextReply is send_text_reply
