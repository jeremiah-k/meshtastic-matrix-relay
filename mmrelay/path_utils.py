# ./mmrelay/path_utils.py:
import os
import pathlib

from platformdirs import user_config_dir, user_data_dir

# Define app name and author for platformdirs
APP_NAME = "mmrelay"
APP_AUTHOR = "mmrelay"  # Use the same for simplicity, or your author name

# --- Path Definitions ---

# ~/.config/mmrelay or platform equivalent
_USER_CONFIG_DIR = pathlib.Path(user_config_dir(APP_NAME, APP_AUTHOR))
# ~/.local/share/mmrelay or platform equivalent
_USER_DATA_DIR = pathlib.Path(user_data_dir(APP_NAME, APP_AUTHOR))

# ~/meshtastic-matrix-relay (legacy)
_LEGACY_DIR = pathlib.Path.home() / "meshtastic-matrix-relay"


def _ensure_dir_exists(dir_path: pathlib.Path):
    """Ensure the directory exists, creating it if necessary."""
    dir_path.mkdir(parents=True, exist_ok=True)


def get_config_path() -> pathlib.Path:
    """
    Find the path to config.yaml.
    Priority:
    1. Current Working Directory (./config.yaml)
    2. User config directory (~/.config/mmrelay/config.yaml or equivalent)
    3. Legacy directory (~/meshtastic-matrix-relay/config.yaml)

    If not found, returns the path for the user config directory (for potential creation).
    """
    # 1. Check CWD
    cwd_path = pathlib.Path.cwd() / "config.yaml"
    if cwd_path.is_file():
        return cwd_path

    # 2. Check User Config Dir
    user_config_path = _USER_CONFIG_DIR / "config.yaml"
    if user_config_path.is_file():
        return user_config_path

    # 3. Check Legacy Dir
    legacy_path = _LEGACY_DIR / "config.yaml"
    if legacy_path.is_file():
        return legacy_path

    # 4. Default to User Config Dir (even if it doesn't exist yet)
    # Ensure the parent directory exists for potential creation by caller
    _ensure_dir_exists(_USER_CONFIG_DIR)
    return user_config_path


def get_data_dir() -> pathlib.Path:
    """
    Find the data directory (for db, logs, plugins, etc.).
    Priority:
    1. User data directory (~/.local/share/mmrelay or equivalent) - Check if exists
    2. Legacy directory (~/meshtastic-matrix-relay) - Check if exists

    If neither exists, creates and returns the user data directory path.
    """
    # Check if user data dir indicates an existing install (e.g., contains db or logs)
    if (
        _USER_DATA_DIR.is_dir()
        and (
            (_USER_DATA_DIR / "meshtastic.sqlite").exists()
            or (_USER_DATA_DIR / "logs").exists()
            or (_USER_DATA_DIR / "plugins").exists()
        )
    ):
        _ensure_dir_exists(_USER_DATA_DIR) # Ensure it really is a dir
        return _USER_DATA_DIR

    # Check if legacy dir indicates an existing install
    if (
        _LEGACY_DIR.is_dir()
        and (
            (_LEGACY_DIR / "meshtastic.sqlite").exists()
            or (_LEGACY_DIR / "logs").exists()
            or (_LEGACY_DIR / "plugins").exists()
        )
    ):
        return _LEGACY_DIR

    # Default: Create and return the standard user data directory
    _ensure_dir_exists(_USER_DATA_DIR)
    return _USER_DATA_DIR


def get_db_path() -> pathlib.Path:
    """Get the path to the SQLite database file."""
    data_dir = get_data_dir()
    db_path = data_dir / "meshtastic.sqlite"
    # Ensure parent directory exists
    _ensure_dir_exists(db_path.parent)
    return db_path


def get_log_dir() -> pathlib.Path:
    """Get the path to the log directory."""
    data_dir = get_data_dir()
    log_dir = data_dir / "logs"
    _ensure_dir_exists(log_dir)
    return log_dir


def get_plugins_dir() -> pathlib.Path:
    """Get the base path for plugins (contains custom/ and community/)."""
    data_dir = get_data_dir()
    plugins_dir = data_dir / "plugins"
    _ensure_dir_exists(plugins_dir)
    return plugins_dir


def get_custom_plugins_dir() -> pathlib.Path:
    """Get the path to the custom plugins directory."""
    plugins_dir = get_plugins_dir()
    custom_plugins_dir = plugins_dir / "custom"
    _ensure_dir_exists(custom_plugins_dir)
    return custom_plugins_dir


def get_community_plugins_dir() -> pathlib.Path:
    """Get the path to the community plugins directory."""
    plugins_dir = get_plugins_dir()
    community_plugins_dir = plugins_dir / "community"
    _ensure_dir_exists(community_plugins_dir)
    return community_plugins_dir


# --- Deprecated ---
# This is kept for reference but should not be used for finding config/data files.
# It only finds the installation directory of the package itself.
def get_app_path_deprecated():
    """
    Returns the base directory of the *installed application code*.
    DEPRECATED for finding user config/data files. Use specific functions above.
    """
    if getattr(sys, "frozen", False):
        # Running in a bundle (PyInstaller/Shiv)
        return os.path.dirname(sys.executable)
    else:
        # Running in a normal Python environment - finds the installed package location
        # Note: __file__ points to path_utils.py inside mmrelay package
        return os.path.dirname(os.path.abspath(__file__))
