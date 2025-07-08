import logging
import os
from logging.handlers import RotatingFileHandler

from rich.console import Console
from rich.logging import RichHandler

from mmrelay.cli import parse_arguments
from mmrelay.config import get_log_dir

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


def get_logger(name):
    """Create and configure a logger with rich console and optional file output.
    
    Creates a logger instance with both console and file handlers, supporting
    colorized output through Rich library and log rotation. The logger behavior
    is controlled by global configuration, command line arguments, and sensible
    defaults.
    
    Configuration sources (in priority order):
    1. Command line arguments (--logfile, --log-level)
    2. Global config dictionary (logging section)
    3. Sensible defaults (INFO level, colors enabled)
    
    Features:
    - Rich-formatted console output with timestamps and colors
    - Optional file logging with rotation support
    - Configurable log levels and color enable/disable
    - Automatic log directory creation
    - UTF-8 encoding for international character support
    
    Args:
        name (str): Name of the logger to create. Special handling for
                   "M<>M Relay" which stores the log file path globally.
    
    Returns:
        logging.Logger: Configured logger instance ready for use with
                       appropriate handlers and formatting applied.
    
    Configuration Options:
        logging.level (str): Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        logging.color_enabled (bool): Enable/disable console color output
        logging.log_to_file (bool): Enable/disable file logging (default: True)
        logging.filename (str): Custom log file path
        logging.max_log_size (int): Maximum log file size in bytes (default: 10MB)
        logging.backup_count (int): Number of backup files to keep (default: 1)
    
    Global Variables:
        config (dict): Application configuration dictionary
        log_file_path (str): Set to the log file path when name is "M<>M Relay"
    
    Examples:
        >>> logger = get_logger("MyModule")
        >>> logger.info("This is an info message")
        
        >>> # Special main logger that stores file path
        >>> main_logger = get_logger("M<>M Relay")
        >>> print(f"Logging to: {log_file_path}")
    
    Note:
        This function automatically parses command line arguments on each call
        to check for --logfile argument. For performance-critical applications,
        consider caching the logger instance.
    """
    Create and configure a logger with console and optional file output, supporting colorized output and log rotation.

    The logger's level, color usage, and file logging behavior are determined by global configuration and command line arguments. Console output uses rich formatting if enabled. File logging supports log rotation and stores logs in a configurable or default location. The log file path is stored globally if the logger name is "M<>M Relay".

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
            log_level = getattr(logging, config["logging"]["level"].upper())
        # Check if colors should be disabled
        if "color_enabled" in config["logging"]:
            color_enabled = config["logging"]["color_enabled"]

    logger.setLevel(log_level)
    logger.propagate = False

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

    # Check command line arguments for log file path
    args = parse_arguments()

    # Check if file logging is enabled (default to True for better user experience)
    if (
        config is not None
        and config.get("logging", {}).get("log_to_file", True)
        or args.logfile
    ):
        # Priority: 1. Command line arg, 2. Config file, 3. Default location (~/.mmrelay/logs)
        if args.logfile:
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
        if name == "M<>M Relay":
            global log_file_path
            log_file_path = log_file

        # Create a file handler for logging
        try:
            # Set up size-based log rotation
            max_bytes = 10 * 1024 * 1024  # Default 10 MB
            backup_count = 1  # Default to 1 backup

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


def setup_upstream_logging_capture():
    """Set up centralized logging capture for upstream library messages.
    
    Configures a custom logging handler that captures log messages from external
    libraries and the root logger, routing them through the application047s
    formatted logging system. This ensures consistent log formatting across
    all components while preventing log message duplication.
    
    The capture system:
    1. Creates an "Upstream" logger with application formatting
    2. Installs a custom handler on the root logger
    3. Configures specific loggers for known upstream libraries
    4. Filters out application messages to prevent recursion
    5. Only captures WARNING level and above to reduce noise
    
    Captured Libraries:
    - meshtastic: Meshtastic Python library messages
    - bleak: Bluetooth Low Energy library messages  
    - asyncio: Python asyncio framework messages
    - Any other library using the root logger
    
    Message Format:
        Messages are prefixed with the original logger name in brackets:
        "[meshtastic] Connection timeout after 30 seconds"
    
    Log Level Mapping:
        - ERROR -> main_logger.error()
        - WARNING -> main_logger.warning() 
        - INFO -> main_logger.info()
        - DEBUG -> main_logger.debug()
    
    Example:
        >>> setup_upstream_logging_capture()
        >>> # Now all upstream library warnings/errors appear in app logs
        >>> # with consistent formatting and timestamps
    
    Note:
        This should be called once during application initialization,
        after the logging configuration has been set up but before
        initializing external libraries.
    """
    Redirects warning and error log messages from upstream libraries and the root logger into the application's formatted logging system.

    This ensures that log output from external dependencies (such as "meshtastic", "bleak", and "asyncio") appears with consistent formatting alongside the application's own logs. Only messages at WARNING level or higher are captured, and messages originating from the application's own loggers are excluded to prevent recursion.
    """
    # Get our main logger
    main_logger = get_logger("Upstream")

    # Create a custom handler that redirects root logger messages
    class UpstreamLogHandler(logging.Handler):
        def emit(self, record):
            """Process and redirect log records from external sources.
            
            Handles log records from upstream libraries by filtering out
            application messages and routing external messages through
            the main application logger with appropriate level mapping.
            
            Args:
                record (logging.LogRecord): Log record to process containing
                                          message, level, logger name, and metadata
            
            Processing Logic:
            1. Skip records from application loggers to prevent recursion
            2. Map external log levels to appropriate application logger methods
            3. Prefix messages with original logger name for identification
            4. Route through main logger for consistent formatting
            
            Filtering Rules:
                - Skip if record.name starts with "mmrelay"
                - Skip if record.name is "Upstream"
                - Process all other external library messages
            
            Example:
                Original: logging.getLogger("meshtastic").warning("Connection lost")
                Result: main_logger.warning("[meshtastic] Connection lost")
            """
            Redirects log records from external sources to the main logger, mapping their severity and prefixing with the original logger name.

            Skips records originating from the application's own loggers to prevent recursion.
            """
            if record.name.startswith("mmrelay") or record.name == "Upstream":
                return

            # Map the log level and emit through our logger
            if record.levelno >= logging.ERROR:
                main_logger.error(f"[{record.name}] {record.getMessage()}")
            elif record.levelno >= logging.WARNING:
                main_logger.warning(f"[{record.name}] {record.getMessage()}")
            elif record.levelno >= logging.INFO:
                main_logger.info(f"[{record.name}] {record.getMessage()}")
            else:
                main_logger.debug(f"[{record.name}] {record.getMessage()}")

    # Add our handler to the root logger
    root_logger = logging.getLogger()
    upstream_handler = UpstreamLogHandler()
    upstream_handler.setLevel(logging.WARNING)  # Only capture warnings and errors
    root_logger.addHandler(upstream_handler)

    # Also set up specific loggers for known upstream libraries
    for logger_name in ["meshtastic", "bleak", "asyncio"]:
        upstream_logger = logging.getLogger(logger_name)
        upstream_logger.addHandler(upstream_handler)
        upstream_logger.setLevel(logging.WARNING)
        upstream_logger.propagate = False  # Prevent duplicate messages via root logger
