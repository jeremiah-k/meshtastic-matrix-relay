"""Configuration handling for meshtastic-matrix-relay."""

import os
import sys
import logging
from typing import Any, Dict, List, Optional

import yaml
from yaml.loader import SafeLoader


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


class Config:
    """
    Configuration class for meshtastic-matrix-relay.
    Provides easy access to configuration values with defaults.
    """
    _instance = None

    def __new__(cls):
        """Singleton pattern to ensure only one config instance exists."""
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._config = {}
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """
        Initialize the config if not already initialized.
        This avoids reloading the config multiple times.
        """
        if not self._initialized:
            self._initialized = True

    def load(self, config_path: str) -> Dict[str, Any]:
        """
        Load configuration from the specified path.

        Args:
            config_path: Path to the configuration file

        Returns:
            Loaded configuration dictionary
        """
        if not os.path.isfile(config_path):
            logging.warning(f"Configuration file not found: {config_path}")
            self._config = {}
            return self._config

        try:
            with open(config_path, "r") as f:
                loaded_config = yaml.load(f, Loader=SafeLoader) or {}

            # Ensure the config has the expected structure
            if not isinstance(loaded_config, dict):
                logging.warning(f"Invalid configuration format in {config_path}")
                loaded_config = {}

            # Update the internal config dictionary
            self._config.clear()
            self._config.update(loaded_config)

            logging.info(f"Loaded configuration from {config_path}")
            return self._config

        except Exception as e:
            logging.error(f"Error loading configuration: {e}")
            self._config = {}
            return self._config

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value by key.

        Args:
            key: Configuration key
            default: Default value if key is not found

        Returns:
            Configuration value or default
        """
        return self._config.get(key, default)

    def __getitem__(self, key: str) -> Any:
        """
        Get a configuration value by key using dictionary syntax.

        Args:
            key: Configuration key

        Returns:
            Configuration value

        Raises:
            KeyError: If key is not found
        """
        return self._config[key]

    def __contains__(self, key: str) -> bool:
        """
        Check if a key exists in the configuration.

        Args:
            key: Configuration key

        Returns:
            True if key exists, False otherwise
        """
        return key in self._config

    @property
    def matrix_rooms(self) -> List[Dict[str, Any]]:
        """Get the matrix rooms configuration."""
        return self._config.get("matrix_rooms", [])

    @property
    def matrix(self) -> Dict[str, Any]:
        """Get the matrix configuration."""
        return self._config.get("matrix", {})

    @property
    def meshtastic(self) -> Dict[str, Any]:
        """Get the meshtastic configuration."""
        return self._config.get("meshtastic", {})

    @property
    def logging(self) -> Dict[str, Any]:
        """Get the logging configuration."""
        return self._config.get("logging", {})

    @property
    def db(self) -> Dict[str, Any]:
        """Get the database configuration."""
        return self._config.get("db", {})

    @property
    def plugins(self) -> Dict[str, Any]:
        """Get the plugins configuration."""
        return self._config.get("plugins", {})

    @property
    def custom_plugins(self) -> Dict[str, Any]:
        """Get the custom plugins configuration."""
        return self._config.get("custom-plugins", {})

    @property
    def community_plugins(self) -> Dict[str, Any]:
        """Get the community plugins configuration."""
        return self._config.get("community-plugins", {})

    @property
    def log_level(self) -> str:
        """Get the log level."""
        return self.logging.get("level", "info").upper()

    @property
    def log_to_file(self) -> bool:
        """Check if logging to file is enabled."""
        return self.logging.get("log_to_file", False)

    @property
    def log_filename(self) -> Optional[str]:
        """Get the log filename if specified."""
        return self.logging.get("filename")

    @property
    def matrix_access_token(self) -> Optional[str]:
        """Get the Matrix access token."""
        return self.matrix.get("access_token")

    @property
    def matrix_bot_user_id(self) -> Optional[str]:
        """Get the Matrix bot user ID."""
        return self.matrix.get("bot_user_id")

    @property
    def matrix_homeserver(self) -> Optional[str]:
        """Get the Matrix homeserver URL."""
        return self.matrix.get("homeserver")

    @property
    def meshtastic_connection_type(self) -> str:
        """Get the Meshtastic connection type."""
        return self.meshtastic.get("connection_type", "serial")

    @property
    def meshtastic_serial_port(self) -> Optional[str]:
        """Get the Meshtastic serial port."""
        return self.meshtastic.get("serial_port")

    @property
    def meshtastic_host(self) -> Optional[str]:
        """Get the Meshtastic host for TCP connections."""
        return self.meshtastic.get("host")

    @property
    def meshtastic_broadcast_enabled(self) -> bool:
        """Check if Meshtastic broadcast is enabled."""
        return self.meshtastic.get("broadcast_enabled", False)

    @property
    def meshtastic_channel(self) -> int:
        """Get the Meshtastic channel."""
        return self.meshtastic.get("channel", 0)

    @property
    def meshtastic_meshnet_name(self) -> Optional[str]:
        """Get the Meshtastic meshnet name."""
        return self.meshtastic.get("meshnet_name")

    @property
    def meshtastic_relay_reactions(self) -> bool:
        """Check if relaying reactions is enabled."""
        return self.meshtastic.get("relay_reactions", False)

    @property
    def db_msgs_to_keep(self) -> int:
        """Get the number of messages to keep in the database."""
        return self.db.get("msg_map", {}).get("msgs_to_keep", 500)

    @property
    def db_wipe_on_restart(self) -> bool:
        """Check if the message map should be wiped on restart."""
        return self.db.get("msg_map", {}).get("wipe_on_restart", False)


# Create a global instance of the Config class
config = Config()

# For backward compatibility
relay_config = {}

def load_config(config_path: str) -> Dict[str, Any]:
    """
    Load configuration from the specified path and update relay_config.

    Args:
        config_path: Path to the configuration file

    Returns:
        Loaded configuration dictionary
    """
    global relay_config
    loaded_config = config.load(config_path)
    relay_config.clear()
    relay_config.update(loaded_config)
    return loaded_config
