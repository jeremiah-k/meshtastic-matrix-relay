# isort: skip_file
# ruff: noqa: E402, F401, I001
# fmt: off
# Facade module with load-bearing import ordering:
# globals and constants must be defined before submodule imports.
import asyncio
import atexit
import concurrent.futures
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
    BLE_INTERFACE_CREATE_GRACE_SECS,
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
from mmrelay.runtime_utils import is_running_as_service

# ---------------------------------------------------------------------------
# Facade-owned globals — defined BEFORE submodule imports so that submodules
# can reference facade.<name> at function-call time even during circular
# import resolution.
# ---------------------------------------------------------------------------

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

MeshtasticBLEError = None
BLEDiscoveryError = None
BLEDeviceNotFoundError = None
BLEConnectionTimeoutError = None
BLEConnectionSuppressedError = None
BLEAddressMismatchError = None
BLEDBusTransportError = None
sanitize_address = None
try:
    _ble_interface_module = importlib.import_module("meshtastic.ble_interface")
except Exception:  # noqa: BLE001 - optional mtjk capabilities vary by install
    _ble_interface_module = None  # type: ignore[assignment]
if _ble_interface_module is not None:
    MeshtasticBLEError = getattr(_ble_interface_module, "MeshtasticBLEError", None)
    BLEDiscoveryError = getattr(_ble_interface_module, "BLEDiscoveryError", None)
    BLEDeviceNotFoundError = getattr(
        _ble_interface_module,
        "BLEDeviceNotFoundError",
        None,
    )
    BLEConnectionTimeoutError = getattr(
        _ble_interface_module,
        "BLEConnectionTimeoutError",
        None,
    )
    BLEConnectionSuppressedError = getattr(
        _ble_interface_module,
        "BLEConnectionSuppressedError",
        None,
    )
    BLEAddressMismatchError = getattr(
        _ble_interface_module,
        "BLEAddressMismatchError",
        None,
    )
    BLEDBusTransportError = getattr(_ble_interface_module, "BLEDBusTransportError", None)
    sanitize_address = getattr(_ble_interface_module, "sanitize_address", None)


class BleExecutorDegradedError(Exception):
    """Raised when a BLE address has too many orphaned workers and needs manual recovery."""

    pass


# Global config variable that will be set from config.py
config: dict[str, Any] | None = None

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
# Timer used to decouple startup-drain expiry from packet arrival.
_relay_startup_drain_expiry_timer: threading.Timer | None = None
# Tracks the pending delayed connect-time metadata probe timer for cancellation on disconnect/rollback.
_pending_connect_time_probe_timer: threading.Timer | None = None
# Signals whether startup drain has completed for readiness publication.
_relay_startup_drain_complete_event = threading.Event()
_relay_startup_drain_complete_event.set()
# Only apply startup drain on the first successful process-lifetime connect.
_startup_packet_drain_applied = False
# On reconnects, allow exactly one bounded pre-start skew bootstrap packet
# without enabling a full reconnect drain window.
_relay_reconnect_prestart_bootstrap_deadline_monotonic_secs: float | None = None


def get_startup_drain_complete_event() -> threading.Event | None:
    """Return the startup-drain completion event used by readiness coordination."""
    # Defensive guard: tests intentionally monkeypatch facade globals and may
    # temporarily replace this attribute with non-Event sentinels.
    event = _relay_startup_drain_complete_event
    return event if isinstance(event, threading.Event) else None


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
    BLE_INTERFACE_CREATE_TIMEOUT_FLOOR_SECS
    + BLE_INTERFACE_CREATE_GRACE_SECS
    + BLE_CONNECT_TIMEOUT_SECS
)

reconnecting = False
shutting_down = False

reconnect_task: "asyncio.Task[Any] | Future[Any] | None" = (
    None  # asyncio.Task when scheduled from async, concurrent.futures.Future when scheduled via run_coroutine_threadsafe
)
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
# BLE lifecycle ownership state (per sanitized BLE address).
_ble_lifecycle_lock = threading.Lock()
_ble_generation_by_address: dict[str, int] = {}
_ble_iface_generation_by_id: dict[int, tuple[str, int]] = {}
_ble_teardown_unresolved_by_generation: dict[tuple[str, int], int] = {}
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


# Executor infrastructure — implemented in meshtastic.executors, re-exported here
# for backward-compatible patch targets (tests patch mmrelay.meshtastic_utils.*).

# ---------------------------------------------------------------------------
# Submodule imports — after all facade-owned globals so circular-import
# resolution never sees a partially-initialized module.
# ---------------------------------------------------------------------------

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
    _advance_ble_generation,
    _attach_late_ble_interface_disposer,
    _discard_ble_iface_generation,
    _disconnect_ble_by_address,
    _disconnect_ble_interface,
    _extract_ble_address_from_interface,
    _get_ble_generation,
    _get_ble_iface_generation,
    _get_ble_unresolved_teardown_generations,
    _is_ble_discovery_error,
    _is_ble_duplicate_connect_suppressed_error,
    _is_ble_generation_stale,
    _record_ble_teardown_timeout,
    _register_ble_iface_generation,
    _resolve_ble_teardown_timeout,
    _reset_ble_connection_gate_state,
    _sanitize_ble_address,
    _scan_for_ble_address,
    _validate_ble_connection_address,
)
from mmrelay.meshtastic.connection import (
    _connect_meshtastic_impl,
    _get_connect_time_probe_settings,
    _get_connection_retry_wait_time,
    _log_ble_shutdown_state,
    _rollback_connect_attempt_state,
    _schedule_connect_time_calibration_probe,
    connect_meshtastic,
    serial_port_exists,
)
from mmrelay.meshtastic.events import (
    _schedule_startup_drain_deadline_cleanup,
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
    check_connection,
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
from mmrelay.meshtastic.metadata import (
    _extract_firmware_version_from_client,
    _extract_firmware_version_from_metadata,
    _get_device_metadata,
    _get_name_or_none,
    _get_name_safely,
    _missing_metadata_probe_error,
    _normalize_firmware_version,
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

atexit.register(shutdown_shared_executors)


if __name__ == "__main__":
    # If running this standalone (normally the main.py does the loop), just try connecting and run forever.
    meshtastic_client = connect_meshtastic()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    event_loop = loop  # Set the event loop for use in callbacks
    _check_connection_task = loop.create_task(check_connection())
    loop.run_forever()
