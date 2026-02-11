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

from mmrelay.constants.app import (
    APP_AUTHOR,
    APP_NAME,
    CREDENTIALS_FILENAME,
    MATRIX_DIRNAME,
    STORE_DIRNAME,
    WINDOWS_INSTALLER_DIR_NAME,
)
from mmrelay.log_utils import get_logger


class E2EENotSupportedError(RuntimeError):
    """E2EE not supported on Windows."""

    def __init__(self) -> None:
        """
        Initialize the exception indicating E2EE is unsupported on Windows.

        The exception message is set to "E2EE not supported on Windows".
        """
        super().__init__("E2EE not supported on Windows")


class UnknownPluginTypeError(ValueError):
    """Unknown plugin_type passed to path resolver."""

    def __init__(self, plugin_type: str | None) -> None:
        """
        Create an UnknownPluginTypeError for an unrecognized plugin type.

        Parameters:
            plugin_type (str | None): The unrecognized plugin type included in the exception message.
        """
        super().__init__(f"Unknown plugin_type: {plugin_type!r}")


# Module-level logger
logger = get_logger("paths")

# Global override set from CLI arguments
_home_override: str | None = None
_home_override_source: str | None = None

# Track whether we've already emitted deprecation warnings to avoid duplicates
_deprecation_warning_shown = False


def _reset_deprecation_warning_flag() -> None:
    """
    Reset the deprecation warning flag for testing purposes.

    This is an internal function used by tests to ensure each test
    starts with a clean deprecation warning state.
    """
    global _deprecation_warning_shown
    _deprecation_warning_shown = False


def _has_mmrelay_artifacts(root: Path) -> bool:
    """
    Detect whether a directory contains indicators of an MMRelay data store.

    Performs lightweight existence checks for common MMRelay artifacts (config, credentials,
    or known database file locations) to avoid false positives.

    Parameters:
        root (Path): Directory to inspect for MMRelay artifacts.

    Returns:
        True if any known MMRelay artifact is present in `root`, False otherwise.
    """
    candidates = [
        root / "config.yaml",
        root / "credentials.json",
        root / "matrix" / "credentials.json",
        root / "meshtastic.sqlite",
        root / "data" / "meshtastic.sqlite",
        root / "database" / "meshtastic.sqlite",
        root / "store",
        root / "matrix" / "store",
    ]
    return any(candidate.exists() for candidate in candidates)


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
    """
    Clear any CLI-provided home directory override and its recorded source.

    After calling this, path resolution will no longer use a previously set CLI override.
    """
    global _home_override, _home_override_source
    _home_override = None
    _home_override_source = None


def get_home_dir() -> Path:
    """
    Resolve the application home directory using CLI override, environment variables, and platform defaults.

    Resolution precedence: 1) CLI override set by set_home_override(), 2) MMRELAY_HOME environment variable, 3) legacy environment variables MMRELAY_BASE_DIR / MMRELAY_DATA_DIR (during the deprecation window), 4) platform defaults(~/.mmrelay on Linux/macOS, OS user data dir on Windows). Emits deprecation warnings when legacy environment variables are detected or ignored.

    Returns:
        Path: The resolved application home directory.
    """
    global _deprecation_warning_shown

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
        if legacy_vars and not _deprecation_warning_shown:
            logger.warning(
                "MMRELAY_HOME is set; ignoring legacy environment variable(s): %s. "
                "Support will be removed in v1.4.",
                ", ".join(legacy_vars),
            )
            _deprecation_warning_shown = True
        return Path(env_home).expanduser().absolute()

    # Deprecation window: check legacy environment variables
    env_base_dir = os.getenv("MMRELAY_BASE_DIR")
    env_data_dir = os.getenv("MMRELAY_DATA_DIR")

    if env_base_dir and env_data_dir:
        if not _deprecation_warning_shown:
            logger.warning(
                "Both MMRELAY_BASE_DIR and MMRELAY_DATA_DIR are set. "
                "Preferring MMRELAY_BASE_DIR and ignoring MMRELAY_DATA_DIR. "
                "Support will be removed in v1.4."
            )
            _deprecation_warning_shown = True
        return Path(env_base_dir).expanduser().absolute()

    if env_base_dir:
        if not _deprecation_warning_shown:
            logger.warning(
                "Deprecated environment variable MMRELAY_BASE_DIR is set. "
                "Use MMRELAY_HOME instead. "
                "Support will be removed in v1.4."
            )
            _deprecation_warning_shown = True
        return Path(env_base_dir).expanduser().absolute()

    if env_data_dir:
        if not _deprecation_warning_shown:
            logger.warning(
                "Deprecated environment variable MMRELAY_DATA_DIR is set. "
                "Use MMRELAY_HOME instead. "
                "Support will be removed in v1.4."
            )
            _deprecation_warning_shown = True
        return Path(env_data_dir).expanduser().absolute()

    # Platform defaults
    if sys.platform != "win32":
        return Path.home() / f".{APP_NAME}"
    else:  # Windows
        # Check if Windows installer path exists with MMRelay data
        # This takes precedence over platformdirs for Inno Setup users
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            installer_path = (
                Path(local_app_data) / "Programs" / WINDOWS_INSTALLER_DIR_NAME
            )
            # Check if this looks like an MMRelay installation with data
            if installer_path.exists() and _has_mmrelay_artifacts(installer_path):
                return installer_path

        # Fall back to platformdirs default
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
    home = get_home_dir()
    config_path = home / "config.yaml"
    if config_path not in candidates:
        candidates.append(config_path)

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

    # 5. Platform-specific legacy user data dir (e.g., Windows AppData)
    # This handles configs from older Windows installations
    try:
        platform_user_data = Path(platformdirs.user_data_dir(APP_NAME, APP_AUTHOR))
        if platform_user_data != home and platform_user_data.exists():
            if (platform_user_data / "config.yaml") not in candidates:
                candidates.append(platform_user_data / "config.yaml")
    except (OSError, RuntimeError):
        # platformdirs may fail in some environments, skip it
        pass

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
    return home / MATRIX_DIRNAME / CREDENTIALS_FILENAME


def get_matrix_dir() -> Path:
    """
    Get the Matrix runtime directory under the resolved application home.

    Returns:
        Path to the Matrix directory under the resolved application home.
    """
    return get_home_dir() / MATRIX_DIRNAME


def get_database_dir() -> Path:
    """
    Get the application's database directory.

    Returns:
        Path: Path to the `database` directory inside the resolved application home.
    """
    home = get_home_dir()
    return home / "database"


def get_database_path() -> Path:
    """
    Resolve the path to the application's SQLite database file.

    Returns:
        Path: Path to the file "meshtastic.sqlite" located in the application's database directory.
    """
    return get_database_dir() / "meshtastic.sqlite"


def get_logs_dir() -> Path:
    """
    Resolve the application's logs directory.

    Returns:
        Path: Path to the logs directory located inside the resolved home (home / "logs").
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

    return get_matrix_dir() / STORE_DIRNAME


def get_plugins_dir() -> Path:
    """
    Return the plugins root directory under the resolved application home.

    Returns:
        Path: Path to the plugins directory located inside the application home.
    """
    home = get_home_dir()
    return home / "plugins"


def get_custom_plugins_dir() -> Path:
    """
    Get the application's custom plugins directory path.

    Returns:
        Path: Path to the `plugins/custom` directory inside the application home.
    """
    return get_plugins_dir() / "custom"


def get_community_plugins_dir() -> Path:
    """
    Locate the community plugins directory inside the application's plugins folder.

    Returns:
        Path: The path to the community plugins directory.
    """
    return get_plugins_dir() / "community"


def get_core_plugins_dir() -> Path:
    """
    Get the path to the core plugins directory under the application's plugins root.

    Returns:
        Path: Path to the core plugins directory (MMRELAY_HOME/plugins/core).
    """
    return get_plugins_dir() / "core"


def _normalize_plugin_type(plugin_type: str | None) -> str | None:
    """
    Normalize a plugin type identifier to one of the canonical values used by the resolver.

    Parameters:
        plugin_type (str | None): The plugin type name (case-insensitive) to normalize, or `None` to indicate no type.

    Returns:
        str | None: The canonical plugin type: `'custom'`, `'community'`, or `'core'`; or `None` if `plugin_type` is `None`.

    Raises:
        UnknownPluginTypeError: If `plugin_type` is not `None` and does not match one of the accepted types.
    """
    if plugin_type is None:
        return None
    normalized = plugin_type.strip().lower()
    if normalized in {"custom", "community", "core"}:
        return normalized
    raise UnknownPluginTypeError(plugin_type)


def get_plugin_code_dir(plugin_name: str, plugin_type: str | None = None) -> Path:
    """
    Locate the filesystem path for a plugin's code directory.

    Parameters:
        plugin_name (str): Plugin directory name.
        plugin_type (str | None): One of "custom", "community", or "core". If `None`, searches the custom then community plugin roots and falls back to the bundled core plugins location.

    Returns:
        Path: Filesystem path to the plugin's code directory.
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
    Locate the Tier 2 data directory for a plugin, optionally returning a specific subdirectory.

    If `plugin_type` is "custom", "community", or "core", the corresponding plugin area is used. If `plugin_type` is omitted, the function checks the custom and community plugin areas for the named plugin and falls back to the core plugins area if not found.

    Parameters:
        plugin_name (str): Plugin identifier.
        subdir (str | None): Optional subdirectory name inside the plugin's data directory. If provided, the returned path points to this subdirectory.
        plugin_type (str | None): Optional plugin category: "custom", "community", or "core". If omitted, the function will search custom then community and fall back to core.

    Returns:
        Path: Path to the plugin's data directory, or to the specified subdirectory within it.
    """
    normalized_type = _normalize_plugin_type(plugin_type)
    if normalized_type == "custom":
        base_dir = get_custom_plugins_dir() / plugin_name
    elif normalized_type == "community":
        base_dir = get_community_plugins_dir() / plugin_name
    elif normalized_type == "core":
        base_dir = get_core_plugins_dir() / plugin_name
    else:
        for plugin_root in (get_custom_plugins_dir(), get_community_plugins_dir()):
            candidate = plugin_root / plugin_name
            if candidate.exists():
                base_dir = candidate
                break
        else:
            base_dir = get_core_plugins_dir() / plugin_name

    data_dir = base_dir / "data"
    return data_dir / subdir if subdir else data_dir


def get_plugin_database_path(plugin_name: str) -> Path:
    """
    Locate the filesystem path for a plugin-specific diagnostics database file.

    Parameters:
        plugin_name (str): Plugin identifier used to compose the filename.

    Returns:
        Path: Path to the plugin database file at <home>/database/plugin_data_{plugin_name}.
    """
    # NOTE: MMRelay stores plugin data in the main SQLite database today.
    # This helper exists for diagnostics and potential future per-plugin DB files.
    return get_home_dir() / "database" / f"plugin_data_{plugin_name}.sqlite"


def ensure_directories(*, create_missing: bool = True) -> None:
    """
    Ensure MMRelay's required filesystem directories exist.

    When `create_missing` is True, missing directories are created (with parents). When False, directories are not modified and any missing paths are reported via warnings. The set of checked directories excludes the E2EE store on Windows.
    Parameters:
        create_missing (bool): If True, create any missing directories; if False, only report missing ones.
    """

    raw_dirs = [
        get_home_dir(),
        get_matrix_dir(),
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
    Report whether the v1.3 deprecation window for legacy MMRELAY environment variables is active.

    The window is active when `MMRELAY_HOME` is not set and one or more legacy variables (e.g., `MMRELAY_BASE_DIR`, `MMRELAY_DATA_DIR`) are present in the environment. When active, a deprecation warning listing the detected legacy variables is emitted (once per process).

    Returns:
        True if the deprecation window is active, False otherwise.
    """
    global _deprecation_warning_shown

    # Check if MMRELAY_HOME is being used (new behavior)
    new_home_set = os.getenv("MMRELAY_HOME") is not None

    # If MMRELAY_HOME is NOT set, check for legacy env vars
    if not new_home_set:
        legacy_vars = get_legacy_env_vars()
        if legacy_vars:
            if not _deprecation_warning_shown:
                logger.warning(
                    "Deprecated environment variable(s) detected: %s. "
                    "Use MMRELAY_HOME instead. "
                    "Support will be removed in v1.4.",
                    ", ".join(legacy_vars),
                )
                _deprecation_warning_shown = True
            return True

    return False


def get_legacy_dirs() -> list[Path]:
    """
    List existing legacy MMRelay data directories that are distinct from the current application home.

    Searches common legacy locations in priority order (default legacy home ~/.mmrelay, platform-specific user data dir, MMRELAY_BASE_DIR, MMRELAY_DATA_DIR, and common Docker mounts) and returns those that exist on disk, differ from the resolved current home, and are de-duplicated. This function does not create or modify any files or directories.

    Returns:
        list[Path]: Ordered list of detected legacy directory paths (highest-to-lowest detection priority), excluding the current home.
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

    # 2b. Check Windows Inno Setup installer path (AppData\Local\Programs\MM Relay)
    # This is where the Windows installer puts the application and data
    if sys.platform == "win32":
        try:
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                installer_path = (
                    Path(local_app_data) / "Programs" / WINDOWS_INSTALLER_DIR_NAME
                )
                if installer_path.exists():
                    installer_str = str(installer_path.absolute())
                    if installer_str != home_str and installer_str not in seen:
                        legacy_dirs.append(installer_path)
                        seen.add(installer_str)
        except (OSError, RuntimeError):
            # Skip if we can't access the path
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
        if docker_path.exists() and _has_mmrelay_artifacts(docker_path):
            docker_str = str(docker_path.absolute())
            if docker_str != home_str and docker_str not in seen:
                legacy_dirs.append(docker_path)
                seen.add(docker_str)

    return legacy_dirs


def resolve_all_paths() -> dict[str, Any]:
    """
    Aggregate resolved application paths and related metadata into a single dictionary.

    Returns a mapping of canonical stringified paths and diagnostic metadata with the following keys:
    - home: canonical application home directory
    - matrix_dir: Matrix runtime directory
    - legacy_sources: list of detected legacy MMRelay directories (excluded if equal to home)
    - credentials_path: path to credentials.json
    - database_dir: database directory
    - store_dir: E2EE store directory or "N/A (Windows)"
    - logs_dir: logs directory
    - log_file: active log file (honors MMRELAY_LOG_PATH)
    - plugins_dir: plugins root directory
    - custom_plugins_dir: custom plugins directory
    - community_plugins_dir: community plugins directory
    - deps_dir: plugins dependencies directory (plugins_dir / "deps")
    - env_vars_detected: mapping of relevant environment variables that were present
    - cli_override: CLI override source string if set (for example "--home"), otherwise None
    - home_source: human-readable description of which input determined the home directory
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
        "matrix_dir": str(get_matrix_dir()),
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

    The returned dictionary contains the primary resolved paths, detected environment variables, CLI override/source information, and whether the legacy deprecation window is active.

    Returns:
        diagnostics (dict): Mapping with these keys:
            - "home_dir": Resolved application home directory (string).
            - "credentials_path": Path to credentials.json (string).
            - "database_dir": Database directory (string).
            - "database_path": Full path to the main database file (string).
            - "logs_dir": Logs directory (string).
            - "log_file": Effective log file path (string).
            - "plugins_dir": Plugins root directory (string).
            - "custom_plugins_dir": Custom plugins directory (string).
            - "community_plugins_dir": Community plugins directory (string).
            - "env_vars": Detected relevant environment variables and their values (dict).
            - "cli_override": CLI-provided home override value or source, if any (string or None).
            - "sources_used": Source chosen to determine the home directory (string).
            - "legacy_active": `True` if the legacy deprecation window is active, `False` otherwise.
    """
    # Note: resolve_all_paths() already resolves home and triggers any deprecation warnings

    resolved = resolve_all_paths()
    compat_diagnostics = {
        "home_dir": resolved["home"],
        "matrix_dir": resolved["matrix_dir"],
        "credentials_path": resolved["credentials_path"],
        "database_dir": resolved["database_dir"],
        "database_path": str(get_database_path()),
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
