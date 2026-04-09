from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGetNodeDisplayName:
    def test_short_name_from_interface(self):
        from mmrelay.meshtastic.messaging import _get_node_display_name

        interface = MagicMock()
        interface.nodes = {
            "123": {
                "user": {
                    "shortName": "Short",
                    "longName": "Long Name",
                }
            }
        }
        result = _get_node_display_name("123", interface)
        assert result == "Short"

    def test_short_name_from_db_fallback(self):
        from mmrelay.meshtastic.messaging import _get_node_display_name

        interface = MagicMock()
        interface.nodes = {}
        with (
            patch("mmrelay.db_utils.get_shortname", return_value="DBShort"),
            patch("mmrelay.db_utils.get_longname", return_value="DBLong"),
        ):
            result = _get_node_display_name("456", interface)
        assert result == "DBShort"

    def test_long_name_from_db_fallback(self):
        from mmrelay.meshtastic.messaging import _get_node_display_name

        interface = MagicMock()
        interface.nodes = {}
        with (
            patch("mmrelay.db_utils.get_shortname", return_value=None),
            patch("mmrelay.db_utils.get_longname", return_value="DBLong"),
        ):
            result = _get_node_display_name("456", interface)
        assert result == "DBLong"

    def test_fallback_to_id(self):
        from mmrelay.meshtastic.messaging import _get_node_display_name

        interface = MagicMock()
        interface.nodes = {}
        with (
            patch("mmrelay.db_utils.get_shortname", return_value=None),
            patch("mmrelay.db_utils.get_longname", return_value=None),
        ):
            result = _get_node_display_name("456", interface)
        assert result == "456"

    def test_fallback_to_provided_fallback(self):
        from mmrelay.meshtastic.messaging import _get_node_display_name

        interface = MagicMock()
        interface.nodes = {}
        with (
            patch("mmrelay.db_utils.get_shortname", return_value=None),
            patch("mmrelay.db_utils.get_longname", return_value=None),
        ):
            result = _get_node_display_name("456", interface, fallback="custom")
        assert result == "custom"

    def test_no_interface(self):
        from mmrelay.meshtastic.messaging import _get_node_display_name

        with patch("mmrelay.db_utils.get_shortname", return_value="Short"):
            result = _get_node_display_name("123", None)
        assert result == "Short"


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestSendTextReply:
    def test_send_text_reply_none_interface(self):
        from mmrelay.meshtastic.messaging import send_text_reply

        result = send_text_reply(None, "hello", 123)
        assert result is None

    def test_send_text_reply_system_exit(self):
        from mmrelay.meshtastic.messaging import send_text_reply

        iface = MagicMock()
        iface._generatePacketId.return_value = 42
        iface._sendPacket.side_effect = SystemExit(0)
        with pytest.raises(SystemExit):
            send_text_reply(iface, "hello", 123)

    def test_send_text_reply_send_error(self):
        from mmrelay.meshtastic.messaging import send_text_reply

        iface = MagicMock()
        iface._generatePacketId.return_value = 42
        iface._sendPacket.side_effect = OSError("fail")
        result = send_text_reply(iface, "hello", 123)
        assert result is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestNormalizeRoomChannel:
    def test_valid_integer(self):
        from mmrelay.meshtastic.messaging import _normalize_room_channel

        assert _normalize_room_channel({"meshtastic_channel": 3}) == 3

    def test_string_integer(self):
        from mmrelay.meshtastic.messaging import _normalize_room_channel

        assert _normalize_room_channel({"meshtastic_channel": "2"}) == 2

    def test_none_returns_none(self):
        from mmrelay.meshtastic.messaging import _normalize_room_channel

        assert _normalize_room_channel({"meshtastic_channel": None}) is None

    def test_missing_key_returns_none(self):
        from mmrelay.meshtastic.messaging import _normalize_room_channel

        assert _normalize_room_channel({}) is None

    def test_invalid_value_returns_none(self):
        from mmrelay.meshtastic.messaging import _normalize_room_channel

        assert _normalize_room_channel({"meshtastic_channel": "abc"}) is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGetPacketDetails:
    def test_telemetry_device_metrics(self):
        from mmrelay.meshtastic.messaging import _get_packet_details

        decoded = {
            "telemetry": {
                "deviceMetrics": {"batteryLevel": 85, "voltage": 3.72},
            }
        }
        result = _get_packet_details(decoded, {}, "TELEMETRY_APP")
        assert result["batt"] == "85%"
        assert result["voltage"] == "3.72V"

    def test_telemetry_environment_metrics(self):
        from mmrelay.meshtastic.messaging import _get_packet_details

        decoded = {
            "telemetry": {
                "environmentMetrics": {
                    "temperature": 22.5,
                    "relativeHumidity": 60,
                },
            }
        }
        result = _get_packet_details(decoded, {}, "TELEMETRY_APP")
        assert "temp" in result
        assert "humidity" in result

    def test_signal_info(self):
        from mmrelay.meshtastic.messaging import _get_packet_details

        result = _get_packet_details({}, {"rxRssi": -70, "rxSnr": 7.5}, "UNKNOWN")
        assert "RSSI:-70" in result["signal"]
        assert "SNR:7.5" in result["signal"]

    def test_relay_node(self):
        from mmrelay.meshtastic.messaging import _get_packet_details

        result = _get_packet_details({}, {"relayNode": 42}, "UNKNOWN")
        assert result["relayed"] == "via 42"

    def test_relay_node_zero_excluded(self):
        from mmrelay.meshtastic.messaging import _get_packet_details

        result = _get_packet_details({}, {"relayNode": 0}, "UNKNOWN")
        assert "relayed" not in result

    def test_priority(self):
        from mmrelay.meshtastic.messaging import _get_packet_details

        result = _get_packet_details({}, {"priority": "HIGH"}, "UNKNOWN")
        assert result["priority"] == "HIGH"

    def test_normal_priority_excluded(self):
        from mmrelay.meshtastic.messaging import _get_packet_details

        result = _get_packet_details({}, {"priority": "NORMAL"}, "UNKNOWN")
        assert "priority" not in result

    def test_none_decoded(self):
        from mmrelay.meshtastic.messaging import _get_packet_details

        result = _get_packet_details(None, {}, "UNKNOWN")
        assert result == {}
