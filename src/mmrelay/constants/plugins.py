"""
Plugin system constants.

This module contains constants related to plugin security, validation,
and configuration. These constants help ensure safe plugin loading and
execution by defining trusted sources and dangerous patterns.
"""

from typing import Tuple

# Message length limits
MAX_FORECAST_LENGTH = 200

# Special node identifiers
SPECIAL_NODE_MESSAGES = "!NODE_MSGS!"

# S2 geometry constants for map functionality
S2_PRECISION_BITS_TO_METERS_CONSTANT = 23905787.925008

# Trusted git hosting platforms for community plugins
# These hosts are considered safe for plugin source repositories
DEFAULT_ALLOWED_COMMUNITY_HOSTS: Tuple[str, ...] = (
    "github.com",
    "gitlab.com",
    "codeberg.org",
    "bitbucket.org",
)

# Requirement prefixes that may indicate security risks
# These prefixes allow VCS URLs or direct URLs that could bypass package verification
RISKY_REQUIREMENT_PREFIXES: Tuple[str, ...] = (
    "git+",
    "ssh://",
    "git://",
    "hg+",
    "bzr+",
    "svn+",
    "http://",
    "https://",
)

# Pip source flags that can be followed by URLs
PIP_SOURCE_FLAGS: Tuple[str, ...] = (
    "-e",
    "--editable",
    "-f",
    "--find-links",
    "-i",
    "--index-url",
    "--extra-index-url",
)
