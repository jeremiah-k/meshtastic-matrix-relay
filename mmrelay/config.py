import os
import sys
import pathlib  # Use pathlib

import yaml
from yaml.loader import SafeLoader

# Import the new path utility
from mmrelay.path_utils import get_config_path


# --- Deprecated ---
# Keep the original function for now in case something else relies on it unexpectedly,
# but mark it as deprecated. Finding the app path isn't the right way to find config.
def get_app_path():
    """
    DEPRECATED: Use path_utils for config/data paths.
    Returns the base directory of the application code.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


relay_config = {}

# Use the new function to find the config file path
# This handles CWD, user config dir, and legacy dir automatically.
config_path: pathlib.Path = get_config_path()

if not config_path.is_file():
    # Use os.path.abspath to show the full path in the error message
    print(f"Configuration file not found. Looked for: {config_path.resolve()}")
    # Optionally: Guide user on where to create it
    print(f"You can place it at {config_path.resolve()} or in the current directory.")
    # Decide if you want to exit or continue with empty config
    # sys.exit(1) # Or handle default config loading here
else:
    print(f"Loading configuration from: {config_path.resolve()}") # Inform user which config is used
    try:
        with open(config_path, "r") as f:
            relay_config = yaml.load(f, Loader=SafeLoader)
            if relay_config is None: # Handle empty YAML file case
                relay_config = {}
    except Exception as e:
        print(f"Error loading configuration file {config_path.resolve()}: {e}")
        # Decide if you want to exit or continue
        # sys.exit(1)

# Provide default logging structure if not present in config
if "logging" not in relay_config:
    relay_config["logging"] = {}
if "level" not in relay_config["logging"]:
    relay_config["logging"]["level"] = "INFO" # Default level
if "log_to_file" not in relay_config["logging"]:
    relay_config["logging"]["log_to_file"] = False
