import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import nio
import pytest

from mmrelay.constants.formats import (
    DETECTION_SENSOR_APP,
    MATRIX_SUPPRESS_KEY,
    TEXT_MESSAGE_APP,
)
from mmrelay.constants.network import (
    MATRIX_CLOCK_ROLLBACK_DISABLE_MS,
    MATRIX_STALE_STARTUP_EVENT_DROP_MS,
    MATRIX_STARTUP_STALE_FILTER_WINDOW_MS,
    MATRIX_STARTUP_TIMESTAMP_TOLERANCE_MS,
)
from mmrelay.matrix_utils import (
    NioLocalTransportError,
    _send_matrix_message_with_retry,
    bot_command,
    matrix_relay,
    on_room_message,
)
from tests.constants import (
    TEST_BOT_USER_ID,
    TEST_ROOM_ID,
    TEST_USER_ID,
)

pytestmark = pytest.mark.asyncio

RoomSendError = nio.RoomSendError


def _make_room_send_error(message="API error"):
    obj = MagicMock(spec=RoomSendError)
    obj.message = message
    return obj


@pytest.mark.asyncio
async def test_send_matrix_message_with_retry_api_error_then_success():
    mock_client = MagicMock()
    mock_client.rooms = {"!room:matrix.org": MagicMock(encrypted=False)}
    error_response = _make_room_send_error("API error")
    success_response = MagicMock(event_id="$event123")
    call_count = 0

    async def _room_send_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return error_response
        return success_response

    mock_client.room_send = _room_send_side_effect

    with (
        patch("mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock),
        patch("mmrelay.matrix_utils.logger"),
    ):
        result = await _send_matrix_message_with_retry(
            mock_client,
            "!room:matrix.org",
            {"msgtype": "m.text", "body": "hi"},
            max_retries=3,
            base_delay=0.01,
            max_delay=0.1,
        )

    assert result is not None
    assert result.event_id == "$event123"
    assert call_count == 2


@pytest.mark.asyncio
async def test_send_matrix_message_with_retry_api_error_all_attempts():
    mock_client = MagicMock()
    mock_client.rooms = {"!room:matrix.org": MagicMock(encrypted=False)}
    mock_client.room_send = AsyncMock(
        return_value=_make_room_send_error("persistent error")
    )

    with (
        patch("mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock),
        patch("mmrelay.matrix_utils.logger"),
    ):
        result = await _send_matrix_message_with_retry(
            mock_client,
            "!room:matrix.org",
            {"msgtype": "m.text", "body": "hi"},
            max_retries=3,
            base_delay=0.01,
            max_delay=0.1,
        )

    assert result is None
    assert mock_client.room_send.await_count == 4


@pytest.mark.asyncio
async def test_matrix_relay_missing_meshnet_name_safe():
    mock_client = MagicMock()
    mock_room = MagicMock(encrypted=False)
    mock_client.rooms = {"!room:matrix.org": mock_room}
    mock_client.room_send = AsyncMock(return_value=MagicMock(event_id="$event123"))

    config = {
        "meshtastic": {},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch(
            "mmrelay.matrix_utils.connect_matrix",
            new_callable=AsyncMock,
            return_value=mock_client,
        ),
        patch("mmrelay.matrix_utils.config", config),
        patch("mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock),
        patch("mmrelay.matrix_utils.logger"),
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": False},
        ),
        patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False),
        patch("mmrelay.matrix_utils._get_msgs_to_keep_config", return_value=0),
        patch(
            "mmrelay.matrix_utils._escape_leading_prefix_for_markdown",
            return_value=("test", False),
        ),
        patch("mmrelay.matrix_utils.join_matrix_room", new_callable=AsyncMock),
    ):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="test",
            longname="A",
            shortname="B",
            meshnet_name="",
            portnum=1,
            meshtastic_id=123,
        )


@patch("mmrelay.matrix_utils.config", {"meshtastic": {"meshnet_name": "TestMesh"}})
@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.get_interaction_settings")
@patch("mmrelay.matrix_utils.message_storage_enabled")
@patch("mmrelay.matrix_utils.logger")
async def test_matrix_relay_simple_message(
    _mock_logger, mock_storage_enabled, mock_get_interactions, mock_connect_matrix
):
    """Test that a plain text message is relayed with m.text semantics and metadata."""

    mock_get_interactions.return_value = {"reactions": False, "replies": False}
    mock_storage_enabled.return_value = False

    mock_matrix_client = MagicMock()
    mock_matrix_client.room_send = AsyncMock(
        return_value=MagicMock(event_id="$event123")
    )
    mock_connect_matrix.return_value = mock_matrix_client

    await matrix_relay(
        room_id="!room:matrix.org",
        message="Hello Matrix",
        longname="Alice",
        shortname="A",
        meshnet_name="TestMesh",
        portnum=1,
    )

    mock_matrix_client.room_send.assert_called_once()
    kwargs = mock_matrix_client.room_send.call_args.kwargs
    assert kwargs["room_id"] == "!room:matrix.org"
    content = kwargs["content"]
    assert content["msgtype"] == "m.text"
    assert content["body"] == "Hello Matrix"
    assert content["formatted_body"] == "Hello Matrix"
    assert content["meshtastic_meshnet"] == "TestMesh"
    assert content["meshtastic_portnum"] == 1


@patch("mmrelay.matrix_utils.config", {"meshtastic": {"meshnet_name": "TestMesh"}})
@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.get_interaction_settings")
@patch("mmrelay.matrix_utils.message_storage_enabled")
@patch("mmrelay.matrix_utils.logger")
async def test_matrix_relay_emote_message(
    _mock_logger, mock_storage_enabled, mock_get_interactions, mock_connect_matrix
):
    """
    Test that an emote message is relayed to Matrix with the correct message type.
    Verifies that when the `emote` flag is set, the relayed message is sent as an `m.emote` type event to the specified Matrix room.
    """
    mock_get_interactions.return_value = {"reactions": False, "replies": False}
    mock_storage_enabled.return_value = False

    mock_matrix_client = MagicMock()
    mock_matrix_client.room_send = AsyncMock()
    mock_connect_matrix.return_value = mock_matrix_client

    mock_response = MagicMock()
    mock_response.event_id = "$event123"
    mock_matrix_client.room_send.return_value = mock_response

    await matrix_relay(
        room_id="!room:matrix.org",
        message="waves",
        longname="Alice",
        shortname="A",
        meshnet_name="TestMesh",
        portnum=1,
        emote=True,
    )

    mock_matrix_client.room_send.assert_called_once()
    call_args = mock_matrix_client.room_send.call_args
    content = call_args[1]["content"]
    assert content["msgtype"] == "m.emote"


@patch("mmrelay.matrix_utils.config", {"meshtastic": {"meshnet_name": "TestMesh"}})
@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.get_interaction_settings")
@patch("mmrelay.matrix_utils.message_storage_enabled")
@patch("mmrelay.matrix_utils.logger")
async def test_matrix_relay_client_none(
    _mock_logger, mock_storage_enabled, mock_get_interactions, mock_connect_matrix
):
    """
    Test that `matrix_relay` returns early and logs an error if the Matrix client cannot be initialized.
    """
    mock_get_interactions.return_value = {"reactions": False, "replies": False}
    mock_storage_enabled.return_value = False

    mock_connect_matrix.return_value = None

    await matrix_relay(
        room_id="!room:matrix.org",
        message="Hello world",
        longname="Alice",
        shortname="A",
        meshnet_name="TestMesh",
        portnum=1,
    )

    _mock_logger.error.assert_called_with(
        "Matrix client initialization failed after 3 attempts. Message to room !room:matrix.org may be lost."
    )


@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.logger")
async def test_matrix_relay_no_config_returns(mock_logger, mock_connect_matrix):
    """matrix_relay should return if config is missing."""
    mock_client = MagicMock()
    mock_client.room_send = AsyncMock()
    mock_connect_matrix.return_value = mock_client

    with patch("mmrelay.matrix_utils.config", None):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="Hello Matrix",
            longname="Alice",
            shortname="A",
            meshnet_name="TestMesh",
            portnum=1,
        )

    mock_logger.error.assert_any_call(
        "No configuration available. Cannot relay message to Matrix."
    )
    mock_client.room_send.assert_not_called()


@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.get_interaction_settings")
@patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False)
@patch("mmrelay.matrix_utils.logger")
async def test_matrix_relay_legacy_msg_map_warning(
    mock_logger, _mock_storage_enabled, mock_get_interactions, mock_connect_matrix
):
    """Legacy db.msg_map configuration should log a warning."""
    mock_get_interactions.return_value = {"reactions": False, "replies": False}

    mock_client = MagicMock()
    mock_client.rooms = {"!room:matrix.org": MagicMock(encrypted=False)}
    mock_client.room_send = AsyncMock(return_value=MagicMock(event_id="$event123"))
    mock_connect_matrix.return_value = mock_client

    config = {
        "meshtastic": {"meshnet_name": "TestMesh"},
        "db": {"msg_map": {"msgs_to_keep": 10}},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with patch("mmrelay.matrix_utils.config", config):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="Hello Matrix",
            longname="Alice",
            shortname="A",
            meshnet_name="TestMesh",
            portnum=1,
        )

    assert any(
        "Using 'db.msg_map' configuration (legacy)" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.get_interaction_settings")
@patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False)
async def test_matrix_relay_markdown_processing(
    _mock_storage_enabled, mock_get_interactions, mock_connect_matrix
):
    """Markdown content should be rendered and cleaned before sending."""
    mock_get_interactions.return_value = {"reactions": False, "replies": False}

    mock_client = MagicMock()
    mock_client.rooms = {"!room:matrix.org": MagicMock(encrypted=False)}
    mock_client.room_send = AsyncMock(return_value=MagicMock(event_id="$event123"))
    mock_connect_matrix.return_value = mock_client

    config = {
        "meshtastic": {"meshnet_name": "TestMesh"},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    fake_markdown = SimpleNamespace(markdown=lambda _text: "<strong>bold</strong>")
    fake_bleach = SimpleNamespace(clean=lambda raw_html, **_kwargs: raw_html)

    with (
        patch("mmrelay.matrix_utils.config", config),
        patch.dict("sys.modules", {"markdown": fake_markdown, "bleach": fake_bleach}),
    ):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="**bold**",
            longname="Alice",
            shortname="A",
            meshnet_name="TestMesh",
            portnum=1,
        )

    content = mock_client.room_send.call_args.kwargs["content"]
    assert content["formatted_body"] == "<strong>bold</strong>"
    assert content["body"] == "**bold**"


@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.get_interaction_settings")
@patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False)
async def test_matrix_relay_importerror_fallback(
    _mock_storage_enabled, mock_get_interactions, mock_connect_matrix
):
    """Markdown import errors should fall back to escaped HTML."""
    mock_get_interactions.return_value = {"reactions": False, "replies": False}

    mock_client = MagicMock()
    mock_client.rooms = {"!room:matrix.org": MagicMock(encrypted=False)}
    mock_client.room_send = AsyncMock(return_value=MagicMock(event_id="$event123"))
    mock_connect_matrix.return_value = mock_client

    config = {
        "meshtastic": {"meshnet_name": "TestMesh"},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("markdown", "bleach"):
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    with (
        patch("mmrelay.matrix_utils.config", config),
        patch("builtins.__import__", side_effect=fake_import),
    ):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="<b>hi</b>",
            longname="Alice",
            shortname="A",
            meshnet_name="TestMesh",
            portnum=1,
        )

    content = mock_client.room_send.call_args.kwargs["content"]
    assert content["formatted_body"] == "&lt;b&gt;hi&lt;/b&gt;"
    assert content["body"] == "<b>hi</b>"


@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.get_interaction_settings")
@patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False)
async def test_matrix_relay_reply_formatting(
    _mock_storage_enabled, mock_get_interactions, mock_connect_matrix
):
    """Replies should include m.in_reply_to and mx-reply formatting."""
    mock_get_interactions.return_value = {"reactions": False, "replies": False}

    mock_room = MagicMock()
    mock_room.encrypted = False
    mock_room.display_name = "Room"

    mock_client = MagicMock()
    mock_client.user_id = "@bot:matrix.org"
    mock_client.rooms = {"!room:matrix.org": mock_room}
    mock_client.room_send = AsyncMock(return_value=MagicMock(event_id="$event123"))
    mock_connect_matrix.return_value = mock_client

    config = {
        "meshtastic": {"meshnet_name": "TestMesh"},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.config", config),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
            return_value=("mesh_id", "!room:matrix.org", "original", "TestMesh"),
        ),
    ):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="Reply text",
            longname="Alice",
            shortname="A",
            meshnet_name="TestMesh",
            portnum=1,
            reply_to_event_id="$orig",
        )

    content = mock_client.room_send.call_args.kwargs["content"]
    assert content["m.relates_to"]["m.in_reply_to"]["event_id"] == "$orig"
    assert content["formatted_body"].startswith("<mx-reply>")
    assert "In reply to" in content["formatted_body"]


@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.get_interaction_settings")
@patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False)
async def test_matrix_relay_reply_plain_text_not_html_escaped(
    _mock_storage_enabled, mock_get_interactions, mock_connect_matrix
):
    """Reply plain body should contain raw original text, not HTML-escaped entities."""
    mock_get_interactions.return_value = {"reactions": False, "replies": False}

    mock_room = MagicMock()
    mock_room.encrypted = False
    mock_room.display_name = "Room"

    mock_client = MagicMock()
    mock_client.user_id = "@bot:matrix.org"
    mock_client.rooms = {"!room:matrix.org": mock_room}
    mock_client.room_send = AsyncMock(return_value=MagicMock(event_id="$event123"))
    mock_connect_matrix.return_value = mock_client

    config = {
        "meshtastic": {"meshnet_name": "TestMesh"},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.config", config),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
            return_value=(
                "mesh_id",
                "!room:matrix.org",
                "<b>bold & stuff</b>",
                "TestMesh",
            ),
        ),
    ):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="Reply text",
            longname="Alice",
            shortname="A",
            meshnet_name="TestMesh",
            portnum=1,
            reply_to_event_id="$orig",
        )

    content = mock_client.room_send.call_args.kwargs["content"]
    assert "<b>bold & stuff</b>" in content["body"]
    assert "&lt;b&gt;bold &amp; stuff&lt;/b&gt;" not in content["body"]
    assert "&lt;b&gt;bold &amp; stuff&lt;/b&gt;" in content["formatted_body"]


@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.get_interaction_settings")
@patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False)
@patch("mmrelay.matrix_utils.logger")
async def test_matrix_relay_e2ee_blocked(
    mock_logger, _mock_storage_enabled, mock_get_interactions, mock_connect_matrix
):
    """Encrypted rooms should block sends when E2EE is disabled."""
    mock_get_interactions.return_value = {"reactions": False, "replies": False}

    mock_room = MagicMock()
    mock_room.encrypted = True
    mock_room.display_name = "Secret"

    mock_client = MagicMock()
    mock_client.e2ee_enabled = False
    mock_client.rooms = {"!room:matrix.org": mock_room}
    mock_client.room_send = AsyncMock()
    mock_connect_matrix.return_value = mock_client

    config = {
        "meshtastic": {"meshnet_name": "TestMesh"},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.config", config),
        patch("mmrelay.matrix_utils._get_e2ee_error_message", return_value="E2EE off"),
    ):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="Hello Matrix",
            longname="Alice",
            shortname="A",
            meshnet_name="TestMesh",
            portnum=1,
        )

    mock_client.room_send.assert_not_called()
    assert any("BLOCKED" in call.args[0] for call in mock_logger.error.call_args_list)


@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.get_interaction_settings")
@patch("mmrelay.matrix_utils.message_storage_enabled", return_value=True)
async def test_matrix_relay_store_and_prune_message_map(
    _mock_storage_enabled, mock_get_interactions, mock_connect_matrix
):
    """Stored message mappings should be pruned when configured."""
    mock_get_interactions.return_value = {"reactions": True, "replies": False}

    mock_client = MagicMock()
    mock_client.rooms = {"!room:matrix.org": MagicMock(encrypted=False)}
    mock_client.room_send = AsyncMock(return_value=MagicMock(event_id="$event123"))
    mock_connect_matrix.return_value = mock_client

    config = {
        "meshtastic": {"meshnet_name": "TestMesh"},
        "database": {"msg_map": {"msgs_to_keep": 1}},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.config", config),
        patch(
            "mmrelay.matrix_utils.async_store_message_map", new_callable=AsyncMock
        ) as mock_store,
        patch(
            "mmrelay.matrix_utils.async_prune_message_map", new_callable=AsyncMock
        ) as mock_prune,
    ):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="Hello Matrix",
            longname="Alice",
            shortname="A",
            meshnet_name="TestMesh",
            portnum=1,
            meshtastic_id=123,
        )

    mock_store.assert_awaited_once()
    mock_prune.assert_awaited_once_with(1)


@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.get_interaction_settings")
@patch("mmrelay.matrix_utils.message_storage_enabled", return_value=True)
@patch("mmrelay.matrix_utils.logger")
async def test_matrix_relay_store_failure_logs(
    mock_logger, _mock_storage_enabled, mock_get_interactions, mock_connect_matrix
):
    """Storage errors should be logged and not raise."""
    mock_get_interactions.return_value = {"reactions": True, "replies": False}

    mock_client = MagicMock()
    mock_client.rooms = {"!room:matrix.org": MagicMock(encrypted=False)}
    mock_client.room_send = AsyncMock(return_value=MagicMock(event_id="$event123"))
    mock_connect_matrix.return_value = mock_client

    config = {
        "meshtastic": {"meshnet_name": "TestMesh"},
        "database": {"msg_map": {"msgs_to_keep": 1}},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.config", config),
        patch(
            "mmrelay.matrix_utils.async_store_message_map",
            new_callable=AsyncMock,
            side_effect=Exception("store fail"),
        ),
    ):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="Hello Matrix",
            longname="Alice",
            shortname="A",
            meshnet_name="TestMesh",
            portnum=1,
            meshtastic_id=123,
        )

    assert any(
        "Error storing message map" in call.args[0]
        for call in mock_logger.error.call_args_list
    )


@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.get_interaction_settings")
@patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False)
@patch("mmrelay.matrix_utils.logger")
async def test_matrix_relay_reply_missing_mapping_logs_warning(
    mock_logger, _mock_storage_enabled, mock_get_interactions, mock_connect_matrix
):
    """Missing reply mappings should warn but still send."""
    mock_get_interactions.return_value = {"reactions": False, "replies": False}

    mock_room = MagicMock()
    mock_room.encrypted = False
    mock_room.display_name = "Room"

    mock_client = MagicMock()
    mock_client.user_id = "@bot:matrix.org"
    mock_client.rooms = {"!room:matrix.org": mock_room}
    mock_client.room_send = AsyncMock(return_value=MagicMock(event_id="$event123"))
    mock_connect_matrix.return_value = mock_client

    config = {
        "meshtastic": {"meshnet_name": "TestMesh"},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.config", config),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
            return_value=None,
        ),
    ):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="Reply text",
            longname="Alice",
            shortname="A",
            meshnet_name="TestMesh",
            portnum=1,
            reply_to_event_id="$missing",
        )

    mock_client.room_send.assert_called_once()
    assert any(
        "Could not find original message for reply_to_event_id" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.get_interaction_settings")
@patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False)
@patch("mmrelay.matrix_utils.logger")
async def test_matrix_relay_send_timeout_logs_and_returns(
    mock_logger, _mock_storage_enabled, mock_get_interactions, mock_connect_matrix
):
    """Timeouts during room_send should be logged with retry information and return."""
    mock_get_interactions.return_value = {"reactions": False, "replies": False}

    mock_client = MagicMock()
    mock_client.rooms = {"!room:matrix.org": MagicMock(encrypted=False)}
    mock_client.room_send = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_connect_matrix.return_value = mock_client

    config = {
        "meshtastic": {"meshnet_name": "TestMesh"},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with patch("mmrelay.matrix_utils.config", config):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="Hello Matrix",
            longname="Alice",
            shortname="A",
            meshnet_name="TestMesh",
            portnum=1,
        )

    mock_logger.exception.assert_any_call(
        "Timeout sending message to Matrix room !room:matrix.org after 4 attempts"
    )


@patch("mmrelay.matrix_utils.logger")
async def test_send_matrix_message_with_retry_reuses_tx_id_across_retries(_mock_logger):
    """Retry attempts must reuse one Matrix transaction ID to avoid duplicate sends."""
    mock_client = MagicMock()
    mock_client.rooms = {"!room:matrix.org": MagicMock(encrypted=False)}
    success_response = MagicMock(event_id="$event123")
    mock_client.room_send = AsyncMock(
        side_effect=[asyncio.TimeoutError(), asyncio.TimeoutError(), success_response]
    )

    with patch("mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock):
        response = await _send_matrix_message_with_retry(
            matrix_client=mock_client,
            room_id="!room:matrix.org",
            content={"msgtype": "m.text", "body": "Hello Matrix"},
            max_retries=3,
            base_delay=0.01,
            max_delay=0.1,
        )

    assert response is success_response
    assert mock_client.room_send.await_count == 3
    tx_ids = [
        call.kwargs.get("tx_id") for call in mock_client.room_send.await_args_list
    ]
    assert len(set(tx_ids)) == 1
    assert isinstance(tx_ids[0], str)
    assert tx_ids[0].startswith("mmrelay-")


@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.get_interaction_settings")
@patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False)
@patch("mmrelay.matrix_utils.logger")
async def test_matrix_relay_send_nio_error_logs_and_returns(
    mock_logger, _mock_storage_enabled, mock_get_interactions, mock_connect_matrix
):
    """NIO send errors should be logged and return."""
    mock_get_interactions.return_value = {"reactions": False, "replies": False}

    mock_client = MagicMock()
    mock_client.rooms = {"!room:matrix.org": MagicMock(encrypted=False)}
    mock_client.room_send = AsyncMock(side_effect=NioLocalTransportError("fail"))
    mock_connect_matrix.return_value = mock_client

    config = {
        "meshtastic": {"meshnet_name": "TestMesh"},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with patch("mmrelay.matrix_utils.config", config):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="Hello Matrix",
            longname="Alice",
            shortname="A",
            meshnet_name="TestMesh",
            portnum=1,
        )

    assert any(
        "Error sending message to Matrix room" in call.args[0]
        for call in mock_logger.exception.call_args_list
    )


@pytest.mark.asyncio
async def test_matrix_relay_logs_unexpected_exception():
    """Unexpected errors in matrix_relay should be logged and not raised."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.room_send = AsyncMock()

    config = {
        "meshtastic": {"meshnet_name": "TestMesh"},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.config", config),
        patch("mmrelay.matrix_utils.connect_matrix", return_value=mock_client),
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": False},
        ),
        patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False),
        patch(
            "mmrelay.matrix_utils.join_matrix_room",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        await matrix_relay(
            room_id="!room:matrix.org",
            message="Hello",
            longname="Alice",
            shortname="A",
            meshnet_name="TestMesh",
            portnum=1,
        )

    mock_logger.exception.assert_called_once_with(
        "Error sending radio message to matrix room !room:matrix.org"
    )


# ============================================================================
# on_room_message tests migrated from test_matrix_utils.py
# ============================================================================


@contextlib.contextmanager
def _patch_on_room_message_time_context(
    test_config,
    bot_start_time,
    bot_start_monotonic_secs,
    current_time,
    current_monotonic,
):
    """
    Context manager that patches all time and module-level dependencies for on_room_message tests.

    Patches: load_plugins, get_user_display_name, get_message_queue, queue_message,
    connect_meshtastic, config, matrix_rooms, bot_user_id, bot_start_time,
    bot_start_monotonic_secs, time.time, and time.monotonic.

    Yields:
        MagicMock: The mock_queue_message mock for assertions.
    """
    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch(
            "mmrelay.matrix_utils.get_user_display_name",
            AsyncMock(return_value="user"),
        ),
        patch("mmrelay.matrix_utils.get_message_queue") as mock_get_message_queue,
        patch(
            "mmrelay.matrix_utils.queue_message", return_value=True
        ) as mock_queue_message,
        patch("mmrelay.matrix_utils.connect_meshtastic", return_value=MagicMock()),
        patch("mmrelay.matrix_utils.config", test_config),
        patch("mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]),
        patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]),
        patch("mmrelay.matrix_utils.bot_start_time", bot_start_time),
        patch(
            "mmrelay.matrix_utils.bot_start_monotonic_secs", bot_start_monotonic_secs
        ),
        patch("mmrelay.matrix_utils.time.time", return_value=current_time),
        patch("mmrelay.matrix_utils.time.monotonic", return_value=current_monotonic),
    ):
        mock_get_message_queue.return_value.get_queue_size.return_value = 0
        yield mock_queue_message


async def test_on_room_message_simple_text(
    mock_room,
    mock_event,
    test_config,
):
    """
    Test that a non-reaction text message event is processed and queued for Meshtastic relay.

    Ensures that when a user sends a simple text message, the message is correctly queued with the expected content for relaying.
    """

    # Create a proper async mock function
    async def mock_get_user_display_name_func(*args, **kwargs):
        """
        Provides an async test helper that always returns the fixed display name "user".

        Accepts any positional and keyword arguments and ignores them.

        Returns:
            str: The display name "user".
        """
        return "user"

    dummy_queue = MagicMock()
    dummy_queue.get_queue_size.return_value = 0

    real_loop = asyncio.get_running_loop()

    class DummyLoop:
        def __init__(self, loop):
            """
            Create an instance bound to the given asyncio event loop.

            Parameters:
                loop (asyncio.AbstractEventLoop): Event loop used to schedule and run the instance's asynchronous tasks.
            """
            self._loop = loop

        def is_running(self):
            """
            Indicates whether the component is running.

            Returns:
                `True` since this implementation always reports the component as running.
            """
            return True

        def create_task(self, coro):
            """
            Schedule an awaitable on this instance's event loop and return the created Task.

            Parameters:
                coro: An awaitable or coroutine to schedule on this object's event loop.

            Returns:
                asyncio.Task: The Task object wrapping the scheduled coroutine.
            """
            return self._loop.create_task(coro)

        async def run_in_executor(self, _executor, func, *args):
            """
            Invoke a callable synchronously and return its result.

            _executor is accepted for API compatibility but ignored.
            func is the callable to invoke; any positional args are forwarded to it.

            Returns:
                The value returned by `func(*args)`.
            """
            return func(*args)

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch(
            "mmrelay.matrix_utils.asyncio.get_running_loop",
            return_value=DummyLoop(real_loop),
        ),
        patch(
            "mmrelay.matrix_utils.get_user_display_name",
            side_effect=mock_get_user_display_name_func,
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
        queued_kwargs = mock_queue_message.call_args.kwargs
        assert "Hello, world!" in queued_kwargs["text"]


async def test_on_room_message_remote_prefers_meshtastic_text(
    mock_room,
    mock_event,
    test_config,
):
    """Ensure remote mesh messages fall back to raw meshtastic_text when body is empty."""
    mock_event.body = ""
    mock_event.source = {
        "content": {
            "body": "",
            "meshtastic_longname": "LoRa",
            "meshtastic_shortname": "Trak",
            "meshtastic_meshnet": "remote",
            "meshtastic_text": "Hello from remote mesh",
            "meshtastic_portnum": TEXT_MESSAGE_APP,
        }
    }

    # Remote mesh must differ from local meshnet_name to exercise relay path
    test_config["meshtastic"]["meshnet_name"] = "local_mesh"

    matrix_rooms = test_config["matrix_rooms"]
    dummy_queue = MagicMock()
    dummy_queue.get_queue_size.return_value = 0

    real_loop = asyncio.get_running_loop()

    class DummyLoop:
        def __init__(self, loop):
            """
            Create an instance bound to the given asyncio event loop.

            Parameters:
                loop (asyncio.AbstractEventLoop): Event loop used to schedule and run the instance's asynchronous tasks.
            """
            self._loop = loop

        def is_running(self):
            """
            Indicates whether the component is running.

            Returns:
                `True` since this implementation always reports the component as running.
            """
            return True

        def create_task(self, coro):
            """
            Schedule an awaitable on this instance's event loop and return the created Task.

            Parameters:
                coro: An awaitable or coroutine to schedule on this object's event loop.

            Returns:
                asyncio.Task: The Task object wrapping the scheduled coroutine.
            """
            return self._loop.create_task(coro)

        async def run_in_executor(self, _executor, func, *args):
            """
            Invoke a callable synchronously and return its result.

            _executor is accepted for API compatibility but ignored.
            func is the callable to invoke; any positional args are forwarded to it.

            Returns:
                The value returned by `func(*args)`.
            """
            return func(*args)

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
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
        patch("mmrelay.matrix_utils.matrix_rooms", matrix_rooms),
        patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]),
    ):
        await on_room_message(mock_room, mock_event)

        mock_queue_message.assert_called_once()
        queued_kwargs = mock_queue_message.call_args.kwargs
        assert "Hello from remote mesh" in queued_kwargs["text"]


async def test_on_room_message_dict_keyed_matrix_rooms_uses_direct_room_lookup(
    mock_room,
    mock_event,
):
    """Dict-keyed room mappings should resolve room config by room_id."""
    test_config = {
        "meshtastic": {
            "message_interactions": {"reactions": False, "replies": False},
            "meshnet_name": "test_mesh",
        },
        "matrix_rooms": {mock_room.room_id: {"meshtastic_channel": 0}},
        "matrix": {"bot_user_id": TEST_BOT_USER_ID},
    }
    mock_event.source = {
        "content": {
            "body": "Hello, world!",
            MATRIX_SUPPRESS_KEY: True,
        }
    }

    with (
        patch("mmrelay.matrix_utils.config", test_config),
        patch("mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]),
        patch("mmrelay.matrix_utils.bot_user_id", TEST_BOT_USER_ID),
        patch("mmrelay.matrix_utils.bot_start_time", 1000),
        patch("mmrelay.matrix_utils.bot_start_monotonic_secs", 10.0),
        patch("mmrelay.matrix_utils.time.time", return_value=2.0),
        patch("mmrelay.matrix_utils.time.monotonic", return_value=11.0),
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": False},
        ) as mock_get_interaction_settings,
        patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False),
    ):
        await on_room_message(mock_room, mock_event)

    mock_get_interaction_settings.assert_called_once_with(test_config)


async def test_on_room_message_ignore_bot(
    mock_room,
    mock_event,
    test_config,
):
    """
    Test that messages sent by the bot user are ignored and not relayed to Meshtastic.

    Ensures that when the event sender matches the configured bot user ID, the message is not queued for relay.
    """
    mock_event.sender = test_config["matrix"]["bot_user_id"]
    with (
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
        patch("mmrelay.matrix_utils.connect_meshtastic") as mock_connect_meshtastic,
        patch("mmrelay.matrix_utils.bot_start_time", 1234567880),
        patch("mmrelay.matrix_utils.config", test_config),
        patch("mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]),
        patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]),
    ):
        await on_room_message(mock_room, mock_event)

        mock_queue_message.assert_not_called()
        mock_connect_meshtastic.assert_not_called()


@patch("mmrelay.matrix_utils.bot_start_time", 1234567880)
@patch("mmrelay.matrix_utils.handle_matrix_reply", new_callable=AsyncMock)
async def test_on_room_message_reply_enabled(
    mock_handle_matrix_reply,
    mock_room,
    mock_event,
):
    """
    Test that reply messages are processed and queued when reply interactions are enabled.
    """
    test_config = {
        "meshtastic": {
            "message_interactions": {"replies": True},
            "meshnet_name": "test_mesh",
        },
        "matrix_rooms": [{"id": TEST_ROOM_ID, "meshtastic_channel": 0}],
        "matrix": {"bot_user_id": TEST_BOT_USER_ID},
    }
    mock_handle_matrix_reply.return_value = True
    mock_event.source = {
        "content": {
            "m.relates_to": {"m.in_reply_to": {"event_id": "original_event_id"}}
        }
    }

    with (
        patch("mmrelay.matrix_utils.config", test_config),
        patch("mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]),
        patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]),
    ):
        await on_room_message(mock_room, mock_event)
        mock_handle_matrix_reply.assert_called_once()


@patch("mmrelay.plugin_loader.load_plugins", return_value=[])
@patch("mmrelay.matrix_utils.connect_meshtastic")
@patch("mmrelay.matrix_utils.queue_message")
@patch("mmrelay.matrix_utils.bot_start_time", 1234567880)
@patch("mmrelay.matrix_utils.get_user_display_name")
async def test_on_room_message_reply_disabled(
    mock_get_user_display_name,
    mock_queue_message,
    _mock_connect_meshtastic,
    _mock_load_plugins,
    mock_room,
    mock_event,
    test_config,
):
    """
    Test that reply messages are relayed with full content when reply interactions are disabled.

    Ensures that when reply interactions are disabled in the configuration, the entire event body—including quoted original messages—is queued for Meshtastic relay without stripping quoted lines.
    """

    # Create a proper async mock function
    async def mock_get_user_display_name_func(*args, **kwargs):
        """
        Provides an async test helper that always returns the fixed display name "user".

        Accepts any positional and keyword arguments and ignores them.

        Returns:
            str: The display name "user".
        """
        return "user"

    mock_get_user_display_name.side_effect = mock_get_user_display_name_func
    test_config["meshtastic"]["message_interactions"]["replies"] = False
    mock_event.source = {
        "content": {
            "m.relates_to": {"m.in_reply_to": {"event_id": "original_event_id"}}
        }
    }
    mock_event.body = (
        "> <@original_user:matrix.org> original message\n\nThis is a reply"
    )

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

            # Assert that the message was queued
            mock_queue_message.assert_called_once()
            call_args = mock_queue_message.call_args[1]
            assert mock_event.body in call_args["text"]


async def test_on_room_message_reaction_enabled(mock_room, test_config):
    # This is a reaction event
    """
    Verify that a Matrix reaction event is converted into a Meshtastic relay message and queued when reaction interactions are enabled.

    Asserts that a reaction produces a queued relay entry with a description indicating a local reaction and text that denotes a reacted state.
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
        sender="@user:matrix.org",
        server_timestamp=1234567890,
    )

    test_config["meshtastic"]["message_interactions"]["reactions"] = True

    real_loop = asyncio.get_running_loop()

    class DummyLoop:
        def __init__(self, loop):
            """
            Create an instance bound to the given asyncio event loop.

            Parameters:
                loop (asyncio.AbstractEventLoop): Event loop used to schedule and run the instance's asynchronous tasks.
            """
            self._loop = loop

        def is_running(self):
            """
            Indicates whether the component is running.

            Returns:
                `True` since this implementation always reports the component as running.
            """
            return True

        def create_task(self, coro):
            """
            Schedule an awaitable on this instance's event loop and return the created Task.

            Parameters:
                coro: An awaitable or coroutine to schedule on this object's event loop.

            Returns:
                asyncio.Task: The Task object wrapping the scheduled coroutine.
            """
            return self._loop.create_task(coro)

        async def run_in_executor(self, _executor, func, *args):
            """
            Invoke a callable synchronously and return its result.

            _executor is accepted for API compatibility but ignored.
            func is the callable to invoke; any positional args are forwarded to it.

            Returns:
                The value returned by `func(*args)`.
            """
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
        assert queued_args[0].__name__ == "send_text_reply"


async def test_on_room_message_reaction_non_numeric_meshtastic_id(
    mock_room, test_config
):
    """Non-numeric mapped meshtastic_id should send as normal message, not reply."""
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
        def __init__(self, loop):
            self._loop = loop

        def is_running(self):
            return True

        def create_task(self, coro):
            return self._loop.create_task(coro)

        async def run_in_executor(self, _executor, func, *args):
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

        mock_queue_message.assert_called_once()
        queued_args = mock_queue_message.call_args.args
        queued_kwargs = mock_queue_message.call_args.kwargs
        assert "reacted" in queued_kwargs["text"]
        assert "reply_id" not in queued_kwargs
        assert queued_args[0] is mock_meshtastic.sendText
    assert any(
        "is not numeric" in call.args[0] for call in mock_logger.warning.call_args_list
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


@patch("mmrelay.matrix_utils.connect_meshtastic")
@patch("mmrelay.matrix_utils.queue_message")
@patch("mmrelay.matrix_utils.bot_start_time", 1234567880)
async def test_on_room_message_unsupported_room(
    mock_queue_message, _mock_connect_meshtastic, mock_room, mock_event, test_config
):
    """
    Test that messages from unsupported Matrix rooms are ignored.

    Verifies that when a message event originates from a Matrix room not listed in the configuration, it is not queued for Meshtastic relay.
    """
    mock_room.room_id = "!unsupported:matrix.org"
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


async def test_on_room_message_detection_sensor_enabled(
    mock_room, mock_event, test_config
):
    """
    Test that a detection sensor message is processed and queued with the correct port number when detection_sensor is enabled.

    This test specifically covers the code path where meshtastic.protobuf.portnums_pb2
    is imported locally to delay logger creation for component logging timing.
    """
    # Arrange - Set up event as detection sensor message
    mock_event.body = "Detection data"
    mock_event.source = {
        "content": {
            "body": "Detection data",
            "meshtastic_portnum": DETECTION_SENSOR_APP,
        }
    }

    # Enable detection sensor and broadcast in config
    test_config["meshtastic"]["detection_sensor"] = True
    test_config["meshtastic"]["broadcast_enabled"] = True

    real_loop = asyncio.get_running_loop()

    class DummyLoop:
        def __init__(self, loop):
            """
            Create an instance bound to the given asyncio event loop.

            Parameters:
                loop (asyncio.AbstractEventLoop): Event loop used to schedule and run the instance's asynchronous tasks.
            """
            self._loop = loop

        def is_running(self):
            """
            Indicates whether the component is running.

            Returns:
                `True` since this implementation always reports the component as running.
            """
            return True

        def create_task(self, coro):
            """
            Schedule an awaitable on this instance's event loop and return the created Task.

            Parameters:
                coro: An awaitable or coroutine to schedule on this object's event loop.

            Returns:
                asyncio.Task: The Task object wrapping the scheduled coroutine.
            """
            return self._loop.create_task(coro)

        async def run_in_executor(self, _executor, func, *args):
            """
            Invoke a callable synchronously and return its result.

            _executor is accepted for API compatibility but ignored.
            func is the callable to invoke; any positional args are forwarded to it.

            Returns:
                The value returned by `func(*args)`.
            """
            return func(*args)

    # Act - Process the detection sensor message
    with (
        patch(
            "mmrelay.matrix_utils.queue_message", return_value=True
        ) as mock_queue_message,
        patch("mmrelay.matrix_utils.connect_meshtastic", return_value=MagicMock()),
        patch("mmrelay.matrix_utils.bot_start_time", 1234567880),
        patch("mmrelay.matrix_utils.config", test_config),
        patch("mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]),
        patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]),
        patch(
            "mmrelay.matrix_utils.asyncio.get_running_loop",
            return_value=DummyLoop(real_loop),
        ),
    ):
        # Mock the room.user_name method to return our test display name
        mock_room.user_name.return_value = "TestUser"
        await on_room_message(mock_room, mock_event)

    # Assert - Verify the message was queued with correct detection sensor parameters
    mock_queue_message.assert_called_once()
    call_args = mock_queue_message.call_args

    # Verify the port number is set to DETECTION_SENSOR_APP (it will be a Mock object due to import)
    assert "portNum" in call_args.kwargs
    # The portNum should be the DETECTION_SENSOR_APP enum value from protobuf
    assert call_args.kwargs["description"] == "Detection sensor data from TestUser"
    # The data should be raw text without prefix for detection sensor packets
    assert call_args.kwargs["data"] == b"Detection data"


async def test_on_room_message_detection_sensor_disabled(
    mock_room, mock_event, test_config
):
    """
    Test that a detection sensor message is ignored when detection_sensor is disabled in config.
    """
    # Arrange - Set up event as detection sensor message but disable detection sensor
    mock_event.source = {
        "content": {
            "body": "Detection data",
            "meshtastic_portnum": DETECTION_SENSOR_APP,
        }
    }

    # Disable detection sensor in config
    test_config["meshtastic"]["detection_sensor"] = False
    test_config["meshtastic"]["broadcast_enabled"] = True

    # Act - Process the detection sensor message
    with (
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

    # Assert - Verify the message was not queued since detection sensor is disabled
    mock_queue_message.assert_not_called()


async def test_on_room_message_detection_sensor_broadcast_disabled(
    mock_room, mock_event, test_config
):
    """
    Detection sensor packets should not connect or queue when broadcast is disabled.
    """
    mock_event.source = {
        "content": {
            "body": "Detection data",
            "meshtastic_portnum": DETECTION_SENSOR_APP,
        }
    }
    test_config["meshtastic"]["detection_sensor"] = True
    test_config["meshtastic"]["broadcast_enabled"] = False

    with (
        patch(
            "mmrelay.matrix_utils.queue_message", return_value=True
        ) as mock_queue_message,
        patch(
            "mmrelay.matrix_utils.connect_meshtastic", return_value=MagicMock()
        ) as mock_connect,
        patch("mmrelay.matrix_utils.bot_start_time", 1234567880),
        patch("mmrelay.matrix_utils.config", test_config),
        patch("mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]),
        patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]),
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue_message.assert_not_called()
    mock_connect.assert_not_called()


async def test_on_room_message_detection_sensor_connect_failure(
    mock_room, mock_event, test_config
):
    """When detection sensor is enabled but connection fails, nothing should be queued."""
    mock_event.source = {
        "content": {
            "body": "Detection data",
            "meshtastic_portnum": DETECTION_SENSOR_APP,
        }
    }
    test_config["meshtastic"]["detection_sensor"] = True
    test_config["meshtastic"]["broadcast_enabled"] = True

    with (
        patch(
            "mmrelay.matrix_utils.queue_message", return_value=True
        ) as mock_queue_message,
        patch("mmrelay.matrix_utils.connect_meshtastic", return_value=None),
        patch("mmrelay.matrix_utils.bot_start_time", 1234567880),
        patch("mmrelay.matrix_utils.config", test_config),
        patch("mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]),
        patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]),
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue_message.assert_not_called()


async def test_on_room_message_does_not_drop_old_timestamp_messages(
    mock_room, mock_event, test_config
):
    """Older event timestamps should still be processed after startup clock corrections."""
    base_ts = 1_700_000_000_000
    message_ts = base_ts
    startup_ts = message_ts + MATRIX_STARTUP_TIMESTAMP_TOLERANCE_MS - 1
    mock_event.server_timestamp = message_ts

    with _patch_on_room_message_time_context(
        test_config=test_config,
        bot_start_time=startup_ts,
        bot_start_monotonic_secs=10_000.0,
        current_time=(startup_ts / 1000) + 0.01,
        current_monotonic=10_000.01,
    ) as mock_queue_message:
        await on_room_message(mock_room, mock_event)

    mock_queue_message.assert_called_once()


async def test_on_room_message_drops_clearly_stale_startup_backlog(
    mock_room, mock_event, test_config
):
    """Clearly stale pre-start events should be dropped when startup clock is stable."""
    base_ts = 1_700_000_000_000
    message_ts = base_ts
    bot_start_time = message_ts + MATRIX_STALE_STARTUP_EVENT_DROP_MS + 1000
    mock_event.server_timestamp = message_ts

    with _patch_on_room_message_time_context(
        test_config=test_config,
        bot_start_time=bot_start_time,
        bot_start_monotonic_secs=10_000.0,
        current_time=bot_start_time / 1000
        + MATRIX_STARTUP_STALE_FILTER_WINDOW_MS / 1000 / 4,
        current_monotonic=10_000.0 + MATRIX_STARTUP_STALE_FILTER_WINDOW_MS / 1000 / 4,
    ) as mock_queue_message:
        await on_room_message(mock_room, mock_event)

    mock_queue_message.assert_not_called()


async def test_on_room_message_allows_old_timestamp_after_clock_rollback(
    mock_room, mock_event, test_config
):
    """Clock rollback after startup should not drop legitimate Matrix events."""
    base_ts = 1_700_000_000_000
    message_ts = base_ts
    startup_ts = message_ts + MATRIX_CLOCK_ROLLBACK_DISABLE_MS + 1000
    mock_event.server_timestamp = message_ts

    with _patch_on_room_message_time_context(
        test_config=test_config,
        bot_start_time=startup_ts,
        bot_start_monotonic_secs=10_000.0,
        current_time=message_ts / 1000,
        current_monotonic=10_000.0 + MATRIX_STARTUP_STALE_FILTER_WINDOW_MS / 1000 / 2,
    ) as mock_queue_message:
        await on_room_message(mock_room, mock_event)

    mock_queue_message.assert_called_once()


async def test_on_room_message_allows_stale_timestamp_after_startup_window(
    mock_room, mock_event, test_config
):
    """Stale startup filtering should not drop old events after startup window elapses."""
    base_ts = 1_700_000_000_000
    message_ts = base_ts
    bot_start_time = message_ts + MATRIX_STALE_STARTUP_EVENT_DROP_MS + 1000
    mock_event.server_timestamp = message_ts

    with _patch_on_room_message_time_context(
        test_config=test_config,
        bot_start_time=bot_start_time,
        bot_start_monotonic_secs=10_000.0,
        current_time=bot_start_time / 1000
        + (MATRIX_STARTUP_STALE_FILTER_WINDOW_MS / 1000)
        + 100,
        current_monotonic=10_000.0
        + (MATRIX_STARTUP_STALE_FILTER_WINDOW_MS / 1000)
        + 100,
    ) as mock_queue_message:
        await on_room_message(mock_room, mock_event)

    mock_queue_message.assert_called_once()


async def test_on_room_message_config_none_logs_and_returns(
    monkeypatch, mock_room, mock_event
):
    """Missing config should log errors and return without relaying."""
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": mock_room.room_id, "meshtastic_channel": 0}],
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.bot_user_id", "@bot:matrix.org", raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.bot_start_time", 0, raising=False)
    monkeypatch.setattr("mmrelay.matrix_utils.config", None, raising=False)

    with (
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
    ):
        await on_room_message(mock_room, mock_event)

    mock_logger.error.assert_any_call(
        "No configuration available for Matrix message processing."
    )
    mock_logger.error.assert_any_call(
        "No configuration available. Cannot process Matrix message."
    )
    mock_queue_message.assert_not_called()


async def test_on_room_message_suppressed_message_returns(
    mock_room, mock_event, test_config
):
    """Suppressed messages should exit early without relaying."""
    mock_event.source = {
        "content": {"body": "Suppressed message", MATRIX_SUPPRESS_KEY: True}
    }

    with (
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
        patch("mmrelay.matrix_utils.bot_start_time", 0),
        patch("mmrelay.matrix_utils.config", test_config),
        patch("mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]),
        patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]),
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue_message.assert_not_called()


async def test_on_room_message_remote_reaction_relay_success(monkeypatch, mock_room):
    """Remote meshnet reactions should be relayed to the local mesh when enabled."""
    from mmrelay.matrix_utils import RoomMessageEmote

    class MockEmote(RoomMessageEmote):  # type: ignore[misc]
        def __init__(self):
            self.source = {
                "content": {
                    "body": 'reacted :) to "hello"',
                    "meshtastic_replyId": 123,
                    "meshtastic_longname": "RemoteUser",
                    "meshtastic_meshnet": "remote_mesh",
                    "meshtastic_text": "Original text from mesh",
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
    assert "reacted" in queued_kwargs["text"]
    assert queued_kwargs["description"] == "Remote reaction from remote_mesh"


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
        source={"content": {"m.relates_to": {"event_id": "missing", "key": "x"}}},
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
        "Original message for reaction not found" in call.args[0]
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
        source={"content": {"m.relates_to": {"event_id": "orig", "key": "x"}}},
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
            return_value=(12345, mock_room.room_id, "text", "meshnet"),
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


async def test_on_room_message_reply_handled_short_circuits(
    monkeypatch, mock_room, mock_event
):
    """Handled replies should not be relayed as normal messages."""
    mock_event.source = {
        "content": {"m.relates_to": {"m.in_reply_to": {"event_id": "orig"}}}
    }

    config = {
        "meshtastic": {
            "meshnet_name": "local_mesh",
            "message_interactions": {"reactions": False, "replies": True},
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
        patch("mmrelay.matrix_utils.handle_matrix_reply", AsyncMock(return_value=True)),
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue_message.assert_not_called()


async def test_on_room_message_remote_meshnet_empty_after_prefix_skips(
    monkeypatch, mock_room, mock_event
):
    """Remote meshnet messages should be skipped if only a prefix remains."""
    prefix = "[RemoteUser/remote]:"
    mock_event.body = prefix
    mock_event.source = {
        "content": {
            "body": prefix,
            "meshtastic_longname": "RemoteUser",
            "meshtastic_meshnet": "remote",
        }
    }

    config = {
        "meshtastic": {
            "meshnet_name": "local_mesh",
            "message_interactions": {"reactions": False, "replies": False},
        },
        "matrix_rooms": [{"id": mock_room.room_id, "meshtastic_channel": 0}],
        "matrix": {"bot_user_id": "@bot:matrix.org"},
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
        patch("mmrelay.matrix_utils.get_matrix_prefix", return_value=prefix),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue_message.assert_not_called()
    mock_logger.warning.assert_any_call(
        "Remote meshnet message from %s had empty text after formatting; skipping relay",
        "remote",
    )


async def test_on_room_message_portnum_string_digits(
    monkeypatch, mock_room, mock_event, test_config
):
    """Numeric string portnum values should be handled without errors."""
    mock_event.source = {"content": {"body": "Message", "meshtastic_portnum": "123"}}

    test_config["meshtastic"]["broadcast_enabled"] = True

    class DummyInterface:
        def __init__(self):
            self.sendText = MagicMock()

    class DummyQueue:
        def get_queue_size(self):
            return 1

    monkeypatch.setattr("mmrelay.matrix_utils.bot_start_time", 0, raising=False)
    monkeypatch.setattr("mmrelay.matrix_utils.config", test_config, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"], raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.bot_user_id",
        test_config["matrix"]["bot_user_id"],
        raising=False,
    )

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            AsyncMock(return_value=(DummyInterface(), 0)),
        ),
        patch("mmrelay.matrix_utils.get_message_queue", return_value=DummyQueue()),
        patch("mmrelay.matrix_utils.queue_message", return_value=True) as mock_queue,
        patch(
            "mmrelay.matrix_utils.get_user_display_name",
            AsyncMock(return_value="User"),
        ),
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue.assert_called_once()


async def test_on_room_message_plugin_handle_exception_logs_and_continues(
    monkeypatch, mock_room, mock_event, test_config
):
    """Plugin handler exceptions should be logged and not stop relaying."""

    class ExplodingPlugin:
        plugin_name = "boom"

        async def handle_room_message(self, _room, _event, _text):
            raise RuntimeError("boom")

    class DummyInterface:
        def __init__(self):
            self.sendText = MagicMock()

    class DummyQueue:
        def get_queue_size(self):
            return 1

    test_config["meshtastic"]["broadcast_enabled"] = True

    monkeypatch.setattr("mmrelay.matrix_utils.bot_start_time", 0, raising=False)
    monkeypatch.setattr("mmrelay.matrix_utils.config", test_config, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"], raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.bot_user_id",
        test_config["matrix"]["bot_user_id"],
        raising=False,
    )

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[ExplodingPlugin()]),
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            AsyncMock(return_value=(DummyInterface(), 0)),
        ),
        patch("mmrelay.matrix_utils.get_message_queue", return_value=DummyQueue()),
        patch("mmrelay.matrix_utils.queue_message", return_value=True) as mock_queue,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.get_user_display_name",
            AsyncMock(return_value="User"),
        ),
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue.assert_called_once()
    mock_logger.error.assert_any_call(
        "Error processing message with plugin %s: %s", "boom", "RuntimeError"
    )
    mock_logger.exception.assert_any_call(
        "Error processing message with plugin %s", "boom"
    )


async def test_on_room_message_plugin_match_exception_does_not_block(
    monkeypatch, mock_room, mock_event, test_config
):
    """Plugin match errors should be logged and ignored."""

    class MatchExplodingPlugin:
        plugin_name = "matcher"

        async def handle_room_message(self, _room, _event, _text):
            return False

        def matches(self, _event):
            raise RuntimeError("boom")

    class CommandExplodingPlugin:
        plugin_name = "commands"

        async def handle_room_message(self, _room, _event, _text):
            return False

        def get_matrix_commands(self):
            raise RuntimeError("boom")

    class DummyInterface:
        def __init__(self):
            self.sendText = MagicMock()

    class DummyQueue:
        def get_queue_size(self):
            return 1

    test_config["meshtastic"]["broadcast_enabled"] = True

    monkeypatch.setattr("mmrelay.matrix_utils.bot_start_time", 0, raising=False)
    monkeypatch.setattr("mmrelay.matrix_utils.config", test_config, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"], raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.bot_user_id",
        test_config["matrix"]["bot_user_id"],
        raising=False,
    )

    with (
        patch(
            "mmrelay.plugin_loader.load_plugins",
            return_value=[MatchExplodingPlugin(), CommandExplodingPlugin()],
        ),
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            AsyncMock(return_value=(DummyInterface(), 0)),
        ),
        patch("mmrelay.matrix_utils.get_message_queue", return_value=DummyQueue()),
        patch("mmrelay.matrix_utils.queue_message", return_value=True) as mock_queue,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.get_user_display_name",
            AsyncMock(return_value="User"),
        ),
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue.assert_called_once()
    assert any(
        "Error checking plugin match" in call.args[0]
        for call in mock_logger.exception.call_args_list
    )
    assert any(
        "Error checking plugin commands" in call.args[0]
        for call in mock_logger.exception.call_args_list
    )


async def test_on_room_message_no_meshtastic_interface_returns(
    monkeypatch, mock_room, mock_event, test_config
):
    """If Meshtastic connection fails, messages should not be queued."""
    monkeypatch.setattr("mmrelay.matrix_utils.bot_start_time", 0, raising=False)
    monkeypatch.setattr("mmrelay.matrix_utils.config", test_config, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"], raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.bot_user_id",
        test_config["matrix"]["bot_user_id"],
        raising=False,
    )

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            AsyncMock(return_value=(None, None)),
        ),
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
        patch(
            "mmrelay.matrix_utils.get_user_display_name",
            AsyncMock(return_value="User"),
        ),
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue_message.assert_not_called()


async def test_on_room_message_broadcast_disabled_no_queue(
    monkeypatch, mock_room, mock_event, test_config
):
    """broadcast_enabled=False should avoid queueing messages."""
    test_config["meshtastic"]["broadcast_enabled"] = False

    class DummyInterface:
        def __init__(self):
            self.sendText = MagicMock()

    monkeypatch.setattr("mmrelay.matrix_utils.bot_start_time", 0, raising=False)
    monkeypatch.setattr("mmrelay.matrix_utils.config", test_config, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"], raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.bot_user_id",
        test_config["matrix"]["bot_user_id"],
        raising=False,
    )

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            AsyncMock(return_value=(DummyInterface(), 0)),
        ),
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.get_user_display_name",
            AsyncMock(return_value="User"),
        ),
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue_message.assert_not_called()
    assert any(
        "broadcast_enabled is False" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


async def test_on_room_message_queue_failure_logs_error(
    monkeypatch, mock_room, mock_event, test_config
):
    """Queue failures should log and stop processing."""
    test_config["meshtastic"]["broadcast_enabled"] = True

    class DummyInterface:
        def __init__(self):
            self.sendText = MagicMock()

    monkeypatch.setattr("mmrelay.matrix_utils.bot_start_time", 0, raising=False)
    monkeypatch.setattr("mmrelay.matrix_utils.config", test_config, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"], raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.bot_user_id",
        test_config["matrix"]["bot_user_id"],
        raising=False,
    )

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            AsyncMock(return_value=(DummyInterface(), 0)),
        ),
        patch("mmrelay.matrix_utils.queue_message", return_value=False) as mock_queue,
        patch("mmrelay.meshtastic_utils.logger") as mock_meshtastic_logger,
        patch(
            "mmrelay.matrix_utils.get_user_display_name",
            AsyncMock(return_value="User"),
        ),
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue.assert_called_once()
    mock_meshtastic_logger.error.assert_any_call(
        "Failed to relay message to Meshtastic"
    )


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

    mapping = ("mesh_id", room_id, "text", "meshnet")
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
    queue_mock.assert_called()


async def test_on_room_message_command_short_circuits(
    monkeypatch, mock_room, mock_event, test_config
):
    """Commands should not be relayed to Meshtastic."""
    test_config["meshtastic"]["broadcast_enabled"] = True
    monkeypatch.setattr("mmrelay.matrix_utils.config", test_config, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"], raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.bot_user_id", "@bot:matrix.org", raising=False
    )
    mock_event.body = "!ping"

    class DummyPlugin:
        plugin_name = "dummy"

        async def handle_room_message(self, *_args, **_kwargs):
            """
            Handle an incoming Matrix room message and indicate whether it was processed.

            This implementation does not process messages and always reports the message as not handled.

            Returns:
                handled (bool): `False` indicating the message was not handled.
            """
            return False

        def get_matrix_commands(self):
            """
            Return the list of Matrix commands supported by this handler.

            Returns:
                list[str]: A list of command names; currently contains `"ping"`.
            """
            return ["ping"]

        def matches(self, event):
            """Use bot_command to detect this plugin's commands."""

            return any(
                bot_command(cmd, event, require_mention=False)
                for cmd in self.get_matrix_commands()
            )

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[DummyPlugin()]),
        patch("mmrelay.matrix_utils.bot_command", return_value=True),
        patch("mmrelay.matrix_utils.queue_message") as mock_queue,
        patch("mmrelay.matrix_utils.connect_meshtastic") as mock_connect,
        patch("mmrelay.matrix_utils.bot_start_time", 1234567880),
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue.assert_not_called()
    mock_connect.assert_not_called()


async def test_on_room_message_requires_mention_before_filtering_command(
    monkeypatch, mock_room, mock_event, test_config
):
    """Plugins that require mentions should not block relaying unmentioned commands."""
    test_config["meshtastic"]["broadcast_enabled"] = True
    monkeypatch.setattr("mmrelay.matrix_utils.config", test_config, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        test_config["matrix_rooms"],
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.bot_user_id", "@bot:matrix.org", raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.bot_start_time", 0, raising=False)
    mock_event.body = "!ping"
    mock_event.source["content"]["body"] = "!ping"

    class MentionedPlugin:
        plugin_name = "ping"

        async def handle_room_message(self, *_args, **_kwargs):
            """
            Handle an incoming room message event and indicate that it was not processed.

            This method accepts arbitrary positional and keyword arguments from the message dispatcher (for example, room and event) but intentionally does not process them; it always signals that the message was not handled.

            Returns:
                False (bool): Indicates the message was not handled.
            """
            return False

        def get_matrix_commands(self):
            """
            Return the list of Matrix command keywords supported by this handler.

            Returns:
                list[str]: Supported command strings, for example `["ping"]`.
            """
            return ["ping"]

        def get_require_bot_mention(self):
            """
            Indicates whether commands require an explicit bot mention.

            Returns:
                bool: `True` if the bot must be explicitly mentioned to accept commands, `False` otherwise.
            """
            return True

    mock_interface = MagicMock()

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[MentionedPlugin()]),
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            AsyncMock(return_value=(mock_interface, 0)),
        ),
        patch(
            "mmrelay.matrix_utils.get_user_display_name",
            AsyncMock(return_value="User"),
        ),
        patch("mmrelay.matrix_utils.queue_message") as mock_queue,
    ):
        mock_queue.return_value = True
        await on_room_message(mock_room, mock_event)

    mock_queue.assert_called_once()


async def test_on_room_message_creates_mapping_info():
    """on_room_message should build mapping info when storage is enabled."""
    room = MagicMock()
    room.room_id = "!room:matrix.org"

    event = MagicMock()
    event.sender = "@user:matrix.org"
    event.server_timestamp = 1234
    event.event_id = "$event123"
    event.body = "Hello"
    event.source = {"content": {"body": "Hello", "meshtastic_portnum": 1}}

    config = {
        "meshtastic": {"meshnet_name": "LocalMesh", "broadcast_enabled": True},
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    mock_queue = MagicMock()
    mock_queue.get_queue_size.return_value = 1

    with (
        patch("mmrelay.matrix_utils.config", config),
        patch(
            "mmrelay.matrix_utils.matrix_rooms",
            [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        ),
        patch("mmrelay.matrix_utils.bot_user_id", "@bot:matrix.org"),
        patch("mmrelay.matrix_utils.bot_start_time", 0),
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": True, "replies": False},
        ),
        patch(
            "mmrelay.matrix_utils.get_user_display_name",
            new_callable=AsyncMock,
            return_value="User",
        ),
        patch("mmrelay.matrix_utils.message_storage_enabled", return_value=True),
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            new_callable=AsyncMock,
            return_value=(MagicMock(), 0),
        ),
        patch("mmrelay.matrix_utils._get_msgs_to_keep_config", return_value=5),
        patch(
            "mmrelay.matrix_utils._create_mapping_info",
            return_value={"matrix_event_id": "$event123"},
        ) as mock_mapping,
        patch("mmrelay.matrix_utils.queue_message", return_value=True),
        patch("mmrelay.matrix_utils.get_message_queue", return_value=mock_queue),
    ):
        await on_room_message(room, event)

    mock_mapping.assert_called_once()
    args, _ = mock_mapping.call_args
    assert args[0] == "$event123"
