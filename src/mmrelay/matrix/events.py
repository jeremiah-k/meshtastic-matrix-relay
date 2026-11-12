"""Matrix event handlers.

Extracted from matrix_utils.py — on_room_message, on_decryption_failure,
on_room_member, and on_invite.
"""

import asyncio
import inspect
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

from medre.adapters.matrix.relations import (
    extract_matrix_relation,
    strip_reply_fallback_body,
)
from nio.events.room_events import RoomMemberEvent

import mmrelay.matrix_utils as facade
from mmrelay.constants.formats import MATRIX_SUPPRESS_KEY
from mmrelay.matrix.canonical_ingress import canonicalize_matrix_event
from mmrelay.matrix.peer_context import (
    load_peer_relays,
    radio_packet_id,
    trusted_peer_metadata,
)
from mmrelay.meshtastic.reaction_preview import reaction_preview

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


def _plugins_owning_room(candidates: list[str]) -> list[Any]:
    """
    Return active plugins that opted in to unmapped rooms and own one of the given room IDs.

    Parameters:
        candidates (list[str]): Canonical room IDs to check against each
            plugin's declared get_unmapped_room_ids().

    Returns:
        list: Plugins whose declared room IDs include one of the candidates.
    """
    from mmrelay.plugin_loader import load_plugins

    owners: list[Any] = []
    for plugin in load_plugins():
        if not getattr(plugin, "handles_unmapped_rooms", False):
            continue
        try:
            room_ids = plugin.get_unmapped_room_ids()
            if not isinstance(room_ids, (list, tuple)):
                continue
            # Only exact room IDs from the declared sequence grant ownership.
            declared = {room_id for room_id in room_ids if isinstance(room_id, str)}
            owns_room = any(candidate in declared for candidate in candidates)
        except Exception:  # noqa: BLE001 - broad catch for plugin isolation
            facade.logger.exception(
                "Error checking unmapped rooms for %s",
                getattr(plugin, "plugin_name", plugin),
            )
            continue
        if owns_room:
            owners.append(plugin)
    return owners


async def _dispatch_unmapped_room_message(
    room: MatrixRoom,
    event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
) -> None:
    """
    Offer a message from a room outside matrix_rooms to the plugin that owns it.

    Events in unmapped rooms are never relayed to Meshtastic; this dispatch
    exists so plugin-owned rooms (such as a dedicated admin room) can drive
    plugins without participating in the mesh relay. Reaction events and
    suppressed events are ignored.

    Parameters:
        room (MatrixRoom): The Matrix room the event was received in.
        event (RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote): The received room event.
    """
    if isinstance(event, ReactionEvent):
        return
    if event.source["content"].get(MATRIX_SUPPRESS_KEY):
        return
    text = getattr(event, "body", "") or event.source["content"].get("body", "")
    if not isinstance(text, str) or not text.strip():
        return
    text = text.strip()

    for plugin in _plugins_owning_room([room.room_id]):
        try:
            handler_result = plugin.handle_room_message(room, event, text)
            if inspect.isawaitable(handler_result):
                claimed = await handler_result
            else:
                claimed = bool(handler_result)
            if claimed:
                facade.logger.info(
                    f"Processed command with plugin: {plugin.plugin_name} from {event.sender}"
                )
                break
        except Exception as exc:  # noqa: BLE001 - broad catch for plugin isolation
            facade.logger.error(
                "Error processing message with plugin %s: %s",
                plugin.plugin_name,
                type(exc).__name__,
            )
            facade.logger.exception(
                "Error processing message with plugin %s", plugin.plugin_name
            )


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
    rooms: Any = facade.matrix_rooms
    if isinstance(rooms, dict):
        direct = rooms.get(room.room_id)
        if isinstance(direct, dict):
            room_config = direct
        else:
            for key, room_conf in rooms.items():
                if key == room.room_id or (
                    isinstance(room_conf, dict) and room_conf.get("id") == room.room_id
                ):
                    room_config = room_conf if isinstance(room_conf, dict) else None
                    break
    else:
        for room_conf in rooms or []:
            if isinstance(room_conf, dict) and room_conf.get("id") == room.room_id:
                room_config = room_conf
                break

    if not room_config:
        await _dispatch_unmapped_room_message(room, event)
        return

    if facade.config is None:
        facade.logger.error("No configuration available for Matrix message processing.")
        return
    try:
        peers = load_peer_relays(facade.config)
    except ValueError as exc:
        facade.logger.error("Invalid peer relay configuration: %s", exc)
        return
    interactions = facade.get_interaction_settings(facade.config)
    storage_enabled = facade.message_storage_enabled(interactions)
    content = event.source.get("content", {})
    if content.get(MATRIX_SUPPRESS_KEY):
        return
    local_meshnet_name = facade.get_meshtastic_config_value(
        facade.config, "meshnet_name", ""
    )
    canonical_event = None
    try:
        canonical_event = canonicalize_matrix_event(
            room, event, reaction=isinstance(event, ReactionEvent)
        )
    except Exception:  # noqa: BLE001 - preserve authored messages on codec failure
        facade.logger.exception(
            "Failed to canonicalize Matrix event; using native text"
        )

    # The native parser is also the failure path: flat peer fields are never read.
    native_relation = extract_matrix_relation(event.source)
    body = getattr(event, "body", None)
    text = body if isinstance(body, str) else content.get("body", "")
    text = text if isinstance(text, str) else ""
    if native_relation is not None and native_relation.has_reply_fallback:
        text = strip_reply_fallback_body(text)
    if canonical_event is not None:
        canonical_body = canonical_event.payload.get("body")
        if isinstance(canonical_body, str):
            text = canonical_body
    text = text.strip()
    peer = (
        trusted_peer_metadata(canonical_event, event.sender, facade.bot_user_id, peers)
        if canonical_event is not None
        else None
    )
    longname = (peer.longname or peer.shortname or peer.sender_id) if peer else None
    shortname = peer.shortname if peer else None
    meshnet_name = peer.meshnet if peer else None
    portnum = peer.portnum if peer else None
    if peer is not None and meshnet_name and meshnet_name == local_meshnet_name:
        return

    is_reaction = isinstance(event, ReactionEvent)
    reaction_emoji = None
    original_matrix_event_id = None
    is_reply = False
    reply_to_event_id = None
    radio_reply_id = None
    peer_reaction = False
    if native_relation is not None:
        if native_relation.kind == "reaction":
            is_reaction = True
            reaction_emoji = native_relation.key
            original_matrix_event_id = native_relation.target_event_id
        else:
            reply_to_event_id = native_relation.explicit_reply_target
            is_reply = reply_to_event_id is not None
    # Peer hints supplement missing native relations; they cannot replace a
    # Matrix user's explicit reply, annotation, or thread fallback semantics.
    if peer is not None and peer.relation is not None and native_relation is None:
        relation = peer.relation
        target = relation.target_native_ref
        if target is None:
            facade.logger.debug("Ignoring peer relation without a native target")
            return
        # A Matrix-native hint target ($-prefixed event id in this room) is
        # recognized regardless of the adapter id the peer namespaced it
        # under: trusted relays emit their own adapter id (e.g. MEDRE's
        # "medre"), so adapter equality cannot be required. Resolution still
        # happens below against THIS relay's message map, so a foreign event
        # id simply never matches.
        matrix_native_target = (
            target.native_channel_id == room.room_id
            and target.native_message_id.startswith("$")
        )
        radio_target = (
            target.adapter == peer.source_adapter
            and target.native_channel_id == peer.source_channel
            and meshnet_name
            and meshnet_name != local_meshnet_name
        )
        if relation.relation_type == "reaction" and (
            matrix_native_target or radio_target
        ):
            candidate = (
                radio_packet_id(target.native_message_id) if radio_target else None
            )
            if matrix_native_target or candidate is not None:
                is_reaction = True
                peer_reaction = True
                reaction_emoji = relation.key
                original_matrix_event_id = (
                    target.native_message_id if matrix_native_target else None
                )
                radio_reply_id = candidate
        elif relation.relation_type == "reply" and matrix_native_target:
            is_reply = True
            reply_to_event_id = target.native_message_id
    if is_reaction and (
        not isinstance(reaction_emoji, str) or not reaction_emoji.strip()
    ):
        return
    if is_reaction and not interactions["reactions"]:
        return
    if peer_reaction and not text:
        facade.logger.debug("Ignoring peer reaction without authored text")
        return
    if not is_reaction and not text:
        return

    if is_reaction and interactions["reactions"]:
        if peer_reaction and radio_reply_id is not None:
            # The target belongs to the remote radio namespace. Never bind it to
            # an unrelated local packet ID; retain the visible authored message.
            interface, channel = await facade._get_meshtastic_interface_and_channel(
                room_config, "relay reaction"
            )
            if (
                interface
                and text
                and facade.get_meshtastic_config_value(
                    facade.config,
                    "broadcast_enabled",
                    facade.DEFAULT_BROADCAST_ENABLED,
                    required=False,
                )
            ):
                # Peer reaction bodies are already authored for radio display.
                # Do not prepend a locally inferred prefix: the originating
                # relay may use a different prefix format, and the body may
                # deliberately be unprefixed.
                facade.queue_message(
                    interface.sendText,
                    text=facade.truncate_message(text),
                    channelIndex=channel,
                    description=f"Remote reaction from {meshnet_name}",
                )
            return

        if original_matrix_event_id:
            orig = await asyncio.to_thread(
                facade.get_message_map_by_matrix_event_id, original_matrix_event_id
            )
            if not orig or orig[1] != room.room_id:
                facade.logger.debug("Reaction target is not mapped in this Matrix room")
                return

            (
                meshtastic_id_raw,
                _matrix_room_id,
                meshtastic_text_db,
                _meshtastic_meshnet_db,
            ) = orig

            meshtastic_reply_id = radio_packet_id(meshtastic_id_raw)
            if meshtastic_reply_id is None:
                facade.logger.debug("Ignoring reaction with an invalid radio target")
                return

            full_display_name = await facade.get_user_display_name(room, event)

            prefix = facade.get_meshtastic_prefix(facade.config, full_display_name)

            abbreviated_text = reaction_preview(meshtastic_text_db or "")

            reaction_message = (
                text
                if peer_reaction
                else f'{prefix}reacted {reaction_emoji} to "{abbreviated_text}"'
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
                    f"Relaying reaction from {full_display_name} to radio broadcast as reply"
                )
                facade.logger.debug(
                    f"Sending reaction reply to Meshtastic message {meshtastic_reply_id}: {reaction_message}"
                )
                success = facade.queue_message(
                    facade.send_text_reply,
                    interface=meshtastic_interface,
                    text=facade.truncate_message(reaction_message),
                    reply_id=meshtastic_reply_id,
                    channelIndex=meshtastic_channel,
                    description=f"Local reaction from {full_display_name} (reply to {meshtastic_reply_id})",
                )

                if success:
                    facade.logger.debug(
                        f"Queued local reaction to Meshtastic: {reaction_message}"
                    )
                else:
                    facade.logger.error("Failed to relay local reaction to Meshtastic")
                    return
            return

        # A native annotation without an eligible target must never fall
        # through to the ordinary message path with an empty body.
        facade.logger.debug("Ignoring Matrix reaction without a resolved target")
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
            longname=longname,
            shortname=shortname,
            meshnet_name=meshnet_name,
            text_is_canonical=True,
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
            original_prefix = facade.get_matrix_prefix(
                facade.config, longname, shortname, meshnet_name
            )
            if original_prefix and text.startswith(original_prefix):
                text = text[len(original_prefix) :]
                facade.logger.debug(
                    f"Removed original prefix '{original_prefix}' from remote meshnet message"
                )
            text = facade.truncate_message(text)
            prefix = facade.get_matrix_prefix(
                facade.config, longname, shortname, short_meshnet_name
            )
            full_message = facade.truncate_message(f"{prefix}{text}")
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
                    text_is_canonical=True,
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
    Handle an invite targeted at the bot and join the room when it is configured in matrix_rooms or owned by a plugin.

    Attempts to join the invited room via the global matrix_client when all of the following are true: the event's state_key matches the bot's user id, the membership is "invite", and the room is present in the matrix_rooms configuration or declared by an active plugin via handles_unmapped_rooms/get_unmapped_room_ids(). Logs outcomes and failures; performs no return value.

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

    is_mapped = any(facade._is_room_mapped(facade.matrix_rooms, c) for c in candidates)
    # Plugin-owned unmapped rooms must match the immutable room ID. Invite
    # aliases are mutable room state and cannot grant plugin-room ownership.
    if not is_mapped and not _plugins_owning_room([room_id]):
        facade.logger.info(
            f"Room '{room_id}' is not in matrix_rooms configuration, ignoring invite"
        )
        return
    if is_mapped:
        facade.logger.info(
            f"Room '{room_id}' is in matrix_rooms configuration, accepting invite"
        )
    else:
        facade.logger.info(
            f"Room '{room_id}' is a configured plugin room, accepting invite"
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
    except Exception:  # noqa: BLE001 - broad catch for invite-join resilience
        facade.logger.exception(f"Unexpected error joining room '{room_id}'")
