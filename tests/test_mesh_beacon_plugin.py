from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from mmrelay.plugins.mesh_beacon_plugin import (
    MeshBeaconConfigError,
    Plugin,
    _channel_is_usable,
    _enum_name,
    _enum_number,
    _find_channel,
    _strict_bool,
)


class _EnumValue:
    def __init__(self, name: str, number: int) -> None:
        self.name = name
        self.number = number


class _EnumDescriptor:
    def __init__(self, values: dict[str, int]) -> None:
        self.values_by_name = {
            name: _EnumValue(name, number) for name, number in values.items()
        }
        self.values_by_number = {
            number: self.values_by_name[name] for name, number in values.items()
        }


class _FieldDescriptor:
    def __init__(self, enum: _EnumDescriptor) -> None:
        self.enum_type = enum


_PRESETS = {
    "LONG_FAST": 0,
    "MEDIUM_SLOW": 3,
    "MEDIUM_FAST": 4,
    "SHORT_SLOW": 5,
    "SHORT_FAST": 6,
    "LONG_MODERATE": 7,
    "SHORT_TURBO": 8,
}
_REGIONS = {"UNSET": 0, "US": 1, "EU_868": 3}


class _Lora:
    DESCRIPTOR = SimpleNamespace(
        fields_by_name={
            "modem_preset": _FieldDescriptor(_EnumDescriptor(_PRESETS)),
            "region": _FieldDescriptor(_EnumDescriptor(_REGIONS)),
        }
    )

    def __init__(
        self,
        *,
        use_preset: bool = True,
        region: int = _REGIONS["US"],
        modem_preset: int = _PRESETS["LONG_FAST"],
    ) -> None:
        self.use_preset = use_preset
        self.region = region
        self.modem_preset = modem_preset


class _ChannelSettings:
    def __init__(self, name: str = "", psk: bytes = b"") -> None:
        self.name = name
        self.psk = psk


class _Channel:
    def __init__(self, index: int, role: int, name: str, psk: bytes) -> None:
        self.index = index
        self.role = role
        self.settings = _ChannelSettings(name, psk)


class _Target:
    def __init__(
        self,
        *,
        preset: int | None = None,
        region: int = 0,
        channel_index: int | None = None,
    ) -> None:
        self.preset = preset
        self.region = region
        self.channel_index = channel_index

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Target) and vars(self) == vars(other)


class _Targets(list[_Target]):
    def add(self) -> _Target:
        target = _Target()
        self.append(target)
        return target


class _BeaconConfig:
    FLAG_LISTEN_ENABLED = 1
    FLAG_BROADCAST_ENABLED = 2
    FLAG_LEGACY_SPLIT = 4

    def __init__(self) -> None:
        self.flags = self.FLAG_LISTEN_ENABLED | self.FLAG_LEGACY_SPLIT
        self.broadcast_message = ""
        self.broadcast_offer_channel = _ChannelSettings()
        self.broadcast_offer_region = 0
        self.broadcast_offer_preset = 0
        self.broadcast_interval_secs = 0
        self.broadcast_targets = _Targets()

    def CopyFrom(self, other: _BeaconConfig) -> None:
        self.flags = other.flags
        self.broadcast_message = other.broadcast_message
        self.broadcast_offer_channel = copy.deepcopy(other.broadcast_offer_channel)
        self.broadcast_offer_region = other.broadcast_offer_region
        self.broadcast_offer_preset = other.broadcast_offer_preset
        self.broadcast_interval_secs = other.broadcast_interval_secs
        self.broadcast_targets = _Targets(copy.deepcopy(list(other.broadcast_targets)))

    def ClearField(self, field_name: str) -> None:
        if field_name == "broadcast_offer_channel":
            self.broadcast_offer_channel = _ChannelSettings()
        elif field_name == "broadcast_targets":
            self.broadcast_targets = _Targets()
        else:
            raise ValueError(field_name)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _BeaconConfig) and (
            self.flags,
            self.broadcast_message,
            self.broadcast_offer_channel.name,
            self.broadcast_offer_channel.psk,
            self.broadcast_offer_region,
            self.broadcast_offer_preset,
            self.broadcast_interval_secs,
            list(self.broadcast_targets),
        ) == (
            other.flags,
            other.broadcast_message,
            other.broadcast_offer_channel.name,
            other.broadcast_offer_channel.psk,
            other.broadcast_offer_region,
            other.broadcast_offer_preset,
            other.broadcast_interval_secs,
            list(other.broadcast_targets),
        )


class _ModuleConfig:
    def __init__(self, *, supported: bool = True) -> None:
        self.mesh_beacon = _BeaconConfig()
        self.supported = supported

    def HasField(self, field_name: str) -> bool:
        if field_name != "mesh_beacon":
            raise ValueError(field_name)
        return self.supported


class _LocalNode:
    def __init__(self, *, lora: _Lora | None = None, supported: bool = True) -> None:
        self.localConfig = SimpleNamespace(lora=lora or _Lora())
        self.moduleConfig = _ModuleConfig(supported=supported)
        self.channels = [
            _Channel(0, 1, "HomeMesh", b"home-key"),
            _Channel(1, 2, "BeaconPublic", b"\x01"),
            _Channel(2, 0, "", b""),
        ]
        self.writeConfig = MagicMock()


class _Interface:
    def __init__(
        self,
        *,
        lora: _Lora | None = None,
        supported: bool = True,
        allowed: tuple[int, ...] | None = tuple(_PRESETS.values()),
    ) -> None:
        self.localNode = _LocalNode(lora=lora, supported=supported)
        self._allowed = allowed

    def get_allowed_modem_presets(self, region: int) -> tuple[int, ...] | None:
        assert region == self.localNode.localConfig.lora.region
        return self._allowed


def _plugin(**config: Any) -> Plugin:
    plugin = Plugin()
    plugin.config = {
        "active": True,
        "broadcast": True,
        "message": "Join HomeMesh",
        "interval_seconds": 3600,
        "offer_channel_index": 0,
        "targets": [
            {"preset": "LONG_FAST", "channel_index": 0},
            {"preset": "MEDIUM_FAST", "channel_index": 0},
            {"preset": "SHORT_FAST", "channel_index": 1},
        ],
        **config,
    }
    return plugin


def test_configures_native_cross_preset_beacon() -> None:
    plugin = _plugin(legacy_split=False, listen=True)
    interface = _Interface()

    assert plugin.configure_firmware(interface) is True

    node = interface.localNode
    beacon = node.moduleConfig.mesh_beacon
    node.writeConfig.assert_called_once_with("mesh_beacon")
    assert beacon.flags & _BeaconConfig.FLAG_BROADCAST_ENABLED
    assert beacon.flags & _BeaconConfig.FLAG_LISTEN_ENABLED
    assert not beacon.flags & _BeaconConfig.FLAG_LEGACY_SPLIT
    assert beacon.broadcast_message == "Join HomeMesh"
    assert beacon.broadcast_interval_secs == 3600
    assert beacon.broadcast_offer_region == _REGIONS["US"]
    assert beacon.broadcast_offer_preset == _PRESETS["LONG_FAST"]
    assert beacon.broadcast_offer_channel.name == "HomeMesh"
    assert beacon.broadcast_offer_channel.psk == b"home-key"
    targets = [
        (target.preset, target.region, target.channel_index)
        for target in beacon.broadcast_targets
    ]
    assert targets == [
        (_PRESETS["LONG_FAST"], _REGIONS["US"], 0),
        (_PRESETS["MEDIUM_FAST"], _REGIONS["US"], 0),
        (_PRESETS["SHORT_FAST"], _REGIONS["US"], 1),
    ]


def test_does_not_rewrite_matching_firmware_config() -> None:
    plugin = _plugin()
    interface = _Interface()
    assert plugin.configure_firmware(interface) is True
    interface.localNode.writeConfig.reset_mock()

    assert plugin.configure_firmware(interface) is False
    interface.localNode.writeConfig.assert_not_called()


def test_rejects_same_preset_only_configuration() -> None:
    plugin = _plugin(targets=[{"preset": "LONG_FAST", "channel_index": 0}])
    interface = _Interface()

    with pytest.raises(MeshBeaconConfigError, match="at least one target"):
        plugin.configure_firmware(interface)
    interface.localNode.writeConfig.assert_not_called()


def test_rejects_firmware_without_mesh_beacon_module() -> None:
    plugin = _plugin()
    interface = _Interface(supported=False)

    with pytest.raises(MeshBeaconConfigError, match="Firmware 2.8"):
        plugin.configure_firmware(interface)
    interface.localNode.writeConfig.assert_not_called()


def test_rejects_custom_lora_configuration() -> None:
    plugin = _plugin()
    interface = _Interface(lora=_Lora(use_preset=False))

    with pytest.raises(MeshBeaconConfigError, match="standard LoRa modem preset"):
        plugin.configure_firmware(interface)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"interval_seconds": 3599}, "interval_seconds"),
        ({"message": "é" * 51}, "100 UTF-8 bytes"),
        ({"targets": [{"preset": "MEDIUM_FAST", "channel_index": 0}] * 5}, "at most 4"),
        (
            {"targets": [{"preset": "MEDIUM_FAST", "channel_index": 2}]},
            "enabled configured channel",
        ),
    ],
)
def test_rejects_invalid_broadcast_policy(
    override: dict[str, Any], message: str
) -> None:
    plugin = _plugin(**override)
    interface = _Interface()

    with pytest.raises(MeshBeaconConfigError, match=message):
        plugin.configure_firmware(interface)


def test_uses_conservative_presets_when_firmware_has_no_region_map() -> None:
    interface = _Interface(allowed=None)
    plugin = _plugin(targets=[{"preset": "MEDIUM_FAST", "channel_index": 0}])
    assert plugin.configure_firmware(interface) is True

    plugin = _plugin(targets=[{"preset": "SHORT_TURBO", "channel_index": 0}])
    with pytest.raises(MeshBeaconConfigError, match="not allowed"):
        plugin.configure_firmware(_Interface(allowed=None))


def test_explicit_null_offer_channel_keeps_psk_private() -> None:
    plugin = _plugin(offer_channel_index=None)
    interface = _Interface()

    assert plugin.configure_firmware(interface) is True
    beacon = interface.localNode.moduleConfig.mesh_beacon
    assert beacon.broadcast_offer_channel.name == ""
    assert beacon.broadcast_offer_channel.psk == b""


def test_write_failure_restores_cached_config() -> None:
    plugin = _plugin()
    interface = _Interface()
    node = interface.localNode
    original = copy.deepcopy(node.moduleConfig.mesh_beacon)
    node.writeConfig.side_effect = RuntimeError("radio write failed")

    with pytest.raises(RuntimeError, match="radio write failed"):
        plugin.configure_firmware(interface)
    assert node.moduleConfig.mesh_beacon == original


def test_disabled_broadcast_only_clears_broadcast_flag() -> None:
    plugin = _plugin(broadcast=False)
    interface = _Interface()
    beacon = interface.localNode.moduleConfig.mesh_beacon
    beacon.flags |= _BeaconConfig.FLAG_BROADCAST_ENABLED
    beacon.broadcast_message = "existing"

    assert plugin.configure_firmware(interface) is True
    assert not beacon.flags & _BeaconConfig.FLAG_BROADCAST_ENABLED
    assert beacon.flags & _BeaconConfig.FLAG_LISTEN_ENABLED
    assert beacon.broadcast_message == "existing"


def test_plugin_does_not_claim_matrix_or_mesh_messages() -> None:
    plugin = _plugin()
    assert plugin.get_matrix_commands() == []
    assert asyncio.run(plugin.handle_meshtastic_message({}, "", "", "")) is False
    assert asyncio.run(plugin.handle_room_message(None, None, "")) is False


def test_lifecycle_subscribes_to_connection_events(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _plugin()
    mock_pub = MagicMock()
    monkeypatch.setattr("mmrelay.plugins.mesh_beacon_plugin.pub", mock_pub)
    monkeypatch.setattr("mmrelay.meshtastic_utils.meshtastic_client", None)

    plugin.start()
    mock_pub.subscribe.assert_called_once_with(
        plugin._on_connection_established,
        "meshtastic.connection.established",
    )

    plugin.stop()
    mock_pub.unsubscribe.assert_called_once_with(
        plugin._on_connection_established,
        "meshtastic.connection.established",
    )


def test_requires_explicit_offer_and_target_channels() -> None:
    """Beacon configuration never chooses a credential-bearing channel implicitly."""
    plugin = _plugin()
    del plugin.config["offer_channel_index"]
    with pytest.raises(MeshBeaconConfigError, match="must be set explicitly"):
        plugin.configure_firmware(_Interface())

    plugin = _plugin(targets=[{"preset": "MEDIUM_FAST"}])
    with pytest.raises(MeshBeaconConfigError, match="channel_index must be specified"):
        plugin.configure_firmware(_Interface())


def test_explicit_secondary_offer_channel_shares_only_selected_credentials() -> None:
    """An explicit offer index copies exactly that configured channel into the offer."""
    plugin = _plugin(
        offer_channel_index=1,
        targets=[{"preset": "MEDIUM_FAST", "channel_index": 1}],
    )
    interface = _Interface()

    assert plugin.configure_firmware(interface) is True

    offer = interface.localNode.moduleConfig.mesh_beacon.broadcast_offer_channel
    assert offer.name == "BeaconPublic"
    assert offer.psk == b"\x01"


@pytest.mark.parametrize(
    ("configured_index", "message"),
    [
        (True, "integer or null"),
        (99, "is not configured"),
        (2, "disabled or blank"),
    ],
)
def test_rejects_invalid_offer_channel_selection(
    configured_index: Any, message: str
) -> None:
    """Explicit offer indexes must identify usable configured channel slots."""
    plugin = _plugin(offer_channel_index=configured_index)
    with pytest.raises(MeshBeaconConfigError, match=message):
        plugin.configure_firmware(_Interface())


def test_rejects_missing_channel_table_for_explicit_offer() -> None:
    """An explicit offer cannot be resolved when the radio exposes no channel table."""
    interface = _Interface()
    interface.localNode.channels = []
    with pytest.raises(MeshBeaconConfigError, match="channel configuration"):
        _plugin().configure_firmware(interface)


@pytest.mark.parametrize(
    ("targets", "message"),
    [
        (None, "at least one"),
        ([], "at least one"),
        (["MEDIUM_FAST"], "must be a mapping"),
        ([{"preset": None, "channel_index": 0}], "must be a preset name"),
        ([{"preset": "NOT_A_PRESET", "channel_index": 0}], "unknown modem_preset"),
        ([{"preset": "MEDIUM_FAST", "channel_index": True}], "must be an integer"),
        ([{"preset": "MEDIUM_FAST", "channel_index": 99}], "enabled configured channel"),
        (
            [
                {"preset": "MEDIUM_FAST", "channel_index": 0},
                {"preset": "medium-fast", "channel_index": 0},
            ],
            "duplicates an earlier",
        ),
    ],
)
def test_rejects_malformed_or_duplicate_targets(targets: Any, message: str) -> None:
    """Target validation rejects ambiguous, invalid, and duplicate radio destinations."""
    with pytest.raises(MeshBeaconConfigError, match=message):
        _plugin(targets=targets).configure_firmware(_Interface())


@pytest.mark.parametrize(
    ("override", "lora", "message"),
    [
        ({"message": 123}, None, "message must be a string"),
        ({"interval_seconds": True}, None, "must be an integer"),
        ({"interval_seconds": 0x100000000}, None, "must be between"),
        ({}, _Lora(region=0), "configured LoRa region"),
    ],
)
def test_rejects_invalid_scalar_broadcast_settings(
    override: dict[str, Any], lora: _Lora | None, message: str
) -> None:
    """Broadcast scalar values stay within firmware and protobuf constraints."""
    with pytest.raises(MeshBeaconConfigError, match=message):
        _plugin(**override).configure_firmware(_Interface(lora=lora))


def test_rejects_invalid_boolean_settings() -> None:
    """Boolean feature flags do not accept truthy scalar substitutes."""
    with pytest.raises(MeshBeaconConfigError, match="listen must be true or false"):
        _plugin(listen=1).configure_firmware(_Interface())
    assert _strict_bool({}, "listen", None) is None
    assert _strict_bool({"listen": False}, "listen", None) is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("no_local_node", "no local node"),
        ("no_module_config", "module configuration"),
        ("bad_schema", "schema does not expose"),
        ("no_lora", "LoRa configuration"),
    ],
)
def test_rejects_incomplete_connected_radio_state(mutation: str, message: str) -> None:
    """Configuration fails clearly when mtjk has not populated required local state."""
    interface = _Interface()
    if mutation == "no_local_node":
        interface.localNode = None
    elif mutation == "no_module_config":
        interface.localNode.moduleConfig = None
    elif mutation == "bad_schema":
        interface.localNode.moduleConfig.HasField = MagicMock(side_effect=ValueError("old schema"))
    else:
        interface.localNode.localConfig.lora = None

    with pytest.raises(MeshBeaconConfigError, match=message):
        _plugin().configure_firmware(interface)


def test_enum_and_channel_helpers_cover_schema_and_channel_edge_cases() -> None:
    """Low-level validation helpers fail closed for missing schema and unusable slots."""
    with pytest.raises(MeshBeaconConfigError, match="enum metadata"):
        _enum_number(SimpleNamespace(), "modem_preset", "LONG_FAST")
    assert _enum_number(_Lora(), "modem_preset", "medium-fast") == _PRESETS["MEDIUM_FAST"]
    assert _enum_name(_Lora(), "region", 999) == "999"
    assert _find_channel([], 0) is None
    assert _channel_is_usable(None) is False
    assert _channel_is_usable(SimpleNamespace(role=0)) is False
    assert _channel_is_usable(SimpleNamespace(role=2, settings=None)) is False
    assert _channel_is_usable(_Channel(3, 2, "", b"")) is False
    assert _channel_is_usable(_Channel(0, 1, "", b""), allow_blank_primary=True) is True
    assert _channel_is_usable(_Channel(3, 2, "named", b"")) is True
    assert _channel_is_usable(_Channel(3, 2, "", b"key")) is True


def test_allowed_presets_falls_back_without_interface_helper() -> None:
    """Conservative preset validation remains available on older mtjk interfaces."""
    allowed = Plugin._allowed_presets(SimpleNamespace(), _Lora(), _REGIONS["US"])
    assert _PRESETS["LONG_FAST"] in allowed
    assert _PRESETS["SHORT_TURBO"] not in allowed


def test_lifecycle_applies_connected_radio_once_and_handles_unsubscribe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lifecycle wiring configures an existing connection and tolerates stale subscriptions."""
    plugin = _plugin()
    plugin.logger = MagicMock()
    interface = _Interface()
    mock_pub = MagicMock()
    plugin._apply_safely = MagicMock()
    monkeypatch.setattr("mmrelay.plugins.mesh_beacon_plugin.pub", mock_pub)
    monkeypatch.setattr("mmrelay.meshtastic_utils.meshtastic_client", interface)

    plugin.start()
    plugin.start()

    mock_pub.subscribe.assert_called_once()
    assert plugin._apply_safely.call_count == 2
    mock_pub.unsubscribe.side_effect = RuntimeError("already gone")
    plugin.stop()
    plugin.on_stop()
    plugin.logger.debug.assert_any_call(
        "Mesh Beacon connection callback was already unsubscribed",
        exc_info=True,
    )


def test_connection_callback_delegates_to_safe_apply() -> None:
    """Connection events are routed through the guarded firmware configuration path."""
    plugin = _plugin()
    interface = _Interface()
    plugin._apply_safely = MagicMock()
    plugin._on_connection_established(interface)
    plugin._apply_safely.assert_called_once_with(interface)


@pytest.mark.parametrize(
    ("result", "exception", "logger_method"),
    [
        (True, None, "info"),
        (False, None, "debug"),
        (None, MeshBeaconConfigError("invalid"), "error"),
        (None, RuntimeError("write failed"), "exception"),
    ],
)
def test_safe_apply_logs_configuration_outcomes(
    result: bool | None, exception: Exception | None, logger_method: str
) -> None:
    """Background configuration reports success, no-op, policy errors, and unexpected failures."""
    plugin = _plugin()
    plugin.logger = MagicMock()
    if exception is None:
        plugin.configure_firmware = MagicMock(return_value=result)
    else:
        plugin.configure_firmware = MagicMock(side_effect=exception)

    plugin._apply_safely(_Interface())

    assert getattr(plugin.logger, logger_method).called


def test_description_identifies_firmware_native_cross_preset_behavior() -> None:
    """The core plugin description states the native cross-preset scope."""
    assert "Firmware 2.8" in _plugin().description
    assert "cross-preset" in _plugin().description
