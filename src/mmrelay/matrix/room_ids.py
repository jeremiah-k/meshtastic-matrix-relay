"""Room identifier semantics shared across MMRelay.

Room IDs are opaque strings. Since Matrix spec v1.16, rooms at version 12
and newer take their room ID from the create event's ID under a ``!``
sigil — unpadded URL-safe base64 with **no server suffix** — while older
rooms carry ``!opaque:server``. Both forms are equally valid, and no code
may complete, strip, or derive routing from an ID's internal structure:
the domain part never carried routing semantics (matrix.to permalinks
supply ``via`` parameters for that). Copy IDs exactly as clients show
them and byte-match them against the room's actual ID.

The classic form's server suffix must still be a structurally valid
Matrix ``server_name`` (DNS name, bracketed IPv6 literal, optional port),
and identifiers are limited to 255 UTF-8 bytes, per the Matrix identifier
grammar: https://spec.matrix.org/unstable/appendices/#identifier-grammar.
Legacy localparts remain opaque and are never restricted to ASCII.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

_DOMAIN_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")
_V12_LOCALPART = re.compile(r"[A-Za-z0-9_-]+")


def _valid_server_name(value: str) -> bool:
    """Validate a Matrix ``server_name`` without interpreting room routing.

    This applies only to the legacy namespaced suffix and to alias domains;
    a domain-less v12 room ID must never acquire a synthetic server name.
    """
    if not value:
        return False
    if value.startswith("["):
        close = value.find("]")
        if close == -1:
            return False
        # RFC 3986 IP-literals do not contain scoped/zone identifiers.
        literal = value[1:close]
        if "%" in literal:
            return False
        try:
            ipaddress.IPv6Address(literal)
        except ValueError:
            return False
        suffix = value[close + 1 :]
        if not suffix:
            return True
        if not suffix.startswith(":"):
            return False
        port = suffix[1:]
    else:
        host, separator, port = value.partition(":")
        if not host or (separator and ":" in port):
            return False
        if not all(_DOMAIN_LABEL.fullmatch(label) for label in host.split(".")):
            return False
        if len(host) > 255:
            return False
        if not separator:
            return True
    return (
        bool(port)
        and len(port) <= 5
        and port.isascii()
        and port.isdecimal()
        and 1 <= int(port) <= 65535
    )


def _valid_length(value: str) -> bool:
    """Matrix room IDs and aliases are limited to 255 UTF-8 bytes."""
    try:
        return len(value.encode("utf-8")) <= 255
    except UnicodeEncodeError:
        # YAML may represent unpaired surrogates; they are not UTF-8 IDs.
        return False


def is_room_id(value: Any) -> bool:
    """Whether the value is a structurally valid room ID.

    Accepts both the domain-less room version 12+ form (``!opaque``) and
    the classic form (``!opaque:server``); rejects aliases, empty
    localparts, a colon without a domain, malformed server names, and
    identifiers beyond the 255-byte limit.
    """
    if not isinstance(value, str):
        return False
    # Configuration, mapping and room ownership compare IDs byte-for-byte.
    # Accepting a trimmed value here would bless a room that can never match.
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        return False
    room_id = value
    if not room_id.startswith("!"):
        return False
    localpart, sep, domain = room_id[1:].partition(":")
    return (
        bool(localpart)
        and _valid_length(room_id)
        and (
            _valid_server_name(domain)
            if sep
            else bool(_V12_LOCALPART.fullmatch(localpart))
        )
    )


def is_room_alias(value: Any) -> bool:
    """Whether the value is a structurally valid room alias.

    Aliases always carry their server part (``#local:server``) and are
    resolved to room IDs at join time; they are mutable room state and
    cannot serve as authorization anchors. The server part must be a valid
    ``server_name`` and the alias must fit in 255 UTF-8 bytes.
    """
    if not isinstance(value, str):
        return False
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        return False
    alias = value
    if not alias.startswith("#"):
        return False
    localpart, sep, domain = alias[1:].partition(":")
    return (
        bool(localpart)
        and bool(sep)
        and _valid_length(alias)
        and _valid_server_name(domain)
    )
