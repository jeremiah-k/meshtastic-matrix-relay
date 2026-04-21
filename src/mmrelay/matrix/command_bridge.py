import asyncio
import html
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import unquote

from nio import (
    AsyncClient,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

import mmrelay.matrix_utils as facade

__all__ = [
    "ParsedMatrixCommand",
    "_parse_matrix_message_command",
    "_estimate_clock_rollback_ms",
    "_refresh_bot_start_timestamps",
    "get_displayname",
    "bot_command",
    "_connect_meshtastic",
    "_get_meshtastic_interface_and_channel",
    "_handle_detection_sensor_packet",
]


@dataclass(frozen=True)
class ParsedMatrixCommand:
    """Normalized result for a parsed Matrix command invocation."""

    command: str
    args: str


def _extract_anchor_href(anchor_attrs: str) -> str | None:
    """
    Return the value of an exact ``href`` attribute from an anchor tag.

    The attribute name must be exactly ``href`` (case-insensitive). Attributes
    such as ``data-href`` or ``aria-href`` are ignored.
    """
    for attr_match in re.finditer(
        r"""(?is)(?:^|\s)(?P<name>[^\s=/>]+)\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<uq>[^\s>]+))""",
        anchor_attrs,
    ):
        if attr_match.group("name").casefold() != "href":
            continue
        for group_name in ("dq", "sq", "uq"):
            value = attr_match.group(group_name)
            if value is not None:
                href = value.strip()
                return href or None
    return None


def _extract_matrix_mxid_from_href(href: str) -> str | None:
    """
    Extract a Matrix MXID from common mention-link href formats.

    Supported patterns include matrix.to permalinks and direct/URI-style MXID
    references. Returns ``None`` when the href does not cleanly resolve to an
    MXID token.
    """
    decoded_href = unquote(html.unescape(href)).strip()
    if not decoded_href:
        return None

    matrix_to_match = re.search(
        r"(?i)matrix\.to/#/(?P<target>[^?#]+)",
        decoded_href,
    )
    if matrix_to_match is not None:
        target = unquote(matrix_to_match.group("target")).lstrip("/")
        return target if target.startswith("@") else None

    if decoded_href.startswith("matrix:"):
        target = unquote(decoded_href[len("matrix:") :]).lstrip("/")
        if target.startswith("u/"):
            target = target[2:]
            if target and not target.startswith("@"):
                target = f"@{target}"
        target = target.split("?", maxsplit=1)[0]
        return target if target.startswith("@") else None

    if decoded_href.startswith("@"):
        return decoded_href.split("?", maxsplit=1)[0]

    return None


def _normalize_formatted_body_for_command_detection(
    formatted_body: object, *, bot_mxid: str | None
) -> str:
    """
    Convert Matrix ``formatted_body`` HTML into conservative plain text for matching.

    The normalization removes reply blocks and tags, unescapes HTML entities, and
    collapses whitespace. For anchor tags, only links that resolve to the configured
    bot MXID are preserved (as the MXID token itself); all other anchors are
    discarded. This keeps mention matching strict while still supporting Matrix
    mention pills that display a human-friendly name.
    """
    if not isinstance(formatted_body, str) or not formatted_body:
        return ""

    normalized = re.sub(
        r"(?is)<mx-reply>.*?</mx-reply>",
        " ",
        formatted_body,
    )

    def _replace_anchor(match: re.Match[str]) -> str:
        if not bot_mxid:
            return " "
        anchor_attrs = match.group("attrs")
        href = _extract_anchor_href(anchor_attrs)
        if href is None:
            return " "
        mxid_target = _extract_matrix_mxid_from_href(href)
        return bot_mxid if mxid_target == bot_mxid else " "

    normalized = re.sub(
        r"(?is)<a\b(?P<attrs>[^>]*)>.*?</a>",
        _replace_anchor,
        normalized,
    )
    normalized = re.sub(r"(?i)<br\s*/?>", " ", normalized)
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    normalized = html.unescape(normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _build_command_lookup(commands: Iterable[str]) -> dict[str, str]:
    """Build case-insensitive command lookup preserving canonical spellings."""
    command_lookup: dict[str, str] = {}
    for command in commands:
        if not isinstance(command, str):
            continue
        normalized = command.strip()
        if not normalized:
            continue
        if normalized.startswith("!"):
            normalized = normalized[1:]
        if not normalized:
            continue
        key = normalized.casefold()
        if key not in command_lookup:
            command_lookup[key] = normalized
    return command_lookup


def _extract_candidate_bodies(
    message: (
        str | RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote
    ),
    *,
    bot_mxid: str | None,
) -> list[str]:
    """Return plain/normalized message variants used for command matching."""
    if isinstance(message, str):
        stripped = message.strip()
        return [stripped] if stripped else []

    bodies: list[str] = []
    plain_raw = getattr(message, "body", "")
    if isinstance(plain_raw, str):
        plain_body = plain_raw.strip()
        if plain_body:
            bodies.append(plain_body)

    source = getattr(message, "source", {})
    content = source.get("content", {}) if isinstance(source, dict) else {}
    formatted_body = (
        content.get("formatted_body", "") if isinstance(content, dict) else ""
    )
    normalized_formatted = _normalize_formatted_body_for_command_detection(
        formatted_body,
        bot_mxid=bot_mxid,
    )
    if normalized_formatted and normalized_formatted not in bodies:
        bodies.append(normalized_formatted)
    return bodies


def _resolve_bot_mxid() -> str | None:
    """Return the configured bot MXID as a safe string, if available."""
    identifier = facade.bot_user_id
    if not identifier:
        return None
    try:
        mxid = str(identifier).strip()
    except Exception as exc:  # noqa: BLE001 - broken __str__ should not crash parsing
        facade.logger.debug(
            "Failed to stringify bot MXID %r for command parsing: %s",
            identifier,
            type(exc).__name__,
        )
        return None
    return mxid or None


def _resolve_bot_display_name() -> str | None:
    """Return the configured bot display name as a safe string, if available."""
    name = facade.bot_user_name
    if not name:
        return None
    try:
        display_name = str(name).strip()
    except Exception:  # noqa: BLE001
        return None
    return display_name or None


def _consume_mxid_mention_prefix(message: str, bot_mxid: str) -> str | None:
    """
    Consume an exact bot MXID mention prefix and return the remaining text.

    Supported mention separators between MXID and command:
    - one or more whitespace characters
    - a single `:` immediately followed by whitespace
    """
    if not message.startswith(bot_mxid):
        return None

    remainder = message[len(bot_mxid) :]
    if not remainder:
        return None

    if remainder[0].isspace():
        return remainder.lstrip()

    if remainder[0] == ":":
        if len(remainder) < 2 or not remainder[1].isspace():
            return None
        return remainder[1:].lstrip()

    return None


def _consume_display_name_prefix(message: str, display_name: str) -> str | None:
    """
    Consume a display-name mention prefix and return the remaining text.

    This is a controlled fallback for Matrix clients that render mention pills
    with only the display name in the plain-text body.  Matching is
    case-insensitive and strictly anchored to the start of the message.

    Accepted forms:
    - ``DisplayName !cmd``  (whitespace separator)
    - ``DisplayName: !cmd`` (colon + whitespace separator)

    Rejected forms:
    - ``DisplayName!cmd``  (no separator)
    - ``words DisplayName !cmd`` (not at start)
    - partial prefix matches
    """
    if not display_name:
        return None

    if not message.lower().startswith(display_name.lower()):
        return None

    remainder = message[len(display_name) :]
    if not remainder:
        return None

    if remainder[0].isspace():
        return remainder.lstrip()

    if remainder[0] == ":":
        if len(remainder) < 2 or not remainder[1].isspace():
            return None
        return remainder[1:].lstrip()

    return None


def _match_bang_command(
    message: str, command_lookup: dict[str, str]
) -> ParsedMatrixCommand | None:
    """Parse a leading ``!command`` plus optional args from a message body."""
    if not message:
        return None

    # Sort longest-first so the alternation group matches the longest candidate.
    command_alternatives = sorted(command_lookup.values(), key=len, reverse=True)
    command_pattern = "|".join(re.escape(command) for command in command_alternatives)
    match = re.match(
        rf"^!(?P<command>{command_pattern})(?=$|\s)(?:\s+(?P<args>.*))?$",
        message,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    matched_command = match.group("command")
    canonical = command_lookup.get(matched_command.casefold(), matched_command)
    args = (match.group("args") or "").strip()
    return ParsedMatrixCommand(command=canonical, args=args)


def _parse_matrix_message_command(
    message: (
        str | RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote
    ),
    commands: Iterable[str],
    *,
    require_mention: bool = False,
) -> ParsedMatrixCommand | None:
    """
    Parse a Matrix message and return the matched command plus arguments.

    Mention matching is tried in priority order:

    A. **MXID mention** (exact match on ``@bot:server``):
       - ``@bot:server !cmd`` or ``@bot:server: !cmd``
       - Compact ``@bot:server!cmd`` is rejected.

    B. **Mention pill** (via ``formatted_body`` HTML):
       - ``<a href="matrix:…/@bot:server">DisplayName</a>: !cmd``
       - Matched by MXID link target, not visible text.

    C. **Display-name fallback** (only when A and B fail):
       - ``BotName !cmd`` or ``BotName: !cmd``
       - Case-insensitive, anchored at start, no arbitrary prefixes.
       - Intended for clients that strip MXID from the plain-text body.

    Display-name matching never overrides an MXID or pill match.  Matching
    is anchored to the start of the message; arbitrary prose before the
    command is not accepted.

    Command matching is case-insensitive; command canonicalization follows
    ``commands`` input.
    """
    command_lookup = _build_command_lookup(commands)
    if not command_lookup:
        return None

    bot_mxid = _resolve_bot_mxid()
    if require_mention and not bot_mxid:
        return None

    bot_display_name = _resolve_bot_display_name() if require_mention else None

    candidate_bodies = _extract_candidate_bodies(message, bot_mxid=bot_mxid)
    if not candidate_bodies:
        return None

    if require_mention:
        # Pass 1: require exact MXID mention semantics across all body variants.
        # This tier includes plain-body MXID prefixes and formatted-body mention
        # pills that normalize to MXID prefixes.
        for body in candidate_bodies:
            suffix = _consume_mxid_mention_prefix(body, bot_mxid)
            if suffix is None:
                continue
            parsed = _match_bang_command(suffix, command_lookup)
            if parsed is not None:
                return parsed

        # Pass 2: fall back to anchored display-name prefixes only if MXID tier
        # produced no result from any candidate body.
        if bot_display_name:
            for body in candidate_bodies:
                suffix = _consume_display_name_prefix(body, bot_display_name)
                if suffix is None:
                    continue
                parsed = _match_bang_command(suffix, command_lookup)
                if parsed is not None:
                    return parsed

        return None

    for body in candidate_bodies:
        parsed = _match_bang_command(body, command_lookup)
        if parsed is not None:
            return parsed

        if bot_mxid:
            suffix = _consume_mxid_mention_prefix(body, bot_mxid)
            if suffix is None:
                continue
            parsed = _match_bang_command(suffix, command_lookup)
            if parsed is not None:
                return parsed

    return None


def _estimate_clock_rollback_ms(
    bot_start_time: int, bot_start_monotonic_secs: float
) -> int:
    """
    Estimate how many milliseconds the local clock has rolled backward since bot startup.

    Compares the expected current time (based on monotonic elapsed time since startup)
    against the actual wall-clock time to detect clock rollback events.

    Parameters:
        bot_start_time: The bot's startup timestamp in milliseconds (from time.time()).
        bot_start_monotonic_secs: The bot's startup monotonic time in seconds.

    Returns:
        The estimated rollback in milliseconds. Positive values indicate the local
        clock appears to have stepped backward relative to the monotonic clock.
    """
    now_ms = int(facade.time.time() * facade.MILLISECONDS_PER_SECOND)
    elapsed_ms = int(
        (facade.time.monotonic() - bot_start_monotonic_secs)
        * facade.MILLISECONDS_PER_SECOND
    )
    expected_now_ms = bot_start_time + elapsed_ms
    return expected_now_ms - now_ms


def _refresh_bot_start_timestamps() -> None:
    """
    Refresh bot_start_time and bot_start_monotonic_secs to the current wall/monotonic time.

    Called at the start of each Matrix bootstrap so that stale-event startup
    window filtering is anchored to the actual bootstrap rather than module import.

    Note: Not thread-safe. Must be called only during single-threaded bootstrap,
    before concurrent event processing begins.
    """
    facade.bot_start_time = int(facade.time.time() * facade.MILLISECONDS_PER_SECOND)
    facade.bot_start_monotonic_secs = facade.time.monotonic()


async def get_displayname(user_id: str) -> str | None:
    """
    Get the display name for a given user ID.

    Parameters:
        user_id (str): The Matrix user ID.

    Returns:
        str | None: The display name, or None if not available.
    """
    if facade.matrix_client:
        client = cast(AsyncClient, facade.matrix_client)
        response = await client.get_displayname(user_id)
        return getattr(response, "displayname", None)
    return None


def bot_command(
    command: str,
    event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
    require_mention: bool = False,
) -> bool:
    """
    Determine whether a Matrix event addresses the bot with the given command.

    Uses the shared Matrix command parser against both plain ``body`` and
    normalized ``formatted_body`` content. Mentions are MXID-only.

    Parameters:
        command (str): Command name to detect (without the leading `!`).
        event: Matrix event object expected to provide a plain `body` and a `source`/`content` with optional `formatted_body`.
        require_mention (bool): If True, only accept commands that explicitly mention the bot; if False, accept bare `!<command>` messages as well.

    Returns:
        bool: `True` if the message addresses the bot with the given command, `False` otherwise.
    """
    if not command:
        return False

    return (
        _parse_matrix_message_command(
            event,
            (command,),
            require_mention=require_mention,
        )
        is not None
    )


async def _connect_meshtastic() -> Any:
    """
    Obtain a Meshtastic interface usable from asynchronous code.

    Returns:
        meshtastic_iface: The Meshtastic interface or proxy object produced by the synchronous connector.
    """
    return await asyncio.to_thread(facade.connect_meshtastic)


async def _get_meshtastic_interface_and_channel(
    room_config: dict[str, Any], purpose: str
) -> tuple[Any | None, int | None]:
    """
    Return a connected Meshtastic interface and the room's validated Meshtastic channel.

    Parameters:
        room_config (dict): Room configuration; must contain a non-negative integer under "meshtastic_channel".
        purpose (str): Short description of the caller's intent used in logged error messages.

    Returns:
        tuple: (meshtastic_interface, channel)
            - meshtastic_interface (Any | None): A connected Meshtastic interface object, or `None` if a connection could not be made.
            - channel (int | None): The validated non-negative channel number from the room config, or `None` if missing or invalid.
    """
    from mmrelay.meshtastic_utils import logger as meshtastic_logger

    meshtastic_channel = room_config.get("meshtastic_channel")
    if meshtastic_channel is None:
        meshtastic_logger.error(
            f"Room config missing 'meshtastic_channel'; cannot {purpose}."
        )
        return None, None
    if (
        isinstance(meshtastic_channel, bool)
        or not isinstance(meshtastic_channel, int)
        or meshtastic_channel < 0
    ):
        meshtastic_logger.error(
            f"Invalid meshtastic_channel value {meshtastic_channel!r} in room config; must be a non-negative integer."
        )
        return None, None

    meshtastic_interface = await facade._connect_meshtastic()
    if not meshtastic_interface:
        meshtastic_logger.error(f"Failed to connect to Meshtastic. Cannot {purpose}.")
        return None, None

    return meshtastic_interface, meshtastic_channel


async def _handle_detection_sensor_packet(
    config: dict[str, Any],
    room_config: dict[str, Any],
    full_display_name: str,
    text: str,
) -> None:
    """
    Relay detection-sensor text from Matrix to Meshtastic as a DETECTION_SENSOR_APP payload when enabled.

    If both global broadcast and detection_sensor processing are enabled, queue the provided text on the room's configured Meshtastic channel using the DETECTION_SENSOR_APP port; otherwise do nothing. Logs outcomes and returns silently on failures to obtain a Meshtastic interface or channel.

    Parameters:
        config (dict[str, Any]): Global configuration used to determine feature flags.
        room_config (dict[str, Any]): Room-specific configuration; must include "meshtastic_channel".
        full_display_name (str): Matrix sender display name used in the queued message description.
        text (str): Plain-text payload to send.
    """
    detection_enabled = facade.get_meshtastic_config_value(
        config, "detection_sensor", facade.DEFAULT_DETECTION_SENSOR
    )
    broadcast_enabled = facade.get_meshtastic_config_value(
        config,
        "broadcast_enabled",
        facade.DEFAULT_BROADCAST_ENABLED,
        required=False,
    )
    from mmrelay.meshtastic_utils import logger as meshtastic_logger

    if not broadcast_enabled:
        meshtastic_logger.debug(
            f"Detection sensor packet received from {full_display_name}, but broadcast is disabled."
        )
        return

    if not detection_enabled:
        meshtastic_logger.debug(
            f"Detection sensor packet received from {full_display_name}, but detection sensor processing is disabled."
        )
        return

    (
        meshtastic_interface,
        meshtastic_channel,
    ) = await facade._get_meshtastic_interface_and_channel(
        room_config, "relay detection data"
    )
    if not meshtastic_interface:
        return

    import meshtastic.protobuf.portnums_pb2

    success = facade.queue_message(
        meshtastic_interface.sendData,
        data=text.encode(facade.DEFAULT_TEXT_ENCODING, facade.ENCODING_ERROR_IGNORE),
        channelIndex=meshtastic_channel,
        portNum=meshtastic.protobuf.portnums_pb2.PortNum.DETECTION_SENSOR_APP,
        description=f"Detection sensor data from {full_display_name}",
    )

    if success:
        queue_size = facade.get_message_queue().get_queue_size()
        if queue_size > 1:
            meshtastic_logger.info(
                f"Relaying detection sensor data from {full_display_name} to radio broadcast (queued: {queue_size} messages)"
            )
        else:
            meshtastic_logger.info(
                f"Relaying detection sensor data from {full_display_name} to radio broadcast"
            )
    else:
        meshtastic_logger.error("Failed to relay detection sensor data to Meshtastic")
