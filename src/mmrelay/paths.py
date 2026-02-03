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

# Global override set from CLI arguments
_home_override: str | None = None
_home_override_source: str | None = None


def set_home_override(path: str, *, source: str | None = None) -> None:
    """Set home directory override from CLI arguments.

    Args:
        path: User-specified home directory path.
        source: Source of the override (e.g., "--home", "--base-dir", "--data-dir").
    """
    global _home_override, _home_override_source
    _home_override = path
    _home_override_source = source


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
    Get prioritized list of candidate config file paths.

    Search order (highest to lowest):
        1. Explicit path from --config argument (always included, even if doesn't exist)
        2. MMRELAY_HOME/config.yaml
        3. Current directory (fallback)
        4. Legacy locations (for deprecation window)

    Args:
        explicit: Optional explicit config file path from CLI.

    Returns:
        list[Path]: Ordered, de-duplicated list of candidate paths.
    """
    candidates = []

    # 1. Explicit CLI argument - ALWAYS add as first candidate, even if it doesn't exist
    # This allows downstream code to report "file not found" errors appropriately
    # rather than silently falling back to other locations.
    if explicit:
        explicit_path = Path(explicit).absolute()
        candidates.append(explicit_path)

    # 2. MMRELAY_HOME/config.yaml
    # Always resolve home for use in later checks
    home = get_home_dir()
    if not explicit:
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
    Get credentials file path (single location).

    Returns:
        Path: Location of credentials.json.
    """
    home = get_home_dir()
    return home / "credentials.json"


def get_database_dir() -> Path:
    """
    Get database directory.

    Returns:
        Path: Database directory location.
    """
    home = get_home_dir()
    return home / "database"


def get_database_path() -> Path:
    """
    Get SQLite database file path.

    Returns:
        Path: Database file location.
    """
    return get_database_dir() / "meshtastic.sqlite"


def get_logs_dir() -> Path:
    """
    Get logs directory.

    Returns:
        Path: Logs directory location.
    """
    home = get_home_dir()
    return home / "logs"


def get_log_file() -> Path:
    """
    Get log file path.

    Environment variable override: MMRELAY_LOG_PATH

    Returns:
        Path: Log file location.
    """
    env_log = os.getenv("MMRELAY_LOG_PATH")
    if env_log:
        return Path(env_log).expanduser().absolute()
    return get_logs_dir() / "mmrelay.log"


def get_e2ee_store_dir() -> Path:
    """
    Get E2EE encryption key store directory.

    Unix/macOS only - E2EE not available on Windows.

    Returns:
        Path: E2EE store directory location.
    """
    if sys.platform == "win32":
        raise RuntimeError("E2EE not supported on Windows")

    home = get_home_dir()
    return home / "store"


def get_plugins_dir() -> Path:
    """
    Get plugins root directory.

    Returns:
        Path: Plugins root directory.
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
    Get community plugins directory.

    Returns:
        Path: Community plugins directory.
    """
    return get_plugins_dir() / "community"


def get_plugin_code_dir(plugin_name: str) -> Path:
    """
    Get plugin code directory (Tier 1).

    Args:
        plugin_name: Name of the plugin.

    Returns:
        Path: Directory where plugin's .py file is located.
    """
    # For custom/community plugins
    return get_plugins_dir() / plugin_name


def get_plugin_data_dir(plugin_name: str, subdir: str | None = None) -> Path:
    """
    Get plugin data directory (Tier 2 or 3).

    Three-tier plugin data system:
    - Tier 1 (Code): Where plugin .py file lives
    - Tier 2 (Filesystem): Disk storage for plugins needing it
    - Tier 3 (Database): SQLite database (default for most plugins)

    Args:
        plugin_name: Name of the plugin.
        subdir: Optional subdirectory name inside plugin data directory.

    Returns:
        Path: Plugin data directory.
    """
    if subdir:
        # Tier 2: Filesystem storage (e.g., GPX files, JSON caches)
        return get_plugins_dir() / plugin_name / "data" / subdir
    else:
        # Tier 3: Database storage (default for most plugins)
        return get_home_dir() / "database" / "plugin_data" / plugin_name


def get_plugin_database_path(plugin_name: str) -> Path:
    """
    Get path to plugin data in SQLite database.

    This is a convenience function that returns the path identifier
    for plugin data stored in the centralized database.

    Args:
        plugin_name: Name of the plugin.

    Returns:
        Path: Path identifier for plugin data in database.
    """
    # For database path representation
    return get_home_dir() / "database" / f"plugin_data_{plugin_name}"


def ensure_directories(*, create_missing: bool = True) -> None:
    """
    Ensure all required directories exist.

    Creates missing directories with appropriate permissions.
    Logs errors but does not raise on permission failures.

    Args:
        create_missing: If True, create directories that don't exist.
                        If False, only check and log missing ones.
    """

    raw_dirs = [
        get_home_dir(),
        get_database_dir(),
        get_logs_dir(),
        get_e2ee_store_dir() if sys.platform != "win32" else None,
        get_plugins_dir(),
        get_custom_plugins_dir(),
        get_community_plugins_dir(),
    ]

    # Filter out None values to prevent crashes on Windows
    dirs_to_ensure: list[Path] = [d for d in raw_dirs if d is not None]

    logger = get_logger("paths")
    for dir_path in dirs_to_ensure:
        if create_missing:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
                logger.debug("Created directory: %s", dir_path)
            except OSError as e:
                logger.error("Failed to create directory %s: %s", dir_path, e)
        else:
            if not dir_path.exists():
                logger.warning("Directory missing: %s", dir_path)


def get_legacy_env_vars() -> list[str]:
    """
    Get list of deprecated environment variables (for warnings).

    Returns:
        list[str]: Deprecated environment variable names.
    """
    legacy_vars = []

    # Check for legacy environment variables
    for var in ["MMRELAY_BASE_DIR", "MMRELAY_DATA_DIR"]:
        if os.getenv(var):
            legacy_vars.append(var)

    return legacy_vars


def is_deprecation_window_active() -> bool:
    """
    Check if deprecation window is active (v1.3).

    During v1.3, legacy environment variables are supported
    alongside the new MMRELAY_HOME variable.

    The deprecation window is active when MMRELAY_HOME is NOT set
    (so users with old configs continue to work with warnings).

    Returns:
        bool: True if deprecation window is active.
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
    Get list of legacy directories that actually exist.

    Returns directories that were used in v1.2.x or earlier layouts.
    This is the authoritative source for all legacy roots across the entire codebase.

    Legacy directories checked (in order):
        - ~/.mmrelay (default legacy home)
        - platformdirs.user_data_dir(APP_NAME) (Windows or platform-specific locations)
        - MMRELAY_BASE_DIR environment variable (legacy)
        - MMRELAY_DATA_DIR environment variable (legacy)
        - Common Docker legacy mounts:
            - /data
            - /app/data
            - /var/lib/mmrelay

    Only returns directories that:
        1. Actually exist on the filesystem
        2. Are not equal to the current HOME directory
        3. Are deduplicated

    This function is READ-ONLY and does NOT create directories.

    Returns:
        list[Path]: List of legacy directory paths that exist.
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
    Resolve all application paths from a single source of truth.

    Returns a comprehensive dictionary with all resolved paths:
        - home: The canonical HOME directory
        - legacy_sources: List of legacy directories that exist (not equal to home)
        - credentials_path: Path to credentials.json
        - database_dir: Path to database directory
        - store_dir: Path to E2EE store directory
        - logs_dir: Path to logs directory
        - log_file: Path to log file
        - plugins_dir: Path to plugins root directory
        - deps_dir: Path to plugin dependencies directory
        - env_vars_detected: Dictionary of detected environment variables
        - cli_override: CLI override detected (--home, --base-dir, --data-dir), may be None
        - home_source: Which input determined to home directory

    Returns:
        dict: Comprehensive path resolution information.
    """
    # mypy: ignore[arg-type]  # cli_override may be None which is valid for dict[str, Any]
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
    Get comprehensive diagnostic information about path resolution and configuration.

    Returns a dictionary containing:
        - home_dir: Resolved home directory path
        - credentials_path: Path to credentials.json
        - database_dir: Database directory path
        - database_path: Full path to database file
        - logs_dir: Logs directory path
        - log_file: Effective log file path
        - plugins_dir: Plugins directory path
        - custom_plugins_dir: Custom plugins directory path
        - community_plugins_dir: Community plugins directory path
        - env_vars: Detected environment variables
        - cli_override: CLI override (--home, --base-dir, --data-dir)
        - sources_used: Which inputs determined to home directory
        - legacy_active: Whether deprecation window is active
    """
    _logger = get_logger("paths")
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
        "sources_used": resolved.get("sources_used", []),
        "legacy_active": is_deprecation_window_active(),
    }

    return compat_diagnostics
