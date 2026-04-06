import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.matrix_utils import (
    format_reply_message,
    handle_matrix_reply,
    send_reply_to_meshtastic,
)
from tests.helpers import InlineExecutorLoop


@pytest.mark.asyncio
async def test_handle_matrix_reply_returns_false_on_send_failure():
    mock_room = MagicMock()
    mock_event = MagicMock()
    mock_event.sender = "@user:matrix.org"
    mock_room_config = {"meshtastic_channel": 0}
    mock_config = {"matrix_rooms": []}

    loop = asyncio.get_running_loop()
    with (
        patch(
            "mmrelay.matrix_utils.asyncio.get_running_loop",
            return_value=InlineExecutorLoop(loop),
        ),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
            return_value=(123, "!room", "text", "local"),
        ),
        patch(
            "mmrelay.matrix_utils.send_reply_to_meshtastic",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("mmrelay.matrix_utils.format_reply_message", return_value="reply"),
        patch(
            "mmrelay.matrix_utils.get_user_display_name",
            new_callable=AsyncMock,
            return_value="User",
        ),
    ):
        result = await handle_matrix_reply(
            mock_room,
            mock_event,
            "event_id",
            "text",
            mock_room_config,
            True,
            "meshnet",
            mock_config,
        )
        assert result is False


def test_format_reply_message_passes_user_id_to_prefix():
    config = {"meshtastic": {"prefix_enabled": True, "prefix_format": "{user}: "}}
    with patch("mmrelay.matrix_utils.get_meshtastic_prefix") as mock_prefix:
        mock_prefix.return_value = "@user:matrix.org: "
        format_reply_message(config, "Display", "hello", user_id="@user:matrix.org")
        mock_prefix.assert_called_once_with(config, "Display", "@user:matrix.org")


def test_format_reply_message_user_placeholder_resolves():
    config = {"meshtastic": {"prefix_enabled": True, "prefix_format": "[{user}]: "}}
    result = format_reply_message(
        config, "Display", "hello", user_id="@alice:matrix.org"
    )
    assert "@alice:matrix.org" in result


@pytest.mark.asyncio
async def test_handle_matrix_reply_success():
    """Test handle_matrix_reply processes reply successfully."""

    mock_room = MagicMock()
    mock_event = MagicMock()
    mock_room_config = {"meshtastic_channel": 0}
    mock_config = {"matrix_rooms": []}

    loop = asyncio.get_running_loop()
    with (
        patch(
            "mmrelay.matrix_utils.asyncio.get_running_loop",
            return_value=InlineExecutorLoop(loop),
        ),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id"
        ) as mock_db_lookup,
        patch(
            "mmrelay.matrix_utils.send_reply_to_meshtastic",
            new_callable=AsyncMock,
        ) as mock_send_reply,
        patch("mmrelay.matrix_utils.format_reply_message") as mock_format_reply,
        patch(
            "mmrelay.matrix_utils.get_user_display_name", new_callable=AsyncMock
        ) as mock_get_display_name,
    ):
        mock_db_lookup.return_value = (
            "orig_mesh_id",
            "!room123",
            "original text",
            "local",
        )
        mock_format_reply.return_value = "formatted reply"
        mock_get_display_name.return_value = "Test User"
        mock_send_reply.return_value = True

        result = await handle_matrix_reply(
            mock_room,
            mock_event,
            "reply_to_event_id",
            "reply text",
            mock_room_config,
            True,
            "local_meshnet",
            mock_config,
        )

        assert result is True
        mock_db_lookup.assert_called_once_with("reply_to_event_id")
        mock_format_reply.assert_called_once()
        mock_send_reply.assert_called_once()


@pytest.mark.asyncio
async def test_handle_matrix_reply_numeric_string_reply_id():
    """Numeric string meshtastic_id should be treated as a reply_id."""
    mock_room = MagicMock()
    mock_event = MagicMock()
    mock_room_config = {"meshtastic_channel": 0}
    mock_config = {"matrix_rooms": []}

    loop = asyncio.get_running_loop()
    with (
        patch(
            "mmrelay.matrix_utils.asyncio.get_running_loop",
            return_value=InlineExecutorLoop(loop),
        ),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id"
        ) as mock_db_lookup,
        patch(
            "mmrelay.matrix_utils.send_reply_to_meshtastic",
            new_callable=AsyncMock,
        ) as mock_send_reply,
        patch("mmrelay.matrix_utils.format_reply_message") as mock_format_reply,
        patch(
            "mmrelay.matrix_utils.get_user_display_name", new_callable=AsyncMock
        ) as mock_get_display_name,
    ):
        mock_db_lookup.return_value = ("123", "!room123", "original text", "remote")
        mock_format_reply.return_value = "formatted reply"
        mock_get_display_name.return_value = "Test User"
        mock_send_reply.return_value = True

        result = await handle_matrix_reply(
            mock_room,
            mock_event,
            "reply_to_event_id",
            "reply text",
            mock_room_config,
            True,
            "local_meshnet",
            mock_config,
        )

        assert result is True
        assert mock_send_reply.call_args.kwargs["reply_id"] == 123


@pytest.mark.asyncio
async def test_handle_matrix_reply_unexpected_id_type_broadcasts():
    """Unexpected meshtastic_id types should fall back to broadcast replies."""
    mock_room = MagicMock()
    mock_event = MagicMock()
    mock_room_config = {"meshtastic_channel": 0}
    mock_config = {"matrix_rooms": []}

    loop = asyncio.get_running_loop()
    with (
        patch(
            "mmrelay.matrix_utils.asyncio.get_running_loop",
            return_value=InlineExecutorLoop(loop),
        ),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id"
        ) as mock_db_lookup,
        patch(
            "mmrelay.matrix_utils.send_reply_to_meshtastic",
            new_callable=AsyncMock,
        ) as mock_send_reply,
        patch("mmrelay.matrix_utils.format_reply_message") as mock_format_reply,
        patch(
            "mmrelay.matrix_utils.get_user_display_name", new_callable=AsyncMock
        ) as mock_get_display_name,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        mock_db_lookup.return_value = (12.34, "!room123", "original text", "local")
        mock_format_reply.return_value = "formatted reply"
        mock_get_display_name.return_value = "Test User"
        mock_send_reply.return_value = True

        result = await handle_matrix_reply(
            mock_room,
            mock_event,
            "reply_to_event_id",
            "reply text",
            mock_room_config,
            True,
            "local_meshnet",
            mock_config,
        )

        assert result is True
        assert mock_send_reply.call_args.kwargs["reply_id"] is None
        mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_handle_matrix_reply_integer_id():
    """Integer meshtastic_id should be used directly as reply_id."""
    mock_room = MagicMock()
    mock_event = MagicMock()
    mock_room_config = {"meshtastic_channel": 0}
    mock_config = {"matrix_rooms": []}

    loop = asyncio.get_running_loop()
    with (
        patch(
            "mmrelay.matrix_utils.asyncio.get_running_loop",
            return_value=InlineExecutorLoop(loop),
        ),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id"
        ) as mock_db_lookup,
        patch(
            "mmrelay.matrix_utils.send_reply_to_meshtastic",
            new_callable=AsyncMock,
        ) as mock_send_reply,
        patch("mmrelay.matrix_utils.format_reply_message") as mock_format_reply,
        patch(
            "mmrelay.matrix_utils.get_user_display_name", new_callable=AsyncMock
        ) as mock_get_display_name,
    ):
        mock_db_lookup.return_value = (123, "!room123", "original text", "remote")
        mock_format_reply.return_value = "formatted reply"
        mock_get_display_name.return_value = "Test User"
        mock_send_reply.return_value = True

        result = await handle_matrix_reply(
            mock_room,
            mock_event,
            "reply_to_event_id",
            "reply text",
            mock_room_config,
            True,
            "local_meshnet",
            mock_config,
        )

        assert result is True
        assert mock_send_reply.call_args.kwargs["reply_id"] == 123


@pytest.mark.asyncio
async def test_handle_matrix_reply_original_not_found():
    """Test handle_matrix_reply when original message is not found."""

    mock_room = MagicMock()
    mock_event = MagicMock()
    mock_room_config = {"meshtastic_channel": 0}
    mock_config = {"matrix_rooms": []}

    loop = asyncio.get_running_loop()
    with (
        patch(
            "mmrelay.matrix_utils.asyncio.get_running_loop",
            return_value=InlineExecutorLoop(loop),
        ),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id"
        ) as mock_db_lookup,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        mock_db_lookup.return_value = None
        result = await handle_matrix_reply(
            mock_room,
            mock_event,
            "reply_to_event_id",
            "reply text",
            mock_room_config,
            True,
            "local_meshnet",
            mock_config,
        )
        assert result is False
        mock_db_lookup.assert_called_once_with("reply_to_event_id")
        mock_logger.debug.assert_called_once()


@pytest.mark.asyncio
async def test_send_reply_to_meshtastic_with_reply_id():
    """Test sending a reply to Meshtastic with reply_id."""
    mock_room_config = {"meshtastic_channel": 0}
    mock_room = MagicMock()
    mock_event = MagicMock()

    real_loop = asyncio.get_running_loop()

    class DummyLoop:
        def __init__(self, loop):
            self._loop = loop

        def is_running(self):
            return True

        def create_task(self, coro):
            return self._loop.create_task(coro)

        async def run_in_executor(self, _executor, func, *args):
            return func(*args)

    with (
        patch(
            "mmrelay.matrix_utils.config", {"meshtastic": {"broadcast_enabled": True}}
        ),
        patch(
            "mmrelay.matrix_utils.asyncio.get_running_loop",
            return_value=DummyLoop(real_loop),
        ),
        patch("mmrelay.matrix_utils.connect_meshtastic", return_value=MagicMock()),
        patch("mmrelay.matrix_utils.queue_message", return_value=True) as mock_queue,
    ):
        await send_reply_to_meshtastic(
            reply_message="Test reply",
            full_display_name="Alice",
            room_config=mock_room_config,
            room=mock_room,
            event=mock_event,
            text="Original text",
            storage_enabled=True,
            local_meshnet_name="TestMesh",
            reply_id=12345,
        )

        mock_queue.assert_called_once()
        call_kwargs = mock_queue.call_args.kwargs
        assert call_kwargs["reply_id"] == 12345


@pytest.mark.asyncio
async def test_send_reply_to_meshtastic_no_reply_id():
    """Test sending a reply to Meshtastic without reply_id."""
    mock_room_config = {"meshtastic_channel": 0}
    mock_room = MagicMock()
    mock_event = MagicMock()

    real_loop = asyncio.get_running_loop()

    class DummyLoop:
        def __init__(self, loop):
            self._loop = loop

        def is_running(self):
            return True

        def create_task(self, coro):
            return self._loop.create_task(coro)

        async def run_in_executor(self, _executor, func, *args):
            return func(*args)

    with (
        patch(
            "mmrelay.matrix_utils.config", {"meshtastic": {"broadcast_enabled": True}}
        ),
        patch(
            "mmrelay.matrix_utils.asyncio.get_running_loop",
            return_value=DummyLoop(real_loop),
        ),
        patch("mmrelay.matrix_utils.connect_meshtastic", return_value=MagicMock()),
        patch("mmrelay.matrix_utils.queue_message", return_value=True) as mock_queue,
    ):
        await send_reply_to_meshtastic(
            reply_message="Test reply",
            full_display_name="Alice",
            room_config=mock_room_config,
            room=mock_room,
            event=mock_event,
            text="Original text",
            storage_enabled=False,
            local_meshnet_name="TestMesh",
            reply_id=None,
        )

        mock_queue.assert_called_once()
        call_kwargs = mock_queue.call_args.kwargs
        assert call_kwargs.get("reply_id") is None


@pytest.mark.asyncio
async def test_send_reply_to_meshtastic_returns_when_interface_missing(monkeypatch):
    """Return early when the Meshtastic interface cannot be obtained."""
    monkeypatch.setattr(
        "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
        AsyncMock(return_value=(None, None)),
        raising=False,
    )
    mock_queue = MagicMock()
    monkeypatch.setattr("mmrelay.matrix_utils.queue_message", mock_queue, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config", {"meshtastic": {}}, raising=False
    )

    await send_reply_to_meshtastic(
        reply_message="Test reply",
        full_display_name="Alice",
        room_config={"meshtastic_channel": 0},
        room=MagicMock(),
        event=MagicMock(),
        text="Original text",
        storage_enabled=False,
        local_meshnet_name="TestMesh",
        reply_id=123,
    )

    mock_queue.assert_not_called()


@pytest.mark.asyncio
async def test_send_reply_to_meshtastic_structured_reply_queue_size(monkeypatch):
    """Structured replies log queue size details when queued."""
    mock_interface = MagicMock()
    mock_queue = MagicMock(return_value=True)
    queue_state = MagicMock()
    queue_state.get_queue_size.return_value = 2

    monkeypatch.setattr(
        "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
        AsyncMock(return_value=(mock_interface, 1)),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_meshtastic_config_value",
        MagicMock(return_value=True),
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.queue_message", mock_queue, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_message_queue", MagicMock(return_value=queue_state)
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config", {"meshtastic": {}}, raising=False
    )

    await send_reply_to_meshtastic(
        reply_message="Test reply",
        full_display_name="Alice",
        room_config={"meshtastic_channel": 0},
        room=MagicMock(),
        event=MagicMock(),
        text="Original text",
        storage_enabled=False,
        local_meshnet_name="TestMesh",
        reply_id=123,
    )

    assert mock_queue.called


@pytest.mark.asyncio
async def test_send_reply_to_meshtastic_structured_reply_failure(monkeypatch):
    """Structured replies return after queueing failures."""
    mock_interface = MagicMock()
    mock_queue = MagicMock(return_value=False)

    monkeypatch.setattr(
        "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
        AsyncMock(return_value=(mock_interface, 1)),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_meshtastic_config_value",
        MagicMock(return_value=True),
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.queue_message", mock_queue, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config", {"meshtastic": {}}, raising=False
    )

    await send_reply_to_meshtastic(
        reply_message="Test reply",
        full_display_name="Alice",
        room_config={"meshtastic_channel": 0},
        room=MagicMock(),
        event=MagicMock(),
        text="Original text",
        storage_enabled=False,
        local_meshnet_name="TestMesh",
        reply_id=123,
    )

    assert mock_queue.called


@pytest.mark.asyncio
async def test_send_reply_to_meshtastic_fallback_queue_size(monkeypatch):
    """Fallback replies log queue size details when queued."""
    mock_interface = MagicMock()
    mock_interface.sendText = MagicMock()
    mock_queue = MagicMock(return_value=True)
    queue_state = MagicMock()
    queue_state.get_queue_size.return_value = 2

    monkeypatch.setattr(
        "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
        AsyncMock(return_value=(mock_interface, 1)),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_meshtastic_config_value",
        MagicMock(return_value=True),
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.queue_message", mock_queue, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_message_queue", MagicMock(return_value=queue_state)
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config", {"meshtastic": {}}, raising=False
    )

    await send_reply_to_meshtastic(
        reply_message="Test reply",
        full_display_name="Alice",
        room_config={"meshtastic_channel": 0},
        room=MagicMock(),
        event=MagicMock(),
        text="Original text",
        storage_enabled=False,
        local_meshnet_name="TestMesh",
        reply_id=None,
    )

    assert mock_queue.called


@pytest.mark.asyncio
async def test_send_reply_to_meshtastic_fallback_failure(monkeypatch):
    """Fallback replies return after queueing failures."""
    mock_interface = MagicMock()
    mock_interface.sendText = MagicMock()
    mock_queue = MagicMock(return_value=False)

    monkeypatch.setattr(
        "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
        AsyncMock(return_value=(mock_interface, 1)),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_meshtastic_config_value",
        MagicMock(return_value=True),
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.queue_message", mock_queue, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config", {"meshtastic": {}}, raising=False
    )

    await send_reply_to_meshtastic(
        reply_message="Test reply",
        full_display_name="Alice",
        room_config={"meshtastic_channel": 0},
        room=MagicMock(),
        event=MagicMock(),
        text="Original text",
        storage_enabled=False,
        local_meshnet_name="TestMesh",
        reply_id=None,
    )

    assert mock_queue.called


@pytest.mark.asyncio
async def test_send_reply_to_meshtastic_defaults_config_when_missing():
    """send_reply_to_meshtastic should tolerate a missing global config."""
    room = MagicMock()
    room.room_id = "!room:example.org"
    event = MagicMock()
    event.event_id = "$event"
    room_config = {"meshtastic_channel": 0}

    with (
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            new_callable=AsyncMock,
            return_value=(MagicMock(), 0),
        ) as mock_get_interface,
        patch("mmrelay.matrix_utils.get_meshtastic_config_value", return_value=True),
        patch("mmrelay.matrix_utils.queue_message", return_value=True) as mock_queue,
        patch("mmrelay.matrix_utils._create_mapping_info", return_value=None),
        patch("mmrelay.matrix_utils.config", None),
    ):
        await send_reply_to_meshtastic(
            "reply",
            "Test User",
            room_config,
            room,
            event,
            "text",
            False,
            "local_meshnet",
        )

    mock_get_interface.assert_called_once()
    mock_queue.assert_called_once()


@pytest.mark.asyncio
async def test_handle_matrix_reply_local_user_not_treated_as_remote():
    """A local Matrix user replying to a remote-mesh message should NOT inherit
    the original message's meshnet name."""
    mock_room = MagicMock()
    mock_event = MagicMock()
    mock_event.sender = "@localuser:matrix.org"
    mock_room_config = {"meshtastic_channel": 0}
    mock_config = {"matrix_rooms": []}

    loop = asyncio.get_running_loop()
    with (
        patch(
            "mmrelay.matrix_utils.asyncio.get_running_loop",
            return_value=InlineExecutorLoop(loop),
        ),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id"
        ) as mock_db_lookup,
        patch(
            "mmrelay.matrix_utils.send_reply_to_meshtastic",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("mmrelay.matrix_utils.format_reply_message") as mock_format_reply,
        patch(
            "mmrelay.matrix_utils.get_user_display_name",
            new_callable=AsyncMock,
            return_value="Local User",
        ),
    ):
        mock_db_lookup.return_value = (123, "!room", "text", "RemoteMesh")
        mock_format_reply.return_value = "formatted reply"

        await handle_matrix_reply(
            mock_room,
            mock_event,
            "event_id",
            "reply text",
            mock_room_config,
            True,
            "LocalMesh",
            mock_config,
        )

        call_kwargs = mock_format_reply.call_args.kwargs
        assert call_kwargs["meshnet_name"] is None


def test_format_reply_message_remote_prefix_disabled():
    """Remote reply should fall back to hard-coded format when matrix prefix is disabled."""
    config = {"matrix": {"prefix_enabled": False}}
    result = format_reply_message(
        config,
        "Alice",
        "[LoRa/Mt.P]: Hello",
        longname="LoRa",
        shortname="LR",
        meshnet_name="Mt.P",
        local_meshnet_name="Forx",
        mesh_text_override="Hello",
    )

    assert result.startswith("LR/Mt.P:")
    assert "Hello" in result


def test_format_reply_message_remote_custom_prefix():
    """Remote reply should use a configured custom matrix prefix format."""
    config = {"matrix": {"prefix_enabled": True, "prefix_format": "<{short}/{mesh}> "}}
    result = format_reply_message(
        config,
        "Alice",
        "Hello",
        longname="LongName",
        shortname="LN",
        meshnet_name="RemoteMesh",
        local_meshnet_name="LocalMesh",
        mesh_text_override="Hello",
    )

    assert result.startswith("<LN/Remo>")
    assert "Hello" in result


def test_format_reply_message_remote_fallback_when_prefix_empty():
    """Remote reply falls back to hard-coded short/meshnet when matrix prefix is disabled."""
    config = {"matrix": {"prefix_enabled": False}}
    result = format_reply_message(
        config,
        "Alice",
        "Test",
        longname="LoRa",
        shortname="Trak",
        meshnet_name="Mt.Peaks",
        local_meshnet_name="Forx",
        mesh_text_override="Test",
    )

    assert result == "Trak/Mt.P: Test"
