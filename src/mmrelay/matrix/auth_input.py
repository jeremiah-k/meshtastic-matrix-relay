"""Normalize operator login inputs without rewriting discovered endpoint URLs."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


class LoginInputError(ValueError):
    """An operator-safe input error, without echoing entered values."""


def _input_text(value: str, label: str) -> str:
    if not isinstance(value, str) or any(
        ord(char) < 32 or ord(char) == 127 for char in value
    ):
        raise LoginInputError(f"Invalid Matrix {label}.")
    value = value.strip()
    if not value or any(char.isspace() for char in value):
        raise LoginInputError(f"Invalid Matrix {label}.")
    return value


def normalize_homeserver(value: str) -> str:
    """Default a bare host to HTTPS; retain explicit HTTP for password login."""
    value = _input_text(value, "homeserver")
    if "://" not in value:
        value = "https://" + value
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise LoginInputError("Invalid Matrix homeserver.") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port == 0
        or "\\" in value
    ):
        raise LoginInputError(
            "Use a Matrix server name or HTTP(S) URL without credentials."
        )
    # Compare equivalent host spellings consistently when reusing saved sessions.
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    default_port = 443 if parsed.scheme == "https" else 80
    authority = hostname if port in {None, default_port} else f"{hostname}:{port}"
    return urlunsplit((parsed.scheme, authority, parsed.path.rstrip("/"), "", ""))


def normalize_username(value: str) -> str:
    """Accept a localpart or full MXID without inferring a delegated server name."""
    value = _input_text(value, "username")
    name = value.removeprefix("@")
    localpart, separator, server = name.partition(":")
    if not localpart or "@" in localpart or (separator and not server):
        raise LoginInputError(
            "Use a Matrix username or full user ID, such as @bot:example.com."
        )
    return "@" + name if separator else name


def matches_username(user_id: str, expected: str) -> bool:
    """Compare a full ID exactly, or a localpart against the authenticated ID."""
    if expected.startswith("@"):
        return user_id == expected
    return user_id.startswith("@") and user_id[1:].partition(":")[0] == expected
