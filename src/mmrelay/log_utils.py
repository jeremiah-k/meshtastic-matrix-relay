import argparse
import contextlib
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Any, Dict, Iterator, Set

if TYPE_CHECKING:
    from rich.console import Console
    from rich.logging import RichHandler

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
if RICH_AVAILABLE:
    console: Console | None = Console()
else:
    console = None

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

# Track loggers configured through this module so we can reconfigure them when
# configuration changes later in the startup sequence.
_registered_logger_names: Set[str] = set()

# Keep a generation counter so we know when to refresh handlers on existing loggers
_config_generation = 0
_logger_config_generations: Dict[str, int] = {}

# Toggle for CLI-only commands to avoid polluting user-facing output.
_cli_mode = False

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


def configure_component_debug_logging() -> None:
    """
    Apply per-component debug logging settings from the global configuration.

    Reads config["logging"]["debug"] (expected as a dict) and for each known component enables or suppresses its loggers: a boolean value enables DEBUG, a valid logging level name (string) sets that level, and a falsy or missing value suppresses the component by setting its loggers above CRITICAL. No-op if the global `config` is None.
    """
    global config

    # Only configure when config is available
    if config is None:
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


def _should_log_to_file(args: argparse.Namespace | None) -> bool:
    """
    Determine if file logging should be enabled based on configuration and CLI options.

    When the module is in CLI mode, file logging defaults to off unless explicitly enabled
    in the logging configuration or a logfile is provided on the command line.

    Parameters:
        args (argparse.Namespace | None): Parsed CLI arguments. If present and `args.logfile`
            is truthy, file logging is forced on.

    Returns:
        bool: `True` if file logging should be enabled, `False` otherwise.
    """
    logging_config: dict[str, Any] = config.get("logging", {}) if config else {}

    # Default off in CLI mode so we only log to file when explicitly enabled.
    default_enabled = False if _cli_mode else True
    enabled = logging_config.get("log_to_file", default_enabled)

    # Command-line argument always wins and forces file logging on
    logfile = getattr(args, "logfile", None) if args is not None else None
    if logfile:
        enabled = True

    return bool(enabled)


def _resolve_log_file(args: argparse.Namespace | None) -> str:
    """
    Determine the log file path, preferring a CLI-provided value, then the configuration, and falling back to the default log directory.

    Parameters:
        args: An argparse-like namespace or object; may be None. If present and has a truthy `logfile` attribute, that value is used.

    Returns:
        str: Filesystem path to the log file chosen according to the precedence: `args.logfile`, `config["logging"]["filename"]`, or the default "<log_dir>/mmrelay.log".
    """
    logfile = getattr(args, "logfile", None) if args is not None else None
    if isinstance(logfile, str) and logfile:
        return logfile

    config_log_file = config.get("logging", {}).get("filename") if config else None
    if isinstance(config_log_file, str) and config_log_file:
        return config_log_file

    return os.path.join(get_log_dir(), "mmrelay.log")


def _configure_logger(
    logger: logging.Logger, *, args: argparse.Namespace | None = None
) -> logging.Logger:
    """
    Configure and attach console and optional rotating file handlers to a logger based on application configuration and optional CLI arguments.

    Reapplies the current logging settings (level, formatters, handlers) when the configuration generation has changed, disables propagation on the logger, and updates the global `log_file_path` when the main application logger is configured for file logging.

    Parameters:
        args: Optional CLI arguments that can override or force file logging and influence logfile path resolution.

    Returns:
        The same `logging.Logger` instance after applying the configuration.
    """
    global log_file_path

    # Default to INFO level if config is not available
    log_level = logging.INFO
    color_enabled = True  # Default to using colors
    rich_tracebacks_enabled = False  # Default to disabling rich tracebacks

    # Try to get log level and color settings from config
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

    # Capture CLI args from callers (main passes them) to avoid tight coupling to the CLI module here
    effective_args = args

    needs_refresh = (
        not logger.handlers
        or _logger_config_generations.get(logger.name) != _config_generation
    )

    if not needs_refresh:
        return logger

    # Reset handlers so we can rebuild with the latest configuration
    for handler in list(logger.handlers):
        with contextlib.suppress(OSError, ValueError):
            handler.close()
    logger.handlers.clear()

    # Add handler for console logging (with or without colors), unless in CLI mode.
    if not _cli_mode:
        if color_enabled and RICH_AVAILABLE:
            # Use Rich handler with colors
            console_handler: logging.Handler = RichHandler(
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
            console_handler: logging.Handler = logging.StreamHandler()  # type: ignore[no-redef]
            console_handler.setFormatter(
                logging.Formatter(
                    fmt="%(asctime)s %(levelname)s:%(name)s:%(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S %z",
                )
            )
        logger.addHandler(console_handler)

    # Determine whether to attach a file handler
    if _should_log_to_file(effective_args):
        log_file = _resolve_log_file(effective_args)

        # Create log directory if it doesn't exist
        log_dir = os.path.dirname(log_file)
        if log_dir:  # Ensure non-empty directory paths exist
            try:
                os.makedirs(log_dir, exist_ok=True)
            except OSError as e:
                # Use the logger itself to report the error if available, otherwise print
                error_msg = f"Error creating log directory {log_dir}: {e}"
                if logger and logger.handlers:
                    logger.exception(error_msg)
                else:
                    print(error_msg)
                return logger  # Return logger without file handler

        # Store the log file path for later use
        if logger.name == APP_DISPLAY_NAME:
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
        except OSError as e:
            # Use the logger itself to report the error if available, otherwise print
            error_msg = f"Error creating log file at {log_file}: {e}"
            if logger and logger.handlers:
                logger.exception(error_msg)
            else:
                print(error_msg)
            return logger  # Return logger without file handler
        except Exception as e:
            # Catch any other unexpected exceptions
            error_msg = f"Unexpected error creating log file at {log_file}: {e}"
            if logger and logger.handlers:
                logger.exception(error_msg)
            else:
                print(error_msg)
            return logger  # Return logger without file handler

        file_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s:%(name)s:%(message)s",
                datefmt="%Y-%m-%d %H:%M:%S %z",
            )
        )
        logger.addHandler(file_handler)
    elif logger.name == APP_DISPLAY_NAME:
        log_file_path = None

    _logger_config_generations[logger.name] = _config_generation
    return logger


def get_logger(name: str, args: argparse.Namespace | None = None) -> logging.Logger:
    """
    Create or retrieve a named logger configured for console output and optional rotating file logging.

    Parameters:
        name (str): Logger name. If file logging is enabled and `name` equals APP_DISPLAY_NAME, the module-level `log_file_path` is set to the resolved logfile path.
        args (argparse.Namespace | None): Optional CLI arguments that may override logging behavior (e.g., `logfile`). If omitted, configuration values are used.

    Returns:
        logging.Logger: The configured logger instance.
    """
    logger = logging.getLogger(name=name)
    _registered_logger_names.add(name)

    return _configure_logger(logger, args=args)


@contextlib.contextmanager
def cli_logging_mode(args: argparse.Namespace | None = None) -> Iterator[None]:
    """
    Temporarily disable console logging while preserving file logging for CLI commands.

    Sets the internal CLI mode to True, refreshes all registered loggers so console handlers are removed,
    yields control to the caller, then restores the previous CLI mode and refreshes loggers again.

    Parameters:
        args (argparse.Namespace | None): Optional CLI argument namespace forwarded to logger refresh calls;
            used to determine file-logging behavior when loggers are reconfigured.
    """
    global _cli_mode
    previous_mode = _cli_mode
    _cli_mode = True
    refresh_all_loggers(args=args)
    try:
        yield
    finally:
        _cli_mode = previous_mode
        refresh_all_loggers(args=args)


def refresh_all_loggers(args: argparse.Namespace | None = None) -> None:
    """
    Reconfigure all registered loggers to apply the current logging configuration.

    Increments the internal configuration generation, then re-applies configuration to every logger created via get_logger(). Not thread-safe; intended for startup or controlled configuration reload paths.

    Parameters:
        args (argparse.Namespace | None): Optional CLI arguments that influence logging configuration (for example, a provided logfile). If None, global configuration is used.
    """
    global _config_generation

    _config_generation += 1

    for logger_name in list(_registered_logger_names):
        _configure_logger(logging.getLogger(logger_name), args=args)
