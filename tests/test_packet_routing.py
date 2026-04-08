from mmrelay.constants.config import (
    DEFAULT_ENCRYPTED_ACTION,
)
from mmrelay.constants.formats import TEXT_MESSAGE_APP
from mmrelay.constants.messages import PORTNUM_TEXT_MESSAGE_APP
from mmrelay.meshtastic.packet_routing import (
    PacketAction,
    _get_encrypted_action,
    _get_packet_routing_overrides,
    _get_portnum_name,
    _is_encrypted_packet,
    _is_text_message_portnum,
    _resolve_portnum_set,
    _warn_once,
    classify_packet,
)


class TestWarnOnce:
    def test_warn_once_deduplicates_same_key(self):
        import mmrelay.meshtastic.packet_routing as pr

        original = pr._warned_packet_routing_issues.copy()
        try:
            pr._warned_packet_routing_issues.clear()
            from unittest.mock import patch

            with patch.object(pr.logger, "warning") as mock_warn:
                _warn_once("test_key", "Message %s", "arg")
                _warn_once("test_key", "Message %s", "arg")
                assert mock_warn.call_count == 1
                assert "test_key" in pr._warned_packet_routing_issues
        finally:
            pr._warned_packet_routing_issues.clear()
            pr._warned_packet_routing_issues.update(original)

    def test_warn_once_allows_different_keys(self):
        import mmrelay.meshtastic.packet_routing as pr

        original = pr._warned_packet_routing_issues.copy()
        try:
            pr._warned_packet_routing_issues.clear()
            from unittest.mock import patch

            with patch.object(pr.logger, "warning") as mock_warn:
                _warn_once("key_a", "Message A")
                _warn_once("key_b", "Message B")
                assert mock_warn.call_count == 2
        finally:
            pr._warned_packet_routing_issues.clear()
            pr._warned_packet_routing_issues.update(original)


class TestGetPortnumName:
    def test_get_portnum_name_empty_string(self):
        assert _get_portnum_name("") == "UNKNOWN (empty string)"

    def test_get_portnum_name_bool_type(self):
        assert _get_portnum_name(True) == "UNKNOWN (type=bool)"

    def test_get_portnum_name_float_type(self):
        assert _get_portnum_name(3.14) == "UNKNOWN (type=float)"

    def test_get_portnum_name_dict_type(self):
        assert _get_portnum_name({}) == "UNKNOWN (type=dict)"

    def test_get_portnum_name_unknown_int_portnum(self):
        result = _get_portnum_name(9999999)
        assert "UNKNOWN" in result
        assert "9999999" in result

    def test_get_portnum_name_valid_int_portnum(self):
        from unittest.mock import patch

        with patch(
            "mmrelay.meshtastic.packet_routing.portnums_pb2.PortNum.Name",
            return_value="TEXT_MESSAGE_APP",
        ):
            result = _get_portnum_name(PORTNUM_TEXT_MESSAGE_APP)
            assert result == "TEXT_MESSAGE_APP"

    def test_get_portnum_name_int_portnum_value_error(self):
        from unittest.mock import patch

        with patch(
            "mmrelay.meshtastic.packet_routing.portnums_pb2.PortNum.Name",
            side_effect=ValueError("unknown"),
        ):
            result = _get_portnum_name(9999999)
            assert "UNKNOWN" in result
            assert "9999999" in result

    def test_get_portnum_name_int_portnum_empty_name(self):
        from unittest.mock import patch

        with patch(
            "mmrelay.meshtastic.packet_routing.portnums_pb2.PortNum.Name",
            return_value="",
        ):
            result = _get_portnum_name(42)
            assert "UNKNOWN" in result
            assert "42" in result

    def test_get_portnum_name_none_with_encrypted_packet(self):
        result = _get_portnum_name(None, {"encrypted": True})
        assert result == "ENCRYPTED"

    def test_get_portnum_name_none_without_packet(self):
        result = _get_portnum_name(None)
        assert result == "UNKNOWN (None)"

    def test_get_portnum_name_none_with_non_encrypted_packet(self):
        result = _get_portnum_name(None, {})
        assert result == "UNKNOWN (None)"

    def test_get_portnum_name_string(self):
        result = _get_portnum_name("TELEMETRY_APP")
        assert result == "TELEMETRY_APP"


class TestIsEncryptedPacket:
    def test_encrypted_packet(self):
        assert _is_encrypted_packet({"encrypted": True}) is True

    def test_non_encrypted_packet(self):
        assert _is_encrypted_packet({"encrypted": False}) is False

    def test_missing_encrypted_key(self):
        assert _is_encrypted_packet({}) is False

    def test_none_packet(self):
        assert _is_encrypted_packet(None) is False


class TestIsTextMessagePortnum:
    def test_text_message_app_constant(self):
        assert _is_text_message_portnum(PORTNUM_TEXT_MESSAGE_APP) is True

    def test_text_message_app_string(self):
        assert _is_text_message_portnum(TEXT_MESSAGE_APP) is True

    def test_other_portnum(self):
        assert _is_text_message_portnum("TELEMETRY_APP") is False

    def test_none_portnum(self):
        assert _is_text_message_portnum(None) is False


class TestResolvePortnumSet:
    def test_resolve_portnum_set_empty_string_warns(self):
        from unittest.mock import patch

        import mmrelay.meshtastic.packet_routing as pr

        original = pr._warned_packet_routing_issues.copy()
        try:
            pr._warned_packet_routing_issues.clear()
            with patch.object(pr.logger, "warning") as mock_warn:
                result = _resolve_portnum_set("  ", "test_setting")
                assert result == frozenset()
                assert mock_warn.call_count == 1
        finally:
            pr._warned_packet_routing_issues.clear()
            pr._warned_packet_routing_issues.update(original)

    def test_resolve_portnum_set_invalid_type_dict(self):
        from unittest.mock import patch

        import mmrelay.meshtastic.packet_routing as pr

        original = pr._warned_packet_routing_issues.copy()
        try:
            pr._warned_packet_routing_issues.clear()
            with patch.object(pr.logger, "warning") as mock_warn:
                result = _resolve_portnum_set({"KEY": "value"}, "bad_setting")
                assert result == frozenset()
                assert mock_warn.call_count == 1
        finally:
            pr._warned_packet_routing_issues.clear()
            pr._warned_packet_routing_issues.update(original)

    def test_resolve_portnum_set_invalid_type_int(self):
        from unittest.mock import patch

        import mmrelay.meshtastic.packet_routing as pr

        original = pr._warned_packet_routing_issues.copy()
        try:
            pr._warned_packet_routing_issues.clear()
            with patch.object(pr.logger, "warning") as mock_warn:
                result = _resolve_portnum_set(42, "numeric_setting")
                assert result == frozenset()
                assert mock_warn.call_count == 1
        finally:
            pr._warned_packet_routing_issues.clear()
            pr._warned_packet_routing_issues.update(original)

    def test_resolve_portnum_set_tuple(self):
        result = _resolve_portnum_set(("TEXT_MESSAGE_APP", "TELEMETRY_APP"))
        assert result == frozenset({"TEXT_MESSAGE_APP", "TELEMETRY_APP"})

    def test_resolve_portnum_set_set(self):
        result = _resolve_portnum_set({"TEXT_MESSAGE_APP"})
        assert result == frozenset({"TEXT_MESSAGE_APP"})

    def test_resolve_portnum_set_frozenset(self):
        result = _resolve_portnum_set(frozenset({"TEXT_MESSAGE_APP"}))
        assert result == frozenset({"TEXT_MESSAGE_APP"})

    def test_resolve_portnum_set_with_unknown_entries_warns(self):
        from unittest.mock import patch

        import mmrelay.meshtastic.packet_routing as pr

        original = pr._warned_packet_routing_issues.copy()
        try:
            pr._warned_packet_routing_issues.clear()
            with patch.object(pr.logger, "warning") as mock_warn:
                result = _resolve_portnum_set(["TEXT_MESSAGE_APP", 9999999])
                assert "TEXT_MESSAGE_APP" in result
                assert 9999999 not in result
                assert mock_warn.call_count == 1
        finally:
            pr._warned_packet_routing_issues.clear()
            pr._warned_packet_routing_issues.update(original)

    def test_resolve_portnum_set_list_strips_whitespace(self):
        result = _resolve_portnum_set([" RANGE_TEST_APP "])
        assert result == frozenset({"RANGE_TEST_APP"})

    def test_resolve_portnum_set_list_whitespace_only_warns(self):
        from unittest.mock import patch

        import mmrelay.meshtastic.packet_routing as pr

        original = pr._warned_packet_routing_issues.copy()
        try:
            pr._warned_packet_routing_issues.clear()
            with patch.object(pr.logger, "warning") as mock_warn:
                result = _resolve_portnum_set(["  "])
                assert result == frozenset()
                assert mock_warn.call_count == 1
        finally:
            pr._warned_packet_routing_issues.clear()
            pr._warned_packet_routing_issues.update(original)

    def test_resolve_portnum_set_list_strips_whitespace_multiple(self):
        result = _resolve_portnum_set([" TEXT_MESSAGE_APP ", "TELEMETRY_APP"])
        assert result == frozenset({"TEXT_MESSAGE_APP", "TELEMETRY_APP"})


class TestGetPacketRoutingOverrides:
    def test_non_dict_config(self):
        chat, disabled = _get_packet_routing_overrides("not a dict")
        assert chat == frozenset()
        assert disabled == frozenset()

    def test_non_dict_meshtastic_section(self):
        config = {"meshtastic": "not_a_dict"}
        chat, disabled = _get_packet_routing_overrides(config)
        assert chat == frozenset()
        assert disabled == frozenset()

    def test_non_dict_routing_section(self):
        config = {"meshtastic": {"packet_routing": "not_a_dict"}}
        chat, disabled = _get_packet_routing_overrides(config)
        assert chat == frozenset()
        assert disabled == frozenset()

    def test_missing_routing_section(self):
        config = {"meshtastic": {}}
        chat, disabled = _get_packet_routing_overrides(config)
        assert chat == frozenset()
        assert disabled == frozenset()

    def test_full_config(self):
        config = {
            "meshtastic": {
                "packet_routing": {
                    "chat_portnums": ["RANGE_TEST_APP"],
                    "disabled_portnums": ["TELEMETRY_APP"],
                }
            }
        }
        chat, disabled = _get_packet_routing_overrides(config)
        assert chat == frozenset({"RANGE_TEST_APP"})
        assert disabled == frozenset({"TELEMETRY_APP"})


class TestGetEncryptedAction:
    def test_non_dict_config_returns_default(self):
        result = _get_encrypted_action("not_a_dict")
        assert result == DEFAULT_ENCRYPTED_ACTION

    def test_non_dict_meshtastic_section_returns_default(self):
        result = _get_encrypted_action({"meshtastic": "not_a_dict"})
        assert result == DEFAULT_ENCRYPTED_ACTION

    def test_non_dict_routing_section_returns_default(self):
        result = _get_encrypted_action({"meshtastic": {"packet_routing": "not_a_dict"}})
        assert result == DEFAULT_ENCRYPTED_ACTION

    def test_missing_encrypted_action_returns_default(self):
        result = _get_encrypted_action({"meshtastic": {"packet_routing": {}}})
        assert result == DEFAULT_ENCRYPTED_ACTION

    def test_plugin_only_action(self):
        result = _get_encrypted_action(
            {"meshtastic": {"packet_routing": {"encrypted_action": "plugin_only"}}}
        )
        assert result == PacketAction.PLUGIN_ONLY

    def test_drop_action(self):
        result = _get_encrypted_action(
            {"meshtastic": {"packet_routing": {"encrypted_action": "drop"}}}
        )
        assert result == PacketAction.DROP

    def test_upcase_action_normalizes(self):
        result = _get_encrypted_action(
            {"meshtastic": {"packet_routing": {"encrypted_action": "DROP"}}}
        )
        assert result == PacketAction.DROP

    def test_action_with_whitespace(self):
        result = _get_encrypted_action(
            {"meshtastic": {"packet_routing": {"encrypted_action": "  drop  "}}}
        )
        assert result == PacketAction.DROP

    def test_invalid_string_action_warns(self):
        from unittest.mock import patch

        import mmrelay.meshtastic.packet_routing as pr

        original = pr._warned_packet_routing_issues.copy()
        try:
            pr._warned_packet_routing_issues.clear()
            with patch.object(pr.logger, "warning") as mock_warn:
                result = _get_encrypted_action(
                    {
                        "meshtastic": {
                            "packet_routing": {"encrypted_action": "invalid_value"}
                        }
                    }
                )
                assert result == DEFAULT_ENCRYPTED_ACTION
                assert mock_warn.call_count == 1
        finally:
            pr._warned_packet_routing_issues.clear()
            pr._warned_packet_routing_issues.update(original)

    def test_non_string_action_warns(self):
        from unittest.mock import patch

        import mmrelay.meshtastic.packet_routing as pr

        original = pr._warned_packet_routing_issues.copy()
        try:
            pr._warned_packet_routing_issues.clear()
            with patch.object(pr.logger, "warning") as mock_warn:
                result = _get_encrypted_action(
                    {"meshtastic": {"packet_routing": {"encrypted_action": 42}}}
                )
                assert result == DEFAULT_ENCRYPTED_ACTION
                assert mock_warn.call_count == 1
        finally:
            pr._warned_packet_routing_issues.clear()
            pr._warned_packet_routing_issues.update(original)

    def test_bool_action_warns(self):
        from unittest.mock import patch

        import mmrelay.meshtastic.packet_routing as pr

        original = pr._warned_packet_routing_issues.copy()
        try:
            pr._warned_packet_routing_issues.clear()
            with patch.object(pr.logger, "warning") as mock_warn:
                result = _get_encrypted_action(
                    {"meshtastic": {"packet_routing": {"encrypted_action": True}}}
                )
                assert result == DEFAULT_ENCRYPTED_ACTION
                assert mock_warn.call_count == 1
        finally:
            pr._warned_packet_routing_issues.clear()
            pr._warned_packet_routing_issues.update(original)

    def test_list_action_warns(self):
        from unittest.mock import patch

        import mmrelay.meshtastic.packet_routing as pr

        original = pr._warned_packet_routing_issues.copy()
        try:
            pr._warned_packet_routing_issues.clear()
            with patch.object(pr.logger, "warning") as mock_warn:
                result = _get_encrypted_action(
                    {"meshtastic": {"packet_routing": {"encrypted_action": ["drop"]}}}
                )
                assert result == DEFAULT_ENCRYPTED_ACTION
                assert mock_warn.call_count == 1
        finally:
            pr._warned_packet_routing_issues.clear()
            pr._warned_packet_routing_issues.update(original)


class TestClassifyPacketDetectionSensor:
    def test_detection_sensor_enabled_without_chat_overrides(self):
        from mmrelay.constants.messages import PORTNUM_DETECTION_SENSOR_APP

        config = {
            "meshtastic": {
                "meshnet_name": "TestNet",
                "detection_sensor": True,
            }
        }
        action = classify_packet(PORTNUM_DETECTION_SENSOR_APP, config)
        assert action == PacketAction.RELAY

    def test_detection_sensor_enabled_with_chat_overrides(self):
        from mmrelay.constants.messages import PORTNUM_DETECTION_SENSOR_APP

        config = {
            "meshtastic": {
                "meshnet_name": "TestNet",
                "detection_sensor": True,
                "packet_routing": {
                    "chat_portnums": ["DETECTION_SENSOR_APP"],
                },
            }
        }
        action = classify_packet(PORTNUM_DETECTION_SENSOR_APP, config)
        assert action == PacketAction.RELAY

    def test_detection_sensor_disabled_without_chat_overrides(self):
        from mmrelay.constants.messages import PORTNUM_DETECTION_SENSOR_APP

        config = {
            "meshtastic": {
                "meshnet_name": "TestNet",
                "detection_sensor": False,
            }
        }
        action = classify_packet(PORTNUM_DETECTION_SENSOR_APP, config)
        assert action == PacketAction.PLUGIN_ONLY

    def test_detection_sensor_disabled_with_chat_overrides(self):
        from mmrelay.constants.messages import PORTNUM_DETECTION_SENSOR_APP

        config = {
            "meshtastic": {
                "meshnet_name": "TestNet",
                "detection_sensor": False,
                "packet_routing": {
                    "chat_portnums": ["DETECTION_SENSOR_APP"],
                },
            }
        }
        action = classify_packet(PORTNUM_DETECTION_SENSOR_APP, config)
        assert action == PacketAction.PLUGIN_ONLY

    def test_text_message_always_relays(self):
        config = {"meshtastic": {"meshnet_name": "TestNet"}}
        action = classify_packet(PORTNUM_TEXT_MESSAGE_APP, config)
        assert action == PacketAction.RELAY

    def test_unknown_portnum_defaults_to_plugin_only(self):
        config = {"meshtastic": {"meshnet_name": "TestNet"}}
        action = classify_packet("UNKNOWN_APP", config)
        assert action == PacketAction.PLUGIN_ONLY

    def test_chat_override_promotes_unknown_to_relay(self):
        config = {
            "meshtastic": {
                "meshnet_name": "TestNet",
                "packet_routing": {
                    "chat_portnums": ["RANGE_TEST_APP"],
                },
            }
        }
        action = classify_packet("RANGE_TEST_APP", config)
        assert action == PacketAction.RELAY

    def test_disabled_overrides_drop(self):
        config = {
            "meshtastic": {
                "meshnet_name": "TestNet",
                "packet_routing": {
                    "disabled_portnums": ["RANGE_TEST_APP"],
                },
            }
        }
        action = classify_packet("RANGE_TEST_APP", config)
        assert action == PacketAction.DROP

    def test_disabled_takes_precedence_over_chat(self):
        config = {
            "meshtastic": {
                "meshnet_name": "TestNet",
                "packet_routing": {
                    "chat_portnums": ["RANGE_TEST_APP"],
                    "disabled_portnums": ["RANGE_TEST_APP"],
                },
            }
        }
        action = classify_packet("RANGE_TEST_APP", config)
        assert action == PacketAction.DROP

    def test_none_config_defaults_text_message_to_relay(self):
        action = classify_packet(PORTNUM_TEXT_MESSAGE_APP, None)
        assert action == PacketAction.RELAY

    def test_none_config_unknown_portnum_plugin_only(self):
        action = classify_packet("SOME_APP", None)
        assert action == PacketAction.PLUGIN_ONLY

    def test_encrypted_packet_drop(self):
        config = {
            "meshtastic": {
                "meshnet_name": "TestNet",
                "packet_routing": {"encrypted_action": "drop"},
            }
        }
        action = classify_packet(None, config, {"encrypted": True})
        assert action == PacketAction.DROP

    def test_encrypted_packet_plugin_only_default(self):
        config = {"meshtastic": {"meshnet_name": "TestNet"}}
        action = classify_packet(None, config, {"encrypted": True})
        assert action == PacketAction.PLUGIN_ONLY

    def test_encrypted_packet_ignores_disabled_portnums(self):
        config = {
            "meshtastic": {
                "meshnet_name": "TestNet",
                "packet_routing": {
                    "encrypted_action": "plugin_only",
                    "disabled_portnums": ["ENCRYPTED"],
                },
            }
        }
        action = classify_packet(None, config, {"encrypted": True})
        assert action == PacketAction.PLUGIN_ONLY
