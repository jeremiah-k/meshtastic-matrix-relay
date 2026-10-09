#!/usr/bin/env python3
"""
Tests for dispatching Matrix events from rooms outside matrix_rooms.

Covers the plugin-owned room contract introduced for dedicated rooms (such as
a remote-admin room) that must reach plugins without mesh relaying:

- _plugins_owning_room() ownership matching and error isolation
- _dispatch_unmapped_room_message() dispatch, filtering, and claiming
- on_room_message() offering unmapped-room events to owning plugins only
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from nio import ReactionEvent

from mmrelay.constants.formats import MATRIX_SUPPRESS_KEY
from mmrelay.matrix_utils import (
    _dispatch_unmapped_room_message,
    _plugins_owning_room,
    on_room_message,
)
from mmrelay.plugins.base_plugin import BasePlugin
from tests.constants import TEST_USER_ID

UNMAPPED_ROOM_ID = "!admin-room:matrix.org"


class FakePlugin:
    """Minimal plugin double for the unmapped-room contract."""

    def __init__(
        self,
        name: str,
        rooms: list[str] | None = None,
        handles: bool = False,
        claimed: bool = True,
        raise_on_handle: bool = False,
        raise_on_room_ids: bool = False,
    ) -> None:
        self.plugin_name = name
        self.handles_unmapped_rooms = handles
        self._rooms = rooms or []
        self.claimed = claimed
        self.raise_on_handle = raise_on_handle
        self.raise_on_room_ids = raise_on_room_ids
        self.calls: list[tuple[str, str]] = []

    def get_unmapped_room_ids(self) -> list[str]:
        if self.raise_on_room_ids:
            raise RuntimeError("room id failure")
        return self._rooms

    async def handle_room_message(self, room: Any, event: Any, text: str) -> bool:
        self.calls.append((room.room_id, text))
        if self.raise_on_handle:
            raise RuntimeError("handler failure")
        return self.claimed


def _make_room(room_id: str = UNMAPPED_ROOM_ID) -> MagicMock:
    room = MagicMock()
    room.room_id = room_id
    return room


def _make_text_event(body: str = "!admin help") -> MagicMock:
    event = MagicMock()
    event.sender = TEST_USER_ID
    event.body = body
    event.source = {"content": {"body": body}}
    event.server_timestamp = 1234567890
    return event


@pytest.mark.usefixtures("reset_matrix_utils_globals")
def test_plugins_owning_room_matches_opted_in_owner() -> None:
    """Only plugins that opted in and declared the room are returned."""
    owner = FakePlugin("owner", rooms=[UNMAPPED_ROOM_ID], handles=True)
    opted_in_other_room = FakePlugin(
        "other", rooms=["!elsewhere:matrix.org"], handles=True
    )
    opted_out_owner = FakePlugin("no_opt_in", rooms=[UNMAPPED_ROOM_ID])

    with patch(
        "mmrelay.plugin_loader.load_plugins",
        return_value=[owner, opted_in_other_room, opted_out_owner],
    ):
        assert _plugins_owning_room([UNMAPPED_ROOM_ID]) == [owner]


@pytest.mark.usefixtures("reset_matrix_utils_globals")
@patch("mmrelay.matrix_utils.logger")
def test_plugins_owning_room_isolates_room_id_errors(
    mock_logger: MagicMock,
) -> None:
    """A plugin whose get_unmapped_room_ids raises is skipped, not fatal."""
    broken = FakePlugin("broken", handles=True, raise_on_room_ids=True)
    owner = FakePlugin("owner", rooms=[UNMAPPED_ROOM_ID], handles=True)

    with patch("mmrelay.plugin_loader.load_plugins", return_value=[broken, owner]):
        assert _plugins_owning_room([UNMAPPED_ROOM_ID]) == [owner]
    mock_logger.exception.assert_called_once()


@pytest.mark.usefixtures("reset_matrix_utils_globals")
@patch("mmrelay.matrix_utils.logger")
async def test_dispatch_unmapped_room_message_offers_stripped_text(
    mock_logger: MagicMock,
) -> None:
    """The owning plugin receives the message with stripped text."""
    owner = FakePlugin("owner", rooms=[UNMAPPED_ROOM_ID], handles=True)
    event = _make_text_event("  !admin help  ")

    with patch("mmrelay.plugin_loader.load_plugins", return_value=[owner]):
        await _dispatch_unmapped_room_message(_make_room(), event)

    assert owner.calls == [(UNMAPPED_ROOM_ID, "!admin help")]
    mock_logger.info.assert_any_call(
        f"Processed command with plugin: owner from {TEST_USER_ID}"
    )


@pytest.mark.usefixtures("reset_matrix_utils_globals")
@patch("mmrelay.matrix_utils.logger")
async def test_dispatch_unmapped_room_message_isolates_handler_errors(
    mock_logger: MagicMock,
) -> None:
    """A raising handler is logged; a second owning plugin still runs."""
    broken = FakePlugin(
        "broken", rooms=[UNMAPPED_ROOM_ID], handles=True, raise_on_handle=True
    )
    owner = FakePlugin("owner", rooms=[UNMAPPED_ROOM_ID], handles=True)

    with patch("mmrelay.plugin_loader.load_plugins", return_value=[broken, owner]):
        await _dispatch_unmapped_room_message(_make_room(), _make_text_event())

    assert owner.calls
    mock_logger.error.assert_called_once()
    mock_logger.exception.assert_called_once()


@pytest.mark.usefixtures("reset_matrix_utils_globals")
async def test_dispatch_unmapped_room_message_skips_reactions() -> None:
    """Reaction events never reach unmapped-room plugins."""
    owner = FakePlugin("owner", rooms=[UNMAPPED_ROOM_ID], handles=True)
    reaction = MagicMock(spec=ReactionEvent)
    reaction.source = {"content": {"m.relates_to": {}}}

    with patch("mmrelay.plugin_loader.load_plugins", return_value=[owner]):
        await _dispatch_unmapped_room_message(_make_room(), reaction)

    assert owner.calls == []


@pytest.mark.usefixtures("reset_matrix_utils_globals")
async def test_dispatch_unmapped_room_message_skips_suppressed_events() -> None:
    """Events carrying the relay suppression key are ignored."""
    owner = FakePlugin("owner", rooms=[UNMAPPED_ROOM_ID], handles=True)
    event = _make_text_event()
    event.source["content"][MATRIX_SUPPRESS_KEY] = True

    with patch("mmrelay.plugin_loader.load_plugins", return_value=[owner]):
        await _dispatch_unmapped_room_message(_make_room(), event)

    assert owner.calls == []


@pytest.mark.usefixtures("reset_matrix_utils_globals")
async def test_dispatch_unmapped_room_message_skips_empty_text() -> None:
    """Blank messages are not dispatched."""
    owner = FakePlugin("owner", rooms=[UNMAPPED_ROOM_ID], handles=True)

    with patch("mmrelay.plugin_loader.load_plugins", return_value=[owner]):
        await _dispatch_unmapped_room_message(_make_room(), _make_text_event("   "))
        await _dispatch_unmapped_room_message(_make_room(), _make_text_event(""))

    assert owner.calls == []


@pytest.mark.usefixtures("reset_matrix_utils_globals")
@patch("mmrelay.matrix_utils.queue_message")
async def test_on_room_message_dispatches_unmapped_room_without_relay(
    mock_queue_message: MagicMock,
) -> None:
    """An unmapped-room message reaches the owning plugin and is never relayed."""
    owner = FakePlugin("owner", rooms=[UNMAPPED_ROOM_ID], handles=True)
    bystander = FakePlugin("bystander")
    room = _make_room()
    event = _make_text_event("!admin help")

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[owner, bystander]),
        patch("mmrelay.matrix_utils.bot_start_time", 1234567880),
        patch("mmrelay.matrix_utils.bot_user_id", "@bot:matrix.org"),
        patch("mmrelay.matrix_utils.matrix_rooms", [{"id": "!mapped:matrix.org"}]),
    ):
        await on_room_message(room, event)

    assert owner.calls == [(UNMAPPED_ROOM_ID, "!admin help")]
    assert bystander.calls == []
    mock_queue_message.assert_not_called()


@pytest.mark.usefixtures("reset_matrix_utils_globals")
@pytest.mark.parametrize("room_ids", [None, 42])
def test_plugins_owning_room_isolates_malformed_room_lists(room_ids: Any) -> None:
    """Malformed ownership declarations cannot disable a healthy owner."""
    broken = MagicMock()
    broken.handles_unmapped_rooms = True
    broken.get_unmapped_room_ids.return_value = room_ids
    owner = FakePlugin("owner", rooms=[UNMAPPED_ROOM_ID], handles=True)
    with patch("mmrelay.plugin_loader.load_plugins", return_value=[broken, owner]):
        assert _plugins_owning_room([UNMAPPED_ROOM_ID]) == [owner]


# --- sync handlers and the default room contract -------------------------------


class SyncPlugin(FakePlugin):
    """A plugin whose handler is a plain function returning bool."""

    async def _unused(self) -> None:  # pragma: no cover - typing shim only
        return None

    def handle_room_message(
        self, room: Any, event: Any, text: str
    ) -> bool:  # noqa: D102
        self.calls.append((room.room_id, text))
        return self.claimed


@pytest.mark.usefixtures("reset_matrix_utils_globals")
@patch("mmrelay.matrix_utils.logger")
async def test_dispatch_unmapped_room_message_accepts_sync_handlers(
    mock_logger: MagicMock,
) -> None:
    """A plugin with a non-async handler is claimed through the same path."""
    owner = SyncPlugin("sync-owner", rooms=[UNMAPPED_ROOM_ID], handles=True)

    with patch("mmrelay.plugin_loader.load_plugins", return_value=[owner]):
        await _dispatch_unmapped_room_message(_make_room(), _make_text_event())

    assert owner.calls == [(UNMAPPED_ROOM_ID, "!admin help")]
    mock_logger.info.assert_any_call(
        f"Processed command with plugin: sync-owner from {TEST_USER_ID}"
    )


class OptedOutDefaultRoomsPlugin(BasePlugin):
    """Opts into unmapped rooms but declares no rooms (base default)."""

    plugin_name = "opted_default_rooms"
    handles_unmapped_rooms = True

    async def handle_meshtastic_message(
        self, packet: Any, formatted_message: str, longname: str, meshnet_name: str
    ) -> bool:
        return False

    async def handle_room_message(self, room: Any, event: Any, text: str) -> bool:
        return False


@pytest.mark.usefixtures("reset_matrix_utils_globals")
def test_opted_in_plugin_without_rooms_owns_nothing() -> None:
    """The base get_unmapped_room_ids default keeps an opted-in plugin inert."""
    plugin = OptedOutDefaultRoomsPlugin()
    assert plugin.get_unmapped_room_ids() == []

    with patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]):
        assert _plugins_owning_room([UNMAPPED_ROOM_ID]) == []
