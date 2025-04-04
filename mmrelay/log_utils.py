import logging
import os
from logging.handlers import RotatingFileHandler
import pathlib # Use pathlib

from mmrelay.config import relay_config
# Import the new path utility
from mmrelay.path_utils import get_log_dir


def get_logger(name):
    logger = logging.getLogger(name=name)
    # Ensure logger level is set only once
    if logger.hasHandlers():
         # Logger already configured, just return it
         return logger

    log_level_name = relay_config.get("logging", {}).get("level", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    logger.setLevel(log_level)
    logger.propagate = False # Prevent duplicating logs to root logger

    # Common formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s:%(name)s:%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S %z",
    )

    # Add stream handler (console logging) if not already added
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    # Check if file logging is enabled
    if relay_config.get("logging", {}).get("log_to_file", False):
        # Get the log directory using path_utils (ensures it exists)
        log_dir: pathlib.Path = get_log_dir()
        # Default filename within the log directory
        default_log_filename = "mmrelay.log"
        log_file_name = relay_config.get("logging", {}).get("filename", default_log_filename)

        # Construct the full path to the log file
        log_file_path = log_dir / log_file_name

        # Set up size-based log rotation
        max_bytes = relay_config.get("logging", {}).get(
            "max_log_size", 10 * 1024 * 1024 # Default 10 MB
        )
        backup_count = relay_config.get("logging", {}).get(
            "backup_count", 1 # Default to 1 backup
        )

        # Add file handler if not already added
        if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
            try:
                file_handler = RotatingFileHandler(
                    log_file_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
                )
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)
                # Log the path only once on successful setup
                # Use a flag or check handlers to prevent repeated logging if get_logger is called multiple times
                if not getattr(logger, '_has_logged_file_path', False):
                     logger.info(f"Logging to file: {log_file_path}")
                     logger._has_logged_file_path = True # Set flag

            except Exception as e:
                 # Log error to console if file logging setup fails
                 logger.error(f"Failed to set up file logging at {log_file_path}: {e}", exc_info=True)


    return logger
