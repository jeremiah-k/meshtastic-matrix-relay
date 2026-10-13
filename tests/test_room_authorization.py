"""Tests for shared room authorization semantics."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nio import RoomGetStateResponse

from mmrelay.matrix.room_authorization import (
    clear_creator_cache,
    is_authorized_in_room,
    room_creators,
    sender_power_level,
)

ROOM_ID = "!Ah4tQ7vZn9WkP2mXrB6yJc8sLd3FgUeH5iK1oN0pRwz"
SENDER = "@tadchilly:matrix.org"


@pytest.fixture(autouse=True)
def _fresh_creator_cache():
    clear_creator_cache()
    yield
    clear_creator_cache()


def _room(
    users: dict[str, int] | None = None,
    users_default: int = 0,
) -> Any:
    room = MagicMock()
    room.room_id = ROOM_ID
    room.power_levels = SimpleNamespace(
        users=users if users is not None else {},
        defaults=SimpleNamespace(users_default=users_default),
    )
    return room


def _client_with_state(events: list[dict[str, Any]]) -> Any:
    client = MagicMock()
    client.room_get_state = AsyncMock(
        return_value=RoomGetStateResponse(events=events, room_id=ROOM_ID)
    )
    return client


def _create_event(
    sender: str = "@creator:matrix.org",
    room_version: Any = "12",
    additional_creators: Any = None,
) -> dict[str, Any]:
    content: dict[str, Any] = {"room_version": room_version}
    if additional_creators is not None:
        content["additional_creators"] = additional_creators
    return {
        "type": "m.room.create",
        "sender": sender,
        "state_key": "",
        "content": content,
    }


# --- sender_power_level ------------------------------------------------------


def test_power_level_uses_users_map_over_default() -> None:
    assert sender_power_level(_room(users={SENDER: 10}, users_default=75), SENDER) == 10


def test_power_level_falls_back_to_users_default() -> None:
    assert sender_power_level(_room(users_default=75), SENDER) == 75


def test_power_level_is_none_without_state() -> None:
    room = _room()
    room.power_levels = None
    assert sender_power_level(room, SENDER) is None


class _RaisingLevels:
    @property
    def users(self) -> Any:
        raise TypeError("no users")

    @property
    def defaults(self) -> Any:
        raise TypeError("no defaults")


def test_power_level_is_none_when_state_is_unreadable() -> None:
    assert (
        sender_power_level(SimpleNamespace(power_levels=_RaisingLevels()), SENDER)
        is None
    )


# --- room_creators -----------------------------------------------------------


async def test_v12_create_sender_is_a_creator() -> None:
    creators = await room_creators(
        _client_with_state([_create_event(sender=SENDER)]), ROOM_ID
    )
    assert creators == {SENDER}


async def test_v12_additional_creators_are_included() -> None:
    creators = await room_creators(
        _client_with_state(
            [_create_event(additional_creators=["@extra:example.org", 42, None])]
        ),
        ROOM_ID,
    )
    assert creators == {"@creator:matrix.org", "@extra:example.org"}


async def test_pre_v12_create_grants_no_creator_power() -> None:
    creators = await room_creators(
        _client_with_state([_create_event(room_version="10")]), ROOM_ID
    )
    assert creators == frozenset()


async def test_missing_create_event_grants_no_creator_power() -> None:
    creators = await room_creators(
        _client_with_state([{"type": "m.room.name", "content": {"name": "x"}}]),
        ROOM_ID,
    )
    assert creators == frozenset()


async def test_fetch_failures_fail_closed_and_retry() -> None:
    client = MagicMock()
    client.room_get_state = AsyncMock(side_effect=RuntimeError("network down"))
    assert await room_creators(client, ROOM_ID) == frozenset()
    assert await room_creators(client, ROOM_ID) == frozenset()
    assert client.room_get_state.await_count == 2


async def test_non_state_responses_fail_closed() -> None:
    client = MagicMock()
    client.room_get_state = AsyncMock(return_value=MagicMock())
    assert await room_creators(client, ROOM_ID) == frozenset()


async def test_successful_fetch_is_cached_per_room() -> None:
    client = _client_with_state([_create_event(sender=SENDER)])
    assert await room_creators(client, ROOM_ID) == {SENDER}
    assert await room_creators(client, ROOM_ID) == {SENDER}
    assert client.room_get_state.await_count == 1


async def test_none_client_fails_closed() -> None:
    assert await room_creators(None, ROOM_ID) == frozenset()


# --- is_authorized_in_room ---------------------------------------------------


def _power_event(users: dict[str, Any], users_default: Any = 0) -> dict[str, Any]:
    return {
        "type": "m.room.power_levels",
        "state_key": "",
        "content": {"users": users, "users_default": users_default},
    }


async def test_cached_power_level_without_client_cannot_authorize() -> None:
    assert not await is_authorized_in_room(None, _room(users={SENDER: 100}), SENDER, 50)


async def test_fresh_power_level_authorizes_at_threshold() -> None:
    client = _client_with_state([_power_event({SENDER: 50})])
    assert await is_authorized_in_room(client, _room(), SENDER, 50)


async def test_removing_override_revokes_cached_admin_power() -> None:
    room = _room(users={SENDER: 100})
    client = _client_with_state([_power_event({SENDER: 100})])
    assert await is_authorized_in_room(client, room, SENDER, 50)

    client.room_get_state.return_value = RoomGetStateResponse(
        events=[_power_event({})], room_id=ROOM_ID
    )
    assert not await is_authorized_in_room(client, room, SENDER, 50)
    assert client.room_get_state.await_count == 2


@pytest.mark.parametrize("level", [49, "invalid", True, 50.5, None, {}, []])
async def test_insufficient_or_malformed_fresh_level_refused(level: Any) -> None:
    client = _client_with_state([_power_event({SENDER: level})])
    assert not await is_authorized_in_room(
        client, _room(users={SENDER: 100}), SENDER, 50
    )


async def test_fresh_default_applies_after_removing_override() -> None:
    client = _client_with_state([_power_event({}, users_default=50)])
    assert await is_authorized_in_room(client, _room(users={SENDER: 0}), SENDER, 50)


async def test_fresh_state_failure_cannot_reuse_cached_grant() -> None:
    client = _client_with_state([])
    client.room_get_state.side_effect = RuntimeError("network down")
    assert not await is_authorized_in_room(
        client, _room(users={SENDER: 100}), SENDER, 50
    )


async def test_error_response_cannot_reuse_cached_grant() -> None:
    client = _client_with_state([])
    client.room_get_state.return_value = MagicMock()
    assert not await is_authorized_in_room(
        client, _room(users={SENDER: 100}), SENDER, 50
    )


async def test_authorized_by_creator_despite_empty_users_map() -> None:
    client = _client_with_state([_create_event(sender=SENDER)])
    assert await is_authorized_in_room(client, _room(users_default=0), SENDER, 50)


async def test_authorized_by_creator_without_power_level_state() -> None:
    room = _room()
    room.power_levels = None
    client = _client_with_state([_create_event(sender=SENDER)])
    assert await is_authorized_in_room(client, room, SENDER, 50)


async def test_non_creator_refused_with_empty_users_map() -> None:
    client = _client_with_state([_create_event(sender="@creator:matrix.org")])
    assert not await is_authorized_in_room(client, _room(), SENDER, 50)


async def test_pre_v12_creator_is_refused_by_power_level() -> None:
    client = _client_with_state([_create_event(sender=SENDER, room_version="10")])
    assert not await is_authorized_in_room(client, _room(), SENDER, 50)


async def test_create_event_with_non_mapping_content_grants_no_creators() -> None:
    creators = await room_creators(
        _client_with_state(
            [{"type": "m.room.create", "sender": SENDER, "content": "not-a-mapping"}]
        ),
        ROOM_ID,
    )
    assert creators == frozenset()


async def test_create_event_with_non_numeric_room_version_grants_no_creators() -> None:
    creators = await room_creators(
        _client_with_state(
            [
                {
                    "type": "m.room.create",
                    "sender": SENDER,
                    "state_key": "",
                    "content": {"room_version": "twelve"},
                }
            ]
        ),
        ROOM_ID,
    )
    assert creators == frozenset()


async def test_fresh_power_level_lookup_timeout_refuses_cached_admin() -> None:
    """A hung homeserver state request must fail closed within its own budget."""
    client = _client_with_state([])
    cancelled = asyncio.Event()

    async def unavailable_state(*_args: Any) -> Any:
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    client.room_get_state.side_effect = unavailable_state
    with patch(
        "mmrelay.matrix.room_authorization.ROOM_STATE_FETCH_TIMEOUT_SECONDS", 0.01
    ):
        assert not await is_authorized_in_room(
            client, _room(users={SENDER: 100}), SENDER, 50
        )
    assert cancelled.is_set()


async def test_creator_lookup_timeout_is_not_cached() -> None:
    """Creator lookups also bound the state fetch and retry after failure."""
    client = _client_with_state([])

    async def unavailable_state(*_args: Any) -> Any:
        await asyncio.Future()

    client.room_get_state.side_effect = unavailable_state
    with patch(
        "mmrelay.matrix.room_authorization.ROOM_STATE_FETCH_TIMEOUT_SECONDS", 0.01
    ):
        assert await room_creators(client, ROOM_ID) == frozenset()
    client.room_get_state.side_effect = None
    client.room_get_state.return_value = RoomGetStateResponse(
        events=[_create_event(sender=SENDER)], room_id=ROOM_ID
    )
    assert await room_creators(client, ROOM_ID) == {SENDER}
    assert client.room_get_state.await_count == 2
