import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.matrix_utils import format_reply_message, handle_matrix_reply


class InlineExecutorLoop:
    def __init__(self, loop):
        self._loop = loop

    def run_in_executor(self, executor, func, *args):
        result = func(*args)
        fut = self._loop.create_future()
        fut.set_result(result)
        return fut


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
