"""
This script connects a Meshtastic mesh network to Matrix chat rooms by relaying messages between them.
It uses Meshtastic-python and Matrix nio client library to interface with the radio and the Matrix server respectively.
"""

import asyncio
import logging
import signal
import sys
from typing import List

from nio import ReactionEvent, RoomMessageEmote, RoomMessageNotice, RoomMessageText

# Import meshtastic_utils as a module to set event_loop
from mmrelay import meshtastic_utils
from mmrelay.cli import parse_arguments
from mmrelay.config import relay_config
from mmrelay.db_utils import (
    initialize_database,
    update_longnames,
    update_shortnames,
    wipe_message_map,
)
from mmrelay.log_utils import get_logger
from mmrelay.matrix_utils import connect_matrix, join_matrix_room
from mmrelay.matrix_utils import logger as matrix_logger
from mmrelay.matrix_utils import on_room_message
from mmrelay.meshtastic_utils import connect_meshtastic
from mmrelay.meshtastic_utils import logger as meshtastic_logger
from mmrelay.plugin_loader import load_plugins

# Initialize logger
logger = get_logger(name="M<>M Relay")

# Extract Matrix configuration
matrix_rooms: List[dict] = relay_config["matrix_rooms"]

# Set the logging level for 'nio' to ERROR to suppress warnings
logging.getLogger("nio").setLevel(logging.ERROR)


async def main():
    """
    Main asynchronous function to set up and run the relay.
    Includes logic for wiping the message_map if configured in database.msg_map.wipe_on_restart
    or db.msg_map.wipe_on_restart (legacy format).
    Also updates longnames and shortnames periodically as before.
    """
    # Set the event loop in meshtastic_utils
    meshtastic_utils.event_loop = asyncio.get_event_loop()

    # Initialize the SQLite database
    initialize_database()

    # Check database config for wipe_on_restart (preferred format)
    database_config = relay_config.get("database", {})
    msg_map_config = database_config.get("msg_map", {})
    wipe_on_restart = msg_map_config.get("wipe_on_restart", False)

    # If not found in database config, check legacy db config
    if not wipe_on_restart:
        db_config = relay_config.get("db", {})
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
    load_plugins()

    # Connect to Meshtastic
    meshtastic_utils.meshtastic_client = connect_meshtastic()

    # Connect to Matrix
    matrix_client = await connect_matrix()
    if matrix_client is None:
        logger.error("Failed to connect to Matrix. Exiting.")
        return

    # Join the rooms specified in the config.yaml
    for room in matrix_rooms:
        await join_matrix_room(matrix_client, room["id"])

    # Register the message callback for Matrix
    matrix_logger.info("Listening for inbound Matrix messages...")
    matrix_client.add_event_callback(
        on_room_message, (RoomMessageText, RoomMessageNotice, RoomMessageEmote)
    )
    # Add ReactionEvent callback so we can handle matrix reactions
    matrix_client.add_event_callback(on_room_message, ReactionEvent)

    # Set up shutdown event
    shutdown_event = asyncio.Event()

    async def shutdown():
        matrix_logger.info("Shutdown signal received. Closing down...")
        meshtastic_utils.shutting_down = True  # Set the shutting_down flag
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
    # IMPORTANT: We create a task to run the meshtastic_utils.check_connection()
    # so its while loop runs in parallel with the matrix sync loop
    # Use "_" to avoid trunk's "assigned but unused variable" warning
    # -------------------------------------------------------------------
    _ = asyncio.create_task(meshtastic_utils.check_connection())

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
        matrix_logger.info("Closing Matrix client...")
        await matrix_client.close()
        if meshtastic_utils.meshtastic_client:
            meshtastic_logger.info("Closing Meshtastic client...")
            try:
                meshtastic_utils.meshtastic_client.close()
            except Exception as e:
                meshtastic_logger.warning(f"Error closing Meshtastic client: {e}")

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


def generate_sample_config():
    """Generate a sample config.yaml file."""
    import os
    import shutil

    import pkg_resources

    from mmrelay.config import get_config_paths

    # Get the first config path (highest priority)
    config_paths = get_config_paths()

    # Check if any config file exists
    existing_config = None
    for path in config_paths:
        if os.path.isfile(path):
            existing_config = path
            break

    if existing_config:
        print(f"A config file already exists at: {existing_config}")
        print(
            "Use --config to specify a different location if you want to generate a new one."
        )
        return False

    # No config file exists, generate one in the first location
    target_path = config_paths[0]

    # Ensure the directory exists
    os.makedirs(os.path.dirname(target_path), exist_ok=True)

    # Copy the sample config to the target location
    sample_config_path = pkg_resources.resource_filename(
        "mmrelay", "../sample_config.yaml"
    )
    if not os.path.exists(sample_config_path):
        # Try alternative location
        sample_config_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "sample_config.yaml"
        )

    if os.path.exists(sample_config_path):
        shutil.copy(sample_config_path, target_path)
        print(f"Generated sample config file at: {target_path}")
        return True
    else:
        print("Error: Could not find sample_config.yaml")
        return False


def run():
    """Entry point for the application."""
    # Parse command-line arguments
    args = parse_arguments()  # This initializes the arguments for other modules to use

    # Handle --install-service
    if args.install_service:
        from mmrelay.setup_utils import install_service

        success = install_service()
        import sys

        sys.exit(0 if success else 1)

    # Handle --generate-config
    if args.generate_config:
        if generate_sample_config():
            # Exit with success if config was generated
            return
        else:
            # Exit with error if config generation failed
            import sys

            sys.exit(1)

    # Check if config exists
    from mmrelay.config import relay_config

    if not relay_config:
        # Exit with error if no config exists
        import sys

        sys.exit(1)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
