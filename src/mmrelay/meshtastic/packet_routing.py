from collections.abc import Mapping
from typing import Any, cast

from meshtastic.protobuf import portnums_pb2

from mmrelay.config import get_meshtastic_config_value
from mmrelay.constants.config import (
    CONFIG_KEY_CHAT_PORTNUMS,
    CONFIG_KEY_DISABLED_PORTNUMS,
    CONFIG_KEY_ENCRYPTED_ACTION,
    CONFIG_KEY_PACKET_ROUTING,
    CONFIG_SECTION_MESHTASTIC,
    DEFAULT_DETECTION_SENSOR,
    DEFAULT_ENCRYPTED_ACTION,
)
from mmrelay.constants.formats import DETECTION_SENSOR_APP, TEXT_MESSAGE_APP
from mmrelay.constants.messages import (
    PORTNUM_DETECTION_SENSOR_APP,
    PORTNUM_TEXT_MESSAGE_APP,
)
from mmrelay.log_utils import get_logger

__all__ = [
    "CHAT_ELIGIBLE_PORTNUMS",
    "PacketAction",
    "classify_packet",
]


class PacketAction:
    RELAY = "relay"
    PLUGIN_ONLY = "plugin_only"
    DROP = "drop"


CHAT_ELIGIBLE_PORTNUMS = frozenset({TEXT_MESSAGE_APP})
logger = get_logger("Meshtastic")
_warned_packet_routing_issues: set[str] = set()


def _warn_once(key: str, message: str, *args: object) -> None:
    if key in _warned_packet_routing_issues:
        return
    _warned_packet_routing_issues.add(key)
    logger.warning(message, *args)


def _is_text_message_portnum(portnum: object) -> bool:
    return (
        portnum
        in (
            PORTNUM_TEXT_MESSAGE_APP,
            TEXT_MESSAGE_APP,
        )
        or _get_portnum_name(portnum) == TEXT_MESSAGE_APP
    )


def _get_portnum_name(
    portnum: object, packet: Mapping[str, object] | None = None
) -> str:
    if portnum is None:
        if packet and packet.get("encrypted"):
            return "ENCRYPTED"
        return "UNKNOWN (None)"

    if isinstance(portnum, str):
        if portnum:
            return portnum
        return "UNKNOWN (empty string)"

    if isinstance(portnum, int) and not isinstance(portnum, bool):
        try:
            resolved_name = portnums_pb2.PortNum.Name(cast(Any, portnum))
            if isinstance(resolved_name, str) and resolved_name:
                return resolved_name
            return f"UNKNOWN (portnum={portnum})"
        except ValueError:
            return f"UNKNOWN (portnum={portnum})"

    return f"UNKNOWN (type={type(portnum).__name__})"


def _is_encrypted_packet(packet: Mapping[str, object] | None) -> bool:
    return bool(packet and packet.get("encrypted"))


def _resolve_portnum_set(
    portnums: object,
    setting_name: str = "portnums",
) -> frozenset[str]:
    if portnums is None:
        return frozenset()
    if isinstance(portnums, str):
        stripped = portnums.strip()
        if not stripped:
            _warn_once(
                f"{setting_name}:empty_string",
                "Ignoring empty string in meshtastic.packet_routing.%s.",
                setting_name,
            )
            return frozenset()
        return frozenset({stripped})
    if isinstance(portnums, (list, tuple, set, frozenset)):
        resolved: set[str] = set()
        dropped_invalid_value = False
        for entry in portnums:
            item: object = entry
            if isinstance(item, str):
                item = item.strip()
                if not item:
                    dropped_invalid_value = True
                    continue
            name = _get_portnum_name(item)
            if not name.startswith("UNKNOWN"):
                resolved.add(name)
            else:
                dropped_invalid_value = True
        if dropped_invalid_value:
            _warn_once(
                f"{setting_name}:invalid_entries",
                "Ignoring unsupported values in meshtastic.packet_routing.%s; "
                "use valid portnum names or numeric PortNum values.",
                setting_name,
            )
        return frozenset(resolved)
    _warn_once(
        f"{setting_name}:invalid_type",
        "Ignoring meshtastic.packet_routing.%s with unsupported type %s; "
        "expected a string or list-like value.",
        setting_name,
        type(portnums).__name__,
    )
    return frozenset()


def _get_packet_routing_overrides(
    config: Mapping[str, object] | None,
) -> tuple[frozenset[str], frozenset[str]]:
    if not isinstance(config, Mapping):
        return frozenset(), frozenset()
    meshtastic_section = config.get(CONFIG_SECTION_MESHTASTIC, {})
    if not isinstance(meshtastic_section, Mapping):
        return frozenset(), frozenset()
    routing_section = meshtastic_section.get(CONFIG_KEY_PACKET_ROUTING, {})
    if not isinstance(routing_section, dict):
        _warn_once(
            "packet_routing:not_a_mapping",
            "Ignoring meshtastic.%s=%r (type=%s); expected a mapping.",
            CONFIG_KEY_PACKET_ROUTING,
            routing_section,
            type(routing_section).__name__,
        )
        return frozenset(), frozenset()
    chat_portnums = _resolve_portnum_set(
        routing_section.get(CONFIG_KEY_CHAT_PORTNUMS),
        CONFIG_KEY_CHAT_PORTNUMS,
    )
    disabled_portnums = _resolve_portnum_set(
        routing_section.get(CONFIG_KEY_DISABLED_PORTNUMS),
        CONFIG_KEY_DISABLED_PORTNUMS,
    )
    return chat_portnums, disabled_portnums


def _get_encrypted_action(config: Mapping[str, object] | None) -> str:
    if not isinstance(config, Mapping):
        return DEFAULT_ENCRYPTED_ACTION
    meshtastic_section = config.get(CONFIG_SECTION_MESHTASTIC, {})
    if not isinstance(meshtastic_section, Mapping):
        return DEFAULT_ENCRYPTED_ACTION
    routing_section = meshtastic_section.get(CONFIG_KEY_PACKET_ROUTING, {})
    if not isinstance(routing_section, dict):
        _warn_once(
            "encrypted_action:packet_routing_not_a_mapping",
            "Ignoring meshtastic.%s=%r (type=%s); expected a mapping.",
            CONFIG_KEY_PACKET_ROUTING,
            routing_section,
            type(routing_section).__name__,
        )
        return DEFAULT_ENCRYPTED_ACTION
    action = routing_section.get(CONFIG_KEY_ENCRYPTED_ACTION)
    if action is None:
        return DEFAULT_ENCRYPTED_ACTION
    if isinstance(action, str):
        action = action.strip().lower()
        if action in (PacketAction.PLUGIN_ONLY, PacketAction.DROP):
            return action
        _warn_once(
            "encrypted_action:invalid_value",
            "Invalid meshtastic.packet_routing.%s=%r; using default %r. "
            "Valid values: %r, %r.",
            CONFIG_KEY_ENCRYPTED_ACTION,
            action,
            DEFAULT_ENCRYPTED_ACTION,
            PacketAction.PLUGIN_ONLY,
            PacketAction.DROP,
        )
        return DEFAULT_ENCRYPTED_ACTION
    _warn_once(
        "encrypted_action:invalid_type",
        "Ignoring meshtastic.packet_routing.%s with unsupported type %s; "
        "using default %r.",
        CONFIG_KEY_ENCRYPTED_ACTION,
        type(action).__name__,
        DEFAULT_ENCRYPTED_ACTION,
    )
    return DEFAULT_ENCRYPTED_ACTION


def classify_packet(
    portnum: object,
    config: Mapping[str, object] | None,
    packet: Mapping[str, object] | None = None,
) -> str:
    """
    Classify an inbound packet for chat relay routing.

    Returns one of PacketAction.RELAY, PacketAction.PLUGIN_ONLY, or
    PacketAction.DROP.

    Encrypted packets are handled by a dedicated policy (encrypted_action)
    separate from portnum overrides, because the actual portnum is unknown.
    """
    if _is_encrypted_packet(packet):
        action = _get_encrypted_action(config)
        if action == PacketAction.DROP:
            logger.debug(
                "Encrypted packet classified as %s via encrypted_action policy.",
                action,
            )
        return action

    portnum_name = _get_portnum_name(portnum, packet)

    chat_overrides, disabled_overrides = _get_packet_routing_overrides(config)

    if portnum_name in disabled_overrides:
        logger.debug(
            "Packet %s classified as %s via config override.",
            portnum_name,
            PacketAction.DROP,
        )
        return PacketAction.DROP

    is_detection_sensor = (
        portnum
        in (
            PORTNUM_DETECTION_SENSOR_APP,
            DETECTION_SENSOR_APP,
        )
        or portnum_name == DETECTION_SENSOR_APP
    )

    is_text_message = (
        portnum
        in (
            PORTNUM_TEXT_MESSAGE_APP,
            TEXT_MESSAGE_APP,
        )
        or portnum_name in CHAT_ELIGIBLE_PORTNUMS
    )

    if portnum_name in chat_overrides:
        if is_detection_sensor:
            detection_sensor_enabled = get_meshtastic_config_value(
                config if isinstance(config, dict) else {},
                "detection_sensor",
                DEFAULT_DETECTION_SENSOR,
            )
            if not detection_sensor_enabled:
                logger.debug(
                    "Packet %s in chat_portnums but detection_sensor is disabled; "
                    "classifying as %s.",
                    portnum_name,
                    PacketAction.PLUGIN_ONLY,
                )
                return PacketAction.PLUGIN_ONLY
        logger.debug(
            "Packet %s classified as %s via config override.",
            portnum_name,
            PacketAction.RELAY,
        )
        return PacketAction.RELAY

    if is_detection_sensor:
        detection_sensor_enabled = get_meshtastic_config_value(
            config if isinstance(config, dict) else {},
            "detection_sensor",
            DEFAULT_DETECTION_SENSOR,
        )
        if detection_sensor_enabled:
            return PacketAction.RELAY

        logger.debug(
            "Packet %s classified as %s; skipping Matrix chat relay because "
            "meshtastic.detection_sensor is disabled.",
            portnum_name,
            PacketAction.PLUGIN_ONLY,
        )
        return PacketAction.PLUGIN_ONLY

    if is_text_message:
        return PacketAction.RELAY

    return PacketAction.PLUGIN_ONLY
