import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.matrix.relay import RoomSendError
from mmrelay.matrix_utils import _send_matrix_message_with_retry, matrix_relay


def _make_room_send_error(message="API error"):
    obj = MagicMock()
    obj.__class__ = RoomSendError
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
