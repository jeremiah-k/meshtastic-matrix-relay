"""Matrix reaction event handling, including peer radio reactions and ID zero."""

import asyncio
from collections.abc import Callable, Coroutine
from concurrent.futures import Executor
from typing import TypeVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.matrix_utils import on_room_message, send_text_reply
from tests.constants import TEST_BOT_USER_ID, TEST_ROOM_ID, TEST_USER_ID
from tests.matrix_peer_fixtures import set_peer_content

T = TypeVar("T")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("reset_matrix_utils_globals"),
]


@pytest.mark.parametrize("mapped_text", ["original_text", None])
async def test_on_room_message_reaction_enabled(mock_room, test_config, mapped_text):
    # This is a reaction event
    """
    Verify that a Matrix reaction event is converted into a Meshtastic relay message and queued when reaction interactions are enabled.

    Asserts that a reaction produces a queued relay entry with a description indicating a local reaction and text that denotes a reacted state.
    """
    from nio import ReactionEvent

    class MockReactionEvent(ReactionEvent):
        def __init__(self, source: dict, sender: str, server_timestamp: int) -> None:
            self.source = source
            self.sender = sender
            self.server_timestamp = server_timestamp

    mock_event = MockReactionEvent(
        source={
            "content": {
                "m.relates_to": {
                    "event_id": "original_event_id",
                    "key": "👍",
                    "rel_type": "m.annotation",
                }
            }
        },
        sender="@user:matrix.org",
        server_timestamp=1234567890,
    )

    test_config["meshtastic"]["message_interactions"]["reactions"] = True

    real_loop = asyncio.get_running_loop()

    class DummyLoop:
        def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
            self._loop = loop

        def is_running(self) -> bool:
            return True

        def create_task(
            self, coro: Coroutine[object, object, object]
        ) -> asyncio.Task[object]:
            return self._loop.create_task(coro)

        async def run_in_executor(
            self, _executor: Executor | None, func: Callable[..., T], *args: object
        ) -> T:
            return func(*args)

    dummy_queue = MagicMock()
    dummy_queue.get_queue_size.return_value = 0

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch("mmrelay.matrix_utils.get_user_display_name", return_value="MockUser"),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
            return_value=(
                "12345",
                TEST_ROOM_ID,
                mapped_text,
                "test_mesh",
            ),
        ),
        patch(
            "mmrelay.matrix_utils.asyncio.get_running_loop",
            return_value=DummyLoop(real_loop),
        ),
        patch("mmrelay.matrix_utils.get_message_queue", return_value=dummy_queue),
        patch(
            "mmrelay.matrix_utils.queue_message", return_value=True
        ) as mock_queue_message,
        patch("mmrelay.matrix_utils.connect_meshtastic", return_value=MagicMock()),
        patch("mmrelay.matrix_utils.bot_start_time", 1234567880),
        patch("mmrelay.matrix_utils.config", test_config),
        patch("mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]),
        patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]),
    ):
        await on_room_message(mock_room, mock_event)

        mock_queue_message.assert_called_once()
        queued_args = mock_queue_message.call_args.args
        queued_kwargs = mock_queue_message.call_args.kwargs
        assert queued_kwargs["description"].startswith("Local reaction")
        assert queued_kwargs["reply_id"] == 12345
        assert "reacted" in queued_kwargs["text"]
        if mapped_text is None:
            assert 'to ""' in queued_kwargs["text"]
        assert queued_args[0] is send_text_reply


async def test_on_room_message_reaction_non_numeric_meshtastic_id(
    mock_room, test_config
):
    """Non-numeric native mapping IDs must not produce radio reactions."""
    from nio import ReactionEvent

    class MockReactionEvent(ReactionEvent):
        def __init__(self, source: dict, sender: str, server_timestamp: int) -> None:
            self.source = source
            self.sender = sender
            self.server_timestamp = server_timestamp

    mock_event = MockReactionEvent(
        source={
            "content": {
                "m.relates_to": {
                    "event_id": "original_event_id",
                    "key": "👍",
                    "rel_type": "m.annotation",
                }
            }
        },
        sender="@user:matrix.org",
        server_timestamp=1234567890,
    )

    test_config["meshtastic"]["message_interactions"]["reactions"] = True

    real_loop = asyncio.get_running_loop()

    class DummyLoop:
        def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
            self._loop = loop

        def is_running(self) -> bool:
            return True

        def create_task(
            self, coro: Coroutine[object, object, object]
        ) -> asyncio.Task[object]:
            return self._loop.create_task(coro)

        async def run_in_executor(
            self, _executor: Executor | None, func: Callable[..., T], *args: object
        ) -> T:
            return func(*args)

    dummy_queue = MagicMock()
    dummy_queue.get_queue_size.return_value = 0
    mock_meshtastic = MagicMock()

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch("mmrelay.matrix_utils.get_user_display_name", return_value="MockUser"),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
            return_value=(
                "mesh_id",
                TEST_ROOM_ID,
                "original_text",
                "test_mesh",
            ),
        ),
        patch(
            "mmrelay.matrix_utils.asyncio.get_running_loop",
            return_value=DummyLoop(real_loop),
        ),
        patch("mmrelay.matrix_utils.get_message_queue", return_value=dummy_queue),
        patch(
            "mmrelay.matrix_utils.queue_message", return_value=True
        ) as mock_queue_message,
        patch("mmrelay.matrix_utils.connect_meshtastic", return_value=mock_meshtastic),
        patch("mmrelay.matrix_utils.bot_start_time", 1234567880),
        patch("mmrelay.matrix_utils.config", test_config),
        patch("mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]),
        patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        await on_room_message(mock_room, mock_event)

        mock_queue_message.assert_not_called()
        assert any(
            "invalid radio target" in str(call)
            for call in mock_logger.debug.call_args_list
        )


async def test_on_room_message_reaction_mapped_int_id(mock_room, test_config):
    """An int meshtastic_id from the DB should be accepted as a valid reply ID."""
    from nio import ReactionEvent

    class MockReactionEvent(ReactionEvent):
        def __init__(self, source: dict, sender: str, server_timestamp: int) -> None:
            self.source = source
            self.sender = sender
            self.server_timestamp = server_timestamp

    mock_event = MockReactionEvent(
        source={
            "content": {
                "m.relates_to": {
                    "event_id": "original_event_id",
                    "key": "👍",
                    "rel_type": "m.annotation",
                }
            }
        },
        sender="@user:matrix.org",
        server_timestamp=1234567890,
    )

    test_config["meshtastic"]["message_interactions"]["reactions"] = True
    real_loop = asyncio.get_running_loop()

    class DummyLoop:
        def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
            self._loop = loop

        def is_running(self) -> bool:
            return True

        def create_task(
            self, coro: Coroutine[object, object, object]
        ) -> asyncio.Task[object]:
            return self._loop.create_task(coro)

        async def run_in_executor(
            self, _executor: Executor | None, func: Callable[..., T], *args: object
        ) -> T:
            return func(*args)

    dummy_queue = MagicMock()
    dummy_queue.get_queue_size.return_value = 0

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch("mmrelay.matrix_utils.get_user_display_name", return_value="MockUser"),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
            return_value=(12345, TEST_ROOM_ID, "original_text", "test_mesh"),
        ),
        patch(
            "mmrelay.matrix_utils.asyncio.get_running_loop",
            return_value=DummyLoop(real_loop),
        ),
        patch("mmrelay.matrix_utils.get_message_queue", return_value=dummy_queue),
        patch(
            "mmrelay.matrix_utils.queue_message", return_value=True
        ) as mock_queue_message,
        patch("mmrelay.matrix_utils.connect_meshtastic", return_value=MagicMock()),
        patch("mmrelay.matrix_utils.bot_start_time", 1234567880),
        patch("mmrelay.matrix_utils.config", test_config),
        patch("mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]),
        patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]),
    ):
        await on_room_message(mock_room, mock_event)

        mock_queue_message.assert_called_once()
        queued_args = mock_queue_message.call_args.args
        queued_kwargs = mock_queue_message.call_args.kwargs
        assert queued_kwargs["description"].startswith("Local reaction")
        assert queued_kwargs["reply_id"] == 12345
        assert "reacted" in queued_kwargs["text"]
        assert queued_args[0] is send_text_reply


async def test_on_room_message_reaction_unexpected_type_logs_warning(
    mock_room, test_config
):
    """Invalid native mapping IDs must not trigger an unstructured send."""
    from nio import ReactionEvent

    class MockReactionEvent(ReactionEvent):
        def __init__(self, source: dict, sender: str, server_timestamp: int) -> None:
            self.source = source
            self.sender = sender
            self.server_timestamp = server_timestamp

    mock_event = MockReactionEvent(
        source={
            "content": {
                "m.relates_to": {
                    "event_id": "original_event_id",
                    "key": "👍",
                    "rel_type": "m.annotation",
                }
            }
        },
        sender="@user:matrix.org",
        server_timestamp=1234567890,
    )

    test_config["meshtastic"]["message_interactions"]["reactions"] = True
    real_loop = asyncio.get_running_loop()

    class DummyLoop:
        def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
            self._loop = loop

        def is_running(self) -> bool:
            return True

        def create_task(
            self, coro: Coroutine[object, object, object]
        ) -> asyncio.Task[object]:
            return self._loop.create_task(coro)

        async def run_in_executor(
            self, _executor: Executor | None, func: Callable[..., T], *args: object
        ) -> T:
            return func(*args)

    dummy_queue = MagicMock()
    dummy_queue.get_queue_size.return_value = 0
    mock_meshtastic = MagicMock()

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch("mmrelay.matrix_utils.get_user_display_name", return_value="MockUser"),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
            return_value=(12.5, TEST_ROOM_ID, "original_text", "test_mesh"),
        ),
        patch(
            "mmrelay.matrix_utils.asyncio.get_running_loop",
            return_value=DummyLoop(real_loop),
        ),
        patch("mmrelay.matrix_utils.get_message_queue", return_value=dummy_queue),
        patch(
            "mmrelay.matrix_utils.queue_message", return_value=True
        ) as mock_queue_message,
        patch("mmrelay.matrix_utils.connect_meshtastic", return_value=mock_meshtastic),
        patch("mmrelay.matrix_utils.bot_start_time", 1234567880),
        patch("mmrelay.matrix_utils.config", test_config),
        patch("mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]),
        patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        await on_room_message(mock_room, mock_event)

        mock_queue_message.assert_not_called()
        assert any(
            "invalid radio target" in str(call)
            for call in mock_logger.debug.call_args_list
        )


@patch("mmrelay.matrix_utils.connect_meshtastic")
@patch("mmrelay.matrix_utils.queue_message")
@patch("mmrelay.matrix_utils.bot_start_time", 1234567880)
async def test_on_room_message_reaction_disabled(
    mock_queue_message,
    _mock_connect_meshtastic,
    mock_room,
    test_config,
):
    # This is a reaction event
    """
    Test that reaction events are not queued when reaction interactions are disabled in the configuration.
    """
    from nio import ReactionEvent

    class MockReactionEvent(ReactionEvent):
        def __init__(self, source, sender, server_timestamp):
            """
            Create a wrapper for a Matrix event that stores its raw payload, sender MXID, and server timestamp.

            Parameters:
                source (dict): Raw Matrix event JSON payload as received from the client/server.
                sender (str): Sender Matrix user ID (MXID), e.g. "@alice:example.org".
                server_timestamp (int | float): Server timestamp in milliseconds since the UNIX epoch.
            """
            self.source = source
            self.sender = sender
            self.server_timestamp = server_timestamp

    mock_event = MockReactionEvent(
        source={
            "content": {
                "m.relates_to": {
                    "event_id": "original_event_id",
                    "key": "👍",
                    "rel_type": "m.annotation",
                }
            }
        },
        sender=TEST_USER_ID,
        server_timestamp=1234567890,
    )

    test_config["meshtastic"]["message_interactions"]["reactions"] = False

    with (
        patch("mmrelay.matrix_utils.config", test_config),
        patch("mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]),
        patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]),
    ):
        # Mock the matrix client - use MagicMock to prevent coroutine warnings
        mock_matrix_client = MagicMock()
        with patch("mmrelay.matrix_utils.matrix_client", mock_matrix_client):
            # Run the function
            await on_room_message(mock_room, mock_event)

            # Assert that the message was not queued
            mock_queue_message.assert_not_called()


@pytest.mark.parametrize(
    ("reply_id", "reaction_key"),
    [
        (123, None),
        (123, "🎉"),
        (0, "🔥"),
    ],
)
async def test_on_room_message_remote_reaction_relay_success(
    monkeypatch, mock_room, reply_id, reaction_key
):
    """Remote meshnet reactions should be relayed to the local mesh when enabled."""
    from mmrelay.matrix_utils import RoomMessageEmote

    class MockEmote(RoomMessageEmote):  # type: ignore[misc]
        def __init__(self):
            self.source = {
                "content": {
                    "body": 'reacted :) to "hello"',
                }
            }
            self.sender = "@user:remote"
            self.server_timestamp = 1

    mock_event = MockEmote()

    config = {
        "meshtastic": {
            "meshnet_name": "local_mesh",
            "broadcast_enabled": True,
            "message_interactions": {"reactions": True, "replies": False},
        },
        "matrix_rooms": [{"id": mock_room.room_id, "meshtastic_channel": 0}],
        "matrix": {"bot_user_id": "@bot:matrix.org"},
    }

    class DummyInterface:
        def __init__(self):
            self.sendText = MagicMock()

    monkeypatch.setattr("mmrelay.matrix_utils.bot_start_time", 0, raising=False)
    monkeypatch.setattr("mmrelay.matrix_utils.config", config, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms", config["matrix_rooms"], raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.bot_user_id",
        config["matrix"]["bot_user_id"],
        raising=False,
    )

    await set_peer_content(
        mock_event,
        mock_room,
        config,
        message='reacted :) to "hello"',
        longname="RemoteUser",
        shortname="RU",
        meshnet_name="remote_mesh",
        portnum=1,
        emoji=reaction_key is not None,
        meshtastic_replyId=reply_id,
        reaction_key=reaction_key,
    )
    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            AsyncMock(return_value=(DummyInterface(), 0)),
        ),
        patch("mmrelay.matrix_utils.queue_message", return_value=True) as mock_queue,
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue.assert_called_once()
    queued_kwargs = mock_queue.call_args.kwargs
    assert 'reacted :) to "hello"' in queued_kwargs["text"]
    assert queued_kwargs["description"].startswith(
        "Remote reaction" if reaction_key is not None else "Message from"
    )


async def test_on_room_message_reaction_missing_mapping_logs_debug(
    monkeypatch, mock_room
):
    """Reactions without a message mapping should not be relayed."""
    from nio import ReactionEvent

    class MockReactionEvent(ReactionEvent):
        def __init__(self, source, sender, server_timestamp):
            self.source = source
            self.sender = sender
            self.server_timestamp = server_timestamp

    mock_event = MockReactionEvent(
        source={
            "content": {
                "m.relates_to": {
                    "rel_type": "m.annotation",
                    "event_id": "missing",
                    "key": "x",
                }
            }
        },
        sender=TEST_USER_ID,
        server_timestamp=1,
    )

    config = {
        "meshtastic": {
            "meshnet_name": "local_mesh",
            "message_interactions": {"reactions": True, "replies": False},
        },
        "matrix_rooms": [{"id": mock_room.room_id, "meshtastic_channel": 0}],
        "matrix": {"bot_user_id": TEST_BOT_USER_ID},
    }

    monkeypatch.setattr("mmrelay.matrix_utils.bot_start_time", 0, raising=False)
    monkeypatch.setattr("mmrelay.matrix_utils.config", config, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms", config["matrix_rooms"], raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.bot_user_id",
        config["matrix"]["bot_user_id"],
        raising=False,
    )

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
            return_value=None,
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue_message.assert_not_called()
    assert any(
        "Reaction target is not mapped" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


async def test_on_room_message_local_reaction_queue_failure_logs(
    monkeypatch, mock_room
):
    """Local reaction failures should log an error."""
    from nio import ReactionEvent

    class MockReactionEvent(ReactionEvent):
        def __init__(self, source, sender, server_timestamp):
            self.source = source
            self.sender = sender
            self.server_timestamp = server_timestamp

    mock_event = MockReactionEvent(
        source={
            "content": {
                "m.relates_to": {
                    "event_id": "orig",
                    "key": "x",
                    "rel_type": "m.annotation",
                }
            }
        },
        sender=TEST_USER_ID,
        server_timestamp=1,
    )

    config = {
        "meshtastic": {
            "meshnet_name": "local_mesh",
            "broadcast_enabled": True,
            "message_interactions": {"reactions": True, "replies": False},
        },
        "matrix_rooms": [{"id": mock_room.room_id, "meshtastic_channel": 0}],
        "matrix": {"bot_user_id": TEST_BOT_USER_ID},
    }

    monkeypatch.setattr("mmrelay.matrix_utils.bot_start_time", 0, raising=False)
    monkeypatch.setattr("mmrelay.matrix_utils.config", config, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms", config["matrix_rooms"], raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.bot_user_id",
        config["matrix"]["bot_user_id"],
        raising=False,
    )

    class DummyInterface:
        def __init__(self):
            self.sendText = MagicMock()

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
            return_value=("12345", mock_room.room_id, "text", "meshnet"),
        ),
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            AsyncMock(return_value=(DummyInterface(), 0)),
        ),
        patch(
            "mmrelay.matrix_utils.get_user_display_name",
            AsyncMock(return_value="User"),
        ),
        patch("mmrelay.matrix_utils.queue_message", return_value=False) as mock_queue,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue.assert_called_once()
    mock_logger.error.assert_any_call("Failed to relay local reaction to Meshtastic")


async def test_on_room_message_emote_reaction_uses_original_event_id(monkeypatch):
    """Emote reactions with m.relates_to should populate original_matrix_event_id for reaction handling."""
    from mmrelay.matrix_utils import RoomMessageEmote

    room_id = "!room:example"
    sender_id = "@user:example"

    # Minimal RoomMessageEmote-like object
    class MockEmote(RoomMessageEmote):  # type: ignore[misc]
        def __init__(self):
            self.source = {
                "content": {
                    "body": 'reacted 👍 to "something"',
                    "m.relates_to": {
                        "event_id": "orig_evt",
                        "key": "👍",
                        "rel_type": "m.annotation",
                    },
                }
            }
            self.sender = sender_id
            self.server_timestamp = 1

    mock_event = MockEmote()
    mock_room = MagicMock()
    mock_room.room_id = room_id
    mock_room.display_name = "Test Room"
    mock_room.encrypted = False

    # Patch globals/config for the handler
    monkeypatch.setattr(
        "mmrelay.matrix_utils.bot_user_id", "@bot:example", raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.bot_start_time", 0, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config",
        {
            "meshtastic": {
                "meshnet_name": "local",
                "message_interactions": {"reactions": True},
            },
            "matrix_rooms": [{"id": room_id, "meshtastic_channel": 0}],
        },
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": room_id, "meshtastic_channel": 0}],
        raising=False,
    )

    # Stub dependencies
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_meshtastic_prefix",
        lambda *_args, **_kwargs: "prefix ",
        raising=False,
    )

    mapping = ("0", room_id, "text", "meshnet")
    get_map_mock = MagicMock(return_value=mapping)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
        get_map_mock,
        raising=False,
    )

    class DummyQueue:
        def get_queue_size(self):
            return 1

    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_message_queue", lambda: DummyQueue(), raising=False
    )

    queue_mock = MagicMock(return_value=True)
    monkeypatch.setattr("mmrelay.matrix_utils.queue_message", queue_mock, raising=False)

    class DummyInterface:
        def __init__(self):
            self.sendText = MagicMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils._connect_meshtastic",
        AsyncMock(return_value=DummyInterface()),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_user_display_name",
        AsyncMock(return_value="User"),
        raising=False,
    )

    await on_room_message(mock_room, mock_event)

    get_map_mock.assert_called_once_with("orig_evt")
    queue_mock.assert_called_once()
    assert queue_mock.call_args.kwargs["reply_id"] == 0
