import logging
from logging.handlers import RotatingFileHandler

from rich.console import Console
from rich.logging import RichHandler

# Import parse_arguments only when needed to avoid conflicts with pytest
from mmrelay.config import get_log_dir
from mmrelay.constants.app import APP_DISPLAY_NAME
from mmrelay.constants.messages import (
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_SIZE_MB,
    LOG_SIZE_BYTES_MULTIPLIER,
)

# Initialize Rich console
console = Console()

# Define custom log level styles - not used directly but kept for reference
# Rich 14.0.0+ supports level_styles parameter, but we're using an approach
# that works with older versions too
LOG_LEVEL_STYLES = {
    "DEBUG": "dim blue",
    "INFO": "green",
    "WARNING": "yellow",
    "ERROR": "bold red",
    "CRITICAL": "bold white on red",
}

# Global config variable that will be set from main.py
config = None

# Global variable to store the log file path
log_file_path = None

# Track if component debug logging has been configured
_component_debug_configured = False

# Component logger mapping for data-driven configuration
_COMPONENT_LOGGERS = {
    "matrix_nio": [
        "nio",
        "nio.client",
        "nio.http",
        "nio.crypto",
        "nio.responses",
        "nio.rooms",
    ],
    "bleak": ["bleak", "bleak.backends"],
    "meshtastic": [
        "meshtastic",
        "meshtastic.serial_interface",
        "meshtastic.tcp_interface",
        "meshtastic.ble_interface",
    ],
}


def configure_component_debug_logging():
    """
    Enables debug-level logging for selected external components based on configuration settings.

    This function sets the log level to DEBUG for specific libraries (matrix_nio, bleak, meshtastic) if enabled in the global configuration under `logging.debug`. It ensures that component debug logging is configured only once per application run.
    """
    global _component_debug_configured, config

    # Only configure once
    if _component_debug_configured or config is None:
        return

    debug_config = config.get("logging", {}).get("debug", {})

    for component, loggers in _COMPONENT_LOGGERS.items():
        if debug_config.get(component, False):
            for logger_name in loggers:
                logging.getLogger(logger_name).setLevel(logging.DEBUG)
        else:
            # Suppress external library messages when debug is disabled
            # Use ERROR level to minimize noise while allowing mmrelay debug logging
            for logger_name in loggers:
                logging.getLogger(logger_name).setLevel(logging.ERROR)

    _component_debug_configured = True


def get_logger(name):
    """
    Create and configure a logger with console output (optionally colorized) and optional rotating file logging.

    The logger's log level, colorization, and file logging behavior are determined by global configuration and command-line arguments. Log files are rotated by size, and the log directory is created if necessary. If the logger name matches the application display name, the log file path is stored globally for reference.

    Parameters:
        name (str): The name of the logger to create.

    Returns:
        logging.Logger: The configured logger instance.
    """
    logger = logging.getLogger(name=name)

    # Default to INFO level if config is not available
    log_level = logging.INFO
    color_enabled = True  # Default to using colors

    # Try to get log level and color settings from config
    global config
    if config is not None and "logging" in config:
        if "level" in config["logging"]:
            try:
                log_level = getattr(logging, config["logging"]["level"].upper())
            except AttributeError:
                # Invalid log level, fall back to default
                log_level = logging.INFO
        # Check if colors should be disabled
        if "color_enabled" in config["logging"]:
            color_enabled = config["logging"]["color_enabled"]

    logger.setLevel(log_level)
    logger.propagate = False

    # Check if logger already has handlers to avoid duplicates
    if logger.handlers:
        return logger

    # Add handler for console logging (with or without colors)
    if color_enabled:
        # Use Rich handler with colors
        console_handler = RichHandler(
            rich_tracebacks=True,
            console=console,
            show_time=True,
            show_level=True,
            show_path=False,
            markup=True,
            log_time_format="%Y-%m-%d %H:%M:%S",
            omit_repeated_times=False,
        )
        console_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    else:
        # Use standard handler without colors
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s:%(name)s:%(message)s",
                datefmt="%Y-%m-%d %H:%M:%S %z",
            )
        )
    logger.addHandler(console_handler)

    # Check command line arguments for log file path (only if not in test environment)
    args = None
    try:
        # Only parse arguments if we're not in a test environment
        import os

        if not os.environ.get("MMRELAY_TESTING"):
            from mmrelay.cli import parse_arguments

            args = parse_arguments()
    except (SystemExit, ImportError):
        # If argument parsing fails (e.g., in tests), continue without CLI arguments
        pass

    # Check if file logging is enabled (default to True for better user experience)
    if (
        config is not None
        and config.get("logging", {}).get("log_to_file", True)
        or (args and args.logfile)
    ):
        # Priority: 1. Command line argument, 2. Config file, 3. Default location (~/.mmrelay/logs)
        if args and args.logfile:
            log_file = args.logfile
        else:
            config_log_file = (
                config.get("logging", {}).get("filename")
                if config is not None
                else None
            )

            if config_log_file:
                # Use the log file specified in config
                log_file = config_log_file
            else:
                # Default to standard log directory
                log_file = os.path.join(get_log_dir(), "mmrelay.log")

        # Create log directory if it doesn't exist
        log_dir = os.path.dirname(log_file)
        if log_dir:  # Ensure non-empty directory paths exist
            os.makedirs(log_dir, exist_ok=True)

        # Store the log file path for later use
        if name == APP_DISPLAY_NAME:
            global log_file_path
            log_file_path = log_file

        # Create a file handler for logging
        try:
            # Set up size-based log rotation
            max_bytes = DEFAULT_LOG_SIZE_MB * LOG_SIZE_BYTES_MULTIPLIER
            backup_count = DEFAULT_LOG_BACKUP_COUNT

            if config is not None and "logging" in config:
                max_bytes = config["logging"].get("max_log_size", max_bytes)
                backup_count = config["logging"].get("backup_count", backup_count)
            file_handler = RotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
        except Exception as e:
            print(f"Error creating log file at {log_file}: {e}")
            return logger  # Return logger without file handler

        file_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s:%(name)s:%(message)s",
                datefmt="%Y-%m-%d %H:%M:%S %z",
            )
        )
        logger.addHandler(file_handler)

    return logger
