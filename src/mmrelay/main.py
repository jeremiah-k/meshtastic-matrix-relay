"""
This script connects a Meshtastic mesh network to Matrix chat rooms by relaying messages between them.
It uses Meshtastic-python and Matrix nio client library to interface with the radio and the Matrix server respectively.
"""

import asyncio
import functools
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any, Callable, cast

from aiohttp import ClientError
from nio import (
    MegolmEvent,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)
from nio.events.room_events import RoomMemberEvent

# Import version from package
# Import meshtastic_utils as a module to set event_loop
from mmrelay import __version__, meshtastic_utils
from mmrelay.cli_utils import msg_suggest_check_config, msg_suggest_generate_config
from mmrelay.constants.app import (
    APP_DISPLAY_NAME,
    DEFAULT_READY_HEARTBEAT_SECONDS,
    MESSAGE_QUEUE_SHUTDOWN_TIMEOUT_SECONDS,
    PLUGIN_SHUTDOWN_TIMEOUT_SECONDS,
    SECURE_DIR_PERMISSIONS,
    SECURE_FILE_PERMISSIONS,
    WINDOWS_PLATFORM,
)
from mmrelay.constants.config import (
    CONFIG_KEY_MESSAGE_DELAY,
    CONFIG_KEY_MSG_MAP,
    CONFIG_KEY_WIPE_ON_RESTART,
    CONFIG_SECTION_DATABASE,
    CONFIG_SECTION_DATABASE_LEGACY,
    CONFIG_SECTION_MESHTASTIC,
    REQUIRED_CONFIG_KEYS_WITH_CREDENTIALS,
    REQUIRED_CONFIG_KEYS_WITHOUT_CREDENTIALS,
)
from mmrelay.constants.network import (
    MATRIX_CLIENT_CLOSE_TIMEOUT_SECONDS,
    MESHTASTIC_CLOSE_TIMEOUT_SECONDS,
    NODEDB_BACKOFF_INITIAL_SECONDS,
    NODEDB_BACKOFF_MAX_SECONDS,
    NODEDB_SHUTDOWN_TIMEOUT_SECONDS,
)
from mmrelay.constants.queue import DEFAULT_MESSAGE_DELAY
from mmrelay.db_utils import (
    initialize_database,
    wipe_message_map,
)
from mmrelay.log_utils import get_logger
from mmrelay.matrix_utils import InviteMemberEvent  # type: ignore[attr-defined]
from mmrelay.matrix_utils import (
    connect_matrix,
    join_matrix_room,
)
from mmrelay.matrix_utils import logger as matrix_logger
from mmrelay.matrix_utils import (
    on_decryption_failure,
    on_invite,
    on_room_member,
    on_room_message,
)
from mmrelay.meshtastic_utils import connect_meshtastic
from mmrelay.meshtastic_utils import logger as meshtastic_logger
from mmrelay.message_queue import (
    get_message_queue,
    start_message_queue,
    stop_message_queue,
)
from mmrelay.paths import get_home_dir, get_legacy_dirs, get_legacy_env_vars
from mmrelay.plugin_loader import load_plugins, shutdown_plugins

# Initialize logger
logger = get_logger(name=APP_DISPLAY_NAME)
_DEFAULT_CHECK_CONNECTION_CALLABLE = meshtastic_utils.check_connection


# Flag to track if banner has been printed
_banner_printed = False
_ready_file_path = os.environ.get("MMRELAY_READY_FILE")
_ready_heartbeat_seconds_raw = os.environ.get(
    "MMRELAY_READY_HEARTBEAT_SECONDS",
    str(DEFAULT_READY_HEARTBEAT_SECONDS),
)
_PLUGIN_SHUTDOWN_TIMEOUT_SECONDS = PLUGIN_SHUTDOWN_TIMEOUT_SECONDS
_MESSAGE_QUEUE_SHUTDOWN_TIMEOUT_SECONDS = MESSAGE_QUEUE_SHUTDOWN_TIMEOUT_SECONDS
try:
    _ready_heartbeat_seconds = int(_ready_heartbeat_seconds_raw)
except (TypeError, ValueError):
    logger.warning(
        "Invalid MMRELAY_READY_HEARTBEAT_SECONDS=%r; defaulting to %d",
        _ready_heartbeat_seconds_raw,
        DEFAULT_READY_HEARTBEAT_SECONDS,
    )
    _ready_heartbeat_seconds = DEFAULT_READY_HEARTBEAT_SECONDS


def _write_ready_file() -> None:
    """
    Create or update the Kubernetes readiness marker file used by external probes.

    If MMRELAY_READY_FILE is unset, this function is a no-op. When configured, it
    ensures the parent directory exists (attempting to set owner-only mode 0o700),
    writes the readiness file atomically from a temporary file, and attempts to
    set owner-only file permissions (0o600) to avoid world-readable files. Filesystem
    errors are caught and suppressed; failures are logged at debug level.
    """
    if not _ready_file_path:
        return
    try:
        ready_dir = os.path.dirname(_ready_file_path)
        if ready_dir:
            # Create parent directory with restrictive permissions (owner only)
            os.makedirs(ready_dir, exist_ok=True, mode=SECURE_DIR_PERMISSIONS)
            # Ensure directory has correct permissions when we own it.
            try:
                if (
                    os.path.isdir(ready_dir)
                    and os.stat(ready_dir).st_uid == os.geteuid()
                ):
                    os.chmod(ready_dir, SECURE_DIR_PERMISSIONS)
            except OSError:
                logger.debug(
                    "Failed to set readiness directory permissions: %s",
                    ready_dir,
                    exc_info=True,
                )

        # Write atomically using a temp file in the same directory
        ready_path = Path(_ready_file_path)
        temp_path = ready_path.with_suffix(".tmp")

        # Create temp file with restrictive permissions (owner read/write only)
        with os.fdopen(
            os.open(
                temp_path,
                os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
                SECURE_FILE_PERMISSIONS,
            ),
            "w",
        ):
            pass

        # Atomically rename temp file to target
        temp_path.rename(ready_path)
        logger.debug("Wrote readiness file: %s", _ready_file_path)
    except OSError:
        logger.debug(
            "Failed to write readiness file: %s", _ready_file_path, exc_info=True
        )


def _touch_ready_file() -> None:
    """
    Update the readiness marker file's modification timestamp, creating the file if it does not exist.

    The file path is taken from MMRELAY_READY_FILE (no default; must be set to enable).
    If no readiness file path is configured, this function does nothing. Filesystem errors
    during the touch/create operation are suppressed.
    """
    if not _ready_file_path:
        return
    try:
        Path(_ready_file_path).touch(mode=SECURE_FILE_PERMISSIONS, exist_ok=True)
        os.chmod(_ready_file_path, SECURE_FILE_PERMISSIONS)
        logger.debug("Touched readiness file: %s", _ready_file_path)
    except OSError:
        logger.debug(
            "Failed to touch readiness file: %s", _ready_file_path, exc_info=True
        )


async def _ready_heartbeat(shutdown_event: asyncio.Event) -> None:
    """
    Keep the Kubernetes readiness marker file's modification time updated until shutdown.

    If a readiness file path is not configured or the heartbeat interval is less than or equal to zero, this coroutine returns immediately; otherwise it periodically updates the file's timestamp at the configured interval while `shutdown_event` is not set.

    Parameters:
        shutdown_event (asyncio.Event): Event that, when set, stops the heartbeat and allows the coroutine to exit.
    """
    if _ready_heartbeat_seconds <= 0 or not _ready_file_path:
        return
    while not shutdown_event.is_set():
        await asyncio.to_thread(_touch_ready_file)
        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=_ready_heartbeat_seconds,
            )
        except asyncio.TimeoutError:
            continue


def _remove_ready_file() -> None:
    """
    Remove the readiness marker file on shutdown.

    The file path is taken from MMRELAY_READY_FILE (no default; must be set to enable).
    If no readiness file path is configured, this function does nothing. Filesystem
    errors during the remove operation are suppressed.
    """
    if not _ready_file_path:
        return
    try:
        if os.path.exists(_ready_file_path):
            os.remove(_ready_file_path)
            logger.debug("Removed readiness file: %s", _ready_file_path)
    except OSError:
        logger.debug(
            "Failed to remove readiness file: %s", _ready_file_path, exc_info=True
        )


def print_banner() -> None:
    """
    Log a single startup banner containing the application version.

    Subsequent calls have no effect.
    """
    global _banner_printed
    # Only print the banner once
    if not _banner_printed:
        logger.info(f"Starting MMRelay version {__version__}")
        _banner_printed = True


def _coerce_config_bool(value: Any) -> bool:
    """
    Normalize config values to a strict boolean.

    Accepts booleans directly plus common boolean-like strings and 0/1 values.
    Unknown values are treated as False.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"", "0", "false", "no", "off"}:
            return False
        logger.debug(
            "Unrecognized boolean config value %r; treating as False",
            value,
        )
        return False
    if isinstance(value, (int, float)):
        if value in (0, 1):
            return bool(value)
        logger.debug(
            "Unexpected numeric config value %r; treating as False",
            value,
        )
        return False
    if value is not None:
        logger.debug(
            "Unexpected config value type %s for %r; treating as False",
            type(value).__name__,
            value,
        )
    return False


def _requires_continuous_health_monitor(config: dict[str, Any]) -> bool:
    """
    Return True when check_connection() is expected to run continuously.

    Delegates to meshtastic_utils.requires_continuous_health_monitor() for the
    actual predicate logic to avoid duplication.
    """
    return meshtastic_utils.requires_continuous_health_monitor(config)


async def main(config: dict[str, Any]) -> None:
    """
    Run the relay: initialize core services, connect to Meshtastic and Matrix, run the Matrix sync loop with health-monitoring and retry behavior, and perform an orderly shutdown.

    Initializes the database and plugins, starts the message queue and Meshtastic connection, connects and joins configured Matrix rooms, registers Matrix event handlers (including invite and member events), monitors connection health, and coordinates a graceful shutdown sequence (optionally wiping the message map on startup and shutdown).

    Parameters:
        config (dict[str, Any]): Application configuration. Relevant keys:
            - "matrix_rooms": list of room dicts containing at least an "id" key.
            - "meshtastic": optional dict; may include "message_delay" to control outbound pacing.
            - "database" (preferred) or legacy "db": optional dict containing "msg_map" with a boolean "wipe_on_restart" that, when true, causes the message map to be wiped at startup and shutdown. Using the legacy "db.msg_map" triggers a deprecation warning.

    Raises:
        ConnectionError: If a Matrix client cannot be established and operation cannot continue.
    """
    # Extract Matrix configuration
    matrix_rooms: list[dict[str, Any]] = config["matrix_rooms"]

    loop = asyncio.get_running_loop()
    meshtastic_utils.event_loop = loop

    # Initialize the SQLite database
    initialize_database()

    # Check database config for wipe_on_restart (preferred format)
    database_config = config.get(CONFIG_SECTION_DATABASE)
    msg_map_config = (
        database_config.get(CONFIG_KEY_MSG_MAP)
        if isinstance(database_config, dict)
        else None
    )
    preferred_msg_map_config = (
        msg_map_config if isinstance(msg_map_config, dict) else {}
    )
    has_preferred_wipe_on_restart = (
        CONFIG_KEY_WIPE_ON_RESTART in preferred_msg_map_config
    )
    preferred_wipe_on_restart = preferred_msg_map_config.get(
        CONFIG_KEY_WIPE_ON_RESTART, False
    )
    wipe_on_restart = (
        _coerce_config_bool(preferred_wipe_on_restart)
        if has_preferred_wipe_on_restart
        else False
    )

    # If not found in database config, check legacy db config
    if not has_preferred_wipe_on_restart:
        db_config = config.get(CONFIG_SECTION_DATABASE_LEGACY)
        legacy_msg_map_config = (
            db_config.get(CONFIG_KEY_MSG_MAP) if isinstance(db_config, dict) else None
        )
        if not isinstance(legacy_msg_map_config, dict):
            legacy_msg_map_config = {}
        legacy_wipe_on_restart_value = legacy_msg_map_config.get(
            CONFIG_KEY_WIPE_ON_RESTART, False
        )
        legacy_wipe_on_restart = _coerce_config_bool(legacy_wipe_on_restart_value)

        if legacy_wipe_on_restart:
            wipe_on_restart = True
            logger.warning(
                "Using 'db.msg_map' configuration (legacy). 'database.msg_map' is now the preferred format and 'db.msg_map' will be deprecated in a future version."
            )

    if wipe_on_restart:
        logger.debug("wipe_on_restart enabled. Wiping message_map now (startup).")
        wipe_message_map()

    # Set up shutdown event
    shutdown_event = asyncio.Event()

    ready_task: asyncio.Task[None] | None = None
    check_connection_task: asyncio.Task[Any] | None = None
    node_name_refresh_task: asyncio.Task[None] | None = None
    matrix_client: Any | None = None
    fatal_exception: BaseException | None = None
    plugins_cleanup_needed = False
    message_queue_cleanup_needed = False

    def _set_shutdown_flag() -> None:
        """
        Set the Meshtastic shutdown flag and signal the shutdown event so tasks waiting for shutdown can proceed.
        """
        meshtastic_utils.shutting_down = True
        shutdown_event.set()

    def shutdown() -> None:
        """
        Request application shutdown and notify waiting coroutines.

        Logs that a shutdown was requested, sets the global shutdown flag, and signals the local shutdown event so tasks waiting on it can begin cleanup.
        """
        matrix_logger.info("Shutdown signal received. Closing down...")
        _set_shutdown_flag()

    def signal_handler() -> None:
        """
        Trigger the application's shutdown sequence from a synchronous signal handler.
        """
        shutdown()

    async def _run_blocking_shutdown_step(
        step_func: Callable[[], None],
        *,
        step_name: str,
        timeout_seconds: float,
    ) -> None:
        """
        Run a potentially blocking shutdown step off the event loop with a timeout.

        The callable executes on a daemon thread so shutdown can continue even if
        the step hangs. Exceptions are logged and swallowed so remaining cleanup
        still runs.
        """
        loop = asyncio.get_running_loop()
        step_result: asyncio.Future[BaseException | None] = loop.create_future()

        def _run_step() -> None:
            step_error: BaseException | None = None
            try:
                step_func()
            except BaseException as exc:
                step_error = exc

            def _publish_result() -> None:
                if not step_result.done():
                    step_result.set_result(step_error)

            try:
                loop.call_soon_threadsafe(_publish_result)
            except RuntimeError:
                # Event loop is closing; no further action is needed.
                return

        worker = threading.Thread(
            target=_run_step,
            name=f"shutdown-{step_name.replace(' ', '-')}",
            daemon=True,
        )
        worker.start()

        try:
            result = await asyncio.wait_for(step_result, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning(
                "Timed out stopping %s after %.1fs; continuing shutdown",
                step_name,
                timeout_seconds,
            )
            return

        if result is not None:
            logger.error(
                "Error while stopping %s",
                step_name,
                exc_info=(type(result), result, result.__traceback__),
            )

    async def _close_matrix_client_best_effort(*, context: str) -> None:
        """
        Close the Matrix client without aborting remaining shutdown work.
        """
        if matrix_client is None:
            return
        matrix_logger.info("Closing Matrix client...")
        try:
            await asyncio.wait_for(
                matrix_client.close(), timeout=MATRIX_CLIENT_CLOSE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            matrix_logger.error(
                "Timed out closing Matrix client during %s; continuing shutdown",
                context,
            )
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            matrix_logger.exception("Failed to close Matrix client during %s", context)

    async def _close_meshtastic_client_best_effort(*, context: str) -> None:
        """
        Close the Meshtastic client using BLE-aware blocking shutdown safeguards.
        """
        if not meshtastic_utils.meshtastic_client:
            return

        meshtastic_logger.info("Closing Meshtastic client...")

        def _close_meshtastic() -> None:
            if not meshtastic_utils.meshtastic_client:
                return
            if meshtastic_utils.meshtastic_client is meshtastic_utils.meshtastic_iface:
                # BLE shutdown needs explicit disconnect to release BlueZ state.
                meshtastic_utils._disconnect_ble_interface(
                    meshtastic_utils.meshtastic_iface,
                    reason=context,
                )
                meshtastic_utils.meshtastic_iface = None
            else:
                meshtastic_utils.meshtastic_client.close()

        try:
            await asyncio.to_thread(
                meshtastic_utils._run_blocking_with_timeout,
                _close_meshtastic,
                timeout=MESHTASTIC_CLOSE_TIMEOUT_SECONDS,
                label=f"meshtastic-client-close-{context.replace(' ', '-')}",
                timeout_log_level=None,
            )
            meshtastic_logger.info("Meshtastic client closed successfully")
        except TimeoutError:
            meshtastic_logger.warning(
                "Meshtastic client close timed out during %s - may cause notification errors",
                context,
            )
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            meshtastic_logger.exception(
                "Unexpected error during Meshtastic client close during %s",
                context,
            )
        finally:
            meshtastic_utils.meshtastic_client = None
            meshtastic_utils.meshtastic_iface = None

    try:
        # Load plugins early (run in executor to avoid blocking event loop with time.sleep)
        plugins_cleanup_needed = True
        await loop.run_in_executor(
            None, functools.partial(load_plugins, passed_config=config)
        )

        # Start message queue with configured message delay
        meshtastic_config = config.get(CONFIG_SECTION_MESHTASTIC)
        if not isinstance(meshtastic_config, dict):
            meshtastic_config = {}
        message_delay = meshtastic_config.get(
            CONFIG_KEY_MESSAGE_DELAY,
            DEFAULT_MESSAGE_DELAY,
        )
        message_queue_cleanup_needed = True
        start_message_queue(message_delay=message_delay)

        # Connect to Meshtastic
        meshtastic_utils.meshtastic_client = await asyncio.to_thread(
            connect_meshtastic, passed_config=config
        )
        if meshtastic_utils.meshtastic_client is None:
            raise ConnectionError(
                "Failed to connect to Meshtastic. Cannot continue without a relay client."
            )

        # Connect to Matrix
        matrix_client = await connect_matrix(passed_config=config)

        # Check if Matrix connection was successful
        if matrix_client is None:
            # The error is logged by connect_matrix, so we can just raise here.
            raise ConnectionError(
                "Failed to connect to Matrix. Cannot continue without Matrix client."
            )

        # Join the rooms specified in the config.yaml
        for room in matrix_rooms:
            await join_matrix_room(matrix_client, room["id"])

        # Register the message callback for Matrix
        matrix_logger.info("Listening for inbound Matrix messages...")
        matrix_client.add_event_callback(
            cast(Any, on_room_message),
            cast(
                Any,
                (RoomMessageText, RoomMessageNotice, RoomMessageEmote, ReactionEvent),
            ),
        )
        # Add E2EE callbacks - MegolmEvent only goes to decryption failure handler
        # Successfully decrypted messages will be converted to RoomMessageText etc. by matrix-nio
        matrix_client.add_event_callback(
            cast(Any, on_decryption_failure), cast(Any, (MegolmEvent,))
        )
        # Add RoomMemberEvent callback to track room-specific display name changes
        matrix_client.add_event_callback(
            cast(Any, on_room_member), cast(Any, (RoomMemberEvent,))
        )
        # Add InviteMemberEvent callback to automatically join mapped rooms on invite
        matrix_client.add_event_callback(
            cast(Any, on_invite), cast(Any, (InviteMemberEvent,))
        )

        # Handle signals differently based on the platform
        if sys.platform != WINDOWS_PLATFORM:
            signals = [signal.SIGINT, signal.SIGTERM]
            # Handle terminal hangups (e.g., SSH session closes) when supported.
            if hasattr(signal, "SIGHUP"):
                signals.append(signal.SIGHUP)
            for sig in signals:
                loop.add_signal_handler(sig, signal_handler)
        else:
            # On Windows, we can't use add_signal_handler, so we'll handle KeyboardInterrupt
            pass

        # Start connection health monitoring using getMetadata() heartbeat
        # This provides proactive connection detection for all interface types
        check_connection_callable = meshtastic_utils.check_connection
        check_connection_task = asyncio.create_task(check_connection_callable())

        def _on_check_connection_done(task: asyncio.Task[Any]) -> None:
            nonlocal fatal_exception
            if task.cancelled():
                return
            if shutdown_event.is_set():
                return
            exc = task.exception()
            if exc is not None:
                fatal_exception = exc
                meshtastic_logger.error(
                    "Connection health task exited unexpectedly",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                _set_shutdown_flag()
                # Mark exception as consumed to prevent double-logging in _await_background_task_shutdown
                task._exception_consumed = True  # type: ignore[attr-defined]
                return

            if (
                check_connection_callable is _DEFAULT_CHECK_CONNECTION_CALLABLE
                and _requires_continuous_health_monitor(config)
            ):
                fatal_exception = RuntimeError(
                    "Connection health task exited unexpectedly without an exception"
                )
                meshtastic_logger.error(
                    "Connection health task exited unexpectedly without an exception"
                )
                _set_shutdown_flag()

        check_connection_task.add_done_callback(_on_check_connection_done)
        # Give the health-check task one scheduling opportunity before readiness logic.
        # This only guards against immediate startup failures in check_connection().
        await asyncio.sleep(0)
    except BaseException:
        _set_shutdown_flag()
        if check_connection_task is not None:
            check_connection_task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(check_connection_task, return_exceptions=True),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                meshtastic_logger.warning(
                    "Timed out waiting for connection health task during startup rollback"
                )
        _remove_ready_file()
        if plugins_cleanup_needed:
            await _run_blocking_shutdown_step(
                shutdown_plugins,
                step_name="plugins",
                timeout_seconds=_PLUGIN_SHUTDOWN_TIMEOUT_SECONDS,
            )
        if message_queue_cleanup_needed:
            await _run_blocking_shutdown_step(
                stop_message_queue,
                step_name="message queue",
                timeout_seconds=_MESSAGE_QUEUE_SHUTDOWN_TIMEOUT_SECONDS,
            )
        await _close_matrix_client_best_effort(context="startup rollback")
        await _close_meshtastic_client_best_effort(context="startup rollback")
        await asyncio.to_thread(meshtastic_utils.shutdown_shared_executors)
        raise

    async def _node_name_refresh_supervisor(refresh_interval_seconds: float) -> None:
        """
        Run and supervise periodic NodeDB-derived name-cache refresh work.

        Current scope is refreshing long/short name tables from `client.nodes`.
        The interval setting is future-oriented and may be reused for broader
        NodeDB persistence in later releases.
        """
        restart_attempt = 0
        backoff_seconds = NODEDB_BACKOFF_INITIAL_SECONDS
        max_backoff_seconds = NODEDB_BACKOFF_MAX_SECONDS
        first_pass = True

        while first_pass or not shutdown_event.is_set():
            first_pass = False
            refresh_task = asyncio.create_task(
                meshtastic_utils.refresh_node_name_tables(
                    shutdown_event,
                    refresh_interval_seconds=refresh_interval_seconds,
                )
            )
            [refresh_result] = await asyncio.gather(
                refresh_task,
                return_exceptions=True,
            )

            if isinstance(refresh_result, asyncio.CancelledError):
                return

            if isinstance(refresh_result, Exception):
                if shutdown_event.is_set():
                    return
                if refresh_interval_seconds <= 0:
                    meshtastic_logger.error(
                        "NodeDB name-cache refresh task failed in one-shot mode (refresh_interval_seconds=%.1f); exiting",
                        refresh_interval_seconds,
                        exc_info=(
                            type(refresh_result),
                            refresh_result,
                            refresh_result.__traceback__,
                        ),
                    )
                    return
                restart_attempt += 1
                meshtastic_logger.error(
                    "NodeDB name-cache refresh task failed (attempt %d); restarting in %.1fs",
                    restart_attempt,
                    backoff_seconds,
                    exc_info=(
                        type(refresh_result),
                        refresh_result,
                        refresh_result.__traceback__,
                    ),
                )
            else:
                if shutdown_event.is_set() or refresh_interval_seconds <= 0:
                    return
                restart_attempt = 0
                backoff_seconds = NODEDB_BACKOFF_INITIAL_SECONDS
                meshtastic_logger.warning(
                    "NodeDB name-cache refresh task exited unexpectedly; restarting in %.1fs",
                    backoff_seconds,
                )

            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=backoff_seconds)
                return
            except asyncio.TimeoutError:
                backoff_seconds = min(backoff_seconds * 2.0, max_backoff_seconds)

    async def _await_background_task_shutdown(
        task: asyncio.Task[Any] | None,
        *,
        task_name: str,
        timeout_seconds: float,
    ) -> None:
        """
        Let a background task exit after shutdown is signaled, then cancel as fallback.
        """
        if task is None:
            return

        wait_error: BaseException | None = None
        if not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                task.cancel()
            except asyncio.CancelledError:
                task.cancel()
                return
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                wait_error = exc
                logger.error(
                    "Error while waiting for %s to finish during shutdown",
                    task_name,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        if not task.done():
            task.cancel()
            try:
                cleanup_results = await asyncio.wait_for(
                    asyncio.gather(task, return_exceptions=True),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Timed out cancelling %s; continuing shutdown",
                    task_name,
                )
                return
            except asyncio.CancelledError:
                return

            result = cleanup_results[0]
            if (
                isinstance(result, Exception)
                and not isinstance(result, asyncio.CancelledError)
                and result is not wait_error
            ):
                logger.error(
                    "Error during %s cleanup",
                    task_name,
                    exc_info=(type(result), result, result.__traceback__),
                )
            return

        cleanup_results = await asyncio.gather(task, return_exceptions=True)
        done_result: object = cleanup_results[0]
        if getattr(task, "_exception_consumed", False):
            return
        if (
            isinstance(done_result, Exception)
            and not isinstance(
                done_result,
                asyncio.CancelledError,
            )
            and done_result is not wait_error
        ):
            logger.error(
                "Error during %s cleanup",
                task_name,
                exc_info=(type(done_result), done_result, done_result.__traceback__),
            )

    # Start the Matrix client sync loop
    try:
        nodedb_refresh_interval_seconds = (
            meshtastic_utils.get_nodedb_refresh_interval_seconds(config)
        )
        if shutdown_event.is_set():
            matrix_logger.warning(
                "Skipping readiness publication because shutdown was requested during startup"
            )
        else:
            node_name_refresh_task = asyncio.create_task(
                _node_name_refresh_supervisor(
                    nodedb_refresh_interval_seconds,
                )
            )

            # Ensure message queue processor is started now that event loop is running.
            get_message_queue().ensure_processor_started()

            # Publish readiness only after startup wiring in this section is complete.
            _write_ready_file()

            # Start heartbeat AFTER readiness is confirmed
            if _ready_heartbeat_seconds > 0:
                ready_task = asyncio.create_task(_ready_heartbeat(shutdown_event))
        while not shutdown_event.is_set():
            sync_task: asyncio.Task[Any] | None = None
            shutdown_task: asyncio.Task[Any] | None = None
            try:
                matrix_logger.info("Starting Matrix sync loop...")
                sync_filter = getattr(matrix_client, "mmrelay_sync_filter", None)
                first_sync_filter = getattr(
                    matrix_client, "mmrelay_first_sync_filter", None
                )
                sync_task = asyncio.create_task(
                    matrix_client.sync_forever(
                        timeout=30000,
                        sync_filter=sync_filter,
                        first_sync_filter=first_sync_filter,
                    )
                )

                shutdown_task = asyncio.create_task(shutdown_event.wait())

                # Wait for either the matrix sync to fail, or for a shutdown
                done, pending = await asyncio.wait(
                    [sync_task, shutdown_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # Cancel any pending tasks
                for pending_task in pending:
                    if pending_task is shutdown_task:
                        pending_task.cancel()
                    await _await_background_task_shutdown(
                        pending_task,
                        task_name="matrix sync pending task",
                        timeout_seconds=0.0 if pending_task is shutdown_task else 5.0,
                    )

                if shutdown_event.is_set():
                    matrix_logger.info("Shutdown event detected. Stopping sync loop...")
                    break

                # Check if sync_task completed with an exception
                if sync_task in done:
                    try:
                        # This will raise the exception if the task failed
                        sync_task.result()
                        # If we get here, sync completed normally (shouldn't happen with sync_forever)
                        matrix_logger.warning(
                            "Matrix sync_forever completed unexpectedly"
                        )
                        try:
                            await asyncio.wait_for(shutdown_event.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            pass
                    except asyncio.TimeoutError as exc:
                        matrix_logger.warning(
                            "Matrix sync timed out, retrying: %s", exc
                        )
                        sync_task._exception_consumed = True  # type: ignore[attr-defined]
                        try:
                            await asyncio.wait_for(shutdown_event.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            pass
                    except ClientError as exc:
                        matrix_logger.warning("Matrix sync failed, retrying: %s", exc)
                        sync_task._exception_consumed = True  # type: ignore[attr-defined]
                        try:
                            await asyncio.wait_for(shutdown_event.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            pass
                    except (ConnectionError, OSError, RuntimeError, ValueError):
                        matrix_logger.exception("Matrix sync failed")
                        sync_task._exception_consumed = True  # type: ignore[attr-defined]
                        try:
                            await asyncio.wait_for(shutdown_event.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            pass

            except (ClientError, ConnectionError, OSError, RuntimeError, ValueError):
                if shutdown_event.is_set():
                    break
                matrix_logger.exception("Error syncing with Matrix server")
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
            finally:
                tasks_to_cleanup: list[asyncio.Task[Any]] = []
                for cleanup_task in (sync_task, shutdown_task):
                    if cleanup_task is None:
                        continue
                    if not cleanup_task.done():
                        cleanup_task.cancel()
                    tasks_to_cleanup.append(cleanup_task)

                if tasks_to_cleanup:
                    for cleanup_task in tasks_to_cleanup:
                        await _await_background_task_shutdown(
                            cleanup_task,
                            task_name="matrix sync cleanup task",
                            timeout_seconds=5.0,
                        )
    except KeyboardInterrupt:
        shutdown()
    finally:
        _set_shutdown_flag()
        await _await_background_task_shutdown(
            ready_task,
            task_name="ready heartbeat task",
            timeout_seconds=5.0,
        )
        _remove_ready_file()
        await _await_background_task_shutdown(
            node_name_refresh_task,
            task_name="NodeDB name-cache refresh task",
            timeout_seconds=NODEDB_SHUTDOWN_TIMEOUT_SECONDS,
        )
        await _await_background_task_shutdown(
            check_connection_task,
            task_name="connection health task",
            timeout_seconds=5.0,
        )
        # Cleanup
        matrix_logger.info("Stopping plugins...")
        await _run_blocking_shutdown_step(
            shutdown_plugins,
            step_name="plugins",
            timeout_seconds=_PLUGIN_SHUTDOWN_TIMEOUT_SECONDS,
        )
        matrix_logger.info("Stopping message queue...")
        await _run_blocking_shutdown_step(
            stop_message_queue,
            step_name="message queue",
            timeout_seconds=_MESSAGE_QUEUE_SHUTDOWN_TIMEOUT_SECONDS,
        )
        await _close_matrix_client_best_effort(context="shutdown")
        await _close_meshtastic_client_best_effort(context="shutdown")
        await asyncio.to_thread(meshtastic_utils.shutdown_shared_executors)

        # Attempt to wipe message_map on shutdown if enabled
        if wipe_on_restart:
            logger.debug("wipe_on_restart enabled. Wiping message_map now (shutdown).")
            wipe_message_map()

        # Cancel the reconnect task if it exists
        if meshtastic_utils.reconnect_task:
            meshtastic_utils.reconnect_task.cancel()
            meshtastic_logger.info("Cancelled Meshtastic reconnect task.")

        matrix_logger.info("Shutdown complete.")
        if fatal_exception is not None and sys.exc_info()[1] is None:
            raise fatal_exception


def run_main(args: Any) -> int:
    """
    Load and validate configuration, initialize logging and modules, and run the main application loop.

    Parameters:
        args (Any): Parsed command-line arguments (may be None). Recognized option: `log_level` to override the configured logging level.

    Returns:
        int: `0` on successful completion or user-initiated interrupt, `1` for configuration errors or unhandled runtime exceptions.
    """
    # Load configuration
    from mmrelay.config import load_config

    # Load configuration with args
    config = load_config(args=args)

    # Handle --log-level option
    if args and args.log_level:
        # Override the log level from config
        if "logging" not in config:
            config["logging"] = {}
        config["logging"]["level"] = args.log_level

    # Set the global config variables in each module
    from mmrelay import (
        db_utils,
        log_utils,
        matrix_utils,
        meshtastic_utils,
        plugin_loader,
    )
    from mmrelay.config import set_config
    from mmrelay.plugins import base_plugin

    # Apply logging configuration first so all subsequent logs land in the file
    set_config(log_utils, config)
    log_utils.refresh_all_loggers(args=args)

    # Ensure the module-level logger reflects the refreshed configuration
    global logger
    logger = get_logger(name=APP_DISPLAY_NAME, args=args)

    # Print the banner once logging is fully configured (so it reaches the log file)
    print_banner()

    # Use the centralized set_config function to set up the configuration for all modules
    set_config(matrix_utils, config)
    set_config(meshtastic_utils, config)
    set_config(plugin_loader, config)
    set_config(db_utils, config)
    set_config(base_plugin, config)

    # Configure component debug logging now that config is available
    log_utils.configure_component_debug_logging()

    # Get config path and log file path for logging
    from mmrelay.config import config_path
    from mmrelay.log_utils import log_file_path

    # Create a logger with a different name to avoid conflicts with the one in config.py
    config_rich_logger = get_logger("ConfigInfo", args=args)

    # Now log the config file and log file locations with the properly formatted logger
    if config_path:
        config_rich_logger.info(f"Config file location: {config_path}")
    if log_file_path:
        config_rich_logger.info(f"Log file location: {log_file_path}")

    legacy_envs = get_legacy_env_vars()
    legacy_dirs = get_legacy_dirs()
    if legacy_envs or legacy_dirs:
        config_rich_logger.warning(
            "Legacy data layout detected (MMRELAY_HOME=%s, legacy_env_vars=%s, legacy_dirs=%s). This layout is deprecated and will be removed in a future release.",
            str(get_home_dir()),
            ", ".join(legacy_envs) if legacy_envs else "none",
            ", ".join(str(p) for p in legacy_dirs) if legacy_dirs else "none",
        )
        config_rich_logger.warning(
            "To migrate to the new layout, see docs/DOCKER.md: Migrating to the New Layout."
        )

    # Check if config exists and has the required keys
    # Note: matrix section is optional if credentials.json exists
    from mmrelay.config import load_credentials

    credentials = load_credentials()

    if credentials:
        # With credentials.json, only meshtastic and matrix_rooms are required
        required_keys = list(REQUIRED_CONFIG_KEYS_WITH_CREDENTIALS)
    else:
        # Without credentials.json, all sections are required
        required_keys = list(REQUIRED_CONFIG_KEYS_WITHOUT_CREDENTIALS)

    # Check each key individually for better debugging
    for key in required_keys:
        if key not in config:
            logger.error(f"Required key '{key}' is missing from config")

    if not config or not all(key in config for key in required_keys):
        # Exit with error if no config exists
        missing_keys = [key for key in required_keys if key not in config]
        if credentials:
            logger.error(f"Configuration is missing required keys: {missing_keys}")
            logger.error("Matrix authentication will use credentials.json")
            logger.error("Next steps:")
            logger.error(
                f"  • Create a valid config.yaml file or {msg_suggest_generate_config()}"
            )
            logger.error(f"  • {msg_suggest_check_config()}")
        else:
            logger.error(f"Configuration is missing required keys: {missing_keys}")
            logger.error("Next steps:")
            logger.error(
                f"  • Create a valid config.yaml file or {msg_suggest_generate_config()}"
            )
            logger.error(f"  • {msg_suggest_check_config()}")
        return 1

    try:
        asyncio.run(main(config))
        return 0
    except KeyboardInterrupt:
        meshtastic_utils.shutting_down = True
        logger.info("Interrupted by user. Exiting.")
        return 0
    except Exception:  # noqa: BLE001 — top-level guard to log and exit cleanly
        logger.exception("Error running main functionality")
        return 1


if __name__ == "__main__":
    import sys

    from mmrelay.cli import main as cli_main

    sys.exit(cli_main())
