"""
Plugin system constants.

This module contains constants related to plugin security, validation,
and configuration. These constants help ensure safe plugin loading and
execution by defining trusted sources and dangerous patterns.
"""

import re
from typing import Tuple

# Message length limits
MAX_FORECAST_LENGTH = 200
MAX_PUNCTUATION_LENGTH = 5

# Map image size limits
MAX_MAP_IMAGE_SIZE = 1000

# Special node identifiers
SPECIAL_NODE_MESSAGES = "!NODE_MSGS!"

# S2 geometry constants for map functionality
S2_PRECISION_BITS_TO_METERS_CONSTANT = 23905787.925008

# Precompiled regex patterns for validation
COMMIT_HASH_PATTERN = re.compile(r"[0-9a-fA-F]{7,40}")
REF_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")

# Default branch names to try when ref is not specified
DEFAULT_BRANCHES = ["main", "master"]

# Environment keys that indicate pipx is being used (for security/testability)
PIPX_ENVIRONMENT_KEYS = ("PIPX_HOME", "PIPX_LOCAL_VENVS", "PIPX_BIN_DIR")

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
