"""Matrix command for concise, recency-sorted Meshtastic node listings."""

import asyncio
import math
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, NamedTuple, cast

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

DEFAULT_NODE_LIST_LIMIT = 10
NODE_SNAPSHOT_ATTEMPTS = 3
NODES_USAGE = "Usage: !nodes [full|<count>]"


class _NodeSummary(NamedTuple):
    node_id: str | None
    short_name: str
    long_name: str
    hw_model: str
    battery: str
    voltage: str
    snr: str
    hops: str
    last_heard: str
    last_heard_timestamp: float | None

    @property
    def sort_key(self) -> tuple[bool, float, str, str, str]:
        """Sort recent nodes first, with deterministic name and ID tie-breakers."""
        return (
            self.last_heard_timestamp is None,
            -(self.last_heard_timestamp or 0.0),
            self.short_name.casefold(),
            self.long_name.casefold(),
            (self.node_id or "").casefold(),
        )

    def render(self) -> str:
        """Render one concise node line."""
        names = f"{self.short_name} {self.long_name}"
        if self.node_id is not None:
            names = f"{names} ({self.node_id})"
        parts = [
            names,
            self.hw_model,
            f"{self.battery} {self.voltage}",
            self.snr,
            self.hops,
            self.last_heard,
        ]
        return " / ".join(part for part in parts if part)


def get_relative_time(timestamp: float) -> str:
    """Convert a POSIX timestamp into a concise relative time string."""
    now = datetime.now()
    dt = datetime.fromtimestamp(timestamp)
    total_seconds = int((now - dt).total_seconds())
    if total_seconds <= 0:
        return "Just now"
    if total_seconds > RELATIVE_TIME_DAYS_THRESHOLD * SECONDS_PER_DAY:
        return dt.strftime(DATE_FORMAT_LONG)

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


def _parse_node_limit(args: str) -> int | None:
    """Parse an optional node count; ``None`` means the full database."""
    normalized = args.strip().casefold()
    if not normalized:
        return DEFAULT_NODE_LIST_LIMIT
    if normalized in {"all", "full"}:
        return None
    if normalized.isdecimal():
        requested = int(normalized)
        if requested > 0:
            return requested
    raise ValueError(NODES_USAGE)


def _snapshot_node_items(
    nodes: object,
) -> list[tuple[object, dict[str, Any]]] | None:
    """Copy live node mappings with bounded retries during concurrent mutation."""
    items_method = getattr(nodes, "items", None)
    if not callable(items_method):
        return []

    for attempt in range(1, NODE_SNAPSHOT_ATTEMPTS + 1):
        try:
            # `callable()` does not narrow `object` for pyright/mypy; assert the
            # duck-typed contract that the node DB exposes for `.items()`.
            items_getter = cast(
                Callable[[], Iterable[tuple[object, Any]]], items_method
            )
            raw_items = list(items_getter())
            snapshot: list[tuple[object, dict[str, Any]]] = []
            for node_key, info in raw_items:
                if not isinstance(info, dict):
                    continue
                copied_info = dict(info)
                for nested_key in ("user", "deviceMetrics"):
                    nested_value = copied_info.get(nested_key)
                    if isinstance(nested_value, dict):
                        copied_info[nested_key] = dict(nested_value)
                snapshot.append((node_key, copied_info))
            return snapshot
        except RuntimeError:
            logger.debug(
                "Node database changed during snapshot attempt %s/%s",
                attempt,
                NODE_SNAPSHOT_ATTEMPTS,
            )
    logger.warning(
        "Unable to capture a stable node database snapshot after %s attempts",
        NODE_SNAPSHOT_ATTEMPTS,
    )
    return None


def _node_identifier(
    node_key: object, info: dict[str, Any], user_info: dict[str, Any]
) -> str | None:
    """Resolve the canonical ``!xxxxxxxx`` identifier when available."""
    for candidate in (user_info.get("id"), info.get("id"), node_key):
        if isinstance(candidate, str) and candidate.startswith("!"):
            return candidate

    node_num = info.get("num")
    if isinstance(node_num, int) and not isinstance(node_num, bool) and node_num >= 0:
        return f"!{node_num & 0xFFFFFFFF:08x}"
    return None


def _last_heard(value: object) -> tuple[float | None, str]:
    """Return a sortable timestamp and display value for ``lastHeard``."""
    if value is None:
        return None, "?"
    try:
        # lastHeard arrives from protobuf as int/float; cast narrows for the
        # static checker while the try/except handles unexpected runtime types.
        timestamp = float(cast("float | int | str", value))
        if not math.isfinite(timestamp) or timestamp <= 0:
            raise ValueError("timestamp must be finite and positive")
        return timestamp, get_relative_time(timestamp)
    except (TypeError, ValueError, OverflowError, OSError):
        logger.debug("Failed to parse lastHeard timestamp: %s", value)
        return None, "?"


def _hop_description(value: object) -> str:
    """Format the node database hop count without assuming an integer type."""
    if value is None:
        return "? hops away"
    if value == 0:
        return "direct"
    if value == 1:
        return "1 hop away"
    return f"{value} hops away"


def _signal_description(value: object) -> str:
    """Format a received SNR value when one is available."""
    return "" if value is None else f"{value}{SNR_UNIT_SUFFIX}"


def _power_descriptions(value: object) -> tuple[str, str]:
    """Return battery and voltage display values from device metrics."""
    if not isinstance(value, dict):
        return "?%", "?V"
    battery_level = value.get("batteryLevel")
    voltage = value.get("voltage")
    return (
        "?%" if battery_level is None else f"{battery_level}%",
        "?V" if voltage is None else f"{voltage}V",
    )


def _summarize_node(node_key: object, info: dict[str, Any]) -> _NodeSummary:
    """Normalize one node database record for sorting and display."""
    user = info.get("user")
    user_info = dict(user) if isinstance(user, dict) else {}
    battery, voltage = _power_descriptions(info.get("deviceMetrics"))
    last_heard_timestamp, last_heard = _last_heard(info.get("lastHeard"))

    return _NodeSummary(
        node_id=_node_identifier(node_key, info, user_info),
        short_name=str(user_info.get("shortName") or UNKNOWN_NODE_VALUE),
        long_name=str(user_info.get("longName") or UNKNOWN_NODE_VALUE),
        hw_model=str(user_info.get("hwModel") or UNKNOWN_NODE_VALUE),
        battery=battery,
        voltage=voltage,
        snr=_signal_description(info.get("snr")),
        hops=_hop_description(info.get("hopsAway")),
        last_heard=last_heard,
        last_heard_timestamp=last_heard_timestamp,
    )


class Plugin(BasePlugin):
    """Expose bounded node-database summaries to Matrix rooms."""

    plugin_name = "nodes"
    is_core_plugin = True

    @property
    def description(self) -> str:
        return """Show mesh radios and node data, newest first

!nodes full shows the complete database; !nodes <count> chooses a limit.
$shortname $longname ($nodeid) / $devicemodel / $battery $voltage / $snr / $hops / $lastseen
"""

    def generate_response(self, limit: int | None = DEFAULT_NODE_LIST_LIMIT) -> str:
        """Build a newest-first summary from a stable node database snapshot."""
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive or None")

        # Lazy import avoids initializing transport configuration during discovery.
        from mmrelay.meshtastic_utils import (  # pylint: disable=import-outside-toplevel
            connect_meshtastic,
        )

        meshtastic_client = connect_meshtastic()
        if meshtastic_client is None:
            return "Unable to connect to Meshtastic device."

        snapshot = _snapshot_node_items(getattr(meshtastic_client, "nodes", None))
        if snapshot is None:
            return "Node database is updating; try again."

        summaries = sorted(
            (_summarize_node(node_key, info) for node_key, info in snapshot),
            key=lambda summary: summary.sort_key,
        )
        total = len(summaries)
        visible = summaries if limit is None else summaries[:limit]

        if len(visible) < total:
            header = (
                f"Nodes: showing {len(visible)} of {total} "
                "(newest first; use !nodes full for all)"
            )
        else:
            header = f"Nodes: {total} (newest first)"

        lines = [header, *(summary.render() for summary in visible)]
        return "\n".join(lines) + "\n"

    async def handle_meshtastic_message(
        self, packet: Any, formatted_message: str, longname: str, meshnet_name: str
    ) -> bool:
        """Keep node listings off the constrained mesh transport."""
        _ = packet, formatted_message, longname, meshnet_name
        return False

    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
    ) -> bool:
        """Handle ``!nodes``, ``!nodes full``, and numeric-limit variants."""
        if not self.matches(event):
            return False

        command = self.plugin_name or "nodes"
        args = self.extract_command_args(command, event=event)
        if args is None:
            args = self.extract_command_args(command, text=full_message)
        if args is None:
            args = ""

        try:
            limit = _parse_node_limit(args)
        except ValueError:
            await self.send_matrix_message(
                room_id=room.room_id,
                message=NODES_USAGE,
                formatted=False,
            )
            await self.send_matrix_reaction(room.room_id, event.event_id, "❌")
            return True

        try:
            response = await asyncio.to_thread(self.generate_response, limit=limit)
            await self.send_matrix_message(
                room_id=room.room_id,
                message=response,
                formatted=False,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            # Plugin handlers are the final command boundary and must not escape.
            self.logger.exception("Error handling nodes command")
            await self.send_matrix_reaction(room.room_id, event.event_id, "❌")
            return True
        await self.send_matrix_reaction(room.room_id, event.event_id, "✅")
        return True
