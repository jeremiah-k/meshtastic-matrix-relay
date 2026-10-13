"""Room authorization semantics shared across MMRelay.

``m.room.power_levels`` governs sender power. Rooms at version 12 and newer
additionally grant their creators intrinsically infinite, undemotable power
that no users-map entry can express — v12 auth rules even reject power-level
events listing creators, so an empty ``users`` map is normal in those rooms
(Element's "Creator" badge comes from the same rule). Command authorization
fetches fresh state for every invocation and fails closed on unavailable
state. The separate immutable-creator lookup caches only creator identities;
cached power levels are for diagnostics and must never authorize commands.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nio import AsyncClient, MatrixRoom, RoomGetStateResponse

ROOM_STATE_FETCH_TIMEOUT_SECONDS = 10.0

_creator_cache: dict[str, frozenset[str]] = {}


def clear_creator_cache() -> None:
    """Forget cached room creators (test hook)."""
    _creator_cache.clear()


def sender_power_level(room: MatrixRoom, sender: str) -> int | None:
    """Return cached sender power for diagnostics, never command authorization.

    Returns None when the room carries no readable power-levels state.
    Senders absent from the users map fall back to the event's
    users_default.
    """
    power_levels = getattr(room, "power_levels", None)
    if power_levels is None:
        return None
    try:
        users = power_levels.users
        default_level = power_levels.defaults.users_default
    except (AttributeError, TypeError):
        return None
    try:
        return int(users.get(sender, default_level))
    except (TypeError, ValueError):
        return None


async def room_creators(client: AsyncClient | None, room_id: str) -> frozenset[str]:
    """Return the creators whose power is intrinsically infinite (v12+).

    A missing or pre-v12 ``m.room.create`` yields an empty set; fetch
    failures fail closed and are not cached.
    """
    cached = _creator_cache.get(room_id)
    if cached is not None:
        return cached
    creators: frozenset[str] = frozenset()
    if client is not None:
        try:
            response = await asyncio.wait_for(
                client.room_get_state(room_id), timeout=ROOM_STATE_FETCH_TIMEOUT_SECONDS
            )
        except Exception:  # noqa: BLE001 - authorization must fail closed
            response = None
        if isinstance(response, RoomGetStateResponse):
            creators = _creators_from_state(response.events)
            _creator_cache[room_id] = creators
    return creators


def _creators_from_state(events: list[Any]) -> frozenset[str]:
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "m.room.create":
            continue
        content = event.get("content")
        if not isinstance(content, dict):
            break
        try:
            room_version = int(content.get("room_version", 1))
        except (TypeError, ValueError):
            room_version = 1
        if room_version < 12:
            break
        additional = content.get("additional_creators")
        candidates = [event.get("sender")]
        if isinstance(additional, list):
            candidates.extend(additional)
        return frozenset(
            creator for creator in candidates if isinstance(creator, str) and creator
        )
    return frozenset()


async def is_authorized_in_room(
    client: AsyncClient | None,
    room: MatrixRoom,
    sender: str,
    min_level: int,
) -> bool:
    """Authorize against fresh room state, including v12 creator authority.

    nio merges power-level user maps in its room cache, retaining overrides
    removed by a later event. Cached power levels cannot authorize execution.
    Fetch failures and timeouts deny the command rather than trusting a stale
    grant. Room-state reads have their own budget; the mtjk command timeout
    only begins after authorization and cannot bound Matrix network waits.
    """
    if client is None:
        return False
    try:
        response = await asyncio.wait_for(
            client.room_get_state(room.room_id),
            timeout=ROOM_STATE_FETCH_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 - authorization must fail closed
        return False
    if not isinstance(response, RoomGetStateResponse):
        return False

    creators = _creators_from_state(response.events)
    _creator_cache[room.room_id] = creators
    if sender in creators:
        return True
    for event in response.events:
        if (
            not isinstance(event, dict)
            or event.get("type") != "m.room.power_levels"
            or event.get("state_key") != ""
        ):
            continue
        content = event.get("content")
        if not isinstance(content, dict):
            return False
        users = content.get("users", {})
        if not isinstance(users, dict):
            return False
        level = users.get(sender, content.get("users_default", 0))
        if isinstance(level, bool) or not isinstance(level, (int, str)):
            return False
        try:
            return int(level) >= min_level
        except ValueError:
            return False
    return False
