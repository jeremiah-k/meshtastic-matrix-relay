"""
This script connects a Meshtastic mesh network to Matrix chat rooms by relaying messages between them.
It uses Meshtastic-python and Matrix nio client library to interface with the radio and the Matrix server respectively.
"""

import asyncio
import concurrent.futures
import logging
import signal
import sys

from nio import (
    MegolmEvent,
    ReactionEvent,
    RoomEncryptionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)
from nio.events.room_events import RoomMemberEvent

# Import version from package
# Import meshtastic_utils as a module to set event_loop
from mmrelay import __version__, meshtastic_utils
from mmrelay.cli_utils import msg_suggest_generate_config
from mmrelay.constants.app import APP_DISPLAY_NAME, WINDOWS_PLATFORM
from mmrelay.db_utils import (
    initialize_database,
    update_longnames,
    update_shortnames,
    wipe_message_map,
)
from mmrelay.log_utils import get_logger
from mmrelay.matrix_utils import (
    connect_matrix,
    join_matrix_room,
)
from mmrelay.matrix_utils import logger as matrix_logger
from mmrelay.matrix_utils import (
    on_decryption_failure,
    on_room_member,
    on_room_message,
)
from mmrelay.meshtastic_utils import connect_meshtastic
from mmrelay.meshtastic_utils import logger as meshtastic_logger
from mmrelay.message_queue import (
    DEFAULT_MESSAGE_DELAY,
    get_message_queue,
    start_message_queue,
    stop_message_queue,
)
from mmrelay.plugin_loader import load_plugins

# Initialize logger
logger = get_logger(name=APP_DISPLAY_NAME)

# Set the logging level for 'nio' to ERROR to suppress warnings
logging.getLogger("nio").setLevel(logging.ERROR)


# Flag to track if banner has been printed
_banner_printed = False


def print_banner():
    """Print a simple startup message with version information."""
    global _banner_printed
    # Only print the banner once
    if not _banner_printed:
        logger.info(f"Starting MMRelay v{__version__}")
        _banner_printed = True


async def main(config):
    """
    Coordinates the main asynchronous relay loop between Meshtastic and Matrix clients.

    Initializes the database, loads plugins, starts the message queue, and establishes connections to both Meshtastic and Matrix. Joins configured Matrix rooms, registers event callbacks for message and membership events, and periodically updates node names from the Meshtastic network. Monitors connection health, manages the Matrix sync loop with reconnection and shutdown handling, and ensures graceful shutdown of all components. Optionally wipes the message map on startup and shutdown if configured.
    """
    # Extract Matrix configuration
    from typing import List

    matrix_rooms: List[dict] = config["matrix_rooms"]

    # Set the event loop in meshtastic_utils
    meshtastic_utils.event_loop = asyncio.get_event_loop()

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

    # Load plugins early
    load_plugins(passed_config=config)

    # Start message queue with configured message delay
    message_delay = config.get("meshtastic", {}).get(
        "message_delay", DEFAULT_MESSAGE_DELAY
    )
    start_message_queue(message_delay=message_delay)

    # Connect to Meshtastic
    meshtastic_utils.meshtastic_client = connect_meshtastic(passed_config=config)

    # Connect to Matrix
    matrix_client = await connect_matrix(passed_config=config)

    # Join the rooms specified in the config.yaml
    for room in matrix_rooms:
        await join_matrix_room(matrix_client, room["id"])

    # Register the message callback for Matrix
    matrix_logger.info("Listening for inbound Matrix messages...")
    matrix_client.add_event_callback(
        on_room_message,
        (RoomMessageText, RoomMessageNotice, RoomMessageEmote, ReactionEvent),
    )
    # Add E2EE callbacks
    matrix_client.add_event_callback(on_decryption_failure, (MegolmEvent,))
    matrix_client.add_event_callback(on_room_message, (RoomEncryptionEvent,))
    # Add RoomMemberEvent callback to track room-specific display name changes
    matrix_client.add_event_callback(on_room_member, (RoomMemberEvent,))

    # Set up shutdown event
    shutdown_event = asyncio.Event()

    async def shutdown():
        matrix_logger.info("Shutdown signal received. Closing down...")
        meshtastic_utils.shutting_down = True  # Set the shutting_down flag
        shutdown_event.set()

    loop = asyncio.get_running_loop()

    # Handle signals differently based on the platform
    if sys.platform != WINDOWS_PLATFORM:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))
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
                    # Update longnames & shortnames
                    update_longnames(meshtastic_utils.meshtastic_client.nodes)
                    update_shortnames(meshtastic_utils.meshtastic_client.nodes)
                else:
                    meshtastic_logger.warning("Meshtastic client is not connected.")

                matrix_logger.info("Starting Matrix sync loop...")
                sync_task = asyncio.create_task(
                    matrix_client.sync_forever(timeout=30000)
                )

                shutdown_task = asyncio.create_task(shutdown_event.wait())

                # Wait for either the matrix sync to fail, or for a shutdown
                done, pending = await asyncio.wait(
                    [sync_task, shutdown_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if shutdown_event.is_set():
                    matrix_logger.info("Shutdown event detected. Stopping sync loop...")
                    sync_task.cancel()
                    try:
                        await sync_task
                    except asyncio.CancelledError:
                        pass
                    break

            except Exception as e:
                if shutdown_event.is_set():
                    break
                matrix_logger.error(f"Error syncing with Matrix server: {e}")
                await asyncio.sleep(5)  # Wait briefly before retrying
    except KeyboardInterrupt:
        await shutdown()
    finally:
        # Cleanup
        matrix_logger.info("Stopping message queue...")
        stop_message_queue()

        matrix_logger.info("Closing Matrix client...")
        await matrix_client.close()
        if meshtastic_utils.meshtastic_client:
            meshtastic_logger.info("Closing Meshtastic client...")
            try:
                # Timeout wrapper to prevent infinite hanging during shutdown
                # The meshtastic library can sometimes hang indefinitely during close()
                # operations, especially with BLE connections. This timeout ensures
                # the application can shut down gracefully within 10 seconds.

                def _close_meshtastic():
                    """
                    Closes the Meshtastic client connection synchronously.
                    """
                    meshtastic_utils.meshtastic_client.close()

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_close_meshtastic)
                    future.result(timeout=10.0)  # 10-second timeout

                meshtastic_logger.info("Meshtastic client closed successfully")
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
        tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        matrix_logger.info("Shutdown complete.")


def run_main(args):
    """Run the main functionality of the application.

    Args:
        args: The parsed command-line arguments

    Returns:
        int: Exit code (0 for success, non-zero for failure)
    """
    # Print the banner at startup
    print_banner()

    # Handle the --data-dir option
    if args and args.data_dir:
        import os

        import mmrelay.config

        # Set the global custom_data_dir variable
        mmrelay.config.custom_data_dir = os.path.abspath(args.data_dir)
        # Create the directory if it doesn't exist
        os.makedirs(mmrelay.config.custom_data_dir, exist_ok=True)

    # Load configuration
    from mmrelay.config import load_config

    # Load configuration with args
    config = load_config(args=args)

    # Handle the --log-level option
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

    # Use the centralized set_config function to set up the configuration for all modules
    set_config(matrix_utils, config)
    set_config(meshtastic_utils, config)
    set_config(plugin_loader, config)
    set_config(log_utils, config)
    set_config(db_utils, config)
    set_config(base_plugin, config)

    # Configure component debug logging now that config is available
    log_utils.configure_component_debug_logging()

    # Get config path and log file path for logging
    from mmrelay.config import config_path
    from mmrelay.log_utils import log_file_path

    # Create a logger with a different name to avoid conflicts with the one in config.py
    config_rich_logger = get_logger("ConfigInfo")

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
            logger.error(
                f"Configuration is missing required keys: {missing_keys}. "
                f"Matrix authentication will use credentials.json. "
                f"Please create a valid config.yaml file or {msg_suggest_generate_config()}"
            )
        else:
            logger.error(
                f"Configuration is missing required keys: {missing_keys}. "
                f"Please create a valid config.yaml file or {msg_suggest_generate_config()}"
            )
        return 1

    try:
        asyncio.run(main(config))
        return 0
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Exiting.")
        return 0
    except Exception as e:
        import traceback

        logger.error(f"Error running main functionality: {e}")
        logger.error("Full traceback:")
        logger.error(traceback.format_exc())
        return 1


if __name__ == "__main__":
    import sys

    from mmrelay.cli import main

    sys.exit(main())
