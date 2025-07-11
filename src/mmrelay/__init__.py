"""
Meshtastic Matrix Relay - Bridge between Meshtastic mesh networks and Matrix chat rooms.
"""

import os
from importlib.metadata import PackageNotFoundError, version

# First try to get version from environment variable (GitHub tag)
if "GITHUB_REF_NAME" in os.environ:
    __version__ = os.environ.get("GITHUB_REF_NAME")
else:
    # Fall back to package metadata using importlib.metadata (modern replacement for pkg_resources)
    try:
        __version__ = version("mmrelay")
    except PackageNotFoundError:
        # If all else fails, use hardcoded version
        __version__ = "1.1.2"
