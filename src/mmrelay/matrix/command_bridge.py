import asyncio
import re
from typing import Any, cast

from nio import (
    AsyncClient,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

import mmrelay.matrix_utils as facade

__all__ = [
    "_estimate_clock_rollback_ms",
    "_refresh_bot_start_timestamps",
    "get_displayname",
    "bot_command",
    "_connect_meshtastic",
    "_get_meshtastic_interface_and_channel",
    "_handle_detection_sensor_packet",
]


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

    Checks the event's plain and HTML-formatted bodies. Matches when the message either starts with `!<command>` (only allowed when `require_mention` is False) or begins with an explicit mention of the bot (bot MXID or display name) optionally followed by punctuation/whitespace and then `!<command>`.

    Parameters:
        command (str): Command name to detect (without the leading `!`).
        event: Matrix event object expected to provide a plain `body` and a `source`/`content` with optional `formatted_body`.
        require_mention (bool): If True, only accept commands that explicitly mention the bot; if False, accept bare `!<command>` messages as well.

    Returns:
        bool: `True` if the message addresses the bot with the given command, `False` otherwise.
    """
    full_message = (getattr(event, "body", "") or "").strip()
    if not command:
        return False
    content = event.source.get("content", {})
    formatted_body = content.get("formatted_body", "")

    text_content = re.sub(r"<[^>]+>", "", formatted_body).strip()

    bodies = [full_message, text_content]

    bare_pattern = rf"^!{re.escape(command)}(?:\s|$)"

    if not require_mention and any(
        re.match(bare_pattern, body, re.IGNORECASE) for body in bodies if body
    ):
        return True

    mention_parts: list[str] = []
    for ident in (facade.bot_user_id, facade.bot_user_name):
        if ident:
            try:
                mention_parts.append(re.escape(str(ident)))
            except Exception as exc:  # noqa: BLE001 - str() may invoke broken __str__
                facade.logger.debug(
                    "Failed to escape identifier %r for bot_command pattern: %s",
                    ident,
                    type(exc).__name__,
                )
                continue

    if not mention_parts:
        return False

    pattern = (
        rf"^(?:{'|'.join(mention_parts)})[,:;]?\s*!" rf"{re.escape(command)}(?:\s|$)"
    )

    return any(re.match(pattern, body, re.IGNORECASE) for body in bodies if body)


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

    meshtastic_interface = await facade._connect_meshtastic()
    if not meshtastic_interface:
        meshtastic_logger.error(f"Failed to connect to Meshtastic. Cannot {purpose}.")
        return None, None

    meshtastic_channel = room_config.get("meshtastic_channel")
    if meshtastic_channel is None:
        meshtastic_logger.error(
            f"Room config missing 'meshtastic_channel'; cannot {purpose}."
        )
        return None, None
    if not isinstance(meshtastic_channel, int) or meshtastic_channel < 0:
        meshtastic_logger.error(
            f"Invalid meshtastic_channel value {meshtastic_channel!r} in room config; must be a non-negative integer."
        )
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
