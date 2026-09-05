from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from mmrelay.plugins.mesh_beacon_plugin import MeshBeaconConfigError, Plugin


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
        "targets": [
            {"preset": "LONG_FAST"},
            {"preset": "MEDIUM_FAST"},
            {"preset": "SHORT_FAST", "channel_index": 1},
        ],
        **config,
    }
    return plugin


def test_configures_native_cross_preset_beacon() -> None:
    plugin = _plugin(legacy_split=False)
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
        (_PRESETS["LONG_FAST"], _REGIONS["US"], None),
        (_PRESETS["MEDIUM_FAST"], _REGIONS["US"], None),
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
    plugin = _plugin(targets=[{"preset": "LONG_FAST"}])
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
        ({"targets": [{"preset": "MEDIUM_FAST"}] * 5}, "at most 4"),
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
    plugin = _plugin(targets=[{"preset": "MEDIUM_FAST"}])
    assert plugin.configure_firmware(interface) is True

    plugin = _plugin(targets=[{"preset": "SHORT_TURBO"}])
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
