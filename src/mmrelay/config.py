import asyncio
import functools
import json
import ntpath
import os
import re
import sys
import warnings
from collections.abc import Mapping as MappingABC
from typing import TYPE_CHECKING, Any, Iterable, Mapping, cast

import yaml
from yaml.loader import SafeLoader

import mmrelay.paths as paths_module
from mmrelay.constants.app import CREDENTIALS_FILENAME, MATRIX_DIRNAME

# Import application constants
from mmrelay.constants.config import (
    CONFIG_KEY_ACCESS_TOKEN,
    CONFIG_KEY_BOT_USER_ID,
    CONFIG_KEY_HOMESERVER,
    CONFIG_SECTION_MATRIX,
)

# Import new path resolution system
from mmrelay.paths import get_config_paths as get_unified_config_paths
from mmrelay.paths import (
    get_credentials_path,
)
from mmrelay.paths import get_e2ee_store_dir as get_unified_store_dir
from mmrelay.paths import (
    get_home_dir,
    get_legacy_dirs,
)
from mmrelay.paths import get_logs_dir as get_unified_logs_dir
from mmrelay.paths import get_plugin_data_dir as get_unified_plugin_data_dir
from mmrelay.paths import (
    get_plugins_dir,
    is_deprecation_window_active,
)

if TYPE_CHECKING:
    import logging


class CredentialsPathError(OSError):
    def __init__(self) -> None:
        super().__init__("No candidate credentials paths available")


def _expand_path(path: str) -> str:
    """
    Expand a filesystem path, resolving a leading '~' and returning the absolute path.

    Parameters:
        path (str): Path that may contain a leading `~` or be relative.

    Returns:
        str: Absolute path with any leading `~` expanded to the user's home directory.
    """
    return os.path.abspath(os.path.expanduser(path))


@functools.lru_cache(maxsize=None)
def _warn_deprecated(_name: str) -> None:
    """
    Emit a DeprecationWarning instructing callers to use paths.get_home_dir().

    Parameters:
        _name (str): Ignored; present so callers can cache warnings per name (e.g., via lru_cache).
    """
    warnings.warn(
        "Use paths.get_home_dir() instead. Support will be removed in v1.4.",
        DeprecationWarning,
        stacklevel=3,
    )


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


def get_base_dir() -> str:
    """
    Get the filesystem base directory used to store the application's files.

    Deprecated: Use `get_home_dir()` from `mmrelay.paths` instead; this wrapper exists for backward compatibility.

    Returns:
        The filesystem path to the application's base data directory as a string.
    """
    _warn_deprecated("get_base_dir")
    return str(get_home_dir())


def get_app_path() -> str:
    """
    Determine the application's base directory, accounting for frozen (bundled) executables.

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
    Get a prioritized list of candidate configuration file paths for the application.

    Ensures the user's home configuration directory exists (best-effort) before returning paths.

    Parameters:
        args (Any): Parsed command-line arguments; if present, `args.config` is used as an explicit config candidate.

    Returns:
        list[str]: Absolute paths to candidate configuration files, ordered by priority.
    """
    explicit = getattr(args, "config", None) if args else None
    paths = get_unified_config_paths(explicit=explicit)

    # Match legacy behavior: ensure home directory (user config dir) exists
    try:
        os.makedirs(str(get_home_dir()), exist_ok=True)
    except (OSError, PermissionError):
        pass

    # Convert Path objects to absolute strings
    return [str(p.absolute()) for p in paths]


def get_credentials_search_paths(
    *,
    explicit_path: str | None = None,
    config_paths: Iterable[str] | None = None,
    include_base_data: bool = True,
) -> list[str]:
    """
    Build an ordered, de-duplicated list of candidate credentials.json paths.

    Parameters:
        explicit_path (str | None): Optional explicit file or directory path.
        config_paths (Iterable[str] | None): Optional iterable of config file paths.
        include_base_data (bool): When True, include base/data directory fallbacks.

    Returns:
        list[str]: Ordered candidate credential file paths.
    """
    candidate_paths: list[str] = []
    seen: set[str] = set()

    def _add(path: str | None) -> None:
        """
        Add a non-empty, previously unseen path to the candidate_paths collection.

        If `path` is None, empty, or already present in `seen`, the function has no effect; otherwise it appends `path` to `candidate_paths` and records it in `seen`.

        Parameters:
            path: Path string to add; ignored if `None` or already added.
        """
        if not path or path in seen:
            return
        candidate_paths.append(path)
        seen.add(path)

    if explicit_path:
        raw_path = explicit_path
        expanded_path = _expand_path(raw_path)
        path_is_dir = os.path.isdir(expanded_path)
        if not path_is_dir:
            path_is_dir = bool(
                raw_path.endswith(os.path.sep)
                or (os.path.altsep and raw_path.endswith(os.path.altsep))
            )
        if path_is_dir:
            normalized_dir = os.path.normpath(expanded_path)
            _add(os.path.join(normalized_dir, "credentials.json"))
        else:
            _add(expanded_path)

    if config_paths:
        for config_path in config_paths:
            if not config_path:
                continue
            config_dir = os.path.dirname(os.path.abspath(config_path))
            _add(os.path.join(config_dir, "credentials.json"))
            _add(os.path.join(config_dir, MATRIX_DIRNAME, "credentials.json"))

    if include_base_data:
        # Prefer v1.3 unified HOME locations before compatibility/legacy fallbacks.
        _add(str(get_credentials_path()))
        # Compatibility fallback for pre-1.3 credentials location.
        _add(os.path.join(str(get_home_dir()), CREDENTIALS_FILENAME))

        if is_deprecation_window_active():
            for legacy_dir in get_legacy_dirs():
                _add(os.path.join(legacy_dir, "credentials.json"))
                _add(os.path.join(legacy_dir, MATRIX_DIRNAME, "credentials.json"))

    return candidate_paths


# Backward-compatibility alias (tests/integrations may still patch this name)
def get_candidate_credentials_paths(
    *,
    explicit_path: str | None = None,
    config_paths: Iterable[str] | None = None,
    include_base_data: bool = True,
) -> list[str]:
    """
    Provide a backwards-compatible alias that builds an ordered list of candidate filesystem paths to search for a credentials.json file.

    Parameters:
        explicit_path (str | None): An explicit credentials path to prefer if provided.
        config_paths (Iterable[str] | None): Iterable of config file paths to derive candidate credentials locations from.
        include_base_data (bool): If True, include the unified base data directory's credentials path in candidates.

    Returns:
        list[str]: Prioritized candidate absolute paths to search for a credentials.json file.
    """
    return get_credentials_search_paths(
        explicit_path=explicit_path,
        config_paths=config_paths,
        include_base_data=include_base_data,
    )


class InvalidCredentialsPathTypeError(TypeError):
    """Raised when credentials_path is not a string."""

    def __init__(self) -> None:
        """
        Initialize the exception raised when a provided `credentials_path` value is not a string.

        The exception message is set to: "credentials_path must be a string".
        """
        super().__init__("credentials_path must be a string")


def get_explicit_credentials_path(config: Mapping[str, Any] | None) -> str | None:
    """
    Get the explicitly configured credentials path, if present.

    Checks the `MMRELAY_CREDENTIALS_PATH` environment variable first, then the top-level
    `credentials_path` key in `config`, and finally `config["matrix"]["credentials_path"]`.
    If a configured value is present it must be a string.

    Parameters:
        config (Mapping[str, Any] | None): Optional configuration mapping to consult.

    Returns:
        str | None: The configured credentials path string, or `None` if not set.

    Raises:
        InvalidCredentialsPathTypeError: If a found `credentials_path` value exists but is not a string.
    """
    env_path = os.getenv("MMRELAY_CREDENTIALS_PATH")
    if env_path:
        return env_path
    if not isinstance(config, MappingABC):
        return None
    explicit_path = config.get("credentials_path")
    if explicit_path:
        if not isinstance(explicit_path, str):
            raise InvalidCredentialsPathTypeError()
        return explicit_path
    matrix_section = config.get("matrix")
    if isinstance(matrix_section, dict):
        credentials_path = matrix_section.get("credentials_path")
        if credentials_path is not None and not isinstance(credentials_path, str):
            raise InvalidCredentialsPathTypeError()
        return credentials_path or None
    return None


def get_data_dir(*, create: bool = True) -> str:
    """
    Return the application's data directory path.

    Deprecated: use `get_home_dir()` from mmrelay.paths instead. If `create` is True, the directory will be created if it does not exist.

    Parameters:
        create (bool): If True, ensure the returned directory exists by creating it when necessary.

    Returns:
        str: Absolute path to the data directory.
    """
    _warn_deprecated("get_data_dir")
    home = str(get_home_dir())
    if create:
        os.makedirs(home, exist_ok=True)
    return home


def get_plugin_data_dir(
    plugin_name: str | None = None,
    *,
    subdir: str | None = None,
    plugin_type: str | None = None,
) -> str:
    """
    Resolve the application's plugins data directory or a plugin-specific data directory.

    Ensures the top-level plugins directory exists; when `plugin_name` is provided, ensures and returns the plugin's data directory (optionally the named `subdir`) and will infer `plugin_type` from the global `relay_config` if not supplied.

    Parameters:
        plugin_name (str | None): Optional plugin identifier to return a plugin-specific directory.
        subdir (str | None): Optional subdirectory name inside the plugin's data directory.
        plugin_type (str | None): Optional plugin category; expected values include `"custom"`, `"community"`, or `"core"`. If omitted, the function attempts to infer the type from `relay_config`.

    Returns:
        str: Absolute path to the resolved plugins directory or the plugin-specific data directory.
    """
    plugins_data_dir = str(get_plugins_dir())
    os.makedirs(plugins_data_dir, exist_ok=True)

    # If a plugin name is provided, create and return a plugin-specific directory
    if plugin_name:
        if plugin_type is None and isinstance(relay_config, dict):
            community_plugins = relay_config.get("community-plugins") or {}
            custom_plugins = relay_config.get("custom-plugins") or {}
            if isinstance(community_plugins, dict) and plugin_name in community_plugins:
                plugin_type = "community"
            elif isinstance(custom_plugins, dict) and plugin_name in custom_plugins:
                plugin_type = "custom"
            else:
                plugin_type = "core"

        plugin_data_dir = get_unified_plugin_data_dir(
            plugin_name, subdir=subdir, plugin_type=plugin_type
        )
        os.makedirs(str(plugin_data_dir), exist_ok=True)
        return str(plugin_data_dir)

    return plugins_data_dir


def get_log_dir() -> str:
    """
    Return the application's log directory, creating it if missing.

    Returns:
        Absolute path to the log directory as a string; the directory is guaranteed to exist.
    """
    log_dir = str(get_unified_logs_dir())
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def _get_fallback_store_dir() -> str:
    """
    Produce a home-based fallback path for the Matrix E2EE store.

    Used when the platform does not officially support E2EE (e.g. Windows)
    or when directory creation in the primary location fails.

    Returns:
        str: Absolute path to the fallback store directory.
    """
    base = str(get_home_dir())
    if sys.platform == "win32":
        return ntpath.join(base, MATRIX_DIRNAME, "store")
    return os.path.join(base, MATRIX_DIRNAME, "store")


def get_e2ee_store_dir() -> str:
    """
    Return the absolute path to the application's E2EE data store directory, creating it when possible.

    If the unified store resolver signals the platform does not support E2EE, this function returns a legacy store path under the user's home directory and logs a warning. If directory creation fails due to filesystem permissions or other OS errors, it returns the same home-based fallback path and logs a warning instead of failing. If the unified resolver raises a RuntimeError for reasons other than platform support, that RuntimeError is propagated.

    Returns:
        The absolute path to the E2EE store directory as a string.

    Raises:
        RuntimeError: If the unified store resolver raises a RuntimeError that should not be handled as a platform-unsupported condition.
    """
    try:
        store_dir = str(get_unified_store_dir())
        os.makedirs(store_dir, exist_ok=True)
        return store_dir
    except paths_module.E2EENotSupportedError as e:
        # Match legacy behavior on Windows: logs warning and returns a path anyway
        # (even if it won't be used for E2EE)
        logger.warning("E2EE store not officially supported on this platform: %s", e)
        return _get_fallback_store_dir()
    except (OSError, PermissionError) as e:
        # Fallback for permission errors - log and return a home-based path
        logger.warning("Could not create E2EE store directory: %s", e)
        return _get_fallback_store_dir()


def _convert_env_bool(value: str, var_name: str) -> bool:
    """
    Parse an environment-variable string into a boolean.

    Accepts (case-insensitive) true values: "true", "1", "yes", "on"; false values: "false", "0", "no", "off".

    Parameters:
        value (str): The environment variable value to convert.
        var_name (str): Name of the environment variable — included in the ValueError message if parsing fails.

    Returns:
        `True` if `value` represents a true value, `False` if it represents a false value.

    Raises:
        ValueError: If `value` is not a recognized boolean representation; the error message includes `var_name`.
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


def load_matrix_config_from_env() -> dict[str, Any] | None:
    """
    Build a Matrix configuration fragment from environment variables.

    Reads the Matrix-related environment variables defined in the module mapping and returns a configuration fragment suitable for merging into the top-level config.

    Returns:
        dict[str, Any]: Dictionary of parsed Matrix configuration values if any mapped environment variables were present.
        None: If no relevant environment variables were set.
    """
    config = _load_config_from_env_mapping(_MATRIX_ENV_VAR_MAPPINGS)
    if config:
        logger.debug(
            f"Loaded Matrix configuration from environment variables: {list(config.keys())}"
        )
    return config


def is_e2ee_enabled(config: dict[str, Any] | None) -> bool:
    """
    Determine whether End-to-End Encryption (E2EE) is enabled in the given configuration.

    If the platform does not support E2EE (Windows), this function always reports that E2EE is disabled. The function inspects the top-level `matrix` section and treats E2EE as enabled when either `matrix.encryption.enabled` or `matrix.e2ee.enabled` is true.

    Parameters:
        config (dict[str, Any] | None): Top-level configuration mapping which may be empty or None.

    Returns:
        bool: `True` if E2EE is enabled in the configuration and the platform supports E2EE, `False` otherwise.
    """
    # E2EE is not supported on Windows
    if sys.platform == "win32":
        return False

    if not config:
        return False

    matrix_cfg = config.get("matrix", {}) or {}
    if not isinstance(matrix_cfg, dict) or not matrix_cfg:
        return False

    encryption_cfg = matrix_cfg.get("encryption")
    if not isinstance(encryption_cfg, dict):
        encryption_cfg = {}
    e2ee_cfg = matrix_cfg.get("e2ee")
    if not isinstance(e2ee_cfg, dict):
        e2ee_cfg = {}
    encryption_value = encryption_cfg.get("enabled", False)
    encryption_enabled = (
        encryption_value if isinstance(encryption_value, bool) else False
    )
    e2ee_value = e2ee_cfg.get("enabled", False)
    e2ee_enabled = e2ee_value if isinstance(e2ee_value, bool) else False

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


def _normalize_optional_dict_sections(
    config: dict[str, Any],
    section_names: tuple[str, ...],
) -> None:
    """
    Normalize optional mapping sections that are present but null.

    YAML allows keys with no value to parse as None; convert those to empty dicts
    for known mapping sections so downstream code can safely use .get/.update.
    """
    for section_name in section_names:
        if section_name in config and config[section_name] is None:
            config[section_name] = {}


def _get_mapping_section(
    config: dict[str, Any], section_name: str
) -> dict[str, Any] | None:
    """
    Return a mutable mapping for a config section, creating it when missing.

    Returns None if the section exists but is not a mapping.
    """
    section = config.get(section_name)
    if section is None:
        section = {}
        config[section_name] = section
        return section
    if not isinstance(section, dict):
        logger.warning(
            "Config section '%s' is not a mapping; skipping environment overrides",
            section_name,
        )
        return None
    return section


def apply_env_config_overrides(config: dict[str, Any] | None) -> dict[str, Any]:
    """
    Merge configuration values derived from environment variables into a configuration dictionary.

    If `config` is falsy, a new dict is created. Environment-derived fragments are merged into the top-level
    keys "meshtastic", "logging", "database", and "matrix" when present; existing keys in those sections are preserved.
    The input dictionary may be mutated in place.

    Parameters:
        config (dict[str, Any] | None): Base configuration to update (or None to start from an empty dict).

    Returns:
        dict[str, Any]: The configuration dictionary with environment overrides applied.
    """
    if not config:
        config = {}
    else:
        _normalize_optional_dict_sections(
            config,
            (
                "matrix",
                "meshtastic",
                "logging",
                "database",
                "db",
                "plugins",
                "custom-plugins",
                "community-plugins",
            ),
        )

    # Apply Meshtastic configuration overrides
    meshtastic_env_config = load_meshtastic_config_from_env()
    if meshtastic_env_config:
        meshtastic_section = _get_mapping_section(config, "meshtastic")
        if meshtastic_section is not None:
            meshtastic_section.update(meshtastic_env_config)
            logger.debug("Applied Meshtastic environment variable overrides")

    # Apply logging configuration overrides
    logging_env_config = load_logging_config_from_env()
    if logging_env_config:
        logging_section = _get_mapping_section(config, "logging")
        if logging_section is not None:
            logging_section.update(logging_env_config)
            logger.debug("Applied logging environment variable overrides")

    # Apply database configuration overrides
    database_env_config = load_database_config_from_env()
    if database_env_config:
        database_section = _get_mapping_section(config, "database")
        if database_section is not None:
            database_section.update(database_env_config)
            logger.debug("Applied database environment variable overrides")

    # Apply Matrix configuration overrides
    matrix_env_config = load_matrix_config_from_env()
    if matrix_env_config:
        matrix_section = _get_mapping_section(config, "matrix")
        if matrix_section is not None:
            matrix_section.update(matrix_env_config)
            logger.debug("Applied Matrix environment variable overrides")

    return config


def load_credentials() -> dict[str, Any] | None:
    """
    Locate and load Matrix credentials from candidate credentials.json files.

    Searches an explicit credentials path (from environment or configuration) followed by prioritized candidate locations and returns the first successfully parsed credentials mapping. If a credentials file is found in a legacy directory during the deprecation window, a migration warning is emitted. On any read/parse error or if no credentials file is found, returns None.

    Returns:
        dict[str, Any]: Parsed credentials mapping if a valid credentials.json is found.
        None: If no readable, valid credentials file is found.
    """
    try:
        explicit_path = get_explicit_credentials_path(relay_config)
        config_paths = [config_path] if config_path else None
        candidate_paths = get_credentials_search_paths(
            explicit_path=explicit_path,
            config_paths=config_paths,
        )
        logger.debug("Looking for credentials at: %s", candidate_paths)
    except (OSError, PermissionError, TypeError):
        logger.exception("Error preparing credentials path candidates")
        return None

    legacy_dirs = (
        {os.path.abspath(str(p)) for p in get_legacy_dirs()}
        if is_deprecation_window_active()
        else set()
    )
    primary_credentials_path = os.path.abspath(str(get_credentials_path()))
    legacy_home_credentials = os.path.abspath(
        os.path.join(str(get_home_dir()), CREDENTIALS_FILENAME)
    )
    for credentials_path in candidate_paths:
        if not os.path.exists(credentials_path):
            continue

        try:
            with open(credentials_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, PermissionError, json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "Ignoring unreadable or invalid credentials.json at %s: %s",
                credentials_path,
                exc,
            )
            continue

        if not isinstance(loaded, dict):
            logger.error("credentials.json must be a JSON object: %s", credentials_path)
            continue

        credentials = cast(dict[str, Any], loaded)
        missing_required = [
            key
            for key in ("homeserver", "access_token", "user_id")
            if not isinstance(credentials.get(key), str)
            or not credentials.get(key, "").strip()
        ]
        if missing_required:
            logger.warning(
                "Ignoring credentials.json missing required keys (%s): %s",
                ", ".join(missing_required),
                credentials_path,
            )
            continue

        creds_dir = os.path.abspath(os.path.dirname(credentials_path))
        if creds_dir in legacy_dirs:
            _get_config_logger().warning(
                "Credentials found in legacy location: %s. "
                "Please run 'mmrelay migrate' to move to new unified structure. "
                "Support for legacy credentials will be removed in v1.4.",
                credentials_path,
            )
        elif (
            os.path.abspath(credentials_path) == legacy_home_credentials
            and os.path.abspath(credentials_path) != primary_credentials_path
        ):
            _get_config_logger().warning(
                "Credentials found in legacy location: %s. "
                "Please run 'mmrelay migrate' to move to new unified structure.",
                credentials_path,
            )
        logger.debug("Successfully loaded credentials from %s", credentials_path)
        return credentials

    # On Windows, also log the directory contents for debugging
    if sys.platform == "win32":
        debug_candidates: list[str] = []
        if config_path:
            debug_candidates.append(os.path.dirname(config_path))
        debug_candidates.append(str(get_home_dir()))
        seen: set[str] = set()
        for debug_dir in debug_candidates:
            if not debug_dir or debug_dir in seen:
                continue
            seen.add(debug_dir)
            try:
                files = os.listdir(debug_dir)
                logger.debug("Directory contents of %s: %s", debug_dir, files)
            except OSError:
                pass
    return None


async def async_load_credentials() -> dict[str, Any] | None:
    """
    Load Matrix credentials using a background thread.

    Note: This reads module-level globals (relay_config, config_path) without
    explicit synchronization. This is safe assuming these globals are fully
    initialized during the application's sequential startup before any
    asynchronous tasks are spawned.

    Returns:
        dict[str, Any] | None: Parsed credentials mapping if a readable, valid credentials file is found; `None` otherwise.
    """
    return await asyncio.to_thread(load_credentials)


def save_credentials(
    credentials: dict[str, Any], credentials_path: str | None = None
) -> None:
    """
    Persist the given credentials mapping to the resolved credentials.json file.

    Resolves the target path from the explicit `credentials_path` argument, an explicit path from configuration, or the unified credentials path; if the resolved target refers to a directory the filename "credentials.json" is appended. The function creates the target directory if missing, writes the credentials as pretty-printed JSON (indent=2), and on Unix-like systems sets file permissions to owner read/write (0o600). I/O and permission failures are logged and re-raised.

    Parameters:
        credentials (dict): JSON-serializable mapping of credentials to persist.
        credentials_path (str | None): Optional file path or directory to write to; when omitted the function uses a configured or unified credentials path.

    Raises:
        OSError: If creating directories or writing the file fails.
        PermissionError: If permission is denied when creating directories or writing the file.
    """

    # Determine target path
    path_module = os.path
    if sys.platform == "win32":
        path_module = ntpath

    if credentials_path:
        # Explicit path provided - use it directly
        raw_target_path = credentials_path
        target_path = path_module.normpath(_expand_path(credentials_path))
    else:
        explicit_path = get_explicit_credentials_path(relay_config)
        if explicit_path:
            raw_target_path = explicit_path
            target_path = path_module.normpath(_expand_path(explicit_path))
        else:
            raw_target_path = str(get_credentials_path())
            target_path = path_module.normpath(raw_target_path)

    if (
        path_module.isdir(target_path)
        or raw_target_path.endswith(path_module.sep)
        or (path_module.altsep and raw_target_path.endswith(path_module.altsep))
    ):
        target_path = path_module.join(
            path_module.normpath(target_path), "credentials.json"
        )

    # Ensure target directory exists
    target_dir = path_module.dirname(target_path) or "."
    try:
        os.makedirs(target_dir, exist_ok=True)
    except (OSError, PermissionError):
        logger.exception("Could not create credentials directory %s", target_dir)
        if sys.platform == "win32":
            logger.warning(
                "On Windows, ensure the application has write permissions to the credentials path."
            )
        raise

    # Write credentials
    try:
        logger.info("Saving credentials to: %s", target_path)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(credentials, f, indent=2)
    except (OSError, PermissionError):
        logger.exception("Error writing credentials.json to %s", target_path)
        raise

    # Set secure permissions on Unix systems (600 - owner read/write only)
    set_secure_file_permissions(target_path)

    if os.path.exists(target_path):
        logger.debug("Verified credentials.json exists at %s", target_path)

    logger.info("Successfully saved credentials to %s", target_path)


# Use structured logging to align with the rest of the codebase.
def _get_config_logger() -> "logging.Logger":
    # Late import avoids circular dependency (log_utils -> config).
    """
    Obtain a logger for configuration-related messages.

    Returns:
        logging.Logger: Logger instance named "Config".
    """
    from mmrelay.log_utils import get_logger

    return get_logger("Config")


# Lazy logger initialization to avoid circular import issues during startup
_config_logger: "logging.Logger | None" = None


def _get_logger() -> "logging.Logger":
    """
    Return the configuration logger, initializing it on first access.

    Returns:
        logging.Logger: The configuration logger instance.
    """
    global _config_logger
    if _config_logger is None:
        _config_logger = _get_config_logger()
    return _config_logger


# Proxy for backward compatibility and tests
class _LoggerProxy:
    """Proxy for the configuration logger to avoid circular imports during startup."""

    def __getattr__(self, name: str) -> Any:
        """Forward attribute access to the real logger."""
        return getattr(_get_logger(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Allow setting attributes (needed for mock.patch)."""
        self.__dict__[name] = value


logger: Any = _LoggerProxy()

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

_MATRIX_ENV_VAR_MAPPINGS: list[dict[str, Any]] = [
    {
        "env_var": "MMRELAY_MATRIX_HOMESERVER",
        "config_key": "homeserver",
        "type": "string",
    },
    {
        "env_var": "MMRELAY_MATRIX_BOT_USER_ID",
        "config_key": "bot_user_id",
        "type": "string",
    },
    {"env_var": "MMRELAY_MATRIX_PASSWORD", "config_key": "password", "type": "string"},
    {
        "env_var": "MMRELAY_MATRIX_ACCESS_TOKEN",
        "config_key": "access_token",
        "type": "string",
    },
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
    Assign the provided configuration mapping to a module and apply known module-specific settings.

    When the module appears to be the Matrix helper, propagate `matrix_rooms` and, when a `matrix` section contains `homeserver`, `access_token`, and `bot_user_id`, assign those values to the module's corresponding attributes. When the module appears to be the Meshtastic helper, propagate `matrix_rooms`. If the module exposes a callable `setup_config()`, it will be invoked after assignments.

    Parameters:
        module (Any): Module object to receive configuration attributes.
        passed_config (dict[str, Any]): Configuration mapping to assign to the module.

    Returns:
        dict[str, Any]: The same `passed_config` object that was attached to the module.
    """
    # Set the module's config variable
    module.config = passed_config

    # Handle module-specific setup based on module name
    module_name = module.__name__.split(".")[-1]

    if module_name == "matrix_utils":
        # Set Matrix-specific configuration
        if hasattr(module, "matrix_rooms") and "matrix_rooms" in passed_config:
            module.matrix_rooms = passed_config["matrix_rooms"]

        # Only set matrix config variables if matrix section exists and has required fields
        # When using credentials.json (from mmrelay auth login), these will be loaded by connect_matrix() instead
        matrix_section = passed_config.get(CONFIG_SECTION_MATRIX)
        if (
            hasattr(module, "matrix_homeserver")
            and isinstance(matrix_section, dict)
            and CONFIG_KEY_HOMESERVER in matrix_section
            and CONFIG_KEY_ACCESS_TOKEN in matrix_section
            and CONFIG_KEY_BOT_USER_ID in matrix_section
        ):
            module.matrix_homeserver = matrix_section[CONFIG_KEY_HOMESERVER]
            module.matrix_access_token = matrix_section[CONFIG_KEY_ACCESS_TOKEN]
            module.bot_user_id = matrix_section[CONFIG_KEY_BOT_USER_ID]

    elif module_name == "meshtastic_utils":
        # Set Meshtastic-specific configuration
        if hasattr(module, "matrix_rooms") and "matrix_rooms" in passed_config:
            module.matrix_rooms = passed_config["matrix_rooms"]

    # If the module still has a setup_config function, call it for backward compatibility
    if hasattr(module, "setup_config") and callable(module.setup_config):
        module.setup_config()

    return passed_config


def load_config(
    config_file: str | None = None,
    args: Any = None,
    config_paths: list[str] | None = None,
) -> dict[str, Any]:
    """
    Load the application configuration from a YAML file or from environment variables.

    If `config_file` is provided and readable, that file is used; otherwise candidate locations from `config_paths` or `get_config_paths(args)` are searched in order and the first readable YAML file is loaded. Empty or null YAML content is treated as an empty dictionary. Environment-derived overrides are merged into the loaded configuration. The function updates the module-level `relay_config` and `config_path` to reflect the resulting configuration source.

    Parameters:
        config_file (str | None): Path to a specific YAML configuration file to load. If `None`, candidate paths from `config_paths` or `get_config_paths(args)` are used.
        args: Parsed command-line arguments forwarded to `get_config_paths()` to influence search order when `config_paths` is not provided.
        config_paths: Optional list of config paths to search instead of calling `get_config_paths(args)`.

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
    if config_paths is None:
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
        elif os.path.isdir(path):
            logger.warning(
                f"Candidate configuration path is a directory, skipping: {path}"
            )

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


def _resolve_credentials_path(
    path_override: str | None, *, allow_relay_config_sources: bool
) -> tuple[str, str]:
    """
    Determine the filesystem path to credentials.json and the directory that will contain it.

    Parameters:
        path_override (str | None): Explicit path or directory provided by the caller. If this is a directory (or ends with a path separator), `credentials.json` will be appended.
        allow_relay_config_sources (bool): When True, also consider the MMRELAY_CREDENTIALS_PATH environment variable and `relay_config` keys `credentials_path` and `matrix.credentials_path` as possible overrides.

    Returns:
        tuple[str, str]: A pair (credentials_path, config_dir) where `credentials_path` is the resolved absolute path to `credentials.json` and `config_dir` is the directory that contains it.
    """
    candidate = path_override

    if not candidate and allow_relay_config_sources:
        candidate = os.getenv("MMRELAY_CREDENTIALS_PATH")
        if not candidate:
            candidate = relay_config.get("credentials_path")
        if not candidate:
            matrix_config = relay_config.get("matrix", {})
            if isinstance(matrix_config, dict):
                candidate = matrix_config.get("credentials_path")

    if candidate:
        candidate = os.path.abspath(os.path.expanduser(candidate))
        path_is_dir = os.path.isdir(candidate)
        if not path_is_dir:
            path_is_dir = bool(
                candidate.endswith(os.path.sep)
                or (os.path.altsep and candidate.endswith(os.path.altsep))
            )
        if path_is_dir:
            candidate = os.path.join(candidate, CREDENTIALS_FILENAME)
        config_dir = os.path.dirname(candidate)
        if not config_dir:
            config_dir = str(get_home_dir())
            candidate = os.path.join(config_dir, os.path.basename(candidate))
        return candidate, config_dir

    base_dir = str(get_home_dir())
    matrix_dir = os.path.join(base_dir, MATRIX_DIRNAME)
    return os.path.join(matrix_dir, CREDENTIALS_FILENAME), matrix_dir


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
