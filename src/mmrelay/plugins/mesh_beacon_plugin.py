from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, cast

from pubsub import pub

from mmrelay.plugins.base_plugin import BasePlugin

_CONNECTION_TOPIC = "meshtastic.connection.established"
_MIN_INTERVAL_SECONDS = 3600
_MAX_INTERVAL_SECONDS = 0xFFFFFFFF
_MAX_MESSAGE_BYTES = 100
_MAX_TARGETS = 4
_FLAG_LISTEN_ENABLED = 1
_FLAG_BROADCAST_ENABLED = 2
_FLAG_LEGACY_SPLIT = 4
_CONSERVATIVE_PRESETS = frozenset(
    {
        "LONG_FAST",
        "LONG_MODERATE",
        "MEDIUM_SLOW",
        "MEDIUM_FAST",
        "SHORT_SLOW",
        "SHORT_FAST",
    }
)


class MeshBeaconConfigError(ValueError):
    """Raised when the configured firmware Mesh Beacon policy is invalid."""


def _strict_bool(
    config: Mapping[str, Any], key: str, default: bool | None
) -> bool | None:
    """Return a boolean option without accepting truthy non-boolean values."""
    if key not in config:
        return default
    value = config[key]
    if isinstance(value, bool):
        return value
    raise MeshBeaconConfigError(f"{key} must be true or false")


def _normalize_enum_name(value: str) -> str:
    """Normalize human-readable enum names to protobuf enum spelling."""
    return value.strip().upper().replace("-", "_").replace(" ", "_")


def _enum_descriptor(message: Any, field_name: str) -> Any:
    """Return enum metadata for a protobuf field or fail closed."""
    descriptor = getattr(message, "DESCRIPTOR", None)
    fields = getattr(descriptor, "fields_by_name", {})
    field = fields.get(field_name) if hasattr(fields, "get") else None
    enum = getattr(field, "enum_type", None)
    if enum is None:
        raise MeshBeaconConfigError(
            f"connected mtjk schema does not expose enum metadata for {field_name}"
        )
    return enum


def _enum_number(message: Any, field_name: str, configured_name: str) -> int:
    """Resolve a configured enum name to its protobuf numeric value."""
    enum = _enum_descriptor(message, field_name)
    name = _normalize_enum_name(configured_name)
    values = getattr(enum, "values_by_name", {})
    value = values.get(name) if hasattr(values, "get") else None
    if value is None:
        choices = ", ".join(sorted(str(item) for item in values))
        raise MeshBeaconConfigError(
            f"unknown {field_name} value {configured_name!r}; "
            f"expected one of: {choices}"
        )
    return int(value.number)


def _enum_name(message: Any, field_name: str, number: int) -> str:
    """Resolve an enum number to its name, preserving unknown numbers as text."""
    enum = _enum_descriptor(message, field_name)
    values = getattr(enum, "values_by_number", {})
    value = values.get(int(number)) if hasattr(values, "get") else None
    return str(value.name) if value is not None else str(number)


def _copy_message(message: Any) -> Any:
    """Clone a protobuf-style message without sharing mutable state."""
    clone = type(message)()
    clone.CopyFrom(message)
    return clone


def _find_channel(channels: Sequence[Any], channel_index: int) -> Any | None:
    """Find a configured channel by its device slot index."""
    for channel in channels:
        if int(getattr(channel, "index", -1)) == channel_index:
            return channel
    return None


def _channel_is_usable(channel: Any, *, allow_blank_primary: bool = False) -> bool:
    """Return whether a channel slot can be used for beacon offer or TX."""
    if channel is None or int(getattr(channel, "role", 0)) == 0:
        return False
    settings = getattr(channel, "settings", None)
    if settings is None:
        return False
    if allow_blank_primary and int(getattr(channel, "role", 0)) == 1:
        return True
    return bool(getattr(settings, "name", "") or bytes(getattr(settings, "psk", b"")))


class Plugin(BasePlugin):
    plugin_name = "mesh_beacon"
    is_core_plugin = True

    def __init__(self, plugin_name: str | None = None) -> None:
        super().__init__(plugin_name)
        self._connection_subscribed = False
        self._configure_lock = threading.Lock()

    @property
    def description(self) -> str:
        """Describe the firmware-native cross-preset beacon controller."""
        return "Configure Firmware 2.8 native cross-preset Mesh Beacon broadcasts"

    def get_matrix_commands(self) -> list[str]:
        """Expose no Matrix commands; configuration is applied from plugin settings."""
        return []

    def start(self) -> None:
        """Subscribe to connections and configure an already-connected radio."""
        super().start()
        if not self._connection_subscribed:
            pub.subscribe(self._on_connection_established, _CONNECTION_TOPIC)
            self._connection_subscribed = True

        from mmrelay import meshtastic_utils

        interface = meshtastic_utils.meshtastic_client
        if interface is not None and getattr(interface, "localNode", None) is not None:
            self._apply_safely(interface)

    def on_stop(self) -> None:
        """Remove the connection subscription when the plugin stops."""
        if not self._connection_subscribed:
            return
        try:
            pub.unsubscribe(self._on_connection_established, _CONNECTION_TOPIC)
        except Exception:
            self.logger.debug(
                "Mesh Beacon connection callback was already unsubscribed",
                exc_info=True,
            )
        self._connection_subscribed = False

    def _on_connection_established(self, interface: Any) -> None:
        """Apply beacon policy whenever mtjk establishes a radio connection."""
        self._apply_safely(interface)

    def _apply_safely(self, interface: Any) -> None:
        """Apply configuration while containing policy and transport failures."""
        try:
            with self._configure_lock:
                changed = self.configure_firmware(interface)
        except MeshBeaconConfigError as exc:
            self.logger.error("Mesh Beacon configuration not applied: %s", exc)
        except Exception:
            self.logger.exception("Failed to configure firmware Mesh Beacon module")
        else:
            if changed:
                self.logger.info("Applied firmware Mesh Beacon configuration")
            else:
                self.logger.debug(
                    "Firmware Mesh Beacon configuration is already current"
                )

    def configure_firmware(self, interface: Any) -> bool:
        """Validate and write the desired native Mesh Beacon module configuration."""
        local_node = getattr(interface, "localNode", None)
        if local_node is None:
            raise MeshBeaconConfigError("connected interface has no local node")

        module_config = getattr(local_node, "moduleConfig", None)
        if module_config is None:
            raise MeshBeaconConfigError("local module configuration is not available")
        try:
            beacon_supported = bool(module_config.HasField("mesh_beacon"))
        except (ValueError, TypeError, AttributeError) as exc:
            raise MeshBeaconConfigError(
                "mtjk schema does not expose the mesh_beacon module"
            ) from exc
        if not beacon_supported:
            raise MeshBeaconConfigError(
                "connected firmware does not expose the Firmware 2.8 Mesh Beacon module"
            )

        local_config = getattr(local_node, "localConfig", None)
        lora = getattr(local_config, "lora", None)
        if lora is None:
            raise MeshBeaconConfigError("local LoRa configuration is not available")

        existing = module_config.mesh_beacon
        desired = _copy_message(existing)
        broadcast = _strict_bool(self.config, "broadcast", True)
        listen = _strict_bool(self.config, "listen", None)
        legacy_split = _strict_bool(self.config, "legacy_split", None)

        flags = int(getattr(desired, "flags", 0))
        flags = self._set_flag(flags, _FLAG_BROADCAST_ENABLED, bool(broadcast))
        if listen is not None:
            flags = self._set_flag(flags, _FLAG_LISTEN_ENABLED, listen)
        if legacy_split is not None:
            flags = self._set_flag(flags, _FLAG_LEGACY_SPLIT, legacy_split)
        desired.flags = flags

        if broadcast:
            self._configure_broadcast(interface, local_node, lora, desired)

        if desired == existing:
            return False

        original = _copy_message(existing)
        existing.CopyFrom(desired)
        try:
            local_node.writeConfig("mesh_beacon")
        except Exception:
            existing.CopyFrom(original)
            raise
        return True

    @staticmethod
    def _set_flag(flags: int, flag: int, enabled: bool) -> int:
        """Set or clear one Mesh Beacon bitfield flag."""
        return flags | flag if enabled else flags & ~flag

    def _configure_broadcast(
        self,
        interface: Any,
        local_node: Any,
        lora: Any,
        desired: Any,
    ) -> None:
        """Populate broadcast values that are safe for the connected radio."""
        if not bool(getattr(lora, "use_preset", False)):
            raise MeshBeaconConfigError(
                "broadcast requires the radio to use a standard LoRa modem preset"
            )

        current_region = int(getattr(lora, "region", 0))
        if current_region == 0:
            raise MeshBeaconConfigError("broadcast requires a configured LoRa region")
        current_preset = int(getattr(lora, "modem_preset", 0))

        message = self.config.get("message", getattr(desired, "broadcast_message", ""))
        if not isinstance(message, str):
            raise MeshBeaconConfigError("message must be a string")
        if len(message.encode("utf-8")) > _MAX_MESSAGE_BYTES:
            raise MeshBeaconConfigError(
                f"message must be at most {_MAX_MESSAGE_BYTES} UTF-8 bytes"
            )
        desired.broadcast_message = message

        current_interval = int(getattr(desired, "broadcast_interval_secs", 0))
        default_interval = max(current_interval, _MIN_INTERVAL_SECONDS)
        interval = self.config.get("interval_seconds", default_interval)
        if isinstance(interval, bool) or not isinstance(interval, int):
            raise MeshBeaconConfigError("interval_seconds must be an integer")
        if not _MIN_INTERVAL_SECONDS <= interval <= _MAX_INTERVAL_SECONDS:
            raise MeshBeaconConfigError(
                f"interval_seconds must be between {_MIN_INTERVAL_SECONDS} "
                f"and {_MAX_INTERVAL_SECONDS}"
            )
        desired.broadcast_interval_secs = interval

        desired.broadcast_offer_region = current_region
        desired.broadcast_offer_preset = current_preset
        self._configure_offer_channel(local_node, desired)
        self._configure_targets(interface, local_node, lora, desired)

    def _configure_offer_channel(self, local_node: Any, desired: Any) -> None:
        """Copy only an explicitly selected channel into the advertised join offer."""
        if "offer_channel_index" not in self.config:
            raise MeshBeaconConfigError(
                "offer_channel_index must be set explicitly to an integer or null"
            )

        configured_index = self.config["offer_channel_index"]
        if configured_index is None:
            desired.ClearField("broadcast_offer_channel")
            return
        if isinstance(configured_index, bool) or not isinstance(configured_index, int):
            raise MeshBeaconConfigError(
                "offer_channel_index must be an integer or null"
            )

        channels = list(getattr(local_node, "channels", ()) or ())
        if not channels:
            raise MeshBeaconConfigError("channel configuration is not available")
        channel = _find_channel(channels, configured_index)
        if channel is None:
            raise MeshBeaconConfigError(
                f"offer_channel_index {configured_index} is not configured"
            )

        allow_blank_primary = int(getattr(channel, "role", 0)) == 1
        if not _channel_is_usable(channel, allow_blank_primary=allow_blank_primary):
            raise MeshBeaconConfigError(
                "offer channel index "
                f"{getattr(channel, 'index', '?')} is disabled or blank"
            )

        settings = channel.settings
        desired.ClearField("broadcast_offer_channel")
        desired.broadcast_offer_channel.name = str(getattr(settings, "name", ""))
        desired.broadcast_offer_channel.psk = bytes(getattr(settings, "psk", b""))

    def _configure_targets(
        self,
        interface: Any,
        local_node: Any,
        lora: Any,
        desired: Any,
    ) -> None:
        """Validate explicit cross-preset TX destinations and write target entries."""
        raw_targets = self.config.get("targets")
        if not isinstance(raw_targets, list) or not raw_targets:
            raise MeshBeaconConfigError(
                "targets must contain at least one cross-preset broadcast target"
            )
        if len(raw_targets) > _MAX_TARGETS:
            raise MeshBeaconConfigError(
                f"targets supports at most {_MAX_TARGETS} entries"
            )

        current_region = int(lora.region)
        current_preset = int(lora.modem_preset)
        allowed = self._allowed_presets(interface, lora, current_region)
        channels = list(getattr(local_node, "channels", ()) or ())
        seen: set[tuple[int, int]] = set()
        has_cross_preset = False

        desired.ClearField("broadcast_targets")
        for index, raw_target in enumerate(raw_targets):
            if not isinstance(raw_target, Mapping):
                raise MeshBeaconConfigError(f"targets[{index}] must be a mapping")
            preset_name = raw_target.get("preset")
            if not isinstance(preset_name, str) or not preset_name.strip():
                raise MeshBeaconConfigError(
                    f"targets[{index}].preset must be a preset name"
                )
            preset = _enum_number(lora, "modem_preset", preset_name)
            if preset not in allowed:
                region_name = _enum_name(lora, "region", current_region)
                raise MeshBeaconConfigError(
                    f"targets[{index}].preset {preset_name!r} is not allowed "
                    f"in region {region_name}"
                )

            if "channel_index" not in raw_target:
                raise MeshBeaconConfigError(
                    f"targets[{index}].channel_index must be specified explicitly"
                )
            channel_index = raw_target["channel_index"]
            if isinstance(channel_index, bool) or not isinstance(channel_index, int):
                raise MeshBeaconConfigError(
                    f"targets[{index}].channel_index must be an integer"
                )
            channel = _find_channel(channels, channel_index)
            if channel is None or not _channel_is_usable(
                channel,
                allow_blank_primary=int(getattr(channel, "role", 0)) == 1,
            ):
                raise MeshBeaconConfigError(
                    f"targets[{index}].channel_index {channel_index} is not an "
                    "enabled configured channel"
                )

            identity = (preset, channel_index)
            if identity in seen:
                raise MeshBeaconConfigError(
                    f"targets[{index}] duplicates an earlier preset/channel target"
                )
            seen.add(identity)
            has_cross_preset = has_cross_preset or preset != current_preset

            target = desired.broadcast_targets.add()
            target.preset = preset
            target.region = current_region
            target.channel_index = channel_index

        if not has_cross_preset:
            current_name = _enum_name(lora, "modem_preset", current_preset)
            raise MeshBeaconConfigError(
                "at least one target must use a preset other than the radio's "
                f"current {current_name} preset"
            )

    @staticmethod
    def _allowed_presets(interface: Any, lora: Any, region: int) -> set[int]:
        """Return region-valid presets, using a conservative compatibility fallback."""
        getter = getattr(interface, "get_allowed_modem_presets", None)
        if callable(getter):
            allowed = cast(Iterable[int] | None, getter(region))
            if allowed is not None:
                return {int(value) for value in allowed}
        return {
            _enum_number(lora, "modem_preset", name) for name in _CONSERVATIVE_PRESETS
        }

    async def handle_meshtastic_message(
        self,
        packet: dict[str, Any],
        formatted_message: str,
        longname: str,
        meshnet_name: str,
    ) -> bool:
        """Decline Meshtastic messages; the firmware owns beacon packet handling."""
        _ = packet, formatted_message, longname, meshnet_name
        return False

    async def handle_room_message(
        self,
        room: Any,
        event: Any,
        full_message: str,
    ) -> bool:
        """Decline Matrix events because this plugin is configuration-only."""
        _ = room, event, full_message
        return False
