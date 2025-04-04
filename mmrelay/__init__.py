# ./mmrelay/__init__.py:
import configparser
import pathlib
import sys

# Default values
__version__ = "0.0.0"
__author__ = "Unknown"

try:
    config = configparser.ConfigParser()
    # Construct path relative to this file
    setup_cfg_path = pathlib.Path(__file__).parent.parent / "setup.cfg"

    if setup_cfg_path.is_file():
        config.read(setup_cfg_path)
        __version__ = config.get("metadata", "version", fallback=__version__)
        __author__ = config.get("metadata", "author", fallback=__author__)
    # else: maybe log a warning if setup.cfg isn't found, but often expected in installed envs

except Exception as e:
    # Log error if needed, but avoid crashing during import
    print(f"Warning: Could not read package metadata from setup.cfg: {e}", file=sys.stderr)