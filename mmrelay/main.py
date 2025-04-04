# ./mmrelay/main.py:
"""
Core asynchronous logic for the Matrix <> Meshtastic Relay.
Assumes configuration is loaded and logging is set up before `main()` is called.
"""

# Keep essential imports for the core logic
import asyncio
import logging
import signal
import sys
from typing import List

# --- Imports needed for the main async logic ---
# These are safe because this code runs after setup in cli.py
from nio import ReactionEvent, RoomMessageEmote, RoomMessageNotice, RoomMessageText

# Import package modules needed for core operation
from mmrelay import meshtastic_utils # Import module to access event_loop, client etc.
from mmrelay.config import relay_config # Access the globally loaded config
from mmrelay.db_utils import initialize_database, wipe_message_map, update_longnames, update_shortnames, get_db_path
from mmrelay.matrix_utils import connect_matrix, join_matrix_room, on_room_message
from mmrelay.matrix_utils import logger as matrix_logger # Get specific loggers
from mmrelay.meshtastic_utils import connect_meshtastic, check_connection
from mmrelay.meshtastic_utils import logger as meshtastic_logger # Get specific loggers
from mmrelay.plugin_loader import load_plugins
from mmrelay import __version__ # Get version info if needed for logging


# Get logger instance (handlers are configured by cli.py)
logger = logging.getLogger(__name__)


# Renamed from main_async in previous step, now it's the primary function here.
async def main():
    """
    Main asynchronous function containing the core relay logic.
    """
    logger.info(f"Starting Matrix <> Meshtastic Relay core v{__version__}")

    # --- Setup based on loaded config ---

    # Set the event loop in meshtastic_utils (assuming it needs it early)
    # Consider if this should be passed or handled differently if meshtastic_utils becomes more complex
    if not meshtastic_utils.event_loop:
         meshtastic_utils.event_loop = asyncio.get_event_loop()
         logger.debug("Event loop set in meshtastic_utils")


    # Initialize the SQLite database (uses path determined by path_utils)
    # Log the path used by db_utils
    logger.info(f"Using database at: {get_db_path()}")
    try:
        initialize_database()
    except Exception as e:
         logger.critical(f"Failed to initialize database: {e}", exc_info=True)
         # Decide whether to exit if DB init fails
         return


    # Check db config for wipe_on_restart
    db_config = relay_config.get("db", {}) # relay_config is already populated
    msg_map_config = db_config.get("msg_map", {})
    wipe_on_restart = msg_map_config.get("wipe_on_restart", False)

    if wipe_on_restart:
        logger.debug("wipe_on_restart enabled. Wiping message_map now (startup).")
        try:
            wipe_message_map()
        except Exception as e:
             logger.error(f"Failed to wipe message map on startup: {e}", exc_info=True)


    # Load plugins (uses relay_config)
    try:
         load_plugins()
    except Exception as e:
         logger.critical(f"Failed to load plugins: {e}", exc_info=True)
         # Decide whether to exit if plugin loading fails
         return


    # Connect to Meshtastic (uses relay_config)
    # connect_meshtastic handles retries internally
    meshtastic_utils.meshtastic_client = connect_meshtastic()
    if not meshtastic_utils.meshtastic_client:
         logger.error("Initial Meshtastic connection failed after retries. Will keep trying in background.")
         # The check_connection task will handle future attempts


    # Connect to Matrix (uses relay_config)
    matrix_client = None # Initialize
    try:
         matrix_client = await connect_matrix()
         if matrix_client is None:
             logger.critical("Failed to connect to Matrix. Cannot proceed.")
             return # Exit if Matrix connection fails initially
    except Exception as e:
         logger.critical(f"Failed during initial Matrix connection: {e}", exc_info=True)
         return


    # Join the rooms specified in the config.yaml
    matrix_rooms: List[dict] = relay_config.get("matrix_rooms", [])
    if not matrix_rooms:
         logger.warning("No matrix_rooms defined in the configuration.")
    else:
         logger.info(f"Attempting to join {len(matrix_rooms)} Matrix room(s)...")
         for room in matrix_rooms:
             room_id = room.get("id")
             if room_id:
                  try:
                      await join_matrix_room(matrix_client, room_id)
                  except Exception as e:
                       logger.error(f"Failed to join Matrix room '{room_id}': {e}", exc_info=True)
             else:
                  logger.warning(f"Skipping room entry with missing 'id': {room}")


    # Register the message callback for Matrix
    matrix_logger.info("Registering Matrix message callbacks...")
    matrix_client.add_event_callback(
        on_room_message, (RoomMessageText, RoomMessageNotice, RoomMessageEmote)
    )
    # Add ReactionEvent callback so we can handle matrix reactions
    matrix_client.add_event_callback(on_room_message, ReactionEvent)


    # Set up shutdown event
    shutdown_event = asyncio.Event()


    async def shutdown_signal_handler(sig_name):
        if not shutdown_event.is_set(): # Prevent multiple shutdowns
             logger.info(f"Shutdown signal ({sig_name}) received. Closing down...")
             meshtastic_utils.shutting_down = True  # Set the shutting_down flag
             shutdown_event.set()
        else:
             logger.debug("Shutdown already in progress.")


    loop = asyncio.get_running_loop()


    # Handle signals for graceful shutdown
    shutdown_signals = (signal.SIGINT, signal.SIGTERM)
    for sig in shutdown_signals:
        sig_name = signal.Signals(sig).name # Get signal name
        try:
            # Pass signal name to handler for logging clarity
            loop.add_signal_handler(sig, lambda s=sig_name: asyncio.create_task(shutdown_signal_handler(s)))
            logger.debug(f"Registered signal handler for {sig_name}")
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for SIGTERM, SIGINT is handled by KeyboardInterrupt
            logger.debug(f"Signal handler for {sig_name} not supported on this platform.")
        except ValueError as e:
            # Handle cases like running in a thread where signal handlers can't be added
            logger.warning(f"Could not set signal handler for {sig_name}: {e}")


    # Create task for periodic Meshtastic connection check
    logger.info("Starting background task: Meshtastic connection watcher.")
    check_conn_task = asyncio.create_task(check_connection(), name="ConnectionChecker")


    # --- Main Run Loop ---
    logger.info("Entering main run loop (Matrix sync / event processing)...")
    try:
        while not shutdown_event.is_set():
            try:
                # Start the Matrix client sync loop
                # Using a timeout allows the loop to periodically check the shutdown_event
                logger.debug("Starting Matrix sync cycle...")
                # Consider making timeout configurable
                sync_timeout = relay_config.get("matrix", {}).get("sync_timeout", 30)
                await matrix_client.sync(timeout=sync_timeout * 1000) # nio expects ms
                logger.debug("Matrix sync cycle complete.")

            except asyncio.TimeoutError:
                 # Expected timeout, continue loop to check shutdown_event
                 logger.debug(f"Matrix sync timed out after {sync_timeout}s, continuing loop.")
                 pass # Normal timeout, just continue the loop
            except ConnectionError as e:
                 matrix_logger.error(f"Matrix connection error during sync: {e}. Will retry.")
                 await asyncio.sleep(15) # Wait before retrying sync after connection error
            except Exception as e:
                # Catch other unexpected errors during sync
                if shutdown_event.is_set():
                    logger.debug("Shutdown initiated during sync error, exiting loop.")
                    break # Exit loop if shutdown initiated during sync error
                matrix_logger.error(f"Unhandled error during Matrix sync: {e}", exc_info=True)
                await asyncio.sleep(30) # Wait longer before retrying sync after unknown error

    except asyncio.CancelledError:
         logger.info("Main run loop task cancelled.")
         # Ensure shutdown is triggered if cancelled externally
         if not shutdown_event.is_set():
             await shutdown_signal_handler("ExternalCancellation")
    finally:
        # --- Cleanup ---
        logger.info("Starting final cleanup sequence...")

        # Ensure shutdown event is set and flag is True
        shutdown_event.set()
        meshtastic_utils.shutting_down = True

        # Close Matrix client
        if matrix_client:
            matrix_logger.info("Closing Matrix client connection...")
            try:
                await matrix_client.close()
                matrix_logger.info("Matrix client closed.")
            except Exception as e:
                 matrix_logger.error(f"Error closing Matrix client: {e}", exc_info=True)

        # Close Meshtastic client
        if meshtastic_utils.meshtastic_client:
            meshtastic_logger.info("Closing Meshtastic interface...")
            try:
                # Run close in executor if it might block significantly?
                # loop = asyncio.get_running_loop()
                # await loop.run_in_executor(None, meshtastic_utils.meshtastic_client.close)
                meshtastic_utils.meshtastic_client.close() # Try direct call first
                meshtastic_logger.info("Meshtastic interface closed.")
            except Exception as e:
                meshtastic_logger.warning(f"Error closing Meshtastic interface: {e}", exc_info=True)

        # Attempt to wipe message_map on shutdown if enabled
        db_config = relay_config.get("db", {})
        msg_map_config = db_config.get("msg_map", {})
        if msg_map_config.get("wipe_on_restart", False):
            logger.info("Wiping message_map as per configuration (shutdown).")
            try:
                 wipe_message_map()
            except Exception as e:
                 logger.error(f"Error wiping message map during shutdown: {e}", exc_info=True)


        # Cancel pending tasks (like check_connection, reconnect)
        logger.info("Cancelling background tasks...")
        tasks_to_cancel = [check_conn_task]
        if meshtastic_utils.reconnect_task and not meshtastic_utils.reconnect_task.done():
             tasks_to_cancel.append(meshtastic_utils.reconnect_task)

        for task in tasks_to_cancel:
             if task and not task.done():
                 task_name = task.get_name() if hasattr(task, 'get_name') else 'Unnamed Task'
                 logger.debug(f"Cancelling task: {task_name}")
                 task.cancel()
                 try:
                     # Give task a chance to handle cancellation
                     await asyncio.wait_for(task, timeout=2.0)
                 except asyncio.CancelledError:
                     logger.debug(f"Task {task_name} cancelled successfully.")
                 except asyncio.TimeoutError:
                      logger.warning(f"Task {task_name} did not finish cancelling within timeout.")
                 except Exception as e:
                      logger.error(f"Error during cancellation of task {task_name}: {e}", exc_info=True)

        logger.info("Core relay shutdown process complete.")


# Keep this block for direct execution `python mmrelay/main.py`
# This now calls the *new* entry point in cli.py
if __name__ == "__main__":
    print("Running main.py directly, invoking CLI entry point...", file=sys.stderr)
    # Import the actual entry point function from cli.py
    try:
        from mmrelay.cli import entry_point
        entry_point()
    except ImportError:
         print("Error: Could not import entry_point from mmrelay.cli", file=sys.stderr)
         print("Please run using 'python -m mmrelay' or the installed 'mmrelay' command.", file=sys.stderr)
         sys.exit(1)
    except Exception as e:
         print(f"Error running entry point from main.py: {e}", file=sys.stderr)
         sys.exit(1)