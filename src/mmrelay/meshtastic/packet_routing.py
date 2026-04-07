from typing import Any, cast

from meshtastic.protobuf import portnums_pb2

from mmrelay.config import get_meshtastic_config_value
from mmrelay.constants.config import (
    CONFIG_KEY_CHAT_PORTNUMS,
    CONFIG_KEY_DISABLED_PORTNUMS,
    CONFIG_KEY_PACKET_ROUTING,
    CONFIG_SECTION_MESHTASTIC,
    DEFAULT_DETECTION_SENSOR,
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


def _get_portnum_name(portnum: Any) -> str:
    if portnum is None:
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


def _resolve_portnum_set(portnums: Any) -> frozenset[str]:
    if portnums is None:
        return frozenset()
    if isinstance(portnums, str):
        return frozenset({portnums})
    if isinstance(portnums, (list, tuple, set, frozenset)):
        resolved: set[str] = set()
        for entry in portnums:
            name = _get_portnum_name(entry)
            if not name.startswith("UNKNOWN"):
                resolved.add(name)
        return frozenset(resolved)
    return frozenset()


def _get_packet_routing_overrides(
    config: dict[str, Any] | None,
) -> tuple[frozenset[str], frozenset[str]]:
    if not isinstance(config, dict):
        return frozenset(), frozenset()
    meshtastic_section = config.get(CONFIG_SECTION_MESHTASTIC, {})
    if not isinstance(meshtastic_section, dict):
        return frozenset(), frozenset()
    routing_section = meshtastic_section.get(CONFIG_KEY_PACKET_ROUTING, {})
    if not isinstance(routing_section, dict):
        return frozenset(), frozenset()
    chat_portnums = _resolve_portnum_set(routing_section.get(CONFIG_KEY_CHAT_PORTNUMS))
    disabled_portnums = _resolve_portnum_set(
        routing_section.get(CONFIG_KEY_DISABLED_PORTNUMS)
    )
    return chat_portnums, disabled_portnums


def classify_packet(portnum: Any, config: dict[str, Any] | None) -> str:
    """
    Classify an inbound packet for chat relay routing.

    Returns one of PacketAction.RELAY, PacketAction.PLUGIN_ONLY, or
    PacketAction.DROP.
    """
    portnum_name = _get_portnum_name(portnum)

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

    logger.debug(
        "Packet %s classified as %s; skipping Matrix chat relay.",
        portnum_name,
        PacketAction.PLUGIN_ONLY,
    )
    return PacketAction.PLUGIN_ONLY
