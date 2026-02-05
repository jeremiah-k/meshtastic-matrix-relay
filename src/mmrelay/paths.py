"""
Unified path resolution for MMRelay v1.3.

This module provides a single, consistent interface for all filesystem paths,
replacing the dual layout (base_dir/data_dir) and legacy/new detection
with a unified MMRELAY_HOME concept.

All paths are now derived from a single source of truth:
- Environment variable: MMRELAY_HOME
- CLI argument: --home <path>
- Platform defaults: ~/.mmrelay (Linux/macOS), platformdirs.user_data_dir() (Windows)

The three-tier plugin data system:
- Tier 1: Plugin code location
- Tier 2: Plugin filesystem data (disk storage)
- Tier 3: Plugin database data (SQLite, default)
"""

import os
import sys
from pathlib import Path
from typing import Any

import platformdirs

from mmrelay.constants.app import APP_AUTHOR, APP_NAME
from mmrelay.log_utils import get_logger


class E2EENotSupportedError(RuntimeError):
    """E2EE not supported on Windows."""

    def __init__(self) -> None:
        """Create an E2EENotSupportedError with a descriptive message."""
        super().__init__("E2EE not supported on Windows")


class UnknownPluginTypeError(ValueError):
    """Unknown plugin_type passed to path resolver."""

    def __init__(self, plugin_type: str | None) -> None:
        super().__init__(f"Unknown plugin_type: {plugin_type!r}")


# Global override set from CLI arguments
_home_override: str | None = None
_home_override_source: str | None = None


def set_home_override(path: str, *, source: str | None = None) -> None:
    """
    Store a CLI-provided application home path and its source as the module-level override used by path resolution.

    Parameters:
        path (str): The user-specified home directory path.
        source (str | None): Optional identifier of the override source (e.g., "--home", "--base-dir"); may be None.
    """
    global _home_override, _home_override_source
    _home_override = path
    _home_override_source = source


def reset_home_override() -> None:
    """Reset home directory override (primarily for tests)."""
    global _home_override, _home_override_source
    _home_override = None
    _home_override_source = None


def get_home_dir() -> Path:
    """
    Get application home directory (single source of truth).

    Resolution order (during deprecation window):
        1. --home CLI argument
        2. MMRELAY_HOME environment variable
        3. MMRELAY_BASE_DIR environment variable (legacy, deprecation warning)
        4. MMRELAY_DATA_DIR environment variable (legacy, strong deprecation warning)
        5. Platform defaults (~/.mmrelay on Linux/macOS, platformdirs on Windows)

    After deprecation window:
        1. --home CLI argument
        2. MMRELAY_HOME environment variable
        3. Platform defaults (~/.mmrelay on Linux/macOS, platformdirs on Windows)

    Returns:
        Path: Application home directory.
    """
    _logger = get_logger("paths")

    # Check CLI override first
    if _home_override:
        return Path(_home_override).expanduser().absolute()

    # Check new MMRELAY_HOME environment variable
    env_home = os.getenv("MMRELAY_HOME")
    if env_home:
        # Check if legacy env vars also exist - warn they're ignored
        legacy_vars = []
        if os.getenv("MMRELAY_BASE_DIR"):
            legacy_vars.append("MMRELAY_BASE_DIR")
        if os.getenv("MMRELAY_DATA_DIR"):
            legacy_vars.append("MMRELAY_DATA_DIR")
        if legacy_vars:
            _logger.warning(
                "MMRELAY_HOME is set; ignoring legacy environment variable(s): %s. "
                "Support will be removed in v1.4.",
                ", ".join(legacy_vars),
            )
        return Path(env_home).expanduser().absolute()

    # Deprecation window: check legacy environment variables
    env_base_dir = os.getenv("MMRELAY_BASE_DIR")
    env_data_dir = os.getenv("MMRELAY_DATA_DIR")

    if env_base_dir and env_data_dir:
        _logger.warning(
            "Both MMRELAY_BASE_DIR and MMRELAY_DATA_DIR are set. "
            "Preferring MMRELAY_BASE_DIR and ignoring MMRELAY_DATA_DIR. "
            "Support will be removed in v1.4."
        )
        return Path(env_base_dir).expanduser().absolute()

    if env_base_dir:
        _logger.warning(
            "Deprecated environment variable MMRELAY_BASE_DIR is set. "
            "Use MMRELAY_HOME instead. "
            "Support will be removed in v1.4."
        )
        return Path(env_base_dir).expanduser().absolute()

    if env_data_dir:
        _logger.warning(
            "Deprecated environment variable MMRELAY_DATA_DIR is set. "
            "Use MMRELAY_HOME instead. "
            "Support will be removed in v1.4."
        )
        return Path(env_data_dir).expanduser().absolute()

    # Platform defaults
    if sys.platform in ["linux", "darwin"]:
        return Path.home() / f".{APP_NAME}"
    else:  # Windows
        return Path(platformdirs.user_data_dir(APP_NAME, APP_AUTHOR))


def get_config_paths(*, explicit: str | None = None) -> list[Path]:
    """
    Produce an ordered list of candidate config.yaml file locations to try, from highest to lowest priority.

    Order:
      1. Explicit CLI path (if provided) — always included first, even if the file does not exist.
      2. MMRELAY_HOME/config.yaml (skipped when an explicit path is provided).
      3. ./config.yaml in the current working directory (skipped if identical to home).
      4. Legacy ~/.{APP_NAME}/config.yaml — included only if the directory exists and is not equal to the resolved home.

    Parameters:
        explicit (str | None): Optional explicit config file path from the CLI; when provided it is added first.

    Returns:
        list[Path]: Ordered, de-duplicated list of candidate config file paths (highest to lowest priority).
    """
    candidates = []

    # 1. Explicit CLI argument - ALWAYS add as first candidate, even if it doesn't exist
    # This allows downstream code to report "file not found" errors appropriately
    # rather than silently falling back to other locations.
    if explicit:
        explicit_path = Path(explicit).expanduser().absolute()
        candidates.append(explicit_path)

    # 2. MMRELAY_HOME/config.yaml
    # Always resolve home for use in later checks
    home = get_home_dir()
    if not explicit:
        config_path = home / "config.yaml"
        if config_path not in candidates:
            candidates.append(config_path)

    if not explicit:
        # 3. Current working directory (fallback)
        cwd = Path.cwd()
        cwd_config = cwd / "config.yaml"
        if cwd != home and cwd_config not in candidates:
            candidates.append(cwd_config)

        # 4. Legacy locations (deprecation window)
        # These are searched for migration purposes
        legacy_home = Path.home() / f".{APP_NAME}"
        if legacy_home != home and legacy_home.exists():
            if (legacy_home / "config.yaml") not in candidates:
                candidates.append(legacy_home / "config.yaml")

    # Remove duplicates while preserving order
    seen = set()
    unique_candidates = []
    for path in candidates:
        path_str = str(path.absolute())
        if path_str not in seen:
            unique_candidates.append(path)
            seen.add(path_str)

    return unique_candidates


def get_credentials_path() -> Path:
    """
    Resolve the credentials.json file path inside the application home directory.

    Returns:
        Path: Path to the credentials.json file located in the resolved application home directory.
    """
    home = get_home_dir()
    return home / "credentials.json"


def get_database_dir() -> Path:
    """
    Return the path to the application's database directory.

    Returns:
        Path: Path pointing to the `database` directory inside the resolved application home.
    """
    home = get_home_dir()
    return home / "database"


def get_database_path() -> Path:
    """
    Return the file path for the application's SQLite database.

    Returns:
        Path: Path to the SQLite file "meshtastic.sqlite" located in the application's database directory.
    """
    return get_database_dir() / "meshtastic.sqlite"


def get_logs_dir() -> Path:
    """
    Get the application's logs directory located under the resolved home.

    Returns:
        Path: Path to the logs directory (home / "logs").
    """
    home = get_home_dir()
    return home / "logs"


def get_log_file() -> Path:
    """
    Determine the filesystem path for the application's log file.

    If the MMRELAY_LOG_PATH environment variable is set, that path is used (expanded and made absolute). Otherwise the function returns the default log file path inside the configured logs directory: `<logs_dir>/mmrelay.log`.

    Returns:
        Path: Path to the log file; `MMRELAY_LOG_PATH` if set, otherwise the default logs directory file.
    """
    env_log = os.getenv("MMRELAY_LOG_PATH")
    if env_log:
        return Path(env_log).expanduser().absolute()
    return get_logs_dir() / "mmrelay.log"


def get_e2ee_store_dir() -> Path:
    """
    Directory for storing end-to-end encryption (E2EE) keys.

    Only available on Unix-like platforms; calling this on Windows raises an error.

    Returns:
        Path: Path to E2EE key store directory.

    Raises:
        E2EENotSupportedError: If invoked on Windows (E2EE is not supported on Windows).
    """
    if sys.platform == "win32":
        raise E2EENotSupportedError()

    home = get_home_dir()
    return home / "store"


def get_plugins_dir() -> Path:
    """
    Resolve the plugins root directory within the application home.

    Returns:
        The path to the plugins directory located under the application home.
    """
    home = get_home_dir()
    return home / "plugins"


def get_custom_plugins_dir() -> Path:
    """
    Get custom plugins directory.

    Returns:
        Path: Custom plugins directory.
    """
    return get_plugins_dir() / "custom"


def get_community_plugins_dir() -> Path:
    """
    Return the community plugins directory within the application's plugins folder.

    Returns:
        Path: Path to the community plugins directory.
    """
    return get_plugins_dir() / "community"


def get_core_plugins_dir() -> Path:
    """
    Return the directory used for core plugin data storage under MMRELAY_HOME.

    Core plugins are bundled with the application code, but any filesystem data
    they write should live under the unified plugins tree to keep runtime state
    in MMRELAY_HOME.
    """
    return get_plugins_dir() / "core"


def _normalize_plugin_type(plugin_type: str | None) -> str | None:
    if plugin_type is None:
        return None
    normalized = plugin_type.strip().lower()
    if normalized in {"custom", "community", "core"}:
        return normalized
    raise UnknownPluginTypeError(plugin_type)


def get_plugin_code_dir(plugin_name: str, plugin_type: str | None = None) -> Path:
    """
    Locate the Tier 1 code directory for a plugin.

    Args:
        plugin_name: Plugin name (directory name under the plugins directory).
        plugin_type: Plugin category ("custom", "community", or "core"). If omitted,
            the function will try to locate the plugin under custom/community
            directories and fall back to the bundled core plugins path.

    Returns:
        Path to the plugin's code directory.
    """
    normalized_type = _normalize_plugin_type(plugin_type)
    if normalized_type == "custom":
        return get_custom_plugins_dir() / plugin_name
    if normalized_type == "community":
        return get_community_plugins_dir() / plugin_name
    if normalized_type == "core":
        return Path(__file__).resolve().parent / "plugins" / plugin_name

    for plugin_root in (get_custom_plugins_dir(), get_community_plugins_dir()):
        candidate = plugin_root / plugin_name
        if candidate.exists():
            return candidate

    return Path(__file__).resolve().parent / "plugins" / plugin_name


def get_plugin_data_dir(
    plugin_name: str, subdir: str | None = None, plugin_type: str | None = None
) -> Path:
    """
    Return the filesystem path for a plugin's Tier 2 data directory.

    Parameters:
        plugin_name (str): Plugin identifier.
        subdir (str | None): Optional subdirectory name inside the plugin's data directory.
        plugin_type (str | None): Plugin category ("custom", "community", or "core").
            When omitted, the function will try to resolve the plugin under
            custom/community directories and fall back to the core plugins area.

    Returns:
        Path: Path to the plugin's data directory (filesystem Tier 2 path).
    """
    normalized_type = _normalize_plugin_type(plugin_type)
    if normalized_type == "custom":
        base_dir = get_custom_plugins_dir() / plugin_name
    elif normalized_type == "community":
        base_dir = get_community_plugins_dir() / plugin_name
    elif normalized_type == "core":
        base_dir = get_core_plugins_dir() / plugin_name
    else:
        base_dir = None
        for plugin_root in (get_custom_plugins_dir(), get_community_plugins_dir()):
            candidate = plugin_root / plugin_name
            if candidate.exists():
                base_dir = candidate
                break
        if base_dir is None:
            base_dir = get_core_plugins_dir() / plugin_name

    data_dir = base_dir / "data"
    return data_dir / subdir if subdir else data_dir


def get_plugin_database_path(plugin_name: str) -> Path:
    """
    Provide the filesystem path for the central database file used by a plugin.

    Parameters:
        plugin_name (str): Plugin identifier.

    Returns:
        Path: Path to a plugin-specific database file (diagnostic only).
    """
    # NOTE: MMRelay stores plugin data in the main SQLite database today.
    # This helper exists for diagnostics and potential future per-plugin DB files.
    return get_home_dir() / "database" / f"plugin_data_{plugin_name}"


def ensure_directories(*, create_missing: bool = True) -> None:
    """
    Ensure required MMRelay directories exist.

    Creates any missing directories when `create_missing` is True and logs failures without raising.
    When `create_missing` is False, does not modify the filesystem and logs a warning for each missing directory.
    The set of directories excludes the E2EE store on Windows.

    Parameters:
        create_missing (bool): If True, create missing directories; if False, only report missing ones.
    """

    raw_dirs = [
        get_home_dir(),
        get_database_dir(),
        get_logs_dir(),
        get_e2ee_store_dir() if sys.platform != "win32" else None,
        get_plugins_dir(),
        get_custom_plugins_dir(),
        get_community_plugins_dir(),
        get_core_plugins_dir(),
    ]

    # Filter out None values to prevent crashes on Windows
    dirs_to_ensure: list[Path] = [d for d in raw_dirs if d is not None]

    logger = get_logger("paths")
    for dir_path in dirs_to_ensure:
        if create_missing:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
                logger.debug("Created directory: %s", dir_path)
            except OSError:
                logger.exception("Failed to create directory %s", dir_path)
        else:
            if not dir_path.exists():
                logger.warning("Directory missing: %s", dir_path)


def get_legacy_env_vars() -> list[str]:
    """
    List deprecated MMRELAY environment variable names that are currently set.

    Returns:
        list[str]: Names of deprecated environment variables (`MMRELAY_BASE_DIR`, `MMRELAY_DATA_DIR`) that exist in the current environment.
    """
    legacy_vars = []

    # Check for legacy environment variables
    for var in ["MMRELAY_BASE_DIR", "MMRELAY_DATA_DIR"]:
        if os.getenv(var):
            legacy_vars.append(var)

    return legacy_vars


def is_deprecation_window_active() -> bool:
    """
    Determine whether the v1.3 deprecation window for legacy environment variables is active.

    The window is active when the new `MMRELAY_HOME` environment variable is not set and one or more legacy variables (e.g., `MMRELAY_BASE_DIR`, `MMRELAY_DATA_DIR`) are present in the environment.

    Returns:
        True if the deprecation window is active, False otherwise.
    """
    # Check if MMRELAY_HOME is being used (new behavior)
    new_home_set = os.getenv("MMRELAY_HOME") is not None

    # If MMRELAY_HOME is NOT set, check for legacy env vars
    if not new_home_set:
        legacy_vars = get_legacy_env_vars()
        if legacy_vars:
            _logger = get_logger("paths")
            _logger.warning(
                "Deprecated environment variable(s) detected: %s. "
                "Use MMRELAY_HOME instead. "
                "Support will be removed in v1.4.",
                ", ".join(legacy_vars),
            )
            return True

    return False


def get_legacy_dirs() -> list[Path]:
    """
    Return existing legacy MMRelay data directories that are distinct from the current home.

    Considers the following legacy sources (if present): ~/.mmrelay, platform-specific user data dir for the app, the MMRELAY_BASE_DIR and MMRELAY_DATA_DIR environment variables, and common Docker mounts (/data, /app/data, /var/lib/mmrelay). Only directories that exist on disk, are not equal to the resolved current home directory, and are not duplicates are returned. This function does not create or modify any files or directories.

    Returns:
        list[Path]: Ordered list of existing legacy directory paths (highest-to-lowest detection priority), de-duplicated and excluding the current home.
    """
    legacy_dirs: list[Path] = []
    seen: set[str] = set()

    # Get current HOME to exclude it from legacy list
    try:
        home = get_home_dir()
        home_str = str(home)
    except (OSError, RuntimeError):
        # If we can't resolve HOME, we can't filter properly
        # Just return empty list to avoid false positives
        return []

    # 1. Check legacy default home: ~/.mmrelay
    default_legacy = Path.home() / f".{APP_NAME}"
    if default_legacy.exists():
        legacy_str = str(default_legacy.absolute())
        if legacy_str != home_str and legacy_str not in seen:
            legacy_dirs.append(default_legacy)
            seen.add(legacy_str)

    # 2. Check platformdirs.user_data_dir() (Windows or platform-specific)
    try:
        platform_user_data = Path(platformdirs.user_data_dir(APP_NAME, APP_AUTHOR))
        if platform_user_data.exists():
            platform_str = str(platform_user_data.absolute())
            if platform_str != home_str and platform_str not in seen:
                legacy_dirs.append(platform_user_data)
                seen.add(platform_str)
    except (OSError, RuntimeError):
        # platformdirs may fail in some environments, skip it
        pass

    # 3. Check legacy MMRELAY_BASE_DIR environment variable
    env_base_dir = os.getenv("MMRELAY_BASE_DIR")
    if env_base_dir:
        base_path = Path(env_base_dir).expanduser().absolute()
        if base_path.exists():
            base_str = str(base_path)
            if base_str != home_str and base_str not in seen:
                legacy_dirs.append(base_path)
                seen.add(base_str)

    # 4. Check legacy MMRELAY_DATA_DIR environment variable
    env_data_dir = os.getenv("MMRELAY_DATA_DIR")
    if env_data_dir:
        data_path = Path(env_data_dir).expanduser().absolute()
        if data_path.exists():
            data_str = str(data_path)
            if data_str != home_str and data_str not in seen:
                legacy_dirs.append(data_path)
                seen.add(data_str)

    # 5. Check common Docker legacy mounts
    # These are common volume mount points in Docker deployments
    docker_legacy_paths = [
        Path("/data"),
        Path("/app/data"),
        Path("/var/lib/mmrelay"),
    ]
    for docker_path in docker_legacy_paths:
        if docker_path.exists():
            docker_str = str(docker_path.absolute())
            if docker_str != home_str and docker_str not in seen:
                legacy_dirs.append(docker_path)
                seen.add(docker_str)

    return legacy_dirs


def resolve_all_paths() -> dict[str, Any]:
    """
    Aggregate resolved application paths and related metadata into a single dictionary.

    The returned dictionary contains canonical paths (stringified) and metadata used for diagnostics and tooling. Keys:
    - home: canonical application home directory
    - legacy_sources: list of existing legacy directories (not equal to home)
    - credentials_path: path to credentials.json
    - database_dir: path to the database directory
    - store_dir: path to the E2EE store directory or "N/A (Windows)"
    - logs_dir: path to the logs directory
    - log_file: path to the active log file (respecting MMRELAY_LOG_PATH if set)
    - plugins_dir: path to the plugins root
    - custom_plugins_dir: path to the custom plugins directory
    - community_plugins_dir: path to the community plugins directory
    - deps_dir: path to the plugins dependencies directory (plugins_dir / "deps")
    - env_vars_detected: mapping of relevant environment variables that were present
    - cli_override: CLI override source string if set (e.g. "--home"), otherwise None
    - home_source: human-readable description of which input determined the home directory

    Returns:
        dict: Mapping of the keys above to their resolved string values or metadata.
    """
    home = get_home_dir()
    legacy_dirs = get_legacy_dirs()

    # Detect environment variables
    env_vars_detected: dict[str, str] = {}
    env_home = os.getenv("MMRELAY_HOME")
    if env_home is not None:
        env_vars_detected["MMRELAY_HOME"] = env_home
    env_base = os.getenv("MMRELAY_BASE_DIR")
    if env_base is not None:
        env_vars_detected["MMRELAY_BASE_DIR"] = env_base
    env_data = os.getenv("MMRELAY_DATA_DIR")
    if env_data is not None:
        env_vars_detected["MMRELAY_DATA_DIR"] = env_data
    env_log = os.getenv("MMRELAY_LOG_PATH")
    if env_log is not None:
        env_vars_detected["MMRELAY_LOG_PATH"] = env_log

    cli_override: str | None = _home_override_source

    # Determine home source
    home_source: str
    if cli_override == "--home":
        home_source = "CLI (--home)"
    elif cli_override == "--base-dir":
        home_source = "CLI (--base-dir)"
    elif cli_override == "--data-dir":
        home_source = "CLI (--data-dir)"
    elif "MMRELAY_HOME" in env_vars_detected:
        home_source = "MMRELAY_HOME env var"
    elif "MMRELAY_BASE_DIR" in env_vars_detected:
        home_source = "MMRELAY_BASE_DIR env var"
    elif "MMRELAY_DATA_DIR" in env_vars_detected:
        home_source = "MMRELAY_DATA_DIR env var"
    else:
        home_source = "Platform defaults"

    return {
        "home": str(home),
        "legacy_sources": [str(d) for d in legacy_dirs],
        "credentials_path": str(get_credentials_path()),
        "database_dir": str(get_database_dir()),
        "store_dir": (
            str(get_e2ee_store_dir()) if sys.platform != "win32" else "N/A (Windows)"
        ),
        "logs_dir": str(get_logs_dir()),
        "log_file": str(get_log_file()),
        "plugins_dir": str(get_plugins_dir()),
        "custom_plugins_dir": str(get_custom_plugins_dir()),
        "community_plugins_dir": str(get_community_plugins_dir()),
        "deps_dir": str(get_plugins_dir() / "deps"),
        "env_vars_detected": env_vars_detected,
        "cli_override": cli_override,
        "home_source": home_source,
    }


def get_diagnostics() -> dict[str, Any]:
    """
    Produce a diagnostic snapshot of resolved application paths and environment state.

    Returns:
        diagnostics (dict): Mapping of diagnostic keys to their values. Keys include:
            - "home_dir": Resolved application home directory (string).
            - "credentials_path": Path to credentials.json (string).
            - "database_dir": Database directory (string).
            - "database_path": Full path to the main database file (string).
            - "logs_dir": Logs directory (string).
            - "log_file": Effective log file path (string).
            - "plugins_dir": Plugins directory (string).
            - "custom_plugins_dir": Custom plugins directory (string).
            - "community_plugins_dir": Community plugins directory (string).
            - "env_vars": Detected relevant environment variables and their values (dict).
            - "cli_override": CLI-provided override source/value, if any (string or None).
            - "sources_used": Source chosen to determine the home directory (string).
            - "legacy_active": `True` if the legacy deprecation window is active, `False` otherwise.
    """
    # Note: resolve_all_paths() already resolves home and triggers any deprecation warnings

    resolved = resolve_all_paths()
    compat_diagnostics = {
        "home_dir": resolved["home"],
        "credentials_path": resolved["credentials_path"],
        "database_dir": resolved["database_dir"],
        "database_path": str(Path(resolved["database_dir"]) / "meshtastic.sqlite"),
        "logs_dir": resolved["logs_dir"],
        "log_file": resolved["log_file"],
        "plugins_dir": resolved["plugins_dir"],
        "custom_plugins_dir": resolved["custom_plugins_dir"],
        "community_plugins_dir": resolved["community_plugins_dir"],
        "env_vars": resolved.get("env_vars_detected", {}),
        "cli_override": resolved.get("cli_override"),
        "sources_used": resolved.get("home_source"),
        "legacy_active": is_deprecation_window_active(),
    }

    return compat_diagnostics
