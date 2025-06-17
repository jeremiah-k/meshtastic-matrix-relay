"""
This script connects a Meshtastic mesh network to Matrix chat rooms by relaying messages between them.
It uses Meshtastic-python and Matrix nio client library to interface with the radio and the Matrix server respectively.
"""

import asyncio
import logging
import signal
import sys

from nio import ReactionEvent, RoomMessageEmote, RoomMessageNotice, RoomMessageText
from nio.events.room_events import RoomMemberEvent

# Import version from package
from mmrelay import __version__
from mmrelay.db_utils import (
    initialize_database,
    update_longnames,
    update_shortnames,
    wipe_message_map,
)
from mmrelay.log_utils import get_logger
from mmrelay.matrix.client import connect_matrix, join_matrix_room
from mmrelay.matrix.handlers import on_room_member, on_room_message
from mmrelay.meshtastic.interface import (
    connect_meshtastic,
    check_connection as meshtastic_check_connection, # Alias to avoid conflict if main has one
    set_event_loop as set_meshtastic_event_loop,
    set_shutting_down_flag as set_meshtastic_shutting_down_flag,
    get_meshtastic_interface,
    # meshtastic_client as meshtastic_interface_client, # For direct access if needed, prefer get_meshtastic_interface
    reconnect_task as meshtastic_reconnect_task # For shutdown cleanup
)

from mmrelay.plugin_loader import load_plugins
# Import the meshtastic_utils shim to access its re-exported reconnect_task for now
# This is a temporary measure. Ideally, main.py would get reconnect_task from meshtastic.interface directly.
from mmrelay import meshtastic_utils


# Initialize logger
logger = get_logger(name="M<>M Relay")

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
    Main asynchronous function to set up and run the relay.
    Includes logic for wiping the message_map if configured in database.msg_map.wipe_on_restart
    or db.msg_map.wipe_on_restart (legacy format).
    Also updates longnames and shortnames periodically as before.

    Args:
        config: The loaded configuration
    """
    # Extract Matrix configuration
    from typing import List

    matrix_rooms: List[dict] = config["matrix_rooms"]

    # Set the event loop in meshtastic.interface
    set_meshtastic_event_loop(asyncio.get_event_loop())

    # Initialize the SQLite database
    initialize_database()

# Initialize logger
logger = get_logger(name="M<>M Relay")

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
    Main asynchronous function to set up and run the relay.
    Includes logic for wiping the message_map if configured in database.msg_map.wipe_on_restart
    or db.msg_map.wipe_on_restart (legacy format).
    Also updates longnames and shortnames periodically as before.

    Args:
        config: The loaded configuration
    """
    # Extract Matrix configuration
    from typing import List

    # Check database config for wipe_on_restart (preferred format)
    database_config = config.get("database", {})
    msg_map_config = database_config.get("msg_map", {})
    wipe_on_restart = msg_map_config.get("wipe_on_restart", False)

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

    # Connect to Meshtastic - this will set the client in meshtastic.interface
    connect_meshtastic(passed_config=config)

    # Connect to Matrix
    matrix_client = await connect_matrix(passed_config=config)

    # Join the rooms specified in the config.yaml
    for room in matrix_rooms:
        await join_matrix_room(matrix_client, room["id"])

    # Register the message callback for Matrix
    logger.info("Listening for inbound Matrix messages...") # Use main logger
    matrix_client.add_event_callback(
        on_room_message, (RoomMessageText, RoomMessageNotice, RoomMessageEmote)
    )
    # Add ReactionEvent callback so we can handle matrix reactions
    matrix_client.add_event_callback(on_room_message, ReactionEvent)
    # Add RoomMemberEvent callback to track room-specific display name changes
    matrix_client.add_event_callback(on_room_member, RoomMemberEvent)

    # Set up shutdown event
    shutdown_event = asyncio.Event()

    async def shutdown():
        logger.info("Shutdown signal received. Closing down...") # Use main logger
        set_meshtastic_shutting_down_flag(True)  # Use new function
        shutdown_event.set()

    loop = asyncio.get_running_loop()

    # Handle signals differently based on the platform
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))
    else:
        # On Windows, we can't use add_signal_handler, so we'll handle KeyboardInterrupt
        pass

    # -------------------------------------------------------------------
    # IMPORTANT: We create a task to run the meshtastic_check_connection()
    # so its while loop runs in parallel with the matrix sync loop
    # Use "_" to avoid trunk's "assigned but unused variable" warning
    # -------------------------------------------------------------------
    _ = asyncio.create_task(meshtastic_check_connection()) # Use new function

    # Start the Matrix client sync loop
    try:
        while not shutdown_event.is_set():
            try:
                current_meshtastic_client = get_meshtastic_interface()
                if current_meshtastic_client:
                    # Update longnames & shortnames
                    update_longnames(current_meshtastic_client.nodes)
                    update_shortnames(current_meshtastic_client.nodes)
                else:
                    logger.warning("Meshtastic client is not connected.") # Use main logger

                logger.info("Starting Matrix sync loop...") # Use main logger
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
                        pass # Expected during shutdown
                    break # Exit while loop

            except Exception as e:
                if shutdown_event.is_set():
                    break # Exit while loop if shutdown during error handling
                logger.error(f"Error syncing with Matrix server: {e}") # Use main logger
                await asyncio.sleep(5)  # Wait briefly before retrying
    except KeyboardInterrupt: # Should be caught by signal handler on non-Windows
        logger.info("KeyboardInterrupt received, initiating shutdown...")
        await shutdown()
    finally:
        # Cleanup
        logger.info("Closing Matrix client...") # Use main logger
        if matrix_client and hasattr(matrix_client, 'close'): # Ensure client exists and has close method
            await matrix_client.close()

        current_meshtastic_client = get_meshtastic_interface()
        if current_meshtastic_client:
            logger.info("Closing Meshtastic client...") # Use main logger
            try:
                current_meshtastic_client.close()
            except Exception as e:
                logger.warning(f"Error closing Meshtastic client: {e}") # Use main logger

        # Attempt to wipe message_map on shutdown if enabled
        if wipe_on_restart:
            logger.debug("wipe_on_restart enabled. Wiping message_map now (shutdown).")
            wipe_message_map()

        # Cancel the reconnect task if it exists - use the one from meshtastic_utils shim for now
        if meshtastic_utils.reconnect_task and not meshtastic_utils.reconnect_task.done():
            meshtastic_utils.reconnect_task.cancel()
            logger.info("Cancelled Meshtastic reconnect task via shim.") # Use main logger

        # Cancel any remaining tasks (including the check_conn_task)
        tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task() and not t.done()]
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

    # Use the centralized set_config function to set up the configuration for modules that still use it.
    # matrix_utils and meshtastic_utils (now shims) don't need set_config.
    # Their underlying modules (matrix.client, meshtastic.interface) get config via connect_* calls.
    set_config(plugin_loader, config)
    set_config(log_utils, config)
    set_config(db_utils, config)
    set_config(base_plugin, config)

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
    required_keys = ["matrix", "meshtastic", "matrix_rooms"]

    # Check each key individually for better debugging
    for key in required_keys:
        if key not in config:
            logger.error(f"Required key '{key}' is missing from config")

    if not config or not all(key in config for key in required_keys):
        # Exit with error if no config exists
        missing_keys = [key for key in required_keys if key not in config]
        logger.error(
            f"Configuration is missing required keys: {missing_keys}. "
            "Please create a valid config.yaml file or use --generate-config to create one."
        )
        return 1

    try:
        asyncio.run(main(config))
        return 0
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Exiting.")
        return 0
    except Exception as e:
        logger.error(f"Error running main functionality: {e}")
        return 1


if __name__ == "__main__":
    import sys

    from mmrelay.cli import main

    sys.exit(main())
