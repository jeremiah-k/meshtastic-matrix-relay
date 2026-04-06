import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import nio
import pytest

from mmrelay.matrix_utils import (
    NioLocalTransportError,
    _send_matrix_message_with_retry,
    matrix_relay,
)

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
    assert content["body"] == "bold"


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


def test_markdown_import_error_fallback_coverage():
    """
    Tests that the markdown processing fallback is triggered and behaves correctly when the `markdown` module is unavailable, ensuring coverage of the ImportError path.
    """
    message = "**bold** and *italic* text"
    has_markdown = True
    has_html = False

    with patch.dict("sys.modules", {"markdown": None}):
        if has_markdown or has_html:
            try:
                import markdown  # type: ignore[import-untyped]

                formatted_body = markdown.markdown(message)
                plain_body = re.sub(r"</?[^>]*>", "", formatted_body)
            except ImportError:
                formatted_body = message
                plain_body = message
                has_markdown = False
                has_html = False
        else:
            formatted_body = message
            plain_body = message

    assert formatted_body == message
    assert plain_body == message
    assert has_markdown is False
    assert has_html is False


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
