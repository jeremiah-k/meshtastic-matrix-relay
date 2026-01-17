import json
import logging
import os
import re
import sys
from typing import Any, cast

import platformdirs
import yaml  # type: ignore[import-untyped]
from yaml.loader import SafeLoader  # type: ignore[import-untyped]

# Import application constants
from mmrelay.constants.app import APP_AUTHOR, APP_NAME
from mmrelay.constants.config import (
    CONFIG_KEY_ACCESS_TOKEN,
    CONFIG_KEY_BOT_USER_ID,
    CONFIG_KEY_HOMESERVER,
    CONFIG_SECTION_MATRIX,
)

# Global variable to store the custom data directory
custom_data_dir: str | None = None


def set_secure_file_permissions(file_path: str, mode: int = 0o600) -> None:
    """
    Set restrictive Unix permission bits on a file to limit access.

    On Linux/macOS attempts to set the file's mode (default 0o600). No action is performed on other platforms; failures are logged and not raised.

    Parameters:
        file_path (str): Path to the file to modify.
        mode (int): Unix permission bits to apply (default 0o600).
    """
    if sys.platform in ["linux", "darwin"]:
        try:
            os.chmod(file_path, mode)
            logger.debug(f"Set secure permissions ({oct(mode)}) on {file_path}")
        except (OSError, PermissionError) as e:
            logger.warning(f"Could not set secure permissions on {file_path}: {e}")


# Custom base directory for Unix systems
def get_base_dir() -> str:
    """
    Determine the filesystem base directory used to store the application's files.

    If the module-level `custom_data_dir` is set, that path is returned. On Linux and macOS the directory is `~/.<APP_NAME>`; on Windows the platform-specific user data directory for the application is returned.

    Returns:
        The filesystem path to the application's base data directory.
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


def get_app_path() -> str:
    """
    Get the application's base directory, accounting for frozen (bundled) executables.

    Returns:
        The path to the application's base directory: the directory containing the frozen executable when running from a bundle, or the directory containing this source file otherwise.
    """
    if getattr(sys, "frozen", False):
        # Running in a bundle (PyInstaller)
        return os.path.dirname(sys.executable)
    else:
        # Running in a normal Python environment
        return os.path.dirname(os.path.abspath(__file__))


def get_config_paths(args: Any = None) -> list[str]:
    """
    Produce a prioritized list of candidate configuration file paths for the application.

    Order of priority: a path provided via command-line args (args.config), the user config directory (created when possible), the current working directory, and the application directory. The user config directory entry is omitted if the directory cannot be created.

    Parameters:
        args (Any): Parsed command-line arguments, expected to have an optional `config` attribute specifying a config file path.

    Returns:
        list[str]: Absolute paths to candidate configuration files, ordered by priority.
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


def get_data_dir() -> str:
    """
    Get the application's data directory, creating it if necessary.

    On Linux and macOS this is "<base_dir>/data" (where base_dir is returned by get_base_dir()).
    On Windows this is "<custom_data_dir>/data" if a global override is set, otherwise the platform default user data directory for the application.

    Returns:
        Absolute path to the data directory.
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


def get_plugin_data_dir(plugin_name: str | None = None) -> str:
    """
    Resolve and ensure the application's plugins data directory, optionally for a specific plugin.

    Creates the top-level plugins directory if missing; if `plugin_name` is provided, creates and returns a subdirectory for that plugin.

    Parameters:
        plugin_name (str | None): Optional plugin identifier to return a plugin-specific subdirectory.

    Returns:
        str: Absolute path to the plugins directory, or to the plugin-specific subdirectory when `plugin_name` is provided.
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


def get_log_dir() -> str:
    """
    Get the application's log directory, creating it if missing.

    On Linux/macOS this is "<base_dir>/logs". On Windows this is "<custom_data_dir>/logs" when a global custom data directory is set, otherwise the platform-specific user log directory is used.

    Returns:
        str: Absolute path to the log directory; the directory is guaranteed to exist.
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


def get_e2ee_store_dir() -> str:
    """
    Get the absolute path to the application's end-to-end encryption (E2EE) data store directory, creating it if necessary.

    On Linux and macOS the directory is located under the application base directory; on Windows it uses the configured custom data directory when set, otherwise the platform-specific user data directory. The directory will be created if it does not exist.

    Returns:
        store_dir (str): Absolute path to the ensured E2EE store directory.
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


def _convert_env_bool(value: str, var_name: str) -> bool:
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


def _convert_env_int(
    value: str,
    var_name: str,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
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


def _convert_env_float(
    value: str,
    var_name: str,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
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


def load_meshtastic_config_from_env() -> dict[str, Any] | None:
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


def load_logging_config_from_env() -> dict[str, Any] | None:
    """
    Load logging configuration from environment variables.

    Builds a logging configuration dictionary from the module's predefined environment-variable mappings. If the resulting mapping contains a "filename" key, adds "log_to_file": True.

    Returns:
        dict[str, Any] | None: Parsed logging configuration when any relevant environment variables are set; otherwise `None`.
    """
    config = _load_config_from_env_mapping(_LOGGING_ENV_VAR_MAPPINGS)
    if config:
        if config.get("filename"):
            config["log_to_file"] = True
        logger.debug(
            f"Loaded logging configuration from environment variables: {list(config.keys())}"
        )
    return config


def load_database_config_from_env() -> dict[str, Any] | None:
    """
    Build a database configuration fragment from environment variables.

    Reads the environment variables specified by the module-level mapping and converts present values into a dictionary keyed by configuration keys. Useful for merging database-related overrides into the main application config.

    Returns:
        dict[str, Any] | None: A dictionary of database configuration values if any mapped environment variables were found, `None` otherwise.
    """
    config = _load_config_from_env_mapping(_DATABASE_ENV_VAR_MAPPINGS)
    if config:
        logger.debug(
            f"Loaded database configuration from environment variables: {list(config.keys())}"
        )
    return config


def is_e2ee_enabled(config: dict[str, Any]) -> bool:
    """
    Determine whether End-to-End Encryption (E2EE) is enabled in the provided configuration.

    Checks the `matrix` section for either `matrix.encryption.enabled` or `matrix.e2ee.enabled` and returns True if either is set to True. On Windows (`sys.platform == "win32"`), E2EE is treated as unsupported and this function always returns False.

    Parameters:
        config (dict[str, Any]): Top-level configuration mapping (may be empty or None).

    Returns:
        bool: `true` if E2EE is enabled in the configuration and the platform supports E2EE, `false` otherwise.
    """
    # E2EE is not supported on Windows
    if sys.platform == "win32":
        return False

    if not config:
        return False

    matrix_cfg = config.get("matrix", {}) or {}
    if not matrix_cfg:
        return False

    encryption_enabled = cast(
        bool, matrix_cfg.get("encryption", {}).get("enabled", False)
    )
    e2ee_enabled = cast(bool, matrix_cfg.get("e2ee", {}).get("enabled", False))

    return encryption_enabled or e2ee_enabled


def check_e2ee_enabled_silently(args: Any = None) -> bool:
    """
    Check whether End-to-End Encryption (E2EE) is enabled by inspecting the first readable configuration file.

    This function examines candidate configuration files in priority order, ignoring unreadable files and YAML parsing errors, and returns as soon as a readable configuration enabling E2EE is found. On Windows this function always returns False.

    Parameters:
        args: Optional parsed command-line arguments that can influence config search order.

    Returns:
        True if E2EE is enabled in the first readable configuration file, False otherwise.
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


def apply_env_config_overrides(config: dict[str, Any] | None) -> dict[str, Any]:
    """
    Merge configuration values derived from environment variables into a configuration dictionary.

    If `config` is falsy, a new dict is created. Environment-derived fragments are merged into the top-level
    keys "meshtastic", "logging", and "database" when present; existing keys in those sections are preserved.
    The input dictionary may be mutated in place.

    Parameters:
        config (dict[str, Any] | None): Base configuration to update (or None to start from an empty dict).

    Returns:
        dict[str, Any]: The configuration dictionary with environment overrides applied.
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


def load_credentials() -> dict[str, Any] | None:
    """
    Load Matrix credentials from the application's base configuration directory.

    Searches for "credentials.json" in the application's base configuration directory and parses it as JSON.

    Returns:
        dict[str, Any] | None: Parsed credentials dictionary when the file exists and contains valid JSON; `None` if the file is missing, unreadable, or contains invalid JSON.
    """
    config_dir = ""
    try:
        config_dir = get_base_dir()
        credentials_path = os.path.join(config_dir, "credentials.json")

        logger.debug(f"Looking for credentials at: {credentials_path}")

        if os.path.exists(credentials_path):
            with open(credentials_path, "r", encoding="utf-8") as f:
                credentials = cast(dict[str, Any], json.load(f))
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


def save_credentials(credentials: dict[str, Any]) -> None:
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
    config_dir = ""
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
relay_config: dict[str, Any] = {}
config_path: str | None = None

# Environment variable mappings for configuration sections
_MESHTASTIC_ENV_VAR_MAPPINGS: list[dict[str, Any]] = [
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

_LOGGING_ENV_VAR_MAPPINGS: list[dict[str, Any]] = [
    {
        "env_var": "MMRELAY_LOGGING_LEVEL",
        "config_key": "level",
        "type": "enum",
        "valid_values": ("debug", "info", "warning", "error", "critical"),
        "transform": lambda x: x.lower(),
    },
    {"env_var": "MMRELAY_LOG_FILE", "config_key": "filename", "type": "string"},
]

_DATABASE_ENV_VAR_MAPPINGS: list[dict[str, Any]] = [
    {"env_var": "MMRELAY_DATABASE_PATH", "config_key": "path", "type": "string"},
]


def _load_config_from_env_mapping(
    mappings: list[dict[str, Any]],
) -> dict[str, Any] | None:
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
            value: Any
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


def set_config(module: Any, passed_config: dict[str, Any]) -> dict[str, Any]:
    """
    Assign the given configuration to a module and apply known module-specific settings.

    If the target module's name is "matrix_utils", this may assign `matrix_rooms` and, when present, `matrix.homeserver`, `matrix.access_token`, and `matrix.bot_user_id` into module attributes. If the module's name is "meshtastic_utils", this may assign `matrix_rooms`. If the module exposes a callable `setup_config()`, it will be invoked.

    Parameters:
        module (Any): The module object to receive the configuration.
        passed_config (dict[str, Any]): Configuration mapping to attach to the module.

    Returns:
        dict[str, Any]: The same `passed_config` object that was assigned to the module.
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


def load_config(config_file: str | None = None, args: Any = None) -> dict[str, Any]:
    """
    Load the application configuration from a YAML file or from environment variables.

    If `config_file` is provided and readable, that file is used; otherwise candidate locations from `get_config_paths(args)` are searched in order and the first readable YAML file is loaded. Empty or null YAML content is treated as an empty dictionary. Environment-derived overrides are merged into the loaded configuration. The function updates the module-level `relay_config` and `config_path` to reflect the resulting configuration source.

    Parameters:
        config_file (str | None): Path to a specific YAML configuration file to load. If `None`, candidate paths from `get_config_paths(args)` are used.
        args: Parsed command-line arguments forwarded to `get_config_paths()` to influence search order.

    Returns:
        dict: The resulting configuration dictionary. Returns an empty dict if no configuration is found or a file read/parse error occurs.
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
        try:
            from mmrelay.cli_utils import msg_suggest_generate_config
        except ImportError:
            logger.debug("Could not import CLI suggestion helpers", exc_info=True)
        else:
            logger.error(msg_suggest_generate_config())
        return {}


def validate_yaml_syntax(
    config_content: str, config_path: str
) -> tuple[bool, str | None, Any]:
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
        mark = getattr(e, "problem_mark", None)
        if mark is not None:
            mark_any = cast(Any, mark)
            error_line = mark_any.line + 1
            error_column = mark_any.column + 1
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


def get_meshtastic_config_value(
    config: dict[str, Any], key: str, default: Any = None, required: bool = False
) -> Any:
    """
    Retrieve a value from the `meshtastic` section of a configuration mapping.

    If the `meshtastic` section or the requested key is missing, returns `default` unless `required` is True, in which case an error is logged and a KeyError is raised.

    Parameters:
        config (dict): Configuration mapping that may contain a `meshtastic` section.
        key (str): Key to look up within the `meshtastic` section.
        default: Value to return when the key is absent and `required` is False.
        required (bool): If True, a missing key causes a KeyError to be raised and an error to be logged.

    Returns:
        The value of `meshtastic.<key>`, or `default` if the key is missing and `required` is False.

    Raises:
        KeyError: If `required` is True and the requested key is missing.
    """
    section = config.get("meshtastic", {}) if isinstance(config, dict) else {}
    if not isinstance(section, dict):
        section = {}
    try:
        return section[key]
    except KeyError:
        if required:
            try:
                from mmrelay.cli_utils import msg_suggest_check_config
            except ImportError:

                def msg_suggest_check_config() -> str:
                    """
                    Provide a fallback suggestion string for checking configuration when the real helper is unavailable.
                    
                    Returns:
                        suggestion (str): An empty string indicating no suggestion is available.
                    """
                    return ""

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