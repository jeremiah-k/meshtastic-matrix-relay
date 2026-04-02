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

from mmrelay.config import get_meshtastic_config_value
from mmrelay.constants.config import (
    CONFIG_KEY_MESHNET_NAME,
    CONFIG_KEY_NODEDB_REFRESH_INTERVAL,
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
    BLE_CONNECT_TIMEOUT_SECS,
    BLE_DISCONNECT_MAX_RETRIES,
    BLE_DISCONNECT_SETTLE_SECS,
    BLE_DISCONNECT_TIMEOUT_SECS,
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
    METADATA_WATCHDOG_SECS,
    STALE_DISCONNECT_TIMEOUT_SECS,
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
    _ble_gating_module = importlib.import_module("meshtastic.interfaces.ble.gating")
except ModuleNotFoundError:
    _ble_gating_module = None
except Exception:  # noqa: BLE001 - defensive import of optional fork-specific feature
    _ble_gating_module = None
else:
    clear_all_registries = getattr(_ble_gating_module, "_clear_all_registries", None)
    if callable(clear_all_registries):
        _ble_gate_reset_callable = cast(Callable[[], None], clear_all_registries)

# Meshtastic text payloads are UTF-8 on the wire.
MESHTASTIC_TEXT_ENCODING = "utf-8"

# Session cutoff used to filter out backlog packets; reset on each new connection.
RELAY_START_TIME = time.time()
# Per-connection rxTime clock skew, calibrated from tracked health-probe responses.
_relay_rx_time_clock_skew_secs: float | None = None
_relay_rx_time_clock_skew_lock = threading.Lock()


# Global variables for the Meshtastic connection and event loop management
meshtastic_client = None
meshtastic_iface = None  # BLE interface instance for process lifetime
event_loop = None  # Will be set from main.py

meshtastic_lock = (
    threading.Lock()
)  # To prevent race conditions on meshtastic_client access

reconnecting = False
shutting_down = False

reconnect_task = None  # To keep track of the reconnect task
meshtastic_iface_lock = (
    threading.Lock()
)  # To prevent race conditions on BLE interface singleton creation

# Subscription flags to prevent duplicate subscriptions
subscribed_to_messages = False
subscribed_to_connection_lost = False

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


def _is_ble_duplicate_connect_suppressed_error(exc: BaseException) -> bool:
    """
    Return whether an exception message matches Meshtastic duplicate-connect suppression.

    This targets forked meshtastic BLE gate errors such as:
    "Connection suppressed: recently connected elsewhere".
    """
    message = str(exc).strip().lower()
    if not message:
        return False
    return "recently connected elsewhere" in message or (
        "connection suppressed" in message and "connected elsewhere" in message
    )


def _reset_ble_connection_gate_state(ble_address: str, *, reason: str) -> bool:
    """
    Best-effort reset of process-local BLE connection gate state.

    This recovery hook is only active when the installed Meshtastic library
    exposes a connection-gate reset API. Otherwise this function is a no-op.
    """
    if _ble_gate_reset_callable is None:
        return False

    try:
        _ble_gate_reset_callable()
    except Exception:
        logger.debug(
            "BLE connection-state reset failed for %s (%s)",
            ble_address,
            reason,
            exc_info=True,
        )
        return False

    logger.warning(
        "Reset BLE connection state for %s (%s)",
        ble_address,
        reason,
    )
    return True


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


def _shutdown_shared_executors() -> None:
    """
    Shutdown shared executors on interpreter exit to avoid blocking.

    Attempts to cancel any pending futures and shutdown without waiting
    to prevent interpreter hangs when tasks are stuck.

    Note: This is called via atexit during interpreter shutdown. It performs
    cleanup without waiting to avoid blocking the interpreter exit sequence.
    """
    global _ble_executor, _ble_future, _ble_future_address
    global _ble_future_started_at, _ble_future_timeout_secs
    global _metadata_executor, _metadata_future, _metadata_future_started_at
    global _health_probe_request_deadlines
    global _metadata_executor_orphaned_workers, _ble_executor_orphaned_workers_by_address
    global _ble_executor_degraded_addresses, _metadata_executor_degraded

    # Cancel any pending BLE operation
    # Capture future ref inside lock, cancel outside to avoid deadlock with done callbacks
    ble_future_to_cancel = None
    with _ble_executor_lock:
        if _ble_future and not _ble_future.done():
            logger.debug("Cancelling pending BLE future during executor shutdown")
            ble_future_to_cancel = _ble_future
        _ble_future = None
        _ble_future_address = None
        _ble_future_started_at = None
        _ble_future_timeout_secs = None
        with _ble_timeout_lock:
            _ble_timeout_counts.clear()
            _ble_executor_orphaned_workers_by_address.clear()
        _ble_executor_degraded_addresses.clear()

        executor = _ble_executor
        _ble_executor = None
    if ble_future_to_cancel is not None:
        ble_future_to_cancel.cancel()
    if executor is not None and not getattr(executor, "_shutdown", False):
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=False)

    # Cancel any pending metadata operation
    # Capture future ref inside lock, cancel outside to avoid deadlock with done callbacks
    metadata_future_to_cancel = None
    with _metadata_future_lock:
        if _metadata_future and not _metadata_future.done():
            logger.debug("Cancelling pending metadata future during executor shutdown")
            metadata_future_to_cancel = _metadata_future
        _metadata_future = None
        _metadata_future_started_at = None
        _metadata_executor_orphaned_workers = 0
        _metadata_executor_degraded = False

        executor = _metadata_executor
        _metadata_executor = None
    with _health_probe_request_lock:
        _health_probe_request_deadlines.clear()
    if metadata_future_to_cancel is not None:
        metadata_future_to_cancel.cancel()
    if executor is not None and not getattr(executor, "_shutdown", False):
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=False)


def shutdown_shared_executors() -> None:
    """
    Public wrapper for shutting down shared Meshtastic executors.

    This is primarily intended for test teardown and explicit cleanup paths.
    """
    _shutdown_shared_executors()


def reset_executor_degraded_state(
    ble_address: str | None = None, *, reset_all: bool = False
) -> bool:
    """
    Reset degraded state for executors, allowing recovery after reconnect.

    When executors reach the orphan threshold, they enter a degraded state
    and refuse new work submissions. This function clears that state so normal
    operation can resume after a successful reconnect or manual intervention.

    Note:
        When ble_address is provided (and reset_all is False), this function
        resets both the BLE executor degraded state AND the metadata executor
        degraded state. This connection-scoped behavior reflects that a successful
        BLE reconnect typically also restores the metadata probe path.

    Parameters:
        ble_address (str | None): Specific BLE address to reset. If None and
            reset_all is False, only metadata executor is reset.
        reset_all (bool): If True, reset all degraded state including all
            BLE addresses and metadata executor.

    Returns:
        bool: True if any degraded state was reset, False otherwise.
    """
    global _ble_executor_degraded_addresses, _metadata_executor_degraded
    global _metadata_executor_orphaned_workers, _ble_executor_orphaned_workers_by_address
    global _ble_executor, _metadata_executor

    reset_any = False

    if reset_all:
        stale_ble_executor = None
        with _ble_executor_lock:
            if _ble_executor_degraded_addresses:
                logger.info(
                    "Resetting degraded state for all BLE executors: %s",
                    ", ".join(sorted(_ble_executor_degraded_addresses)),
                )
                _ble_executor_degraded_addresses.clear()
                with _ble_timeout_lock:
                    _ble_executor_orphaned_workers_by_address.clear()
                if _ble_executor is not None:
                    stale_ble_executor = _ble_executor
                    _ble_executor = None
                reset_any = True
        if stale_ble_executor is not None:
            try:
                stale_ble_executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                stale_ble_executor.shutdown(wait=False)
        stale_metadata_executor = None
        with _metadata_future_lock:
            if _metadata_executor_degraded:
                logger.info("Resetting degraded state for metadata executor")
                _metadata_executor_degraded = False
                _metadata_executor_orphaned_workers = 0
                if _metadata_executor is not None:
                    stale_metadata_executor = _metadata_executor
                    _metadata_executor = None
                reset_any = True
        if stale_metadata_executor is not None:
            try:
                stale_metadata_executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                stale_metadata_executor.shutdown(wait=False)
        return reset_any

    if ble_address is not None:
        stale_ble_executor = None
        with _ble_executor_lock:
            if ble_address in _ble_executor_degraded_addresses:
                logger.info(
                    "Resetting degraded state for BLE executor: %s",
                    ble_address,
                )
                _ble_executor_degraded_addresses.discard(ble_address)
                with _ble_timeout_lock:
                    _ble_executor_orphaned_workers_by_address.pop(ble_address, None)
                if _ble_executor is not None:
                    stale_ble_executor = _ble_executor
                    _ble_executor = None
                reset_any = True
        if stale_ble_executor is not None:
            try:
                stale_ble_executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                stale_ble_executor.shutdown(wait=False)

    stale_metadata_executor = None
    with _metadata_future_lock:
        if _metadata_executor_degraded:
            logger.info("Resetting degraded state for metadata executor")
            _metadata_executor_degraded = False
            _metadata_executor_orphaned_workers = 0
            if _metadata_executor is not None:
                stale_metadata_executor = _metadata_executor
                _metadata_executor = None
            reset_any = True
    if stale_metadata_executor is not None:
        try:
            stale_metadata_executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            stale_metadata_executor.shutdown(wait=False)

    return reset_any


atexit.register(shutdown_shared_executors)


def _get_ble_executor() -> ThreadPoolExecutor:
    """
    Get or create a BLE executor thread pool.

    Returns the shared BLE executor, creating it if it has been shut down or is None.
    This handles cases where executor has been shut down during test cleanup.
    Note: Caller must hold _ble_executor_lock to avoid race conditions.

    Returns:
        ThreadPoolExecutor: The shared BLE executor instance.
    """
    global _ble_executor
    if _ble_executor is None or getattr(_ble_executor, "_shutdown", False):
        _ble_executor = ThreadPoolExecutor(max_workers=1)
    return _ble_executor


def _get_metadata_executor() -> ThreadPoolExecutor:
    """
    Get or create the metadata executor thread pool.

    Returns the shared metadata executor, creating it if it has been shut down or is None.
    This handles cases where executor has been shut down during test cleanup.
    Note: Caller must hold _metadata_future_lock to avoid race conditions.

    Returns:
        ThreadPoolExecutor: The shared metadata executor instance.
    """
    global _metadata_executor
    if _metadata_executor is None or getattr(_metadata_executor, "_shutdown", False):
        _metadata_executor = ThreadPoolExecutor(max_workers=1)
    return _metadata_executor


def _clear_metadata_future_if_current(done_future: Future[Any]) -> None:
    """
    Clear the shared metadata future if it still refers to `done_future`.
    """
    global _metadata_future, _metadata_future_started_at
    with _metadata_future_lock:
        if _metadata_future is done_future:
            _metadata_future = None
            _metadata_future_started_at = None


def _reset_metadata_executor_for_stale_probe() -> None:
    """
    Replace the shared metadata executor after stale probe detection.

    A wedged single-worker executor can block all later probe submissions.
    This function abandons the old executor and usually creates a fresh one so
    probe retries are not queued behind a stuck worker.

    When the number of orphaned workers reaches EXECUTOR_ORPHAN_THRESHOLD,
    the executor enters a degraded state: submission of new probes is refused
    and further automatic recovery is disabled. Recovery requires an explicit
    reconnect or process restart.
    """
    global _metadata_executor, _metadata_future, _metadata_future_started_at
    global _metadata_executor_orphaned_workers, _metadata_executor_degraded

    stale_executor: ThreadPoolExecutor | None = None
    orphaned_workers: int | None = None
    degraded_now = False

    with _metadata_future_lock:
        if _metadata_executor_degraded:
            logger.debug(
                "Metadata executor is in degraded state; refusing to reset. "
                "Reconnect or restart required to recover."
            )
            return

        projected_orphans = _metadata_executor_orphaned_workers + 1
        if projected_orphans >= EXECUTOR_ORPHAN_THRESHOLD:
            _metadata_executor_degraded = True
            _metadata_executor_orphaned_workers = projected_orphans
            logger.error(
                "METADATA EXECUTOR DEGRADED: %s workers have been orphaned due to "
                "repeated hangs. Further automatic recovery is disabled. "
                "Reconnect or restart the relay to restore metadata probing.",
                projected_orphans,
            )
            _metadata_future = None
            _metadata_future_started_at = None
            stale_executor = _metadata_executor
            # Keep degraded mode fail-fast: stop automatic executor recreation.
            _metadata_executor = None
            degraded_now = True

        if not degraded_now:
            stale_executor = _metadata_executor
            _metadata_future = None
            _metadata_future_started_at = None
            _metadata_executor = ThreadPoolExecutor(max_workers=1)
            if stale_executor is not None and not getattr(
                stale_executor, "_shutdown", False
            ):
                _metadata_executor_orphaned_workers = projected_orphans
                orphaned_workers = _metadata_executor_orphaned_workers

    if (
        not degraded_now
        and stale_executor is not None
        and not getattr(stale_executor, "_shutdown", False)
    ):
        logger.warning(
            "Replacing stale metadata executor after probe timeout; "
            "orphaned metadata workers=%s (threshold=%s)",
            orphaned_workers,
            EXECUTOR_ORPHAN_THRESHOLD,
        )
        try:
            stale_executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            stale_executor.shutdown(wait=False)
    elif (
        degraded_now
        and stale_executor is not None
        and not getattr(stale_executor, "_shutdown", False)
    ):
        try:
            stale_executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            stale_executor.shutdown(wait=False)


def _schedule_metadata_future_cleanup(
    future: Future[Any],
    reason: str,
) -> None:
    """
    Schedule delayed cleanup for a metadata probe future that appears stuck.

    If the future is still the active metadata future after
    `METADATA_WATCHDOG_SECS`, clear the shared reference so later health checks
    are not permanently suppressed by a stale in-flight marker.
    """

    def _cleanup() -> None:
        if future.done():
            return

        with _metadata_future_lock:
            should_clear = _metadata_future is future

        if not should_clear:
            return

        logger.warning(
            "Metadata worker still running after %.0fs; clearing stale future (%s)",
            METADATA_WATCHDOG_SECS,
            reason,
        )
        _reset_metadata_executor_for_stale_probe()

    try:
        timer = threading.Timer(METADATA_WATCHDOG_SECS, _cleanup)
        timer.daemon = True
        future.add_done_callback(lambda _f: timer.cancel())
        timer.start()
    except Exception as exc:  # noqa: BLE001 - best-effort watchdog setup
        logger.debug(
            "Failed to schedule metadata future cleanup watchdog",
            exc_info=exc,
        )


def _submit_metadata_probe(probe: Callable[[], Any]) -> Future[Any] | None:
    """
    Submit a metadata-related admin probe unless one is already in flight.

    Returns the shared concurrent future for the submitted probe, or `None`
    when another metadata operation is already running.
    """
    global _metadata_future, _metadata_future_started_at
    stale_detected = False
    with _metadata_future_lock:
        if _metadata_future is not None and not _metadata_future.done():
            if _metadata_future_started_at is None or (
                time.monotonic() - _metadata_future_started_at < METADATA_WATCHDOG_SECS
            ):
                return None
            stale_detected = True

    if stale_detected:
        logger.warning(
            "Metadata worker still running after %.0fs; clearing stale future (%s)",
            METADATA_WATCHDOG_SECS,
            "submit-retry",
        )
        _reset_metadata_executor_for_stale_probe()

    submission_error: RuntimeError | None = None
    with _metadata_future_lock:
        if _metadata_executor_degraded:
            logger.error(
                "Metadata executor degraded: too many orphaned workers. "
                "Reconnect or restart required to restore metadata probing."
            )
            raise MetadataExecutorDegradedError(
                "Metadata executor is degraded; reconnect or restart required"
            )
        if _metadata_future is not None and not _metadata_future.done():
            return None
        try:
            future = _get_metadata_executor().submit(probe)
        except RuntimeError as exc:
            submission_error = exc
            future = None
        if future is not None:
            _metadata_future = future
            _metadata_future_started_at = time.monotonic()

    if submission_error is not None:
        logger.debug(
            "Metadata probe submission failed; resetting metadata executor",
            exc_info=submission_error,
        )
        _reset_metadata_executor_for_stale_probe()
        raise submission_error

    if future is None:
        return None

    future.add_done_callback(_clear_metadata_future_if_current)
    _schedule_metadata_future_cleanup(future, reason="metadata-probe")
    return future


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


def _parse_refresh_interval_seconds(raw_interval: Any) -> float | None:
    """
    Parse and validate a refresh interval value.

    Returns the parsed float if valid, or None if invalid (wrong type, non-finite, etc.).
    """
    try:
        if isinstance(raw_interval, bool):
            raise TypeError("boolean interval")
        interval = float(raw_interval)
        if not math.isfinite(interval):
            raise ValueError("non-finite interval")
        if interval < 0:
            raise ValueError("negative interval")
        return interval
    except (TypeError, ValueError, OverflowError):
        return None


def get_nodedb_refresh_interval_seconds(
    passed_config: dict[str, Any] | None = None,
) -> float:
    """
    Return the configured nodedb refresh interval (seconds).

    Reads `meshtastic.nodedb_refresh_interval` and falls back to
    `DEFAULT_NODEDB_REFRESH_INTERVAL` when missing or invalid.

    Current scope: this interval controls periodic refresh of cached long/short
    node-name tables derived from the Meshtastic NodeDB. The key name is
    future-oriented because later releases may expand persistence beyond names.

    Parameters:
        passed_config (dict[str, Any] | None): Optional config to read from.
            When omitted, uses this module's global `config`.
    """
    config_source = passed_config if passed_config is not None else config
    if not isinstance(config_source, dict):
        config_source = {}
    raw_interval = get_meshtastic_config_value(
        config_source,
        CONFIG_KEY_NODEDB_REFRESH_INTERVAL,
        DEFAULT_NODEDB_REFRESH_INTERVAL,
    )
    interval = _parse_refresh_interval_seconds(raw_interval)
    if interval is not None:
        return interval

    logger.warning(
        "Invalid meshtastic.nodedb_refresh_interval=%r; defaulting to %.1f",
        raw_interval,
        DEFAULT_NODEDB_REFRESH_INTERVAL,
    )
    return DEFAULT_NODEDB_REFRESH_INTERVAL


def _snapshot_node_name_rows() -> tuple[dict[str, Any] | None, bool]:
    """
    Build a minimal node-name snapshot under meshtastic_lock.

    Returns:
        tuple[dict[str, Any] | None, bool]:
            - Snapshot suitable for sync_name_tables_if_changed(), or None when unavailable.
            - True when the Meshtastic client is unavailable.
    """
    with meshtastic_lock:
        client = meshtastic_client
        if client is None:
            return None, True

        raw_nodes = getattr(client, "nodes", None)
        if not isinstance(raw_nodes, dict):
            return None, False

        nodes_snapshot: dict[str, Any] = {}
        for node_id, raw_node in raw_nodes.items():
            node_key = str(node_id)
            if not isinstance(raw_node, dict):
                nodes_snapshot[node_key] = {"user": None}
                continue

            raw_user = raw_node.get("user")
            if not isinstance(raw_user, dict):
                nodes_snapshot[node_key] = {"user": {"id": None}}
                continue

            user_snapshot: dict[str, Any] = {
                "id": raw_user.get("id"),
                PROTO_NODE_NAME_LONG: raw_user.get(PROTO_NODE_NAME_LONG),
                PROTO_NODE_NAME_SHORT: raw_user.get(PROTO_NODE_NAME_SHORT),
            }
            nodes_snapshot[node_key] = {"user": user_snapshot}

        return nodes_snapshot, False


async def refresh_node_name_tables(
    shutdown_event: asyncio.Event,
    *,
    refresh_interval_seconds: float | None = None,
) -> None:
    """
    Periodically sync longname/shortname tables from the current Meshtastic node DB.

    The first refresh attempt runs immediately. When `refresh_interval_seconds`
    is zero, one immediate refresh is attempted and periodic refresh
    is disabled afterward.

    Current scope: this task updates only long/short name cache tables from the
    NodeDB snapshot. Future releases may extend persistence to broader NodeDB
    fields while keeping this interval setting.

    Note: Exceptions are intentionally propagated to the caller (the supervisor in
    main.py) which catches them and restarts this task with exponential backoff.
    This prevents silent infinite retry loops on persistent errors while still
    allowing recovery from transient failures.
    """
    if refresh_interval_seconds is None:
        interval = get_nodedb_refresh_interval_seconds()
    else:
        parsed = _parse_refresh_interval_seconds(refresh_interval_seconds)
        if parsed is None:
            configured_interval = get_nodedb_refresh_interval_seconds()
            logger.warning(
                "Invalid NodeDB name-cache refresh interval override %r; defaulting to configured interval %.1f",
                refresh_interval_seconds,
                configured_interval,
            )
            interval = configured_interval
        else:
            interval = parsed

    previous_state: NodeNameState | None = None
    while not shutdown_event.is_set():
        try:
            nodes_snapshot, client_missing = await asyncio.to_thread(
                _snapshot_node_name_rows
            )

            if nodes_snapshot is None:
                if client_missing:
                    logger.debug(
                        "Skipping name-cache refresh from NodeDB because Meshtastic client is unavailable"
                    )
                else:
                    logger.debug(
                        "Skipping name-cache refresh from NodeDB because client.nodes is unavailable"
                    )
            else:
                previous_state = await asyncio.to_thread(
                    sync_name_tables_if_changed,
                    nodes_snapshot,
                    previous_state,
                )
        except Exception:
            logger.exception("Failed to refresh name-cache tables from NodeDB snapshot")
            raise

        if interval <= 0:
            logger.debug(
                "NodeDB name-cache periodic refresh disabled (interval=%.3f)",
                float(interval),
            )
            return

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=float(interval))
        except asyncio.TimeoutError:
            continue


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
        parsed = _coerce_positive_int_id(candidate)
        if parsed is not None:
            return parsed
    return None


def _prune_health_probe_tracking(now: float | None = None) -> None:
    """
    Remove expired health-probe request IDs from the in-memory tracking map.
    """
    current = now if now is not None else time.monotonic()
    expired_ids = [
        request_id
        for request_id, deadline in _health_probe_request_deadlines.items()
        if deadline <= current
    ]
    for request_id in expired_ids:
        _health_probe_request_deadlines.pop(request_id, None)


def _track_health_probe_request_id(
    raw_request_id: Any, timeout_secs: float
) -> int | None:
    """
    Track a newly sent health-probe request ID for response log classification.

    Returns the normalized request ID if tracking succeeded.
    """
    request_id = _coerce_positive_int_id(raw_request_id)
    if request_id is None:
        return None

    expires_at = (
        time.monotonic() + max(float(timeout_secs), 1.0) + HEALTH_PROBE_TRACK_GRACE_SECS
    )
    with _health_probe_request_lock:
        _prune_health_probe_tracking()
        _health_probe_request_deadlines[request_id] = expires_at
    return request_id


def _is_health_probe_response_packet(packet: dict[str, Any], interface: Any) -> bool:
    """
    Determine if an inbound packet is a tracked health-probe response.
    """
    request_id = _extract_packet_request_id(packet)
    if request_id is None:
        return False

    sender = _coerce_int_id(packet.get("from"))
    local_num_raw = getattr(getattr(interface, "myInfo", None), "my_node_num", None)
    if local_num_raw is None:
        local_num_raw = getattr(getattr(interface, "localNode", None), "nodeNum", None)
    local_num = _coerce_int_id(local_num_raw)
    if sender is not None and local_num is not None and sender != local_num:
        return False

    with _health_probe_request_lock:
        _prune_health_probe_tracking()
        return request_id in _health_probe_request_deadlines


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

    sender_num = _coerce_int_id(sender_raw)

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
        raise _missing_local_node_ack_state_error()

    decoded = packet.get("decoded") if isinstance(packet, dict) else None
    routing = decoded.get("routing") if isinstance(decoded, dict) else None
    if isinstance(routing, dict):
        error_reason = routing.get("errorReason")
        if error_reason and error_reason != "NONE":
            if hasattr(ack_state, "receivedNak"):
                ack_state.receivedNak = True
                return
            raise _missing_received_nak_error()

    if _set_probe_ack_flag_from_packet(local_node, packet):
        return

    raise _failed_probe_ack_state_error()


def _wait_for_probe_ack(client: Any, timeout_secs: float) -> None:
    """
    Wait for ACK/NAK flags with a bounded timeout for health probes.

    Uses the interface acknowledgment object directly so probe duration is
    capped independently of the interface-wide timeout setting.
    """
    ack_state = getattr(client, "_acknowledgment", None)
    if ack_state is None:
        raise _missing_ack_state_error()

    ack_attrs = ("receivedAck", "receivedNak", "receivedImplAck")

    deadline = time.monotonic() + timeout_secs
    while time.monotonic() < deadline:
        if any(bool(getattr(ack_state, attr, False)) for attr in ack_attrs):
            _reset_probe_ack_state(ack_state)
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(ACK_POLL_INTERVAL_SECS, remaining))

    # Final check catches ACK/NAK updates that may land near the deadline.
    if any(bool(getattr(ack_state, attr, False)) for attr in ack_attrs):
        _reset_probe_ack_state(ack_state)
        return

    raise _metadata_probe_ack_timeout_error(timeout_secs)


def _probe_device_connection(
    client: Any, timeout_secs: float = DEFAULT_MESHTASTIC_OPERATION_TIMEOUT
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
        raise _missing_probe_transport_error()

    # Clear stale ACK/NAK flags so this probe cannot "pass" on prior traffic.
    ack_state = getattr(client, "_acknowledgment", None)
    if ack_state is None:
        ack_state = getattr(getattr(local_node, "iface", None), "_acknowledgment", None)
    if ack_state is not None:
        _reset_probe_ack_state(ack_state)

    request = admin_pb2.AdminMessage()
    request.get_device_metadata_request = True
    # Use the public sendData API instead of private _sendAdmin
    node_num = getattr(local_node, "nodeNum", None)
    destination_id = node_num if node_num is not None else "^local"
    sent_packet = client.sendData(
        request.SerializeToString(),
        destinationId=destination_id,
        portNum=portnums_pb2.PortNum.ADMIN_APP,
        wantAck=True,
        wantResponse=True,
        onResponse=functools.partial(_handle_probe_ack_callback, local_node),
    )
    request_id = _track_health_probe_request_id(
        (
            getattr(sent_packet, "id", None)
            if not isinstance(sent_packet, dict)
            else sent_packet.get("id")
        ),
        timeout_secs,
    )
    if request_id is not None:
        logger.debug(
            "[HEALTH_CHECK] Sent metadata probe requestId=%s timeout=%.1fs",
            request_id,
            timeout_secs,
        )
    else:
        logger.debug(
            "[HEALTH_CHECK] Sent metadata probe timeout=%.1fs",
            timeout_secs,
        )

    if getattr(client, "_acknowledgment", None) is not None:
        _wait_for_probe_ack(client, timeout_secs)
        return

    if callable(getattr(client, "waitForAckNak", None)):
        _run_blocking_with_timeout(
            cast(Callable[[], Any], client.waitForAckNak),
            timeout=timeout_secs,
            label="metadata-probe-waitForAckNak",
            timeout_log_level=logging.DEBUG,
        )
        return

    raise _missing_probe_wait_error()


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


def _clear_ble_future(done_future: Future[Any]) -> None:
    """
    Release the module's active BLE future reference if it matches the completed future.

    If `done_future` is the currently tracked BLE executor future, clear the tracked
    future and its associated address; also remove the per-address timeout count.
    Parameters:
        done_future (concurrent.futures.Future | asyncio.Future): The future that has completed and should be cleared if it matches the active BLE task.
    """
    global _ble_future, _ble_future_address
    global _ble_future_started_at, _ble_future_timeout_secs
    with _ble_executor_lock:
        if _ble_future is done_future:
            _ble_future = None
            if _ble_future_address:
                with _ble_timeout_lock:
                    _ble_timeout_counts.pop(_ble_future_address, None)
            _ble_future_address = None
            _ble_future_started_at = None
            _ble_future_timeout_secs = None


def _schedule_ble_future_cleanup(
    future: Future[Any],
    ble_address: str,
    reason: str,
) -> None:
    """
    Schedule a delayed cleanup for a stuck BLE future.

    If a BLE task cannot be cancelled, we avoid blocking all future retries by
    clearing the shared future reference after a grace period.
    """
    watchdog_secs = _coerce_positive_float(
        _ble_future_watchdog_secs,
        BLE_FUTURE_WATCHDOG_SECS,
        "_ble_future_watchdog_secs",
    )

    def _cleanup() -> None:
        """
        Clear a stale BLE worker future when it exceeds the watchdog timeout.

        If the provided future is still running and remains the active BLE future, logs a warning including the watchdog duration, BLE address, and reason, then attempts to reset the executor.
        """
        if future.done():
            return
        with _ble_executor_lock:
            if _ble_future is not future:
                return
        logger.warning(
            "BLE worker still running after %.0fs for %s; resetting executor (%s)",
            watchdog_secs,
            ble_address,
            reason,
        )
        reset_threshold = _coerce_positive_int(
            _ble_timeout_reset_threshold,
            BLE_TIMEOUT_RESET_THRESHOLD,
        )
        _maybe_reset_ble_executor(ble_address, reset_threshold)

    timer = threading.Timer(watchdog_secs, _cleanup)
    timer.daemon = True
    future.add_done_callback(lambda _f: timer.cancel())
    timer.start()


def _attach_late_ble_interface_disposer(
    future: Future[Any],
    ble_address: str,
    reason: str,
    *,
    fallback_iface: Any | None = None,
) -> None:
    """
    Dispose interfaces created by abandoned BLE futures that complete late.

    Timeout paths can drop `_ble_future` while a worker thread is still running.
    If that worker later succeeds, this callback prevents orphaned interfaces
    from remaining connected outside normal ownership.
    """

    def _dispose(done_future: Future[Any]) -> None:
        if done_future.cancelled():
            return

        late_iface = fallback_iface
        try:
            result = done_future.result()
            if result is not None:
                late_iface = result
        except Exception:  # noqa: BLE001 - futures can raise backend-specific errors
            late_iface = fallback_iface

        if late_iface is None:
            return

        if not (hasattr(late_iface, "disconnect") or hasattr(late_iface, "close")):
            return

        with meshtastic_iface_lock:
            active_iface = meshtastic_iface
        if active_iface is late_iface:
            return

        logger.warning(
            "Cleaning up late BLE interface completion for %s (%s)",
            ble_address,
            reason,
        )
        try:
            _disconnect_ble_interface(
                late_iface,
                reason=f"late completion after {reason}",
            )
        except Exception:  # noqa: BLE001 - cleanup must not propagate
            logger.debug(
                "Late BLE interface cleanup failed for %s (%s)",
                ble_address,
                reason,
                exc_info=True,
            )

    future.add_done_callback(_dispose)


def _record_ble_timeout(ble_address: str) -> int:
    """
    Increment the recorded BLE timeout count for the given BLE address.

    This operation is thread-safe.

    Parameters:
        ble_address (str): BLE device address to record the timeout for.

    Returns:
        int: The updated timeout count for the specified BLE address (1 or greater).
    """
    with _ble_timeout_lock:
        _ble_timeout_counts[ble_address] = _ble_timeout_counts.get(ble_address, 0) + 1
        return _ble_timeout_counts[ble_address]


def _ensure_ble_worker_available(ble_address: str, *, operation: str) -> None:
    """
    Ensure the shared BLE worker is available for a new operation.

    When a previous BLE future remains in-flight beyond its timeout budget, treat
    it as stale and force-reset the worker so retries can make forward progress.
    """
    stale_elapsed_secs: float | None = None
    stale_timeout_secs: float | None = None
    stale_address: str | None = None
    stale_grace_secs = _coerce_nonnegative_float(
        _ble_future_stale_grace_secs, BLE_FUTURE_STALE_GRACE_SECS
    )
    reset_threshold = _coerce_positive_int(
        _ble_timeout_reset_threshold, BLE_TIMEOUT_RESET_THRESHOLD
    )

    with _ble_executor_lock:
        active_future = _ble_future
        if active_future is None or active_future.done():
            return

        if _ble_future_started_at is not None and _ble_future_timeout_secs is not None:
            elapsed = time.monotonic() - _ble_future_started_at
            stale_after = _ble_future_timeout_secs + stale_grace_secs
            if elapsed >= stale_after:
                stale_elapsed_secs = elapsed
                stale_timeout_secs = _ble_future_timeout_secs
                stale_address = _ble_future_address or ble_address

    if (
        stale_elapsed_secs is not None
        and stale_timeout_secs is not None
        and stale_address is not None
    ):
        logger.warning(
            "BLE worker appears stale during %s for %s (elapsed=%.1fs, timeout=%.1fs); forcing worker reset",
            operation,
            stale_address,
            stale_elapsed_secs,
            stale_timeout_secs,
        )
        _reset_ble_connection_gate_state(
            stale_address,
            reason=f"stale worker during {operation}",
        )
        timeout_count = _record_ble_timeout(stale_address)
        _maybe_reset_ble_executor(
            stale_address,
            max(timeout_count, reset_threshold),
        )

    with _ble_executor_lock:
        if _ble_future and not _ble_future.done():
            logger.debug("BLE worker busy; skipping %s for %s", operation, ble_address)
            raise TimeoutError(
                f"BLE {operation} already in progress for {ble_address}."
            )


def _maybe_reset_ble_executor(ble_address: str, timeout_count: int) -> None:
    """
    Reset the BLE worker executor when an address has reached the timeout threshold.

    Recreates the module's BLE executor and clears any active BLE future/state for the given
    BLE address when `timeout_count` meets or exceeds the configured reset threshold. Performs a
    best-effort cancellation and cleanup of a possibly stuck BLE task and resets the per-address
    timeout counter to zero.

    When the number of orphaned workers for a BLE address reaches EXECUTOR_ORPHAN_THRESHOLD,
    that address enters a degraded state: submission of new BLE work is refused and further
    automatic recovery is disabled. Recovery requires an explicit reconnect or process restart.

    Parameters:
        ble_address (str): BLE device address associated with the observed timeouts.
        timeout_count (int): Number of consecutive timeouts recorded for that address.
    """
    global _ble_executor, _ble_future, _ble_future_address
    global _ble_future_started_at, _ble_future_timeout_secs
    global _ble_executor_orphaned_workers_by_address, _ble_executor_degraded_addresses

    if ble_address in _ble_executor_degraded_addresses:
        logger.debug(
            "BLE executor for %s is in degraded state; refusing to reset. "
            "Reconnect or restart required to recover.",
            ble_address,
        )
        return

    reset_threshold = _coerce_positive_int(
        _ble_timeout_reset_threshold, BLE_TIMEOUT_RESET_THRESHOLD
    )
    # Capture future ref inside lock, cancel outside to avoid deadlock with done callbacks
    ble_future_to_cancel = None
    orphaned_workers = 0
    stale_executor: ThreadPoolExecutor | None = None
    with _ble_executor_lock:
        if timeout_count < reset_threshold:
            return

        current_orphans = _ble_executor_orphaned_workers_by_address.get(ble_address, 0)
        if current_orphans + 1 >= EXECUTOR_ORPHAN_THRESHOLD:
            _ble_executor_degraded_addresses.add(ble_address)
            logger.error(
                "BLE EXECUTOR DEGRADED for %s: %s workers have been orphaned due to "
                "repeated hangs. Further automatic recovery is disabled for this device. "
                "Reconnect or restart the relay to restore BLE connectivity.",
                ble_address,
                current_orphans + 1,
            )
            ble_future_to_cancel = _ble_future
            _ble_future = None
            stale_executor = _ble_executor
            _ble_future_address = None
            _ble_future_started_at = None
            _ble_future_timeout_secs = None
            with _ble_timeout_lock:
                _ble_timeout_counts[ble_address] = 0

        if ble_address in _ble_executor_degraded_addresses:
            degraded_now = True
        else:
            degraded_now = False
            if _ble_future and not _ble_future.done():
                ble_future_to_cancel = _ble_future
            if _ble_executor is not None and not getattr(
                _ble_executor, "_shutdown", False
            ):
                with _ble_timeout_lock:
                    orphaned_workers = current_orphans + 1
                    _ble_executor_orphaned_workers_by_address[ble_address] = (
                        orphaned_workers
                    )
                stale_executor = _ble_executor
            logger.warning(
                "BLE worker timed out %s times for %s; recreating executor "
                "(orphaned BLE workers=%s, threshold=%s)",
                timeout_count,
                ble_address,
                orphaned_workers,
                EXECUTOR_ORPHAN_THRESHOLD,
            )
            _ble_executor = ThreadPoolExecutor(max_workers=1)
            _ble_future = None
            _ble_future_address = None
            _ble_future_started_at = None
            _ble_future_timeout_secs = None

    if degraded_now:
        if ble_future_to_cancel is not None:
            ble_future_to_cancel.cancel()
            try:
                ble_future_to_cancel.result(timeout=FUTURE_CANCEL_TIMEOUT_SECS)
            except Exception as exc:  # noqa: BLE001 - best-effort degraded cleanup
                logger.debug(
                    "BLE future cancellation raised error for %s: %s",
                    ble_address,
                    exc,
                )
        if stale_executor is not None and not getattr(
            stale_executor, "_shutdown", False
        ):
            try:
                stale_executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                stale_executor.shutdown(wait=False)
        return

    if ble_future_to_cancel is not None:
        ble_future_to_cancel.cancel()
        try:
            ble_future_to_cancel.result(timeout=FUTURE_CANCEL_TIMEOUT_SECS)
        except FuturesTimeoutError:
            pass
        except Exception as exc:  # noqa: BLE001 - best-effort reset cleanup
            logger.debug("BLE worker errored during reset: %s", exc)
    if stale_executor is not None and not getattr(stale_executor, "_shutdown", False):
        try:
            stale_executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            stale_executor.shutdown(wait=False)
    with _ble_timeout_lock:
        _ble_timeout_counts[ble_address] = 0


def _scan_for_ble_address(ble_address: str, timeout: float) -> bool:
    """
    Performs a best-effort BLE scan to check whether a device with the given address is discoverable.

    If the Bleak library is unavailable or an active asyncio event loop is running, the function does not perform a scan and returns `false`.

    Returns:
        `true` if the device address was observed in a scan within the given timeout; `false` if the device was not observed, the scan failed, Bleak is unavailable, or scanning was skipped due to an active event loop.
    """
    if not BLE_AVAILABLE:
        return False

    try:
        from bleak import BleakScanner
    except ImportError:
        return False

    async def _scan() -> bool:
        """
        Determine whether the target BLE device is discoverable within the scan timeout.

        Returns:
            bool: `True` if a device with the target BLE address is discovered within the timeout, `False` otherwise (including when BLE discovery errors occur).
        """
        try:
            find_device = getattr(BleakScanner, "find_device_by_address", None)
            if callable(find_device):
                try:
                    coro: Coroutine[Any, Any, Any] = cast(
                        Coroutine[Any, Any, Any],
                        find_device(ble_address, timeout=timeout),
                    )
                    result = await coro
                    return result is not None
                except TypeError:
                    return False

            devices = await BleakScanner.discover(timeout=timeout)
            return any(
                getattr(device, "address", None) == ble_address for device in devices
            )
        except (
            BleakError,
            BleakDBusError,
            OSError,
            RuntimeError,
            asyncio.TimeoutError,
        ) as exc:
            logger.debug("BLE scan failed for %s: %s", ble_address, exc)
            return False

    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop and running_loop.is_running():
        logger.debug(
            "Skipping BLE scan for %s; running event loop is active",
            ble_address,
        )
        return False

    try:
        return asyncio.run(_scan())
    except (
        BleakError,
        BleakDBusError,
        OSError,
        RuntimeError,
        asyncio.TimeoutError,
    ) as exc:
        logger.debug("BLE scan failed for %s: %s", ble_address, exc)
        return False


def _is_ble_discovery_error(error: Exception) -> bool:
    """
    Determine whether an exception represents a BLE discovery or connection completion failure.

    Returns:
        True if the exception indicates a BLE discovery or connection completion failure, False otherwise.
    """
    message = str(error)
    if "No Meshtastic BLE peripheral" in message:
        return True
    if "Timed out waiting for connection completion" in message:
        return True
    if isinstance(error, KeyError):
        normalized_keys = {
            str(item).strip().strip("'").strip('"') for item in error.args
        }
        if "path" in normalized_keys:
            return True

    def _is_type_or_tuple(candidate: object) -> bool:
        if isinstance(candidate, type):
            return True
        if isinstance(candidate, tuple):
            return all(isinstance(item, type) for item in candidate)
        return False

    ble_interface = getattr(meshtastic.ble_interface, "BLEInterface", None)
    ble_error_type = getattr(ble_interface, "BLEError", None)
    if (
        ble_error_type
        and _is_type_or_tuple(ble_error_type)
        and isinstance(error, ble_error_type)
    ):
        return True

    mesh_interface = getattr(meshtastic, "mesh_interface", None)
    mesh_interface_class = getattr(mesh_interface, "MeshInterface", None)
    mesh_error_type = getattr(mesh_interface_class, "MeshInterfaceError", None)
    if (
        mesh_error_type
        and _is_type_or_tuple(mesh_error_type)
        and isinstance(error, mesh_error_type)
    ):
        return True

    return False


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


def _resolve_plugin_timeout(
    cfg: dict[str, Any] | None, default: float = DEFAULT_PLUGIN_TIMEOUT_SECS
) -> float:
    """
    Resolve the plugin timeout value from the configuration.

    Reads `meshtastic.plugin_timeout` from `cfg` and returns it as a positive float. If the value is missing, cannot be converted to a number, or is not greater than 0, the provided `default` is returned and a warning is logged.

    Parameters:
        cfg (dict | None): Configuration mapping that may contain a "meshtastic" section with a "plugin_timeout" value.
        default (float): Fallback timeout in seconds used when `cfg` does not provide a valid value.

    Returns:
        float: A positive timeout in seconds.
    """

    raw_value = default
    if isinstance(cfg, dict):
        try:
            raw_value = cfg.get("meshtastic", {}).get("plugin_timeout", default)
        except AttributeError:
            raw_value = default

    try:
        if isinstance(raw_value, bool):
            raise TypeError("boolean timeout")
        timeout = float(raw_value)
        if timeout > 0 and math.isfinite(timeout):
            return timeout
        logger.warning(
            "Invalid meshtastic.plugin_timeout value %r; using %.1fs fallback.",
            raw_value,
            default,
        )
    except (TypeError, ValueError, OverflowError):
        logger.warning(
            "Invalid meshtastic.plugin_timeout value %r; using %.1fs fallback.",
            raw_value,
            default,
        )

    return default


def _resolve_plugin_result(
    handler_result: Any,
    plugin: Any,
    plugin_timeout: float,
    loop: asyncio.AbstractEventLoop,
) -> bool:
    """
    Resolve a plugin handler result to a boolean, handling async timeouts and bad awaitables.

    Returns True when the plugin should be treated as handled, False otherwise.
    """
    if not inspect.iscoroutine(handler_result) and not inspect.isawaitable(
        handler_result
    ):
        return bool(handler_result)

    result_future = _submit_coro(handler_result, loop=loop)
    if result_future is None:
        logger.warning("Plugin %s returned no awaitable; skipping.", plugin.plugin_name)
        return False
    try:
        return bool(_wait_for_result(result_future, plugin_timeout, loop=loop))
    except (asyncio.TimeoutError, FuturesTimeoutError) as exc:
        logger.warning(
            "Plugin %s did not respond within %ss: %s",
            plugin.plugin_name,
            plugin_timeout,
            exc,
        )
        return True


def _run_meshtastic_plugins(
    *,
    packet: dict[str, Any],
    formatted_message: str | None,
    longname: str | None,
    meshnet_name: str | None,
    loop: asyncio.AbstractEventLoop,
    cfg: dict[str, Any] | None,
    use_keyword_args: bool = False,
    log_with_portnum: bool = False,
    portnum: Any | None = None,
) -> bool:
    """
    Invoke Meshtastic plugins and return True when a plugin handles the message.
    """
    from mmrelay.plugin_loader import load_plugins

    plugins = load_plugins()
    plugin_timeout = _resolve_plugin_timeout(cfg, default=DEFAULT_PLUGIN_TIMEOUT_SECS)

    found_matching_plugin = False
    for plugin in plugins:
        if not found_matching_plugin:
            try:
                if use_keyword_args:
                    handler_result = plugin.handle_meshtastic_message(
                        packet,
                        formatted_message=formatted_message,
                        longname=longname,
                        meshnet_name=meshnet_name,
                    )
                else:
                    handler_result = plugin.handle_meshtastic_message(
                        packet,
                        formatted_message,
                        longname,
                        meshnet_name,
                    )

                found_matching_plugin = _resolve_plugin_result(
                    handler_result,
                    plugin,
                    plugin_timeout,
                    loop,
                )

                if found_matching_plugin:
                    if log_with_portnum:
                        logger.debug(
                            f"Processed {portnum} with plugin {plugin.plugin_name}"
                        )
                    else:
                        logger.debug(f"Processed by plugin {plugin.plugin_name}")
            except Exception:
                logger.exception(f"Plugin {plugin.plugin_name} failed")
                # Continue processing other plugins

    return found_matching_plugin


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


def _sanitize_ble_address(address: str) -> str:
    """
    Normalize a BLE address by removing common separators and converting to lowercase.

    This matches the sanitization logic used by both official and forked meshtastic
    libraries, ensuring consistent address comparison.

    Parameters:
        address: The BLE address to sanitize.

    Returns:
        Sanitized address with all "-", "_", ":" removed and lowercased.
    """
    if not address:
        return address
    return address.replace("-", "").replace("_", "").replace(":", "").lower()


def _validate_ble_connection_address(interface: Any, expected_address: str) -> bool:
    """
    Validate that a BLE interface is connected to the configured device address.

    Compares the configured address to the interface's connected address after normalizing
    (both addresses have separators removed and are lowercased). Works with both the
    official and forked Meshtastic interface shapes by attempting common attribute paths.
    This is a best-effort check: if the connected address cannot be determined the
    function returns `True` to avoid false negatives; it returns `False` only when a
    determinate mismatch is detected.

    Parameters:
        interface (Any): BLE interface object whose connected address should be inspected.
        expected_address (str): Configured BLE device address to validate against.

    Returns:
        bool: `True` if the connected device matches `expected_address` or the address
        cannot be determined, `False` if a definitive mismatch is found.
    """
    try:
        expected_sanitized = _sanitize_ble_address(expected_address)

        # Try to get the actual connected address from the interface
        actual_address = None
        actual_sanitized = None

        if hasattr(interface, "client") and interface.client is not None:
            # Official version: client has bleak_client attribute
            bleak_client = getattr(interface.client, "bleak_client", None)
            if bleak_client is not None:
                actual_address = getattr(bleak_client, "address", None)
            # Forked version: client might be wrapped differently
            if actual_address is None:
                actual_address = getattr(interface.client, "address", None)

        if actual_address is None:
            logger.warning(
                "Could not determine connected BLE device address for validation. "
                "Proceeding with caution - verify correct device is connected."
            )
            return True

        actual_sanitized = _sanitize_ble_address(actual_address)

        if actual_sanitized == expected_sanitized:
            logger.debug(
                f"BLE connection validation passed: connected to {actual_address} "
                f"(expected: {expected_address})"
            )
            return True
        else:
            logger.error(
                f"BLE CONNECTION VALIDATION FAILED: Connected to {actual_address} "
                f"but expected {expected_address}. This could be caused by "
                "substring matching in device discovery selecting wrong device. "
                "Disconnecting to prevent misconfiguration."
            )
            return False
    except Exception as e:  # noqa: BLE001 - validation is best-effort
        logger.warning(
            f"Error during BLE connection address validation: {e}. "
            "Proceeding with caution."
        )
        return True


def _disconnect_ble_by_address(address: str) -> None:
    """
    Disconnect a potentially stale BlueZ BLE connection for the given address.

    If a BleakClient is available, attempts a graceful disconnect with retries and timeouts.
    Operates correctly from an existing asyncio event loop or by creating a temporary loop
    when none is running. If Bleak is not installed, the function exits silently.

    Parameters:
        address (str): BLE address of the device to disconnect (any common separator format).
    """
    logger.debug(f"Checking for stale BlueZ connection to {address}")

    try:
        from bleak import BleakClient
        from bleak.exc import BleakDBusError as BleakClientDBusError
        from bleak.exc import BleakError as BleakClientError

        async def disconnect_stale_connection() -> None:
            """
            Perform a best-effort disconnect of a stale BlueZ BLE connection for the target address.

            Attempts to detect whether the Bleak client for the configured address is connected and, if so, issues a bounded series of disconnect attempts with timeouts and short settle delays. All errors and timeouts are suppressed (logged at debug/warning levels) so this function never raises; a final cleanup disconnect is always attempted.
            """
            BLEAK_EXCEPTIONS = (
                BleakClientError,
                BleakClientDBusError,
                OSError,
                RuntimeError,
                # Bleak/DBus can raise these during teardown with malformed payloads
                # or unexpected awaitable shapes; cleanup stays best-effort.
                ValueError,
                TypeError,
            )
            client = None
            try:
                client = BleakClient(address)

                connected_status = None
                is_connected_method = getattr(client, "is_connected", None)

                # Bleak exposes either an is_connected() method or a bool attribute,
                # depending on version/backend; treat unknown shapes as disconnected
                # to keep this cleanup best-effort and non-blocking.
                # Bleak backends differ: is_connected may be sync (bool) or async.
                # Handle both to keep this cleanup path resilient to mocks and
                # backend-specific behavior.
                if is_connected_method and callable(is_connected_method):
                    try:
                        connected_result = is_connected_method()
                    except BLEAK_EXCEPTIONS as e:
                        logger.debug(
                            "Failed to call is_connected for %s: %s", address, e
                        )
                        return
                    if inspect.isawaitable(connected_result):
                        connected_status = await cast(Awaitable[bool], connected_result)
                    elif isinstance(connected_result, bool):
                        connected_status = connected_result
                    else:
                        # Unexpected return type; treat as disconnected so cleanup
                        # remains non-blocking in test/mocked environments.
                        connected_status = False
                elif isinstance(is_connected_method, bool):
                    connected_status = is_connected_method
                else:
                    connected_status = False
            except BLEAK_EXCEPTIONS as e:
                # Bleak backends raise a mix of DBus/IO errors; treat them as
                # non-fatal because stale disconnects are best-effort cleanup.
                logger.debug(
                    "Failed to check connection state for %s: %s",
                    address,
                    e,
                    exc_info=True,
                )
                return

            try:
                if connected_status:
                    logger.warning(
                        f"Device {address} is already connected in BlueZ. Disconnecting..."
                    )
                    # Retry logic for disconnect with timeout
                    max_retries = BLE_DISCONNECT_MAX_RETRIES
                    for attempt in range(max_retries):
                        try:
                            # Some backends or test doubles return a sync result
                            # from disconnect(); only await when needed.
                            disconnect_result = client.disconnect()
                            if inspect.isawaitable(disconnect_result):
                                await asyncio.wait_for(
                                    disconnect_result,
                                    timeout=BLE_DISCONNECT_TIMEOUT_SECS,
                                )
                            await asyncio.sleep(BLE_DISCONNECT_SETTLE_SECS)
                            logger.debug(
                                "Successfully disconnected stale connection to %s on attempt %s, "
                                "waiting %.1fs for BlueZ to settle",
                                address,
                                attempt + 1,
                                BLE_DISCONNECT_SETTLE_SECS,
                            )
                            break
                        except asyncio.TimeoutError:
                            if attempt < max_retries - 1:
                                logger.warning(
                                    f"Disconnect attempt {attempt + 1} for {address} timed out, retrying..."
                                )
                                await asyncio.sleep(BLE_RETRY_DELAY_SECS)
                            else:
                                logger.warning(
                                    f"Disconnect for {address} timed out after {max_retries} attempts"
                                )
                        except BLEAK_EXCEPTIONS as e:
                            # Bleak disconnects can throw DBus/IO errors depending
                            # on adapter state; retry a few times then give up.
                            if attempt < max_retries - 1:
                                logger.warning(
                                    "Disconnect attempt %s for %s failed: %s, retrying...",
                                    attempt + 1,
                                    address,
                                    e,
                                    exc_info=True,
                                )
                                await asyncio.sleep(BLE_RETRY_DELAY_SECS)
                            else:
                                logger.warning(
                                    "Disconnect for %s failed after %s attempts: %s",
                                    address,
                                    max_retries,
                                    e,
                                    exc_info=True,
                                )
                else:
                    logger.debug(f"Device {address} not currently connected in BlueZ")
            except BLEAK_EXCEPTIONS as e:
                # Stale disconnects are best-effort; do not fail startup/reconnect
                # on cleanup errors from BlueZ/DBus.
                logger.debug(
                    "Error disconnecting stale connection to %s",
                    address,
                    exc_info=e,
                )
            finally:
                try:
                    if client:
                        # Always attempt a short final disconnect to release the
                        # adapter even when we think it's already disconnected.
                        # Some backends or test doubles return a sync result
                        # from disconnect(); only await when needed.
                        disconnect_result = client.disconnect()
                        if inspect.isawaitable(disconnect_result):
                            await asyncio.wait_for(
                                disconnect_result, timeout=BLE_DISCONNECT_TIMEOUT_SECS
                            )
                        await asyncio.sleep(BLE_DISCONNECT_SETTLE_SECS)
                except asyncio.TimeoutError:
                    logger.debug(f"Final disconnect for {address} timed out (cleanup)")
                except BLEAK_EXCEPTIONS as e:
                    # Ignore disconnect errors during cleanup - connection may already be closed
                    logger.debug(
                        "Final disconnect for %s failed during cleanup",
                        address,
                        exc_info=e,
                    )

        runtime_error: RuntimeError | None = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as e:
            loop = None
            runtime_error = e

        if loop and loop.is_running():
            logger.debug(
                "Found running event loop; scheduling disconnect task for %s",
                address,
            )
            _fire_and_forget(disconnect_stale_connection(), loop=loop)
            return

        if event_loop and getattr(event_loop, "is_running", lambda: False)():
            logger.debug(
                "Using global event loop, waiting for disconnect task for %s",
                address,
            )
            future = asyncio.run_coroutine_threadsafe(
                disconnect_stale_connection(), event_loop
            )
            try:
                future.result(timeout=STALE_DISCONNECT_TIMEOUT_SECS)
                logger.debug(f"Stale connection disconnect completed for {address}")
            except FuturesTimeoutError:
                logger.warning(
                    f"Stale connection disconnect timed out after {STALE_DISCONNECT_TIMEOUT_SECS:.0f}s for {address}"
                )
                if not future.done():
                    # Cancel the cleanup task so we do not block a new connect
                    # attempt on a hung DBus/Bleak operation.
                    future.cancel()
            return

        # No running event loop in this thread (and no global loop to target);
        # create a temporary loop to perform a blocking best-effort cleanup.
        logger.debug(
            "No running event loop (RuntimeError: %s), creating temporary loop for %s",
            runtime_error,
            address,
        )
        asyncio.run(disconnect_stale_connection())
        logger.debug(f"Stale connection disconnect completed for {address}")
    except ImportError:
        # Bleak is optional in some deployments; skip stale cleanup rather than
        # breaking startup when BLE support isn't installed.
        logger.debug("BleakClient not available for stale connection cleanup")
    except Exception as e:  # noqa: BLE001 - disconnect cleanup must not block startup
        # Other errors during best-effort disconnect (e.g., from future.result() or asyncio.run())
        # are non-fatal; log and continue.
        logger.debug(
            "Error during BLE disconnect cleanup for %s",
            address,
            exc_info=e,
        )


def _disconnect_ble_interface(iface: Any, reason: str = "disconnect") -> None:
    """
    Tear down a BLE interface and release its underlying Bluetooth resources.

    Safely disconnects and closes the provided BLE interface (no-op if `None`), suppressing non-fatal errors
    and ensuring the Bluetooth adapter has time to release the connection.

    Parameters:
        iface (Any): BLE interface instance to disconnect; may be `None`.
        reason (str): Short human-readable reason included in log messages.
    """
    if iface is None:
        return

    # Pre-disconnect delay to allow pending notifications to complete
    # This helps prevent "Unexpected EOF on notification file handle" errors
    logger.debug(f"Waiting before disconnecting BLE interface ({reason})")
    time.sleep(0.5)
    timeout_log_level = logging.DEBUG if reason == "shutdown" else logging.WARNING
    retry_log = logger.debug if reason == "shutdown" else logger.warning
    final_log = logger.debug if reason == "shutdown" else logger.error

    try:
        if hasattr(iface, "_exit_handler") and iface._exit_handler:
            # Best-effort: avoid atexit callbacks blocking shutdown when the
            # official library registers close handlers we already ran.
            with contextlib.suppress(Exception):
                atexit.unregister(iface._exit_handler)
            iface._exit_handler = None

        # Check if interface has a disconnect method (forked version)
        if hasattr(iface, "disconnect"):
            logger.debug(f"Disconnecting BLE interface ({reason})")

            # Retry logic for disconnect operations
            max_disconnect_retries = 3
            for attempt in range(max_disconnect_retries):
                try:
                    disconnect_method = iface.disconnect
                    if inspect.iscoroutinefunction(disconnect_method):
                        _wait_for_result(disconnect_method(), timeout=3.0)
                    else:
                        # Run sync disconnect in a daemon thread to avoid hangs.
                        def _disconnect_sync(
                            method: Callable[[], Any] = disconnect_method,
                        ) -> None:
                            """
                            Call the provided disconnect callable and wait briefly if it returns an awaitable.

                            Parameters:
                                method (Callable[[], Any]): A zero-argument callable that performs a disconnect. If omitted, a module-level
                                    default `disconnect_method` is used. If the callable returns an awaitable, this function will wait up to
                                    3.0 seconds for completion.
                            """
                            result = method()
                            if inspect.isawaitable(result):
                                _wait_for_result(result, timeout=3.0)

                        _run_blocking_with_timeout(
                            _disconnect_sync,
                            timeout=3.0,
                            label=f"ble-interface-disconnect-{reason}",
                            timeout_log_level=timeout_log_level,
                        )
                    # Give the adapter time to complete the disconnect
                    time.sleep(1.0)
                    logger.debug(
                        f"BLE interface disconnect succeeded on attempt {attempt + 1} ({reason})"
                    )
                    break
                except Exception as e:
                    if attempt < max_disconnect_retries - 1:
                        retry_log(
                            f"BLE interface disconnect attempt {attempt + 1} failed ({reason}): {e}, retrying..."
                        )
                        time.sleep(0.5)
                    else:
                        final_log(
                            f"BLE interface disconnect failed after {max_disconnect_retries} attempts ({reason}): {e}"
                        )
        else:
            logger.debug(
                f"BLE interface has no disconnect() method, using close() only ({reason})"
            )

        # Always call close() to release resources
        logger.debug(f"Closing BLE interface ({reason})")

        # For BLE interfaces, explicitly disconnect the underlying BleakClient
        # to prevent stale connections in BlueZ (official library bug)
        # Check that client attribute exists AND is not None (handles forked lib close race)
        if getattr(iface, "client", None) is not None:
            logger.debug(f"Explicitly disconnecting BLE client ({reason})")

            # Retry logic for client disconnect
            max_client_retries = 2
            for attempt in range(max_client_retries):
                # Re-check client before each attempt (may become None during close)
                client_obj = getattr(iface, "client", None)
                if client_obj is None:
                    logger.debug(
                        f"BLE client became None before attempt {attempt + 1} ({reason}), skipping"
                    )
                    break
                try:
                    disconnect_method = client_obj.disconnect
                    # Check _exit_handler on the client object safely
                    client_exit_handler = getattr(client_obj, "_exit_handler", None)
                    if client_exit_handler:
                        with contextlib.suppress(ValueError):
                            atexit.unregister(client_exit_handler)
                        with contextlib.suppress(AttributeError, TypeError):
                            client_obj._exit_handler = None
                    with contextlib.suppress(ValueError):
                        atexit.unregister(disconnect_method)

                    if inspect.iscoroutinefunction(disconnect_method):
                        _wait_for_result(disconnect_method(), timeout=2.0)
                    else:
                        # Run sync disconnect in a daemon thread so it cannot
                        # block shutdown if BlueZ/DBus is hung.
                        def _disconnect_sync(
                            method: Callable[[], Any] = disconnect_method,
                        ) -> None:
                            """
                            Call a disconnection callable and, if it returns an awaitable, wait up to 2 seconds for it to complete.

                            Parameters:
                                method (Callable[[], Any]): A synchronous or asynchronous disconnect callable to invoke. If it returns an awaitable, this function will wait up to 2.0 seconds for completion.
                            """
                            result = method()
                            if inspect.isawaitable(result):
                                _wait_for_result(result, timeout=2.0)

                        _run_blocking_with_timeout(
                            _disconnect_sync,
                            timeout=2.0,
                            label=f"ble-client-disconnect-{reason}",
                            timeout_log_level=timeout_log_level,
                        )
                    time.sleep(1.0)
                    logger.debug(
                        f"BLE client disconnect succeeded on attempt {attempt + 1} ({reason})"
                    )
                    break
                except Exception as e:
                    if attempt < max_client_retries - 1:
                        retry_log(
                            f"BLE client disconnect attempt {attempt + 1} failed ({reason}): {e}, retrying..."
                        )
                        time.sleep(0.3)
                    else:
                        # Ignore disconnect errors on final attempt - connection may already be closed
                        logger.debug(
                            f"BLE client disconnect failed after {max_client_retries} attempts ({reason}): {e}"
                        )

        close_method = iface.close
        with contextlib.suppress(Exception):
            atexit.unregister(close_method)
        if inspect.iscoroutinefunction(close_method):
            _wait_for_result(close_method(), timeout=5.0)
        else:
            # Close can block indefinitely in the official library; run it in
            # a daemon thread with a timeout to allow clean shutdown.
            def _close_sync(method: Callable[[], Any] = close_method) -> None:
                """
                Invoke a close-like callable and, if it returns an awaitable, wait up to 5 seconds for it to complete.

                Parameters:
                    method (Callable[[], Any]): A zero-argument function that performs a close/teardown action. If the callable returns an awaitable, this function will wait up to 5.0 seconds for completion.
                """
                result = method()
                if inspect.isawaitable(result):
                    _wait_for_result(result, timeout=5.0)

            _run_blocking_with_timeout(
                _close_sync,
                timeout=5.0,
                label=f"ble-interface-close-{reason}",
                timeout_log_level=timeout_log_level,
            )
    except TimeoutError as exc:
        logger.debug("BLE interface %s timed out: %s", reason, exc)
    except Exception as e:  # noqa: BLE001 - cleanup must not block shutdown
        logger.debug(f"Error during BLE interface {reason}", exc_info=e)
    finally:
        # Small delay to ensure the adapter has fully released the connection
        time.sleep(0.5)


def _get_packet_details(
    decoded: dict[str, Any] | None, packet: dict[str, Any], portnum_name: str
) -> dict[str, Any]:
    """
    Extract telemetry, signal, relay, and priority fields from a Meshtastic packet for logging.

    Parameters:
        decoded: Decoded packet payload (may be None); used to extract telemetry fields when present.
        packet: Full packet dictionary; used to extract signal (RSSI/SNR), relay, and priority information.
        portnum_name: Port identifier name (e.g., "TELEMETRY_APP") that determines telemetry parsing.

    Returns:
        dict: Mapping of short detail keys to formatted string values (e.g., 'batt': '85%', 'signal': 'RSSI:-70 SNR:7.5').
    """
    details = {}

    if decoded and isinstance(decoded, dict) and portnum_name == "TELEMETRY_APP":
        if (telemetry := decoded.get("telemetry")) and isinstance(telemetry, dict):
            if (metrics := telemetry.get("deviceMetrics")) and isinstance(
                metrics, dict
            ):
                if (batt := metrics.get("batteryLevel")) is not None:
                    details["batt"] = f"{batt}%"
                if (voltage := metrics.get("voltage")) is not None:
                    details["voltage"] = f"{voltage:.2f}V"
            elif (metrics := telemetry.get("environmentMetrics")) and isinstance(
                metrics, dict
            ):
                if (temp := metrics.get("temperature")) is not None:
                    details["temp"] = f"{temp:.1f}°C"
                if (humidity := metrics.get("relativeHumidity")) is not None:
                    details["humidity"] = f"{humidity:.0f}%"

    signal_info = []
    rssi = packet.get("rxRssi")
    if rssi is not None:
        signal_info.append(f"RSSI:{rssi}")
    snr = packet.get("rxSnr")
    if snr is not None:
        signal_info.append(f"SNR:{snr:.1f}")
    if signal_info:
        details["signal"] = " ".join(signal_info)

    relay = packet.get("relayNode")
    if relay is not None and relay != 0:
        details["relayed"] = f"via {relay}"

    priority = packet.get("priority")
    if priority and priority != "NORMAL":
        details["priority"] = priority

    return details


def _get_portnum_name(portnum: Any) -> str:
    """
    Get a human-readable name for a Meshtastic port identifier.

    Accepts an integer enum value, a string name, or None. For a valid enum integer returns the enum name; for a non-empty string returns it unchanged; for None, an empty string, an unknown integer, or an unexpected type returns a descriptive "UNKNOWN (...)" string.

    Parameters:
        portnum (Any): The port identifier to convert; may be an int enum value, a string name, or None.

    Returns:
        str: The resolved port name or an `UNKNOWN (...)` description for invalid or missing inputs.
    """
    if portnum is None:
        return "UNKNOWN (None)"

    if isinstance(portnum, str):
        if portnum:
            return portnum
        return "UNKNOWN (empty string)"

    if isinstance(portnum, int) and not isinstance(portnum, bool):
        try:
            return portnums_pb2.PortNum.Name(portnum)  # type: ignore[no-any-return]
        except ValueError:
            return f"UNKNOWN (portnum={portnum})"

    return f"UNKNOWN (type={type(portnum).__name__})"


def _get_node_display_name(
    from_id: int | str, interface: Any, fallback: str | None = None
) -> str:
    """
    Get a human-readable display name for a Meshtastic node.

    Prioritizes short name from interface, then short name from database,
    then long name from database, falling back to node ID if none found.

    Parameters:
        from_id: Meshtastic node identifier (int or str)
        interface: Meshtastic interface with nodes mapping
        fallback: Optional fallback string if no name found; when None, uses the node ID

    Returns:
        str: Node display name or node ID if no name available
    """
    from_id_str = str(from_id)

    if interface and hasattr(interface, "nodes"):
        nodes = interface.nodes
        if nodes and isinstance(nodes, dict):
            if from_id_str in nodes:
                node = nodes[from_id_str]
                if isinstance(node, dict):
                    user = node.get("user")
                    if user and isinstance(user, dict):
                        if short_name := user.get("shortName"):
                            return cast(str, short_name)

    from mmrelay.db_utils import get_longname, get_shortname

    if short_name := get_shortname(from_id_str):
        return short_name

    if long_name := get_longname(from_id_str):
        return long_name

    return fallback if fallback is not None else from_id_str


def serial_port_exists(port_name: str) -> bool:
    """
    Determine whether a serial port with the given device name exists on the system.

    Parameters:
        port_name (str): Device name to check (e.g., '/dev/ttyUSB0' on Unix or 'COM3' on Windows).

    Returns:
        `True` if a matching port device name is present, `False` otherwise.
    """
    ports = [p.device for p in serial.tools.list_ports.comports()]
    return port_name in ports


def _get_connection_retry_wait_time(attempts: int) -> float:
    """Return capped exponential retry backoff without exponentiating past the cap."""
    if attempts <= 0 or CONNECTION_RETRY_BACKOFF_MAX_SECS <= 0:
        return 0.0

    if CONNECTION_RETRY_BACKOFF_BASE <= 1:
        return min(
            float(CONNECTION_RETRY_BACKOFF_BASE**attempts),
            float(CONNECTION_RETRY_BACKOFF_MAX_SECS),
        )

    max_capped_attempt = math.ceil(
        math.log(
            CONNECTION_RETRY_BACKOFF_MAX_SECS,
            CONNECTION_RETRY_BACKOFF_BASE,
        )
    )
    exponent = min(attempts, max_capped_attempt)
    return min(
        float(CONNECTION_RETRY_BACKOFF_BASE**exponent),
        float(CONNECTION_RETRY_BACKOFF_MAX_SECS),
    )


def connect_meshtastic(
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
    global meshtastic_client, meshtastic_iface, shutting_down, reconnecting, config
    global RELAY_START_TIME, _relay_rx_time_clock_skew_secs
    global matrix_rooms, _ble_future, _ble_future_address
    global _ble_future_started_at, _ble_future_timeout_secs
    if shutting_down:
        logger.debug("Shutdown in progress. Not attempting to connect.")
        return None

    if reconnecting and not force_connect:
        logger.debug("Reconnection already in progress. Not attempting new connection.")
        return None

    # Update the global config if a config is passed
    if passed_config is not None:
        config = passed_config

        # If config is valid, extract matrix_rooms
        if config and "matrix_rooms" in config:
            matrix_rooms = config["matrix_rooms"]

    with meshtastic_lock:
        if meshtastic_client and not force_connect:
            return meshtastic_client

        # Close previous connection if exists
        if meshtastic_client:
            try:
                if meshtastic_client is meshtastic_iface:
                    # BLE needs an explicit disconnect to release BlueZ state; a
                    # plain close() can leave the adapter "busy" for the next
                    # connect attempt.
                    _disconnect_ble_interface(meshtastic_iface, reason="reconnect")
                    meshtastic_iface = None
                else:
                    meshtastic_client.close()
            except Exception as e:
                logger.warning(
                    "Error closing previous connection: %s", e, exc_info=True
                )
            meshtastic_client = None

        # Check if config is available
        if config is None:
            logger.error("No configuration available. Cannot connect to Meshtastic.")
            return None

        # Check if meshtastic config section exists
        if (
            CONFIG_SECTION_MESHTASTIC not in config
            or config[CONFIG_SECTION_MESHTASTIC] is None
        ):
            logger.error(
                "No Meshtastic configuration section found. Cannot connect to Meshtastic."
            )
            return None

        # Check if connection_type is specified
        if (
            CONFIG_KEY_CONNECTION_TYPE not in config[CONFIG_SECTION_MESHTASTIC]
            or config[CONFIG_SECTION_MESHTASTIC][CONFIG_KEY_CONNECTION_TYPE] is None
        ):
            logger.error(
                "No connection type specified in Meshtastic configuration. Cannot connect to Meshtastic."
            )
            return None

        # Determine connection type and attempt connection
        connection_type = config[CONFIG_SECTION_MESHTASTIC][CONFIG_KEY_CONNECTION_TYPE]

        # Support legacy "network" connection type (now "tcp")
        if connection_type == CONNECTION_TYPE_NETWORK:
            connection_type = CONNECTION_TYPE_TCP
            logger.warning(
                "Using 'network' connection type (legacy). 'tcp' is now the preferred name and 'network' will be deprecated in a future version."
            )

    # Move retry loop outside the lock to prevent blocking other threads
    meshtastic_settings = config.get(CONFIG_SECTION_MESHTASTIC, {}) if config else {}
    retry_limit_raw = meshtastic_settings.get("retries")
    if retry_limit_raw is None:
        retry_limit_raw = meshtastic_settings.get("retry_limit", INFINITE_RETRIES)
        if "retry_limit" in meshtastic_settings:
            logger.warning(
                "'retry_limit' is deprecated in meshtastic config; use 'retries' instead"
            )
    try:
        retry_limit = int(retry_limit_raw)
    except (TypeError, ValueError):
        retry_limit = INFINITE_RETRIES
    attempts = 0
    timeout_attempts = 0
    successful = False
    ble_scan_after_failure = False
    ble_scan_reason: str | None = None

    # Get timeout configuration (default: DEFAULT_MESHTASTIC_TIMEOUT)
    timeout_raw = meshtastic_settings.get(
        CONFIG_KEY_TIMEOUT, DEFAULT_MESHTASTIC_TIMEOUT
    )
    try:
        timeout = int(timeout_raw)
        if timeout <= 0:
            logger.warning(
                "Non-positive meshtastic.timeout value %r; using %ss fallback.",
                timeout_raw,
                DEFAULT_MESHTASTIC_TIMEOUT,
            )
            timeout = DEFAULT_MESHTASTIC_TIMEOUT
    except (TypeError, ValueError):
        # None or invalid value - use default silently
        if timeout_raw is not None:
            logger.warning(
                "Invalid meshtastic.timeout value %r; using %ss fallback.",
                timeout_raw,
                DEFAULT_MESHTASTIC_TIMEOUT,
            )
        timeout = DEFAULT_MESHTASTIC_TIMEOUT
    configured_timeout_secs = float(timeout)
    configured_timeout_arg = max(1, math.ceil(configured_timeout_secs))
    create_timeout_floor_secs = _coerce_positive_float(
        _ble_interface_create_timeout_secs,
        BLE_INTERFACE_CREATE_TIMEOUT_FLOOR_SECS,
        "_ble_interface_create_timeout_secs",
    )
    ble_create_timeout_secs = max(configured_timeout_secs, create_timeout_floor_secs)

    while (
        not successful
        and (retry_limit == 0 or attempts <= retry_limit)
        and not shutting_down
    ):
        # Initialize before try block to avoid unbound variable errors
        ble_address: str | None = None
        supports_auto_reconnect = False

        try:
            client = None
            if connection_type == CONNECTION_TYPE_SERIAL:
                # Serial connection
                serial_port = config["meshtastic"].get(CONFIG_KEY_SERIAL_PORT)
                if not serial_port:
                    logger.error(
                        "No serial port specified in Meshtastic configuration."
                    )
                    return None

                logger.info(f"Connecting to serial port {serial_port}")

                # Check if serial port exists before connecting
                if not serial_port_exists(serial_port):
                    raise serial.SerialException(
                        f"Serial port {serial_port} does not exist."
                    )

                client = meshtastic.serial_interface.SerialInterface(
                    serial_port, timeout=configured_timeout_arg
                )

            elif connection_type == CONNECTION_TYPE_BLE:
                # BLE connection
                ble_address = config["meshtastic"].get(CONFIG_KEY_BLE_ADDRESS)
                if ble_address:
                    logger.info(f"Connecting to BLE address {ble_address}")

                    iface = None
                    supports_auto_reconnect = False
                    late_creation_disposer_future: Future[Any] | None = None
                    with meshtastic_iface_lock:
                        # If BLE address has changed, re-create the interface
                        if (
                            meshtastic_iface
                            and getattr(meshtastic_iface, "address", None)
                            != ble_address
                        ):
                            old_address = getattr(
                                meshtastic_iface, "address", "unknown"
                            )
                            logger.info(
                                f"BLE address has changed from {old_address} to {ble_address}. "
                                "Disconnecting old interface and creating new one."
                            )
                            # Properly disconnect the old interface to ensure sequential connections
                            _disconnect_ble_interface(
                                meshtastic_iface, reason="address change"
                            )
                            meshtastic_iface = None

                        if meshtastic_iface is None:
                            # Disconnect any stale BlueZ connection before creating new interface
                            _disconnect_ble_by_address(ble_address)

                            # Create a single BLEInterface instance for process lifetime
                            sanitized_address = _sanitize_ble_address(ble_address)
                            logger.debug(
                                f"Creating new BLE interface for {ble_address} (sanitized: {sanitized_address})"
                            )
                            # Detect whether this BLEInterface implementation supports
                            # explicit auto_reconnect control.
                            ble_init_sig = inspect.signature(
                                meshtastic.ble_interface.BLEInterface.__init__
                            )
                            ble_kwargs = {
                                "address": ble_address,
                                "noProto": False,
                                "debugOut": None,
                                "noNodes": False,
                                # Preserve user-configured Meshtastic reply timeout.
                                "timeout": configured_timeout_arg,
                            }

                            # Configure auto_reconnect only when supported.
                            supports_auto_reconnect = (
                                "auto_reconnect" in ble_init_sig.parameters
                            )
                            if supports_auto_reconnect:
                                ble_kwargs["auto_reconnect"] = False
                                logger.debug(
                                    "BLEInterface supports auto_reconnect; setting auto_reconnect=False "
                                    "to ensure sequential reconnection control"
                                )
                            else:
                                logger.debug(
                                    "BLEInterface auto_reconnect parameter not available; using compatibility mode"
                                )
                            # Compatibility mode (official library) can benefit from
                            # pre-scan retries. Forked interfaces that expose
                            # auto_reconnect already perform richer connect/discovery
                            # orchestration, so avoid duplicate scan work there.
                            if ble_scan_after_failure and not supports_auto_reconnect:
                                scan_timeout_secs = _coerce_positive_float(
                                    _ble_scan_timeout_secs,
                                    BLE_SCAN_TIMEOUT_SECS,
                                    "_ble_scan_timeout_secs",
                                )
                                logger.debug(
                                    "Scanning for BLE device before retrying %s (%s)",
                                    ble_address,
                                    ble_scan_reason or "previous failure",
                                )
                                _scan_for_ble_address(ble_address, scan_timeout_secs)
                                ble_scan_after_failure = False
                                ble_scan_reason = None

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
                                return meshtastic.ble_interface.BLEInterface(**kwargs)

                            # Use the larger of configured connect timeout and the safety floor.
                            # This keeps stale-worker detection and future.result() budget aligned
                            # with the actual BLEInterface constructor timeout.
                            create_timeout_secs = ble_create_timeout_secs

                            # Guard against overlapping BLE tasks: if a previous BLE operation is
                            # still running (often due to a hung BlueZ/DBus call), we skip queuing
                            # a new task. Raising TimeoutError here intentionally reuses the
                            # existing retry/backoff logic rather than silently proceeding.
                            if shutting_down:
                                logger.debug(
                                    "Skipping BLE interface creation for %s (shutting down)",
                                    ble_address,
                                )
                                raise TimeoutError(
                                    f"BLE interface creation cancelled for {ble_address} (shutting down)."
                                )

                            _ensure_ble_worker_available(
                                ble_address,
                                operation="interface creation",
                            )

                            try:
                                with _ble_executor_lock:
                                    if _ble_future and not _ble_future.done():
                                        logger.debug(
                                            "BLE worker busy; skipping interface creation for %s",
                                            ble_address,
                                        )
                                        raise TimeoutError(
                                            f"BLE interface creation already in progress for {ble_address}."
                                        )
                                    if ble_address in _ble_executor_degraded_addresses:
                                        logger.error(
                                            "BLE executor degraded for %s: too many orphaned workers. "
                                            "Reconnect or restart required to restore BLE connectivity.",
                                            ble_address,
                                        )
                                        raise BleExecutorDegradedError(
                                            f"BLE executor degraded for {ble_address}; reset required"
                                        )
                                    try:
                                        future = _get_ble_executor().submit(
                                            create_ble_interface, ble_kwargs
                                        )
                                    except RuntimeError as exc:
                                        # The shared executor can be shutting down during interpreter
                                        # teardown; treat this as a timeout so retry logic applies.
                                        logger.exception(
                                            "BLE interface creation submission failed for %s",
                                            ble_address,
                                        )
                                        raise TimeoutError(
                                            f"BLE interface creation could not be scheduled for {ble_address}."
                                        ) from exc
                                    _ble_future = future
                                    _ble_future_address = ble_address
                                    _ble_future_started_at = time.monotonic()
                                    _ble_future_timeout_secs = create_timeout_secs
                                future.add_done_callback(_clear_ble_future)
                                try:
                                    meshtastic_iface = future.result(
                                        timeout=create_timeout_secs
                                    )
                                    logger.debug(
                                        f"BLE interface created successfully for {ble_address}"
                                    )
                                    if hasattr(meshtastic_iface, "auto_reconnect"):
                                        supports_auto_reconnect = True
                                    else:
                                        reset_executor_degraded_state(
                                            ble_address=ble_address
                                        )
                                except FuturesTimeoutError as err:
                                    logger.error(
                                        "BLE interface creation timed out after %.1f seconds for %s.",
                                        create_timeout_secs,
                                        ble_address,
                                        exc_info=True,
                                    )
                                    logger.warning(
                                        "This may indicate a stale BlueZ connection or Bluetooth adapter issue."
                                    )
                                    logger.warning(
                                        BLE_TROUBLESHOOTING_GUIDANCE.format(
                                            ble_address=ble_address
                                        )
                                    )
                                    # Best-effort cancellation: if the worker is hung we cannot force
                                    # it to stop, but this signals intent and lets retries proceed
                                    # only if the future transitions to done/cancelled.
                                    if future.cancel():
                                        _clear_ble_future(future)
                                    else:
                                        _schedule_ble_future_cleanup(
                                            future,
                                            ble_address,
                                            reason="interface creation timeout",
                                        )
                                        late_creation_disposer_future = future
                                        timeout_count = _record_ble_timeout(ble_address)
                                        _maybe_reset_ble_executor(
                                            ble_address, timeout_count
                                        )
                                    meshtastic_iface = None
                                    raise TimeoutError(
                                        f"BLE connection attempt timed out for {ble_address}."
                                    ) from err
                            except TimeoutError:
                                raise
                            except Exception:
                                # Late BLE worker failures can surface during shutdown
                                # after cancellation. Treat those as expected noise.
                                if shutting_down:
                                    logger.debug(
                                        "BLE interface creation ended during shutdown for %s",
                                        ble_address,
                                        exc_info=True,
                                    )
                                else:
                                    logger.exception("BLE interface creation failed")
                                raise
                        else:
                            logger.debug(
                                f"Reusing existing BLE interface for {ble_address}"
                            )
                            if hasattr(meshtastic_iface, "auto_reconnect"):
                                supports_auto_reconnect = True
                            else:
                                try:
                                    existing_sig = inspect.signature(
                                        type(meshtastic_iface).__init__
                                    )
                                    supports_auto_reconnect = (
                                        "auto_reconnect" in existing_sig.parameters
                                    )
                                except (TypeError, ValueError):
                                    supports_auto_reconnect = False

                        iface = meshtastic_iface

                    if late_creation_disposer_future is not None:
                        _attach_late_ble_interface_disposer(
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
                        logger.info(
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
                        if shutting_down:
                            logger.debug(
                                "Skipping BLE connect() for %s (shutting down)",
                                ble_address,
                            )
                            raise TimeoutError(
                                f"BLE connect cancelled for {ble_address} (shutting down)."
                            )

                        _ensure_ble_worker_available(
                            ble_address,
                            operation="connect",
                        )

                        with _ble_executor_lock:
                            if _ble_future and not _ble_future.done():
                                logger.debug(
                                    "BLE worker busy; skipping connect() for %s",
                                    ble_address,
                                )
                                raise TimeoutError(
                                    f"BLE connect already in progress for {ble_address}."
                                )
                            if ble_address in _ble_executor_degraded_addresses:
                                logger.error(
                                    "BLE executor degraded for %s: too many orphaned workers. "
                                    "Reconnect or restart required to restore BLE connectivity.",
                                    ble_address,
                                )
                                raise BleExecutorDegradedError(
                                    f"BLE executor degraded for {ble_address}; reset required"
                                )
                            try:
                                connect_future = _get_ble_executor().submit(
                                    connect_iface, iface
                                )
                            except RuntimeError as exc:
                                logger.exception(
                                    "BLE connect() submission failed for %s",
                                    ble_address,
                                )
                                raise TimeoutError(
                                    f"BLE connect could not be scheduled for {ble_address}."
                                ) from exc
                            _ble_future = connect_future
                            _ble_future_address = ble_address
                            _ble_future_started_at = time.monotonic()
                            _ble_future_timeout_secs = BLE_CONNECT_TIMEOUT_SECS
                        connect_future.add_done_callback(_clear_ble_future)
                        try:
                            connect_future.result(timeout=BLE_CONNECT_TIMEOUT_SECS)
                            logger.info(f"BLE connection established to {ble_address}")
                            reset_executor_degraded_state(ble_address=ble_address)
                        except FuturesTimeoutError as err:
                            # Use logger.exception so timeouts include stack context (TRY400),
                            # but raise a short error and keep operator guidance in logs (TRY003).
                            logger.exception(
                                f"BLE connect() call timed out after {BLE_CONNECT_TIMEOUT_SECS} seconds for %s.",
                                ble_address,
                            )
                            logger.warning(
                                "This may indicate a BlueZ or adapter issue."
                            )
                            logger.warning(
                                f"BlueZ may be in a bad state. {BLE_TROUBLESHOOTING_GUIDANCE.format(ble_address=ble_address)}"
                            )
                            # Best-effort cancellation: a hung BLE connect blocks the worker
                            # thread, so we cancel to allow retries only if it completes.
                            if connect_future.cancel():
                                _clear_ble_future(connect_future)
                            else:
                                timed_out_iface = iface
                                # Clear global/local references before attaching late
                                # disposer so late completions cannot observe stale active
                                # globals and skip cleanup.
                                iface = None
                                meshtastic_iface = None
                                _schedule_ble_future_cleanup(
                                    connect_future,
                                    ble_address,
                                    reason="connect timeout",
                                )
                                _attach_late_ble_interface_disposer(
                                    connect_future,
                                    ble_address,
                                    reason="connect timeout",
                                    fallback_iface=timed_out_iface,
                                )
                                timeout_count = _record_ble_timeout(ble_address)
                                _maybe_reset_ble_executor(ble_address, timeout_count)
                            # Don't use iface if connect() timed out - it may be in an inconsistent state
                            iface = None
                            meshtastic_iface = None
                            raise TimeoutError(
                                f"BLE connect() timed out for {ble_address}."
                            ) from err
                    elif iface is not None and hasattr(iface, "connect"):
                        logger.debug(
                            "Skipping explicit BLE connect in compatibility mode; "
                            "interface is expected to connect during initialization for %s",
                            ble_address,
                        )

                    client = iface
                else:
                    logger.error("No BLE address provided.")
                    return None

            elif connection_type == CONNECTION_TYPE_TCP:
                # TCP connection
                target_host = config["meshtastic"].get(CONFIG_KEY_HOST)
                if not target_host:
                    logger.error(
                        "No host specified in Meshtastic configuration for TCP connection."
                    )
                    return None

                target_port = DEFAULT_TCP_PORT
                configured_port = config["meshtastic"].get(CONFIG_KEY_PORT)
                if configured_port is not None:
                    try:
                        parsed_port = int(configured_port)
                        if parsed_port <= 0 or parsed_port > 65535:
                            raise ValueError
                        target_port = parsed_port
                    except (TypeError, ValueError):
                        logger.warning(
                            "Invalid meshtastic.port value %r; using default TCP port %s",
                            configured_port,
                            DEFAULT_TCP_PORT,
                        )

                logger.info(f"Connecting to host {target_host}:{target_port}")

                # Connect without progress indicator
                client = meshtastic.tcp_interface.TCPInterface(
                    hostname=target_host,
                    portNumber=target_port,
                    timeout=configured_timeout_arg,
                )
            else:
                logger.error(f"Unknown connection type: {connection_type}")
                return None

            successful = True

            # Acquire lock only for the final setup and subscription
            with meshtastic_lock:
                meshtastic_client = client
                # Use connection start time (not module import time) for stale rxTime filtering.
                RELAY_START_TIME = time.time()
                with _relay_rx_time_clock_skew_lock:
                    _relay_rx_time_clock_skew_secs = None

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
                    expected_ble_address = config["meshtastic"].get(
                        CONFIG_KEY_BLE_ADDRESS
                    )
                    if expected_ble_address and not _validate_ble_connection_address(
                        meshtastic_client, expected_ble_address
                    ):
                        # Validation failed - wrong device connected
                        # Disconnect immediately to prevent communication with wrong device
                        logger.error(
                            "BLE connection validation failed - connected to wrong device. "
                            "Disconnecting and raising error to force retry."
                        )
                        try:
                            if meshtastic_client is meshtastic_iface:
                                # BLE interface - use proper disconnect sequence
                                _disconnect_ble_interface(
                                    meshtastic_iface, reason="address validation failed"
                                )
                            else:
                                meshtastic_client.close()
                        except Exception as e:
                            logger.warning(f"Error closing invalid BLE connection: {e}")
                        raise ConnectionRefusedError(
                            f"Connected to wrong BLE device. Expected: {expected_ble_address}"
                        )

                nodeInfo = meshtastic_client.getMyNodeInfo()

                # Safely access node info fields
                user_info = nodeInfo.get("user", {}) if nodeInfo else {}
                short_name = user_info.get("shortName", "unknown")
                hw_model = user_info.get("hwModel", "unknown")

                # Get firmware version from device metadata
                metadata = _get_device_metadata(meshtastic_client)
                firmware_version = metadata["firmware_version"]

                if metadata.get("success"):
                    logger.info(
                        f"Connected to {short_name} / {hw_model} / Meshtastic Firmware version {firmware_version}"
                    )
                else:
                    logger.info(f"Connected to {short_name} / {hw_model}")
                    logger.debug(
                        "Device firmware version unavailable from getMetadata()"
                    )

                # Subscribe to message and connection lost events (only once per application run)
                global subscribed_to_messages, subscribed_to_connection_lost
                if not subscribed_to_messages:
                    pub.subscribe(on_meshtastic_message, "meshtastic.receive")
                    subscribed_to_messages = True
                    logger.debug("Subscribed to meshtastic.receive")

                if not subscribed_to_connection_lost:
                    pub.subscribe(
                        on_lost_meshtastic_connection, "meshtastic.connection.lost"
                    )
                    subscribed_to_connection_lost = True
                    logger.debug("Subscribed to meshtastic.connection.lost")

        except (ConnectionRefusedError, MemoryError, BleExecutorDegradedError):
            # Handle critical errors that should not be retried
            logger.exception("Critical connection error")
            return None
        except (FuturesTimeoutError, TimeoutError) as e:
            if shutting_down:
                break
            attempts += 1
            if retry_limit == INFINITE_RETRIES:
                timeout_attempts += 1
                if timeout_attempts > MAX_TIMEOUT_RETRIES_INFINITE:
                    logger.exception(
                        "Connection timed out after %s attempts (unlimited retries); aborting",
                        attempts,
                    )
                    return None
            elif attempts > retry_limit:
                logger.exception("Connection failed after %s attempts", attempts)
                return None

            wait_time = _get_connection_retry_wait_time(attempts)
            logger.warning(
                "Connection attempt %s timed out (%s). Retrying in %s seconds...",
                attempts,
                e,
                wait_time,
            )
            time.sleep(wait_time)
        except Exception as e:
            if shutting_down:
                logger.debug("Shutdown in progress. Aborting connection attempts.")
                break
            if (
                connection_type == CONNECTION_TYPE_BLE
                and ble_address
                and _is_ble_duplicate_connect_suppressed_error(e)
            ):
                logger.warning(
                    "Detected duplicate BLE connect suppression for %s",
                    ble_address,
                )
                if not _reset_ble_connection_gate_state(
                    ble_address,
                    reason="duplicate connect suppression",
                ):
                    logger.debug(
                        "BLE gate reset hook unavailable for %s; retrying without local reset",
                        ble_address,
                    )
            attempts += 1
            if (
                connection_type == CONNECTION_TYPE_BLE
                and ble_address
                # Keep discovery-triggered scan recovery scoped to compatibility
                # mode; forked auto_reconnect-capable implementations handle
                # discovery/connect retries internally.
                and not supports_auto_reconnect
                and _is_ble_discovery_error(e)
            ):
                ble_scan_after_failure = True
                ble_scan_reason = type(e).__name__
            if retry_limit == 0 or attempts <= retry_limit:
                wait_time = _get_connection_retry_wait_time(attempts)
                logger.warning(
                    "An unexpected error occurred on attempt %s: %s. Retrying in %s seconds...",
                    attempts,
                    e,
                    wait_time,
                )
                time.sleep(wait_time)
            else:
                logger.exception("Connection failed after %s attempts", attempts)
                return None

    return meshtastic_client


def on_lost_meshtastic_connection(
    interface: Any = None,
    detection_source: str = "unknown",
    topic: Any = pub.AUTO_TOPIC,
) -> None:
    """
    Mark the Meshtastic connection as lost, close the current client, and start an asynchronous reconnect.

    If a shutdown is underway or a reconnect is already in progress this function returns immediately. When proceeding it sets the module-level `reconnecting` flag, attempts a best-effort close/cleanup of the current Meshtastic client/interface (with special handling for BLE interfaces), clears any in-flight BLE future state, and schedules the `reconnect()` coroutine on the global event loop.

    Parameters:
        detection_source (str): Identifier for where or how the loss was detected; if `"unknown"`, the function will prefer an interface-provided `_last_disconnect_source`, then derive a name from `topic`, and finally fall back to `"meshtastic.connection.lost"`.
        topic (Any): Optional pubsub topic object (from pypubsub); when provided and `detection_source` is `"unknown"`, the topic's name will be used to derive the detection source.
    """
    # Keep these as one-global-per-line to minimize merge churn as this list evolves.
    global meshtastic_client
    global meshtastic_iface
    global reconnecting
    global shutting_down
    global event_loop
    global reconnect_task
    global _ble_future
    global _ble_future_address
    global _ble_future_started_at
    global _ble_future_timeout_secs
    global _ble_executor

    with meshtastic_lock:
        if shutting_down:
            logger.debug("Shutdown in progress. Not attempting to reconnect.")
            return
        if reconnecting:
            logger.debug(
                "Reconnection already in progress. Skipping additional reconnection attempt."
            )
            return
        if detection_source == "unknown":
            interface_source = getattr(interface, "_last_disconnect_source", None)
            if isinstance(interface_source, str) and (
                stripped := interface_source.strip()
            ):
                # Strip 'ble.' prefix to make detection source library-agnostic
                res = stripped[4:].strip() if stripped.startswith("ble.") else stripped
                if res:
                    detection_source = res
                    logger.debug(
                        "Using interface-provided detection source: %s",
                        detection_source,
                    )

            if detection_source == "unknown":
                if topic is not None and topic is not pub.AUTO_TOPIC:
                    # Real topic object from pypubsub - extract its name
                    detection_source = getattr(topic, "getName", lambda: str(topic))()
                    logger.debug(
                        "Using pubsub topic-derived detection source: %s",
                        detection_source,
                    )
                else:
                    # Called directly without a topic, or with AUTO_TOPIC sentinel
                    logger.debug(
                        "_last_disconnect_source unavailable; using default detection source"
                    )
                    detection_source = "meshtastic.connection.lost"

        reconnecting = True
        logger.error(f"Lost connection ({detection_source}). Reconnecting...")

        if meshtastic_client:
            if meshtastic_client is meshtastic_iface:
                # This is a BLE interface - use proper disconnect sequence
                logger.debug("Disconnecting BLE interface due to connection loss")
                _disconnect_ble_interface(
                    meshtastic_iface, reason=f"connection loss: {detection_source}"
                )
                meshtastic_iface = None
            else:
                # Serial or TCP interface - use standard close()
                try:
                    meshtastic_client.close()
                except OSError as e:
                    if e.errno == ERRNO_BAD_FILE_DESCRIPTOR:
                        # Bad file descriptor, already closed
                        pass
                    else:
                        logger.warning(f"Error closing Meshtastic client: {e}")
                except Exception as e:
                    logger.warning(f"Error closing Meshtastic client: {e}")
        meshtastic_client = None
        ble_future_to_cancel = None
        stale_executor = None
        stale_ble_address: str | None = None
        with _ble_executor_lock:
            stale_ble_address = _ble_future_address
            if _ble_future and not _ble_future.done():
                logger.debug(
                    "Clearing stale BLE future before reconnect (%s)",
                    detection_source,
                )
                ble_future_to_cancel = _ble_future
                _ble_future = None
                if _ble_future_address:
                    with _ble_timeout_lock:
                        _ble_timeout_counts.pop(_ble_future_address, None)
                _ble_future_address = None
                _ble_future_started_at = None
                _ble_future_timeout_secs = None
                if _ble_executor is not None:
                    stale_executor = _ble_executor
                    _ble_executor = ThreadPoolExecutor(max_workers=1)

        if ble_future_to_cancel is not None:
            if stale_ble_address:
                _attach_late_ble_interface_disposer(
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

        if stale_ble_address is not None:
            reset_executor_degraded_state(ble_address=stale_ble_address)
        else:
            should_reset_all_degraded = False
            with _ble_executor_lock:
                if _ble_executor_degraded_addresses:
                    should_reset_all_degraded = True
                    logger.debug(
                        "Resetting degraded BLE executor state during reconnect "
                        "(no stale_ble_address but degraded addresses exist)"
                    )
            if should_reset_all_degraded:
                reset_executor_degraded_state(reset_all=True)

        if event_loop and not event_loop.is_closed():
            reconnect_task = event_loop.create_task(reconnect())


async def reconnect() -> None:
    """
    Re-establish the Meshtastic connection using exponential backoff.

    Retries connect_meshtastic(force_connect=True) until a connection is obtained, the application begins shutting down, or the task is cancelled. Starts with DEFAULT_BACKOFF_TIME and doubles the wait after each failed attempt, capped at 300 seconds. Stops promptly on cancellation or when shutting_down is set, and ensures the module-level `reconnecting` flag is cleared before returning.
    """
    global meshtastic_client, reconnecting, shutting_down
    backoff_time = DEFAULT_BACKOFF_TIME
    try:
        while not shutting_down:
            try:
                logger.info(
                    f"Reconnection attempt starting in {backoff_time} seconds..."
                )

                # Show reconnection countdown with Rich (if not in a service)
                if not is_running_as_service():
                    try:
                        from rich.progress import (
                            BarColumn,
                            Progress,
                            TextColumn,
                            TimeRemainingColumn,
                        )
                    except ImportError:
                        logger.debug(
                            "Rich not available; falling back to simple reconnection delay"
                        )
                        await asyncio.sleep(backoff_time)
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
                                if shutting_down:
                                    break
                                await asyncio.sleep(1)
                                progress.update(task, advance=1)
                else:
                    await asyncio.sleep(backoff_time)
                if shutting_down:
                    logger.debug(
                        "Shutdown in progress. Aborting reconnection attempts."
                    )
                    break
                loop = asyncio.get_running_loop()
                # Pass the current config during reconnection to ensure matrix_rooms is populated
                # Using None for passed_config would skip matrix_rooms initialization
                meshtastic_client = await loop.run_in_executor(
                    None, connect_meshtastic, config, True
                )
                if meshtastic_client:
                    logger.info("Reconnected successfully.")
                    break
            except Exception:
                if shutting_down:
                    break
                logger.exception("Reconnection attempt failed")
                backoff_time = min(backoff_time * 2, 300)  # Cap backoff at 5 minutes
    except asyncio.CancelledError:
        logger.info("Reconnection task was cancelled.")
    finally:
        reconnecting = False


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
    global config, matrix_rooms, _relay_rx_time_clock_skew_secs

    # Validate packet structure
    if not packet or not isinstance(packet, dict):
        logger.error("Received malformed packet: packet is None or not a dict")
        return

    # Parse rxTime early so health-probe responses can calibrate packet clock skew.
    rx_time_raw = packet.get("rxTime", 0)
    try:
        rx_time = float(rx_time_raw)
    except (TypeError, ValueError):
        rx_time = 0

    is_health_probe_response = _is_health_probe_response_packet(packet, interface)
    if is_health_probe_response:
        if rx_time > 0:
            observed_skew = time.time() - rx_time
            calibrated_now = False
            with _relay_rx_time_clock_skew_lock:
                if _relay_rx_time_clock_skew_secs is None:
                    _relay_rx_time_clock_skew_secs = observed_skew
                    calibrated_now = True
            if calibrated_now:
                logger.debug(
                    "[HEALTH_CHECK] Calibrated rxTime clock skew to %.3f seconds",
                    observed_skew,
                )

        decoded = packet.get("decoded")
        portnum = decoded.get("portnum") if isinstance(decoded, dict) else None
        logger.debug(
            "[HEALTH_CHECK] Metadata probe response requestId=%s from=%s port=%s",
            _extract_packet_request_id(packet),
            packet.get("fromId") or packet.get("from"),
            _get_portnum_name(portnum),
        )
        return

    # Filter out old messages (from before relay start) to prevent flooding.
    # This handles cases where the node dumps stored history upon connection.
    # When health probes calibrate packet clock skew, adjust the relay start
    # cutoff so clock offsets do not hide fresh traffic.
    with _relay_rx_time_clock_skew_lock:
        calibrated_skew = _relay_rx_time_clock_skew_secs
    effective_relay_start_time = RELAY_START_TIME
    if calibrated_skew is not None:
        effective_relay_start_time = RELAY_START_TIME - calibrated_skew

    if rx_time > 0 and rx_time < effective_relay_start_time:
        if calibrated_skew is None:
            logger.debug(
                "Ignoring old packet with rxTime %s (older than start time %s)",
                rx_time,
                RELAY_START_TIME,
            )
        else:
            logger.debug(
                "Ignoring old packet with rxTime %s (older than adjusted start time %s; raw start=%s skew=%s)",
                rx_time,
                effective_relay_start_time,
                RELAY_START_TIME,
                calibrated_skew,
            )
        return

    # Full packet logging for debugging (when enabled in config)
    # Check if full packet logging is enabled - accepts boolean True or string "true"
    debug_settings = config.get("logging", {}).get("debug", {}) if config else {}
    full_packets_setting = debug_settings.get("full_packets")
    if full_packets_setting is True or (
        isinstance(full_packets_setting, str) and full_packets_setting.lower() == "true"
    ):
        logger.debug("Full packet: %s", packet)

    # Log that we received a message (without the full packet details)
    decoded = packet.get("decoded")
    if decoded and isinstance(decoded, dict) and decoded.get("text"):
        logger.info(f"Received Meshtastic message: {decoded.get('text')}")
    else:
        portnum = (
            decoded.get("portnum") if decoded and isinstance(decoded, dict) else None
        )
        portnum_name = _get_portnum_name(portnum)
        from_id = packet.get("fromId") or packet.get("from")
        from_display = ""
        if from_id is not None:
            from_display = _get_node_display_name(from_id, interface, fallback="")
        details_map = {
            "from": from_id,
            "channel": packet.get("channel"),
            "id": packet.get("id"),
        }
        details_map.update(_get_packet_details(decoded, packet, portnum_name))

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
        logger.debug(prefix)

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot process Meshtastic message.")
        return

    # Import the configuration helpers
    from mmrelay.matrix_utils import get_interaction_settings

    # Get interaction settings
    interactions = get_interaction_settings(config)

    # Filter packets based on interaction settings
    if packet.get("decoded", {}).get("portnum") == TEXT_MESSAGE_APP:
        decoded = packet.get("decoded", {})
        # Filter out reactions if reactions are disabled
        if (
            not interactions["reactions"]
            and decoded.get("replyId") is not None
            and "emoji" in decoded
            and decoded.get("emoji") == EMOJI_FLAG_VALUE
        ):
            logger.debug(
                "Filtered out reaction packet due to reactions being disabled."
            )
            return

    from mmrelay.matrix_utils import matrix_relay

    global event_loop

    if shutting_down:
        logger.debug("Shutdown in progress. Ignoring incoming messages.")
        return

    if event_loop is None:
        logger.error("Event loop is not set. Cannot process message.")
        return

    loop = event_loop

    sender = packet.get("fromId") or packet.get("from")
    toId = packet.get("to")

    decoded = packet.get("decoded", {})
    text = decoded.get("text")
    replyId = decoded.get("replyId")
    emoji_flag = "emoji" in decoded and decoded["emoji"] == EMOJI_FLAG_VALUE

    # Determine if this is a direct message to the relay node
    from meshtastic.mesh_interface import BROADCAST_NUM

    if not getattr(interface, "myInfo", None):
        logger.warning("Meshtastic interface missing myInfo; cannot determine node id")
        return
    myId = interface.myInfo.my_node_num

    if toId == myId:
        is_direct_message = True
    elif toId == BROADCAST_NUM or toId is None:
        is_direct_message = False
    else:
        logger.debug(
            "Ignoring message intended for node %s (not broadcast or relay).", toId
        )
        return

    meshnet_name = config[CONFIG_SECTION_MESHTASTIC][CONFIG_KEY_MESHNET_NAME]

    # Reaction handling (Meshtastic -> Matrix)
    # If replyId and emoji_flag are present and reactions are enabled, we relay as text reactions in Matrix
    if replyId and emoji_flag and interactions["reactions"]:
        longname = _get_name_safely(get_longname, sender)
        shortname = _get_name_safely(get_shortname, sender)
        orig = get_message_map_by_meshtastic_id(replyId)
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
            prefix = get_matrix_prefix(config, longname, shortname, meshnet_name)

            reaction_symbol = text.strip() if (text and text.strip()) else "⚠️"
            reaction_message = (
                f'\n {prefix}reacted {reaction_symbol} to "{abbreviated_text}"'
            )

            # Relay the reaction as emote to Matrix, preserving the original meshnet name
            _fire_and_forget(
                matrix_relay(
                    matrix_room_id,
                    reaction_message,
                    longname,
                    shortname,
                    meshnet_name,
                    decoded.get("portnum"),
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
            logger.warning(
                "Original message for reaction (replyId=%s) not found in DB. "
                "Relaying as normal message instead.",
                replyId,
            )

    # Reply handling (Meshtastic -> Matrix)
    # If replyId is present but emoji is not (or not 1), this is a reply
    if replyId and not emoji_flag and interactions["replies"]:
        longname = _get_name_safely(get_longname, sender)
        shortname = _get_name_safely(get_shortname, sender)
        orig = get_message_map_by_meshtastic_id(replyId)
        if orig:
            # orig = (matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
            matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet = orig

            # Import the matrix prefix function
            from mmrelay.matrix_utils import get_matrix_prefix

            # Get the formatted prefix for the reply
            prefix = get_matrix_prefix(config, longname, shortname, meshnet_name)
            formatted_message = f"{prefix}{text}"

            logger.info(f"Relaying Meshtastic reply from {longname} to Matrix")

            # Relay the reply to Matrix with proper reply formatting
            _fire_and_forget(
                matrix_relay(
                    matrix_room_id,
                    formatted_message,
                    longname,
                    shortname,
                    meshnet_name,
                    decoded.get("portnum"),
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
            logger.warning(
                "Original message for reply (replyId=%s) not found in DB. "
                "Relaying as normal message instead.",
                replyId,
            )

    # Normal text messages or detection sensor messages
    if text:
        # Determine the channel for this message
        channel = packet.get("channel")
        if channel is None:
            # If channel not specified, deduce from portnum
            # Note: meshtastic-python emits enum names (e.g., "TEXT_MESSAGE_APP") in decoded dicts,
            # while other paths (protobuf/raw) surface numeric portnums. Support both to avoid drops.
            if decoded.get("portnum") in (
                PORTNUM_TEXT_MESSAGE_APP,
                PORTNUM_DETECTION_SENSOR_APP,
                TEXT_MESSAGE_APP,
                DETECTION_SENSOR_APP,
            ):
                channel = DEFAULT_CHANNEL_VALUE
            else:
                logger.debug(
                    f"Unknown portnum {decoded.get('portnum')}, cannot determine channel"
                )
                return

        # Normalize channel to integer to prevent type mismatch issues
        try:
            channel = int(channel)
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid channel value {channel!r} (type: {type(channel).__name__}), "
                f"defaulting to {DEFAULT_CHANNEL_VALUE}"
            )
            channel = DEFAULT_CHANNEL_VALUE

        # Check if channel is mapped to a Matrix room
        channel_mapped = False
        iterable_rooms = (
            matrix_rooms.values() if isinstance(matrix_rooms, dict) else matrix_rooms
        )
        for room in iterable_rooms:
            if not isinstance(room, dict):
                continue

            room_channel = _normalize_room_channel(room)
            if room_channel is None:
                continue

            if room_channel == channel:
                channel_mapped = True
                logger.debug(
                    f"Channel {channel} mapped to Matrix room {room.get('id', 'unknown')}"
                )
                break

        if not channel_mapped:
            # Use WARNING level so this is visible without debug logging enabled
            # This helps users diagnose configuration issues
            available_channels = []
            for room in iterable_rooms:
                if isinstance(room, dict):
                    ch = _normalize_room_channel(room)
                    if ch is not None:
                        available_channels.append(ch)

            logger.warning(
                f"Skipping message from unmapped channel {channel}. "
                f"Available channels in config: {available_channels}. "
                f"Check your matrix_rooms configuration to ensure this channel is mapped."
            )
            return

        # If detection_sensor is disabled and this is a detection sensor packet, skip it
        portnum = decoded.get("portnum")
        if (
            portnum == PORTNUM_DETECTION_SENSOR_APP or portnum == DETECTION_SENSOR_APP
        ) and not get_meshtastic_config_value(
            config, "detection_sensor", DEFAULT_DETECTION_SENSOR
        ):
            logger.debug(
                "Detection sensor packet received, but detection sensor processing is disabled."
            )
            return

        # Attempt to get longname/shortname from database or nodes
        longname = _get_name_or_none(get_longname, sender)
        if longname is None:
            logger.debug(
                "Failed to get longname from database for %s, will try interface fallback",
                sender,
            )

        shortname = _get_name_or_none(get_shortname, sender)
        if shortname is None:
            logger.debug(
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
                            save_longname(sender, longname_val)
                            longname = longname_val
                    if not shortname:
                        shortname_val = user.get("shortName")
                        if shortname_val and sender is not None:
                            save_shortname(sender, shortname_val)
                            shortname = shortname_val
            else:
                logger.debug(f"Node info for sender {sender} not available yet.")

        # If still not available, fallback to sender ID
        if not longname:
            longname = str(sender)
        if not shortname:
            shortname = str(sender)

        # Import the matrix prefix function
        from mmrelay.matrix_utils import get_matrix_prefix

        # Get the formatted prefix
        prefix = get_matrix_prefix(config, longname, shortname, meshnet_name)
        formatted_message = f"{prefix}{text}"

        # Plugin functionality - Check if any plugin handles this message before relaying
        found_matching_plugin = _run_meshtastic_plugins(
            packet=packet,
            formatted_message=formatted_message,
            longname=longname,
            meshnet_name=meshnet_name,
            loop=loop,
            cfg=config,
        )

        # If message is a DM or handled by plugin, do not relay further
        if is_direct_message:
            logger.debug(
                f"Received a direct message from {longname}: {text}. Not relaying to Matrix."
            )
            return
        if found_matching_plugin:
            logger.debug("Message was handled by a plugin. Not relaying to Matrix.")
            return

        # Check if matrix_rooms is empty BEFORE attempting to relay
        # This can happen during startup race conditions where messages arrive
        # before matrix_rooms is populated, or during reconnection
        if not matrix_rooms:
            logger.warning(
                f"matrix_rooms is empty - cannot relay message from {longname}. "
                f"This may indicate a startup race condition or configuration issue. "
                f"Message will be dropped: {text[:50]}{'...' if len(text) > 50 else ''}"
            )
            return

        # Relay the message to all Matrix rooms mapped to this channel
        logger.info(f"Relaying Meshtastic message from {longname} to Matrix")

        iterable_rooms = (
            matrix_rooms.values() if isinstance(matrix_rooms, dict) else matrix_rooms
        )
        for room in iterable_rooms:
            if not isinstance(room, dict):
                continue

            room_channel = _normalize_room_channel(room)
            if room_channel is None:
                continue

            if room_channel == channel:
                # Storing the message_map (if enabled) occurs inside matrix_relay() now,
                # controlled by relay_reactions.
                try:
                    _fire_and_forget(
                        matrix_relay(
                            room["id"],
                            formatted_message,
                            longname,
                            shortname,
                            meshnet_name,
                            decoded.get("portnum"),
                            meshtastic_id=packet.get("id"),
                            meshtastic_text=text,
                        ),
                        loop=loop,
                    )
                except Exception:
                    logger.exception("Error relaying message to Matrix")
    else:
        # Non-text messages via plugins
        portnum = decoded.get("portnum")
        _run_meshtastic_plugins(
            packet=packet,
            formatted_message=None,
            longname=None,
            meshnet_name=None,
            loop=loop,
            cfg=config,
            use_keyword_args=True,
            log_with_portnum=True,
            portnum=portnum,
        )


def requires_continuous_health_monitor(config: dict[str, Any]) -> bool:
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
    meshtastic_config = config.get(CONFIG_SECTION_MESHTASTIC)
    if not isinstance(meshtastic_config, dict):
        return DEFAULT_HEALTH_CHECK_ENABLED
    if meshtastic_config.get(CONFIG_KEY_CONNECTION_TYPE) == CONNECTION_TYPE_BLE:
        return False
    health_config = meshtastic_config.get("health_check")
    if health_config is None:
        return DEFAULT_HEALTH_CHECK_ENABLED
    if not isinstance(health_config, dict):
        return True
    raw_enabled = health_config.get("enabled", DEFAULT_HEALTH_CHECK_ENABLED)
    return _coerce_bool(
        raw_enabled, DEFAULT_HEALTH_CHECK_ENABLED, "health_check.enabled"
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


def send_text_reply(
    interface: Any,
    text: str,
    reply_id: int,
    destinationId: Any = meshtastic.BROADCAST_ADDR,
    wantAck: bool = False,
    channelIndex: int = 0,
) -> Any:
    """
    Send a Meshtastic text message that references (replies to) a previous Meshtastic message.

    Parameters:
        interface (Any): Meshtastic interface used to send the packet.
        text (str): UTF-8 text to send.
        reply_id (int): ID of the Meshtastic message being replied to.
        destinationId (Any, optional): Recipient address or node ID; defaults to broadcast.
        wantAck (bool, optional): If True, request an acknowledgement for the packet.
        channelIndex (int, optional): Channel index to send the packet on.

    Returns:
        The result returned by the interface's _sendPacket call (typically the sent MeshPacket), or
        `None` if the interface is unavailable or sending fails.
    """
    logger.debug(f"Sending text reply: '{text}' replying to message ID {reply_id}")

    # Check if interface is available
    if interface is None:
        logger.error("No Meshtastic interface available for sending reply")
        return None

    # Create the Data protobuf message with reply_id set
    data_msg = mesh_pb2.Data()
    data_msg.portnum = portnums_pb2.PortNum.TEXT_MESSAGE_APP
    data_msg.payload = text.encode(MESHTASTIC_TEXT_ENCODING)
    data_msg.reply_id = reply_id

    # Create the MeshPacket
    mesh_packet = mesh_pb2.MeshPacket()
    mesh_packet.channel = channelIndex
    mesh_packet.decoded.CopyFrom(data_msg)
    mesh_packet.id = interface._generatePacketId()

    # Send the packet using the existing infrastructure
    try:
        return interface._sendPacket(
            mesh_packet, destinationId=destinationId, wantAck=wantAck
        )
    except (
        AttributeError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        logger.exception("Failed to send text reply")
        return None
    except SystemExit:
        logger.debug("SystemExit encountered, preserving for graceful shutdown")
        raise


# Backward-compatible alias for older call sites.
sendTextReply = send_text_reply


if __name__ == "__main__":
    # If running this standalone (normally the main.py does the loop), just try connecting and run forever.
    meshtastic_client = connect_meshtastic()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    event_loop = loop  # Set the event loop for use in callbacks
    _check_connection_task = loop.create_task(check_connection())
    loop.run_forever()
