import argparse
import contextlib
import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Any, Dict, Iterator, Optional, Set

if TYPE_CHECKING:
    from rich.console import Console

# Import logging configuration helpers and constants.
from mmrelay.constants.app import APP_DISPLAY_NAME, LOG_FILENAME
from mmrelay.constants.formats import DATETIME_FORMAT_WITH_TZ, RICH_LOG_TIME_FORMAT
from mmrelay.constants.messages import (
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_SIZE_MB,
    LOG_SIZE_BYTES_MULTIPLIER,
)

# Import Rich components only when not running as a service
RichHandler: Optional[Any] = None
try:
    from mmrelay.runtime_utils import is_running_as_service

    if not is_running_as_service():
        import rich.logging as _rich_logging
        from rich.console import Console

        RichHandler = _rich_logging.RichHandler

        RICH_AVAILABLE = True
    else:
        RICH_AVAILABLE = False
except ImportError:
    RICH_AVAILABLE = False

# Initialize Rich console only if available
if RICH_AVAILABLE:
    console: Console | None = (
        Console()
    )  # pyright: ignore[reportPossiblyUnboundVariable]
else:
    console = None  # pyright: ignore[reportAssignmentType]

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

# Global variable to store the active log file path. This is populated only
# after a file handler has been opened successfully.
log_file_path: str | None = None

# All MMRelay loggers share one rotating file handler. Separate
# RotatingFileHandler instances pointed at the same path can rotate underneath
# one another, leaving different loggers writing to different generations.
_shared_file_handler: RotatingFileHandler | None = None
_shared_file_handler_key: tuple[str, int, int] | None = None
_shared_file_handler_lock = threading.RLock()

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

# Component loggers are owned by third-party libraries, so only detach handlers
# that this module previously attached. This preserves handlers installed by an
# embedding application while allowing refreshes to replace MMRelay's console
# and shared-file handlers exactly once.
_component_attached_handlers: Dict[str, Set[logging.Handler]] = {}

# Avoid file logging for loggers that are used during path resolution to
# prevent recursive logging configuration (paths -> log_utils -> paths).
# Include both the short name and fully-qualified name for robustness.
_FILE_LOGGING_EXEMPT_LOGGERS = {"paths", "mmrelay.paths"}


def configure_component_debug_logging() -> None:
    """
    Apply per-component debug logging settings from the global configuration.

    Read config["logging"]["debug"] as a mapping of component names to settings and apply them to the component loggers listed in _COMPONENT_LOGGERS. For a component: a boolean or a valid logging level name enables logging at that level (boolean implies DEBUG; invalid level names fall back to DEBUG); a falsy or missing value sets the component loggers to level CRITICAL+1 to suppress their output. If the global `config` is None, no changes are made.
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

        for logger_name in loggers:
            component_logger = logging.getLogger(logger_name)
            previous_handlers = _component_attached_handlers.pop(logger_name, set())
            for handler in previous_handlers:
                if handler in component_logger.handlers:
                    component_logger.removeHandler(handler)

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
                    component_logger.addHandler(handler)
                _component_attached_handlers[logger_name] = set(main_handlers)
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
    # Also default off when config is not yet available to avoid early cycles.
    if config is None:
        default_enabled = False
    else:
        default_enabled = False if _cli_mode else True
    enabled = logging_config.get("log_to_file", default_enabled)

    # Command-line argument always wins and forces file logging on
    logfile = getattr(args, "logfile", None) if args is not None else None
    if logfile:
        enabled = True

    return bool(enabled)


def _expand_log_path(path: str) -> str:
    """Expand environment/user markers and return an absolute log path."""
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def _resolve_log_file(args: argparse.Namespace | None) -> str:
    """Resolve the active log path.

    Precedence is explicit ``--logfile``, ``MMRELAY_LOG_PATH``, configured
    ``logging.filename`` (including the ``MMRELAY_LOG_FILE`` config override),
    then the default logs directory. All selected paths expand ``~`` and
    environment-variable markers before use.
    """
    logfile = getattr(args, "logfile", None) if args is not None else None
    if isinstance(logfile, str) and logfile:
        return _expand_log_path(logfile)

    env_log_file = os.getenv("MMRELAY_LOG_PATH")
    if env_log_file:
        return _expand_log_path(env_log_file)

    config_log_file = config.get("logging", {}).get("filename") if config else None
    if isinstance(config_log_file, str) and config_log_file:
        return _expand_log_path(config_log_file)

    return _expand_log_path(os.path.join(get_log_dir(), LOG_FILENAME))


class LogsDirTypeError(TypeError):
    """logs_dir must be a string."""


def get_log_dir() -> str:
    """
    Retrieve the filesystem directory used for application logs.

    This resolves paths lazily via mmrelay.paths.resolve_all_paths.

    Returns:
        str: The path to the logs directory.

    Raises:
        LogsDirTypeError: If the resolved `logs_dir` value is not a string.
    """
    from mmrelay.paths import resolve_all_paths

    result = resolve_all_paths()["logs_dir"]
    if not isinstance(result, str):
        raise LogsDirTypeError()
    return result


def _detach_handler_from_all_loggers(handler: logging.Handler) -> None:
    """Remove a shared handler from every live logger before closing it."""
    loggers: list[logging.Logger] = [logging.getLogger()]
    for value in logging.Logger.manager.loggerDict.values():
        if isinstance(value, logging.Logger):
            loggers.append(value)
    for logger in loggers:
        if handler in logger.handlers:
            logger.removeHandler(handler)


def _close_shared_file_handler() -> None:
    """Detach and close the process-wide rotating file handler, if present."""
    global _shared_file_handler, _shared_file_handler_key, log_file_path

    with _shared_file_handler_lock:
        handler = _shared_file_handler
        _shared_file_handler = None
        _shared_file_handler_key = None
        log_file_path = None
        if handler is None:
            return
        _detach_handler_from_all_loggers(handler)
        with contextlib.suppress(OSError, ValueError):
            handler.close()


def _get_shared_file_handler(
    log_file: str, *, max_bytes: int, backup_count: int
) -> RotatingFileHandler:
    """Return the single rotating handler used by all MMRelay loggers."""
    global _shared_file_handler, _shared_file_handler_key

    normalized_path = _expand_log_path(log_file)
    key = (normalized_path, int(max_bytes), int(backup_count))
    with _shared_file_handler_lock:
        if _shared_file_handler is not None and _shared_file_handler_key == key:
            return _shared_file_handler

        if _shared_file_handler is not None:
            old_handler = _shared_file_handler
            _shared_file_handler = None
            _shared_file_handler_key = None
            _detach_handler_from_all_loggers(old_handler)
            with contextlib.suppress(OSError, ValueError):
                old_handler.close()

        handler = RotatingFileHandler(
            normalized_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s:%(name)s:%(message)s",
                datefmt=DATETIME_FORMAT_WITH_TZ,
            )
        )
        _shared_file_handler = handler
        _shared_file_handler_key = key
        return handler


def _configure_logger(
    logger: logging.Logger, *, args: argparse.Namespace | None = None
) -> logging.Logger:
    """
    Configure a logger's level and attach console and optional rotating file handlers based on the application's configuration and optional CLI arguments.

    Parameters:
        args (argparse.Namespace | None): Optional CLI arguments that can force or override file logging and influence the resolved logfile path.

    Returns:
        logging.Logger: The configured logger instance.
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

    # Reset handlers so we can rebuild with the latest configuration. The shared
    # file handler may still be attached to other loggers, so only detach it here.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        if handler is _shared_file_handler:
            continue
        with contextlib.suppress(OSError, ValueError):
            handler.close()

    # Add handler for console logging (with or without colors), unless in CLI mode.
    if not _cli_mode:
        if color_enabled and RICH_AVAILABLE and RichHandler is not None:
            # Use Rich handler with colors
            console_handler: logging.Handler = (
                RichHandler(  # pyright: ignore[reportPossiblyUnboundVariable]
                    rich_tracebacks=rich_tracebacks_enabled,
                    console=console,  # pyright: ignore[reportArgumentType]
                    show_time=True,
                    show_level=True,
                    show_path=False,
                    markup=True,
                    log_time_format=RICH_LOG_TIME_FORMAT,
                    omit_repeated_times=False,
                )
            )
            console_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        else:
            # Use standard handler without colors
            console_handler: logging.Handler = logging.StreamHandler()  # type: ignore[no-redef]
            console_handler.setFormatter(
                logging.Formatter(
                    fmt="%(asctime)s %(levelname)s:%(name)s:%(message)s",
                    datefmt=DATETIME_FORMAT_WITH_TZ,
                )
            )
        logger.addHandler(console_handler)

    # Determine whether to attach a file handler
    file_logging_enabled = _should_log_to_file(effective_args)
    if logger.name in _FILE_LOGGING_EXEMPT_LOGGERS:
        file_logging_enabled = False

    if file_logging_enabled:
        log_file = _resolve_log_file(effective_args)

        # Create log directory if it doesn't exist
        log_dir = os.path.dirname(log_file)
        if log_dir:  # Ensure non-empty directory paths exist
            try:
                os.makedirs(log_dir, exist_ok=True)
            except OSError as e:
                if logger.name == APP_DISPLAY_NAME:
                    with _shared_file_handler_lock:
                        log_file_path = None
                # Use the logger itself to report the error if available, otherwise print
                error_msg = f"Error creating log directory {log_dir}: {e}"
                if logger and logger.handlers:
                    logger.exception(error_msg)
                else:
                    print(error_msg)
                return logger  # Return logger without file handler

        # Create or reuse the process-wide file handler. Only advertise the log
        # path after the handler is open successfully.
        try:
            max_bytes = DEFAULT_LOG_SIZE_MB * LOG_SIZE_BYTES_MULTIPLIER
            backup_count = DEFAULT_LOG_BACKUP_COUNT

            if config is not None and "logging" in config:
                max_bytes = config["logging"].get("max_log_size", max_bytes)
                backup_count = config["logging"].get("backup_count", backup_count)
            file_handler = _get_shared_file_handler(
                log_file, max_bytes=max_bytes, backup_count=backup_count
            )
        except OSError as e:
            if logger.name == APP_DISPLAY_NAME:
                with _shared_file_handler_lock:
                    log_file_path = None
            error_msg = f"Error creating log file at {log_file}: {e}"
            if logger and logger.handlers:
                logger.exception(error_msg)
            else:
                print(error_msg)
            return logger
        except Exception as e:
            if logger.name == APP_DISPLAY_NAME:
                with _shared_file_handler_lock:
                    log_file_path = None
            error_msg = f"Unexpected error creating log file at {log_file}: {e}"
            if logger and logger.handlers:
                logger.exception(error_msg)
            else:
                print(error_msg)
            return logger

        logger.addHandler(file_handler)
        if logger.name == APP_DISPLAY_NAME:
            with _shared_file_handler_lock:
                log_file_path = file_handler.baseFilename
    elif logger.name == APP_DISPLAY_NAME:
        with _shared_file_handler_lock:
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

    # Component loggers can share the main handler too. Detach it globally before
    # rebuilding so no logger retains a closed rotation file descriptor.
    _close_shared_file_handler()
    _config_generation += 1

    for logger_name in list(_registered_logger_names):
        _configure_logger(logging.getLogger(logger_name), args=args)

    # Component loggers are configured directly through logging.getLogger() and
    # are therefore not part of _registered_logger_names. Reapply their settings
    # so they receive the newly-created shared handler after the old one closes.
    configure_component_debug_logging()
