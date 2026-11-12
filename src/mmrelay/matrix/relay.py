"""Matrix relay and message send helpers.

Extracted from matrix_utils.py — relay path with retry logic and
exponential backoff.
"""

import asyncio
import secrets
from typing import Any, cast

from nio import RoomSendError

import mmrelay.matrix_utils as facade
from mmrelay.matrix.canonical_outbound import render_matrix_relay_content

__all__ = [
    "_get_e2ee_error_message",
    "_retry_backoff_delay",
    "_send_matrix_message_with_retry",
    "matrix_relay",
]


async def _get_e2ee_error_message() -> str:
    """
    Provide a short, user-facing explanation for why End-to-End Encryption (E2EE) is not enabled.

    Maps the unified E2EE status to a concise, human-readable message suitable for logging or UI display.

    Returns:
        str: A short explanation of the current E2EE problem, or an empty string if no specific issue is detected.
    """
    e2ee_status = await asyncio.to_thread(
        facade.get_e2ee_status, facade.config or {}, facade.config_module.config_path
    )

    return cast(str, facade.get_e2ee_error_message(dict(e2ee_status)))


def _retry_backoff_delay(
    attempt_index: int,
    base_delay: float,
    max_delay: float,
) -> float:
    """
    Compute the exponential backoff delay for a retry attempt, capped at a maximum.

    Parameters:
        attempt_index (int): Zero-based index of the retry attempt (0 yields base_delay).
        base_delay (float): Initial delay in seconds used as the multiplier base.
        max_delay (float): Upper bound in seconds for the returned delay.

    Returns:
        float: Delay in seconds to wait before the next retry; equals base_delay * 2**attempt_index capped at max_delay.
    """
    return min(base_delay * (2.0**attempt_index), max_delay)


async def _send_matrix_message_with_retry(
    matrix_client: Any,
    room_id: str,
    content: dict[str, Any],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    transaction_id: str | None = None,
) -> Any:
    """
    Send a message to a Matrix room, retrying on transient failures with exponential backoff.

    This function will not send to an encrypted room if the client has E2EE disabled; in that case it returns `None`. It retries on transient errors such as timeouts and network/transport exceptions.

    Parameters:
        matrix_client: The Matrix AsyncClient instance used to send the message.
        room_id: The Matrix room ID to send the message to.
        content: The message content dictionary to pass to the Matrix `room_send` API.
        max_retries: Maximum number of retry attempts (default 3).
        base_delay: Initial backoff delay in seconds (used to compute exponential backoff).
        max_delay: Maximum backoff delay in seconds.
        transaction_id: Optional Matrix transaction ID to reuse across retries for idempotent send semantics.

    Returns:
        The response object returned by the client's send call on success, `None` if sending was blocked due to E2EE being disabled for an encrypted room or if all retries are exhausted.
    """
    rng = secrets.SystemRandom()
    stable_transaction_id = transaction_id or f"mmrelay-{secrets.token_hex(16)}"

    for attempt in range(max_retries + 1):
        try:
            room = (
                matrix_client.rooms.get(room_id)
                if matrix_client and hasattr(matrix_client, "rooms")
                else None
            )

            if (
                room
                and getattr(room, "encrypted", False)
                and not getattr(matrix_client, "e2ee_enabled", False)
            ):
                room_name = getattr(room, "display_name", room_id)
                error_message = await _get_e2ee_error_message()
                facade.logger.error(
                    f"BLOCKED: Cannot send message to encrypted room '{room_name}' ({room_id})"
                )
                facade.logger.error(f"Reason: {error_message}")
                facade.logger.info(
                    "Tip: Run 'mmrelay config check' to validate your E2EE setup"
                )
                return None

            response = await asyncio.wait_for(
                matrix_client.room_send(
                    room_id=room_id,
                    message_type=facade.MATRIX_EVENT_TYPE_ROOM_MESSAGE,
                    content=content,
                    tx_id=stable_transaction_id,
                    ignore_unverified_devices=True,
                ),
                timeout=facade.MATRIX_ROOM_SEND_TIMEOUT,
            )
        except asyncio.TimeoutError:
            if attempt < max_retries:
                delay = _retry_backoff_delay(attempt, base_delay, max_delay)
                jitter = rng.uniform(0, delay * 0.1)
                total_delay = delay + jitter
                facade.logger.warning(
                    f"Timeout sending to Matrix room {room_id} (attempt {attempt + 1}/{max_retries + 1}), "
                    f"retrying in {total_delay:.1f}s..."
                )
                await asyncio.sleep(total_delay)
            else:
                facade.logger.exception(
                    f"Timeout sending message to Matrix room {room_id} after {max_retries + 1} attempts"
                )

        except facade.NIO_COMM_EXCEPTIONS as e:
            if attempt < max_retries:
                delay = _retry_backoff_delay(attempt, base_delay, max_delay)
                jitter = rng.uniform(0, delay * 0.1)
                total_delay = delay + jitter
                facade.logger.warning(
                    f"Network error sending to Matrix room {room_id} (attempt {attempt + 1}/{max_retries + 1}): {e}, "
                    f"retrying in {total_delay:.1f}s..."
                )
                await asyncio.sleep(total_delay)
            else:
                facade.logger.exception(
                    f"Error sending message to Matrix room {room_id} after {max_retries + 1} attempts"
                )
        else:
            if isinstance(response, RoomSendError):
                if attempt < max_retries:
                    delay = _retry_backoff_delay(attempt, base_delay, max_delay)
                    jitter = rng.uniform(0, delay * 0.1)
                    total_delay = delay + jitter
                    facade.logger.warning(
                        "API error sending to Matrix room %s (attempt %d/%d): %s, "
                        "retrying in %.1fs...",
                        room_id,
                        attempt + 1,
                        max_retries + 1,
                        getattr(response, "message", response),
                        total_delay,
                    )
                    await asyncio.sleep(total_delay)
                else:
                    facade.logger.error(
                        "API error sending to Matrix room %s after %d attempts: %s",
                        room_id,
                        max_retries + 1,
                        getattr(response, "message", response),
                    )
            else:
                return response

    return None


async def matrix_relay(
    room_id: str,
    message: str,
    longname: str,
    shortname: str,
    meshnet_name: str,
    portnum: int,
    meshtastic_id: int | None = None,
    meshtastic_replyId: int | None = None,
    meshtastic_text: str | None = None,
    emote: bool = False,
    emoji: bool = False,
    reply_to_event_id: str | None = None,
    reaction_key: str | None = None,
    source_node_id: str | None = None,
    source_channel_id: str | None = None,
) -> None:
    """
    Relay a Meshtastic-originated message into a Matrix room and optionally persist a Meshtastic↔Matrix mapping.

    Formats the provided Meshtastic text for Matrix (plain and HTML/quoted forms as appropriate), sends it to the specified Matrix room with the chosen msgtype, and when message storage is enabled, records a mapping from the Meshtastic message to the created Matrix event to support cross-network replies and reactions. The function respects room encryption/E2EE constraints and logs send/storage failures without raising.

    Parameters:
        room_id (str): Matrix room ID or alias to send the message into.
        message (str): Text content from Meshtastic to relay.
        longname (str): Sender long display name from Meshtastic for attribution/metadata.
        shortname (str): Sender short display name from Meshtastic for metadata.
        meshnet_name (str): Meshnet name for the incoming message; if empty, the configured local meshnet is used.
        portnum (int): Meshtastic application/port number for the message.
        meshtastic_id (int | None): Optional Meshtastic message identifier; used to persist a mapping when storage is enabled.
        meshtastic_replyId (int | None): Optional Meshtastic message ID that this message replies to; included as metadata.
        meshtastic_text (str | None): Optional Meshtastic-origin text to store with the mapping; if omitted the relayed `message` is used.
        emote (bool): If True, send as `m.emote` instead of `m.text`.
        emoji (bool): If True, include an emoji flag in the outbound metadata for downstream handling.
        reaction_key (str | None): Exact emoji for Meshtastic reactions; never inferred from the display message.
        source_node_id (str | None): Native Meshtastic sender node ID for canonical provenance. Optional for existing callers without a packet context.
        reply_to_event_id (str | None): Optional Matrix event_id to reply to; if provided and the original mapping is resolvable, the outgoing event includes an `m.in_reply_to` relation and a quoted formatted body.
    """
    facade.logger.debug(
        f"matrix_relay: config is {'available' if facade.config else 'None'}"
    )

    matrix_client = None
    max_init_retries = 3
    init_retry_delay = 2.0

    for init_attempt in range(max_init_retries):
        try:
            matrix_client = await facade.connect_matrix()
        except (
            facade.MissingMatrixRoomsError,
            facade.MatrixSyncTimeoutError,
            facade.MatrixSyncFailedError,
            facade.MatrixSyncFailedDetailsError,
        ) as exc:
            if init_attempt < max_init_retries - 1:
                facade.logger.warning(
                    f"Matrix client initialization failed (attempt {init_attempt + 1}/{max_init_retries}): {exc}, "
                    f"retrying in {init_retry_delay}s..."
                )
                await asyncio.sleep(init_retry_delay)
            else:
                facade.logger.exception(
                    "Matrix client initialization failed after %s attempts. "
                    "Message to room %s may be lost.",
                    max_init_retries,
                    room_id,
                )
                return
            continue
        except OSError:
            facade.logger.exception(
                "Unexpected OS/network error during Matrix client initialization"
            )
            raise

        if matrix_client is not None:
            break

        if init_attempt < max_init_retries - 1:
            facade.logger.warning(
                f"Matrix client initialization returned None (attempt {init_attempt + 1}/{max_init_retries}), "
                f"retrying in {init_retry_delay}s..."
            )
            await asyncio.sleep(init_retry_delay)
        else:
            facade.logger.error(
                f"Matrix client initialization failed after {max_init_retries} attempts. "
                f"Message to room {room_id} may be lost."
            )
            return

    if matrix_client is None:
        facade.logger.error("Matrix client is None. Cannot send message.")
        return

    if facade.config is None:
        facade.logger.error(
            "No configuration available. Cannot relay message to Matrix."
        )
        return

    interactions = facade.get_interaction_settings(facade.config)
    storage_enabled = facade.message_storage_enabled(interactions)
    msgs_to_keep = facade._get_msgs_to_keep_config(facade.config)

    try:
        if room_id not in matrix_client.rooms:
            await facade.join_matrix_room(matrix_client, room_id)

        relay_meshnet_name = meshnet_name or facade.get_meshtastic_config_value(
            facade.config, "meshnet_name", ""
        )

        reply_preview: tuple[str, str | None] | None = None
        if reply_to_event_id:
            try:
                orig = await asyncio.to_thread(
                    facade.get_message_map_by_matrix_event_id, reply_to_event_id
                )
                if orig:
                    _, mapped_room_id, original_text, original_meshnet = orig
                    if mapped_room_id != room_id:
                        facade.logger.warning(
                            "Ignoring Matrix reply target %s mapped to a different room",
                            reply_to_event_id,
                        )
                        reply_to_event_id = None
                    else:
                        reply_preview = (original_text or "", original_meshnet)
                else:
                    facade.logger.warning(
                        "Could not find original message for reply_to_event_id: %s",
                        reply_to_event_id,
                    )
            except Exception:  # noqa: BLE001 - reply context is best-effort
                facade.logger.exception("Error looking up Matrix reply context")

        content = await render_matrix_relay_content(
            room_id=room_id,
            message=message,
            longname=longname,
            shortname=shortname,
            meshnet_name=relay_meshnet_name,
            portnum=portnum,
            meshtastic_id=meshtastic_id,
            meshtastic_replyId=meshtastic_replyId,
            meshtastic_text=meshtastic_text,
            emote=emote,
            emoji=emoji,
            reaction_key=reaction_key,
            source_node_id=source_node_id,
            source_channel_id=source_channel_id,
            reply_to_event_id=reply_to_event_id,
            reply_preview=reply_preview,
            bot_user_id=getattr(matrix_client, "user_id", None),
        )

        try:
            room = (
                matrix_client.rooms.get(room_id)
                if matrix_client and hasattr(matrix_client, "rooms")
                else None
            )

            if room:
                encrypted_status = getattr(room, "encrypted", "unknown")
                facade.logger.debug(
                    f"Room {room_id} encryption status: encrypted={encrypted_status}"
                )

            response = await _send_matrix_message_with_retry(
                matrix_client=matrix_client,
                room_id=room_id,
                content=content,
                max_retries=3,
                base_delay=1.0,
            )

            if response is None:
                facade.logger.error(
                    f"Failed to send message to Matrix room {room_id} after all retry attempts. "
                    f"Message may be lost."
                )
                return

            facade.logger.info(f"Sent inbound radio message to matrix room: {room_id}")
            event_id = getattr(response, "event_id", None)
            if event_id:
                facade.logger.debug(f"Message event_id: {event_id}")

        except facade.NIO_COMM_EXCEPTIONS:
            facade.logger.exception(f"Error sending message to Matrix room {room_id}")
            return

        if (
            storage_enabled
            and meshtastic_id is not None
            and not emote
            and getattr(response, "event_id", None) is not None
        ):
            try:
                event_id = getattr(response, "event_id", None)
                if isinstance(event_id, str):
                    await facade.async_store_message_map(
                        meshtastic_id,
                        event_id,
                        room_id,
                        meshtastic_text if meshtastic_text else message,
                        meshtastic_meshnet=relay_meshnet_name,
                    )
                    facade.logger.debug(
                        f"Stored message map for meshtastic_id: {meshtastic_id}"
                    )

                if msgs_to_keep > 0:
                    await facade.async_prune_message_map(msgs_to_keep)
            except Exception as e:
                facade.logger.error(f"Error storing message map: {e}")

    except asyncio.TimeoutError:
        facade.logger.error("Timed out while waiting for Matrix response")
    except Exception:
        facade.logger.exception(f"Error sending radio message to matrix room {room_id}")
