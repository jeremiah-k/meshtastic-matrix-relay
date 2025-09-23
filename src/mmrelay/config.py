import json
import logging
import os
import re
import sys

import platformdirs
import yaml
from yaml.loader import SafeLoader

# Import application constants
from mmrelay.cli_utils import msg_suggest_check_config, msg_suggest_generate_config
from mmrelay.constants.app import APP_AUTHOR, APP_NAME
from mmrelay.constants.config import (
    CONFIG_KEY_ACCESS_TOKEN,
    CONFIG_KEY_BOT_USER_ID,
    CONFIG_KEY_HOMESERVER,
    CONFIG_SECTION_MATRIX,
)

# Global variable to store the custom data directory
custom_data_dir = None


def set_secure_file_permissions(file_path: str, mode: int = 0o600) -> None:
    """
    Set secure file permissions for a file on Unix-like systems.

    This attempts to chmod the given file to the provided mode (default 0o600 — owner read/write).
    No action is taken on non-Unix platforms (e.g., Windows). Failures to change permissions are
    caught and handled internally (the function does not raise).
    Parameters:
        file_path (str): Path to the file whose permissions should be tightened.
        mode (int): Unix permission bits to apply (default 0o600).
    """
    if sys.platform in ["linux", "darwin"]:
        try:
            os.chmod(file_path, mode)
            logger.debug(f"Set secure permissions ({oct(mode)}) on {file_path}")
        except (OSError, PermissionError) as e:
            logger.warning(f"Could not set secure permissions on {file_path}: {e}")


# Custom base directory for Unix systems
def get_base_dir():
    """Returns the base directory for all application files.

    If a custom data directory has been set via --data-dir, that will be used.
    Otherwise, defaults to ~/.mmrelay on Unix systems or the appropriate
    platformdirs location on Windows.
    """
    # If a custom data directory has been set, use that
    if custom_data_dir:
        return custom_data_dir

    if sys.platform in ["linux", "darwin"]:
        # Use ~/.mmrelay for Linux and Mac
        return os.path.expanduser(os.path.join("~", "." + APP_NAME))
    else:
        # Use platformdirs default for Windows
        return platformdirs.user_data_dir(APP_NAME, APP_AUTHOR)


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


def get_config_paths(args=None):
    """
    Return a prioritized list of possible configuration file paths for the application.

    The search order is: a command-line specified path (if provided), the user config directory, the current working directory, and the application directory. The user config directory is skipped if it cannot be created due to permission or OS errors.

    Parameters:
        args: Parsed command-line arguments, expected to have a 'config' attribute specifying a config file path.

    Returns:
        List of absolute paths to candidate configuration files, ordered by priority.
    """
    paths = []

    # Check command line arguments for config path
    if args and args.config:
        paths.append(os.path.abspath(args.config))

    # Check user config directory (preferred location)
    if sys.platform in ["linux", "darwin"]:
        # Use ~/.mmrelay/ for Linux and Mac
        user_config_dir = get_base_dir()
    else:
        # Use platformdirs default for Windows
        user_config_dir = platformdirs.user_config_dir(APP_NAME, APP_AUTHOR)

    try:
        os.makedirs(user_config_dir, exist_ok=True)
        user_config_path = os.path.join(user_config_dir, "config.yaml")
        paths.append(user_config_path)
    except (OSError, PermissionError):
        # If we can't create the user config directory, skip it
        pass

    # Check current directory (for backward compatibility)
    current_dir_config = os.path.join(os.getcwd(), "config.yaml")
    paths.append(current_dir_config)

    # Check application directory (for backward compatibility)
    app_dir_config = os.path.join(get_app_path(), "config.yaml")
    paths.append(app_dir_config)

    return paths


def get_data_dir():
    """
    Return the directory for application data, creating it if it does not exist.

    On Linux and macOS this is <base_dir>/data (where base_dir is returned by get_base_dir()).
    On Windows, if a global custom_data_dir is set it returns <custom_data_dir>/data; otherwise it falls back to platformdirs.user_data_dir(APP_NAME, APP_AUTHOR).

    Returns:
        str: Absolute path to the data directory.
    """
    if sys.platform in ["linux", "darwin"]:
        # Use ~/.mmrelay/data/ for Linux and Mac
        data_dir = os.path.join(get_base_dir(), "data")
    else:
        # Honor --data-dir on Windows too
        if custom_data_dir:
            data_dir = os.path.join(custom_data_dir, "data")
        else:
            # Use platformdirs default for Windows
            data_dir = platformdirs.user_data_dir(APP_NAME, APP_AUTHOR)

    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def get_plugin_data_dir(plugin_name=None):
    """
    Returns the directory for storing plugin-specific data files.
    If plugin_name is provided, returns a plugin-specific subdirectory.
    Creates the directory if it doesn't exist.

    Example:
    - get_plugin_data_dir() returns ~/.mmrelay/data/plugins/
    - get_plugin_data_dir("my_plugin") returns ~/.mmrelay/data/plugins/my_plugin/
    """
    # Get the base data directory
    base_data_dir = get_data_dir()

    # Create the plugins directory
    plugins_data_dir = os.path.join(base_data_dir, "plugins")
    os.makedirs(plugins_data_dir, exist_ok=True)

    # If a plugin name is provided, create and return a plugin-specific directory
    if plugin_name:
        plugin_data_dir = os.path.join(plugins_data_dir, plugin_name)
        os.makedirs(plugin_data_dir, exist_ok=True)
        return plugin_data_dir

    return plugins_data_dir


def get_log_dir():
    """
    Return the path to the application's log directory, creating it if missing.

    On Linux/macOS this is '<base_dir>/logs' (where base_dir is returned by get_base_dir()).
    On Windows, if a global custom_data_dir is set it uses '<custom_data_dir>/logs'; otherwise it uses the platform-specific user log directory from platformdirs.

    Returns:
        str: Absolute path to the log directory that now exists (created if necessary).
    """
    if sys.platform in ["linux", "darwin"]:
        # Use ~/.mmrelay/logs/ for Linux and Mac
        log_dir = os.path.join(get_base_dir(), "logs")
    else:
        # Honor --data-dir on Windows too
        if custom_data_dir:
            log_dir = os.path.join(custom_data_dir, "logs")
        else:
            # Use platformdirs default for Windows
            log_dir = platformdirs.user_log_dir(APP_NAME, APP_AUTHOR)

    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def get_e2ee_store_dir():
    """
    Return the absolute path to the application's end-to-end-encryption (E2EE) data store directory, creating it if missing.
    
    On Linux and macOS this is "<base_dir>/store" where base_dir is returned by get_base_dir().
    On Windows this is "<custom_data_dir>/store" when module-level custom_data_dir is set; otherwise it is
    platformdirs.user_data_dir(APP_NAME, APP_AUTHOR)/store.
    
    Returns:
        str: Absolute path to the ensured store directory.
    """
    if sys.platform in ["linux", "darwin"]:
        # Use ~/.mmrelay/store/ for Linux and Mac
        store_dir = os.path.join(get_base_dir(), "store")
    else:
        # Honor --data-dir on Windows too
        if custom_data_dir:
            store_dir = os.path.join(custom_data_dir, "store")
        else:
            # Use platformdirs default for Windows
            store_dir = os.path.join(
                platformdirs.user_data_dir(APP_NAME, APP_AUTHOR), "store"
            )

    os.makedirs(store_dir, exist_ok=True)
    return store_dir


def _convert_env_bool(value, var_name):
    """
    Convert a string from an environment variable into a boolean.

    Accepts (case-insensitive) true values: "true", "1", "yes", "on"; false values: "false", "0", "no", "off".
    If the value is not recognized, raises ValueError including var_name to indicate which environment variable was invalid.

    Parameters:
        value (str): The environment variable value to convert.
        var_name (str): Name of the environment variable (used in the error message).

    Returns:
        bool: The parsed boolean.

    Raises:
        ValueError: If the input is not a recognized boolean representation.
    """
    if value.lower() in ("true", "1", "yes", "on"):
        return True
    elif value.lower() in ("false", "0", "no", "off"):
        return False
    else:
        raise ValueError(
            f"Invalid boolean value for {var_name}: '{value}'. Use true/false, 1/0, yes/no, or on/off"
        )


def _convert_env_int(value, var_name, min_value=None, max_value=None):
    """
    Convert environment variable string to integer with optional range validation.

    Args:
        value (str): Environment variable value
        var_name (str): Variable name for error messages
        min_value (int, optional): Minimum allowed value
        max_value (int, optional): Maximum allowed value

    Returns:
        int: Converted integer value

    Raises:
        ValueError: If value cannot be converted or is out of range
    """
    try:
        int_value = int(value)
    except ValueError:
        raise ValueError(f"Invalid integer value for {var_name}: '{value}'") from None

    if min_value is not None and int_value < min_value:
        raise ValueError(f"{var_name} must be >= {min_value}, got {int_value}")
    if max_value is not None and int_value > max_value:
        raise ValueError(f"{var_name} must be <= {max_value}, got {int_value}")
    return int_value


def _convert_env_float(value, var_name, min_value=None, max_value=None):
    """
    Convert an environment variable string to a float and optionally validate its range.

    Parameters:
        value (str): The raw environment variable value to convert.
        var_name (str): Name of the variable (used in error messages).
        min_value (float, optional): Inclusive minimum allowed value.
        max_value (float, optional): Inclusive maximum allowed value.

    Returns:
        float: The parsed float value.

    Raises:
        ValueError: If the value cannot be parsed as a float or falls outside the specified range.
    """
    try:
        float_value = float(value)
    except ValueError:
        raise ValueError(f"Invalid float value for {var_name}: '{value}'") from None

    if min_value is not None and float_value < min_value:
        raise ValueError(f"{var_name} must be >= {min_value}, got {float_value}")
    if max_value is not None and float_value > max_value:
        raise ValueError(f"{var_name} must be <= {max_value}, got {float_value}")
    return float_value


def load_meshtastic_config_from_env():
    """
    Load Meshtastic-related configuration from environment variables.

    Reads known Meshtastic environment variables (as defined by the module's
    _MESHTASTIC_ENV_VAR_MAPPINGS), converts and validates their types, and
    returns a configuration dict containing any successfully parsed values.
    Returns None if no relevant environment variables are present or valid.
    """
    config = _load_config_from_env_mapping(_MESHTASTIC_ENV_VAR_MAPPINGS)
    if config:
        logger.debug(
            f"Loaded Meshtastic configuration from environment variables: {list(config.keys())}"
        )
    return config


def load_logging_config_from_env():
    """
    Load logging configuration from environment variables.

    Reads the logging-related environment variables defined by the module's mappings and returns a dict of parsed values. If a filename is present in the resulting mapping, adds "log_to_file": True to indicate file logging should be used.

    Returns:
        dict | None: Parsed logging configuration when any relevant environment variables are set; otherwise None.
    """
    config = _load_config_from_env_mapping(_LOGGING_ENV_VAR_MAPPINGS)
    if config:
        if config.get("filename"):
            config["log_to_file"] = True
        logger.debug(
            f"Loaded logging configuration from environment variables: {list(config.keys())}"
        )
    return config


def load_database_config_from_env():
    """
    Build a database configuration fragment from environment variables.

    Reads environment variables defined in the module-level _DATABASE_ENV_VAR_MAPPINGS and converts them into a configuration dictionary suitable for merging into the application's config. Returns None if no mapped environment variables were present.
    """
    config = _load_config_from_env_mapping(_DATABASE_ENV_VAR_MAPPINGS)
    if config:
        logger.debug(
            f"Loaded database configuration from environment variables: {list(config.keys())}"
        )
    return config


def is_e2ee_enabled(config):
    """
    Check if End-to-End Encryption (E2EE) is enabled in the configuration.

    Checks both 'encryption' and 'e2ee' keys in the matrix section for backward compatibility.
    On Windows, this always returns False since E2EE is not supported.

    Parameters:
        config (dict): Configuration dictionary to check.

    Returns:
        bool: True if E2EE is enabled, False otherwise.
    """
    # E2EE is not supported on Windows
    if sys.platform == "win32":
        return False

    if not config:
        return False

    matrix_cfg = config.get("matrix", {}) or {}
    if not matrix_cfg:
        return False

    encryption_enabled = matrix_cfg.get("encryption", {}).get("enabled", False)
    e2ee_enabled = matrix_cfg.get("e2ee", {}).get("enabled", False)

    return encryption_enabled or e2ee_enabled


def check_e2ee_enabled_silently(args=None):
    """
    Check silently whether End-to-End Encryption (E2EE) is enabled in the first readable configuration file.

    Searches candidate configuration paths returned by get_config_paths(args) in priority order, loads the first readable YAML file, and returns True if that configuration enables E2EE (via is_e2ee_enabled). I/O and YAML parsing errors are ignored and the function continues to the next candidate. On Windows this always returns False.

    Parameters:
        args (optional): Parsed command-line arguments that can influence config search order.

    Returns:
        bool: True if E2EE is enabled in the first valid configuration file found; otherwise False.
    """
    # E2EE is not supported on Windows
    if sys.platform == "win32":
        return False

    # Get config paths without logging
    config_paths = get_config_paths(args)

    # Try each config path silently
    for path in config_paths:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    config = yaml.load(f, Loader=SafeLoader)
                if config and is_e2ee_enabled(config):
                    return True
            except (yaml.YAMLError, PermissionError, OSError):
                continue  # Silently try the next path
    # No valid config found or E2EE not enabled in any config
    return False


def apply_env_config_overrides(config):
    """
    Apply environment-derived configuration overrides to a configuration dictionary.

    If `config` is falsy, a new dict is created. Environment variables are read and merged into
    the top-level keys "meshtastic", "logging", and "database" when corresponding environment
    fragments are present. Existing subkeys are updated with environment values while other keys
    in those sections are preserved. The input dict may be mutated in place.

    Parameters:
        config (dict | None): Base configuration to update.

    Returns:
        dict: The configuration dictionary with environment overrides applied (the same object
        passed in, or a newly created dict if a falsy value was provided).
    """
    if not config:
        config = {}

    # Apply Meshtastic configuration overrides
    meshtastic_env_config = load_meshtastic_config_from_env()
    if meshtastic_env_config:
        config.setdefault("meshtastic", {}).update(meshtastic_env_config)
        logger.debug("Applied Meshtastic environment variable overrides")

    # Apply logging configuration overrides
    logging_env_config = load_logging_config_from_env()
    if logging_env_config:
        config.setdefault("logging", {}).update(logging_env_config)
        logger.debug("Applied logging environment variable overrides")

    # Apply database configuration overrides
    database_env_config = load_database_config_from_env()
    if database_env_config:
        config.setdefault("database", {}).update(database_env_config)
        logger.debug("Applied database environment variable overrides")

    return config


def load_credentials():
    """
    Load Matrix credentials from the application's credentials.json file.

    Searches for "credentials.json" in the application's base configuration directory (get_base_dir()). If the file exists and contains valid JSON, returns the parsed credentials as a dict. On missing file, parse errors, or filesystem access errors, returns None.
    """
    try:
        config_dir = get_base_dir()
        credentials_path = os.path.join(config_dir, "credentials.json")

        logger.debug(f"Looking for credentials at: {credentials_path}")

        if os.path.exists(credentials_path):
            with open(credentials_path, "r", encoding="utf-8") as f:
                credentials = json.load(f)
            logger.debug(f"Successfully loaded credentials from {credentials_path}")
            return credentials
        else:
            logger.debug(f"No credentials file found at {credentials_path}")
            # On Windows, also log the directory contents for debugging
            if sys.platform == "win32" and os.path.exists(config_dir):
                try:
                    files = os.listdir(config_dir)
                    logger.debug(f"Directory contents of {config_dir}: {files}")
                except OSError:
                    pass
            return None
    except (OSError, PermissionError, json.JSONDecodeError):
        logger.exception(f"Error loading credentials.json from {config_dir}")
        return None


def save_credentials(credentials):
    """
    Persist a JSON-serializable credentials mapping to <base_dir>/credentials.json.

    Writes the provided credentials (a JSON-serializable mapping) to the application's
    base configuration directory as credentials.json, creating the base directory if
    necessary. On Unix-like systems the file permissions are adjusted to be
    restrictive (0o600) when possible. I/O and permission errors are caught and
    logged; the function does not raise them.

    Parameters:
        credentials (dict): JSON-serializable mapping of credentials to persist.

    Returns:
        None
    """
    try:
        config_dir = get_base_dir()
        # Ensure the directory exists and is writable
        os.makedirs(config_dir, exist_ok=True)
        credentials_path = os.path.join(config_dir, "credentials.json")

        # Log the path for debugging, especially on Windows
        logger.info(f"Saving credentials to: {credentials_path}")

        with open(credentials_path, "w", encoding="utf-8") as f:
            json.dump(credentials, f, indent=2)

        # Set secure permissions on Unix systems (600 - owner read/write only)
        set_secure_file_permissions(credentials_path)

        logger.info(f"Successfully saved credentials to {credentials_path}")

        # Verify the file was actually created
        if os.path.exists(credentials_path):
            logger.debug(f"Verified credentials.json exists at {credentials_path}")
        else:
            logger.error(f"Failed to create credentials.json at {credentials_path}")

    except (OSError, PermissionError):
        logger.exception(f"Error saving credentials.json to {config_dir}")
        # Try to provide helpful Windows-specific guidance
        if sys.platform == "win32":
            logger.error(
                "On Windows, ensure the application has write permissions to the user data directory"
            )
            logger.error(f"Attempted path: {config_dir}")


# Set up a basic logger for config
logger = logging.getLogger("Config")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s:%(name)s:%(message)s",
            datefmt="%Y-%m-%d %H:%M:%S %z",
        )
    )
    logger.addHandler(handler)
logger.propagate = False

# Initialize empty config
relay_config = {}
config_path = None

# Environment variable mappings for configuration sections
_MESHTASTIC_ENV_VAR_MAPPINGS = [
    {
        "env_var": "MMRELAY_MESHTASTIC_CONNECTION_TYPE",
        "config_key": "connection_type",
        "type": "enum",
        "valid_values": ("tcp", "serial", "ble"),
        "transform": lambda x: x.lower(),
    },
    {"env_var": "MMRELAY_MESHTASTIC_HOST", "config_key": "host", "type": "string"},
    {
        "env_var": "MMRELAY_MESHTASTIC_PORT",
        "config_key": "port",
        "type": "int",
        "min_value": 1,
        "max_value": 65535,
    },
    {
        "env_var": "MMRELAY_MESHTASTIC_SERIAL_PORT",
        "config_key": "serial_port",
        "type": "string",
    },
    {
        "env_var": "MMRELAY_MESHTASTIC_BLE_ADDRESS",
        "config_key": "ble_address",
        "type": "string",
    },
    {
        "env_var": "MMRELAY_MESHTASTIC_BROADCAST_ENABLED",
        "config_key": "broadcast_enabled",
        "type": "bool",
    },
    {
        "env_var": "MMRELAY_MESHTASTIC_MESHNET_NAME",
        "config_key": "meshnet_name",
        "type": "string",
    },
    {
        "env_var": "MMRELAY_MESHTASTIC_MESSAGE_DELAY",
        "config_key": "message_delay",
        "type": "float",
        "min_value": 2.0,
    },
]

_LOGGING_ENV_VAR_MAPPINGS = [
    {
        "env_var": "MMRELAY_LOGGING_LEVEL",
        "config_key": "level",
        "type": "enum",
        "valid_values": ("debug", "info", "warning", "error", "critical"),
        "transform": lambda x: x.lower(),
    },
    {"env_var": "MMRELAY_LOG_FILE", "config_key": "filename", "type": "string"},
]

_DATABASE_ENV_VAR_MAPPINGS = [
    {"env_var": "MMRELAY_DATABASE_PATH", "config_key": "path", "type": "string"},
]


def _load_config_from_env_mapping(mappings):
    """
    Build a configuration dictionary from environment variables based on a mapping specification.

    Each mapping entry should be a dict with:
    - "env_var" (str): environment variable name to read.
    - "config_key" (str): destination key in the resulting config dict.
    - "type" (str): one of "string", "int", "float", "bool", or "enum".

    Optional keys (depending on "type"):
    - "min_value", "max_value" (int/float): numeric bounds for "int" or "float" conversions.
    - "valid_values" (iterable): allowed values for "enum".
    - "transform" (callable): function applied to the raw env value before enum validation.

    Behavior:
    - Values are converted/validated according to their type; invalid conversions or values are skipped and an error is logged.
    - Unknown mapping types are skipped and an error is logged.

    Parameters:
        mappings (iterable): Iterable of mapping dicts as described above.

    Returns:
        dict | None: A dict of converted configuration values, or None if no mapped environment variables were present.
    """
    config = {}

    for mapping in mappings:
        env_value = os.getenv(mapping["env_var"])
        if env_value is None:
            continue

        try:
            if mapping["type"] == "string":
                value = env_value
            elif mapping["type"] == "int":
                value = _convert_env_int(
                    env_value,
                    mapping["env_var"],
                    min_value=mapping.get("min_value"),
                    max_value=mapping.get("max_value"),
                )
            elif mapping["type"] == "float":
                value = _convert_env_float(
                    env_value,
                    mapping["env_var"],
                    min_value=mapping.get("min_value"),
                    max_value=mapping.get("max_value"),
                )
            elif mapping["type"] == "bool":
                value = _convert_env_bool(env_value, mapping["env_var"])
            elif mapping["type"] == "enum":
                transformed_value = mapping.get("transform", lambda x: x)(env_value)
                if transformed_value not in mapping["valid_values"]:
                    valid_values_str = "', '".join(mapping["valid_values"])
                    logger.error(
                        f"Invalid {mapping['env_var']}: '{env_value}'. Must be one of: '{valid_values_str}'. Skipping this setting."
                    )
                    continue
                value = transformed_value
            else:
                logger.error(
                    f"Unknown type '{mapping['type']}' for {mapping['env_var']}. Skipping this setting."
                )
                continue

            config[mapping["config_key"]] = value

        except ValueError as e:
            logger.error(
                f"Error parsing {mapping['env_var']}: {e}. Skipping this setting."
            )
            continue

    return config if config else None


def set_config(module, passed_config):
    """
    Assign the given configuration to a module and apply known, optional module-specific settings.

    This function sets module.config = passed_config and, for known module names, applies additional configuration when present:
    - For a module named "matrix_utils": if `matrix_rooms` exists on the module and in the config, it is assigned; if the config contains a `matrix` section with `homeserver`, `access_token`, and `bot_user_id`, those values are assigned to module.matrix_homeserver, module.matrix_access_token, and module.bot_user_id respectively.
    - For a module named "meshtastic_utils": if `matrix_rooms` exists on the module and in the config, it is assigned.

    If the module exposes a callable setup_config() it will be invoked (kept for backward compatibility).

    Returns:
        dict: The same configuration dictionary that was assigned to the module.
    """
    # Set the module's config variable
    module.config = passed_config

    # Handle module-specific setup based on module name
    module_name = module.__name__.split(".")[-1]

    if module_name == "matrix_utils":
        # Set Matrix-specific configuration
        if hasattr(module, "matrix_rooms") and "matrix_rooms" in passed_config:
            module.matrix_rooms = passed_config["matrix_rooms"]

        # Only set matrix config variables if matrix section exists and has the required fields
        # When using credentials.json, these will be loaded by connect_matrix() instead
        if (
            hasattr(module, "matrix_homeserver")
            and CONFIG_SECTION_MATRIX in passed_config
            and CONFIG_KEY_HOMESERVER in passed_config[CONFIG_SECTION_MATRIX]
            and CONFIG_KEY_ACCESS_TOKEN in passed_config[CONFIG_SECTION_MATRIX]
            and CONFIG_KEY_BOT_USER_ID in passed_config[CONFIG_SECTION_MATRIX]
        ):
            module.matrix_homeserver = passed_config[CONFIG_SECTION_MATRIX][
                CONFIG_KEY_HOMESERVER
            ]
            module.matrix_access_token = passed_config[CONFIG_SECTION_MATRIX][
                CONFIG_KEY_ACCESS_TOKEN
            ]
            module.bot_user_id = passed_config[CONFIG_SECTION_MATRIX][
                CONFIG_KEY_BOT_USER_ID
            ]

    elif module_name == "meshtastic_utils":
        # Set Meshtastic-specific configuration
        if hasattr(module, "matrix_rooms") and "matrix_rooms" in passed_config:
            module.matrix_rooms = passed_config["matrix_rooms"]

    # If the module still has a setup_config function, call it for backward compatibility
    if hasattr(module, "setup_config") and callable(module.setup_config):
        module.setup_config()

    return passed_config


def load_config(config_file=None, args=None):
    """
    Load the application configuration from a YAML file or from environment variables.

    If config_file is provided and exists, that file is read and parsed as YAML; otherwise the function searches candidate locations returned by get_config_paths(args) and loads the first readable YAML file found. Empty or null YAML is treated as an empty dict. After loading, environment-derived overrides are merged via apply_env_config_overrides(). The function updates the module-level relay_config and config_path.

    Parameters:
        config_file (str, optional): Path to a specific YAML configuration file to load. If None, candidate paths from get_config_paths(args) are used.
        args: Parsed command-line arguments forwarded to get_config_paths() to influence the search order.

    Returns:
        dict: The resulting configuration dictionary. If no configuration is found or a file read/parse error occurs, returns an empty dict.
    """
    global relay_config, config_path

    # If a specific config file was provided, use it
    if config_file and os.path.isfile(config_file):
        # Store the config path but don't log it yet - will be logged by main.py
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                relay_config = yaml.load(f, Loader=SafeLoader)
            config_path = config_file
            # Treat empty/null YAML files as an empty config dictionary
            if relay_config is None:
                relay_config = {}
            # Apply environment variable overrides
            relay_config = apply_env_config_overrides(relay_config)
            return relay_config
        except (yaml.YAMLError, PermissionError, OSError):
            logger.exception(f"Error loading config file {config_file}")
            return {}

    # Otherwise, search for a config file
    config_paths = get_config_paths(args)

    # Try each config path in order until we find one that exists
    for path in config_paths:
        if os.path.isfile(path):
            config_path = path
            # Store the config path but don't log it yet - will be logged by main.py
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    relay_config = yaml.load(f, Loader=SafeLoader)
                # Treat empty/null YAML files as an empty config dictionary
                if relay_config is None:
                    relay_config = {}
                # Apply environment variable overrides
                relay_config = apply_env_config_overrides(relay_config)
                return relay_config
            except (yaml.YAMLError, PermissionError, OSError):
                logger.exception(f"Error loading config file {path}")
                continue  # Try the next config path

    # No config file found - try to use environment variables only
    logger.warning("Configuration file not found in any of the following locations:")
    for path in config_paths:
        logger.warning(f"  - {path}")

    # Apply environment variable overrides to empty config
    relay_config = apply_env_config_overrides({})

    if relay_config:
        logger.info("Using configuration from environment variables only")
        return relay_config
    else:
        logger.error("No configuration found in files or environment variables.")
        logger.error(msg_suggest_generate_config())
        return {}


def validate_yaml_syntax(config_content, config_path):
    """
    Validate YAML text for syntax and common style issues, parse it with PyYAML, and return results.

    Performs lightweight line-based checks for frequent mistakes (using '=' instead of ':'
    for mappings and non-standard boolean words like 'yes'/'no' or 'on'/'off') and then
    attempts to parse the content with yaml.safe_load. If only style warnings are found,
    parsing is considered successful and warnings are returned; if parsing fails or true
    syntax errors are detected, a detailed error message is returned that references
    config_path to identify the source.

    Parameters:
        config_content (str): Raw YAML text to validate.
        config_path (str): Path or label used in error messages to identify the source of the content.

    Returns:
        tuple:
            is_valid (bool): True if YAML parsed successfully (style warnings allowed), False on syntax/parsing error.
            message (str|None): Human-readable warnings (when parsing succeeded with style issues) or a detailed error description (when parsing failed). None when parsing succeeded without issues.
            parsed_config (object|None): The Python object produced by yaml.safe_load on success; None when parsing failed.
    """
    lines = config_content.split("\n")

    # Check for common YAML syntax issues
    syntax_issues = []

    for line_num, line in enumerate(lines, 1):
        # Skip empty lines and comments
        if not line.strip() or line.strip().startswith("#"):
            continue

        # Check for missing colons in key-value pairs
        if ":" not in line and "=" in line:
            syntax_issues.append(
                f"Line {line_num}: Use ':' instead of '=' for YAML - {line.strip()}"
            )

        # Check for non-standard boolean values (style warning)
        bool_pattern = r":\s*(yes|no|on|off|Yes|No|YES|NO)\s*$"
        match = re.search(bool_pattern, line)
        if match:
            non_standard_bool = match.group(1)
            syntax_issues.append(
                f"Line {line_num}: Style warning - Consider using 'true' or 'false' instead of '{non_standard_bool}' for clarity - {line.strip()}"
            )

    # Try to parse YAML and catch specific errors
    try:
        parsed_config = yaml.safe_load(config_content)
        if syntax_issues:
            # Separate warnings from errors
            warnings = [issue for issue in syntax_issues if "Style warning" in issue]
            errors = [issue for issue in syntax_issues if "Style warning" not in issue]

            if errors:
                return False, "\n".join(errors), None
            elif warnings:
                # Return success but with warnings
                return True, "\n".join(warnings), parsed_config
        return True, None, parsed_config
    except yaml.YAMLError as e:
        error_msg = f"YAML parsing error in {config_path}:\n"

        # Extract line and column information if available
        if hasattr(e, "problem_mark"):
            mark = e.problem_mark
            error_line = mark.line + 1
            error_column = mark.column + 1
            error_msg += f"  Line {error_line}, Column {error_column}: "

            # Show the problematic line
            if error_line <= len(lines):
                problematic_line = lines[error_line - 1]
                error_msg += f"\n  Problematic line: {problematic_line}\n"
                error_msg += f"  Error position: {' ' * (error_column - 1)}^\n"

        # Add the original error message
        error_msg += f"  {str(e)}\n"

        # Provide helpful suggestions based on error type
        error_str = str(e).lower()
        if "mapping values are not allowed" in error_str:
            error_msg += "\n  Suggestion: Check for missing quotes around values containing special characters"
        elif "could not find expected" in error_str:
            error_msg += "\n  Suggestion: Check for unclosed quotes or brackets"
        elif "found character that cannot start any token" in error_str:
            error_msg += (
                "\n  Suggestion: Check for invalid characters or incorrect indentation"
            )
        elif "expected <block end>" in error_str:
            error_msg += (
                "\n  Suggestion: Check indentation - YAML uses spaces, not tabs"
            )

        # Add syntax issues if found
        if syntax_issues:
            error_msg += "\n\nAdditional syntax issues found:\n" + "\n".join(
                syntax_issues
            )

        return False, error_msg, None


def get_meshtastic_config_value(config, key, default=None, required=False):
    """
    Return a value from the "meshtastic" section of the provided configuration.

    Looks up `config["meshtastic"][key]` and returns it if present. If the meshtastic section or the key is missing:
    - If `required` is False, returns `default`.
    - If `required` is True, logs an error with guidance to update the configuration and raises KeyError.

    Parameters:
        config (dict): Parsed configuration mapping containing a "meshtastic" section.
        key (str): Name of the setting to retrieve from the meshtastic section.
        default: Value to return when the key is absent and not required.
        required (bool): When True, a missing key raises KeyError; otherwise returns `default`.

    Returns:
        The value of `config["meshtastic"][key]` if present, otherwise `default`.

    Raises:
        KeyError: If `required` is True and the requested key is not present.
    """
    try:
        return config["meshtastic"][key]
    except KeyError:
        if required:
            logger.error(
                f"Missing required configuration: meshtastic.{key}\n"
                f"Please add '{key}: {default if default is not None else 'VALUE'}' to your meshtastic section in config.yaml\n"
                f"{msg_suggest_check_config()}"
            )
            raise KeyError(
                f"Required configuration 'meshtastic.{key}' is missing. "
                f"Add '{key}: {default if default is not None else 'VALUE'}' to your meshtastic section."
            ) from None
        return default
