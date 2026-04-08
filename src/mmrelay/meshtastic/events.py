import threading
from typing import Any, Iterable

from meshtastic.mesh_interface import BROADCAST_NUM

import mmrelay.meshtastic_utils as facade
from mmrelay.constants.config import (
    CONFIG_KEY_MESHNET_NAME,
    CONFIG_SECTION_MESHTASTIC,
)
from mmrelay.constants.formats import (
    DETECTION_SENSOR_APP,
    EMOJI_FLAG_VALUE,
    TEXT_MESSAGE_APP,
)
from mmrelay.constants.messages import (
    DEFAULT_CHANNEL_VALUE,
    PORTNUM_DETECTION_SENSOR_APP,
    PORTNUM_TEXT_MESSAGE_APP,
)
from mmrelay.meshtastic.packet_routing import (
    PacketAction,
    _get_portnum_name,
    _is_text_message_portnum,
    classify_packet,
)

__all__ = [
    "_schedule_startup_drain_deadline_cleanup",
    "on_lost_meshtastic_connection",
    "on_meshtastic_message",
    "reconnect",
]


def _get_iterable_matrix_rooms() -> Iterable[Any]:
    return (
        facade.matrix_rooms.values()
        if isinstance(facade.matrix_rooms, dict)
        else (facade.matrix_rooms or ())
    )


def _derive_disconnect_detection_source(
    interface: Any, detection_source: str, topic: Any
) -> str:
    if detection_source != "unknown":
        return detection_source

    interface_source = getattr(interface, "_last_disconnect_source", None)
    if isinstance(interface_source, str) and (stripped := interface_source.strip()):
        res = stripped[4:].strip() if stripped.startswith("ble.") else stripped
        if res:
            facade.logger.debug(
                "Using interface-provided detection source: %s",
                res,
            )
            return res

    if topic is not None and topic is not facade.pub.AUTO_TOPIC:
        name = getattr(topic, "getName", lambda: str(topic))()
        facade.logger.debug(
            "Using pubsub topic-derived detection source: %s",
            name,
        )
        return name

    facade.logger.debug(
        "_last_disconnect_source unavailable; using default detection source"
    )
    return "meshtastic.connection.lost"


def _tear_down_meshtastic_client_for_disconnect(detection_source: str) -> None:
    if not facade.meshtastic_client:
        return

    if facade.meshtastic_client is facade.meshtastic_iface:
        facade.logger.debug("Disconnecting BLE interface due to connection loss")
        facade._disconnect_ble_interface(
            facade.meshtastic_iface,
            reason=f"connection loss: {detection_source}",
        )
        facade.meshtastic_iface = None
    else:
        try:
            facade.meshtastic_client.close()
        except OSError as e:
            if e.errno != facade.ERRNO_BAD_FILE_DESCRIPTOR:
                facade.logger.warning(f"Error closing Meshtastic client: {e}")
        except Exception as e:
            facade.logger.warning(f"Error closing Meshtastic client: {e}")


def _clear_stale_ble_future_for_reconnect(
    detection_source: str,
) -> tuple[Any, Any, str | None]:
    ble_future_to_cancel = None
    stale_executor = None
    stale_ble_address: str | None = None
    with facade._ble_executor_lock:
        stale_ble_address = facade._ble_future_address
        if facade._ble_future and not facade._ble_future.done():
            facade.logger.debug(
                "Clearing stale BLE future before reconnect (%s)",
                detection_source,
            )
            ble_future_to_cancel = facade._ble_future
            facade._ble_future = None
            if facade._ble_future_address:
                with facade._ble_timeout_lock:
                    facade._ble_timeout_counts.pop(facade._ble_future_address, None)
            facade._ble_future_address = None
            facade._ble_future_started_at = None
            facade._ble_future_timeout_secs = None
            if facade._ble_executor is not None:
                stale_executor = facade._ble_executor
                facade._ble_executor = facade.ThreadPoolExecutor(max_workers=1)
    return ble_future_to_cancel, stale_executor, stale_ble_address


def _finalize_stale_ble_cleanup(
    ble_future_to_cancel: Any,
    stale_executor: Any,
    stale_ble_address: str | None,
    detection_source: str,
) -> None:
    if ble_future_to_cancel is not None:
        if stale_ble_address:
            facade._attach_late_ble_interface_disposer(
                ble_future_to_cancel,
                stale_ble_address,
                reason=f"connection loss: {detection_source}",
            )
        ble_future_to_cancel.cancel()
    if stale_executor is not None:
        try:
            stale_executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            stale_executor.shutdown(wait=False)


def _reset_ble_degraded_state_after_disconnect(
    stale_ble_address: str | None,
) -> None:
    if stale_ble_address is not None:
        facade.reset_executor_degraded_state(ble_address=stale_ble_address)
    else:
        should_reset_all_degraded = False
        with facade._ble_executor_lock:
            if facade._ble_executor_degraded_addresses:
                should_reset_all_degraded = True
                facade.logger.debug(
                    "Resetting degraded BLE executor state during reconnect "
                    "(no stale_ble_address but degraded addresses exist)"
                )
        if should_reset_all_degraded:
            facade.reset_executor_degraded_state(reset_all=True)


def _schedule_reconnect_after_disconnect() -> None:
    if facade.event_loop and not facade.event_loop.is_closed():
        facade.reconnecting = True
        try:
            facade.reconnect_task = facade.asyncio.run_coroutine_threadsafe(
                facade.reconnect(), facade.event_loop
            )
        except RuntimeError:
            facade.reconnecting = False
            facade.logger.error(
                "Failed to schedule reconnect; event loop became unavailable"
            )
    else:
        facade.reconnecting = False
        facade.logger.error(
            "Cannot schedule reconnect because the event loop is unavailable"
        )


def _schedule_startup_drain_deadline_cleanup(startup_drain_deadline: float) -> None:
    """
    Clear and log startup drain expiry on deadline, independent of packet arrival.

    This keeps startup-drain state authoritative even when no inbound packet
    arrives immediately after the drain window closes.
    """

    def _cleanup() -> None:
        should_log_drain_end = False
        reschedule_deadline: float | None = None
        with facade._relay_rx_time_clock_skew_lock:
            if facade._relay_startup_drain_expiry_timer is timer:
                facade._relay_startup_drain_expiry_timer = None

            current_deadline = facade._relay_startup_drain_deadline_monotonic_secs
            if current_deadline != startup_drain_deadline:
                return
            if current_deadline is None:
                return
            if current_deadline > facade.time.monotonic():
                reschedule_deadline = current_deadline
            else:
                facade._relay_startup_drain_deadline_monotonic_secs = None
                should_log_drain_end = True
        if reschedule_deadline is not None:
            _schedule_startup_drain_deadline_cleanup(reschedule_deadline)
            return
        if should_log_drain_end:
            facade.logger.debug("Startup drain window has ended — accepting packets")

    delay_secs = max(0.0, startup_drain_deadline - facade.time.monotonic())
    timer = threading.Timer(delay_secs, _cleanup)
    timer.daemon = True
    previous_timer = None
    with facade._relay_rx_time_clock_skew_lock:
        previous_timer = facade._relay_startup_drain_expiry_timer
        facade._relay_startup_drain_expiry_timer = timer
    if previous_timer is not None:
        previous_timer.cancel()
    try:
        timer.start()
    except Exception as exc:  # noqa: BLE001 - best-effort timer setup
        with facade._relay_rx_time_clock_skew_lock:
            if facade._relay_startup_drain_expiry_timer is timer:
                facade._relay_startup_drain_expiry_timer = None
        facade.logger.debug(
            "Failed to schedule startup drain expiry cleanup timer",
            exc_info=exc,
        )


def on_lost_meshtastic_connection(
    interface: Any = None,
    detection_source: str = "unknown",
    topic: Any = facade.pub.AUTO_TOPIC,
) -> None:
    """
    Mark the Meshtastic connection as lost, close the current client, and start an asynchronous reconnect.

    If a shutdown is underway or a reconnect is already in progress this function returns immediately. When proceeding it sets the module-level `reconnecting` flag, attempts a best-effort close/cleanup of the current Meshtastic client/interface (with special handling for BLE interfaces), clears any in-flight BLE future state, and schedules the `reconnect()` coroutine on the global event loop.

    Parameters:
        detection_source (str): Identifier for where or how the loss was detected; if `"unknown"`, the function will prefer an interface-provided `_last_disconnect_source`, then derive a name from `topic`, and finally fall back to `"meshtastic.connection.lost"`.
        topic (Any): Optional pubsub topic object (from pypubsub); when provided and `detection_source` is `"unknown"`, the topic's name will be used to derive the detection source.
    """
    with facade.meshtastic_lock:
        if facade.shutting_down:
            facade.logger.debug("Shutdown in progress. Not attempting to reconnect.")
            return
        active_client = facade.meshtastic_client
        active_client_id = facade._relay_active_client_id
        if (
            interface is not None
            and active_client is None
            and (
                facade._callbacks_tearing_down
                or facade.subscribed_to_connection_lost
                or facade.shutting_down
            )
        ):
            facade.logger.debug(
                "Ignoring connection-lost event because no Meshtastic interface is currently active"
            )
            return
        if interface is not None and active_client is not None:
            expected_client_id = (
                active_client_id if active_client_id is not None else id(active_client)
            )
            if id(interface) != expected_client_id:
                facade.logger.debug(
                    "Ignoring connection-lost event from stale Meshtastic interface "
                    "(event_interface_id=%s active_client_id=%s)",
                    id(interface),
                    expected_client_id,
                )
                return
        if facade.reconnecting:
            facade.logger.debug(
                "Reconnection already in progress. Skipping additional reconnection attempt."
            )
            return

        detection_source = _derive_disconnect_detection_source(
            interface, detection_source, topic
        )

        facade.logger.error(f"Lost connection ({detection_source}). Reconnecting...")

        _tear_down_meshtastic_client_for_disconnect(detection_source)
        facade.meshtastic_client = None
        facade._relay_active_client_id = None

        ble_future, stale_exec, stale_addr = _clear_stale_ble_future_for_reconnect(
            detection_source
        )
        _finalize_stale_ble_cleanup(
            ble_future, stale_exec, stale_addr, detection_source
        )
        _reset_ble_degraded_state_after_disconnect(stale_addr)
        _schedule_reconnect_after_disconnect()


async def reconnect() -> None:
    """
    Re-establish the Meshtastic connection using exponential backoff.

    Retries connect_meshtastic(force_connect=True) until a connection is obtained, the application begins shutting down, or the task is cancelled. Starts with DEFAULT_BACKOFF_TIME and doubles the wait after each failed attempt, capped at 300 seconds. Stops promptly on cancellation or when shutting_down is set, and ensures the module-level `reconnecting` flag is cleared before returning.
    """
    backoff_time = facade.DEFAULT_BACKOFF_TIME
    try:
        while not facade.shutting_down:
            try:
                facade.logger.info(
                    f"Reconnection attempt starting in {backoff_time} seconds..."
                )

                # Show reconnection countdown with Rich (if not in a service)
                if not facade.is_running_as_service():
                    try:
                        from rich.progress import (
                            BarColumn,
                            Progress,
                            TextColumn,
                            TimeRemainingColumn,
                        )
                    except ImportError:
                        facade.logger.debug(
                            "Rich not available; falling back to simple reconnection delay"
                        )
                        await facade.asyncio.sleep(backoff_time)
                    else:
                        with Progress(
                            TextColumn("[cyan]Meshtastic: Reconnecting in"),
                            BarColumn(),
                            TextColumn("[cyan]{task.percentage:.0f}%"),
                            TimeRemainingColumn(),
                            transient=True,
                        ) as progress:
                            task = progress.add_task("Waiting", total=backoff_time)
                            for _ in range(backoff_time):
                                if facade.shutting_down:
                                    break
                                await facade.asyncio.sleep(1)
                                progress.update(task, advance=1)
                else:
                    await facade.asyncio.sleep(backoff_time)
                if facade.shutting_down:
                    facade.logger.debug(
                        "Shutdown in progress. Aborting reconnection attempts."
                    )
                    break
                loop = facade.asyncio.get_running_loop()
                # Pass the current config during reconnection to ensure matrix_rooms is populated
                # Using None for passed_config would skip matrix_rooms initialization
                connect_future = facade.asyncio.ensure_future(
                    loop.run_in_executor(
                        None, facade.connect_meshtastic, facade.config, True
                    )
                )
                facade.reconnect_task_future = connect_future
                connected_client = await connect_future
                if connected_client is not None:
                    facade.logger.info("Reconnected successfully.")
                    break
                if facade.shutting_down:
                    break
                facade.logger.warning(
                    "Reconnection attempt did not produce a client; backing off"
                )
                backoff_time = min(backoff_time * 2, 300)
            except Exception:
                if facade.shutting_down:
                    break
                facade.logger.exception("Reconnection attempt failed")
                backoff_time = min(backoff_time * 2, 300)  # Cap backoff at 5 minutes
    except facade.asyncio.CancelledError:
        facade.logger.info("Reconnection task was cancelled.")
    finally:
        if facade.reconnect_task_future is not None:
            if (
                facade.reconnect_task_future.done()
                and not facade.reconnect_task_future.cancelled()
            ):
                facade.reconnect_task_future = None
        facade.reconnecting = False


def on_meshtastic_message(packet: dict[str, Any], interface: Any) -> None:
    """
    Route an incoming Meshtastic packet to configured Matrix rooms or installed plugins based on runtime configuration.

    Processes the decoded packet and, depending on interaction settings and packet contents, will relay emoji reactions and replies to mapped Matrix events, dispatch ordinary text messages to Matrix rooms mapped to the packet's channel (unless the message is a direct message to the relay node or handled by a plugin), and hand non-text or unhandled packets to installed plugins with a per-plugin timeout.

    Parameters:
        packet (dict): Decoded Meshtastic packet. Expected keys include:
            - 'decoded' (dict): may contain 'text', 'replyId', 'portnum', and optional 'emoji'
            - 'fromId' or 'from' (sender id)
            - 'to' (destination id)
            - 'id' (packet id)
            - optional 'channel' (mapped channel value)
        interface: Meshtastic interface used to resolve node information and the relay node id. Must provide .myInfo.my_node_num and a .nodes mapping for sender metadata.
    """
    # Validate packet structure
    if not packet or not isinstance(packet, dict):
        facade.logger.error("Received malformed packet: packet is None or not a dict")
        return

    if facade.shutting_down:
        facade.logger.debug("Shutdown in progress. Ignoring incoming messages.")
        return

    # Read-mostly guard values; avoid meshtastic_lock here because callbacks can
    # fire synchronously while connect_meshtastic() still holds that lock.
    active_client = facade.meshtastic_client
    active_client_id = facade._relay_active_client_id
    if active_client is None:
        if active_client_id is not None:
            facade.logger.error(
                "Inconsistent relay state: active_client is None but active_client_id=%s — this should not happen",
                active_client_id,
            )
        # Runtime callbacks can still arrive briefly during reconnect/teardown
        # windows because subscriptions are process-lifetime.
        #
        # Keep direct unit-level handler invocation behavior unchanged when no
        # active session is being transitioned.
        if (
            facade._callbacks_tearing_down
            or facade.subscribed_to_messages
            or facade.reconnecting
            or facade.shutting_down
        ):
            facade.logger.debug(
                "Ignoring packet because no Meshtastic interface is currently active"
            )
            return
    else:
        expected_client_id = (
            active_client_id if active_client_id is not None else id(active_client)
        )
        if id(interface) != expected_client_id:
            facade.logger.debug(
                "Ignoring packet from stale Meshtastic interface (packet_interface_id=%s active_client_id=%s)",
                id(interface),
                expected_client_id,
            )
            return

    # Parse rxTime early so health-probe responses can calibrate packet clock skew.
    rx_time_raw = packet.get("rxTime", 0)
    try:
        rx_time = float(rx_time_raw)
    except (TypeError, ValueError):
        rx_time = 0

    is_health_probe_response = facade._claim_health_probe_response_and_maybe_calibrate(
        packet, interface, rx_time
    )
    if is_health_probe_response:
        decoded = packet.get("decoded")
        portnum = decoded.get("portnum") if isinstance(decoded, dict) else None
        facade.logger.debug(
            "[HEALTH_CHECK] Metadata probe response requestId=%s from=%s port=%s",
            facade._extract_packet_request_id(packet),
            packet.get("fromId") or packet.get("from"),
            facade._get_portnum_name(portnum, packet),
        )
        return

    now_monotonic = facade.time.monotonic()
    with facade._relay_rx_time_clock_skew_lock:
        relay_start_time = facade.RELAY_START_TIME
        startup_drain_deadline = facade._relay_startup_drain_deadline_monotonic_secs

    if startup_drain_deadline is not None:
        remaining_drain_secs = startup_drain_deadline - now_monotonic
        if remaining_drain_secs > 0:
            calibrated_during_drain = False
            if rx_time > 0:
                calibrated_during_drain = facade._seed_connect_time_skew(rx_time)
            if calibrated_during_drain and rx_time < relay_start_time:
                facade.logger.debug(
                    "Consumed startup bootstrap packet with rxTime %s to calibrate clock skew",
                    rx_time,
                )
            facade.logger.debug(
                "Dropping inbound packet during startup drain window (remaining=%.3f seconds)",
                remaining_drain_secs,
            )
            return
        should_log_drain_end = False
        with facade._relay_rx_time_clock_skew_lock:
            drain_expiry_timer = facade._relay_startup_drain_expiry_timer
            timer_still_active = False
            if drain_expiry_timer is not None:
                timer_is_alive = getattr(drain_expiry_timer, "is_alive", None)
                if callable(timer_is_alive):
                    try:
                        timer_still_active = bool(timer_is_alive())
                    except (RuntimeError, TypeError):
                        timer_still_active = False
            if (
                facade._relay_startup_drain_deadline_monotonic_secs is not None
                and facade._relay_startup_drain_deadline_monotonic_secs <= now_monotonic
                and not timer_still_active
            ):
                facade._relay_startup_drain_expiry_timer = None
                facade._relay_startup_drain_deadline_monotonic_secs = None
                should_log_drain_end = True
        if should_log_drain_end:
            facade.logger.debug("Startup drain window has ended — accepting packets")

    # Seed clock skew from the first non-health-probe packet with a valid
    # rxTime so that the cutoff works even when health checks are disabled.
    # During startup we allow one bounded bootstrap from a pre-start packet,
    # then consume that packet so backlog is not relayed.
    calibrated_from_packet = False
    if rx_time > 0:
        calibrated_from_packet = facade._seed_connect_time_skew(rx_time)

    if calibrated_from_packet and rx_time < relay_start_time:
        facade.logger.debug(
            "Consumed startup bootstrap packet with rxTime %s to calibrate clock skew",
            rx_time,
        )
        return

    # Filter out old messages (from before relay start) to prevent flooding.
    # This handles cases where the node dumps stored history upon connection.
    # When health probes calibrate packet clock skew, adjust the relay start
    # cutoff so clock offsets do not hide fresh traffic.
    with facade._relay_rx_time_clock_skew_lock:
        relay_start_time = facade.RELAY_START_TIME
        calibrated_skew = facade._relay_rx_time_clock_skew_secs
    effective_relay_start_time = relay_start_time
    if calibrated_skew is not None:
        effective_relay_start_time = relay_start_time - calibrated_skew

    if rx_time > 0 and rx_time < effective_relay_start_time:
        if calibrated_skew is None:
            facade.logger.debug(
                "Ignoring old packet with rxTime %s (older than start time %s)",
                rx_time,
                relay_start_time,
            )
        else:
            facade.logger.debug(
                "Ignoring old packet with rxTime %s (older than adjusted start time %s; raw start=%s skew=%s)",
                rx_time,
                effective_relay_start_time,
                relay_start_time,
                calibrated_skew,
            )
        return

    # Full packet logging for debugging (when enabled in config)
    # Check if full packet logging is enabled - accepts boolean True or string "true"
    debug_settings: dict[str, Any] = (
        facade.config.get("logging", {}).get("debug", {}) if facade.config else {}
    )
    full_packets_setting = debug_settings.get("full_packets")
    if full_packets_setting is True or (
        isinstance(full_packets_setting, str) and full_packets_setting.lower() == "true"
    ):
        facade.logger.debug("Full packet: %s", packet)

    # Log that we received a message (without the full packet details)
    decoded = packet.get("decoded")
    if decoded and isinstance(decoded, dict) and decoded.get("text"):
        facade.logger.info(f"Received Meshtastic message: {decoded.get('text')}")
    else:
        portnum = (
            decoded.get("portnum") if decoded and isinstance(decoded, dict) else None
        )
        portnum_name = facade._get_portnum_name(portnum, packet)
        from_id = packet.get("fromId") or packet.get("from")
        from_display = ""
        if from_id is not None:
            from_display = facade._get_node_display_name(
                from_id, interface, fallback=""
            )
        details_map = {
            "from": from_id,
            "channel": packet.get("channel"),
            "id": packet.get("id"),
        }
        details_map.update(facade._get_packet_details(decoded, packet, portnum_name))

        details = []
        if from_display:
            details.append(from_display)
        for key, value in details_map.items():
            if value is not None:
                if key == "from":
                    details.append(f"from={value}")
                elif key == "batt":
                    details.append(f"{value}")
                elif key == "voltage":
                    details.append(f"v={value}")
                elif key == "temp":
                    details.append(f"t={value}")
                elif key == "humidity":
                    details.append(f"h={value}")
                elif key == "signal":
                    details.append(f"s={value}")
                elif key == "relayed":
                    details.append(f"r={value}")
                elif key == "priority":
                    details.append(f"p={value}")
                else:
                    details.append(f"{key}={value}")

        prefix = f"[{portnum_name}] " + " ".join(details)
        facade.logger.debug(prefix)

    # Check if config is available
    if facade.config is None:
        facade.logger.error(
            "No configuration available. Cannot process Meshtastic message."
        )
        return

    # Import the configuration helpers
    from mmrelay.matrix_utils import get_interaction_settings

    # Get interaction settings
    interactions = get_interaction_settings(facade.config)

    decoded = packet.get("decoded")
    if not isinstance(decoded, dict):
        decoded = {}

    # Classify packet BEFORE any Matrix relay branches so that DROP packets
    # never reach Matrix via reply/reaction paths, and PLUGIN_ONLY packets
    # cannot leak into Matrix interaction relay.
    action = classify_packet(decoded.get("portnum"), facade.config, packet)
    if action == PacketAction.DROP:
        facade.logger.debug(
            "Packet %s classified as %s; skipping plugin and Matrix relay pipelines.",
            _get_portnum_name(decoded.get("portnum"), packet),
            PacketAction.DROP,
        )
        return

    # Filter out reactions if reactions are disabled (only for text-message portnums)
    if _is_text_message_portnum(decoded.get("portnum")):
        if (
            not interactions["reactions"]
            and decoded.get("replyId") is not None
            and "emoji" in decoded
            and decoded.get("emoji") == EMOJI_FLAG_VALUE
        ):
            facade.logger.debug(
                "Filtered out reaction packet due to reactions being disabled."
            )
            return

    from mmrelay.matrix_utils import matrix_relay

    if facade.shutting_down:
        facade.logger.debug("Shutdown in progress. Ignoring incoming messages.")
        return

    if facade.event_loop is None:
        facade.logger.error("Event loop is not set. Cannot process message.")
        return

    loop = facade.event_loop

    sender = packet.get("fromId") or packet.get("from")
    toId = packet.get("to")

    text = decoded.get("text")
    replyId = decoded.get("replyId")
    emoji_flag = "emoji" in decoded and decoded["emoji"] == EMOJI_FLAG_VALUE

    # Determine if this is a direct message to the relay node
    if not getattr(interface, "myInfo", None):
        facade.logger.warning(
            "Meshtastic interface missing myInfo; cannot determine node id"
        )
        return
    myId = interface.myInfo.my_node_num

    if toId == myId:
        is_direct_message = True
    elif toId == BROADCAST_NUM or toId is None:
        is_direct_message = False
    else:
        facade.logger.debug(
            "Ignoring message intended for node %s (not broadcast or relay).", toId
        )
        return

    meshnet_name = facade.config[CONFIG_SECTION_MESHTASTIC][CONFIG_KEY_MESHNET_NAME]

    # Reaction handling (Meshtastic -> Matrix)
    # Only for RELAY-classified TEXT_MESSAGE_APP packets.
    # Non-chat portnums (even if promoted to RELAY via config) must not
    # use Matrix interaction relay.
    if (
        action == PacketAction.RELAY
        and _is_text_message_portnum(decoded.get("portnum"))
        and replyId
        and emoji_flag
        and interactions["reactions"]
    ):
        longname = facade._get_name_safely(facade.get_longname, sender)
        shortname = facade._get_name_safely(facade.get_shortname, sender)
        orig = facade.get_message_map_by_meshtastic_id(replyId)
        if orig:
            # orig = (matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
            matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet = orig
            abbreviated_text = (
                meshtastic_text[:40] + "..."
                if len(meshtastic_text) > 40
                else meshtastic_text
            )

            # Import the matrix prefix function
            from mmrelay.matrix_utils import get_matrix_prefix

            # Get the formatted prefix for the reaction
            prefix = get_matrix_prefix(
                facade.config, longname, shortname, meshtastic_meshnet or meshnet_name
            )

            reaction_symbol = text.strip() if (text and text.strip()) else "⚠️"
            reaction_message = (
                f'\n {prefix}reacted {reaction_symbol} to "{abbreviated_text}"'
            )

            # Relay the reaction as emote to Matrix, preserving the original meshnet name
            facade._fire_and_forget(
                matrix_relay(
                    matrix_room_id,
                    reaction_message,
                    longname,
                    shortname,
                    meshtastic_meshnet or meshnet_name,
                    decoded.get("portnum", 0),
                    meshtastic_id=packet.get("id"),
                    meshtastic_replyId=replyId,
                    meshtastic_text=meshtastic_text,
                    emote=True,
                    emoji=True,
                ),
                loop=loop,
            )
            return
        else:
            # Original message not found - fall through to normal text handling
            # This can happen with:
            # - Replies to messages from before the relay started
            # - Cross-meshnet replies where original not in our DB
            # - Signed IDs that don't match (packet from another node/source)
            facade.logger.warning(
                "Original message for reaction (replyId=%s) not found in DB. "
                "Relaying as normal message instead.",
                replyId,
            )

    # Reply handling (Meshtastic -> Matrix)
    # Only for RELAY-classified TEXT_MESSAGE_APP packets.
    if (
        action == PacketAction.RELAY
        and _is_text_message_portnum(decoded.get("portnum"))
        and replyId
        and not emoji_flag
        and interactions["replies"]
    ):
        longname = facade._get_name_safely(facade.get_longname, sender)
        shortname = facade._get_name_safely(facade.get_shortname, sender)
        orig = facade.get_message_map_by_meshtastic_id(replyId)
        if orig:
            # orig = (matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
            matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet = orig

            # Import the matrix prefix function
            from mmrelay.matrix_utils import get_matrix_prefix

            # Get the formatted prefix for the reply
            prefix = get_matrix_prefix(
                facade.config, longname, shortname, meshtastic_meshnet or meshnet_name
            )
            formatted_message = f"{prefix}{text}"

            facade.logger.info(f"Relaying Meshtastic reply from {longname} to Matrix")

            # Relay the reply to Matrix with proper reply formatting
            facade._fire_and_forget(
                matrix_relay(
                    matrix_room_id,
                    formatted_message,
                    longname,
                    shortname,
                    meshtastic_meshnet or meshnet_name,
                    decoded.get("portnum", 0),
                    meshtastic_id=packet.get("id"),
                    meshtastic_replyId=replyId,
                    meshtastic_text=text,
                    reply_to_event_id=matrix_event_id,
                ),
                loop=loop,
            )
            return
        else:
            # Original message not found - fall through to normal text handling
            # This can happen with:
            # - Replies to messages from before the relay started
            # - Cross-meshnet replies where original not in our DB
            # - Signed IDs that don't match (packet from another node/source)
            facade.logger.warning(
                "Original message for reply (replyId=%s) not found in DB. "
                "Relaying as normal message instead.",
                replyId,
            )

    # Normal text messages or detection sensor messages
    if text:
        # Channel deduction and mapping are only relevant for RELAY packets.
        # PLUGIN_ONLY packets skip all channel logic and go straight to plugins.
        skip_matrix_relay = False
        channel: int | None = None
        channel_mapped = False
        matrix_rooms_configured = bool(facade.matrix_rooms)
        iterable_rooms = _get_iterable_matrix_rooms()
        if action == PacketAction.RELAY:
            channel = packet.get("channel")
            if channel is None:
                if decoded.get("portnum") in (
                    PORTNUM_TEXT_MESSAGE_APP,
                    PORTNUM_DETECTION_SENSOR_APP,
                    TEXT_MESSAGE_APP,
                    DETECTION_SENSOR_APP,
                ):
                    channel = DEFAULT_CHANNEL_VALUE
                else:
                    portnum_name = _get_portnum_name(decoded.get("portnum"), packet)
                    facade.logger.debug(
                        "Packet %s promoted to relay via config, but no channel "
                        "could be determined; plugins will run, Matrix relay skipped.",
                        portnum_name,
                    )
                    skip_matrix_relay = True

            if not skip_matrix_relay:
                try:
                    channel = int(channel)  # type: ignore[arg-type]
                except (ValueError, TypeError):
                    facade.logger.warning(
                        f"Invalid channel value {channel!r} (type: {type(channel).__name__}), "
                        f"defaulting to {DEFAULT_CHANNEL_VALUE}"
                    )
                    channel = DEFAULT_CHANNEL_VALUE

                for room in iterable_rooms:
                    if not isinstance(room, dict):
                        continue
                    room_channel = facade._normalize_room_channel(room)
                    if room_channel is None:
                        continue
                    if room_channel == channel:
                        channel_mapped = True
                        facade.logger.debug(
                            f"Channel {channel} mapped to Matrix room {room.get('id', 'unknown')}"
                        )
                        break

        # Resolve sender names (needed for both plugin delivery and Matrix relay)
        longname = facade._get_name_or_none(facade.get_longname, sender)  # type: ignore[assignment]
        if longname is None:
            facade.logger.debug(
                "Failed to get longname from database for %s, will try interface fallback",
                sender,
            )

        shortname = facade._get_name_or_none(facade.get_shortname, sender)  # type: ignore[assignment]
        if shortname is None:
            facade.logger.debug(
                "Failed to get shortname from database for %s, will try interface fallback",
                sender,
            )

        if not longname or not shortname:
            node = interface.nodes.get(sender)
            if node:
                user = node.get("user")
                if user:
                    if not longname:
                        longname_val = user.get("longName")
                        if longname_val and sender is not None:
                            facade.save_longname(sender, longname_val)
                            longname = longname_val
                    if not shortname:
                        shortname_val = user.get("shortName")
                        if shortname_val and sender is not None:
                            facade.save_shortname(sender, shortname_val)
                            shortname = shortname_val
            else:
                facade.logger.debug(f"Node info for sender {sender} not available yet.")

        if not longname:
            longname = str(sender)
        if not shortname:
            shortname = str(sender)

        from mmrelay.matrix_utils import get_matrix_prefix

        prefix = get_matrix_prefix(facade.config, longname, shortname, meshnet_name)
        formatted_message = f"{prefix}{text}"

        # Plugin functionality - Check if any plugin handles this message before relaying
        found_matching_plugin = facade._run_meshtastic_plugins(
            packet=packet,
            formatted_message=formatted_message,
            longname=longname,
            meshnet_name=meshnet_name,
            loop=loop,
            cfg=facade.config,
        )

        if is_direct_message:
            facade.logger.debug(
                f"Received a direct message from {longname}: {text}. Not relaying to Matrix."
            )
            return
        if found_matching_plugin:
            facade.logger.debug(
                "Message was handled by a plugin. Not relaying to Matrix."
            )
            return

        if action == PacketAction.PLUGIN_ONLY:
            return

        if skip_matrix_relay:
            return

        # Only RELAY packets with valid channels reach here
        if not matrix_rooms_configured:
            facade.logger.warning(
                f"matrix_rooms is empty - cannot relay message from {longname}. "
                f"This may indicate a startup race condition or configuration issue. "
                f"Message will be dropped: {text[:50]}{'...' if len(text) > 50 else ''}"
            )
            return

        if not channel_mapped:
            available_channels = []
            for room in iterable_rooms:
                if isinstance(room, dict):
                    ch = facade._normalize_room_channel(room)
                    if ch is not None:
                        available_channels.append(ch)

            facade.logger.warning(
                f"Skipping message from unmapped channel {channel}. "
                f"Available channels in config: {available_channels}. "
                f"Check your matrix_rooms configuration to ensure this channel is mapped."
            )
            return

        facade.logger.info(f"Relaying Meshtastic message from {longname} to Matrix")

        iterable_rooms = _get_iterable_matrix_rooms()
        for room in iterable_rooms:
            if not isinstance(room, dict):
                continue

            room_channel = facade._normalize_room_channel(room)
            if room_channel is None:
                continue

            if room_channel == channel:
                try:
                    facade._fire_and_forget(
                        matrix_relay(
                            room["id"],
                            formatted_message,
                            longname,
                            shortname,
                            meshnet_name,
                            decoded.get("portnum", 0),
                            meshtastic_id=packet.get("id"),
                            meshtastic_text=text,
                        ),
                        loop=loop,
                    )
                except Exception:
                    facade.logger.exception("Error relaying message to Matrix")
    else:
        # Non-text messages via plugins
        portnum = decoded.get("portnum")
        facade._run_meshtastic_plugins(
            packet=packet,
            formatted_message=None,
            longname=None,
            meshnet_name=None,
            loop=loop,
            cfg=facade.config,
            use_keyword_args=True,
            log_with_portnum=True,
            portnum=portnum,
        )
