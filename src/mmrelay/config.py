import os
import sys
import logging

import yaml
from yaml.loader import SafeLoader


def get_app_path():
    """
    Returns the base directory of the application, whether running from source or as an executable.
    """
    if getattr(sys, "frozen", False):
        # Running in a bundle (PyInstaller)
        return os.path.dirname(sys.executable)
    else:
        # Running in a normal Python environment
        return os.path.dirname(os.path.abspath(__file__))


# Global configuration dictionary
relay_config = {}


def load_config(config_path):
    """
    Load configuration from the specified path.

    Args:
        config_path: Path to the configuration file

    Returns:
        Loaded configuration dictionary
    """
    global relay_config

    if not os.path.isfile(config_path):
        logging.warning(f"Configuration file not found: {config_path}")
        relay_config = {}
        return relay_config

    try:
        with open(config_path, "r") as f:
            loaded_config = yaml.load(f, Loader=SafeLoader) or {}

        # Ensure the config has the expected structure
        if not isinstance(loaded_config, dict):
            logging.warning(f"Invalid configuration format in {config_path}")
            loaded_config = {}

        # Update the global relay_config
        relay_config.clear()
        relay_config.update(loaded_config)

        logging.info(f"Loaded configuration from {config_path}")
        return relay_config

    except Exception as e:
        logging.error(f"Error loading configuration: {e}")
        relay_config = {}
        return relay_config


# For backward compatibility, initialize with default path
# This will be overridden by cli.py calling load_config()
config_path = os.path.join(get_app_path(), "config.yaml")
if os.path.isfile(config_path):
    try:
        with open(config_path, "r") as f:
            relay_config = yaml.load(f, Loader=SafeLoader) or {}
    except Exception:
        pass
