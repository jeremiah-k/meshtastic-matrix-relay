"""Matrix command for structured Meshtastic traceroute requests."""

from __future__ import annotations

import asyncio
import math
import re
import shlex
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any, NamedTuple, Protocol, cast

# matrix-nio is not marked py.typed; keep import-untyped for strict mypy.
from nio import (
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

from mmrelay.plugins.base_plugin import BasePlugin

DEFAULT_TRACEROUTE_HOP_LIMIT = 3
MAX_TRACEROUTE_HOP_LIMIT = 7
MAX_CHANNEL_INDEX = 7
NODE_SNAPSHOT_ATTEMPTS = 3
TRACEROUTE_USAGE = "Usage: !traceroute <node-id-or-name> [--hops 1-7] (alias: !trace)"
_NODE_ID_RE = re.compile(r"^![0-9a-fA-F]{8}$")


class _TraceRouteHop(Protocol):  # pylint: disable=too-few-public-methods
    """Runtime shape exposed by mtjk's structured traceroute API."""

    node_num: int
    node_id: str
    snr_db: float | None


class _TraceRouteResult(Protocol):  # pylint: disable=too-few-public-methods
    """Runtime result shape consumed without importing optional mtjk symbols."""

    route_towards: Sequence[_TraceRouteHop]
    route_back: Sequence[_TraceRouteHop] | None


class _TraceRequest(NamedTuple):
    destination: str
    hop_limit: int | None


class TraceRouteCommandError(RuntimeError):
    """Expected user-facing traceroute failure."""


class TraceRouteBusyError(TraceRouteCommandError):
    """Raised when another command already owns the radio traceroute slot."""


@contextmanager
def _traceroute_slot(lock: threading.Lock) -> Iterator[None]:
    """Acquire the exclusive radio slot without queuing duplicate requests."""
    if not lock.acquire(blocking=False):
        raise TraceRouteBusyError(
            "A traceroute is already in progress; try again after it completes."
        )
    try:
        yield
    finally:
        lock.release()


def _parse_request(args: str) -> _TraceRequest:
    """Parse a destination plus an optional explicit hop limit."""
    try:
        tokens = shlex.split(args)
    except ValueError as exc:
        raise TraceRouteCommandError(TRACEROUTE_USAGE) from exc

    destination_tokens: list[str] = []
    hop_limit: int | None = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"--hops", "-h"}:
            if hop_limit is not None or index + 1 >= len(tokens):
                raise TraceRouteCommandError(TRACEROUTE_USAGE)
            raw_hop_limit = tokens[index + 1]
            try:
                hop_limit = int(raw_hop_limit, 10)
            except ValueError as exc:
                raise TraceRouteCommandError(TRACEROUTE_USAGE) from exc
            index += 2
            continue
        if token.startswith("-"):
            raise TraceRouteCommandError(TRACEROUTE_USAGE)
        destination_tokens.append(token)
        index += 1

    destination = " ".join(destination_tokens).strip()
    if not destination:
        raise TraceRouteCommandError(TRACEROUTE_USAGE)
    if hop_limit is not None and not 1 <= hop_limit <= MAX_TRACEROUTE_HOP_LIMIT:
        raise TraceRouteCommandError(
            f"Hop limit must be between 1 and {MAX_TRACEROUTE_HOP_LIMIT}."
        )
    return _TraceRequest(destination=destination, hop_limit=hop_limit)


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
    setting_name: str,
) -> int:
    """Validate an integer configuration value without accepting booleans."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise TraceRouteCommandError(
            f"Traceroute {setting_name} must be an integer between "
            f"{minimum} and {maximum}."
        )
    if not minimum <= value <= maximum:
        raise TraceRouteCommandError(
            f"Traceroute {setting_name} must be between {minimum} and {maximum}."
        )
    return value


def _default_hop_limit(client: object) -> int:
    """Read the radio's configured hop limit, falling back safely."""
    local_node = getattr(client, "localNode", None)
    local_config = getattr(local_node, "localConfig", None)
    lora_config = getattr(local_config, "lora", None)
    candidate = getattr(lora_config, "hop_limit", None)
    if (
        isinstance(candidate, int)
        and not isinstance(candidate, bool)
        and 1 <= candidate <= MAX_TRACEROUTE_HOP_LIMIT
    ):
        return candidate
    return DEFAULT_TRACEROUTE_HOP_LIMIT


def _snapshot_nodes(nodes: object) -> list[tuple[object, dict[str, Any]]]:
    """Take a bounded snapshot of the live node database for name resolution."""
    items = getattr(nodes, "items", None)
    if not callable(items):
        return []
    for _attempt in range(NODE_SNAPSHOT_ATTEMPTS):
        try:
            # `callable()` does not narrow `object` for pyright/mypy; assert the
            # duck-typed contract that the node DB exposes for `.items()`.
            items_getter = cast(Callable[[], Iterable[tuple[object, Any]]], items)
            return [
                (key, dict(value))
                for key, value in list(items_getter())
                if isinstance(value, dict)
            ]
        except RuntimeError:
            continue
    raise TraceRouteCommandError("Node database is updating; try again.")


def _record_destination(
    node_key: object, info: dict[str, Any], user: dict[str, Any]
) -> int | str | None:
    """Return the most stable destination accepted by mtjk."""
    for candidate in (user.get("id"), info.get("id"), node_key):
        if isinstance(candidate, str) and _NODE_ID_RE.fullmatch(candidate):
            return candidate.lower()
    node_num = info.get("num")
    if (
        isinstance(node_num, int)
        and not isinstance(node_num, bool)
        and 0 <= node_num <= 0xFFFFFFFF
    ):
        return node_num
    return None


def _resolve_destination(client: object, query: str) -> int | str:
    """Resolve canonical IDs, decimal node numbers, or exact NodeDB names."""
    stripped = query.strip()
    if _NODE_ID_RE.fullmatch(stripped):
        return stripped.lower()
    if stripped.isdecimal():
        node_num = int(stripped, 10)
        if node_num <= 0xFFFFFFFF:
            return node_num
        raise TraceRouteCommandError(
            "Node number must fit in an unsigned 32-bit value."
        )

    matches: dict[int | str, set[str]] = {}
    for node_key, info in _snapshot_nodes(getattr(client, "nodes", None)):
        user_value = info.get("user")
        user = dict(user_value) if isinstance(user_value, dict) else {}
        destination = _record_destination(node_key, info, user)
        if destination is None:
            continue
        labels = {
            value.strip()
            for value in (
                node_key,
                info.get("id"),
                user.get("id"),
                user.get("shortName"),
                user.get("longName"),
            )
            if isinstance(value, str) and value.strip()
        }
        if any(label.casefold() == stripped.casefold() for label in labels):
            matches.setdefault(destination, set()).update(labels)

    if not matches:
        raise TraceRouteCommandError(
            f"Node '{stripped}' was not found. Use !nodes to find a node ID."
        )
    if len(matches) > 1:
        options = ", ".join(
            str(destination) for destination in sorted(matches, key=str)
        )
        raise TraceRouteCommandError(
            f"Node name '{stripped}' is ambiguous; use one of these IDs: {options}."
        )
    return next(iter(matches))


def _format_snr(value: object) -> str:
    """Format one link SNR while preserving unknown firmware values."""
    if value is None:
        return "SNR unknown"
    try:
        # snr_db arrives from protobuf as int/float; cast narrows for the
        # static checker while the try/except handles unexpected runtime types.
        numeric = float(cast("float | int | str", value))
    except (TypeError, ValueError):
        return "SNR unknown"
    if not math.isfinite(numeric):
        return "SNR unknown"
    return f"{numeric:g} dB"


def _format_route(label: str, hops: Sequence[_TraceRouteHop]) -> str:
    """Format one ordered route and annotate each incoming link with SNR."""
    if not hops:
        raise TraceRouteCommandError("Traceroute returned an empty route.")

    rendered = [str(getattr(hops[0], "node_id", "unknown"))]
    for hop in hops[1:]:
        node_id = str(getattr(hop, "node_id", "unknown"))
        rendered.append(f"→ {node_id} ({_format_snr(getattr(hop, 'snr_db', None))})")
    return f"{label}: {' '.join(rendered)}"


def _format_result(
    result: _TraceRouteResult,
    *,
    destination: int | str,
    hop_limit: int,
    channel_index: int,
) -> str:
    """Render a structured mtjk result for a Matrix room."""
    outbound = _format_route("Outbound", result.route_towards)
    if result.route_back is None:
        return_route = "Return: not reported by firmware"
    else:
        return_route = _format_route("Return", result.route_back)
    return (
        f"Traceroute to {destination} "
        f"(hop limit {hop_limit}, channel {channel_index})\n"
        f"{outbound}\n{return_route}"
    )


class Plugin(BasePlugin):
    """Run structured traceroutes from Matrix without relaying commands to LoRa."""

    plugin_name = "traceroute"
    is_core_plugin = True

    def __init__(self, plugin_name: str | None = None) -> None:
        super().__init__(plugin_name=plugin_name)
        self._request_lock = threading.Lock()

    @property
    def description(self) -> str:
        return (
            "Trace the mesh route to a node from Matrix; "
            "usage: !traceroute <node-id-or-name> [--hops 1-7]"
        )

    def get_matrix_commands(self) -> list[str]:
        """Expose the full command and a concise alias."""
        return ["traceroute", "trace"]

    def _channel_index(self) -> int:
        return _bounded_int(
            self.config.get("channel_index"),
            default=0,
            minimum=0,
            maximum=MAX_CHANNEL_INDEX,
            setting_name="channel_index",
        )

    def _hop_limit(self, client: object, requested: int | None) -> int:
        if requested is not None:
            return requested
        return _bounded_int(
            self.config.get("hop_limit"),
            default=_default_hop_limit(client),
            minimum=1,
            maximum=MAX_TRACEROUTE_HOP_LIMIT,
            setting_name="hop_limit",
        )

    def generate_response(self, request: _TraceRequest) -> str:
        """Execute one structured traceroute under an exclusive radio slot."""
        # Lazy import avoids initializing transport configuration during discovery.
        from mmrelay.meshtastic_utils import (  # pylint: disable=import-outside-toplevel
            connect_meshtastic,
        )

        client = connect_meshtastic()
        if client is None:
            raise TraceRouteCommandError("Meshtastic is not connected.")

        request_method = getattr(client, "requestTraceRoute", None)
        if not callable(request_method):
            raise TraceRouteCommandError(
                "Structured traceroute requires the companion mtjk traceroute API."
            )
        with _traceroute_slot(self._request_lock):
            destination = _resolve_destination(client, request.destination)
            hop_limit = self._hop_limit(client, request.hop_limit)
            channel_index = self._channel_index()
            try:
                raw_result = request_method(  # pylint: disable=not-callable
                    dest=destination,
                    hopLimit=hop_limit,
                    channelIndex=channel_index,
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                detail = str(exc).strip() or type(exc).__name__
                raise TraceRouteCommandError(f"Traceroute failed: {detail}") from exc
            result = cast(_TraceRouteResult, raw_result)
            return _format_result(
                result,
                destination=destination,
                hop_limit=hop_limit,
                channel_index=channel_index,
            )

    async def handle_meshtastic_message(
        self, packet: Any, formatted_message: str, longname: str, meshnet_name: str
    ) -> bool:
        """Keep traceroute control and its potentially long response off LoRa."""
        _ = packet, formatted_message, longname, meshnet_name
        return False

    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
    ) -> bool:
        """Handle a Matrix traceroute command and publish its structured route."""
        _ = full_message
        parsed = self.get_matching_matrix_command_with_args(event)
        if parsed is None:
            return False
        _command, args = parsed

        try:
            request = _parse_request(args)
            response = await asyncio.to_thread(self.generate_response, request)
        except TraceRouteCommandError as exc:
            await self.send_matrix_message(
                room_id=room.room_id,
                message=str(exc),
                formatted=False,
            )
            await self.send_matrix_reaction(room.room_id, event.event_id, "❌")
            return True
        except Exception:  # pylint: disable=broad-exception-caught
            # Plugin handlers are the final command boundary and must not escape.
            self.logger.exception("Unexpected traceroute command failure")
            await self.send_matrix_message(
                room_id=room.room_id,
                message="Traceroute failed unexpectedly; check the relay logs.",
                formatted=False,
            )
            await self.send_matrix_reaction(room.room_id, event.event_id, "❌")
            return True

        await self.send_matrix_message(
            room_id=room.room_id,
            message=response,
            formatted=False,
        )
        await self.send_matrix_reaction(room.room_id, event.event_id, "✅")
        return True
