import functools
import threading
from typing import Any

import mmrelay.meshtastic_utils as facade

__all__ = [
    "_claim_health_probe_response_and_maybe_calibrate",
    "_extract_packet_request_id",
    "_failed_probe_ack_state_error",
    "_handle_probe_ack_callback",
    "_is_health_probe_response_packet",
    "_metadata_probe_ack_timeout_error",
    "_missing_ack_state_error",
    "_missing_local_node_ack_state_error",
    "_missing_probe_transport_error",
    "_missing_probe_wait_error",
    "_missing_received_nak_error",
    "_prune_health_probe_tracking",
    "_probe_device_connection",
    "_reset_probe_ack_state",
    "_seed_connect_time_skew",
    "_set_probe_ack_flag_from_packet",
    "_track_health_probe_request_id",
    "_wait_for_probe_ack",
    "check_connection",
    "requires_continuous_health_monitor",
]


def _extract_packet_request_id(packet: Any) -> int | None:
    """
    Extract a request ID from a Meshtastic packet dict, if present.
    """
    if not isinstance(packet, dict):
        return None

    candidates: list[Any] = [packet.get("requestId"), packet.get("request_id")]
    decoded = packet.get("decoded")
    if isinstance(decoded, dict):
        candidates.extend(
            [
                decoded.get("requestId"),
                decoded.get("request_id"),
            ]
        )

    for candidate in candidates:
        parsed = facade._coerce_positive_int_id(candidate)
        if parsed is not None:
            return parsed
    return None


def _prune_health_probe_tracking(now: float | None = None) -> None:
    """
    Remove expired health-probe request IDs from the in-memory tracking map.
    """
    current = now if now is not None else facade.time.monotonic()
    expired_ids = [
        request_id
        for request_id, deadline in facade._health_probe_request_deadlines.items()
        if deadline <= current
    ]
    for request_id in expired_ids:
        facade._health_probe_request_deadlines.pop(request_id, None)


def _track_health_probe_request_id(
    raw_request_id: Any, timeout_secs: float
) -> int | None:
    """
    Track a newly sent health-probe request ID for response log classification.

    Returns the normalized request ID if tracking succeeded.
    """
    request_id = facade._coerce_positive_int_id(raw_request_id)
    if request_id is None:
        return None

    expires_at = (
        facade.time.monotonic()
        + max(float(timeout_secs), 1.0)
        + facade.HEALTH_PROBE_TRACK_GRACE_SECS
    )
    with facade._health_probe_request_lock:
        facade._prune_health_probe_tracking()
        facade._health_probe_request_deadlines[request_id] = expires_at
    return request_id


def _seed_connect_time_skew(rx_time: float) -> bool:
    """Seed rxTime clock skew from an early packet if not yet calibrated.

    Returns:
        bool: True when a new skew value was calibrated for this packet.
    """
    if rx_time <= 0:
        return False

    calibrated_skew: float = 0.0
    startup_age: float = 0.0
    calibrated_from_reconnect_prestart: bool = False

    now_wall = facade.time.time()
    now_monotonic = facade.time.monotonic()
    observed_skew = now_wall - rx_time

    with facade._relay_rx_time_clock_skew_lock:
        if facade._relay_rx_time_clock_skew_secs is not None:
            return False

        relay_start_time = facade.RELAY_START_TIME
        startup_age = max(
            0.0, now_monotonic - facade._relay_connection_started_monotonic_secs
        )
        within_startup_window = startup_age <= facade.RX_TIME_SKEW_BOOTSTRAP_WINDOW_SECS
        startup_drain_active = (
            facade._relay_startup_drain_deadline_monotonic_secs is not None
        )
        reconnect_bootstrap_deadline = (
            facade._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs
        )
        reconnect_bootstrap_active = (
            reconnect_bootstrap_deadline is not None
            and reconnect_bootstrap_deadline >= now_monotonic
        )
        if (
            reconnect_bootstrap_deadline is not None
            and reconnect_bootstrap_deadline < now_monotonic
        ):
            facade._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = None
        packet_is_post_start = rx_time >= relay_start_time

        if not packet_is_post_start and (
            not within_startup_window
            or (not startup_drain_active and not reconnect_bootstrap_active)
        ):
            return False

        if abs(observed_skew) > facade.RX_TIME_SKEW_BOOTSTRAP_MAX_SKEW_SECS:
            facade.logger.debug(
                "Skipping rxTime skew bootstrap %.3f seconds outside startup limit %.3f",
                observed_skew,
                facade.RX_TIME_SKEW_BOOTSTRAP_MAX_SKEW_SECS,
            )
            return False

        facade._relay_rx_time_clock_skew_secs = observed_skew
        calibrated_skew = observed_skew
        calibrated_from_reconnect_prestart = (
            not packet_is_post_start
            and not startup_drain_active
            and reconnect_bootstrap_active
        )
        if calibrated_from_reconnect_prestart:
            # Consume the one-shot reconnect bootstrap allowance.
            facade._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = None

    if packet_is_post_start:
        facade.logger.debug(
            "Calibrated rxTime clock skew from connect-time packet to %.3f seconds",
            calibrated_skew,
        )
    elif calibrated_from_reconnect_prestart:
        facade.logger.debug(
            "Bootstrapped rxTime clock skew from reconnect packet to %.3f seconds (startup_age=%.3f seconds)",
            calibrated_skew,
            startup_age,
        )
    else:
        facade.logger.debug(
            "Bootstrapped rxTime clock skew from startup packet to %.3f seconds (startup_age=%.3f seconds)",
            calibrated_skew,
            startup_age,
        )

    return True


def _is_health_probe_response_packet(packet: dict[str, Any], interface: Any) -> bool:
    """
    Determine if an inbound packet is a tracked health-probe response.
    """
    request_id = facade._extract_packet_request_id(packet)
    if request_id is None:
        return False

    sender = facade._coerce_int_id(packet.get("from"))
    local_num_raw = getattr(getattr(interface, "myInfo", None), "my_node_num", None)
    if local_num_raw is None:
        local_num_raw = getattr(getattr(interface, "localNode", None), "nodeNum", None)
    local_num = facade._coerce_int_id(local_num_raw)
    if sender is not None and local_num is not None and sender != local_num:
        return False

    with facade._health_probe_request_lock:
        facade._prune_health_probe_tracking()
        return request_id in facade._health_probe_request_deadlines


def _claim_health_probe_response_and_maybe_calibrate(
    packet: dict[str, Any], interface: Any, rx_time: float
) -> bool:
    """
    Atomically claim a tracked health-probe response and calibrate skew once.

    Lock order intentionally matches connect_meshtastic():
    _health_probe_request_lock -> _relay_rx_time_clock_skew_lock.
    """
    request_id = facade._extract_packet_request_id(packet)
    if request_id is None:
        return False

    sender = facade._coerce_int_id(packet.get("from"))
    local_num_raw = getattr(getattr(interface, "myInfo", None), "my_node_num", None)
    if local_num_raw is None:
        local_num_raw = getattr(getattr(interface, "localNode", None), "nodeNum", None)
    local_num = facade._coerce_int_id(local_num_raw)
    if sender is not None and local_num is not None and sender != local_num:
        return False

    observed_skew: float | None = None
    calibrated_now = False
    with facade._health_probe_request_lock:
        facade._prune_health_probe_tracking()
        if request_id not in facade._health_probe_request_deadlines:
            return False

        # Claim request ID so late duplicates cannot recalibrate.
        facade._health_probe_request_deadlines.pop(request_id, None)

        if rx_time > 0:
            observed_skew = facade.time.time() - rx_time
            if abs(observed_skew) > facade.RX_TIME_SKEW_BOOTSTRAP_MAX_SKEW_SECS:
                facade.logger.debug(
                    "[HEALTH_CHECK] Skipping rxTime clock skew calibration %.3f seconds outside startup limit %.3f",
                    observed_skew,
                    facade.RX_TIME_SKEW_BOOTSTRAP_MAX_SKEW_SECS,
                )
            else:
                with facade._relay_rx_time_clock_skew_lock:
                    if facade._relay_rx_time_clock_skew_secs is None:
                        facade._relay_rx_time_clock_skew_secs = observed_skew
                        calibrated_now = True

    if calibrated_now and observed_skew is not None:
        facade.logger.debug(
            "[HEALTH_CHECK] Calibrated rxTime clock skew to %.3f seconds",
            observed_skew,
        )
    return True


def _set_probe_ack_flag_from_packet(local_node: Any, packet: Any) -> bool:
    """
    Best-effort fallback for ACK packets missing routing metadata.

    Some Meshtastic library versions can invoke ACK callbacks with packet shapes
    that do not include `decoded.routing`, which causes `Node.onAckNak()` to
    raise `KeyError("routing")`. For health probes we only need an ACK/NAK
    signal, so this helper sets the same acknowledgment flags used by
    `waitForAckNak()`.

    Parameters:
        local_node (Any): Meshtastic local node object expected to expose `iface`.
        packet (Any): Callback packet payload (typically a dict).

    Returns:
        bool: True if a fallback ACK flag was set, False otherwise.
    """
    iface = getattr(local_node, "iface", None)
    ack_state = getattr(iface, "_acknowledgment", None)
    if ack_state is None:
        return False

    sender_raw = packet.get("from") if isinstance(packet, dict) else None
    local_num = getattr(getattr(iface, "localNode", None), "nodeNum", None)

    sender_num = facade._coerce_int_id(sender_raw)

    if (
        sender_num is not None
        and local_num is not None
        and sender_num == local_num
        and hasattr(ack_state, "receivedImplAck")
    ):
        ack_state.receivedImplAck = True
        return True

    if hasattr(ack_state, "receivedAck"):
        ack_state.receivedAck = True
        return True

    return False


def _missing_local_node_ack_state_error() -> RuntimeError:
    """
    Build the error raised when local node ACK state is unavailable.
    """
    return RuntimeError("Meshtastic local node missing acknowledgment state")


def _missing_received_nak_error() -> RuntimeError:
    """
    Build the error raised when ACK state cannot represent NAK responses.
    """
    return RuntimeError("Meshtastic acknowledgment state missing receivedNak")


def _failed_probe_ack_state_error() -> RuntimeError:
    """
    Build the error raised when a probe response cannot set ACK/NAK state.
    """
    return RuntimeError("Failed to set ACK state from health probe response")


def _missing_ack_state_error() -> RuntimeError:
    """
    Build the error raised when client ACK state is unavailable.
    """
    return RuntimeError("Meshtastic client missing acknowledgment state")


def _metadata_probe_ack_timeout_error(timeout_secs: float) -> TimeoutError:
    """
    Build the timeout error raised when metadata probe ACK wait exceeds limit.
    """
    return TimeoutError(
        f"Timed out waiting for metadata probe ACK after {timeout_secs:.1f} seconds"
    )


def _missing_probe_transport_error() -> RuntimeError:
    """
    Build the error raised when client cannot send metadata probe packets.
    """
    return RuntimeError("Meshtastic client cannot perform metadata liveness probe")


def _missing_probe_wait_error() -> RuntimeError:
    """
    Build the error raised when client cannot wait for metadata probe ACKs.
    """
    return RuntimeError("Meshtastic client cannot wait for metadata probe ACK")


def _reset_probe_ack_state(ack_state: Any) -> None:
    """
    Reset health-probe acknowledgment flags on a Meshtastic ACK state object.

    Uses the object's `reset()` method when available; otherwise manually clears
    known ACK/NAK flags for compatibility with test doubles and older interfaces.
    """
    reset = getattr(ack_state, "reset", None)
    if callable(reset):
        reset()
        return

    for attr in ("receivedAck", "receivedNak", "receivedImplAck"):
        if hasattr(ack_state, attr):
            setattr(ack_state, attr, False)


def _handle_probe_ack_callback(local_node: Any, packet: Any) -> None:
    """
    Handle health-probe response packets across routing/admin response shapes.

    For `get_device_metadata_request`, Meshtastic responses may be:
    - A ROUTING_APP ACK/NAK packet with `decoded.routing.errorReason`, or
    - An ADMIN_APP response packet that includes `requestId` but no
      `decoded.routing`.

    We treat either as forward progress for liveness probing by setting the same
    acknowledgment flags used by `waitForAckNak()`.
    """
    iface = getattr(local_node, "iface", None)
    ack_state = getattr(iface, "_acknowledgment", None)
    if ack_state is None:
        raise facade._missing_local_node_ack_state_error()

    decoded = packet.get("decoded") if isinstance(packet, dict) else None
    routing = decoded.get("routing") if isinstance(decoded, dict) else None
    if isinstance(routing, dict):
        error_reason = routing.get("errorReason")
        if error_reason and error_reason != "NONE":
            if hasattr(ack_state, "receivedNak"):
                ack_state.receivedNak = True
                return
            raise facade._missing_received_nak_error()

    if facade._set_probe_ack_flag_from_packet(local_node, packet):
        return

    raise facade._failed_probe_ack_state_error()


def _wait_for_probe_ack(client: Any, timeout_secs: float) -> None:
    """
    Wait for ACK/NAK flags with a bounded timeout for health probes.

    Uses the interface acknowledgment object directly so probe duration is
    capped independently of the interface-wide timeout setting.
    """
    ack_state = getattr(client, "_acknowledgment", None)
    if ack_state is None:
        raise facade._missing_ack_state_error()

    ack_attrs = ("receivedAck", "receivedNak", "receivedImplAck")

    deadline = facade.time.monotonic() + timeout_secs
    while facade.time.monotonic() < deadline:
        if any(bool(getattr(ack_state, attr, False)) for attr in ack_attrs):
            facade._reset_probe_ack_state(ack_state)
            return
        remaining = deadline - facade.time.monotonic()
        if remaining <= 0:
            break
        facade.time.sleep(min(facade.ACK_POLL_INTERVAL_SECS, remaining))

    # Final check catches ACK/NAK updates that may land near the deadline.
    if any(bool(getattr(ack_state, attr, False)) for attr in ack_attrs):
        facade._reset_probe_ack_state(ack_state)
        return

    raise facade._metadata_probe_ack_timeout_error(timeout_secs)


def _probe_device_connection(
    client: Any, timeout_secs: float = facade.DEFAULT_MESHTASTIC_OPERATION_TIMEOUT
) -> None:
    """
    Send a metadata admin request and wait for an acknowledgment.

    This uses the public sendData API instead of the private _sendAdmin
    to ensure compatibility across Serial, TCP, and BLE interfaces.

    Note: The onResponse callback must handle both routing ACK packets and
    admin response packets for this request shape. Some Meshtastic versions
    provide callback payloads without `decoded.routing`; this function uses a
    guarded callback plus a bounded ACK wait to avoid long probe stalls.

    Parameters:
        client (Any): Meshtastic client/interface instance used for probe send and
            ACK tracking.
        timeout_secs (float): Maximum seconds to wait for probe ACK/NAK before
            raising TimeoutError.
    """
    local_node = getattr(client, "localNode", None)
    if local_node is None or not callable(getattr(client, "sendData", None)):
        raise facade._missing_probe_transport_error()

    # Clear stale ACK/NAK flags so this probe cannot "pass" on prior traffic.
    ack_state = getattr(client, "_acknowledgment", None)
    if ack_state is None:
        ack_state = getattr(getattr(local_node, "iface", None), "_acknowledgment", None)
    if ack_state is not None:
        facade._reset_probe_ack_state(ack_state)

    request = facade.admin_pb2.AdminMessage()
    request.get_device_metadata_request = True
    # Use the public sendData API instead of private _sendAdmin
    node_num = getattr(local_node, "nodeNum", None)
    destination_id = node_num if node_num is not None else "^local"
    sent_packet = client.sendData(
        request.SerializeToString(),
        destinationId=destination_id,
        portNum=facade.portnums_pb2.PortNum.ADMIN_APP,
        wantAck=True,
        wantResponse=True,
        onResponse=functools.partial(facade._handle_probe_ack_callback, local_node),
    )
    request_id = facade._track_health_probe_request_id(
        (
            getattr(sent_packet, "id", None)
            if not isinstance(sent_packet, dict)
            else sent_packet.get("id")
        ),
        timeout_secs,
    )
    if request_id is not None:
        facade.logger.debug(
            "[HEALTH_CHECK] Sent metadata probe requestId=%s timeout=%.1fs",
            request_id,
            timeout_secs,
        )
    else:
        facade.logger.debug(
            "[HEALTH_CHECK] Sent metadata probe timeout=%.1fs",
            timeout_secs,
        )

    if getattr(client, "_acknowledgment", None) is not None:
        facade._wait_for_probe_ack(client, timeout_secs)
        return

    if callable(getattr(client, "waitForAckNak", None)):
        facade._run_blocking_with_timeout(
            client.waitForAckNak,
            timeout=timeout_secs,
            label="metadata-probe-waitForAckNak",
            timeout_log_level=facade.logging.DEBUG,
        )
        return

    raise facade._missing_probe_wait_error()


def requires_continuous_health_monitor(config: dict) -> bool:
    """
    Return True when periodic health monitoring should run for the given config.

    Health monitoring is disabled for BLE connections (which have real-time disconnect
    detection) and when health_check.enabled is explicitly set to False.

    Note: This function only returns False for "clean" exit cases (BLE or explicitly
    disabled). Malformed health_check configs return True so that check_connection
    can log appropriate warnings before exiting.

    Args:
        config: The full configuration dictionary.

    Returns:
        True if health monitoring should run continuously, False otherwise.
    """
    meshtastic_config = config.get(facade.CONFIG_SECTION_MESHTASTIC)
    if not isinstance(meshtastic_config, dict):
        return facade.DEFAULT_HEALTH_CHECK_ENABLED
    if (
        meshtastic_config.get(facade.CONFIG_KEY_CONNECTION_TYPE)
        == facade.CONNECTION_TYPE_BLE
    ):
        return False
    health_config = meshtastic_config.get("health_check")
    if health_config is None:
        return facade.DEFAULT_HEALTH_CHECK_ENABLED
    if not isinstance(health_config, dict):
        return True
    raw_enabled = health_config.get("enabled", facade.DEFAULT_HEALTH_CHECK_ENABLED)
    return facade._coerce_bool(
        raw_enabled, facade.DEFAULT_HEALTH_CHECK_ENABLED, "health_check.enabled"
    )


async def check_connection() -> None:
    """
    Periodically verify the Meshtastic connection and trigger a reconnect when the device appears unresponsive.

    Checks run until the module-level `shutting_down` flag is True. Behavior:
    - Controlled by config["meshtastic"]["health_check"]:
      - `enabled` (bool, default DEFAULT_HEALTH_CHECK_ENABLED) — enable or disable checks.
      - `heartbeat_interval` (int, seconds, default 60) — interval between checks. For backward compatibility, a top-level `heartbeat_interval` under `config["meshtastic"]` is supported.
      - `initial_delay` (float, seconds, default INITIAL_HEALTH_CHECK_DELAY) — delay before first probe.
      - `probe_timeout` (float, seconds, default DEFAULT_MESHTASTIC_OPERATION_TIMEOUT) — timeout per probe cycle.
    - BLE connections are excluded from periodic checks because BLE libraries provide real-time disconnect detection; this function returns early for BLE.
    - Waits one `initial_delay` period before the first check to allow the connection to settle,
      particularly important for fast-responding systems like MeshMonitor where ACK handling
      may not be fully initialized immediately after connection.
    - For non-BLE connections, performs a low-level metadata admin probe using
      the same `get_device_metadata_request` packet as `localNode.getMetadata()`
      but without stdout capture. If the probe fails and no reconnection is
      already in progress, calls on_lost_meshtastic_connection(...) to
      initiate reconnection.
    - If another metadata probe is already in flight, skips the current cycle
      instead of treating the overlap as a transport failure.
    - IMPORTANT: Do not use `getMyNodeInfo()` as the primary liveness probe here.
      In current Meshtastic Python it reads cached local node data and does not
      guarantee a fresh on-wire round trip.

    No return value; side effects are logging and scheduling/triggering reconnection when the device is unresponsive.
    """
    # Check if config is available
    if facade.config is None:
        facade.logger.error("No configuration available. Cannot check connection.")
        return

    # Exit early if health monitoring is not required for this connection type/config
    if not facade.requires_continuous_health_monitor(facade.config):
        meshtastic_config = facade.config.get(facade.CONFIG_SECTION_MESHTASTIC)
        connection_type = (
            meshtastic_config.get(facade.CONFIG_KEY_CONNECTION_TYPE)
            if isinstance(meshtastic_config, dict)
            else None
        )
        if connection_type == facade.CONNECTION_TYPE_BLE:
            facade.logger.debug(
                "BLE connection uses real-time disconnection detection; periodic health checks disabled"
            )
        else:
            facade.logger.info("Connection health checks are disabled in configuration")
        return

    meshtastic_config = facade.config.get(facade.CONFIG_SECTION_MESHTASTIC)
    if not isinstance(meshtastic_config, dict):
        facade.logger.warning(
            "meshtastic config section is not a dictionary; using defaults"
        )
        meshtastic_config = {}
    connection_type = meshtastic_config.get(facade.CONFIG_KEY_CONNECTION_TYPE)

    # Get health check configuration
    health_config = meshtastic_config.get("health_check", {})
    if not isinstance(health_config, dict):
        facade.logger.warning(
            "meshtastic.health_check config is not a dictionary (got %r); using defaults",
            health_config,
        )
        health_config = {}

    raw_health_check_enabled = health_config.get(
        "enabled", facade.DEFAULT_HEALTH_CHECK_ENABLED
    )
    health_check_enabled = facade._coerce_bool(
        raw_health_check_enabled,
        facade.DEFAULT_HEALTH_CHECK_ENABLED,
        "meshtastic.health_check.enabled",
    )

    if not health_check_enabled:
        facade.logger.info("Connection health checks are disabled in configuration")
        return

    heartbeat_interval = health_config.get(
        "heartbeat_interval", facade.DEFAULT_HEARTBEAT_INTERVAL_SECS
    )
    initial_delay = health_config.get(
        "initial_delay", facade.INITIAL_HEALTH_CHECK_DELAY
    )
    probe_timeout = health_config.get(
        "probe_timeout", facade.DEFAULT_MESHTASTIC_OPERATION_TIMEOUT
    )

    # Support legacy heartbeat_interval configuration for backward compatibility
    if (
        isinstance(meshtastic_config, dict)
        and "heartbeat_interval" in meshtastic_config
    ):
        heartbeat_interval = meshtastic_config["heartbeat_interval"]

    heartbeat_interval = facade._coerce_positive_float(
        heartbeat_interval,
        float(facade.DEFAULT_HEARTBEAT_INTERVAL_SECS),
        "meshtastic.health_check.heartbeat_interval",
    )
    initial_delay = facade._coerce_positive_float(
        initial_delay,
        float(facade.INITIAL_HEALTH_CHECK_DELAY),
        "meshtastic.health_check.initial_delay",
    )
    probe_timeout = facade._coerce_positive_float(
        probe_timeout,
        float(facade.DEFAULT_MESHTASTIC_OPERATION_TIMEOUT),
        "meshtastic.health_check.probe_timeout",
    )

    # Initial delay before first health check to allow connection to settle.
    # This is particularly important for fast-responding systems like MeshMonitor
    # where the connection may be established quickly but ACK handling may not be
    # fully initialized yet.
    facade.logger.debug(
        "Waiting before starting connection health checks to allow connection to settle"
    )
    await facade.asyncio.sleep(initial_delay)

    while not facade.shutting_down:
        if facade.meshtastic_client and not facade.reconnecting:
            submitted_client = facade.meshtastic_client
            probe_submission_failed = False
            degraded_error = False
            try:
                probe_future = facade._submit_metadata_probe(
                    functools.partial(
                        facade._probe_device_connection,
                        submitted_client,
                        probe_timeout,
                    )
                )
            except facade.MetadataExecutorDegradedError:
                facade.logger.error(
                    "Metadata executor degraded; triggering reconnection"
                )
                probe_future = None
                degraded_error = True
            except RuntimeError as exc:
                facade.logger.debug(
                    "Skipping connection check - metadata probe submission failed",
                    exc_info=exc,
                )
                probe_future = None
                probe_submission_failed = True

            if degraded_error:
                if (
                    not facade.reconnecting
                    and facade.meshtastic_client is submitted_client
                ):
                    facade.on_lost_meshtastic_connection(
                        interface=submitted_client,
                        detection_source="metadata executor degraded",
                    )
            elif probe_future is None:
                if not probe_submission_failed:
                    facade.logger.debug(
                        "Skipping connection check - metadata probe already in progress"
                    )
            else:
                try:
                    # NOTE: Use the metadata admin request for keepalive/liveness.
                    # `getMyNodeInfo()` is local cached state in Meshtastic Python,
                    # so it can succeed even when the transport is unhealthy.
                    await facade.asyncio.wait_for(
                        facade.asyncio.wrap_future(probe_future),
                        timeout=probe_timeout,
                    )

                except Exception as exc:
                    error_detail = str(exc).strip() or exc.__class__.__name__
                    # Only trigger reconnection if we're not already reconnecting
                    if (
                        not facade.reconnecting
                        and facade.meshtastic_client is submitted_client
                    ):
                        facade.logger.error(
                            "%s connection health check failed: %s",
                            (connection_type or "unknown").capitalize(),
                            error_detail,
                            exc_info=True,
                        )
                        facade.on_lost_meshtastic_connection(
                            interface=submitted_client,
                            detection_source=f"health check failed: {error_detail}",
                        )
                    else:
                        facade.logger.debug(
                            "Skipping reconnection trigger - already reconnecting or client changed"
                        )
        elif facade.reconnecting:
            facade.logger.debug("Skipping connection check - reconnection in progress")
        elif not facade.meshtastic_client:
            facade.logger.debug("Skipping connection check - no client available")

        await facade.asyncio.sleep(heartbeat_interval)
