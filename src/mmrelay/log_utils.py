import logging
import os
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

# Cache for parsed CLI arguments
_cached_args = None

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
    Configure external component loggers according to config["logging"]["debug"].

    Reads the debug section of the global config and for each known component:
    - If enabled (True or a valid logging level string), sets the component loggers to the specified level and attaches the main application's handlers so their output appears alongside application logs.
    - If disabled (falsy or missing), sets the component loggers to a level higher than CRITICAL to suppress their output.

    This function only applies its configuration once and is not thread-safe. Call it after the main application logger is configured and before importing modules that produce component logs.
    """
    global _component_debug_configured, config

    # Only configure once
    if _component_debug_configured or config is None:
        return

    # Get the main application logger and its handlers to attach to component loggers
    main_logger = logging.getLogger(APP_DISPLAY_NAME)
    main_handlers = main_logger.handlers
    debug_settings = config.get("logging", {}).get("debug")

    # Ensure debug_config is a dictionary, handling malformed configs gracefully
    if isinstance(debug_settings, dict):
        debug_config = debug_settings
    else:
        if debug_settings is not None:
            main_logger.warning(
                "Debug logging section is not a dictionary. "
                "All component debug logging will be disabled. "
                "Check your config.yaml debug section formatting."
            )
        debug_config = {}

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

            # Configure all loggers for this component
            for logger_name in loggers:
                component_logger = logging.getLogger(logger_name)
                component_logger.setLevel(log_level)
                component_logger.propagate = False  # Prevent duplicate logging
                # Attach main handlers to the component logger
                for handler in main_handlers:
                    if handler not in component_logger.handlers:
                        component_logger.addHandler(handler)
        else:
            # Component debug is disabled - completely suppress external library logging
            # Use a level higher than CRITICAL to effectively disable all messages
            for logger_name in loggers:
                logging.getLogger(logger_name).setLevel(logging.CRITICAL + 1)

    _component_debug_configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Create and configure a logger with console output (colorized when available) and optional rotating file logging.

    Parameters:
        name (str): Name of the logger. If file logging is enabled and `name` equals APP_DISPLAY_NAME, the global `log_file_path` will be set to the chosen logfile path.

    Returns:
        logging.Logger: The configured logger instance.
    """
    logger = logging.getLogger(name=name)

    # Default to INFO level if config is not available
    log_level = logging.INFO
    color_enabled = True  # Default to using colors
    rich_tracebacks_enabled = False  # Default to disabling rich tracebacks

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
        if "rich_tracebacks" in config["logging"]:
            rich_tracebacks_enabled = bool(config["logging"]["rich_tracebacks"])

    logger.setLevel(log_level)
    logger.propagate = False

    # Check if logger already has handlers to avoid duplicates
    if logger.handlers:
        return logger

    # Add handler for console logging (with or without colors)
    if color_enabled and RICH_AVAILABLE:
        # Use Rich handler with colors
        console_handler = RichHandler(
            rich_tracebacks=rich_tracebacks_enabled,
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
    global _cached_args
    args = _cached_args

    if args is None:
        try:
            # Parse command-line arguments once and cache results; if parsing fails (e.g., in tests), continue without CLI arguments.
            # Note: This is a first-parse-wins cache - subsequent changes to sys.argv won't be picked up.
            from mmrelay.cli import parse_arguments

            args = parse_arguments()
            _cached_args = args
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
