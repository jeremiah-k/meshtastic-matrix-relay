import logging
import os
from logging.handlers import RotatingFileHandler

# Default formatter for all handlers
DEFAULT_FORMATTER = logging.Formatter(
    fmt="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S %z",
)


def setup_logging(config_dict, log_file=None):
    """
    Set up logging configuration based on the provided config.

    Args:
        config_dict: Configuration dictionary
        log_file: Optional override for log file path
    """
    # Configure root logger
    root_logger = logging.getLogger()

    # Clear existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Set log level from config
    log_level = getattr(
        logging,
        config_dict.get("logging", {}).get("level", "INFO").upper()
    )
    root_logger.setLevel(log_level)

    # Add console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(DEFAULT_FORMATTER)
    root_logger.addHandler(console_handler)

    # Add file handler if enabled
    if config_dict.get("logging", {}).get("log_to_file", False):
        # Use override if provided, otherwise use config
        if log_file is None:
            log_file = config_dict["logging"].get("filename", "logs/mmrelay.log")

        # Create log directory if needed
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        # Set up size-based log rotation
        max_bytes = config_dict["logging"].get(
            "max_log_size", 10 * 1024 * 1024
        )  # Default 10 MB
        backup_count = config_dict["logging"].get(
            "backup_count", 1
        )  # Default to 1 backup

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8"
        )
        file_handler.setFormatter(DEFAULT_FORMATTER)
        root_logger.addHandler(file_handler)

        logging.info(f"Logging to file: {log_file}")


def get_logger(name):
    """
    Get a logger with the specified name.

    Args:
        name: Logger name

    Returns:
        Logger instance
    """
    return logging.getLogger(name)
