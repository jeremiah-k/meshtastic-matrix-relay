import os
import sys

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


relay_config = {}
config_path = os.path.join(get_app_path(), "config.yaml")

if not os.path.isfile(config_path):
    print(f"Configuration file not found: {config_path}")
else:
    with open(config_path, "r") as f:
        relay_config = yaml.load(f, Loader=SafeLoader)
