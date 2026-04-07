from typing import Any, cast

from meshtastic.protobuf import portnums_pb2

from mmrelay.config import get_meshtastic_config_value
from mmrelay.constants.config import DEFAULT_DETECTION_SENSOR
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


def classify_packet(portnum: Any, config: dict[str, Any] | None) -> str:
    """
    Classify an inbound packet for chat relay routing.

    Returns one of PacketAction.RELAY, PacketAction.PLUGIN_ONLY, or
    PacketAction.DROP.
    """
    portnum_name = _get_portnum_name(portnum)
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
