# ./mmrelay/log_utils.py:
import logging
import os
import sys
import pathlib
from logging.handlers import RotatingFileHandler

# Import path utils for default log directory
from mmrelay.path_utils import get_log_dir

# Keep relay_config import for easy access within setup_logging if needed later,
# but primarily pass config dictionary as argument.
from mmrelay.config import relay_config


# Flag to prevent setup_logging from running multiple times
_logging_configured = False


def get_logger(name: str) -> logging.Logger:
    """Gets a logger instance. Does not configure handlers."""
    return logging.getLogger(name)


def setup_logging(config: dict, override_logfile_path: str = None):
    """
    Configures logging handlers (Stream + Optional File) based on the loaded config
    and potential command-line override for the file path.

    Args:
        config: The loaded configuration dictionary.
        override_logfile_path: Full path to the log file from command line, if provided.
    """
    global _logging_configured
    if _logging_configured:
        # Avoid re-configuring handlers if called multiple times
        # print("DEBUG: Logging already configured, skipping setup.", file=sys.stderr) # Optional debug print
        return

    log_config = config.get("logging", {})
    log_level_name = log_config.get("level", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    # Get the root logger to configure handlers centrally
    root_logger = logging.getLogger() # Configure root logger
    # Ensure root logger level is set appropriately (e.g., lowest level used by handlers)
    root_logger.setLevel(min(log_level, logging.DEBUG)) # Set root level low enough

    # Clear existing handlers from root to avoid duplication if setup is called again somehow
    # for handler in root_logger.handlers[:]:
    #      root_logger.removeHandler(handler)

    # Common formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s:%(name)s:%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S %z",
    )

    # --- Stream Handler (Console) ---
    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        stream_handler = logging.StreamHandler(sys.stdout) # Explicitly use stdout
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(log_level) # Set level on handler
        root_logger.addHandler(stream_handler)
        # print(f"DEBUG: Added StreamHandler with level {log_level_name}", file=sys.stderr) # Optional debug print

    # --- File Handler ---
    final_log_path = None
    if log_config.get("log_to_file", False):
        if override_logfile_path:
            # Use the command-line override path
            final_log_path = pathlib.Path(override_logfile_path).resolve()
            # Ensure the directory exists for the override path
            try:
                final_log_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                 print(f"Error: Could not create directory for specified log file {final_log_path.parent}: {e}", file=sys.stderr)
                 final_log_path = None # Disable file logging if dir creation fails
        else:
            # Use the default path logic
            try:
                log_dir: pathlib.Path = get_log_dir() # Handles user/legacy and creates dir
                default_log_filename = "mmrelay.log"
                log_file_name = log_config.get("filename", default_log_filename)
                final_log_path = log_dir / log_file_name
            except Exception as e:
                 print(f"Error determining default log directory: {e}", file=sys.stderr)
                 # final_log_path remains None

        if final_log_path and not any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers):
            try:
                max_bytes = log_config.get("max_log_size", 10 * 1024 * 1024) # Default 10 MB
                backup_count = log_config.get("backup_count", 1) # Default 1 backup

                file_handler = RotatingFileHandler(
                    final_log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
                )
                file_handler.setFormatter(formatter)
                file_handler.setLevel(log_level) # Set level on handler
                root_logger.addHandler(file_handler)
                print(f"Logging to file: {final_log_path}") # Use print, logger might not be ready
                # print(f"DEBUG: Added RotatingFileHandler with level {log_level_name}", file=sys.stderr) # Optional debug print

            except Exception as e:
                print(f"Error setting up file logging at {final_log_path}: {e}", file=sys.stderr)

    _logging_configured = True # Mark logging as configured
