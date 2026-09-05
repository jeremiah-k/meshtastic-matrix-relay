import asyncio
import base64
from datetime import datetime
from typing import Any

# matrix-nio is not marked py.typed; keep import-untyped for strict mypy.
from nio import (
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

from mmrelay.constants.domain import (
    RELATIVE_TIME_DAYS_THRESHOLD,
    SECONDS_PER_DAY,
    SECONDS_PER_HOUR,
    SECONDS_PER_MINUTE,
    UNKNOWN_NODE_VALUE,
)
from mmrelay.constants.formats import DATE_FORMAT_LONG, SNR_UNIT_SUFFIX
from mmrelay.log_utils import get_logger
from mmrelay.plugins.base_plugin import BasePlugin

logger = get_logger(__name__)

DEFAULT_FIELDS = ["name", "hardware", "power", "snr", "hops", "last_seen", "status"]
FIELD_PATHS = {
    "short_name": "user.shortName",
    "long_name": "user.longName",
    "node_id": "user.id",
    "node_num": "num",
    "hardware": "user.hwModel",
    "role": "user.role",
    "public_key": "user.publicKey",
    "status": "status",
    "battery": "deviceMetrics.batteryLevel",
    "voltage": "deviceMetrics.voltage",
    "channel_utilization": "deviceMetrics.channelUtilization",
    "air_util_tx": "deviceMetrics.airUtilTx",
    "uptime": "deviceMetrics.uptimeSeconds",
    "snr": "snr",
    "hops": "hopsAway",
    "last_seen": "lastHeard",
    "channel": "channel",
    "favorite": "isFavorite",
    "latitude": "position.latitude",
    "longitude": "position.longitude",
    "altitude": "position.altitude",
}
FIELD_LABELS = {
    "short_name": "short",
    "long_name": "long",
    "node_id": "id",
    "node_num": "num",
    "role": "role",
    "public_key": "key",
    "status": "status",
    "battery": "battery",
    "voltage": "voltage",
    "channel_utilization": "channel util",
    "air_util_tx": "air util tx",
    "uptime": "uptime",
    "channel": "channel",
    "favorite": "favorite",
    "latitude": "lat",
    "longitude": "lon",
    "altitude": "alt",
}
AVAILABLE_FIELDS = tuple(
    ["name", "power", *FIELD_PATHS.keys(), "<dotted node path>"]
)


def get_relative_time(timestamp: float) -> str:
    """
    Convert a POSIX timestamp into a concise, human-readable relative time string.

    Parameters:
        timestamp (float): POSIX timestamp (seconds since the epoch) to compare with the current time.

    Returns:
        str: A relative time description:
                - "Just now" for times less than 60 seconds ago
                - "<N> minutes ago" for times between 60 seconds and 1 hour
                - "<N> hours ago" for times between 1 hour and 24 hours
                - "<N> days ago" for times between 1 day and RELATIVE_TIME_DAYS_THRESHOLD days
                - a timestamp formatted with DATE_FORMAT_LONG when
                  delta > RELATIVE_TIME_DAYS_THRESHOLD * SECONDS_PER_DAY
    """
    now = datetime.now()
    dt = datetime.fromtimestamp(timestamp)

    # Calculate the time difference between the current time and the given timestamp
    delta = now - dt

    # Compute signed total seconds and guard against future timestamps
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "Just now"

    # Convert the time difference into a relative timeframe
    if total_seconds > RELATIVE_TIME_DAYS_THRESHOLD * SECONDS_PER_DAY:
        return dt.strftime(
            DATE_FORMAT_LONG
        )  # Return formatted date if older than RELATIVE_TIME_DAYS_THRESHOLD days

    days = total_seconds // SECONDS_PER_DAY
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''} ago"

    hours = total_seconds // SECONDS_PER_HOUR
    if hours >= 1:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"

    minutes = total_seconds // SECONDS_PER_MINUTE
    if minutes >= 1:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"

    return "Just now"


def _get_field_value(node: dict[str, Any], field_path: str) -> Any:
    value: Any = node
    for key in field_path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _format_public_key(value: Any) -> str | None:
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii") if value else None
    if isinstance(value, bytearray):
        return base64.b64encode(bytes(value)).decode("ascii") if value else None
    if isinstance(value, str):
        return value or None
    return str(value) if value is not None else None


def _format_last_seen(value: Any) -> str:
    if value is None:
        return "?"
    try:
        timestamp = float(value)
        return get_relative_time(timestamp) if timestamp > 0 else "?"
    except (TypeError, ValueError, OverflowError, OSError):
        logger.debug("Failed to parse lastHeard timestamp: %s", value)
        return "?"


def _last_heard_sort_value(info: dict[str, Any]) -> float:
    try:
        return float(info.get("lastHeard") or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _format_hops(value: Any) -> str:
    if value is None:
        return "? hops away"
    if value == 0:
        return "direct"
    if value == 1:
        return "1 hop away"
    return f"{value} hops away"


def _format_field_value(field: str, value: Any) -> str | None:
    if field == "public_key":
        return _format_public_key(value)
    if field == "last_seen":
        return _format_last_seen(value)
    if field == "hops":
        return _format_hops(value)
    if field == "snr":
        return f"{value}{SNR_UNIT_SUFFIX}" if value is not None else None
    if field == "battery":
        return f"{value}%" if value is not None else None
    if field == "voltage":
        return f"{value}V" if value is not None else None
    if field in ("channel_utilization", "air_util_tx"):
        return f"{value}%" if value is not None else None
    if field == "uptime" and isinstance(value, (int, float)):
        return f"{value}s"
    if field == "altitude":
        return f"{value}m" if value is not None else None
    if field in ("latitude", "longitude"):
        return f"{value}°" if value is not None else None
    if field == "favorite":
        return "yes" if value else "no" if value is not None else None
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, bytearray):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value) if value is not None else None


class Plugin(BasePlugin):
    plugin_name = "nodes"
    is_core_plugin = True

    @property
    def description(self) -> str:
        """
        Provide the plugin description and the configurable node-list line format.

        Returns:
            A multiline string describing the node output and configuration key.
        """
        return (
            "Show mesh radios and node data. Output fields can be selected with "
            "plugins.nodes.fields.\n\n"
            "Default: name / hardware / power / snr / hops / last_seen / status"
        )

    def _configured_fields(self) -> list[str]:
        fields = self.config.get("fields", DEFAULT_FIELDS)
        if not isinstance(fields, list) or not fields:
            self.logger.warning(
                "Plugin 'nodes': fields must be a non-empty list; using defaults."
            )
            return DEFAULT_FIELDS.copy()
        configured = [field.strip() for field in fields if isinstance(field, str)]
        configured = [field for field in configured if field]
        if not configured:
            self.logger.warning(
                "Plugin 'nodes': fields contains no valid field names; using defaults."
            )
            return DEFAULT_FIELDS.copy()
        return configured

    def _render_field(self, field: str, node_key: Any, info: dict[str, Any]) -> str | None:
        if field == "name":
            short_name = _get_field_value(info, "user.shortName") or UNKNOWN_NODE_VALUE
            long_name = _get_field_value(info, "user.longName") or UNKNOWN_NODE_VALUE
            return f"{short_name} {long_name}"
        if field == "power":
            battery = _get_field_value(info, "deviceMetrics.batteryLevel")
            voltage = _get_field_value(info, "deviceMetrics.voltage")
            battery_text = f"{battery}%" if battery is not None else "?%"
            voltage_text = f"{voltage}V" if voltage is not None else "?V"
            return f"{battery_text} {voltage_text}"

        field_path = FIELD_PATHS.get(field, field)
        value = _get_field_value(info, field_path)
        if field == "node_id" and value is None and isinstance(node_key, str):
            value = node_key
        if field == "hardware" and value is None:
            value = UNKNOWN_NODE_VALUE

        rendered = _format_field_value(field, value)
        if rendered is None:
            return None
        if field in ("hardware", "snr", "hops", "last_seen"):
            return rendered

        label = FIELD_LABELS.get(field)
        if label is not None:
            return f"{label}: {rendered}"
        return f"{field}: {rendered}"

    def generate_response(self) -> str:
        """
        Build a textual summary of known Meshtastic nodes using configured fields.

        The response begins with "Nodes: <count>" and lists nodes newest-first.
        Fields come from ``plugins.nodes.fields`` and may be aliases or raw dotted
        node-data paths. If the Meshtastic device cannot be contacted, returns the
        error message "Unable to connect to Meshtastic device."

        Returns:
            response (str): The multi-line nodes summary or connection error.
        """
        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client = connect_meshtastic()
        if meshtastic_client is None:
            return "Unable to connect to Meshtastic device."

        fields = self._configured_fields()
        node_entries = [
            (node_key, info)
            for node_key, info in meshtastic_client.nodes.items()
            if isinstance(info, dict)
        ]
        node_entries.sort(
            key=lambda item: _last_heard_sort_value(item[1]),
            reverse=True,
        )

        node_lines: list[str] = []
        for node_key, info in node_entries:
            rendered_fields = [
                rendered
                for field in fields
                if (rendered := self._render_field(field, node_key, info)) is not None
            ]
            node_lines.append(" / ".join(rendered_fields) + "\n")

        response = f"Nodes: {len(node_entries)}\n"
        return response + "".join(node_lines)

    async def handle_meshtastic_message(
        self, packet: Any, formatted_message: str, longname: str, meshnet_name: str
    ) -> bool:
        """
        Handle an incoming Meshtastic packet without processing it.

        Returns:
            bool: `False` indicating the plugin did not handle the message.
        """
        _ = packet, formatted_message, longname, meshnet_name
        return False

    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
    ) -> bool:
        """
        Handle a Matrix room event and send the configured nodes summary.

        Returns:
            bool: `True` if the command was handled, `False` otherwise.
        """
        if not self.matches(event):
            return False
        _ = full_message

        try:
            response = await asyncio.to_thread(self.generate_response)
            await self.send_matrix_message(
                room_id=room.room_id,
                message=response,
                formatted=False,
            )
        except Exception:
            self.logger.exception("Error handling nodes command")
            await self.send_matrix_reaction(room.room_id, event.event_id, "❌")
            return True
        await self.send_matrix_reaction(room.room_id, event.event_id, "✅")
        return True
