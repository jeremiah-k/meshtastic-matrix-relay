"""
This script connects a Meshtastic mesh network to Matrix chat rooms by relaying messages between them.
It uses Meshtastic-python and Matrix nio client library to interface with the radio and the Matrix server respectively.
"""

import asyncio
import concurrent.futures
import functools
import os
import signal
import sys
from pathlib import Path
from typing import Any, cast

from aiohttp import ClientError

# matrix-nio is not marked py.typed; keep import-untyped for strict mypy.
from nio import (  # type: ignore[import-untyped]
    MegolmEvent,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)
from nio.events.room_events import RoomMemberEvent  # type: ignore[import-untyped]

# Import version from package
# Import meshtastic_utils as a module to set event_loop
from mmrelay import __version__, meshtastic_utils
from mmrelay.cli_utils import msg_suggest_check_config, msg_suggest_generate_config
from mmrelay.constants.app import APP_DISPLAY_NAME, WINDOWS_PLATFORM
from mmrelay.constants.queue import DEFAULT_MESSAGE_DELAY
from mmrelay.db_utils import (
    initialize_database,
    update_longnames,
    update_shortnames,
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
from mmrelay.plugin_loader import load_plugins, shutdown_plugins

# Initialize logger
logger = get_logger(name=APP_DISPLAY_NAME)


# Flag to track if banner has been printed
_banner_printed = False
_ready_file_path = os.environ.get("MMRELAY_READY_FILE")
_ready_heartbeat_seconds_raw = os.environ.get("MMRELAY_READY_HEARTBEAT_SECONDS", "60")
try:
    _ready_heartbeat_seconds = int(_ready_heartbeat_seconds_raw)
except (TypeError, ValueError):
    logger.warning(
        "Invalid MMRELAY_READY_HEARTBEAT_SECONDS=%r; defaulting to 60",
        _ready_heartbeat_seconds_raw,
    )
    _ready_heartbeat_seconds = 60


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
            os.makedirs(ready_dir, exist_ok=True, mode=0o700)
            # Ensure directory has correct permissions when we own it.
            try:
                if (
                    os.path.isdir(ready_dir)
                    and os.stat(ready_dir).st_uid == os.geteuid()
                ):
                    os.chmod(ready_dir, 0o700)
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
            os.open(temp_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), "w"
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
        Path(_ready_file_path).touch(mode=0o600, exist_ok=True)
        os.chmod(_ready_file_path, 0o600)
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
        await asyncio.sleep(_ready_heartbeat_seconds)


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
    database_config = config.get("database", {})
    msg_map_config = database_config.get("msg_map", {})
    wipe_on_restart = msg_map_config.get("wipe_on_restart", False)

    # If not found in database config, check legacy db config
    if not wipe_on_restart:
        db_config = config.get("db", {})
        legacy_msg_map_config = db_config.get("msg_map", {})
        legacy_wipe_on_restart = legacy_msg_map_config.get("wipe_on_restart", False)

        if legacy_wipe_on_restart:
            wipe_on_restart = legacy_wipe_on_restart
            logger.warning(
                "Using 'db.msg_map' configuration (legacy). 'database.msg_map' is now the preferred format and 'db.msg_map' will be deprecated in a future version."
            )

    if wipe_on_restart:
        logger.debug("wipe_on_restart enabled. Wiping message_map now (startup).")
        wipe_message_map()

    # Load plugins early (run in executor to avoid blocking event loop with time.sleep)
    await loop.run_in_executor(
        None, functools.partial(load_plugins, passed_config=config)
    )

    # Start message queue with configured message delay
    message_delay = config.get("meshtastic", {}).get(
        "message_delay", DEFAULT_MESSAGE_DELAY
    )
    start_message_queue(message_delay=message_delay)

    # Connect to Meshtastic
    meshtastic_utils.meshtastic_client = await asyncio.to_thread(
        connect_meshtastic, passed_config=config
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

    # Set up shutdown event
    shutdown_event = asyncio.Event()

    # Signal readiness after core services and callbacks are initialized.
    _write_ready_file()
    ready_task: asyncio.Task[None] | None = None
    if _ready_heartbeat_seconds > 0:
        ready_task = asyncio.create_task(_ready_heartbeat(shutdown_event))

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
    _ = asyncio.create_task(meshtastic_utils.check_connection())

    # Ensure message queue processor is started now that event loop is running
    get_message_queue().ensure_processor_started()

    # Start the Matrix client sync loop
    try:
        while not shutdown_event.is_set():
            try:
                if meshtastic_utils.meshtastic_client:
                    nodes_snapshot = dict(meshtastic_utils.meshtastic_client.nodes)
                    await loop.run_in_executor(
                        None,
                        update_longnames,
                        nodes_snapshot,
                    )
                    await loop.run_in_executor(
                        None,
                        update_shortnames,
                        nodes_snapshot,
                    )
                else:
                    meshtastic_logger.warning("Meshtastic client is not connected.")

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
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

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
                    except (
                        Exception
                    ) as exc:  # noqa: BLE001 — sync loop must keep retrying
                        if isinstance(exc, (asyncio.TimeoutError, ClientError)):
                            matrix_logger.warning(
                                "Matrix sync timed out, retrying: %s", exc
                            )
                        else:
                            matrix_logger.exception("Matrix sync failed")
                        # The outer try/catch will handle the retry logic

            except Exception:  # noqa: BLE001 — keep loop alive for retries
                if shutdown_event.is_set():
                    break
                matrix_logger.exception("Error syncing with Matrix server")
                await asyncio.sleep(5)  # Wait briefly before retrying
    except KeyboardInterrupt:
        shutdown()
    finally:
        if ready_task is not None:
            ready_task.cancel()
            try:
                await ready_task
            except asyncio.CancelledError:
                pass
        _remove_ready_file()
        # Cleanup
        matrix_logger.info("Stopping plugins...")
        await loop.run_in_executor(None, shutdown_plugins)
        matrix_logger.info("Stopping message queue...")
        await loop.run_in_executor(None, stop_message_queue)

        matrix_logger.info("Closing Matrix client...")
        await matrix_client.close()
        if meshtastic_utils.meshtastic_client:
            meshtastic_logger.info("Closing Meshtastic client...")
            try:
                # Timeout wrapper to prevent infinite hanging during shutdown
                # The meshtastic library can sometimes hang indefinitely during close()
                # operations, especially with BLE connections. This timeout ensures
                # the application can shut down gracefully within 10 seconds.

                def _close_meshtastic() -> None:
                    """
                    Close and clean up the active Meshtastic client connection.

                    If a BLE interface is the active client, perform an explicit BLE disconnect to release the adapter.
                    Clears meshtastic_utils.meshtastic_client (and meshtastic_utils.meshtastic_iface when applicable).
                    Does nothing if no client is present.
                    """
                    if meshtastic_utils.meshtastic_client:
                        if (
                            meshtastic_utils.meshtastic_client
                            is meshtastic_utils.meshtastic_iface
                        ):
                            # BLE shutdown needs an explicit disconnect to release
                            # the adapter; a plain close() can leave BlueZ stuck.
                            meshtastic_utils._disconnect_ble_interface(
                                meshtastic_utils.meshtastic_iface,
                                reason="shutdown",
                            )
                            meshtastic_utils.meshtastic_iface = None
                        else:
                            meshtastic_utils.meshtastic_client.close()
                        meshtastic_utils.meshtastic_client = None

                # Avoid the context manager here: __exit__ would wait for the
                # worker thread and could block forever if BLE shutdown hangs,
                # negating the timeout protection.
                executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                future = executor.submit(_close_meshtastic)
                close_timed_out = False
                try:
                    future.result(timeout=10.0)  # 10-second timeout
                except concurrent.futures.TimeoutError:
                    close_timed_out = True
                    meshtastic_logger.warning(
                        "Meshtastic client close timed out - may cause notification errors"
                    )
                    # Best-effort cancellation; the underlying close may be
                    # stuck in BLE/DBus, but we cannot block shutdown.
                    future.cancel()
                except Exception:  # noqa: BLE001 - shutdown must keep going
                    meshtastic_logger.exception(
                        "Unexpected error during Meshtastic client close"
                    )
                else:
                    meshtastic_logger.info("Meshtastic client closed successfully")
                finally:
                    if not future.done():
                        if not close_timed_out:
                            meshtastic_logger.warning(
                                "Meshtastic client close timed out - may cause notification errors"
                            )
                        future.cancel()
                    try:
                        # Do not wait for shutdown; if close hangs we still
                        # want the process to exit promptly.
                        executor.shutdown(wait=False, cancel_futures=True)
                    except TypeError:
                        # cancel_futures is unsupported on older Python versions.
                        executor.shutdown(wait=False)
            except concurrent.futures.TimeoutError:
                meshtastic_logger.warning(
                    "Meshtastic client close timed out - forcing shutdown"
                )
            except Exception as e:
                meshtastic_logger.error(
                    f"Unexpected error during Meshtastic client close: {e}",
                    exc_info=True,
                )

        # Attempt to wipe message_map on shutdown if enabled
        if wipe_on_restart:
            logger.debug("wipe_on_restart enabled. Wiping message_map now (shutdown).")
            wipe_message_map()

        # Cancel the reconnect task if it exists
        if meshtastic_utils.reconnect_task:
            meshtastic_utils.reconnect_task.cancel()
            meshtastic_logger.info("Cancelled Meshtastic reconnect task.")

        # Cancel any remaining tasks (including the check_conn_task)
        current_task = asyncio.current_task()
        pending_tasks = [
            task
            for task in asyncio.all_tasks(loop)
            if task is not current_task and not task.done()
        ]

        for task in pending_tasks:
            task.cancel()

        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

        matrix_logger.info("Shutdown complete.")


def run_main(args: Any) -> int:
    """
    Start the application: load configuration, validate required keys, and run the main async runner.

    Loads and applies configuration (optionally overriding logging level from args), initializes module configuration, verifies required configuration sections (required keys are ["meshtastic", "matrix_rooms"] when credentials.json is present, otherwise ["matrix", "meshtastic", "matrix_rooms"]), and executes the main async entrypoint. Returns process exit codes: 0 for successful completion or user interrupt, 1 for configuration errors or unhandled exceptions.

    Parameters:
        args: Parsed command-line arguments (may be None). Recognized option used here: `log_level` to override the configured logging level.

    Returns:
        int: Exit code (0 on success or user-initiated interrupt, 1 on failure such as invalid config or runtime error).
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

    # Check if config exists and has the required keys
    # Note: matrix section is optional if credentials.json exists
    from mmrelay.config import load_credentials

    credentials = load_credentials()

    if credentials:
        # With credentials.json, only meshtastic and matrix_rooms are required
        required_keys = ["meshtastic", "matrix_rooms"]
    else:
        # Without credentials.json, all sections are required
        required_keys = ["matrix", "meshtastic", "matrix_rooms"]

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