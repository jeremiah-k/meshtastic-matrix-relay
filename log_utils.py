import logging
from config import relay_config


def get_logger(name):
    logger = logging.getLogger(name)
    if not logger.handlers:
        log_level = getattr(logging, relay_config["logging"]["level"].upper(), logging.INFO)
        logger.setLevel(log_level)
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s:%(name)s:%(message)s",
            datefmt="%Y-%m-%d %H:%M:%S %z",
        )
        # Stream Handler
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        # File Handler
        if relay_config["logging"].get("file"):
            file_handler = logging.FileHandler(relay_config["logging"]["file"])
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
    return logger
