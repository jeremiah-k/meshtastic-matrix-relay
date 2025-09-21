import logging
from logging.handlers import RotatingFileHandler

# Import Rich components only when not running as a service
try:
    from mmrelay.runtime_utils import is_running_as_service

    if not is_running_as_service():
        from rich.console import Console
        from rich.logging import RichHandler

        RICH_AVAILABLE = True
    else:
        RICH_AVAILABLE = False
except ImportError:
    RICH_AVAILABLE = False

# Import parse_arguments only when needed to avoid conflicts with pytest
from mmrelay.config import get_log_dir
from mmrelay.constants.app import APP_DISPLAY_NAME
from mmrelay.constants.messages import (
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_SIZE_MB,
    LOG_SIZE_BYTES_MULTIPLIER,
)

# Initialize Rich console only if available
console = Console() if RICH_AVAILABLE else None

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
    Configure log levels for external component loggers based on config["logging"]["debug"].

    Reads per-component entries under `config["logging"]["debug"]` and applies one of:
    - falsy or missing: silence the component by setting its loggers to CRITICAL+1
    - boolean True: enable DEBUG for the component's loggers
    - string: interpret as a logging level name (case-insensitive); invalid names fall back to DEBUG

    This function mutates the levels of loggers listed in _COMPONENT_LOGGERS and runs only once per process; no-op if called again or if global `config` is None.
    """
    global _component_debug_configured, config

    # Only configure once
    if _component_debug_configured or config is None:
        return

    debug_config = config.get("logging", {}).get("debug", {})

    for component, loggers in _COMPONENT_LOGGERS.items():
        component_config = debug_config.get(component)

        if component_config:
            # Component debug is enabled - check if it's a boolean or a log level
            if isinstance(component_config, bool):
                # Legacy boolean format - default to DEBUG
                log_level = logging.DEBUG
            elif isinstance(component_config, str):
                # String log level format (e.g., "warning", "error", "debug")
                try:
                    log_level = getattr(logging, component_config.upper())
                except AttributeError:
                    # Invalid log level, fall back to DEBUG
                    log_level = logging.DEBUG
            else:
                # Invalid config, fall back to DEBUG
                log_level = logging.DEBUG

            for logger_name in loggers:
                logging.getLogger(logger_name).setLevel(log_level)
        else:
            # Component debug is disabled - completely suppress external library logging
            # Use a level higher than CRITICAL to effectively disable all messages
            for logger_name in loggers:
                logging.getLogger(logger_name).setLevel(logging.CRITICAL + 1)

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
    if color_enabled and RICH_AVAILABLE:
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
