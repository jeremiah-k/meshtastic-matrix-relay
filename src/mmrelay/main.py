"""
This script connects a Meshtastic mesh network to Matrix chat rooms by relaying messages between them.
It uses Meshtastic-python and Matrix nio client library to interface with the radio and the Matrix server respectively.
"""

import asyncio
import sys

# Import version from package
from mmrelay import __version__
from mmrelay.cli_utils import msg_suggest_check_config, msg_suggest_generate_config
from mmrelay.constants.app import APP_DISPLAY_NAME
from mmrelay.log_utils import get_logger

# Initialize logger
logger = get_logger(name=APP_DISPLAY_NAME)


# Flag to track if banner has been printed
_banner_printed = False


def print_banner():
    """
    Log the MMRelay startup banner with version information once.

    This records an informational message "Starting MMRelay version <version>" via the module logger
    the first time it is called and sets a module-level flag to prevent subsequent prints.
    """
    global _banner_printed
    # Only print the banner once
    if not _banner_printed:
        logger.info(f"Starting MMRelay version {__version__}")
        _banner_printed = True


async def main(config):
    """
    Coordinates the main asynchronous relay loop between Meshtastic and Matrix clients.

    Initializes the database, loads plugins, starts the message queue, and establishes connections to both Meshtastic and Matrix. Joins configured Matrix rooms, registers event callbacks for message and membership events, and periodically updates node names from the Meshtastic network. Monitors connection health, manages the Matrix sync loop with reconnection and shutdown handling, and ensures graceful shutdown of all components. Optionally wipes the message map on startup and shutdown if configured.
    """
    # Configure component debug logging now that config is available
    from mmrelay import log_utils

    log_utils.configure_component_debug_logging()

    # Import meshtastic_utils after component logging configuration to prevent timing issues

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
        logger.info("Interrupted by user. Exiting.")
        return 0
    except Exception:  # noqa: BLE001 — top-level guard to log and exit cleanly
        logger.exception("Error running main functionality")
        return 1


if __name__ == "__main__":
    import sys

    from mmrelay.cli import main

    sys.exit(main())
