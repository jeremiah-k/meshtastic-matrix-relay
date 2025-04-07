"""
Utility functions for handling paths in meshtastic-matrix-relay.
"""

import os
from pathlib import Path

import platformdirs

# Application name for platformdirs
APP_NAME = "mmrelay"
APP_AUTHOR = "meshtastic"

# Legacy directory (for backward compatibility)
LEGACY_DIR = os.path.expanduser("~/meshtastic-matrix-relay")


def get_config_path(config_override=None):
    """
    Get the path to the config file.
    
    Priority:
    1. CLI override
    2. Current working directory
    3. User config directory
    4. Legacy directory
    
    Args:
        config_override: Optional path to config file provided via CLI
        
    Returns:
        Path to config file
    """
    if config_override and os.path.isfile(config_override):
        return config_override
    
    # Check current working directory
    cwd_config = os.path.join(os.getcwd(), "config.yaml")
    if os.path.isfile(cwd_config):
        return cwd_config
    
    # Check user config directory
    user_config_dir = platformdirs.user_config_dir(APP_NAME, APP_AUTHOR)
    os.makedirs(user_config_dir, exist_ok=True)
    user_config = os.path.join(user_config_dir, "config.yaml")
    if os.path.isfile(user_config):
        return user_config
    
    # Check legacy directory
    legacy_config = os.path.join(LEGACY_DIR, "config.yaml")
    if os.path.isfile(legacy_config):
        return legacy_config
    
    # Default to user config directory (will be created if it doesn't exist)
    return user_config


def get_data_dir():
    """
    Get the data directory for the application.
    
    Priority:
    1. User data directory
    2. Legacy directory
    
    Returns:
        Path to data directory
    """
    # Check user data directory
    user_data_dir = platformdirs.user_data_dir(APP_NAME, APP_AUTHOR)
    os.makedirs(user_data_dir, exist_ok=True)
    return user_data_dir


def get_db_path():
    """
    Get the path to the database file.
    
    Returns:
        Path to database file
    """
    data_dir = get_data_dir()
    db_path = os.path.join(data_dir, "meshtastic.sqlite")
    
    # Check if database exists in legacy location but not in new location
    legacy_db = os.path.join(LEGACY_DIR, "meshtastic.sqlite")
    if os.path.isfile(legacy_db) and not os.path.isfile(db_path):
        # Use legacy database
        return legacy_db
    
    return db_path


def get_log_dir(log_override=None):
    """
    Get the directory for log files.
    
    Args:
        log_override: Optional path to log file provided via CLI
        
    Returns:
        Path to log directory
    """
    if log_override:
        # If a specific log file is provided, return its directory
        return os.path.dirname(os.path.abspath(log_override))
    
    data_dir = get_data_dir()
    log_dir = os.path.join(data_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def get_log_path(log_override=None):
    """
    Get the path to the log file.
    
    Args:
        log_override: Optional path to log file provided via CLI
        
    Returns:
        Path to log file
    """
    if log_override:
        return log_override
    
    log_dir = get_log_dir()
    return os.path.join(log_dir, "mmrelay.log")


def get_plugins_dir():
    """
    Get the directory for custom plugins.
    
    Returns:
        Path to plugins directory
    """
    data_dir = get_data_dir()
    plugins_dir = os.path.join(data_dir, "plugins")
    os.makedirs(plugins_dir, exist_ok=True)
    return plugins_dir


def get_package_dir():
    """
    Get the directory of the installed package.
    
    Returns:
        Path to package directory
    """
    return os.path.dirname(os.path.abspath(__file__))
