"""Room identifier semantics shared across MMRelay.

Room IDs are opaque strings. Since Matrix spec v1.16, rooms at version 12
and newer take their room ID from the create event's ID under a ``!``
sigil — unpadded URL-safe base64 with **no server suffix** — while older
rooms carry ``!opaque:server``. Both forms are equally valid, and no code
may complete, strip, or derive routing from an ID's internal structure:
the domain part never carried routing semantics (matrix.to permalinks
supply ``via`` parameters for that). Copy IDs exactly as clients show
them and byte-match them against the room's actual ID.
"""

from __future__ import annotations

from typing import Any


def is_room_id(value: Any) -> bool:
    """Whether the value is a structurally valid room ID.

    Accepts both the domain-less room version 12+ form (``!opaque``) and
    the classic form (``!opaque:server``); rejects aliases, empty
    localparts, and a colon without a domain.
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
    return bool(localpart) and (not sep or bool(domain))


def is_room_alias(value: Any) -> bool:
    """Whether the value is a structurally valid room alias.

    Aliases always carry their server part (``#local:server``) and are
    resolved to room IDs at join time; they are mutable room state and
    cannot serve as authorization anchors.
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
    return bool(localpart) and bool(sep) and bool(domain)
