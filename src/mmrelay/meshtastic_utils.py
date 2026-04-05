import asyncio
import atexit
import contextlib
import functools
import importlib
import importlib.util
import inspect
import io
import logging
import math
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Awaitable, Callable, Coroutine, cast

# meshtastic is not marked py.typed; keep import-untyped for strict mypy.
import meshtastic
import meshtastic.ble_interface
import meshtastic.serial_interface
import meshtastic.tcp_interface
import serial  # For serial port exceptions
import serial.tools.list_ports  # Import serial tools for port listing
from meshtastic.protobuf import admin_pb2, mesh_pb2, portnums_pb2
from pubsub import pub
from pubsub.core.topicexc import TopicNameError

from mmrelay.config import get_meshtastic_config_value
from mmrelay.constants.config import (
    CONFIG_KEY_CONNECT_PROBE_ENABLED,
    CONFIG_KEY_ENABLED,
    CONFIG_KEY_HEALTH_CHECK,
    CONFIG_KEY_MESHNET_NAME,
    CONFIG_KEY_NODEDB_REFRESH_INTERVAL,
    CONFIG_KEY_PROBE_TIMEOUT,
    CONFIG_SECTION_MESHTASTIC,
    DEFAULT_DETECTION_SENSOR,
    DEFAULT_HEALTH_CHECK_ENABLED,
    DEFAULT_NODEDB_REFRESH_INTERVAL,
)
from mmrelay.constants.database import PROTO_NODE_NAME_LONG, PROTO_NODE_NAME_SHORT
from mmrelay.constants.domain import METADATA_OUTPUT_MAX_LENGTH
from mmrelay.constants.formats import (
    DEFAULT_TEXT_ENCODING,
    DETECTION_SENSOR_APP,
    EMOJI_FLAG_VALUE,
    ENCODING_ERROR_IGNORE,
    FIRMWARE_VERSION_REGEX,
    TEXT_MESSAGE_APP,
)
from mmrelay.constants.messages import (
    DEFAULT_CHANNEL_VALUE,
    PORTNUM_DETECTION_SENSOR_APP,
    PORTNUM_TEXT_MESSAGE_APP,
)
from mmrelay.constants.network import (
    ACK_POLL_INTERVAL_SECS,
    BLE_CONN_SUPPRESSED_TOKEN,
    BLE_CONNECT_TIMEOUT_SECS,
    BLE_CONNECTED_ELSEWHERE_TOKEN,
    BLE_DISCONNECT_MAX_RETRIES,
    BLE_DISCONNECT_SETTLE_SECS,
    BLE_DISCONNECT_TIMEOUT_SECS,
    BLE_DUP_CONNECT_SUPPRESSED_TOKEN,
    BLE_FUTURE_STALE_GRACE_SECS,
    BLE_FUTURE_WATCHDOG_SECS,
    BLE_INTERFACE_CREATE_TIMEOUT_FLOOR_SECS,
    BLE_RETRY_DELAY_SECS,
    BLE_SCAN_TIMEOUT_SECS,
    BLE_TIMEOUT_RESET_THRESHOLD,
    BLE_TROUBLESHOOTING_GUIDANCE,
    CONFIG_KEY_BLE_ADDRESS,
    CONFIG_KEY_CONNECTION_TYPE,
    CONFIG_KEY_HOST,
    CONFIG_KEY_PORT,
    CONFIG_KEY_SERIAL_PORT,
    CONFIG_KEY_TIMEOUT,
    CONNECTION_RETRY_BACKOFF_BASE,
    CONNECTION_RETRY_BACKOFF_MAX_SECS,
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_NETWORK,
    CONNECTION_TYPE_SERIAL,
    CONNECTION_TYPE_TCP,
    DEFAULT_BACKOFF_TIME,
    DEFAULT_HEARTBEAT_INTERVAL_SECS,
    DEFAULT_MESHTASTIC_OPERATION_TIMEOUT,
    DEFAULT_MESHTASTIC_TIMEOUT,
    DEFAULT_PLUGIN_TIMEOUT_SECS,
    DEFAULT_TCP_PORT,
    ERRNO_BAD_FILE_DESCRIPTOR,
    EXECUTOR_ORPHAN_THRESHOLD,
    FUTURE_CANCEL_TIMEOUT_SECS,
    HEALTH_PROBE_TRACK_GRACE_SECS,
    INFINITE_RETRIES,
    INITIAL_HEALTH_CHECK_DELAY,
    MAX_TIMEOUT_RETRIES_INFINITE,
    MESHTASTIC_BLE_GATE_RESET_FUNC,
    MESHTASTIC_BLE_GATING_MODULE_PATH,
    METADATA_WATCHDOG_SECS,
    RECONNECT_PRESTART_BOOTSTRAP_WINDOW_SECS,
    RX_TIME_SKEW_BOOTSTRAP_MAX_SKEW_SECS,
    RX_TIME_SKEW_BOOTSTRAP_WINDOW_SECS,
    STALE_DISCONNECT_TIMEOUT_SECS,
    STARTUP_PACKET_DRAIN_SECS,
)
from mmrelay.db_utils import (
    NodeNameState,
    get_longname,
    get_message_map_by_meshtastic_id,
    get_shortname,
    save_longname,
    save_shortname,
    sync_name_tables_if_changed,
)
from mmrelay.log_utils import get_logger
from mmrelay.meshtastic.async_utils import (
    _coerce_bool,
    _coerce_int_id,
    _coerce_nonnegative_float,
    _coerce_positive_float,
    _coerce_positive_int,
    _coerce_positive_int_id,
    _fire_and_forget,
    _make_awaitable,
    _run_blocking_with_timeout,
    _submit_coro,
    _wait_for_future_result_with_shutdown,
    _wait_for_result,
)
from mmrelay.meshtastic.ble import (
    _attach_late_ble_interface_disposer,
    _disconnect_ble_by_address,
    _disconnect_ble_interface,
    _is_ble_discovery_error,
    _is_ble_duplicate_connect_suppressed_error,
    _reset_ble_connection_gate_state,
    _sanitize_ble_address,
    _scan_for_ble_address,
    _validate_ble_connection_address,
)
from mmrelay.meshtastic.connection import (
    _connect_meshtastic_impl,
    _get_connect_time_probe_settings,
    _get_connection_retry_wait_time,
    _rollback_connect_attempt_state,
    _schedule_connect_time_calibration_probe,
    connect_meshtastic,
    serial_port_exists,
)
from mmrelay.meshtastic.events import (
    on_lost_meshtastic_connection,
    on_meshtastic_message,
    reconnect,
)
from mmrelay.meshtastic.executors import (
    _clear_ble_future,
    _clear_metadata_future_if_current,
    _ensure_ble_worker_available,
    _get_ble_executor,
    _get_metadata_executor,
    _maybe_reset_ble_executor,
    _record_ble_timeout,
    _reset_metadata_executor_for_stale_probe,
    _schedule_ble_future_cleanup,
    _schedule_metadata_future_cleanup,
    _shutdown_shared_executors,
    _submit_metadata_probe,
    reset_executor_degraded_state,
    shutdown_shared_executors,
)
from mmrelay.meshtastic.health import (
    _claim_health_probe_response_and_maybe_calibrate,
    _extract_packet_request_id,
    _failed_probe_ack_state_error,
    _handle_probe_ack_callback,
    _is_health_probe_response_packet,
    _metadata_probe_ack_timeout_error,
    _missing_ack_state_error,
    _missing_local_node_ack_state_error,
    _missing_probe_transport_error,
    _missing_probe_wait_error,
    _missing_received_nak_error,
    _probe_device_connection,
    _prune_health_probe_tracking,
    _reset_probe_ack_state,
    _seed_connect_time_skew,
    _set_probe_ack_flag_from_packet,
    _track_health_probe_request_id,
    _wait_for_probe_ack,
    requires_continuous_health_monitor,
)
from mmrelay.meshtastic.messaging import (
    _get_node_display_name,
    _get_packet_details,
    _get_portnum_name,
    _normalize_room_channel,
    send_text_reply,
    sendTextReply,
)
from mmrelay.meshtastic.node_refresh import (
    _parse_refresh_interval_seconds,
    _snapshot_node_name_rows,
    get_nodedb_refresh_interval_seconds,
    refresh_node_name_tables,
)
from mmrelay.meshtastic.plugins import (
    _resolve_plugin_result,
    _resolve_plugin_timeout,
    _run_meshtastic_plugins,
)
from mmrelay.meshtastic.subscriptions import (
    ensure_meshtastic_callbacks_subscribed,
    unsubscribe_meshtastic_callbacks,
)
from mmrelay.runtime_utils import is_running_as_service

try:
    BLE_AVAILABLE = importlib.util.find_spec("bleak") is not None
except ValueError:
    BLE_AVAILABLE = "bleak" in sys.modules


# Import BLE exceptions conditionally
try:
    from bleak.exc import BleakDBusError, BleakError
except ImportError:
    BleakDBusError = Exception  # type: ignore[misc,assignment]
    BleakError = Exception  # type: ignore[misc,assignment]


class BleExecutorDegradedError(Exception):
    """Raised when a BLE address has too many orphaned workers and needs manual recovery."""

    pass


# Global config variable that will be set from config.py
config = None

# Do not import plugin_loader here to avoid circular imports

# Initialize matrix rooms configuration
matrix_rooms: list[dict[str, Any]] = []

# Initialize logger for Meshtastic
logger = get_logger(name="Meshtastic")

# Detect optional BLE connection-gate reset support once at startup.
# The legacy/official Meshtastic BLE implementation does not provide this.
_ble_gate_reset_callable: Callable[[], None] | None = None
_ble_gating_module: Any | None = None
try:
    _ble_gating_module = importlib.import_module(MESHTASTIC_BLE_GATING_MODULE_PATH)
except ModuleNotFoundError:
    _ble_gating_module = None
except Exception:  # noqa: BLE001 - defensive import of optional fork-specific feature
    _ble_gating_module = None
else:
    clear_all_registries = getattr(
        _ble_gating_module, MESHTASTIC_BLE_GATE_RESET_FUNC, None
    )
    if callable(clear_all_registries):
        _ble_gate_reset_callable = cast(Callable[[], None], clear_all_registries)

# Meshtastic text payloads are UTF-8 on the wire.
MESHTASTIC_TEXT_ENCODING = "utf-8"

# Session cutoff used to filter out backlog packets; reset on each new connection.
RELAY_START_TIME = time.time()
# Monotonic timestamp captured with RELAY_START_TIME to keep startup windows
# stable even if the system wall clock jumps during boot/time sync.
_relay_connection_started_monotonic_secs = time.monotonic()
# Per-connection rxTime clock skew, calibrated from tracked health-probe responses.
_relay_rx_time_clock_skew_secs: float | None = None
_relay_rx_time_clock_skew_lock = threading.Lock()
# Allow controlled skew bootstrap shortly after connect so startup can recover
# when host time and packet rxTime disagree before clock sync settles.
# Briefly drain inbound packets after connect to avoid relaying queued backlog
# while connection/session timing state settles.
_relay_startup_drain_deadline_monotonic_secs: float | None = None
# Only apply startup drain on the first successful process-lifetime connect.
_startup_packet_drain_applied = False
# On reconnects, allow exactly one bounded pre-start skew bootstrap packet
# without enabling a full reconnect drain window.
_relay_reconnect_prestart_bootstrap_deadline_monotonic_secs: float | None = None


# Global variables for the Meshtastic connection and event loop management
meshtastic_client = None
# Session guard to reject callbacks emitted by stale interfaces after reconnect.
_relay_active_client_id: int | None = None
meshtastic_iface = None  # BLE interface instance for process lifetime
event_loop = None  # Will be set from main.py

meshtastic_lock = (
    threading.Lock()
)  # To prevent race conditions on meshtastic_client access
# Serialize full connect attempt lifecycles so concurrent callers do not
# create overlapping clients/interfaces and race rollback cleanup.
_connect_attempt_lock = threading.RLock()
_connect_attempt_condition = threading.Condition(_connect_attempt_lock)
_connect_attempt_in_progress = False
_CONNECT_ATTEMPT_WAIT_POLL_SECS = 1.0
_CONNECT_ATTEMPT_WAIT_MAX_SECS = 5.0
_CONNECT_ATTEMPT_BLE_WAIT_MAX_SECS = (
    BLE_INTERFACE_CREATE_TIMEOUT_FLOOR_SECS + BLE_CONNECT_TIMEOUT_SECS
)

reconnecting = False
shutting_down = False

reconnect_task = None  # To keep track of the reconnect task
reconnect_task_future: asyncio.Future[Any] | None = None
meshtastic_iface_lock = (
    threading.Lock()
)  # To prevent race conditions on BLE interface singleton creation

# Subscription flags to prevent duplicate subscriptions
meshtastic_sub_lock = threading.Lock()
subscribed_to_messages = False
subscribed_to_connection_lost = False
# Guard for brief in-flight callback windows during explicit unsubscribe.
_callbacks_tearing_down = False


# Subscription lifecycle — implemented in meshtastic.subscriptions, re-exported here
# for backward-compatible patch targets (tests patch mmrelay.meshtastic_utils.*).

# Shared executor for getMetadata() to avoid leaking threads when metadata calls hang.
# A single worker is enough because getMetadata() is serialized by design.
_metadata_executor: ThreadPoolExecutor | None = ThreadPoolExecutor(max_workers=1)
_metadata_future: Future[Any] | None = None
_metadata_future_started_at: float | None = None
_metadata_future_lock = threading.Lock()
_metadata_executor_orphaned_workers = 0
_health_probe_request_deadlines: dict[int, float] = {}
_health_probe_request_lock = threading.Lock()

# Shared executor for BLE init/connect to avoid leaking threads across retries.
# BLE setup is inherently sequential, so a single worker keeps things predictable.
_ble_executor: ThreadPoolExecutor | None = ThreadPoolExecutor(max_workers=1)
_ble_executor_lock = threading.Lock()
_ble_future: Future[Any] | None = None
_ble_future_address: str | None = None
_ble_future_started_at: float | None = None
_ble_future_timeout_secs: float | None = None
_ble_timeout_counts: dict[str, int] = {}
_ble_executor_orphaned_workers_by_address: dict[str, int] = {}
_ble_timeout_lock = threading.Lock()
_ble_future_watchdog_secs = BLE_FUTURE_WATCHDOG_SECS
_ble_timeout_reset_threshold = BLE_TIMEOUT_RESET_THRESHOLD
_ble_scan_timeout_secs = BLE_SCAN_TIMEOUT_SECS
_ble_future_stale_grace_secs = BLE_FUTURE_STALE_GRACE_SECS
_ble_interface_create_timeout_secs = BLE_INTERFACE_CREATE_TIMEOUT_FLOOR_SECS

_ble_executor_degraded_addresses: set[str] = set()
_metadata_executor_degraded: bool = False


class MetadataExecutorDegradedError(RuntimeError):
    """
    Raised when the metadata executor is in degraded state.

    This exception indicates that too many orphaned workers have accumulated
    and the executor requires a reconnect or restart to restore normal operation.
    Callers should trigger recovery logic (reconnection) when this is raised.
    """


def _coerce_nonnegative_float(value: Any, default: float) -> float:
    """
    Coerce runtime BLE tuning values to a finite non-negative float.
    """
    try:
        if isinstance(value, bool):
            raise TypeError
        parsed = float(value)
        if not math.isfinite(parsed) or parsed < 0:
            raise ValueError
        return parsed
    except (TypeError, ValueError, OverflowError):
        return default


def _coerce_positive_int(value: Any, default: int) -> int:
    """
    Coerce runtime BLE tuning values to a positive integer.
    """
    try:
        if isinstance(value, bool):
            raise TypeError
        parsed = int(value)
        if parsed <= 0:
            raise ValueError
        return parsed
    except (TypeError, ValueError, OverflowError):
        return default


def _normalize_room_channel(room: dict[str, Any]) -> int | None:
    """
    Normalize a room's configured `meshtastic_channel` value to an integer.

    Parameters:
        room (dict[str, Any]): Room configuration dictionary; expected to contain the
            'meshtastic_channel' key. An optional 'id' key may be used in warnings.

    Returns:
        int | None: The channel as an `int`, or `None` if the key is missing or the
        value cannot be converted to an integer.

    Notes:
        Logs a warning mentioning the room `id` when the channel value is present but
        invalid.
    """
    room_channel = room.get("meshtastic_channel")
    if room_channel is None:
        return None
    try:
        return int(room_channel)
    except (ValueError, TypeError):
        logger.warning(
            "Invalid meshtastic_channel value %r in room config "
            "for room %s, skipping this room",
            room_channel,
            room.get("id", "unknown"),
        )
        return None


# Executor infrastructure — implemented in meshtastic.executors, re-exported here
# for backward-compatible patch targets (tests patch mmrelay.meshtastic_utils.*).

atexit.register(shutdown_shared_executors)


def _coerce_positive_int_id(raw_value: Any) -> int | None:
    """
    Convert a potential packet identifier value to a positive integer.

    Returns `None` when conversion fails or value is not positive.
    """
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _coerce_int_id(raw_value: Any) -> int | None:
    """
    Convert a potential identifier value to an integer.

    Returns `None` when conversion fails.
    """
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _coerce_positive_float(value: Any, default: float, setting_name: str) -> float:
    """
    Parse and validate a positive float config value.

    Falls back to `default` and logs a warning if conversion fails or if the
    value is not a finite positive number.
    """
    try:
        if isinstance(value, bool):
            raise TypeError
        parsed = float(value)
        if math.isfinite(parsed) and parsed > 0:
            return parsed
    except (TypeError, ValueError, OverflowError):
        pass

    logger.warning(
        "Invalid %s value %r; using default %.1f",
        setting_name,
        value,
        default,
    )
    return default


def _coerce_bool(value: Any, default: bool, setting_name: str) -> bool:
    """
    Parse and validate a boolean config value.

    Accepts booleans directly, and normalizes string values:
    - "true", "1", "yes", "on" (case-insensitive) -> True
    - "false", "0", "no", "off", "" (case-insensitive) -> False

    Falls back to `default` and logs a warning for unrecognized values.
    """
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        elif normalized in {"", "0", "false", "no", "off"}:
            return False
    elif isinstance(value, (int, float)):
        # For numeric types, use standard bool conversion
        return bool(value)

    logger.warning(
        "Invalid %s value %r; using default %s",
        setting_name,
        value,
        default,
    )
    return default


# Node refresh lifecycle — implemented in meshtastic.node_refresh, re-exported here
# for backward-compatible patch targets (tests patch mmrelay.meshtastic_utils.*).


# Health probe / ACK tracking / rxTime skew calibration — implemented in meshtastic.health,
# re-exported here for backward-compatible patch targets (tests patch mmrelay.meshtastic_utils.*).


def _submit_coro(
    coro: Any,
    loop: asyncio.AbstractEventLoop | None = None,
) -> Future[Any] | None:
    """
    Schedule a coroutine or awaitable on an available asyncio event loop and return a Future for its result.

    Parameters:
        coro: The coroutine or awaitable object to execute. If not awaitable, the function returns None.
        loop: Optional target asyncio event loop to run the coroutine on. If omitted, a suitable loop (module-level or running loop) will be used when available.

    Returns:
        A Future containing the coroutine's result, or `None` if `coro` is not awaitable.
    """
    if not inspect.iscoroutine(coro):
        if not inspect.isawaitable(coro):
            # Guard against test mocks returning non-awaitable values (e.g., return_value vs AsyncMock).
            return None

        # Wrap awaitables that are not coroutine objects (e.g., Futures) for scheduling.
        async def _await_wrapper(awaitable: Any) -> Any:
            """
            Await an awaitable and return its result.

            Parameters:
                awaitable (Any): A coroutine, Future, or other awaitable to be awaited.

            Returns:
                Any: The value produced by awaiting `awaitable`.
            """
            return await awaitable

        coro = _await_wrapper(coro)
    loop = loop or event_loop
    if (
        loop
        and isinstance(loop, asyncio.AbstractEventLoop)
        and not loop.is_closed()
        and loop.is_running()
    ):
        return asyncio.run_coroutine_threadsafe(coro, loop)
    # Fallback: schedule on a real loop if present; tests can override this.
    try:
        running = asyncio.get_running_loop()
        return cast(Future[Any], running.create_task(coro))
    except RuntimeError:
        # No running loop: check if we can safely create a new loop
        try:
            # Try to get the current event loop policy and create a new loop
            # This is safer than asyncio.run() which can cause deadlocks
            policy = asyncio.get_event_loop_policy()
            logger.debug(
                "No running event loop detected; creating a temporary loop to execute coroutine"
            )
            new_loop = policy.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                result = new_loop.run_until_complete(coro)
                result_future: Future[Any] = Future()
                result_future.set_result(result)
                return result_future
            finally:
                new_loop.close()
                asyncio.set_event_loop(None)
        except Exception as e:
            # Final fallback: always return a Future so _fire_and_forget can log
            # exceptions instead of crashing a background thread when no loop is
            # available. We intentionally catch broad exceptions here because the
            # coroutine itself may raise, and we still need a Future wrapper.
            logger.debug(
                "Ultimate fallback triggered for _submit_coro: %s: %s",
                type(e).__name__,
                e,
            )
            error_future: Future[Any] = Future()
            error_future.set_exception(e)
            return error_future


# BLE internals — implemented in meshtastic.ble, re-exported here
# for backward-compatible patch targets (tests patch mmrelay.meshtastic_utils.*).


# BLE internals — implemented in meshtastic.ble, re-exported here
# for backward-compatible patch targets (tests patch mmrelay.meshtastic_utils.*).


def _fire_and_forget(
    coro: Coroutine[Any, Any, Any], loop: asyncio.AbstractEventLoop | None = None
) -> None:
    """
    Schedule a coroutine to run in the background and log any non-cancellation exceptions.

    If `coro` is not a coroutine or scheduling fails, the function returns without side effects. The scheduled task will have a done callback that logs exceptions (except `asyncio.CancelledError`).

    Parameters:
        coro (Coroutine[Any, Any, Any]): The coroutine to execute.
        loop (asyncio.AbstractEventLoop | None): Optional event loop to use; if omitted the module-default loop is used.
    """
    if not inspect.iscoroutine(coro):
        return

    task = _submit_coro(coro, loop=loop)
    if task is None:
        return

    def _handle_exception(t: asyncio.Future[Any] | Future[Any]) -> None:
        """
        Log non-cancellation exceptions raised by a fire-and-forget task.

        If the provided task or future has an exception and it is not an
        asyncio.CancelledError, logs the exception at error level including the
        traceback. If retrieving the exception raises asyncio.CancelledError it is
        ignored; other errors encountered while inspecting the future are logged at
        debug level.

        Parameters:
            t (asyncio.Future | concurrent.futures.Future): Task or future to inspect.
        """
        try:
            if (exc := t.exception()) and not isinstance(exc, asyncio.CancelledError):
                logger.error("Exception in fire-and-forget task", exc_info=exc)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Error retrieving exception from fire-and-forget task: {e}")

    task.add_done_callback(_handle_exception)


def _make_awaitable(
    future: Any, loop: asyncio.AbstractEventLoop | None = None
) -> Awaitable[Any] | Any:
    """
    Convert a future-like object into an awaitable, optionally binding it to a given event loop.

    If `future` already implements the awaitable protocol, it is returned unchanged. Otherwise the function wraps the future so awaiting it yields the future's result; when `loop` is provided the wrapper is bound to that event loop.

    Parameters:
        future: A future-like object or an awaitable.
        loop (asyncio.AbstractEventLoop | None): Event loop to bind non-awaitable futures to; if `None`, no explicit loop binding is applied.

    Returns:
        An awaitable that yields the resolved value of `future`, or `future` itself if it already supports awaiting.
    """
    if hasattr(future, "__await__"):
        return future
    target_loop = loop if isinstance(loop, asyncio.AbstractEventLoop) else None
    return asyncio.wrap_future(future, loop=target_loop)


def _run_blocking_with_timeout(
    action: Callable[[], Any],
    timeout: float,
    label: str,
    timeout_log_level: int | None = logging.WARNING,
) -> None:
    """
    Run a blocking callable in a daemon thread with a timeout to avoid hangs.

    This is used for sync BLE operations in the official meshtastic library
    (notably BLEClient.disconnect/close), which can block indefinitely and
    prevent clean shutdown if executed on a non-daemon thread.

    Parameters:
        action (Callable[[], Any]): Callable to run in a daemon thread.
        timeout (float): Maximum seconds to wait for completion.
        label (str): Short label used for logging/exception messages.
        timeout_log_level (int | None): Logging level for timeouts, or None to suppress.

    Raises:
        TimeoutError: If the action does not finish before the timeout expires.
        Exception: Any exception raised by the action is re-raised.
    """
    done_event = threading.Event()
    action_error: Exception | None = None

    def _runner() -> None:
        """
        Execute the enclosing scope's action callable, record any raised Exception into the nonlocal `action_error`, and mark completion by calling `done_event.set()`.

        This function does not return a value; its observable effects are writing to the nonlocal `action_error` (set to the caught Exception on error) and setting the `done_event` to signal completion.
        """
        nonlocal action_error
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup
            action_error = exc
        finally:
            done_event.set()

    thread = threading.Thread(
        target=_runner,
        name=f"mmrelay-blocking-{label}",
        daemon=True,
    )
    thread.start()
    if not done_event.wait(timeout=timeout):
        if timeout_log_level is not None:
            logger.log(timeout_log_level, "%s timed out after %.1fs", label, timeout)
        raise TimeoutError(f"{label} timed out after {timeout:.1f}s")
    if action_error is not None:
        logger.debug("%s failed: %s", label, action_error)
        raise action_error


def _wait_for_result(
    result_future: Any,
    timeout: float,
    loop: asyncio.AbstractEventLoop | None = None,
) -> Any:
    """
    Wait for and return the resolved value of a future-like or awaitable object, enforcing a timeout.

    Parameters:
        result_future (Any): A concurrent.futures.Future, asyncio Future/Task, awaitable, or object exposing a callable `result(timeout)` method. If None, the function returns False.
        timeout (float): Maximum seconds to wait for the result.
        loop (asyncio.AbstractEventLoop | None): Optional event loop to use; if omitted, the function will use a running loop or create a temporary loop as needed.

    Returns:
        Any: The value produced by the resolved future or awaitable. Returns `False` when `result_future` is `None` or when the function refuses to block the currently running event loop and instead schedules the awaitable to run in the background. Callers should handle False as a "could not wait" signal rather than a failed result.

    Raises:
        asyncio.TimeoutError: If awaiting an asyncio awaitable times out.
        concurrent.futures.TimeoutError: If a concurrent.futures.Future times out.
        Exception: Any exception raised by the resolved future/awaitable is propagated.
    """
    if result_future is None:
        return False

    target_loop = loop if isinstance(loop, asyncio.AbstractEventLoop) else None

    # Handle concurrent.futures.Future directly
    if isinstance(result_future, Future):
        return result_future.result(timeout=timeout)

    # Handle asyncio Future/Task instances
    if isinstance(result_future, asyncio.Future):
        awaitable: Awaitable[Any] = result_future
    elif hasattr(result_future, "result") and callable(result_future.result):
        # Generic future-like object with .result API (used by some tests)
        try:
            return result_future.result(timeout)
        except TypeError:
            return result_future.result()
    else:
        awaitable = _make_awaitable(result_future, loop=target_loop)

    async def _runner() -> Any:
        """
        Await the captured awaitable and enforce the captured timeout.

        Returns:
            The result returned by the awaitable.

        Raises:
            asyncio.TimeoutError: If the awaitable does not complete before the timeout expires.
        """
        return await asyncio.wait_for(awaitable, timeout=timeout)

    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if target_loop and not target_loop.is_closed():
        if target_loop.is_running():
            if running_loop is target_loop:
                # Avoid deadlocking the loop thread; schedule and return.
                logger.warning(
                    "Refusing to block running event loop while waiting for result"
                )
                _fire_and_forget(_runner(), loop=target_loop)
                return False
            return asyncio.run_coroutine_threadsafe(_runner(), target_loop).result(
                timeout=timeout
            )
        return target_loop.run_until_complete(_runner())

    if running_loop and not running_loop.is_closed():
        if running_loop.is_running():
            logger.warning(
                "Refusing to block running event loop while waiting for result"
            )
            _fire_and_forget(_runner(), loop=running_loop)
            return False
        return running_loop.run_until_complete(_runner())

    new_loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(new_loop)
        return new_loop.run_until_complete(_runner())
    finally:
        new_loop.close()
        asyncio.set_event_loop(None)


def _wait_for_future_result_with_shutdown(
    result_future: Future[Any],
    *,
    timeout_seconds: float,
    poll_seconds: float = 1.0,
) -> Any:
    """Wait for a concurrent future while remaining responsive to shutdown.

    Polls `result_future.result()` in short intervals so long BLE operations can
    abort quickly when `shutting_down` is set, instead of waiting the full
    timeout budget in one blocking call.
    """

    deadline = time.monotonic() + float(timeout_seconds)
    poll_budget = max(0.05, float(poll_seconds))
    immediate_timeout_count = 0

    while True:
        if shutting_down:
            raise TimeoutError("Shutdown in progress")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FuturesTimeoutError()

        wait_budget = min(remaining, poll_budget)
        call_started = time.monotonic()
        try:
            return result_future.result(timeout=wait_budget)
        except FuturesTimeoutError:
            call_elapsed = time.monotonic() - call_started
            # Some mocked futures raise timeout immediately instead of blocking for
            # the timeout duration. Avoid a busy-loop that can run until the full
            # deadline in that scenario.
            if call_elapsed < min(0.01, wait_budget * 0.1):
                immediate_timeout_count += 1
                if immediate_timeout_count >= 3:
                    raise
            else:
                immediate_timeout_count = 0
            continue


def _get_name_safely(name_func: Callable[[Any], str | None], sender: Any) -> str:
    """
    Return a display name for a sender, falling back to the sender's string form.

    Parameters:
        name_func (Callable[[Any], str | None]): Function to obtain a name for the sender (e.g., get_longname or get_shortname).
        sender (Any): Sender identifier passed to `name_func`.

    Returns:
        str: The name returned by `name_func`, or `str(sender)` if no name is available or an error occurs.
    """
    try:
        return name_func(sender) or str(sender)
    except (TypeError, AttributeError):
        return str(sender)


def _get_name_or_none(
    name_func: Callable[[Any], str | None], sender: Any
) -> str | None:
    """
    Retrieve a name for a sender using the provided lookup function, or return None if the lookup fails.

    Parameters:
        name_func (Callable[[Any], str | None]): Function that returns a name given the sender (e.g., longname or shortname).
        sender (Any): Sender identifier passed to `name_func`.

    Returns:
        str | None: The name returned by `name_func`, or `None` if the function raises TypeError or AttributeError.
    """
    try:
        return name_func(sender)
    except (TypeError, AttributeError):
        return None


def _normalize_firmware_version(value: Any) -> str | None:
    """
    Normalize a firmware version candidate into a non-empty string.

    Parameters:
        value (Any): Candidate firmware value from metadata sources.

    Returns:
        str | None: Trimmed firmware version string when valid, otherwise None.
    """
    if isinstance(value, bytes):
        value = value.decode(DEFAULT_TEXT_ENCODING, errors=ENCODING_ERROR_IGNORE)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized and normalized.lower() != "unknown":
            return normalized
    return None


def _extract_firmware_version_from_metadata(metadata_source: Any) -> str | None:
    """
    Extract firmware version from a metadata object or mapping.

    Supports both snake_case and camelCase field names for compatibility with
    different Meshtastic payload shapes.

    Parameters:
        metadata_source (Any): Metadata container (protobuf-like object or dict).

    Returns:
        str | None: Firmware version if available, else None.
    """
    if metadata_source is None:
        return None

    if isinstance(metadata_source, dict):
        return _normalize_firmware_version(
            metadata_source.get("firmware_version")
            or metadata_source.get("firmwareVersion")
        )

    return _normalize_firmware_version(
        getattr(metadata_source, "firmware_version", None)
        or getattr(metadata_source, "firmwareVersion", None)
    )


def _extract_firmware_version_from_client(client: Any) -> str | None:
    """
    Return the first normalized firmware version exposed on common client fields.

    Parameters:
        client (Any): Meshtastic client object to inspect.

    Returns:
        str | None: Firmware version if present on the client, local node, or
            local interface metadata.
    """
    local_node = getattr(client, "localNode", None)
    local_iface = getattr(local_node, "iface", None) if local_node else None

    candidates = (
        getattr(client, "metadata", None),
        local_node and getattr(local_node, "metadata", None),
        local_iface and getattr(local_iface, "metadata", None),
    )
    for candidate in candidates:
        parsed = _extract_firmware_version_from_metadata(candidate)
        if parsed is not None:
            return parsed
    return None


def _missing_metadata_probe_error() -> RuntimeError:
    """
    Build the error raised when metadata probing is unavailable on a client.
    """
    return RuntimeError(
        "Meshtastic client has no localNode.getMetadata() for metadata probe"
    )


def _get_device_metadata(
    client: Any,
    *,
    force_refresh: bool = False,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    """
    Retrieve firmware version and raw metadata output from a Meshtastic client.

    Prefers structured metadata already present on the client/interface unless
    `force_refresh=True`. If no
    usable firmware version is cached, attempts to call
    `client.localNode.getMetadata()`, captures console output produced by that
    call, and extracts firmware version information from output and any updated
    structured metadata.

    Parameters:
        client (Any): Meshtastic client object expected to expose localNode.getMetadata(); if absent, metadata retrieval is skipped.
        force_refresh (bool): If `True`, always call `getMetadata()` even when
            structured metadata is already cached. Health checks use this mode
            intentionally because it issues an on-wire admin request.
        raise_on_error (bool): If `True`, re-raise metadata probe failures after
            logging so callers can treat failures as liveness errors.

    Returns:
        dict: {
            "firmware_version" (str): Parsed firmware version or "unknown" when not found,
            "raw_output" (str): Captured output from getMetadata(), truncated to 4096 characters with a trailing ellipsis if longer,
            "success" (bool): `True` when a firmware version was successfully parsed, `False` otherwise
        }
    """
    result = {"firmware_version": "unknown", "raw_output": "", "success": False}

    cached_firmware = _extract_firmware_version_from_client(client)
    if cached_firmware is not None and not force_refresh:
        result["firmware_version"] = cached_firmware
        result["success"] = True
        return result

    # Preflight: client may be a mock without localNode/getMetadata
    if not getattr(client, "localNode", None) or not callable(
        getattr(client.localNode, "getMetadata", None)
    ):
        if raise_on_error:
            raise _missing_metadata_probe_error()
        logger.debug(
            "Meshtastic client has no localNode.getMetadata(); skipping metadata retrieval"
        )
        return result

    try:
        # Capture getMetadata() output to extract firmware version.
        # Use a shared executor to prevent thread leaks if getMetadata() hangs.
        output_capture = io.StringIO()
        # Track redirect state so a timeout cannot leave sys.stdout pointing at
        # a closed StringIO (which can trigger "I/O operation on closed file").
        redirect_active = threading.Event()
        orig_stdout = sys.stdout

        def call_get_metadata() -> None:
            # Capture stdout only; stderr is left intact to avoid losing
            # critical error output if the worker outlives the timeout.
            """
            Invoke the client's getMetadata() while capturing its standard output.

            Calls client.localNode.getMetadata() with stdout redirected into the module's
            output_capture to prevent metadata noise from polluting process stdout; stderr
            is left unchanged. While the call runs, the module-level redirect_active flag
            is set and is cleared on completion to signal the redirect state.
            """
            try:
                with contextlib.redirect_stdout(output_capture):
                    redirect_active.set()
                    try:
                        client.localNode.getMetadata()
                    finally:
                        redirect_active.clear()
            except ValueError as exc:
                if output_capture.closed or "I/O operation on closed file" in str(exc):
                    return
                raise

        try:
            future = _submit_metadata_probe(call_get_metadata)
        except MetadataExecutorDegradedError:
            logger.error(
                "Metadata executor degraded; skipping metadata retrieval. "
                "Reconnect or restart required to restore metadata probing."
            )
            if raise_on_error:
                raise
            return result
        except RuntimeError as exc:
            logger.debug(
                "getMetadata() submission failed; skipping metadata retrieval",
                exc_info=exc,
            )
            if raise_on_error:
                raise
            return result

        if future is None:
            # A previous metadata request is still running; avoid piling up
            # threads and leave the in-flight call to finish in its own time.
            logger.debug("getMetadata() already running; skipping new request")
            return result

        timed_out = False
        future_error: Exception | None = None
        try:
            future.result(timeout=METADATA_WATCHDOG_SECS)
        except FuturesTimeoutError as e:
            timed_out = True
            if raise_on_error:
                future_error = e
            logger.debug(
                f"getMetadata() timed out after {METADATA_WATCHDOG_SECS} seconds"
            )
            # If the worker is still running, restore stdio immediately so the
            # main process does not keep writing to the captured buffer.
            if redirect_active.is_set():
                if sys.stdout is output_capture:
                    sys.stdout = orig_stdout
        except Exception as e:  # noqa: BLE001 - getMetadata errors vary by backend
            future_error = e

        try:
            console_output = output_capture.getvalue()
        except ValueError:
            # If the buffer was closed unexpectedly, treat as empty output.
            console_output = ""

        def _finalize_metadata_capture(done_future: Future[Any]) -> None:
            """
            Finalize capture state for a completed metadata retrieval future.

            Close the shared output capture stream once the worker has fully
            finished with it.

            Parameters:
                done_future (concurrent.futures.Future | asyncio.Future): The future that has completed and triggered finalization.
            """
            if not output_capture.closed:
                output_capture.close()

        # Only close the buffer when the redirect is no longer active; otherwise
        # writes from the worker will raise ValueError("I/O operation on closed file").
        if timed_out and not future.done():
            future.add_done_callback(_finalize_metadata_capture)
        else:
            _finalize_metadata_capture(future)

        # Re-raise any worker exception so the outer handler can log and
        # return default metadata without hiding failures.
        if future_error is not None:
            raise future_error

        raw_output = console_output
        if len(raw_output) > METADATA_OUTPUT_MAX_LENGTH:
            raw_output = raw_output[: max(METADATA_OUTPUT_MAX_LENGTH - 1, 0)] + "…"
        result["raw_output"] = raw_output

        match = FIRMWARE_VERSION_REGEX.search(console_output)
        parsed_output_firmware = (
            _normalize_firmware_version(match.group(1)) if match else None
        )
        if parsed_output_firmware is not None:
            result["firmware_version"] = parsed_output_firmware
            result["success"] = True
        else:
            refreshed_firmware = _extract_firmware_version_from_client(client)
            if refreshed_firmware is not None:
                result["firmware_version"] = refreshed_firmware
                result["success"] = True

    except Exception as e:  # noqa: BLE001 - metadata failures must not block startup
        # Metadata is optional; never block the main connection path on failures
        # in the admin request or parsing logic.
        logger.debug(
            "Could not retrieve device metadata via localNode.getMetadata()", exc_info=e
        )
        if raise_on_error:
            raise

    return result


# BLE internals — implemented in meshtastic.ble, re-exported here
# for backward-compatible patch targets (tests patch mmrelay.meshtastic_utils.*).


# Connection lifecycle — implemented in meshtastic.connection, re-exported here
# for backward-compatible patch targets (tests patch mmrelay.meshtastic_utils.*).
# Event handlers — implemented in meshtastic.events, re-exported here
# for backward-compatible patch targets (tests patch mmrelay.meshtastic_utils.*).


# Health monitoring config interpretation — implemented in meshtastic.health,
# re-exported here for backward-compatible patch targets (tests patch mmrelay.meshtastic_utils.*).


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
    global meshtastic_client, shutting_down, config

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot check connection.")
        return

    # Exit early if health monitoring is not required for this connection type/config
    if not requires_continuous_health_monitor(config):
        connection_type = config[CONFIG_SECTION_MESHTASTIC][CONFIG_KEY_CONNECTION_TYPE]
        if connection_type == CONNECTION_TYPE_BLE:
            logger.debug(
                "BLE connection uses real-time disconnection detection; periodic health checks disabled"
            )
        else:
            logger.info("Connection health checks are disabled in configuration")
        return

    connection_type = config[CONFIG_SECTION_MESHTASTIC][CONFIG_KEY_CONNECTION_TYPE]

    # Get health check configuration
    health_config = config["meshtastic"].get("health_check", {})
    if not isinstance(health_config, dict):
        logger.warning(
            "meshtastic.health_check config is not a dictionary (got %r); using defaults",
            health_config,
        )
        health_config = {}

    raw_health_check_enabled = health_config.get(
        "enabled", DEFAULT_HEALTH_CHECK_ENABLED
    )
    health_check_enabled = _coerce_bool(
        raw_health_check_enabled,
        DEFAULT_HEALTH_CHECK_ENABLED,
        "meshtastic.health_check.enabled",
    )

    if not health_check_enabled:
        logger.info("Connection health checks are disabled in configuration")
        return

    heartbeat_interval = health_config.get(
        "heartbeat_interval", DEFAULT_HEARTBEAT_INTERVAL_SECS
    )
    initial_delay = health_config.get("initial_delay", INITIAL_HEALTH_CHECK_DELAY)
    probe_timeout = health_config.get(
        "probe_timeout", DEFAULT_MESHTASTIC_OPERATION_TIMEOUT
    )

    # Support legacy heartbeat_interval configuration for backward compatibility
    if "heartbeat_interval" in config["meshtastic"]:
        heartbeat_interval = config["meshtastic"]["heartbeat_interval"]

    heartbeat_interval = _coerce_positive_float(
        heartbeat_interval,
        float(DEFAULT_HEARTBEAT_INTERVAL_SECS),
        "meshtastic.health_check.heartbeat_interval",
    )
    initial_delay = _coerce_positive_float(
        initial_delay,
        float(INITIAL_HEALTH_CHECK_DELAY),
        "meshtastic.health_check.initial_delay",
    )
    probe_timeout = _coerce_positive_float(
        probe_timeout,
        float(DEFAULT_MESHTASTIC_OPERATION_TIMEOUT),
        "meshtastic.health_check.probe_timeout",
    )

    # Initial delay before first health check to allow connection to settle.
    # This is particularly important for fast-responding systems like MeshMonitor
    # where the connection may be established quickly but ACK handling may not be
    # fully initialized yet.
    logger.debug(
        "Waiting before starting connection health checks to allow connection to settle"
    )
    await asyncio.sleep(initial_delay)

    while not shutting_down:
        if meshtastic_client and not reconnecting:
            submitted_client = meshtastic_client
            probe_submission_failed = False
            degraded_error = False
            try:
                probe_future = _submit_metadata_probe(
                    functools.partial(
                        _probe_device_connection,
                        submitted_client,
                        probe_timeout,
                    )
                )
            except MetadataExecutorDegradedError:
                logger.error("Metadata executor degraded; triggering reconnection")
                probe_future = None
                degraded_error = True
            except RuntimeError as exc:
                logger.debug(
                    "Skipping connection check - metadata probe submission failed",
                    exc_info=exc,
                )
                probe_future = None
                probe_submission_failed = True

            if degraded_error:
                if not reconnecting and meshtastic_client is submitted_client:
                    on_lost_meshtastic_connection(
                        interface=submitted_client,
                        detection_source="metadata executor degraded",
                    )
            elif probe_future is None:
                if not probe_submission_failed:
                    logger.debug(
                        "Skipping connection check - metadata probe already in progress"
                    )
            else:
                try:
                    # NOTE: Use the metadata admin request for keepalive/liveness.
                    # `getMyNodeInfo()` is local cached state in Meshtastic Python,
                    # so it can succeed even when the transport is unhealthy.
                    await asyncio.wait_for(
                        asyncio.wrap_future(probe_future),
                        timeout=probe_timeout,
                    )

                except Exception as exc:
                    error_detail = str(exc).strip() or exc.__class__.__name__
                    # Only trigger reconnection if we're not already reconnecting
                    if not reconnecting and meshtastic_client is submitted_client:
                        logger.error(
                            "%s connection health check failed: %s",
                            connection_type.capitalize(),
                            error_detail,
                            exc_info=True,
                        )
                        on_lost_meshtastic_connection(
                            interface=submitted_client,
                            detection_source=f"health check failed: {error_detail}",
                        )
                    else:
                        logger.debug(
                            "Skipping reconnection trigger - already reconnecting or client changed"
                        )
        elif reconnecting:
            logger.debug("Skipping connection check - reconnection in progress")
        elif not meshtastic_client:
            logger.debug("Skipping connection check - no client available")

        await asyncio.sleep(heartbeat_interval)


if __name__ == "__main__":
    # If running this standalone (normally the main.py does the loop), just try connecting and run forever.
    meshtastic_client = connect_meshtastic()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    event_loop = loop  # Set the event loop for use in callbacks
    _check_connection_task = loop.create_task(check_connection())
    loop.run_forever()
