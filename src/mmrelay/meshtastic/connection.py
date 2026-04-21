import contextlib
import functools
import inspect
import math
import threading
from concurrent.futures import Future
from typing import Any, NoReturn

import mmrelay.meshtastic_utils as facade
from mmrelay.constants.config import (
    CONFIG_KEY_CONNECT_PROBE_ENABLED,
    CONFIG_KEY_ENABLED,
    CONFIG_KEY_HEALTH_CHECK,
    CONFIG_KEY_PROBE_TIMEOUT,
    CONFIG_SECTION_MESHTASTIC,
    DEFAULT_HEALTH_CHECK_ENABLED,
)
from mmrelay.constants.network import (
    BLE_INTERFACE_CREATE_TIMEOUT_FLOOR_SECS,
    BLE_TROUBLESHOOTING_GUIDANCE,
    CONFIG_KEY_BLE_ADDRESS,
    CONFIG_KEY_CONNECTION_TYPE,
    CONFIG_KEY_HOST,
    CONFIG_KEY_PORT,
    CONFIG_KEY_SERIAL_PORT,
    CONFIG_KEY_TIMEOUT,
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_NETWORK,
    CONNECTION_TYPE_SERIAL,
    CONNECTION_TYPE_TCP,
    DEFAULT_MESHTASTIC_OPERATION_TIMEOUT,
    DEFAULT_MESHTASTIC_TIMEOUT,
    DEFAULT_TCP_PORT,
)

__all__ = [
    "_connect_meshtastic_impl",
    "_log_ble_shutdown_state",
    "_get_connection_retry_wait_time",
    "_get_connect_time_probe_settings",
    "_rollback_connect_attempt_state",
    "_schedule_connect_time_calibration_probe",
    "ConnectionCompletedWithoutClientError",
    "connect_meshtastic",
    "serial_port_exists",
]

CONNECT_PROBE_POST_DRAIN_DELAY_SECS: float = 2.0


class BLEDiscoveryTransientError(Exception):
    """Retryable BLE discovery/setup failure that should not consume timeout budget."""


class ConnectionCompletedWithoutClientError(ConnectionError):
    """Connection path returned without producing a usable client."""


def _raise_no_client(connection_type: str) -> NoReturn:
    """Raise ConnectionCompletedWithoutClientError for the given connection type."""
    raise ConnectionCompletedWithoutClientError(
        f"Meshtastic {connection_type} connection completed without a client."
    )


def serial_port_exists(port_name: str) -> bool:
    """
    Determine whether a serial port with the given device name exists on the system.

    Parameters:
        port_name (str): Device name to check (e.g., '/dev/ttyUSB0' on Unix or 'COM3' on Windows).

    Returns:
        `True` if a matching port device name is present, `False` otherwise.
    """
    ports = [p.device for p in facade.serial.tools.list_ports.comports()]
    return port_name in ports


def _log_ble_shutdown_state(*, context: str) -> None:
    """Log in-flight BLE worker state at shutdown for diagnostics."""
    pending_ble_future: Future[Any] | None = None
    pending_ble_address: str | None = None
    pending_ble_started_at: float | None = None
    pending_ble_timeout_secs: float | None = None
    with facade._ble_executor_lock:
        pending_ble_future = facade._ble_future
        pending_ble_address = facade._ble_future_address
        pending_ble_started_at = facade._ble_future_started_at
        pending_ble_timeout_secs = facade._ble_future_timeout_secs
    if pending_ble_future is None or pending_ble_future.done():
        return
    elapsed_secs: float | None = None
    if pending_ble_started_at is not None:
        elapsed_secs = max(
            0.0,
            facade.time.monotonic() - pending_ble_started_at,
        )
    facade.logger.debug(
        "Meshtastic shutdown during %s with in-flight BLE worker for %s "
        "(elapsed=%ss, timeout=%ss)",
        context,
        pending_ble_address or "unknown",
        "unknown" if elapsed_secs is None else f"{elapsed_secs:.1f}",
        (
            "unknown"
            if pending_ble_timeout_secs is None
            else f"{pending_ble_timeout_secs:.1f}"
        ),
    )


def _get_connection_retry_wait_time(attempts: int) -> float:
    """Return capped exponential retry backoff without exponentiating past the cap."""
    if attempts <= 0 or facade.CONNECTION_RETRY_BACKOFF_MAX_SECS <= 0:
        return 0.0

    if facade.CONNECTION_RETRY_BACKOFF_BASE <= 1:
        return min(
            float(facade.CONNECTION_RETRY_BACKOFF_BASE**attempts),
            float(facade.CONNECTION_RETRY_BACKOFF_MAX_SECS),
        )

    max_capped_attempt = math.ceil(
        math.log(
            facade.CONNECTION_RETRY_BACKOFF_MAX_SECS,
            facade.CONNECTION_RETRY_BACKOFF_BASE,
        )
    )
    exponent = min(attempts, max_capped_attempt)
    return min(
        float(facade.CONNECTION_RETRY_BACKOFF_BASE**exponent),
        float(facade.CONNECTION_RETRY_BACKOFF_MAX_SECS),
    )


def _get_connect_time_probe_settings(
    active_config: dict[str, Any] | None, connection_type: str
) -> tuple[bool, float]:
    """
    Return connect-time probe enablement and timeout settings for a connection.
    """
    default_timeout = float(DEFAULT_MESHTASTIC_OPERATION_TIMEOUT)
    default_enabled = DEFAULT_HEALTH_CHECK_ENABLED
    if not isinstance(active_config, dict):
        return default_enabled, default_timeout

    meshtastic_cfg = active_config.get(CONFIG_SECTION_MESHTASTIC)
    if not isinstance(meshtastic_cfg, dict):
        return default_enabled, default_timeout

    health_cfg = meshtastic_cfg.get(CONFIG_KEY_HEALTH_CHECK)
    if not isinstance(health_cfg, dict):
        return default_enabled, default_timeout

    inherited_enabled = facade._coerce_bool(
        health_cfg.get(CONFIG_KEY_ENABLED, default_enabled),
        default_enabled,
        "meshtastic.health_check.enabled",
    )
    enabled = facade._coerce_bool(
        health_cfg.get(CONFIG_KEY_CONNECT_PROBE_ENABLED, inherited_enabled),
        inherited_enabled,
        "meshtastic.health_check.connect_probe_enabled",
    )
    timeout_secs = facade._coerce_positive_float(
        health_cfg.get(CONFIG_KEY_PROBE_TIMEOUT, default_timeout),
        default_timeout,
        "meshtastic.health_check.probe_timeout",
    )
    return enabled, timeout_secs


def _schedule_connect_time_calibration_probe(
    client: Any,
    *,
    connection_type: str,
    active_config: dict[str, Any] | None,
) -> None:
    """
    Best-effort one-shot metadata probe after connect for skew calibration backup.
    """
    enabled, timeout_secs = facade._get_connect_time_probe_settings(
        active_config, connection_type
    )
    if not enabled:
        facade.logger.debug("Connect-time metadata probe is disabled in configuration")
        return

    local_node = getattr(client, "localNode", None)
    if local_node is None or not callable(getattr(client, "sendData", None)):
        facade.logger.debug(
            "Skipping connect-time metadata probe; client lacks localNode/sendData support"
        )
        return

    def _submit_probe() -> None:
        try:
            probe_future = facade._submit_metadata_probe(
                functools.partial(
                    facade._probe_device_connection,
                    client,
                    timeout_secs,
                )
            )
        except facade.MetadataExecutorDegradedError:
            facade.logger.debug(
                "Skipping connect-time metadata probe; metadata executor is degraded"
            )
            return
        except RuntimeError as exc:
            facade.logger.debug(
                "Skipping connect-time metadata probe; submission failed",
                exc_info=exc,
            )
            return

        if probe_future is None:
            facade.logger.debug(
                "Skipping connect-time metadata probe; metadata probe already in progress"
            )
            return

        facade.logger.debug(
            "Scheduled one-shot connect-time metadata probe (timeout=%.1fs)",
            timeout_secs,
        )

    with facade._relay_rx_time_clock_skew_lock:
        drain_deadline = facade._relay_startup_drain_deadline_monotonic_secs

    if drain_deadline is not None:
        remaining = drain_deadline - facade.time.monotonic()
        if remaining > 0:
            delay = remaining + CONNECT_PROBE_POST_DRAIN_DELAY_SECS
            facade.logger.debug(
                "Delaying connect-time metadata probe by %.1fs until after startup drain window",
                delay,
            )

            def _delayed_submit_with_stale_guard() -> None:
                if facade.shutting_down:
                    facade.logger.debug(
                        "Skipping delayed connect-time metadata probe; shutdown in progress"
                    )
                    with facade._relay_rx_time_clock_skew_lock:
                        if facade._pending_connect_time_probe_timer is timer:
                            facade._pending_connect_time_probe_timer = None
                    return
                active_client = facade.meshtastic_client
                active_client_id = facade._relay_active_client_id
                if active_client is not client and (
                    active_client_id is None or active_client_id != id(client)
                ):
                    facade.logger.debug(
                        "Skipping delayed connect-time metadata probe; active client changed since scheduling"
                    )
                    with facade._relay_rx_time_clock_skew_lock:
                        if facade._pending_connect_time_probe_timer is timer:
                            facade._pending_connect_time_probe_timer = None
                    return
                with facade._relay_rx_time_clock_skew_lock:
                    if facade._pending_connect_time_probe_timer is timer:
                        facade._pending_connect_time_probe_timer = None
                _submit_probe()

            timer = threading.Timer(delay, _delayed_submit_with_stale_guard)
            timer.daemon = True
            old_timer = None
            with facade._relay_rx_time_clock_skew_lock:
                old_timer = facade._pending_connect_time_probe_timer
                facade._pending_connect_time_probe_timer = timer
            if old_timer is not None:
                with contextlib.suppress(Exception):
                    old_timer.cancel()
            timer.start()
            return

    _submit_probe()


def _rollback_connect_attempt_state(
    client: Any,
    client_assigned_for_this_connect: bool,
    startup_drain_armed_for_this_connect: bool,
    startup_drain_applied_for_this_connect: bool,
    reconnect_bootstrap_armed_for_this_connect: bool,
    lock_held: bool = False,
) -> bool:
    """
    Centralize cleanup of partially-assigned clients and timing state when a connect attempt fails.

    Returns the updated value for client_assigned_for_this_connect (always False after cleanup).
    """
    _lock_ctx = contextlib.nullcontext() if lock_held else facade.meshtastic_lock
    startup_drain_timer_to_cancel: Any = None
    mark_startup_drain_complete = False
    if client is not None and (
        client_assigned_for_this_connect or client is facade.meshtastic_iface
    ):
        with _lock_ctx:
            if facade.meshtastic_client is client or client is facade.meshtastic_iface:
                try:
                    if client is facade.meshtastic_iface:
                        facade._disconnect_ble_interface(
                            facade.meshtastic_iface, reason="connect setup failed"
                        )
                        facade.meshtastic_iface = None
                    else:
                        client.close()
                except Exception as cleanup_error:  # noqa: BLE001 - best-effort cleanup
                    facade.logger.warning(
                        "Error closing Meshtastic client after setup failure: %s",
                        cleanup_error,
                    )
                finally:
                    if facade.meshtastic_client is client:
                        facade.meshtastic_client = None
                        facade._relay_active_client_id = None

    if (
        startup_drain_armed_for_this_connect
        or reconnect_bootstrap_armed_for_this_connect
    ):
        with facade._relay_rx_time_clock_skew_lock:
            if startup_drain_armed_for_this_connect:
                startup_drain_timer_to_cancel = facade._relay_startup_drain_expiry_timer
                facade._relay_startup_drain_expiry_timer = None
                facade._relay_startup_drain_deadline_monotonic_secs = None
                mark_startup_drain_complete = True
                if startup_drain_applied_for_this_connect:
                    facade._startup_packet_drain_applied = False
            if reconnect_bootstrap_armed_for_this_connect:
                facade._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = (
                    None
                )
    if mark_startup_drain_complete:
        # Keep lock scope focused on state mutation; event signaling is non-blocking.
        startup_drain_complete_event = facade.get_startup_drain_complete_event()
        if startup_drain_complete_event is not None:
            startup_drain_complete_event.set()
    if startup_drain_timer_to_cancel is not None:
        with contextlib.suppress(AttributeError, RuntimeError, TypeError):
            startup_drain_timer_to_cancel.cancel()

    pending_probe_timer = None
    with facade._relay_rx_time_clock_skew_lock:
        pending_probe_timer = facade._pending_connect_time_probe_timer
        facade._pending_connect_time_probe_timer = None
    if pending_probe_timer is not None:
        with contextlib.suppress(Exception):
            pending_probe_timer.cancel()

    return False


def connect_meshtastic(
    passed_config: dict[str, Any] | None = None,
    force_connect: bool = False,
) -> Any:
    """
    Establish a Meshtastic connection while preventing overlapping attempts.

    This wrapper coordinates concurrent callers with an in-progress marker.
    Callers that arrive while another connect is running wait for that attempt
    to finish, then retry acquisition.
    """
    wait_budget_secs = facade._CONNECT_ATTEMPT_WAIT_MAX_SECS
    config_source = (
        passed_config
        if isinstance(passed_config, dict)
        else facade.config if isinstance(facade.config, dict) else None
    )
    if isinstance(config_source, dict):
        meshtastic_section = config_source.get(CONFIG_SECTION_MESHTASTIC)
        if isinstance(meshtastic_section, dict):
            connection_type = meshtastic_section.get(CONFIG_KEY_CONNECTION_TYPE)
            if connection_type == CONNECTION_TYPE_BLE:
                wait_budget_secs = max(
                    wait_budget_secs,
                    facade._CONNECT_ATTEMPT_BLE_WAIT_MAX_SECS,
                )

    wait_deadline = facade.time.monotonic() + wait_budget_secs

    while True:
        with facade._connect_attempt_condition:
            if not facade._connect_attempt_in_progress:
                facade._connect_attempt_in_progress = True
                break

            remaining_wait = wait_deadline - facade.time.monotonic()
            if remaining_wait <= 0:
                facade.logger.debug(
                    "Timed out waiting for active connect attempt; returning no client"
                )
                return None

            facade.logger.debug(
                "connect_meshtastic() already in progress; waiting for active attempt to finish"
            )

            while facade._connect_attempt_in_progress and not facade.shutting_down:
                remaining_wait = wait_deadline - facade.time.monotonic()
                if remaining_wait <= 0:
                    break
                facade._connect_attempt_condition.wait(
                    timeout=min(facade._CONNECT_ATTEMPT_WAIT_POLL_SECS, remaining_wait)
                )
            if facade.shutting_down:
                facade.logger.debug("Shutdown in progress. Not attempting to connect.")
                return None
            if (
                facade._connect_attempt_in_progress
                and facade.time.monotonic() >= wait_deadline
            ):
                facade.logger.debug(
                    "Timed out waiting for active connect attempt; returning no client"
                )
                return None

    try:
        return facade._connect_meshtastic_impl(
            passed_config=passed_config,
            force_connect=force_connect,
        )
    finally:
        with facade._connect_attempt_condition:
            facade._connect_attempt_in_progress = False
            facade._connect_attempt_condition.notify_all()


def _connect_meshtastic_impl(
    passed_config: dict[str, Any] | None = None,
    force_connect: bool = False,
) -> Any:
    """
    Establishes a Meshtastic client connection using the configured connection type (serial, BLE, or TCP).

    On success updates the module-level client state (meshtastic_client), may update matrix_rooms when a config is provided, and subscribes to meshtastic receive and connection-lost events once for the process lifetime. Honors shutdown and reconnect state and will respect `force_connect` to replace an existing connection.

    Parameters:
        passed_config (dict[str, Any] | None): Optional configuration to use in place of the module-level config; if provided and contains "matrix_rooms", that value will be used to update module-level matrix_rooms.
        force_connect (bool): If True, forces creating a new connection even if a client already exists.

    Returns:
        The connected Meshtastic client instance on success, or `None` if a connection could not be established or shutdown is in progress.
    """
    if facade.shutting_down:
        facade.logger.debug("Shutdown in progress. Not attempting to connect.")
        return None

    if facade.reconnecting and not force_connect:
        facade.logger.debug(
            "Reconnection already in progress. Not attempting new connection."
        )
        return None

    # Update the global config if a config is passed
    if passed_config is not None:
        facade.config = passed_config

        # If config is valid, extract matrix_rooms
        if facade.config and "matrix_rooms" in facade.config:
            facade.matrix_rooms = facade.config["matrix_rooms"]

    with facade.meshtastic_lock:
        if facade.meshtastic_client and not force_connect:
            return facade.meshtastic_client

        # Close previous connection if exists
        if facade.meshtastic_client:
            try:
                if facade.meshtastic_client is facade.meshtastic_iface:
                    # BLE needs an explicit disconnect to release BlueZ state; a
                    # plain close() can leave the adapter "busy" for the next
                    # connect attempt.
                    facade._disconnect_ble_interface(
                        facade.meshtastic_iface, reason="reconnect"
                    )
                    facade.meshtastic_iface = None
                else:
                    facade.meshtastic_client.close()
            except Exception as e:
                facade.logger.warning(
                    "Error closing previous connection: %s", e, exc_info=True
                )
            facade.meshtastic_client = None
            facade._relay_active_client_id = None

        # Check if config is available
        if facade.config is None:
            facade.logger.error(
                "No configuration available. Cannot connect to Meshtastic."
            )
            return None

        # Check if meshtastic config section exists
        if (
            CONFIG_SECTION_MESHTASTIC not in facade.config
            or facade.config[CONFIG_SECTION_MESHTASTIC] is None
        ):
            facade.logger.error(
                "No Meshtastic configuration section found. Cannot connect to Meshtastic."
            )
            return None

        # Check if connection_type is specified
        if (
            CONFIG_KEY_CONNECTION_TYPE not in facade.config[CONFIG_SECTION_MESHTASTIC]
            or facade.config[CONFIG_SECTION_MESHTASTIC][CONFIG_KEY_CONNECTION_TYPE]
            is None
        ):
            facade.logger.error(
                "No connection type specified in Meshtastic configuration. Cannot connect to Meshtastic."
            )
            return None

        # Determine connection type and attempt connection
        connection_type = facade.config[CONFIG_SECTION_MESHTASTIC][
            CONFIG_KEY_CONNECTION_TYPE
        ]

        # Support legacy "network" connection type (now "tcp")
        if connection_type == CONNECTION_TYPE_NETWORK:
            connection_type = CONNECTION_TYPE_TCP
            facade.logger.warning(
                "Using 'network' connection type (legacy). 'tcp' is now the preferred name and 'network' will be deprecated in a future version."
            )

    # Move retry loop outside the lock to prevent blocking other threads
    meshtastic_settings = (
        facade.config.get(CONFIG_SECTION_MESHTASTIC, {}) if facade.config else {}
    )
    retry_limit_raw = meshtastic_settings.get("retries")
    if retry_limit_raw is None:
        retry_limit_raw = meshtastic_settings.get(
            "retry_limit", facade.INFINITE_RETRIES
        )
        if "retry_limit" in meshtastic_settings:
            facade.logger.warning(
                "'retry_limit' is deprecated in meshtastic config; use 'retries' instead"
            )
    try:
        retry_limit = int(retry_limit_raw)
    except (TypeError, ValueError):
        retry_limit = facade.INFINITE_RETRIES
    attempts = 0
    timeout_attempts = 0
    successful = False

    # Get timeout configuration (default: DEFAULT_MESHTASTIC_TIMEOUT)
    timeout_raw = meshtastic_settings.get(
        CONFIG_KEY_TIMEOUT, DEFAULT_MESHTASTIC_TIMEOUT
    )
    try:
        timeout = int(timeout_raw)
        if timeout <= 0:
            facade.logger.warning(
                "Non-positive meshtastic.timeout value %r; using %ss fallback.",
                timeout_raw,
                DEFAULT_MESHTASTIC_TIMEOUT,
            )
            timeout = DEFAULT_MESHTASTIC_TIMEOUT
    except (TypeError, ValueError):
        # None or invalid value - use default silently
        if timeout_raw is not None:
            facade.logger.warning(
                "Invalid meshtastic.timeout value %r; using %ss fallback.",
                timeout_raw,
                DEFAULT_MESHTASTIC_TIMEOUT,
            )
        timeout = DEFAULT_MESHTASTIC_TIMEOUT
    configured_timeout_secs = float(timeout)
    configured_timeout_arg = max(1, math.ceil(configured_timeout_secs))
    create_timeout_floor_secs = facade._coerce_positive_float(
        facade._ble_interface_create_timeout_secs,
        BLE_INTERFACE_CREATE_TIMEOUT_FLOOR_SECS,
        "_ble_interface_create_timeout_secs",
    )
    # Keep the BLE constructor watchdog independent from generic operation timeout.
    # meshtastic.timeout defaults to 300s and is used for mesh operations; reusing it
    # here can stall startup for minutes when BlueZ/DBus creation hangs.
    ble_create_timeout_secs = create_timeout_floor_secs

    while (
        not successful
        and (retry_limit == 0 or attempts <= retry_limit)
        and not facade.shutting_down
    ):
        # Initialize before try block to avoid unbound variable errors
        ble_address: str | None = None
        supports_auto_reconnect = False
        fallback_to_compat_mode = False
        ble_connect_timeout_logged_for_attempt = False
        startup_drain_pending_for_this_connect = False
        startup_drain_armed_for_this_connect = False
        startup_drain_applied_for_this_connect = False
        reconnect_bootstrap_armed_for_this_connect = False
        signal_startup_drain_complete_for_this_connect = False
        client_assigned_for_this_connect = False

        client = None
        try:
            if connection_type == CONNECTION_TYPE_SERIAL:
                # Serial connection
                serial_port = facade.config["meshtastic"].get(CONFIG_KEY_SERIAL_PORT)
                if not serial_port:
                    facade.logger.error(
                        "No serial port specified in Meshtastic configuration."
                    )
                    return None

                facade.logger.info(f"Connecting to serial port {serial_port}")

                # Check if serial port exists before connecting
                if not facade.serial_port_exists(serial_port):
                    raise facade.serial.SerialException(
                        f"Serial port {serial_port} does not exist."
                    )

                client = facade.meshtastic.serial_interface.SerialInterface(
                    serial_port, timeout=configured_timeout_arg
                )

            elif connection_type == CONNECTION_TYPE_BLE:
                # BLE connection
                ble_address = facade.config["meshtastic"].get(CONFIG_KEY_BLE_ADDRESS)
                if ble_address:
                    facade.logger.info(f"Connecting to BLE address {ble_address}")

                    iface = None
                    supports_auto_reconnect = False
                    late_creation_disposer_future: Future[Any] | None = None
                    with facade.meshtastic_iface_lock:
                        # If BLE address has changed, re-create the interface
                        if (
                            facade.meshtastic_iface
                            and not facade._validate_ble_connection_address(
                                facade.meshtastic_iface, ble_address
                            )
                        ):
                            old_address = getattr(
                                facade.meshtastic_iface, "address", "unknown"
                            )
                            facade.logger.info(
                                f"BLE address has changed from {old_address} to {ble_address}. "
                                "Disconnecting old interface and creating new one."
                            )
                            # Properly disconnect the old interface to ensure sequential connections
                            facade._disconnect_ble_interface(
                                facade.meshtastic_iface, reason="address change"
                            )
                            facade.meshtastic_iface = None

                        if facade.meshtastic_iface is None:
                            # Disconnect any stale BlueZ connection before creating new interface
                            facade._disconnect_ble_by_address(ble_address)

                            # Create a single BLEInterface instance for process lifetime
                            sanitized_address = facade._sanitize_ble_address(
                                ble_address
                            )
                            facade.logger.debug(
                                f"Creating new BLE interface for {ble_address} (sanitized: {sanitized_address})"
                            )
                            ble_interface_cls = (
                                facade.meshtastic.ble_interface.BLEInterface
                            )  # pyright: ignore[reportPrivateImportUsage]
                            # Detect whether this BLEInterface implementation supports
                            # explicit auto_reconnect control.
                            try:
                                ble_init_sig = inspect.signature(
                                    ble_interface_cls.__init__
                                )
                            except (TypeError, ValueError):
                                ble_init_sig = None
                                fallback_to_compat_mode = True
                                facade.logger.debug(
                                    "BLEInterface signature unavailable; using compatibility mode"
                                )
                            create_timeout_secs = ble_create_timeout_secs
                            create_timeout_arg = max(1, math.ceil(create_timeout_secs))
                            ble_kwargs = {
                                "address": ble_address,
                                "noProto": False,
                                "debugOut": None,
                                "noNodes": False,
                                # Constructor timeout sent to BLEInterface. Keep this separate
                                # from the outer watchdog timeout used by mmrelay.
                                "timeout": create_timeout_arg,
                            }

                            # Configure auto_reconnect only when supported.
                            supports_auto_reconnect = (
                                ble_init_sig is not None
                                and "auto_reconnect" in ble_init_sig.parameters
                            )
                            if supports_auto_reconnect:
                                ble_kwargs["auto_reconnect"] = False
                                # Auto-reconnect-capable interfaces (for example mtjk)
                                # may perform staged direct/discovery connect work inside
                                # __init__. Keep constructor timeout bounded, but allow a
                                # bounded extra watchdog budget so interface creation can
                                # complete without false-positive worker timeouts.
                                #
                                # Some implementations can spend additional time after the
                                # constructor timeout budget while finalizing BLE state, so
                                # enforce at least one BLE connect-timeout window of slack.
                                create_timeout_secs = max(
                                    create_timeout_secs,
                                    float(
                                        create_timeout_arg
                                        + facade.BLE_INTERFACE_CREATE_GRACE_SECS
                                    ),
                                    float(
                                        create_timeout_arg
                                        + facade.BLE_CONNECT_TIMEOUT_SECS
                                    ),
                                )
                                effective_grace_secs = max(
                                    0.0,
                                    create_timeout_secs - float(create_timeout_arg),
                                )
                                facade.logger.debug(
                                    "BLEInterface supports auto_reconnect; setting auto_reconnect=False "
                                    "to ensure sequential reconnection control"
                                )
                                facade.logger.debug(
                                    "Using BLE interface creation watchdog %.1fs for %s "
                                    "(constructor timeout=%ss, connect-grace=%ss)",
                                    create_timeout_secs,
                                    ble_address,
                                    create_timeout_arg,
                                    effective_grace_secs,
                                )
                            else:
                                facade.logger.debug(
                                    "BLEInterface auto_reconnect parameter not available; using compatibility mode"
                                )

                            # Create BLE interface with timeout protection to prevent indefinite hangs
                            # Use ThreadPoolExecutor to run with timeout, as BLEInterface.__init__
                            # can potentially block indefinitely if BlueZ is in a bad state.
                            def create_ble_interface(
                                kwargs: dict[str, Any],
                            ) -> Any:
                                """
                                Create a BLEInterface configured for Meshtastic BLE connections.

                                Parameters:
                                    kwargs (dict): Keyword arguments forwarded to the Meshtastic BLEInterface constructor (e.g., `address`, `adapter`, `auto_reconnect`, `timeout`). Valid keys depend on the Meshtastic BLEInterface implementation.

                                Returns:
                                    BLEInterface: A newly constructed Meshtastic BLEInterface instance.
                                """
                                return ble_interface_cls(**kwargs)

                            # Guard against overlapping BLE tasks: if a previous BLE operation is
                            # still running (often due to a hung BlueZ/DBus call), we skip queuing
                            # a new task. Raising TimeoutError here intentionally reuses the
                            # existing retry/backoff logic rather than silently proceeding.
                            if facade.shutting_down:
                                facade.logger.debug(
                                    "Skipping BLE interface creation for %s (shutting down)",
                                    ble_address,
                                )
                                raise TimeoutError(
                                    f"BLE interface creation cancelled for {ble_address} (shutting down)."
                                )

                            facade._ensure_ble_worker_available(
                                ble_address,
                                operation="interface creation",
                            )

                            future: Future[Any] | None = None
                            try:
                                with facade._ble_executor_lock:
                                    if (
                                        facade._ble_future
                                        and not facade._ble_future.done()
                                    ):
                                        facade.logger.debug(
                                            "BLE worker busy; skipping interface creation for %s",
                                            ble_address,
                                        )
                                        raise TimeoutError(
                                            f"BLE interface creation already in progress for {ble_address}."
                                        )
                                    if (
                                        ble_address
                                        in facade._ble_executor_degraded_addresses
                                    ):
                                        facade.logger.error(
                                            "BLE executor degraded for %s: too many orphaned workers. "
                                            "Reconnect or restart required to restore BLE connectivity.",
                                            ble_address,
                                        )
                                        raise facade.BleExecutorDegradedError(
                                            f"BLE executor degraded for {ble_address}; reset required"
                                        )
                                    try:
                                        future = facade._get_ble_executor().submit(
                                            create_ble_interface, ble_kwargs
                                        )
                                    except RuntimeError as exc:
                                        # The shared executor can be shutting down during interpreter
                                        # teardown; treat this as a timeout so retry logic applies.
                                        facade.logger.exception(
                                            "BLE interface creation submission failed for %s",
                                            ble_address,
                                        )
                                        raise TimeoutError(
                                            f"BLE interface creation could not be scheduled for {ble_address}."
                                        ) from exc
                                    facade._ble_future = future
                                    facade._ble_future_address = ble_address
                                    facade._ble_future_started_at = (
                                        facade.time.monotonic()
                                    )
                                    facade._ble_future_timeout_secs = (
                                        create_timeout_secs
                                    )
                                future.add_done_callback(facade._clear_ble_future)
                                try:
                                    facade.meshtastic_iface = (
                                        facade._wait_for_future_result_with_shutdown(
                                            future,
                                            timeout_seconds=create_timeout_secs,
                                        )
                                    )
                                    if facade.meshtastic_iface is None:
                                        facade._clear_ble_future(future)
                                        raise RuntimeError(
                                            "BLE interface creation returned no interface"
                                        )
                                    facade.logger.debug(
                                        f"BLE interface created successfully for {ble_address}"
                                    )
                                    if not fallback_to_compat_mode and hasattr(
                                        facade.meshtastic_iface, "auto_reconnect"
                                    ):
                                        supports_auto_reconnect = True
                                    else:
                                        if fallback_to_compat_mode:
                                            facade.logger.debug(
                                                "Keeping BLE interface in compatibility mode for %s "
                                                "(signature introspection unavailable).",
                                                ble_address,
                                            )
                                        supports_auto_reconnect = False
                                        facade.reset_executor_degraded_state(
                                            ble_address=ble_address
                                        )
                                except facade.FuturesTimeoutError as err:
                                    facade.logger.error(
                                        "BLE interface creation timed out after %.1f seconds for %s.",
                                        create_timeout_secs,
                                        ble_address,
                                        exc_info=True,
                                    )
                                    facade.logger.warning(
                                        "This may indicate a stale BlueZ connection or Bluetooth adapter issue."
                                    )
                                    facade.logger.warning(
                                        BLE_TROUBLESHOOTING_GUIDANCE.format(
                                            ble_address=ble_address
                                        )
                                    )
                                    # Best-effort cancellation: if the worker is hung we cannot force
                                    # it to stop, but this signals intent and lets retries proceed
                                    # only if the future transitions to done/cancelled.
                                    if future is not None and future.cancel():
                                        facade._clear_ble_future(future)
                                    elif future is not None:
                                        facade._schedule_ble_future_cleanup(
                                            future,
                                            ble_address,
                                            reason="interface creation timeout",
                                        )
                                        late_creation_disposer_future = future
                                        timeout_count = facade._record_ble_timeout(
                                            ble_address
                                        )
                                        facade._maybe_reset_ble_executor(
                                            ble_address, timeout_count
                                        )
                                    facade.meshtastic_iface = None
                                    raise TimeoutError(
                                        f"BLE connection attempt timed out for {ble_address}."
                                    ) from err
                            except TimeoutError as err:
                                if (
                                    facade.shutting_down
                                    or str(err) == "Shutdown in progress"
                                ):
                                    if future is not None and future.cancel():
                                        facade._clear_ble_future(future)
                                    elif future is not None:
                                        facade._schedule_ble_future_cleanup(
                                            future,
                                            ble_address,
                                            reason="interface creation shutdown cancellation",
                                        )
                                        facade._attach_late_ble_interface_disposer(
                                            future,
                                            ble_address,
                                            reason="interface creation shutdown cancellation",
                                        )
                                    facade.meshtastic_iface = None
                                raise
                            except Exception as err:
                                # Late BLE worker failures can surface during shutdown
                                # after cancellation. Treat those as expected noise.
                                if facade.shutting_down:
                                    facade.logger.debug(
                                        "BLE interface creation ended during shutdown for %s",
                                        ble_address,
                                        exc_info=True,
                                    )
                                elif facade._is_ble_discovery_error(err):
                                    facade.logger.warning(
                                        "BLE interface creation transient failure for %s: %s",
                                        ble_address,
                                        err,
                                    )
                                    raise BLEDiscoveryTransientError(
                                        "BLE interface transient discovery/setup failure for "
                                        f"{ble_address}: {err}"
                                    ) from err
                                else:
                                    facade.logger.exception(
                                        "BLE interface creation failed"
                                    )
                                raise
                        else:
                            facade.logger.debug(
                                f"Reusing existing BLE interface for {ble_address}"
                            )
                            existing_sig = None
                            try:
                                existing_sig = inspect.signature(
                                    type(facade.meshtastic_iface).__init__
                                )
                            except (TypeError, ValueError):
                                fallback_to_compat_mode = True
                                facade.logger.debug(
                                    "Reused BLEInterface signature unavailable; keeping compatibility mode"
                                )
                            if not fallback_to_compat_mode and hasattr(
                                facade.meshtastic_iface, "auto_reconnect"
                            ):
                                supports_auto_reconnect = True
                            else:
                                supports_auto_reconnect = (
                                    existing_sig is not None
                                    and "auto_reconnect" in existing_sig.parameters
                                )

                        iface = facade.meshtastic_iface

                    if late_creation_disposer_future is not None:
                        facade._attach_late_ble_interface_disposer(
                            late_creation_disposer_future,
                            ble_address,
                            reason="interface creation timeout",
                        )

                    # Connect outside singleton-creation lock to avoid blocking other threads.
                    # Interfaces that expose auto_reconnect support use explicit connect()
                    # here; compatibility mode relies on constructor-managed connection.
                    if (
                        iface is not None
                        and supports_auto_reconnect
                        and hasattr(iface, "connect")
                    ):
                        facade.logger.info(
                            f"Initiating BLE connection to {ble_address} (sequential mode)"
                        )

                        # Add timeout protection for connect() call to prevent indefinite hangs
                        # Use ThreadPoolExecutor with 30-second timeout (same as CONNECTION_TIMEOUT)
                        def connect_iface(iface_param: Any) -> None:
                            """
                            Establishes the given interface by invoking its no-argument `connect()` method.

                            Parameters:
                                iface_param (Any): An interface-like object whose `connect()` method will be called to open the underlying connection.
                            """
                            iface_param.connect()

                        # Check if shutting down before submitting connect() tasks
                        if facade.shutting_down:
                            facade.logger.debug(
                                "Skipping BLE connect() for %s (shutting down)",
                                ble_address,
                            )
                            raise TimeoutError(
                                f"BLE connect cancelled for {ble_address} (shutting down)."
                            )

                        facade._ensure_ble_worker_available(
                            ble_address,
                            operation="connect",
                        )

                        connect_future: Future[Any] | None = None
                        with facade._ble_executor_lock:
                            if facade._ble_future and not facade._ble_future.done():
                                facade.logger.debug(
                                    "BLE worker busy; skipping connect() for %s",
                                    ble_address,
                                )
                                raise TimeoutError(
                                    f"BLE connect already in progress for {ble_address}."
                                )
                            if ble_address in facade._ble_executor_degraded_addresses:
                                facade.logger.error(
                                    "BLE executor degraded for %s: too many orphaned workers. "
                                    "Reconnect or restart required to restore BLE connectivity.",
                                    ble_address,
                                )
                                raise facade.BleExecutorDegradedError(
                                    f"BLE executor degraded for {ble_address}; reset required"
                                )
                            try:
                                connect_future = facade._get_ble_executor().submit(
                                    connect_iface, iface
                                )
                            except RuntimeError as exc:
                                facade.logger.exception(
                                    "BLE connect() submission failed for %s",
                                    ble_address,
                                )
                                raise TimeoutError(
                                    f"BLE connect could not be scheduled for {ble_address}."
                                ) from exc
                            facade._ble_future = connect_future
                            facade._ble_future_address = ble_address
                            facade._ble_future_started_at = facade.time.monotonic()
                            facade._ble_future_timeout_secs = (
                                facade.BLE_CONNECT_TIMEOUT_SECS
                            )
                        connect_future.add_done_callback(facade._clear_ble_future)
                        try:
                            facade._wait_for_future_result_with_shutdown(
                                connect_future,
                                timeout_seconds=facade.BLE_CONNECT_TIMEOUT_SECS,
                            )
                            facade.logger.info(
                                f"BLE connection established to {ble_address}"
                            )
                            facade.reset_executor_degraded_state(
                                ble_address=ble_address
                            )
                        except (TimeoutError, facade.FuturesTimeoutError) as err:
                            if (
                                facade.shutting_down
                                or str(err) == "Shutdown in progress"
                            ):
                                facade.logger.debug(
                                    "BLE connect() interrupted by shutdown for %s",
                                    ble_address,
                                )
                                shutdown_iface = iface
                                if (
                                    connect_future is not None
                                    and connect_future.cancel()
                                ):
                                    facade._clear_ble_future(connect_future)
                                elif connect_future is not None:
                                    facade._schedule_ble_future_cleanup(
                                        connect_future,
                                        ble_address,
                                        reason="connect shutdown cancellation",
                                    )
                                    facade._attach_late_ble_interface_disposer(
                                        connect_future,
                                        ble_address,
                                        reason="connect shutdown cancellation",
                                        fallback_iface=shutdown_iface,
                                    )
                                iface = None
                                facade.meshtastic_iface = None
                            else:
                                # Use logger.exception so timeouts include stack context (TRY400),
                                # but raise a short error and keep operator guidance in logs (TRY003).
                                ble_connect_timeout_logged_for_attempt = True
                                facade.logger.exception(
                                    "BLE connect() call timed out after %s seconds for %s.",
                                    facade.BLE_CONNECT_TIMEOUT_SECS,
                                    ble_address,
                                )
                                facade.logger.warning(
                                    "This may indicate a BlueZ or adapter issue."
                                )
                                facade.logger.warning(
                                    f"BlueZ may be in a bad state. {facade.BLE_TROUBLESHOOTING_GUIDANCE.format(ble_address=ble_address)}"
                                )
                                # Best-effort cancellation: a hung BLE connect blocks the worker
                                # thread, so we cancel to allow retries only if it completes.
                                if (
                                    connect_future is not None
                                    and connect_future.cancel()
                                ):
                                    facade._clear_ble_future(connect_future)
                                elif connect_future is not None:
                                    timed_out_iface = iface
                                    # Clear global/local references before attaching late
                                    # disposer so late completions cannot observe stale active
                                    # globals and skip cleanup.
                                    iface = None
                                    facade.meshtastic_iface = None
                                    facade._schedule_ble_future_cleanup(
                                        connect_future,
                                        ble_address,
                                        reason="connect timeout",
                                    )
                                    facade._attach_late_ble_interface_disposer(
                                        connect_future,
                                        ble_address,
                                        reason="connect timeout",
                                        fallback_iface=timed_out_iface,
                                    )
                                    timeout_count = facade._record_ble_timeout(
                                        ble_address
                                    )
                                    facade._maybe_reset_ble_executor(
                                        ble_address, timeout_count
                                    )
                                # Don't use iface if connect() timed out - it may be in an inconsistent state
                                iface = None
                                facade.meshtastic_iface = None
                                raise TimeoutError(
                                    f"BLE connect() timed out for {ble_address}."
                                ) from err
                            raise
                    elif iface is not None and hasattr(iface, "connect"):
                        facade.logger.debug(
                            "Skipping explicit BLE connect in compatibility mode; "
                            "interface is expected to connect during initialization for %s",
                            ble_address,
                        )

                    client = iface
                else:
                    facade.logger.error("No BLE address provided.")
                    return None

            elif connection_type == CONNECTION_TYPE_TCP:
                # TCP connection
                target_host = facade.config["meshtastic"].get(CONFIG_KEY_HOST)
                if not target_host:
                    facade.logger.error(
                        "No host specified in Meshtastic configuration for TCP connection."
                    )
                    return None

                target_port = DEFAULT_TCP_PORT
                configured_port = facade.config["meshtastic"].get(CONFIG_KEY_PORT)
                if configured_port is not None:
                    try:
                        parsed_port = int(configured_port)
                        if parsed_port <= 0 or parsed_port > 65535:
                            raise ValueError
                        target_port = parsed_port
                    except (TypeError, ValueError):
                        facade.logger.warning(
                            "Invalid meshtastic.port value %r; using default TCP port %s",
                            configured_port,
                            DEFAULT_TCP_PORT,
                        )

                facade.logger.info(f"Connecting to host {target_host}:{target_port}")

                # Connect without progress indicator
                client = facade.meshtastic.tcp_interface.TCPInterface(
                    hostname=target_host,
                    portNumber=target_port,
                    timeout=configured_timeout_arg,
                )
            else:
                facade.logger.error(f"Unknown connection type: {connection_type}")
                return None

            if client is None:
                facade.logger.error(
                    "Meshtastic %s connection path completed without a client.",
                    connection_type,
                )
                _raise_no_client(connection_type)

            successful = True

            # Acquire lock only for the final setup and subscription
            with facade.meshtastic_lock:
                # CRITICAL VALIDATION: Verify we're connected to the correct BLE device.
                # This prevents connection to wrong device due to substring matching
                # bugs in meshtastic library's find_device() function. The official
                # version uses substring matching: `address in (device.name, device.address)`
                # which can match a non-target device if its name contains to
                # configured address as a substring.
                #
                # Example vulnerability scenario:
                # - Configured address: AA:BB:CC:DD:EE:FF (Meshtastic device)
                # - Nearby device name: "AA:BB:CC:DD:EE:FF-Sensor" (car/handset/etc)
                # - Result: Bot incorrectly matches and connects to non-Meshtastic device
                #
                # This validation works with both official and forked meshtastic versions.
                # If validation fails, we disconnect immediately to prevent further issues.
                if connection_type == CONNECTION_TYPE_BLE:
                    expected_ble_address = facade.config["meshtastic"].get(
                        CONFIG_KEY_BLE_ADDRESS
                    )
                    if (
                        expected_ble_address
                        and not facade._validate_ble_connection_address(
                            client, expected_ble_address
                        )
                    ):
                        # Validation failed - wrong device connected
                        # Disconnect immediately to prevent communication with wrong device
                        facade.logger.error(
                            "BLE connection validation failed - connected to wrong device. "
                            "Disconnecting and raising error to force retry."
                        )
                        was_shared_interface = client is facade.meshtastic_iface
                        try:
                            if was_shared_interface:
                                facade._disconnect_ble_interface(
                                    facade.meshtastic_iface,
                                    reason="address validation failed",
                                )
                            else:
                                client.close()
                        except Exception as e:
                            facade.logger.warning(
                                f"Error closing invalid BLE connection: {e}"
                            )
                        finally:
                            if was_shared_interface:
                                facade.meshtastic_iface = None
                        raise ConnectionRefusedError(
                            f"Connected to wrong BLE device. Expected: {expected_ble_address}"
                        )

                # Clear health probe deadlines BEFORE resetting clock skew to prevent
                # race condition where a late ACK from the previous interface could
                # seed the new connection with stale skew values.
                #
                # Initialize connection timing state exactly once before metadata
                # probes, node-info fetches, and subscription setup so reconnects
                # cannot handle inbound packets with stale session timing.
                with facade._health_probe_request_lock:
                    facade._health_probe_request_deadlines.clear()
                    with facade._relay_rx_time_clock_skew_lock:
                        facade.RELAY_START_TIME = facade.time.time()
                        facade._relay_connection_started_monotonic_secs = (
                            facade.time.monotonic()
                        )
                        facade._relay_rx_time_clock_skew_secs = None
                        if not facade._startup_packet_drain_applied:
                            facade._relay_startup_drain_deadline_monotonic_secs = None
                            facade._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = (
                                None
                            )
                            startup_drain_pending_for_this_connect = True
                            timing_mode = "startup_pending"
                        else:
                            existing_deadline = (
                                facade._relay_startup_drain_deadline_monotonic_secs
                            )
                            active_startup_drain = (
                                existing_deadline is not None
                                and existing_deadline > facade.time.monotonic()
                            )
                            if active_startup_drain:
                                facade._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = (
                                    facade._relay_connection_started_monotonic_secs
                                    + facade.RECONNECT_PRESTART_BOOTSTRAP_WINDOW_SECS
                                )
                                reconnect_bootstrap_armed_for_this_connect = True
                                timing_mode = "startup_pending_reconnect"
                            else:
                                facade._relay_startup_drain_deadline_monotonic_secs = (
                                    None
                                )
                                facade._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = (
                                    facade._relay_connection_started_monotonic_secs
                                    + facade.RECONNECT_PRESTART_BOOTSTRAP_WINDOW_SECS
                                )
                                signal_startup_drain_complete_for_this_connect = True
                                reconnect_bootstrap_armed_for_this_connect = True
                                timing_mode = "reconnect"
                        facade.logger.debug(
                            "Initialized connection timing state mode=%s start=%.3f monotonic_start=%.3f startup_drain_deadline=%s reconnect_bootstrap_deadline=%s",
                            timing_mode,
                            facade.RELAY_START_TIME,
                            facade._relay_connection_started_monotonic_secs,
                            facade._relay_startup_drain_deadline_monotonic_secs,
                            facade._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs,
                        )
                if signal_startup_drain_complete_for_this_connect:
                    # Signal completion after clock-skew state updates are committed.
                    startup_drain_complete_event = (
                        facade.get_startup_drain_complete_event()
                    )
                    if startup_drain_complete_event is not None:
                        startup_drain_complete_event.set()

                # Publish the active client only after per-connection timing state
                # has been reset, so callbacks cannot observe stale skew windows.
                if facade.shutting_down:
                    facade.logger.debug(
                        "Shutdown started during connect setup; closing new client before publish"
                    )
                    try:
                        if client is facade.meshtastic_iface:
                            facade._disconnect_ble_interface(
                                facade.meshtastic_iface,
                                reason="connect setup cancelled by shutdown",
                            )
                            facade.meshtastic_iface = None
                        else:
                            client.close()
                    except Exception as cleanup_error:  # noqa: BLE001 - best effort
                        facade.logger.warning(
                            "Error closing Meshtastic client during shutdown race: %s",
                            cleanup_error,
                        )
                    with facade._relay_rx_time_clock_skew_lock:
                        if reconnect_bootstrap_armed_for_this_connect:
                            facade._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = (
                                None
                            )
                    successful = False
                    return None

                facade.meshtastic_client = client
                facade._relay_active_client_id = id(client)
                client_assigned_for_this_connect = True

                node_info = client.getMyNodeInfo()

                if facade.shutting_down:
                    facade.logger.debug(
                        "Shutdown started during connect setup (after getMyNodeInfo); rolling back client"
                    )
                    client_assigned_for_this_connect = facade._rollback_connect_attempt_state(
                        client=client,
                        client_assigned_for_this_connect=client_assigned_for_this_connect,
                        startup_drain_armed_for_this_connect=startup_drain_armed_for_this_connect,
                        startup_drain_applied_for_this_connect=startup_drain_applied_for_this_connect,
                        reconnect_bootstrap_armed_for_this_connect=reconnect_bootstrap_armed_for_this_connect,
                        lock_held=True,
                    )
                    successful = False
                    return None

                # Safely access node info fields
                user_info = node_info.get("user", {}) if node_info else {}
                short_name = user_info.get("shortName", "unknown")
                hw_model = user_info.get("hwModel", "unknown")

                # Get firmware version from device metadata
                metadata = facade._get_device_metadata(client)
                firmware_version = metadata["firmware_version"]

                if facade.shutting_down:
                    facade.logger.debug(
                        "Shutdown started during connect setup (after metadata); rolling back client"
                    )
                    client_assigned_for_this_connect = facade._rollback_connect_attempt_state(
                        client=client,
                        client_assigned_for_this_connect=client_assigned_for_this_connect,
                        startup_drain_armed_for_this_connect=startup_drain_armed_for_this_connect,
                        startup_drain_applied_for_this_connect=startup_drain_applied_for_this_connect,
                        reconnect_bootstrap_armed_for_this_connect=reconnect_bootstrap_armed_for_this_connect,
                        lock_held=True,
                    )
                    successful = False
                    return None

                if metadata.get("success"):
                    facade.logger.info(
                        f"Connected to {short_name} / {hw_model} / Meshtastic Firmware version {firmware_version}"
                    )
                else:
                    facade.logger.info(f"Connected to {short_name} / {hw_model}")
                    facade.logger.debug(
                        "Device firmware version unavailable from getMetadata()"
                    )

                # Arm startup drain only once setup completes, so the full drain
                # interval is available when receive handling becomes active.
                startup_drain_deadline: float | None = None
                clear_startup_drain_complete_event = False
                if startup_drain_pending_for_this_connect:
                    with facade._relay_rx_time_clock_skew_lock:
                        if not facade._startup_packet_drain_applied:
                            startup_drain_deadline = (
                                facade.time.monotonic()
                                + facade.STARTUP_PACKET_DRAIN_SECS
                            )
                            facade._relay_startup_drain_deadline_monotonic_secs = (
                                startup_drain_deadline
                            )
                            facade._startup_packet_drain_applied = True
                            startup_drain_applied_for_this_connect = True
                            startup_drain_armed_for_this_connect = True
                            clear_startup_drain_complete_event = True
                    if clear_startup_drain_complete_event:
                        # Keep lock scope focused on shared timing state updates.
                        startup_drain_complete_event = (
                            facade.get_startup_drain_complete_event()
                        )
                        if startup_drain_complete_event is not None:
                            startup_drain_complete_event.clear()
                    if (
                        startup_drain_armed_for_this_connect
                        and startup_drain_deadline is not None
                    ):
                        facade._schedule_startup_drain_deadline_cleanup(
                            startup_drain_deadline
                        )
                        facade.logger.debug(
                            "Armed startup drain window deadline=%s after setup completion",
                            startup_drain_deadline,
                        )

                if facade.shutting_down:
                    facade.logger.debug(
                        "Shutdown started before callback subscription; rolling back client"
                    )
                    client_assigned_for_this_connect = facade._rollback_connect_attempt_state(
                        client=client,
                        client_assigned_for_this_connect=client_assigned_for_this_connect,
                        startup_drain_armed_for_this_connect=startup_drain_armed_for_this_connect,
                        startup_drain_applied_for_this_connect=startup_drain_applied_for_this_connect,
                        reconnect_bootstrap_armed_for_this_connect=reconnect_bootstrap_armed_for_this_connect,
                        lock_held=True,
                    )
                    successful = False
                    return None

                # Subscribe to message and connection-lost events.
                facade.ensure_meshtastic_callbacks_subscribed()

                facade._schedule_connect_time_calibration_probe(
                    client,
                    connection_type=connection_type,
                    active_config=facade.config,
                )

        except ConnectionRefusedError:
            facade._rollback_connect_attempt_state(
                client=client,
                client_assigned_for_this_connect=client_assigned_for_this_connect,
                startup_drain_armed_for_this_connect=startup_drain_armed_for_this_connect,
                startup_drain_applied_for_this_connect=startup_drain_applied_for_this_connect,
                reconnect_bootstrap_armed_for_this_connect=reconnect_bootstrap_armed_for_this_connect,
            )
            client_assigned_for_this_connect = False
            successful = False
            return None
        except (MemoryError, facade.BleExecutorDegradedError):
            facade._rollback_connect_attempt_state(
                client=client,
                client_assigned_for_this_connect=client_assigned_for_this_connect,
                startup_drain_armed_for_this_connect=startup_drain_armed_for_this_connect,
                startup_drain_applied_for_this_connect=startup_drain_applied_for_this_connect,
                reconnect_bootstrap_armed_for_this_connect=reconnect_bootstrap_armed_for_this_connect,
            )
            client_assigned_for_this_connect = False
            successful = False
            facade.logger.exception("Critical connection error")
            return None
        except BLEDiscoveryTransientError as e:
            successful = False
            client_assigned_for_this_connect = facade._rollback_connect_attempt_state(
                client=client,
                client_assigned_for_this_connect=client_assigned_for_this_connect,
                startup_drain_armed_for_this_connect=startup_drain_armed_for_this_connect,
                startup_drain_applied_for_this_connect=startup_drain_applied_for_this_connect,
                reconnect_bootstrap_armed_for_this_connect=reconnect_bootstrap_armed_for_this_connect,
            )
            if facade.shutting_down:
                break
            attempts += 1
            if retry_limit == facade.INFINITE_RETRIES or attempts <= retry_limit:
                wait_time = facade._get_connection_retry_wait_time(attempts)
                facade.logger.warning(
                    "Connection attempt %s hit transient BLE discovery/setup failure (%s). Retrying in %s seconds...",
                    attempts,
                    e,
                    wait_time,
                )
                facade.time.sleep(wait_time)
            else:
                facade.logger.exception("Connection failed after %s attempts", attempts)
                return None
        except (facade.FuturesTimeoutError, TimeoutError) as e:
            successful = False
            client_assigned_for_this_connect = facade._rollback_connect_attempt_state(
                client=client,
                client_assigned_for_this_connect=client_assigned_for_this_connect,
                startup_drain_armed_for_this_connect=startup_drain_armed_for_this_connect,
                startup_drain_applied_for_this_connect=startup_drain_applied_for_this_connect,
                reconnect_bootstrap_armed_for_this_connect=reconnect_bootstrap_armed_for_this_connect,
            )
            if facade.shutting_down:
                break
            if (
                connection_type == CONNECTION_TYPE_BLE
                and ble_address
                and not ble_connect_timeout_logged_for_attempt
                and str(e).startswith("BLE connect() timed out for ")
            ):
                facade.logger.exception(
                    "BLE connect() call timed out after %s seconds for %s.",
                    facade.BLE_CONNECT_TIMEOUT_SECS,
                    ble_address,
                )
                facade.logger.warning("This may indicate a BlueZ or adapter issue.")
                facade.logger.warning(
                    f"BlueZ may be in a bad state. {facade.BLE_TROUBLESHOOTING_GUIDANCE.format(ble_address=ble_address)}"
                )
            attempts += 1
            if retry_limit == facade.INFINITE_RETRIES:
                timeout_attempts += 1
                if timeout_attempts > facade.MAX_TIMEOUT_RETRIES_INFINITE:
                    facade.logger.exception(
                        "Connection timed out after %s attempts (unlimited retries); aborting",
                        attempts,
                    )
                    return None
            elif attempts > retry_limit:
                facade.logger.exception("Connection failed after %s attempts", attempts)
                return None

            wait_time = facade._get_connection_retry_wait_time(attempts)
            facade.logger.warning(
                "Connection attempt %s timed out (%s). Retrying in %s seconds...",
                attempts,
                e,
                wait_time,
            )
            facade.time.sleep(wait_time)
        except Exception as e:
            successful = False
            client_assigned_for_this_connect = facade._rollback_connect_attempt_state(
                client=client,
                client_assigned_for_this_connect=client_assigned_for_this_connect,
                startup_drain_armed_for_this_connect=startup_drain_armed_for_this_connect,
                startup_drain_applied_for_this_connect=startup_drain_applied_for_this_connect,
                reconnect_bootstrap_armed_for_this_connect=reconnect_bootstrap_armed_for_this_connect,
            )
            if facade.shutting_down:
                facade.logger.debug(
                    "Shutdown in progress. Aborting connection attempts."
                )
                break
            if (
                connection_type == CONNECTION_TYPE_BLE
                and ble_address
                and facade._is_ble_duplicate_connect_suppressed_error(e)
            ):
                facade.logger.warning(
                    "Detected duplicate BLE connect suppression for %s",
                    ble_address,
                )
                if not facade._reset_ble_connection_gate_state(
                    ble_address,
                    reason="duplicate connect suppression",
                ):
                    facade.logger.debug(
                        "BLE gate reset hook unavailable for %s; retrying without local reset",
                        ble_address,
                    )
            attempts += 1
            if retry_limit == 0 or attempts <= retry_limit:
                wait_time = facade._get_connection_retry_wait_time(attempts)
                facade.logger.warning(
                    "An unexpected error occurred on attempt %s: %s. Retrying in %s seconds...",
                    attempts,
                    e,
                    wait_time,
                )
                facade.time.sleep(wait_time)
            else:
                facade.logger.exception("Connection failed after %s attempts", attempts)
                return None

    return facade.meshtastic_client
