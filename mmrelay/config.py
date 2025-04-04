# ./mmrelay/config.py:
import os
import sys
import pathlib
import yaml
from yaml.loader import SafeLoader

# Import the path utility to find the default config path
from mmrelay.path_utils import get_config_path

# Global variable to hold the loaded configuration
relay_config = {}


# --- Deprecated get_app_path ---
def get_app_path():
    """
    DEPRECATED: Use path_utils for config/data paths.
    Returns the base directory of the application code.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    else:
        # This will point to the directory containing this file within the package
        return os.path.dirname(os.path.abspath(__file__))


def load_config(override_path_str: str = None) -> dict:
    """
    Loads configuration from YAML file.

    Priority for finding config.yaml:
    1. `override_path_str` if provided and valid.
    2. Current Working Directory (`./config.yaml`).
    3. User config directory (`~/.config/mmrelay/config.yaml` or equivalent).
    4. Legacy directory (`~/meshtastic-matrix-relay/config.yaml`).

    Args:
        override_path_str: A string path provided via command line to override default lookup.

    Returns:
        A dictionary containing the loaded configuration, or a default structure if loading fails.
    """
    global relay_config # We will update the global variable

    config_to_load = None
    loaded_path_info = "Defaults"

    # 1. Check override path
    if override_path_str:
        override_path = pathlib.Path(override_path_str).resolve()
        if override_path.is_file():
            config_to_load = override_path
            loaded_path_info = f"Command line override: {override_path}"
        else:
            print(f"Warning: Config override path specified but not found: {override_path}", file=sys.stderr)
            # Fall through to default lookup if override path is invalid

    # 2. If override not used or invalid, use default lookup logic
    if config_to_load is None:
        default_path = get_config_path() # This handles CWD > User > Legacy
        if default_path.is_file():
            config_to_load = default_path
            loaded_path_info = f"Auto-detected: {default_path}"
        else:
            # Default path (user config dir) doesn't exist either
             loaded_path_info = f"Not found (checked CWD, {default_path.parent}, legacy dir)"
             print(f"Configuration file not found. Looked for: {default_path.resolve()}", file=sys.stderr)
             print(f"You can place it at {default_path.resolve()} or in the current directory.", file=sys.stderr)


    loaded_config = {}
    if config_to_load:
        print(f"Loading configuration from: {loaded_path_info}")
        try:
            with open(config_to_load, "r") as f:
                loaded_config = yaml.load(f, Loader=SafeLoader)
                if loaded_config is None: # Handle empty YAML file case
                    loaded_config = {}
        except Exception as e:
            print(f"Error loading configuration file {config_to_load}: {e}", file=sys.stderr)
            print("Continuing with default configuration structure.", file=sys.stderr)
            # Decide if you want to exit or continue
            # sys.exit(1)
    else:
         print("No configuration file found. Using default configuration structure.", file=sys.stderr)


    # Ensure basic structure exists even if file load failed or was empty
    if "logging" not in loaded_config:
        loaded_config["logging"] = {}
    if "level" not in loaded_config["logging"]:
        loaded_config["logging"]["level"] = "INFO"
    if "log_to_file" not in loaded_config["logging"]:
        loaded_config["logging"]["log_to_file"] = False
    if "matrix_rooms" not in loaded_config:
         loaded_config["matrix_rooms"] = []
    if "meshtastic" not in loaded_config:
         loaded_config["meshtastic"] = {}
    if "matrix" not in loaded_config:
         loaded_config["matrix"] = {}
    # Add other essential top-level keys if needed

    # Update the global variable
    relay_config.clear()
    relay_config.update(loaded_config)

    return relay_config # Return the loaded config as well

# Example of how other modules *could* access config if needed after loading:
# from mmrelay.config import relay_config
# value = relay_config.get("some_key", "default")
