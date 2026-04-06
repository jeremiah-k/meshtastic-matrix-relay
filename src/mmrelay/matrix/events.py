"""Matrix event handlers.

Extracted from matrix_utils.py — on_room_message, on_decryption_failure,
on_room_member, and on_invite.
"""

import asyncio
import inspect
import re
import time
from typing import Any, cast

from nio import (
    AsyncClient,
    MatrixRoom,
    MegolmEvent,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
    ToDeviceError,
    ToDeviceResponse,
)

try:
    from nio import InviteMemberEvent
except ImportError:
    from nio.events.invite_events import InviteMemberEvent

from nio.events.room_events import RoomMemberEvent

import mmrelay.matrix_utils as facade

__all__ = [
    "on_decryption_failure",
    "on_room_message",
    "on_room_member",
    "on_invite",
]


async def on_decryption_failure(room: MatrixRoom, event: MegolmEvent) -> None:
    """
    Handle a MegolmEvent that could not be decrypted by requesting missing session keys with exponential backoff retry.

    If the module-level Matrix client is available, this sets the event's room_id, constructs a to-device key request for the missing Megolm session, and and sends it to the device that holds the keys. Uses exponential backoff retry
    to handle transient federation delays. Logs outcomes and returns without action if no matrix client or device id is available.

    Parameters:
        room (MatrixRoom): The room where the decryption failure occurred.
        event (MegolmEvent): The encrypted event that failed to decrypt; its `room_id` may be updated as part of the request side effect.
    """
    facade.logger.error(
        f"Failed to decrypt event '{event.event_id}' in room '{room.room_id}'! "
        f"This is usually temporary and resolves on its own. "
        f"If this persists, the bot's session may be corrupt. "
        f"{facade.msg_retry_auth_login()}."
    )

    if not facade.matrix_client:
        facade.logger.error("Matrix client not available, cannot request keys.")
        return

    event.room_id = room.room_id

    if not facade.matrix_client.device_id:
        facade.logger.error(
            "Cannot request keys for event %s: client has no device_id",
            event.event_id,
        )
        return

    request = event.as_key_request(
        facade.matrix_client.user_id, facade.matrix_client.device_id
    )

    for attempt in range(facade.E2EE_KEY_REQUEST_MAX_ATTEMPTS):
        is_last_attempt = attempt >= facade.E2EE_KEY_REQUEST_MAX_ATTEMPTS - 1
        backoff_delay = (
            facade._retry_backoff_delay(
                attempt,
                facade.E2EE_KEY_REQUEST_BASE_DELAY,
                facade.E2EE_KEY_REQUEST_MAX_DELAY,
            )
            if not is_last_attempt
            else None
        )
        try:
            response = await asyncio.wait_for(
                facade.matrix_client.to_device(request),
                timeout=facade.MATRIX_TO_DEVICE_TIMEOUT,
            )
            if isinstance(response, ToDeviceResponse):
                facade.logger.info(
                    f"Requested keys for failed decryption of event {event.event_id} "
                    f"(attempt {attempt + 1}/{facade.E2EE_KEY_REQUEST_MAX_ATTEMPTS})"
                )
                await asyncio.sleep(facade.E2EE_KEY_SHARING_DELAY_SECONDS)
                return
            elif isinstance(response, ToDeviceError):
                error_details = getattr(response, "message", str(response))
                facade.logger.warning(
                    "Key request for event %s failed on attempt %s/%s: %s",
                    event.event_id,
                    attempt + 1,
                    facade.E2EE_KEY_REQUEST_MAX_ATTEMPTS,
                    error_details,
                )
                if is_last_attempt:
                    facade.logger.error(
                        "Failed to request keys for event %s after %s attempts. Last error: %s",
                        event.event_id,
                        facade.E2EE_KEY_REQUEST_MAX_ATTEMPTS,
                        error_details,
                    )
                    return
            else:
                response_type = type(response).__name__
                facade.logger.warning(
                    "Unexpected key request response type %s for event %s (attempt %s/%s)",
                    response_type,
                    event.event_id,
                    attempt + 1,
                    facade.E2EE_KEY_REQUEST_MAX_ATTEMPTS,
                )
                if is_last_attempt:
                    facade.logger.error(
                        "Failed to request keys for event %s after %s attempts due to unexpected response type %s",
                        event.event_id,
                        facade.E2EE_KEY_REQUEST_MAX_ATTEMPTS,
                        response_type,
                    )
                    return
        except facade.NIO_COMM_EXCEPTIONS:
            if is_last_attempt:
                facade.logger.exception(
                    f"Failed to request keys for event {event.event_id} "
                    f"after {facade.E2EE_KEY_REQUEST_MAX_ATTEMPTS} attempts"
                )
                return
            facade.logger.warning(
                f"Key request attempt {attempt + 1} failed for event {event.event_id}, retrying..."
            )
            facade.logger.debug("Key request failure details", exc_info=True)

        if backoff_delay is not None:
            await asyncio.sleep(backoff_delay)


async def on_room_message(
    room: MatrixRoom,
    event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
) -> None:
    """
    Handle an incoming Matrix room event and bridge eligible events to Meshtastic.

    Processes RoomMessageText, RoomMessageNotice, RoomMessageEmote, and ReactionEvent events for configured rooms. Ignores messages sent by the bot. Respects per-room and global interaction settings (reactions and replies), delegates command handling to plugins (preventing relay when handled), and forwards eligible reactions, replies, detection-sensor packets, remote-meshnet messages, and ordinary Matrix messages to Meshtastic. When configured, creates and attaches message mapping metadata for reply/reaction correlation.

    Parameters:
        room (MatrixRoom): The Matrix room where the event was received.
        event (RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote): The received room event.
    """
    facade.logger.debug(
        f"Received Matrix event in room {room.room_id}: {type(event).__name__}"
    )
    facade.logger.debug(
        f"Event details - sender: {event.sender}, timestamp: {event.server_timestamp}"
    )

    from mmrelay.meshtastic_utils import logger as meshtastic_logger

    full_display_name = "Unknown user"
    message_timestamp = event.server_timestamp

    if message_timestamp < facade.bot_start_time:
        skew_ms = facade.bot_start_time - message_timestamp
        rollback_ms = facade._estimate_clock_rollback_ms(
            facade.bot_start_time, facade.bot_start_monotonic_secs
        )
        elapsed_since_start_ms = max(
            0,
            int(
                (time.monotonic() - facade.bot_start_monotonic_secs)
                * facade.MILLISECONDS_PER_SECOND
            ),
        )
        baseline_plausible = (
            message_timestamp >= facade.MATRIX_EVENT_EPOCH_FLOOR_MS
            and facade.bot_start_time >= facade.MATRIX_EVENT_EPOCH_FLOOR_MS
        )
        rollback_detected = rollback_ms > facade.MATRIX_CLOCK_ROLLBACK_DISABLE_MS
        startup_window_active = (
            elapsed_since_start_ms <= facade.MATRIX_STARTUP_STALE_FILTER_WINDOW_MS
        )

        if (
            baseline_plausible
            and startup_window_active
            and not rollback_detected
            and skew_ms > facade.MATRIX_STALE_STARTUP_EVENT_DROP_MS
        ):
            facade.logger.debug(
                "Dropping stale Matrix event predating startup baseline "
                "(event_ts=%s bot_start_time=%s skew_ms=%s sender=%s room=%s)",
                message_timestamp,
                facade.bot_start_time,
                skew_ms,
                event.sender,
                room.room_id,
            )
            return

        if skew_ms > facade.MATRIX_STARTUP_TIMESTAMP_TOLERANCE_MS:
            reason = (
                "clock rollback detected"
                if rollback_detected
                else (
                    "within startup window, tolerating skew"
                    if startup_window_active
                    else "startup stale filter window elapsed"
                )
            )
            facade.logger.debug(
                "Processing Matrix event despite startup timestamp skew "
                "(event_ts=%s bot_start_time=%s skew_ms=%s sender=%s room=%s reason=%s)",
                message_timestamp,
                facade.bot_start_time,
                skew_ms,
                event.sender,
                room.room_id,
                reason,
            )

    if event.sender == facade.bot_user_id:
        return

    room_config = None
    iterable: list[dict[str, Any]] | list[Any] = (
        list(facade.matrix_rooms.values())
        if facade.matrix_rooms and isinstance(facade.matrix_rooms, dict)
        else list(facade.matrix_rooms or [])
    )
    for room_conf in iterable:
        if isinstance(room_conf, dict) and room_conf.get("id") == room.room_id:
            room_config = room_conf
            break

    if not room_config:
        return

    relates_to = event.source["content"].get("m.relates_to")

    if not facade.config:
        facade.logger.error("No configuration available for Matrix message processing.")

    is_reaction = False
    reaction_emoji = None
    original_matrix_event_id = None

    if facade.config is None:
        facade.logger.error(
            "No configuration available. Cannot process Matrix message."
        )
        return

    interactions = facade.get_interaction_settings(facade.config)
    storage_enabled = facade.message_storage_enabled(interactions)

    if isinstance(event, ReactionEvent):
        is_reaction = True
        facade.logger.debug(f"Processing Matrix reaction event: {event.source}")
        if relates_to and "event_id" in relates_to and "key" in relates_to:
            reaction_emoji = relates_to["key"]
            original_matrix_event_id = relates_to["event_id"]
            facade.logger.debug(
                f"Original matrix event ID: {original_matrix_event_id}, Reaction emoji: {reaction_emoji}"
            )

    if isinstance(event, RoomMessageEmote):
        facade.logger.debug(f"Processing Matrix emote event: {event.source}")
        content = event.source.get("content", {})
        reaction_body = content.get("body", "")
        meshtastic_replyId = content.get("meshtastic_replyId")
        emote_relates_to = content.get("m.relates_to") or {}

        is_reaction = bool(
            meshtastic_replyId or emote_relates_to.get("rel_type") == "m.annotation"
        )

        if is_reaction:
            reaction_match = re.search(r"reacted (.+?) to", reaction_body)
            reaction_emoji = reaction_match.group(1).strip() if reaction_match else "?"
            if emote_relates_to and "event_id" in emote_relates_to:
                original_matrix_event_id = emote_relates_to["event_id"]

    mesh_text_override = event.source["content"].get("meshtastic_text")
    if isinstance(mesh_text_override, str):
        mesh_text_override = mesh_text_override.strip()
        if not mesh_text_override:
            mesh_text_override = None
    else:
        mesh_text_override = None

    longname = event.source["content"].get("meshtastic_longname")
    shortname = event.source["content"].get("meshtastic_shortname", None)
    meshnet_name = event.source["content"].get("meshtastic_meshnet")
    meshtastic_replyId = event.source["content"].get("meshtastic_replyId")
    suppress = event.source["content"].get("mmrelay_suppress")

    text = ""

    if not is_reaction or mesh_text_override:
        body_text = getattr(event, "body", "")
        content_body = event.source["content"].get("body", "")
        text = mesh_text_override or body_text or content_body or ""
        text = text.strip()

    if suppress:
        return

    if is_reaction and not interactions["reactions"]:
        facade.logger.debug(
            "Reaction event encountered but reactions are disabled. Doing nothing."
        )
        return

    local_meshnet_name = facade.get_meshtastic_config_value(
        facade.config, "meshnet_name", ""
    )

    is_reply = False
    reply_to_event_id = None
    if not is_reaction and relates_to and "m.in_reply_to" in relates_to:
        reply_to_event_id = relates_to["m.in_reply_to"].get("event_id")
        if reply_to_event_id:
            is_reply = True
            facade.logger.debug(
                f"Processing Matrix reply to event: {reply_to_event_id}"
            )

    if is_reaction and interactions["reactions"]:
        if (
            meshnet_name
            and meshnet_name != local_meshnet_name
            and meshtastic_replyId
            and isinstance(event, RoomMessageEmote)
        ):
            facade.logger.info(f"Relaying reaction from remote meshnet: {meshnet_name}")

            short_meshnet_name = meshnet_name[: facade.MESHNET_NAME_ABBREVIATION_LENGTH]

            if not shortname:
                shortname = (
                    longname[: facade.SHORTNAME_FALLBACK_LENGTH] if longname else "???"
                )

            meshtastic_text_db = event.source["content"].get("meshtastic_text", "")
            meshtastic_text_db = facade.strip_quoted_lines(meshtastic_text_db)
            meshtastic_text_db = meshtastic_text_db.replace("\n", " ").replace(
                "\r", " "
            )

            abbreviated_text = (
                meshtastic_text_db[: facade.MESSAGE_PREVIEW_LENGTH] + "..."
                if len(meshtastic_text_db) > facade.MESSAGE_PREVIEW_LENGTH
                else meshtastic_text_db
            )

            reaction_message = f'{shortname}/{short_meshnet_name} reacted {reaction_emoji} to "{abbreviated_text}"'

            (
                meshtastic_interface,
                meshtastic_channel,
            ) = await facade._get_meshtastic_interface_and_channel(
                room_config, "relay reaction"
            )
            if not meshtastic_interface:
                return

            if facade.get_meshtastic_config_value(
                facade.config,
                "broadcast_enabled",
                facade.DEFAULT_BROADCAST_ENABLED,
                required=False,
            ):
                meshtastic_logger.info(
                    f"Relaying reaction from remote meshnet {meshnet_name} to radio broadcast"
                )
                facade.logger.debug(
                    f"Sending reaction to Meshtastic with meshnet={local_meshnet_name}: {reaction_message}"
                )
                success = facade.queue_message(
                    meshtastic_interface.sendText,
                    text=reaction_message,
                    channelIndex=meshtastic_channel,
                    description=f"Remote reaction from {meshnet_name}",
                )

                if success:
                    facade.logger.debug(
                        f"Queued remote reaction to Meshtastic: {reaction_message}"
                    )
                else:
                    facade.logger.error("Failed to relay remote reaction to Meshtastic")
                    return
            return

        if original_matrix_event_id:
            orig = await asyncio.to_thread(
                facade.get_message_map_by_matrix_event_id, original_matrix_event_id
            )
            if not orig:
                facade.logger.debug(
                    "Original message for reaction not found in DB. Possibly a reaction-to-reaction scenario. Not forwarding."
                )
                return

            (
                _meshtastic_id,
                _matrix_room_id,
                meshtastic_text_db,
                _meshtastic_meshnet_db,
            ) = orig
            full_display_name = await facade.get_user_display_name(room, event)

            prefix = facade.get_meshtastic_prefix(facade.config, full_display_name)

            meshtastic_text_db = facade.strip_quoted_lines(meshtastic_text_db)
            meshtastic_text_db = meshtastic_text_db.replace("\n", " ").replace(
                "\r", " "
            )

            abbreviated_text = (
                meshtastic_text_db[: facade.MESSAGE_PREVIEW_LENGTH] + "..."
                if len(meshtastic_text_db) > facade.MESSAGE_PREVIEW_LENGTH
                else meshtastic_text_db
            )

            reaction_message = (
                f'{prefix}reacted {reaction_emoji} to "{abbreviated_text}"'
            )
            (
                meshtastic_interface,
                meshtastic_channel,
            ) = await facade._get_meshtastic_interface_and_channel(
                room_config, "relay reaction"
            )
            if not meshtastic_interface:
                return

            if facade.get_meshtastic_config_value(
                facade.config,
                "broadcast_enabled",
                facade.DEFAULT_BROADCAST_ENABLED,
                required=False,
            ):
                meshtastic_logger.info(
                    f"Relaying reaction from {full_display_name} to radio broadcast"
                )
                facade.logger.debug(
                    f"Sending reaction to Meshtastic with meshnet={local_meshnet_name}: {reaction_message}"
                )
                success = facade.queue_message(
                    meshtastic_interface.sendText,
                    text=reaction_message,
                    channelIndex=meshtastic_channel,
                    description=f"Local reaction from {full_display_name}",
                )

                if success:
                    facade.logger.debug(
                        f"Queued local reaction to Meshtastic: {reaction_message}"
                    )
                else:
                    facade.logger.error("Failed to relay local reaction to Meshtastic")
                    return
            return

    if is_reply and reply_to_event_id and interactions["replies"]:
        reply_handled = await facade.handle_matrix_reply(
            room,
            event,
            reply_to_event_id,
            text,
            room_config,
            storage_enabled,
            local_meshnet_name,
            facade.config,
            mesh_text_override=mesh_text_override,
            longname=longname,
            shortname=shortname,
            meshnet_name=meshnet_name,
        )
        if reply_handled:
            return

    if longname and meshnet_name:
        full_display_name = f"{longname}/{meshnet_name}"

        if meshnet_name != local_meshnet_name:
            facade.logger.info(
                f"Processing message from remote meshnet: {meshnet_name}"
            )
            short_meshnet_name = meshnet_name[: facade.MESHNET_NAME_ABBREVIATION_LENGTH]
            if shortname is None:
                shortname = (
                    longname[: facade.SHORTNAME_FALLBACK_LENGTH] if longname else "???"
                )
            if mesh_text_override:
                text = mesh_text_override
            original_prefix = facade.get_matrix_prefix(
                facade.config, longname, shortname, meshnet_name
            )
            if original_prefix and text.startswith(original_prefix):
                text = text[len(original_prefix) :]
                facade.logger.debug(
                    f"Removed original prefix '{original_prefix}' from remote meshnet message"
                )
            if not text and mesh_text_override:
                text = mesh_text_override
            text = facade.truncate_message(text)
            prefix = facade.get_matrix_prefix(
                facade.config, longname, shortname, short_meshnet_name
            )
            full_message = f"{prefix}{text}"
            if not text:
                facade.logger.warning(
                    "Remote meshnet message from %s had empty text after formatting; skipping relay",
                    meshnet_name,
                )
                return
        else:
            return
    else:
        full_display_name = await facade.get_user_display_name(room, event)
        prefix = facade.get_meshtastic_prefix(
            facade.config, full_display_name, event.sender
        )
        facade.logger.debug(
            f"Processing matrix message from [{full_display_name}]: {text}"
        )
        full_message = f"{prefix}{text}"
        full_message = facade.truncate_message(full_message)

    portnum = event.source["content"].get("meshtastic_portnum")
    if isinstance(portnum, str):
        if portnum.isdigit():
            try:
                portnum = int(portnum)
            except ValueError:
                pass
        elif portnum == facade.DETECTION_SENSOR_APP:
            portnum = facade.PORTNUM_DETECTION_SENSOR_APP

    from mmrelay.plugin_loader import load_plugins

    plugins = load_plugins()

    found_matching_plugin = False
    for plugin in plugins:
        if not found_matching_plugin:
            try:
                handler_result = plugin.handle_room_message(room, event, text)
                if inspect.isawaitable(handler_result):
                    found_matching_plugin = await handler_result
                else:
                    found_matching_plugin = bool(handler_result)

                if found_matching_plugin:
                    facade.logger.info(
                        f"Processed command with plugin: {plugin.plugin_name} from {event.sender}"
                    )
            except Exception as exc:  # noqa: BLE001 - broad catch for plugin isolation
                facade.logger.error(
                    "Error processing message with plugin %s: %s",
                    plugin.plugin_name,
                    type(exc).__name__,
                )
                facade.logger.exception(
                    "Error processing message with plugin %s", plugin.plugin_name
                )

    if found_matching_plugin:
        facade.logger.debug("Message handled by plugin, not sending to mesh")
        return

    def _matches_command(plugin_obj: Any) -> bool:
        if hasattr(plugin_obj, "matches"):
            try:
                return bool(plugin_obj.matches(event))
            except Exception:  # noqa: BLE001 - broad catch for plugin isolation
                facade.logger.exception(
                    "Error checking plugin match for %s",
                    getattr(plugin_obj, "plugin_name", plugin_obj),
                )
                return False
        if hasattr(plugin_obj, "get_matrix_commands"):
            try:
                require_mention_attr = getattr(
                    plugin_obj, "get_require_bot_mention", lambda: False
                )
                require_mention = bool(
                    require_mention_attr()
                    if callable(require_mention_attr)
                    else require_mention_attr
                )
                return any(
                    facade.bot_command(cmd, event, require_mention=require_mention)
                    for cmd in plugin_obj.get_matrix_commands()
                )
            except Exception:  # noqa: BLE001 - broad catch for plugin isolation
                facade.logger.exception(
                    "Error checking plugin commands for %s",
                    getattr(plugin_obj, "plugin_name", plugin_obj),
                )
                return False

        return False

    if any(_matches_command(plugin) for plugin in plugins):
        facade.logger.debug("Message is a command, not sending to mesh")
        return

    is_detection_packet = portnum == facade.PORTNUM_DETECTION_SENSOR_APP

    if is_detection_packet:
        await facade._handle_detection_sensor_packet(
            facade.config, room_config, full_display_name, text
        )
        return

    (
        meshtastic_interface,
        meshtastic_channel,
    ) = await facade._get_meshtastic_interface_and_channel(room_config, "relay message")

    if not meshtastic_interface:
        return

    if not found_matching_plugin:
        if facade.get_meshtastic_config_value(
            facade.config,
            "broadcast_enabled",
            facade.DEFAULT_BROADCAST_ENABLED,
            required=False,
        ):
            mapping_info = None
            if storage_enabled:
                msgs_to_keep = facade._get_msgs_to_keep_config(facade.config)

                mapping_info = facade._create_mapping_info(
                    event.event_id,
                    room.room_id,
                    text,
                    local_meshnet_name,
                    msgs_to_keep,
                )

            success = facade.queue_message(
                meshtastic_interface.sendText,
                text=full_message,
                channelIndex=meshtastic_channel,
                description=f"Message from {full_display_name}",
                mapping_info=mapping_info,
            )

            if success:
                queue_size = facade.get_message_queue().get_queue_size()

                if queue_size > 1:
                    meshtastic_logger.info(
                        f"Relaying message from {full_display_name} to radio broadcast (queued: {queue_size} messages)"
                    )
                else:
                    meshtastic_logger.info(
                        f"Relaying message from {full_display_name} to radio broadcast"
                    )
            else:
                meshtastic_logger.error("Failed to relay message to Meshtastic")
                return
        else:
            facade.logger.debug(
                f"broadcast_enabled is False - not relaying message from {full_display_name} to Meshtastic"
            )


async def on_room_member(room: MatrixRoom, event: RoomMemberEvent) -> None:
    """
    Handle room member events to observe room-specific display name changes.

    This callback is registered so the Matrix client processes member state updates; no explicit action is required here because room-specific display names are available via the room state immediately after this event.
    """


async def on_invite(room: MatrixRoom, event: InviteMemberEvent) -> None:
    """
    Handle an invite targeted at the bot and join the room when it is configured in matrix_rooms.

    Attempts to join the invited room via the global matrix_client when all of the following are true: the event's state_key matches the bot's user id, the membership is "invite", and the room is present in the matrix_rooms configuration. Logs outcomes and failures; performs no return value.

    Parameters:
        room (MatrixRoom): The Matrix room associated with the invite.
        event (InviteMemberEvent): The invite event containing membership and state_key information.
    """
    if not facade.bot_user_id:
        facade.logger.warning("bot_user_id is not set, cannot process invites.")
        return

    if event.state_key != facade.bot_user_id:
        facade.logger.debug(
            f"Ignoring invite for {event.state_key} (not for bot {facade.bot_user_id})"
        )
        return

    if event.membership != "invite":
        facade.logger.debug(f"Ignoring non-invite membership event: {event.membership}")
        return

    room_id = room.room_id

    candidates = [room_id]
    canonical_alias = getattr(room, "canonical_alias", None)
    if isinstance(canonical_alias, str) and canonical_alias:
        candidates.append(canonical_alias)
    aliases = getattr(room, "aliases", None)
    if isinstance(aliases, (list, tuple)):
        candidates.extend(a for a in aliases if isinstance(a, str))

    if not any(facade._is_room_mapped(facade.matrix_rooms, c) for c in candidates):
        facade.logger.info(
            f"Room '{room_id}' is not in matrix_rooms configuration, ignoring invite"
        )
        return
    facade.logger.info(
        f"Room '{room_id}' is in matrix_rooms configuration, accepting invite"
    )

    if not facade.matrix_client:
        facade.logger.error("matrix_client is None, cannot join room")
        return

    client = cast(AsyncClient, facade.matrix_client)
    try:
        if room_id not in client.rooms:
            facade.logger.info(f"Joining mapped room '{room_id}'...")
            response = await client.join(room_id)
            joined_room_id = getattr(response, "room_id", None) if response else None
            if joined_room_id:
                facade.logger.info(f"Successfully joined room '{joined_room_id}'")
            else:
                error_details = facade._get_detailed_matrix_error_message(response)
                facade.logger.error(f"Failed to join room '{room_id}': {error_details}")
        else:
            facade.logger.debug(f"Bot is already in room '{room_id}', no action needed")
    except facade.NIO_COMM_EXCEPTIONS:
        facade.logger.exception(f"Error joining room '{room_id}'")
    except Exception:
        facade.logger.exception(f"Unexpected error joining room '{room_id}'")
