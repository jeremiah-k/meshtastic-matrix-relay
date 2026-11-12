"""Matrix ingress plugin and command dispatch ordering and failure isolation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.matrix_utils import bot_command, on_room_message

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("reset_matrix_utils_globals"),
]


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
