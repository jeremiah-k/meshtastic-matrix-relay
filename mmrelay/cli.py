# ./mmrelay/cli.py:
"""
Command-Line Interface handling for Matrix <> Meshtastic Relay.
Uses argparse to process arguments and coordinates the setup and execution flow.
"""

import argparse
import asyncio
import sys

# Import necessary components from the package
from mmrelay import __version__, __author__
from mmrelay.config import load_config
from mmrelay.log_utils import setup_logging, get_logger

# Import the main async function (will be called after setup)
# It's okay to import main here, as cli.py calls it, not the other way around.
try:
    from mmrelay.main import main as main_async_runner
except ImportError as e:
    print(f"Critical Error: Could not import main async function: {e}", file=sys.stderr)
    sys.exit(1)

# Placeholder logger until setup_logging is called
logger = get_logger(__name__) # Get logger instance, handlers configured later


def parse_arguments():
    """Parses command-line arguments using argparse."""
    parser = argparse.ArgumentParser(
        prog="mmrelay", # Explicitly set prog name
        description=f"Matrix <> Meshtastic Relay v{__version__} by {__author__}",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        metavar="PATH",
        help="Path to the configuration file (config.yaml). Overrides default search paths.",
        default=None, # Default is None, load_config handles the logic
    )
    parser.add_argument(
        "--logfile", "-l",
        type=str,
        metavar="PATH",
        help="Path to the log file. Overrides path derived from config or defaults.",
        default=None, # Default is None, setup_logging handles the logic
    )
    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show program's version number and exit.",
    )
    return parser.parse_args()


def entry_point():
    """
    The main entry point called by the console script and __main__.py.
    Handles argument parsing, config loading, logging setup, and running the async main function.
    """
    args = parse_arguments()

    # --- Configuration Loading ---
    # load_config populates the global 'relay_config' dict in config.py
    try:
        loaded_main_config = load_config(args.config)
    except Exception as e:
         print(f"Critical Error during configuration loading: {e}", file=sys.stderr)
         # Log to stderr before logging is fully configured
         sys.exit(1)

    # --- Logging Setup ---
    # setup_logging configures handlers based on the loaded config and overrides
    try:
        setup_logging(loaded_main_config, args.logfile)
    except Exception as e:
        # Log to stderr if logging setup itself fails
        print(f"Critical Error during logging setup: {e}", file=sys.stderr)
        sys.exit(1)

    # Now that logging is configured, use the logger instance
    logger.debug(f"Command line arguments: {vars(args)}")
    logger.debug(f"Effective configuration: {loaded_main_config}") # Log the loaded config

    # --- Execute Main Async Logic ---
    try:
        asyncio.run(main_async_runner()) # Call the main async function from main.py
    except KeyboardInterrupt:
        logger.info("Application terminated by user (KeyboardInterrupt).")
        sys.exit(0)
    except asyncio.CancelledError:
         logger.info("Main execution cancelled.")
         # Should ideally be handled gracefully within main_async_runner's finally block
    except Exception as e:
         # Catch unexpected errors during async execution
         logger.critical(f"Unhandled exception during relay execution: {e}", exc_info=True)
         sys.exit(1)
    finally:
        logger.info("MMRelay finished.")
