import logging
import os
from logging.handlers import RotatingFileHandler

from config import relay_config


def get_logger(name):
    logger = logging.getLogger(name=name)
    log_level = getattr(logging, relay_config["logging"]["level"].upper())
    logger.setLevel(log_level)
    logger.propagate = False

    # Add stream handler (console logging)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s:%(name)s:%(message)s",
            datefmt="%Y-%m-%d %H:%M:%S %z",
        )
    )
    logger.addHandler(stream_handler)

    # Check if file logging is enabled
    if relay_config["logging"].get("log_to_file", False):
        # Default to `logs/mmrelay.log` if no filename is provided
        log_file = relay_config["logging"].get("filename", "logs/mmrelay.log")

        # Only create directories if the path is not the default
        if log_file != "logs/mmrelay.log":
            log_dir = os.path.dirname(log_file)
            if log_dir:  # Ensure non-empty directory paths exist
                os.makedirs(log_dir, exist_ok=True)

        # Set up size-based log rotation
        max_bytes = relay_config["logging"].get(
            "max_log_size", 10 * 1024 * 1024
        )  # Default 10 MB
        backup_count = relay_config["logging"].get(
            "backup_count", 1
        )  # Default to 1 backup
        file_handler = RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )

        file_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s:%(name)s:%(message)s",
                datefmt="%Y-%m-%d %H:%M:%S %z",
            )
        )
        logger.addHandler(file_handler)

    return logger
