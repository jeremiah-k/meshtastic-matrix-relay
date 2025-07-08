import logging
import os
import sys

import platformdirs
import yaml
from yaml.loader import SafeLoader

# Define custom base directory for Unix systems
APP_NAME = "mmrelay"
APP_AUTHOR = None  # No author directory


# Global variable to store the custom data directory
custom_data_dir = None


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
    Returns a list of possible config file paths in order of priority:
    1. Command line argument (if provided)
    2. User config directory (~/.mmrelay/config/ on Linux)
    3. Current directory (for backward compatibility)
    4. Application directory (for backward compatibility)

    Args:
        args: The parsed command-line arguments
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

    os.makedirs(user_config_dir, exist_ok=True)
    user_config_path = os.path.join(user_config_dir, "config.yaml")
    paths.append(user_config_path)

    # Check current directory (for backward compatibility)
    current_dir_config = os.path.join(os.getcwd(), "config.yaml")
    paths.append(current_dir_config)

    # Check application directory (for backward compatibility)
    app_dir_config = os.path.join(get_app_path(), "config.yaml")
    paths.append(app_dir_config)

    return paths


def get_data_dir():
    """Get the directory for storing application data files.
    
    Returns the platform-appropriate directory for storing MMRelay data files
    such as SQLite databases, logs, and plugin data. On Unix systems, uses
    ~/.mmrelay/data/. On Windows, uses the platformdirs standard data directory.
    Creates the directory structure if it doesn't exist.
    
    Returns:
        str: Absolute path to the data directory
    
    Example:
        >>> get_data_dir()
        '/home/user/.mmrelay/data'
    """
    
    Returns the platform-appropriate directory for storing MMRelay data files
    such as SQLite databases, logs, and plugin data. On Unix systems, uses
    ~/.mmrelay/data/. On Windows, uses the platformdirs standard data directory.
    Creates the directory structure if it doesn't exist.
    
    Returns:
        str: Absolute path to the data directory
    
    Example:
        >>> get_data_dir()
        '/home/user/.mmrelay/data'
    """
    Returns the directory for storing application data files.
    Creates the directory if it doesn't exist.
    """
    if sys.platform in ["linux", "darwin"]:
        # Use ~/.mmrelay/data/ for Linux and Mac
        data_dir = os.path.join(get_base_dir(), "data")
    else:
        # Use platformdirs default for Windows
        data_dir = platformdirs.user_data_dir(APP_NAME, APP_AUTHOR)

    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def get_plugin_data_dir(plugin_name=None):
    """Get the directory for storing plugin-specific data files.
    
    Returns the directory where plugins can store their persistent data files,
    configurations, or cache. If a plugin name is provided, returns a dedicated
    subdirectory for that plugin. Creates the directory structure if it doesn't exist.
    
    Args:
        plugin_name (str, optional): Name of the plugin. If provided, returns
                                   a plugin-specific subdirectory.
    
    Returns:
        str: Absolute path to the plugin data directory. If plugin_name is None,
             returns the base plugins directory. If plugin_name is provided,
             returns the plugin-specific subdirectory.
    
    Examples:
        >>> get_plugin_data_dir()
        '/home/user/.mmrelay/data/plugins'
        >>> get_plugin_data_dir('weather')
        '/home/user/.mmrelay/data/plugins/weather'
    """
    
    Returns the directory where plugins can store their persistent data files,
    configurations, or cache. If a plugin name is provided, returns a dedicated
    subdirectory for that plugin. Creates the directory structure if it doesn't exist.
    
    Args:
        plugin_name (str, optional): Name of the plugin. If provided, returns
                                   a plugin-specific subdirectory.
    
    Returns:
        str: Absolute path to the plugin data directory. If plugin_name is None,
             returns the base plugins directory. If plugin_name is provided,
             returns the plugin-specific subdirectory.
    
    Examples:
        >>> get_plugin_data_dir()
        '/home/user/.mmrelay/data/plugins'
        >>> get_plugin_data_dir('weather')
        '/home/user/.mmrelay/data/plugins/weather'
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
    """Get the directory for storing application log files.
    
    Returns the platform-appropriate directory for storing MMRelay log files.
    On Unix systems, uses ~/.mmrelay/logs/. On Windows, uses a logs subdirectory
    within the platformdirs data directory. Creates the directory structure if
    it doesn't exist.
    
    Returns:
        str: Absolute path to the logs directory
    
    Example:
        >>> get_log_dir()
        '/home/user/.mmrelay/logs'
    """
    
    Returns the platform-appropriate directory for storing MMRelay log files.
    On Unix systems, uses ~/.mmrelay/logs/. On Windows, uses a logs subdirectory
    within the platformdirs data directory. Creates the directory structure if
    it doesn't exist.
    
    Returns:
        str: Absolute path to the logs directory
    
    Example:
        >>> get_log_dir()
        '/home/user/.mmrelay/logs'
    """
    Returns the directory for storing log files.
    Creates the directory if it doesn't exist.
    """
    if sys.platform in ["linux", "darwin"]:
        # Use ~/.mmrelay/logs/ for Linux and Mac
        log_dir = os.path.join(get_base_dir(), "logs")
    else:
        # Use platformdirs default for Windows
        log_dir = platformdirs.user_log_dir(APP_NAME, APP_AUTHOR)

    os.makedirs(log_dir, exist_ok=True)
    return log_dir


# Set up a basic logger for config
logger = logging.getLogger("Config")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(
    logging.Formatter(
        fmt="%(asctime)s %(levelname)s:%(name)s:%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S %z",
    )
)
logger.addHandler(handler)

# Initialize empty config
relay_config = {}
config_path = None


def set_config(module, passed_config):
    """Set the global configuration in a specified module.
    
    Updates the global 'config' variable in the target module with the provided
    configuration dictionary. This is used to propagate configuration settings
    across different MMRelay modules without circular imports.
    
    Args:
        module: Python module object that has a global 'config' variable
        passed_config (dict): Configuration dictionary to set in the module
    
    Example:
        >>> import mmrelay.db_utils as db_utils
        >>> config = {'database': {'path': '/custom/path.db'}}
        >>> set_config(db_utils, config)
    """
    """Set the global configuration in a specified module.
    
    Updates the global 'config' variable in the target module with the provided
    configuration dictionary. This is used to propagate configuration settings
    across different MMRelay modules without circular imports.
    
    Args:
        module: Python module object that has a global 'config' variable
        passed_config (dict): Configuration dictionary to set in the module
    
    Example:
        >>> import mmrelay.db_utils as db_utils
        >>> config = {'database': {'path': '/custom/path.db'}}
        >>> set_config(db_utils, config)
    """
    """
    Set the configuration for a module.

    Args:
        module: The module to set the configuration for
        passed_config: The configuration dictionary to use

    Returns:
        The updated config
    """
    # Set the module's config variable
    module.config = passed_config

    # Handle module-specific setup based on module name
    module_name = module.__name__.split(".")[-1]

    if module_name == "matrix_utils":
        # Set Matrix-specific configuration
        if hasattr(module, "matrix_homeserver") and "matrix" in passed_config:
            module.matrix_homeserver = passed_config["matrix"]["homeserver"]
            module.matrix_rooms = passed_config["matrix_rooms"]
            module.matrix_access_token = passed_config["matrix"]["access_token"]
            module.bot_user_id = passed_config["matrix"]["bot_user_id"]

    elif module_name == "meshtastic_utils":
        # Set Meshtastic-specific configuration
        if hasattr(module, "matrix_rooms") and "matrix_rooms" in passed_config:
            module.matrix_rooms = passed_config["matrix_rooms"]

    # If the module still has a setup_config function, call it for backward compatibility
    if hasattr(module, "setup_config") and callable(module.setup_config):
        module.setup_config()

    return passed_config


def load_config(config_file=None, args=None):
    """Load and merge configuration from YAML file and command line arguments.
    
    Loads the application configuration by reading from a YAML configuration file
    and merging it with command line argument overrides. Searches for config files
    in multiple locations if no specific file is provided. Validates that required
    configuration sections exist.
    
    Configuration loading priority:
    1. Command line --config argument
    2. ~/.mmrelay/config.yaml (preferred)
    3. ./config.yaml (backward compatibility)
    4. Application directory config.yaml
    
    Args:
        config_file (str, optional): Specific path to configuration file.
                                   If None, searches in default locations.
        args (argparse.Namespace, optional): Parsed command line arguments
                                           used to override config file settings.
    
    Returns:
        dict: Merged configuration dictionary with all settings
    
    Raises:
        SystemExit: If no configuration file is found or if the configuration
                   is invalid (missing required sections like 'matrix' or 'meshtastic')
    
    Example:
        >>> config = load_config('/path/to/config.yaml', args)
        >>> matrix_settings = config['matrix']
    """
    
    Loads the application configuration by reading from a YAML configuration file
    and merging it with command line argument overrides. Searches for config files
    in multiple locations if no specific file is provided. Validates that required
    configuration sections exist.
    
    Configuration loading priority:
    1. Command line --config argument
    2. ~/.mmrelay/config.yaml (preferred)
    3. ./config.yaml (backward compatibility)
    4. Application directory config.yaml
    
    Args:
        config_file (str, optional): Specific path to configuration file.
                                   If None, searches in default locations.
        args (argparse.Namespace, optional): Parsed command line arguments
                                           used to override config file settings.
    
    Returns:
        dict: Merged configuration dictionary with all settings
    
    Raises:
        SystemExit: If no configuration file is found or if the configuration
                   is invalid (missing required sections like 'matrix' or 'meshtastic')
    
    Example:
        >>> config = load_config('/path/to/config.yaml', args)
        >>> matrix_settings = config['matrix']
    """

    Args:
        config_file (str, optional): Path to the config file. If None, search for it.
        args: The parsed command-line arguments

    Returns:
        dict: The loaded configuration
    """
    global relay_config, config_path

    # If a specific config file was provided, use it
    if config_file and os.path.isfile(config_file):
        # Store the config path but don't log it yet - will be logged by main.py
        with open(config_file, "r") as f:
            relay_config = yaml.load(f, Loader=SafeLoader)
        config_path = config_file
        return relay_config

    # Otherwise, search for a config file
    config_paths = get_config_paths(args)

    # Try each config path in order until we find one that exists
    for path in config_paths:
        if os.path.isfile(path):
            config_path = path
            # Store the config path but don't log it yet - will be logged by main.py
            with open(config_path, "r") as f:
                relay_config = yaml.load(f, Loader=SafeLoader)
            return relay_config

    # No config file found
    logger.error("Configuration file not found in any of the following locations:")
    for path in config_paths:
        logger.error(f"  - {path}")
    logger.error("Using empty configuration. This will likely cause errors.")
    logger.error(
        "Run 'mmrelay --generate-config' to generate a sample configuration file."
    )

    return relay_config
