import asyncio
from typing import Any, cast

from nio import AsyncClient

import mmrelay.matrix_utils as facade

__all__ = [
    "truncate_message",
    "strip_quoted_lines",
    "get_user_display_name",
    "format_reply_message",
    "send_reply_to_meshtastic",
    "handle_matrix_reply",
]


def truncate_message(
    text: str, max_bytes: int = facade.DEFAULT_MESSAGE_TRUNCATE_BYTES
) -> str:
    """
    Truncate text so its UTF-8 encoding occupies no more than the given byte limit.

    If `max_bytes` cuts a multi-byte UTF-8 character, the partial character is discarded so the result is valid UTF-8.

    Parameters:
        text (str): Input text to truncate.
        max_bytes (int): Maximum allowed size in bytes for the UTF-8 encoded result.

    Returns:
        str: A string whose UTF-8 encoding is at most `max_bytes` bytes.
    """
    truncated_text = text.encode(facade.DEFAULT_TEXT_ENCODING)[:max_bytes].decode(
        facade.DEFAULT_TEXT_ENCODING, facade.ENCODING_ERROR_IGNORE
    )
    return truncated_text


def strip_quoted_lines(text: str) -> str:
    """
    Strip quoted lines (lines starting with '>') from a block of text.

    Parameters:
        text (str): Input text possibly containing quoted lines.

    Returns:
        str: The remaining non-quoted lines joined with single spaces and trimmed of leading/trailing whitespace.
    """
    lines = text.splitlines()
    filtered = [line.strip() for line in lines if not line.strip().startswith(">")]
    return " ".join(line for line in filtered if line).strip()


async def get_user_display_name(
    room: facade.MatrixRoom,
    event: (
        facade.RoomMessageText
        | facade.RoomMessageNotice
        | facade.ReactionEvent
        | facade.RoomMessageEmote
    ),
) -> str:
    """
    Get the display name for an event sender, preferring a room-specific name.

    If the room defines a per-room display name for the sender, that name is returned.
    Otherwise the global display name from the homeserver is returned when available.
    If no display name can be determined, the sender's Matrix ID (MXID) is returned.

    Returns:
        str: The sender's display name or their MXID.
    """
    room_display_name = room.user_name(event.sender)
    if room_display_name:
        return facade.cast(str, room_display_name)

    response_types = tuple(
        t for t in (facade.ProfileGetDisplayNameResponse,) if isinstance(t, type)
    )
    error_types = tuple(
        t for t in (facade.ProfileGetDisplayNameError,) if isinstance(t, type)
    )

    if facade.matrix_client:
        try:
            client = cast(AsyncClient, facade.matrix_client)
            display_name_response = await client.get_displayname(event.sender)
            if response_types and isinstance(display_name_response, response_types):
                return facade.cast(
                    str,
                    getattr(display_name_response, "displayname", None) or event.sender,
                )
            if error_types and isinstance(display_name_response, error_types):
                facade.logger.debug(
                    "Failed to get display name for %s: %s",
                    event.sender,
                    getattr(display_name_response, "message", display_name_response),
                )
            else:
                facade.logger.debug(
                    "Unexpected display name response type %s for %s",
                    type(display_name_response),
                    event.sender,
                )
            display_attr = getattr(display_name_response, "displayname", None)
            if display_attr:
                return facade.cast(str, display_attr)
        except facade.NIO_COMM_EXCEPTIONS as e:
            facade.logger.debug(f"Failed to get display name for {event.sender}: {e}")
            return facade.cast(str, event.sender)
    return facade.cast(str, event.sender)


def format_reply_message(
    config: dict[str, Any],
    full_display_name: str,
    text: str,
    *,
    longname: str | None = None,
    shortname: str | None = None,
    meshnet_name: str | None = None,
    local_meshnet_name: str | None = None,
    mesh_text_override: str | None = None,
    user_id: str | None = None,
) -> str:
    """
    Format a Meshtastic-style reply, applying an appropriate sender prefix and truncating the result to the configured maximum length.

    Parameters:
        config (dict[str, Any]): Runtime configuration used to build prefix formats.
        full_display_name (str): Sender's full display name used when constructing local prefixes.
        text (str): Original reply text; quoted lines (leading '>') will be removed.
        longname (str | None): Optional long form of the sender name for remote-meshnet prefixes.
        shortname (str | None): Optional short form of the sender name for remote-meshnet prefixes.
        meshnet_name (str | None): Remote meshnet name; when provided and different from local_meshnet_name, remote-prefix rules are applied.
        local_meshnet_name (str | None): Local meshnet name used to determine whether a reply is remote.
        mesh_text_override (str | None): Optional raw Meshtastic payload preferred over `text` when generating the reply body.

    Returns:
        str: The formatted reply message with quoted lines removed, the appropriate prefix applied (remote or local), and truncated to the configured maximum length.
    """
    base_text = mesh_text_override if mesh_text_override else text

    clean_text = strip_quoted_lines(base_text).strip()

    if meshnet_name and local_meshnet_name and meshnet_name != local_meshnet_name:
        sender_long = longname or full_display_name or shortname or "???"
        sender_short = (
            shortname or sender_long[: facade.SHORTNAME_FALLBACK_LENGTH] or "???"
        )
        short_meshnet_name = meshnet_name[: facade.MESHNET_NAME_ABBREVIATION_LENGTH]

        prefix_candidates = [
            f"[{sender_long}/{meshnet_name}]: ",
            f"[{sender_long}/{short_meshnet_name}]: ",
            f"{sender_long}/{meshnet_name}: ",
            f"{sender_long}/{short_meshnet_name}: ",
            f"{sender_short}/{meshnet_name}: ",
            f"{sender_short}/{short_meshnet_name}: ",
        ]

        matrix_prefix_full = facade.get_matrix_prefix(
            config, sender_long, sender_short, meshnet_name
        )
        matrix_prefix_short = facade.get_matrix_prefix(
            config, sender_long, sender_short, short_meshnet_name
        )
        prefix_candidates.extend([matrix_prefix_full, matrix_prefix_short])

        for candidate in prefix_candidates:
            if candidate and clean_text.startswith(candidate):
                clean_text = clean_text[len(candidate) :].lstrip()
                break

        if not clean_text and mesh_text_override:
            clean_text = strip_quoted_lines(mesh_text_override).strip()

        mesh_prefix = (
            matrix_prefix_short
            or matrix_prefix_full
            or f"{sender_short}/{short_meshnet_name}:"
        )
        reply_body = f" {clean_text}" if clean_text else ""
        reply_message = f"{mesh_prefix}{reply_body}"
        return truncate_message(reply_message.strip())

    prefix = facade.get_meshtastic_prefix(config, full_display_name, user_id)
    reply_message = f"{prefix}{clean_text}" if clean_text else prefix.rstrip()
    return truncate_message(reply_message)


async def send_reply_to_meshtastic(
    reply_message: str,
    full_display_name: str,
    room_config: dict[str, Any],
    room: facade.MatrixRoom,
    event: (
        facade.RoomMessageText
        | facade.RoomMessageNotice
        | facade.ReactionEvent
        | facade.RoomMessageEmote
    ),
    text: str,
    storage_enabled: bool,
    local_meshnet_name: str,
    reply_id: int | None = None,
    relay_config: dict[str, Any] | None = None,
) -> bool:
    """
    Queue a Meshtastic delivery for a Matrix reply, optionally sending it as a structured reply that targets a specific Meshtastic message.

    Creates and attaches message-mapping metadata when storage is enabled, respects the channel from room_config, and honors an optional relay_config override. Enqueues either a structured reply (when reply_id is provided) or a regular broadcast and logs outcomes; the function handles errors internally and does not raise.

    Parameters:
        reply_message (str): Meshtastic-ready text payload to send.
        full_display_name (str): Sender display name used in queue descriptions and logs.
        room_config (dict): Room-specific configuration; must include "meshtastic_channel" (integer channel index).
        room (facade.MatrixRoom): Matrix room object; room.room_id is used in mapping metadata.
        event (facade.RoomMessageText | facade.RoomMessageNotice | facade.ReactionEvent | facade.RoomMessageEmote): Matrix event object; event.event_id is used in mapping metadata.
        text (str): Original Matrix message text used when building mapping metadata.
        storage_enabled (bool): If True, create and attach a message-mapping record for correlation of future replies/reactions.
        local_meshnet_name (str): Local meshnet name to include in mapping metadata when present.
        reply_id (int | None): If provided, send as a structured Meshtastic reply targeting this Meshtastic message ID; if None, send as a regular broadcast.
        relay_config (dict[str, Any] | None): Optional config override to control Meshtastic broadcast and message-map settings.

    Returns:
        bool: `True` if the message was successfully queued for delivery to Meshtastic, `False` otherwise.
    """
    (
        meshtastic_interface,
        meshtastic_channel,
    ) = await facade._get_meshtastic_interface_and_channel(room_config, "relay reply")
    from mmrelay.meshtastic_utils import logger as meshtastic_logger

    if not meshtastic_interface or meshtastic_channel is None:
        return False

    effective_config = relay_config if relay_config is not None else facade.config
    if effective_config is None:
        effective_config = {}
    broadcast_enabled = facade.get_meshtastic_config_value(
        effective_config,
        "broadcast_enabled",
        facade.DEFAULT_BROADCAST_ENABLED,
        required=False,
    )
    facade.logger.debug(f"broadcast_enabled = {broadcast_enabled}")

    if not broadcast_enabled:
        return False

    try:
        mapping_info = None
        if storage_enabled:
            msgs_to_keep = facade._get_msgs_to_keep_config(effective_config)

            mapping_info = facade._create_mapping_info(
                event.event_id, room.room_id, text, local_meshnet_name, msgs_to_keep
            )

        if reply_id is not None:
            success = facade.queue_message(
                facade.send_text_reply,
                meshtastic_interface,
                text=reply_message,
                reply_id=reply_id,
                channelIndex=meshtastic_channel,
                description=f"Reply from {full_display_name} to message {reply_id}",
                mapping_info=mapping_info,
            )

            if success:
                queue_size = facade.get_message_queue().get_queue_size()

                if queue_size > 1:
                    meshtastic_logger.info(
                        f"Relaying Matrix reply from {full_display_name} to radio broadcast as structured reply (queued: {queue_size} messages)"
                    )
                else:
                    meshtastic_logger.info(
                        f"Relaying Matrix reply from {full_display_name} to radio broadcast as structured reply"
                    )
                return True
            else:
                meshtastic_logger.error(
                    "Failed to relay structured reply to Meshtastic"
                )
                return False
        else:
            success = facade.queue_message(
                meshtastic_interface.sendText,
                text=reply_message,
                channelIndex=meshtastic_channel,
                description=f"Reply from {full_display_name} (fallback to regular message)",
                mapping_info=mapping_info,
            )

            if success:
                queue_size = facade.get_message_queue().get_queue_size()

                if queue_size > 1:
                    meshtastic_logger.info(
                        f"Relaying Matrix reply from {full_display_name} to radio broadcast (queued: {queue_size} messages)"
                    )
                else:
                    meshtastic_logger.info(
                        f"Relaying Matrix reply from {full_display_name} to radio broadcast"
                    )
                return True
            else:
                meshtastic_logger.error("Failed to relay reply message to Meshtastic")
                return False

    except Exception:
        meshtastic_logger.exception("Error sending Matrix reply to Meshtastic")
        return False


async def handle_matrix_reply(
    room: facade.MatrixRoom,
    event: (
        facade.RoomMessageText
        | facade.RoomMessageNotice
        | facade.ReactionEvent
        | facade.RoomMessageEmote
    ),
    reply_to_event_id: str,
    text: str,
    room_config: dict[str, Any],
    storage_enabled: bool,
    local_meshnet_name: str,
    config: dict[str, Any],
    *,
    mesh_text_override: str | None = None,
    longname: str | None = None,
    shortname: str | None = None,
    meshnet_name: str | None = None,
) -> bool:
    """
    Forward a Matrix reply to Meshtastic when the replied-to Matrix event maps to a Meshtastic message.

    If the Matrix event identified by reply_to_event_id has an associated Meshtastic mapping, format a Meshtastic reply that preserves sender attribution and enqueue it referencing the original Meshtastic message ID. If no mapping exists, do nothing.

    Parameters:
        room: Matrix room object where the reply originated.
        event: Matrix event object representing the reply.
        reply_to_event_id (str): Matrix event ID being replied to; used to locate the Meshtastic mapping.
        text (str): The reply text from Matrix.
        room_config (dict): Per-room relay configuration used when sending to Meshtastic.
        storage_enabled (bool): Whether message mapping/storage is enabled.
        local_meshnet_name (str): Local meshnet name used to determine cross-meshnet formatting.
        config (dict): Global relay configuration passed to formatting routines.
        mesh_text_override (str | None): Optional override text to send instead of the derived text.
        longname (str | None): Sender long display name used for prefixing.
        shortname (str | None): Sender short display name used for prefixing.
        meshnet_name (str | None): Remote meshnet name associated with the original mapping, if any.

    Returns:
        bool: `True` if a mapping was found and the reply was queued to Meshtastic, `False` otherwise.
    """
    loop = asyncio.get_running_loop()
    orig = await loop.run_in_executor(
        None, facade.get_message_map_by_matrix_event_id, reply_to_event_id
    )
    if not orig:
        facade.logger.debug(
            f"Original message for Matrix reply not found in DB: {reply_to_event_id}"
        )
        return False

    original_meshtastic_id_raw = orig[0]
    if isinstance(original_meshtastic_id_raw, int):
        original_meshtastic_id = original_meshtastic_id_raw
    elif isinstance(original_meshtastic_id_raw, str):
        if original_meshtastic_id_raw.isdigit():
            original_meshtastic_id = int(original_meshtastic_id_raw)
        else:
            facade.logger.warning(
                "Message map meshtastic_id %r is not numeric; sending broadcast reply",
                original_meshtastic_id_raw,
            )
            original_meshtastic_id = None
    else:
        facade.logger.warning(
            "Message map meshtastic_id has unexpected type %s; sending broadcast reply",
            type(original_meshtastic_id_raw).__name__,
        )
        original_meshtastic_id = None

    full_display_name = await facade.get_user_display_name(room, event)

    reply_meshnet_name = meshnet_name

    reply_message = facade.format_reply_message(
        config,
        full_display_name,
        text,
        longname=longname,
        shortname=shortname,
        meshnet_name=reply_meshnet_name,
        local_meshnet_name=local_meshnet_name,
        mesh_text_override=mesh_text_override,
        user_id=event.sender,
    )

    if original_meshtastic_id is not None:
        facade.logger.info(
            f"Relaying Matrix reply from {full_display_name} to Meshtastic as reply to message {original_meshtastic_id}"
        )
    else:
        facade.logger.info(
            f"Relaying Matrix reply from {full_display_name} to Meshtastic as broadcast reply"
        )

    return await facade.send_reply_to_meshtastic(
        reply_message,
        full_display_name,
        room_config,
        room,
        event,
        text,
        storage_enabled,
        local_meshnet_name,
        reply_id=original_meshtastic_id,
        relay_config=config,
    )
