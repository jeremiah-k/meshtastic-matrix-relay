"""
Android-specific configuration utilities for MMRelay
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Android-specific paths (set by AndroidConfigManager)
_android_config_dir: Optional[str] = None
_android_log_dir: Optional[str] = None
_android_data_dir: Optional[str] = None


def set_android_paths(config_dir: str, log_dir: str, data_dir: str):
    """Set Android-specific paths from Kotlin"""
    global _android_config_dir, _android_log_dir, _android_data_dir

    _android_config_dir = config_dir
    _android_log_dir = log_dir
    _android_data_dir = data_dir

    logger.info(
        f"Android paths set - Config: {config_dir}, Log: {log_dir}, Data: {data_dir}"
    )

    # Ensure directories exist
    for path in [config_dir, log_dir, data_dir]:
        os.makedirs(path, exist_ok=True)


def get_android_config_dir() -> Optional[str]:
    """Get Android config directory"""
    return _android_config_dir


def get_android_log_dir() -> Optional[str]:
    """Get Android log directory"""
    return _android_log_dir


def get_android_data_dir() -> Optional[str]:
    """Get Android data directory"""
    return _android_data_dir


def is_android_environment() -> bool:
    """Check if running in Android environment"""
    return _android_config_dir is not None


def get_config_file_path(filename: str = "config.yaml") -> str:
    """Get full path to config file"""
    if _android_config_dir:
        return os.path.join(_android_config_dir, filename)
    else:
        # Fallback to current directory for non-Android environments
        return filename


def get_log_file_path(filename: str = "mmrelay.log") -> str:
    """Get full path to log file"""
    if _android_log_dir:
        return os.path.join(_android_log_dir, filename)
    else:
        # Fallback to current directory for non-Android environments
        return filename


def get_data_file_path(filename: str) -> str:
    """Get full path to data file"""
    if _android_data_dir:
        return os.path.join(_android_data_dir, filename)
    else:
        # Fallback to current directory for non-Android environments
        return filename


def setup_android_logging():
    """Setup logging for Android environment"""
    if not is_android_environment():
        return

    log_file = get_log_file_path()

    # Create Android-compatible logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),  # Also log to console for Android logcat
        ],
    )

    logger.info("Android logging setup complete")
