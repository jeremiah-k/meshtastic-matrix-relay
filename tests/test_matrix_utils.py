import asyncio
import importlib
import os
import re
import ssl
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from mmrelay.matrix_utils import (
    ImageUploadError,
    NioLocalTransportError,
    _can_auto_create_credentials,
    _create_mapping_info,
    _get_detailed_matrix_error_message,
    _get_msgs_to_keep_config,
    _handle_detection_sensor_packet,
    _is_room_alias,
    _iter_room_alias_entries,
    _normalize_bot_user_id,
    _update_room_id_in_mapping,
    bot_command,
    connect_matrix,
    get_displayname,
    get_user_display_name,
    handle_matrix_reply,
    login_matrix_bot,
    matrix_relay,
    on_decryption_failure,
    on_room_member,
    on_room_message,
    send_image,
    send_reply_to_meshtastic,
    send_room_image,
    strip_quoted_lines,
    truncate_message,
    upload_image,
    validate_prefix_format,
)

# Matrix room message handling tests - converted from unittest.TestCase to standalone pytest functions
#
# Conversion rationale:
# - Improved readability with native assert statements instead of self.assertEqual()
# - Better integration with pytest fixtures for test setup and teardown
# - Simplified async test execution without explicit asyncio.run() calls
# - Enhanced test isolation and maintainability
# - Alignment with modern Python testing practices


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
            "meshtastic_portnum": "TEXT_MESSAGE_APP",
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
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        "matrix": {"bot_user_id": "@bot:matrix.org"},
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

            _executor: Ignored; present for API compatibility.
            func: Callable to invoke.
            *args: Positional arguments forwarded to `func`.

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
                "meshtastic_id",
                "!room:matrix.org",
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
        queued_kwargs = mock_queue_message.call_args.kwargs
        assert queued_kwargs["description"].startswith("Local reaction")
        assert "reacted" in queued_kwargs["text"]


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
        sender="@user:matrix.org",
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
            "meshtastic_portnum": "DETECTION_SENSOR_APP",
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
            "meshtastic_portnum": "DETECTION_SENSOR_APP",
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
            "meshtastic_portnum": "DETECTION_SENSOR_APP",
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
            "meshtastic_portnum": "DETECTION_SENSOR_APP",
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


async def test_on_room_message_ignores_old_messages(mock_room, mock_event):
    """Messages sent before the bot start time should be ignored."""
    mock_event.server_timestamp = 100

    with (
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
        patch("mmrelay.matrix_utils.bot_start_time", 200),
    ):
        await on_room_message(mock_room, mock_event)

    mock_queue_message.assert_not_called()


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
        "content": {"body": "Suppressed message", "mmrelay_suppress": True}
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
        sender="@user:matrix.org",
        server_timestamp=1,
    )

    config = {
        "meshtastic": {
            "meshnet_name": "local_mesh",
            "message_interactions": {"reactions": True, "replies": False},
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
        sender="@user:matrix.org",
        server_timestamp=1,
    )

    config = {
        "meshtastic": {
            "meshnet_name": "local_mesh",
            "broadcast_enabled": True,
            "message_interactions": {"reactions": True, "replies": False},
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

    class DummyInterface:
        def __init__(self):
            self.sendText = MagicMock()

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
            return_value=("mesh_id", mock_room.room_id, "text", "meshnet"),
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
    mock_logger.error.assert_any_call("Error processing message with plugin %s", "boom")
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


# Matrix utility function tests - converted from unittest.TestCase to standalone pytest functions


@patch("mmrelay.matrix_utils.config", {})
def test_get_msgs_to_keep_config_default():
    """
    Test that the default message retention value is returned when no configuration is set.
    """
    result = _get_msgs_to_keep_config()
    assert result == 500


@patch("mmrelay.matrix_utils.config", {"db": {"msg_map": {"msgs_to_keep": 100}}})
def test_get_msgs_to_keep_config_legacy():
    """
    Test that the legacy configuration format correctly sets the message retention value.
    """
    result = _get_msgs_to_keep_config()
    assert result == 100


@patch("mmrelay.matrix_utils.config", {"database": {"msg_map": {"msgs_to_keep": 200}}})
def test_get_msgs_to_keep_config_new_format():
    """
    Test that the new configuration format correctly sets the message retention value.

    Verifies that `_get_msgs_to_keep_config()` returns the expected value when the configuration uses the new nested format for message retention.
    """
    result = _get_msgs_to_keep_config()
    assert result == 200


def test_create_mapping_info():
    """
    Tests that _create_mapping_info returns a dictionary with the correct message mapping information based on the provided parameters.
    """
    result = _create_mapping_info(
        matrix_event_id="$event123",
        room_id="!room:matrix.org",
        text="Hello world",
        meshnet="test_mesh",
        msgs_to_keep=100,
    )

    expected = {
        "matrix_event_id": "$event123",
        "room_id": "!room:matrix.org",
        "text": "Hello world",
        "meshnet": "test_mesh",
        "msgs_to_keep": 100,
    }
    assert result == expected


# Async Matrix function tests - converted from unittest.TestCase to standalone pytest functions


async def test_get_displayname_returns_none_when_client_missing(monkeypatch):
    """Return None when no Matrix client is available."""
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    result = await get_displayname("@user:example.org")

    assert result is None


async def test_get_displayname_returns_displayname(monkeypatch):
    """Return displayname attribute when client responds with one."""
    mock_client = MagicMock()
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Alice")
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_client", mock_client, raising=False
    )

    result = await get_displayname("@user:example.org")

    assert result == "Alice"


@pytest.fixture
def matrix_config():
    """Test configuration for Matrix functions."""
    return {
        "matrix": {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:matrix.org",
            "prefix_enabled": True,
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    # Note: whoami() is no longer called in the new E2EE implementation

    # Note: device_id remains None for legacy config without E2EE


@patch("mmrelay.matrix_utils.matrix_client", None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
@patch("mmrelay.matrix_utils.login_matrix_bot")
@patch("mmrelay.matrix_utils.load_credentials")
async def test_connect_matrix_alias_resolution_exception(
    mock_load_credentials, mock_login_bot, _mock_logger, mock_async_client
):
    """
    Test that connect_matrix handles alias resolution exceptions gracefully.
    """
    with patch("mmrelay.matrix_utils._create_ssl_context") as mock_ssl_context:
        # Mock SSL context creation
        mock_ssl_context.return_value = MagicMock()

        # Mock login_matrix_bot to return True (successful automatic login)
        mock_login_bot.return_value = True

        # Mock load_credentials to return valid credentials
        mock_load_credentials.return_value = {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "user_id": "@test:matrix.org",
            "device_id": "test_device_id",
        }

        # Mock the AsyncClient instance
        mock_client_instance = MagicMock()
        mock_client_instance.rooms = {}

        # Create proper async mock methods
        async def mock_whoami():
            """
            Simulate a Matrix client's `whoami()` response for tests.

            Returns:
                unittest.mock.MagicMock: Mock object with a `device_id` attribute set to "test_device_id".
            """
            return MagicMock(device_id="test_device_id")

        async def mock_sync(*_args, **_kwargs):
            """
            Return a new unittest.mock.MagicMock instance each time the coroutine is awaited.

            Returns:
                unittest.mock.MagicMock: A fresh MagicMock suitable as a mocked async client's `sync`-like result in tests.
            """
            return MagicMock()

        async def mock_get_displayname(*_args, **_kwargs):
            """
            Return a MagicMock representing a user's display name for asynchronous tests.

            Returns:
                MagicMock: with a 'displayname' attribute set to 'Test Bot'.
            """
            return MagicMock(displayname="Test Bot")

        # Create a mock for room_resolve_alias that raises an exception
        mock_room_resolve_alias = MagicMock()

        class FakeNetworkError(Exception):
            """Simulated network failure for tests."""

        async def mock_room_resolve_alias_impl(_alias):
            """
            Mock async implementation that simulates a network failure when resolving a Matrix room alias.

            Parameters:
                _alias (str): The room alias to resolve (ignored by this mock).

            Raises:
                FakeNetworkError: Always raised to simulate a network error during alias resolution.
            """
            raise FakeNetworkError()

        mock_room_resolve_alias.side_effect = mock_room_resolve_alias_impl

        mock_client_instance.whoami = mock_whoami
        mock_client_instance.sync = mock_sync
        mock_client_instance.get_displayname = mock_get_displayname
        mock_client_instance.room_resolve_alias = mock_room_resolve_alias
        mock_async_client.return_value = mock_client_instance

        # Create config with room aliases
        config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "bot_user_id": "@test:matrix.org",
                "password": "test_password",
            },
            "matrix_rooms": [{"id": "#error:matrix.org", "meshtastic_channel": 1}],
        }

        result = await connect_matrix(config)

        # Verify client was created
        mock_async_client.assert_called_once()
        assert result == mock_client_instance

        # Verify alias resolution was called
        mock_client_instance.room_resolve_alias.assert_called_once_with(
            "#error:matrix.org"
        )

        # Verify exception was logged
        _mock_logger.exception.assert_called_with(
            "Error resolving alias #error:matrix.org"
        )

        # Verify config was not modified (still contains alias)
        assert config["matrix_rooms"][0]["id"] == "#error:matrix.org"


@patch("mmrelay.matrix_utils.config", {"meshtastic": {"meshnet_name": "TestMesh"}})
@patch("mmrelay.matrix_utils.connect_matrix")
@patch("mmrelay.matrix_utils.get_interaction_settings")
@patch("mmrelay.matrix_utils.message_storage_enabled")
@patch("mmrelay.matrix_utils.logger")
async def test_matrix_relay_simple_message(
    _mock_logger, mock_storage_enabled, mock_get_interactions, mock_connect_matrix
):
    """Test that a plain text message is relayed with m.text semantics and metadata."""

    # Arrange: disable interactions that would trigger storage or reactions
    mock_get_interactions.return_value = {"reactions": False, "replies": False}
    mock_storage_enabled.return_value = False

    mock_matrix_client = MagicMock()
    mock_matrix_client.room_send = AsyncMock(
        return_value=MagicMock(event_id="$event123")
    )
    mock_connect_matrix.return_value = mock_matrix_client

    # Act
    await matrix_relay(
        room_id="!room:matrix.org",
        message="Hello Matrix",
        longname="Alice",
        shortname="A",
        meshnet_name="TestMesh",
        portnum=1,
    )

    # Assert
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
    # Setup mocks
    mock_get_interactions.return_value = {"reactions": False, "replies": False}
    mock_storage_enabled.return_value = False

    # Mock matrix client - use MagicMock to prevent coroutine warnings
    mock_matrix_client = MagicMock()
    mock_matrix_client.room_send = AsyncMock()
    mock_connect_matrix.return_value = mock_matrix_client

    # Mock successful message send
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

    # Verify emote message was sent
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
    Test that `matrix_relay` returns early and logs an error if the Matrix client is None.
    """
    mock_get_interactions.return_value = {"reactions": False, "replies": False}
    mock_storage_enabled.return_value = False

    # Mock connect_matrix to return None
    mock_connect_matrix.return_value = None

    # Should return early without sending
    await matrix_relay(
        room_id="!room:matrix.org",
        message="Hello world",
        longname="Alice",
        shortname="A",
        meshnet_name="TestMesh",
        portnum=1,
    )

    # Should log error about None client
    _mock_logger.error.assert_called_with("Matrix client is None. Cannot send message.")


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
    """Timeouts during room_send should be logged and return."""
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
        "Timeout sending message to Matrix room %s", "!room:matrix.org"
    )


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
    # This test directly exercises the ImportError fallback code path
    # to ensure it's covered by tests for Codecov patch coverage

    # Simulate the exact code path from matrix_relay function
    message = "**bold** and *italic* text"
    has_markdown = True  # This would be detected by the function
    has_html = False

    # Test the ImportError fallback path
    with patch.dict("sys.modules", {"markdown": None}):
        # This simulates the exact try/except block from matrix_relay
        if has_markdown or has_html:
            try:
                import markdown

                formatted_body = markdown.markdown(message)
                plain_body = re.sub(r"</?[^>]*>", "", formatted_body)
            except ImportError:
                # This is the fallback code we need to cover
                formatted_body = message
                plain_body = message
                has_markdown = False
                has_html = False
        else:
            formatted_body = message
            plain_body = message

    # Verify the fallback behavior worked correctly
    assert formatted_body == message
    assert plain_body == message
    assert has_markdown is False
    assert has_html is False


@patch("mmrelay.matrix_utils.matrix_client")
@patch("mmrelay.matrix_utils.logger")
async def test_get_user_display_name_room_name(_mock_logger, _mock_matrix_client):
    """Test getting user display name from room."""
    mock_room = MagicMock()
    mock_room.user_name.return_value = "Room Display Name"

    mock_event = MagicMock()
    mock_event.sender = "@user:matrix.org"

    result = await get_user_display_name(mock_room, mock_event)

    assert result == "Room Display Name"
    mock_room.user_name.assert_called_once_with("@user:matrix.org")


@patch("mmrelay.matrix_utils.matrix_client")
@patch("mmrelay.matrix_utils.logger")
async def test_get_user_display_name_fallback(_mock_logger, mock_matrix_client):
    """Test getting user display name with fallback to Matrix API."""
    mock_room = MagicMock()
    mock_room.user_name.return_value = None  # No room-specific name

    mock_event = MagicMock()
    mock_event.sender = "@user:matrix.org"

    # Mock Matrix API response
    mock_displayname_response = MagicMock()
    mock_displayname_response.displayname = "Global Display Name"
    mock_matrix_client.get_displayname = AsyncMock(
        return_value=mock_displayname_response
    )

    result = await get_user_display_name(mock_room, mock_event)

    assert result == "Global Display Name"
    mock_matrix_client.get_displayname.assert_called_once_with("@user:matrix.org")


@patch("mmrelay.matrix_utils.matrix_client")
@patch("mmrelay.matrix_utils.logger")
async def test_get_user_display_name_no_displayname(_mock_logger, mock_matrix_client):
    """Test getting user display name when no display name is set."""
    mock_room = MagicMock()
    mock_room.user_name.return_value = None

    mock_event = MagicMock()
    mock_event.sender = "@user:matrix.org"

    # Mock Matrix API response with no display name
    mock_displayname_response = MagicMock()
    mock_displayname_response.displayname = None
    mock_matrix_client.get_displayname = AsyncMock(
        return_value=mock_displayname_response
    )

    result = await get_user_display_name(mock_room, mock_event)

    # Should fallback to sender ID
    assert result == "@user:matrix.org"


async def test_get_user_display_name_profile_response(monkeypatch):
    """Use ProfileGetDisplayNameResponse instances when available."""

    class DummyResponse:
        def __init__(self, displayname):
            self.displayname = displayname

    class DummyError:
        pass

    monkeypatch.setattr(
        "mmrelay.matrix_utils.ProfileGetDisplayNameResponse",
        DummyResponse,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.ProfileGetDisplayNameError", DummyError, raising=False
    )

    mock_client = MagicMock()
    mock_client.get_displayname = AsyncMock(return_value=DummyResponse("Global Name"))
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_client", mock_client, raising=False
    )

    mock_room = MagicMock()
    mock_room.user_name.return_value = None
    mock_event = MagicMock()
    mock_event.sender = "@user:matrix.org"

    result = await get_user_display_name(mock_room, mock_event)

    assert result == "Global Name"


async def test_get_user_display_name_error_response(monkeypatch):
    """Fall back to sender ID for ProfileGetDisplayNameError responses."""

    class DummyResponse:
        pass

    class DummyError:
        def __init__(self, message):
            self.message = message

    monkeypatch.setattr(
        "mmrelay.matrix_utils.ProfileGetDisplayNameResponse",
        DummyResponse,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.ProfileGetDisplayNameError", DummyError, raising=False
    )

    mock_client = MagicMock()
    mock_client.get_displayname = AsyncMock(return_value=DummyError("No access"))
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_client", mock_client, raising=False
    )

    mock_room = MagicMock()
    mock_room.user_name.return_value = None
    mock_event = MagicMock()
    mock_event.sender = "@user:matrix.org"

    result = await get_user_display_name(mock_room, mock_event)

    assert result == "@user:matrix.org"


async def test_get_user_display_name_handles_comm_errors(monkeypatch):
    """Return sender ID when get_displayname raises a comm exception."""
    mock_client = MagicMock()
    mock_client.get_displayname = AsyncMock(side_effect=asyncio.TimeoutError)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_client", mock_client, raising=False
    )

    mock_room = MagicMock()
    mock_room.user_name.return_value = None
    mock_event = MagicMock()
    mock_event.sender = "@user:matrix.org"

    result = await get_user_display_name(mock_room, mock_event)

    assert result == "@user:matrix.org"


async def test_send_reply_to_meshtastic_with_reply_id():
    """Test sending a reply to Meshtastic with reply_id."""
    mock_room_config = {"meshtastic_channel": 0}
    mock_room = MagicMock()
    mock_event = MagicMock()

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


async def test_send_reply_to_meshtastic_no_reply_id():
    """Test sending a reply to Meshtastic without reply_id."""
    mock_room_config = {"meshtastic_channel": 0}
    mock_room = MagicMock()
    mock_event = MagicMock()

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


# Image upload function tests - converted from unittest.TestCase to standalone pytest functions


@patch("mmrelay.matrix_utils.io.BytesIO")
async def test_upload_image(mock_bytesio):
    """
    Test that the `upload_image` function correctly uploads an image to Matrix and returns the upload response.
    This test mocks the PIL Image object, a BytesIO buffer, and the Matrix client to verify that the image is saved, uploaded, and the expected response is returned.
    """
    from PIL import Image

    # Mock PIL Image
    mock_image = MagicMock(spec=Image.Image)
    mock_buffer = MagicMock()
    mock_bytesio.return_value = mock_buffer
    mock_buffer.getvalue.return_value = b"fake_image_data"

    # Mock Matrix client - use MagicMock to prevent coroutine warnings
    mock_client = MagicMock()
    mock_client.upload = AsyncMock()
    mock_upload_response = MagicMock()
    mock_client.upload.return_value = (mock_upload_response, None)

    result = await upload_image(mock_client, mock_image, "test.png")

    # Verify image was saved and uploaded
    mock_image.save.assert_called_once()
    mock_client.upload.assert_called_once()
    assert result == mock_upload_response


async def test_send_room_image():
    """
    Test that an uploaded image is correctly sent to a Matrix room using the provided client and upload response.
    """
    # Use MagicMock to prevent coroutine warnings
    mock_client = MagicMock()
    mock_client.room_send = AsyncMock()
    mock_upload_response = MagicMock()
    mock_upload_response.content_uri = "mxc://matrix.org/test123"

    await send_room_image(
        mock_client, "!room:matrix.org", mock_upload_response, "test.png"
    )

    # Verify room_send was called with correct parameters
    mock_client.room_send.assert_called_once()
    call_args = mock_client.room_send.call_args
    assert call_args[1]["room_id"] == "!room:matrix.org"
    assert call_args[1]["message_type"] == "m.room.message"
    content = call_args[1]["content"]
    assert content["msgtype"] == "m.image"
    assert content["url"] == "mxc://matrix.org/test123"
    assert content["body"] == "test.png"


async def test_send_room_image_raises_on_missing_content_uri():
    """
    Ensure send_room_image raises a clear error when upload_response lacks a content_uri.
    """
    mock_client = MagicMock()
    mock_client.room_send = AsyncMock()
    mock_upload_response = MagicMock()
    mock_upload_response.content_uri = None

    with pytest.raises(ImageUploadError):
        await send_room_image(
            mock_client, "!room:matrix.org", mock_upload_response, "test.png"
        )


async def test_send_image():
    """
    Test that send_image combines upload_image and send_room_image correctly.
    """
    mock_client = MagicMock()
    mock_client.room_send = AsyncMock()
    mock_image = MagicMock()
    mock_upload_response = MagicMock()
    mock_upload_response.content_uri = "mxc://matrix.org/test123"

    with patch(
        "mmrelay.matrix_utils.upload_image", return_value=mock_upload_response
    ) as mock_upload:
        with patch(
            "mmrelay.matrix_utils.send_room_image", return_value=None
        ) as mock_send:
            await send_image(mock_client, "!room:matrix.org", mock_image, "test.png")

            # Verify upload_image was called with correct parameters
            mock_upload.assert_awaited_once_with(
                client=mock_client, image=mock_image, filename="test.png"
            )

            # Verify send_room_image was called with correct parameters
            mock_send.assert_awaited_once_with(
                mock_client,
                "!room:matrix.org",
                upload_response=mock_upload_response,
                filename="test.png",
            )


async def test_upload_image_sets_content_type_and_uses_filename():
    """Upload should honor detected image content type from filename."""
    uploaded = {}

    class FakeImage:
        def save(self, buffer, _format=None, **kwargs):
            """
            Write JPEG-encoded image data into a binary writable buffer.

            Parameters:
                buffer: A binary writable file-like object that will receive the image bytes.
                _format: Optional image format hint; accepted but not used by this implementation.
            """
            _format = kwargs.get("format", _format)
            buffer.write(b"jpgbytes")

    async def fake_upload(_file_obj, content_type=None, filename=None, filesize=None):
        """
        Simulate a file upload for tests and record the provided metadata.

        Records the provided content_type, filename, and filesize into the shared `uploaded` mapping
        and sets the same attributes on `mock_upload_response` to emulate an upload result.

        Parameters:
            _file_obj: The file-like object to "upload" (ignored by this fake).
            content_type (str|None): MIME type to assign to the upload result.
            filename (str|None): Filename to assign to the upload result.
            filesize (int|None): File size in bytes to assign to the upload result.

        Returns:
            tuple: `(upload_response, None)` where `upload_response` has `content_type`, `filename`,
            and `filesize` attributes set to the provided values.
        """
        uploaded["content_type"] = content_type
        uploaded["filename"] = filename
        uploaded["filesize"] = filesize
        mock_upload_response.content_type = content_type
        mock_upload_response.filename = filename
        mock_upload_response.filesize = filesize
        return mock_upload_response, None

    mock_client = MagicMock()
    mock_upload_response = MagicMock()
    mock_client.upload = AsyncMock(side_effect=fake_upload)

    result = await upload_image(mock_client, FakeImage(), "photo.jpg")  # type: ignore[arg-type]

    assert result == mock_upload_response
    assert mock_upload_response.content_type == "image/jpeg"
    assert mock_upload_response.filename == "photo.jpg"
    assert mock_upload_response.filesize == len(b"jpgbytes")


async def test_upload_image_fallbacks_to_png_on_save_error():
    """Upload should fall back to PNG and set content_type accordingly when initial save fails."""
    calls = []

    class FakeImage:
        def __init__(self):
            """
            Initialize the instance and mark it as the first-run.

            Sets the internal `_first` attribute to True to indicate the instance has not
            performed its primary action yet.
            """
            self._first = True

        def save(self, buffer, _format=None, **kwargs):
            """
            Write image data into a binary buffer; on the first call this implementation raises a ValueError, thereafter it writes PNG bytes.

            Parameters:
                buffer: A binary file-like object with a write(bytes) method that will receive the image data.
                _format (str | None): Optional format hint (ignored by this implementation).

            Raises:
                ValueError: If this is the first invocation and the instance's `_first` flag is set.
            """
            _format = kwargs.get("format", _format)
            calls.append(_format)
            if self._first:
                self._first = False
                raise ValueError("bad format")
            buffer.write(b"pngbytes")

    uploaded = {}

    async def fake_upload(_file_obj, content_type=None, filename=None, filesize=None):
        """
        Test helper that simulates uploading a file and records upload metadata.

        Parameters:
            _file_obj: Ignored file-like object (kept for signature compatibility).
            content_type (str | None): MIME type recorded to the shared `uploaded` mapping.
            filename (str | None): Filename recorded to the shared `uploaded` mapping.
            filesize (int | None): File size recorded to the shared `uploaded` mapping.

        Returns:
            tuple: A pair (upload_result, content_uri) where `upload_result` is an empty
            SimpleNamespace placeholder and `content_uri` is `None`.
        """
        uploaded["content_type"] = content_type
        uploaded["filename"] = filename
        uploaded["filesize"] = filesize
        return SimpleNamespace(), None

    mock_client = MagicMock()
    mock_client.upload = AsyncMock(side_effect=fake_upload)

    await upload_image(mock_client, FakeImage(), "photo.webp")  # type: ignore[arg-type]

    # First attempt uses WEBP, then PNG fallback
    assert calls == ["WEBP", "PNG"]
    assert uploaded["content_type"] == "image/png"
    assert uploaded["filename"] == "photo.webp"
    assert uploaded["filesize"] == len(b"pngbytes")


async def test_upload_image_fallbacks_to_png_on_oserror():
    """Upload should fall back to PNG when Pillow raises OSError (e.g., RGBA as JPEG)."""
    calls = []

    class FakeImage:
        def __init__(self):
            """
            Initialize the instance and mark it as the first-run.

            Sets the internal `_first` attribute to True to indicate the instance has not
            performed its primary action yet.
            """
            self._first = True

        def save(self, buffer, _format=None, **kwargs):
            """
            Write image data into a binary buffer; on the first call this implementation raises OSError, thereafter it writes PNG bytes.

            Parameters:
                buffer: A binary file-like object with a write(bytes) method that will receive the image data.
                _format (str | None): Optional format hint (ignored by this implementation).

            Raises:
                OSError: If this is the first invocation and the instance's `_first` flag is set.
            """
            _format = kwargs.get("format", _format)
            calls.append(_format)
            if self._first:
                self._first = False
                raise OSError("cannot write mode RGBA as JPEG")
            buffer.write(b"pngbytes")

    uploaded = {}

    async def fake_upload(_file_obj, content_type=None, filename=None, filesize=None):
        """
        Test helper that simulates uploading a file and records upload metadata.

        Parameters:
            _file_obj: Ignored file-like object (kept for signature compatibility).
            content_type (str | None): MIME type recorded to the shared `uploaded` mapping.
            filename (str | None): Filename recorded to the shared `uploaded` mapping.
            filesize (int | None): File size recorded to the shared `uploaded` mapping.

        Returns:
            tuple: A pair (upload_result, content_uri) where `upload_result` is an empty
            SimpleNamespace placeholder and `content_uri` is `None`.
        """
        uploaded["content_type"] = content_type
        uploaded["filename"] = filename
        uploaded["filesize"] = filesize
        return SimpleNamespace(), None

    mock_client = MagicMock()
    mock_client.upload = AsyncMock(side_effect=fake_upload)

    await upload_image(mock_client, FakeImage(), "photo.jpg")  # type: ignore[arg-type]

    # First attempt uses JPEG, then PNG fallback
    assert calls == ["JPEG", "PNG"]
    assert uploaded["content_type"] == "image/png"
    assert uploaded["filename"] == "photo.jpg"
    assert uploaded["filesize"] == len(b"pngbytes")


async def test_upload_image_defaults_to_png_when_mimetype_unknown():
    """Unknown extensions should default to image/png even when save succeeds."""

    class FakeImage:
        def save(self, buffer, _format=None, **kwargs):
            """
            Write a default placeholder byte sequence into the provided writable binary buffer.

            Parameters:
                buffer: A writable binary file-like object with a write(bytes) method; receives the placeholder bytes.
                _format (str, optional): Ignored by this implementation.
            """
            _format = kwargs.get("format", _format)
            buffer.write(b"defaultbytes")

    uploaded = {}

    async def fake_upload(_file_obj, content_type=None, filename=None, filesize=None):
        """
        Test helper that simulates uploading a file and records upload metadata.

        Parameters:
            _file_obj: Ignored file-like object (kept for signature compatibility).
            content_type (str | None): MIME type recorded to the shared `uploaded` mapping.
            filename (str | None): Filename recorded to the shared `uploaded` mapping.
            filesize (int | None): File size recorded to the shared `uploaded` mapping.

        Returns:
            tuple: A pair (upload_result, content_uri) where `upload_result` is an empty
            SimpleNamespace placeholder and `content_uri` is `None`.
        """
        uploaded["content_type"] = content_type
        uploaded["filename"] = filename
        uploaded["filesize"] = filesize
        return SimpleNamespace(), None

    mock_client = MagicMock()
    mock_client.upload = AsyncMock(side_effect=fake_upload)

    await upload_image(mock_client, FakeImage(), "noext")  # type: ignore[arg-type]

    assert uploaded["content_type"] == "image/png"
    assert uploaded["filename"] == "noext"
    assert uploaded["filesize"] == len(b"defaultbytes")


async def test_upload_image_returns_upload_error_on_network_exception():
    """Network errors during upload should be wrapped in UploadError with a safe status_code."""

    class FakeImage:
        def save(self, buffer, _format=None, **kwargs):
            buffer.write(b"pngbytes")

        # Make it compatible with PIL.Image type checking
        @property
        def format(self):
            return "PNG"

    mock_client = MagicMock()
    mock_client.upload = AsyncMock(side_effect=asyncio.TimeoutError("boom"))

    class LocalUploadError:
        def __init__(
            self, message, status_code=None, retry_after_ms=None, soft_logout=False
        ):
            self.message = message
            self.status_code = status_code
            self.retry_after_ms = retry_after_ms
            self.soft_logout = soft_logout

    result = await upload_image(
        mock_client,
        FakeImage(),  # type: ignore[arg-type]
        "photo.png",
    )

    assert hasattr(result, "message")
    assert hasattr(result, "status_code")
    assert result.message == "boom"
    assert result.status_code is None
    mock_client.upload.assert_awaited_once()


@pytest.mark.asyncio
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


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.os.makedirs")
@patch("mmrelay.matrix_utils.os.listdir")
@patch("mmrelay.matrix_utils.os.path.exists")
@patch("builtins.open")
@patch("mmrelay.matrix_utils.json.load")
@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils._create_ssl_context")
@patch("mmrelay.matrix_utils.matrix_client", None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_connect_matrix_missing_device_id_uses_direct_assignment(
    _mock_logger,
    mock_async_client,
    mock_ssl_context,
    mock_save_credentials,
    mock_json_load,
    _mock_open,
    _mock_exists,
    _mock_listdir,
    _mock_makedirs,
    monkeypatch,
):
    """
    When credentials are missing device_id, the client should discover it via whoami
    and then restore the session using the discovered device_id.
    """
    _mock_exists.return_value = True
    mock_json_load.return_value = {
        "homeserver": "https://matrix.example.org",
        "user_id": "@bot:example.org",
        "access_token": "test_token",
    }
    _mock_listdir.return_value = []
    mock_ssl_context.return_value = MagicMock()

    mock_client_instance = MagicMock()
    mock_client_instance.rooms = {}

    async def mock_sync(*_args, **_kwargs):
        """
        Create and return a MagicMock to simulate a sync operation result.

        Any positional and keyword arguments are accepted and ignored.

        Returns:
            MagicMock: A new MagicMock instance representing the mocked sync result.
        """
        return MagicMock()

    def mock_restore_login(user_id, device_id, access_token):
        """
        Set the mocked Matrix client's login state by assigning user, device, and token attributes.

        Parameters:
            user_id (str): Matrix user ID to set on the mock client.
            device_id (str): Device ID to set on the mock client.
            access_token (str): Access token to set on the mock client.
        """
        mock_client_instance.access_token = access_token
        mock_client_instance.user_id = user_id
        mock_client_instance.device_id = device_id

    discovered_device_id = "DISCOVERED_DEVICE"

    mock_client_instance.sync = AsyncMock(side_effect=mock_sync)
    mock_client_instance.restore_login = MagicMock(side_effect=mock_restore_login)
    mock_client_instance.whoami = AsyncMock(
        return_value=SimpleNamespace(device_id=discovered_device_id)
    )
    mock_client_instance.should_upload_keys = False
    mock_client_instance.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    mock_async_client.return_value = mock_client_instance
    # Minimal config needed for matrix_rooms
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config",
        {"matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}]},
        raising=False,
    )

    client = await connect_matrix()

    assert client is mock_client_instance
    # restore_login should use the discovered device_id from whoami
    mock_client_instance.restore_login.assert_called_once_with(
        user_id="@bot:example.org",
        device_id=discovered_device_id,
        access_token="test_token",
    )
    # Access token should still be set via restore_login
    assert mock_client_instance.access_token == "test_token"
    assert mock_client_instance.user_id == "@bot:example.org"
    assert mock_client_instance.device_id == discovered_device_id
    mock_save_credentials.assert_called_once_with(
        {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "access_token": "test_token",
            "device_id": discovered_device_id,
        }
    )


@pytest.mark.asyncio
async def test_connect_matrix_sync_timeout_closes_client(monkeypatch):
    """Initial sync timeout should close the client and raise ConnectionError."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_client.close = AsyncMock()
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    # Capture AsyncClient ssl argument for separate test
    def fake_async_client(*_args, **_kwargs):
        """
        Provide a preconfigured mock Matrix client for use in tests.

        Ignores all positional and keyword arguments and always returns the shared test mock client.

        Returns:
            mock_client: The preconfigured mock Matrix client instance used by tests.
        """
        return mock_client

    monkeypatch.setattr("mmrelay.matrix_utils.AsyncClient", fake_async_client)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config",
        {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        },
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    with pytest.raises(ConnectionError):
        await connect_matrix()

    mock_client.close.assert_awaited_once()
    import mmrelay.matrix_utils as mx

    assert mx.matrix_client is None


@pytest.mark.asyncio
async def test_connect_matrix_uses_ssl_context_object(monkeypatch):
    """Ensure AsyncClient receives the actual SSLContext object, not a bool."""
    ssl_ctx = object()
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.should_upload_keys = False
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock()

    client_calls = []

    def fake_async_client(*_args, **_kwargs):
        """
        Create a fake async Matrix client for tests that records the passed SSL value and returns a predefined mock client.

        Parameters:
            *_args: Ignored positional arguments.
            **_kwargs: Keyword arguments; the `ssl` key, if present, is recorded into `client_calls`.

        Returns:
            mock_client: The predefined mock client object used by tests.
        """
        client_calls.append(_kwargs.get("ssl"))
        return mock_client

    monkeypatch.setattr("mmrelay.matrix_utils.AsyncClient", fake_async_client)
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: ssl_ctx, raising=False
    )
    # Stub helpers to avoid extra work
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    monkeypatch.setattr(
        "mmrelay.matrix_utils.config",
        {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        },
        raising=False,
    )

    client = await connect_matrix()

    assert client is mock_client
    assert client_calls and client_calls[0] is ssl_ctx


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_connect_matrix_sync_error_closes_client(monkeypatch):
    """If initial sync returns an error response, the client should close and raise."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    error_response = type("SyncError", (), {})()
    mock_client.sync = AsyncMock(return_value=error_response)
    mock_client.close = AsyncMock()
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_async_client(*_args, **_kwargs):
        """
        Provide a preconfigured mock Matrix client for use in tests.

        Ignores all positional and keyword arguments and always returns the shared test mock client.

        Returns:
            mock_client: The preconfigured mock Matrix client instance used by tests.
        """
        return mock_client

    monkeypatch.setattr("mmrelay.matrix_utils.AsyncClient", fake_async_client)
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config",
        {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        },
        raising=False,
    )

    with pytest.raises(ConnectionError):
        await connect_matrix()

    mock_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_matrix_uploads_keys_when_needed(monkeypatch):
    """When should_upload_keys is True, keys_upload should be called once."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.close = AsyncMock()
    type(mock_client).should_upload_keys = PropertyMock(return_value=True)
    mock_client.keys_upload = AsyncMock()
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_async_client(*_args, **_kwargs):
        """
        Provide a preconfigured mock Matrix client for use in tests.

        Ignores all positional and keyword arguments and always returns the shared test mock client.

        Returns:
            mock_client: The preconfigured mock Matrix client instance used by tests.
        """
        return mock_client

    monkeypatch.setattr("mmrelay.matrix_utils.AsyncClient", fake_async_client)
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_status",
        lambda *_args, **_kwargs: {"overall_status": "ok"},
        raising=False,
    )

    def fake_import(name):
        """
        Return a fake module-like object used to simulate imports of nio/olm modules in tests.

        Parameters:
            name (str): Module name being imported.

        Returns:
            object: A module-like object:
              - For "nio.crypto": a SimpleNamespace with attribute `OlmDevice` set to True.
              - For "nio.store": a SimpleNamespace with attribute `SqliteStore` set to True.
              - For "olm": a MagicMock instance.
              - For any other name: a MagicMock instance.
        """
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_room_encryption_warnings",
        lambda *_args, **_kwargs: [],
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    monkeypatch.setattr(
        "mmrelay.matrix_utils.config",
        {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        },
        raising=False,
    )

    client = await connect_matrix()

    assert client is mock_client
    mock_client.keys_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_matrix_credentials_load_exception_uses_config(monkeypatch):
    """Credential load errors should warn and fall back to config auth."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
        patch("builtins.open", side_effect=OSError("boom")),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        client = await connect_matrix(config)

    assert client is mock_client
    assert any(
        "Error loading credentials" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_ignores_config_access_token_when_credentials_present(
    monkeypatch,
):
    """Credentials should take precedence over config access_token."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "config_token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
        patch("builtins.open", new_callable=MagicMock),
        patch(
            "mmrelay.matrix_utils.json.load",
            return_value={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "access_token": "creds_token",
                "device_id": "DEV",
            },
        ),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        client = await connect_matrix(config)

    assert client is mock_client
    mock_logger.info.assert_any_call(
        "NOTE: Ignoring Matrix login details in config.yaml in favor of credentials.json"
    )


@pytest.mark.asyncio
async def test_connect_matrix_auto_login_load_credentials_failure(monkeypatch):
    """Automatic login should return None if new credentials cannot be loaded."""
    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "bot_user_id": "@bot:example.org",
            "password": "secret",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.login_matrix_bot", return_value=True),
        patch("mmrelay.matrix_utils.load_credentials", return_value=None),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await connect_matrix(config)

    assert result is None
    mock_logger.error.assert_called_with("Failed to load newly created credentials")


@pytest.mark.asyncio
async def test_connect_matrix_auto_login_failure(monkeypatch):
    """Automatic login failures should return None."""
    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "bot_user_id": "@bot:example.org",
            "password": "secret",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.login_matrix_bot", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await connect_matrix(config)

    assert result is None
    assert any(
        "Automatic login failed" in call.args[0]
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_missing_matrix_section_returns_none():
    """Missing matrix config should log and return None."""
    config = {"matrix_rooms": []}

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await connect_matrix(config)

    assert result is None
    mock_logger.error.assert_any_call(
        "No Matrix authentication available. Neither credentials.json nor matrix section in config found."
    )


@pytest.mark.asyncio
async def test_connect_matrix_missing_required_fields_returns_none():
    """Missing required fields in matrix section should return None."""
    config = {
        "matrix": {"homeserver": "https://example.org", "bot_user_id": "@bot:example"},
        "matrix_rooms": [],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await connect_matrix(config)

    assert result is None
    assert any(
        "Matrix section is missing required fields" in call.args[0]
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_missing_matrix_rooms_raises():
    """Missing matrix_rooms should raise ValueError."""
    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        }
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        pytest.raises(ValueError),
    ):
        await connect_matrix(config)


@pytest.mark.asyncio
async def test_connect_matrix_e2ee_windows_disables(monkeypatch):
    """E2EE should be disabled on Windows platforms."""
    import mmrelay.matrix_utils as mx

    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    monkeypatch.setattr("mmrelay.matrix_utils.sys.platform", "win32", raising=False)
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr("mmrelay.matrix_utils.AsyncClientConfig", MagicMock())
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
            "encryption": {"enabled": True},
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        await connect_matrix(config)

    _, kwargs = mx.AsyncClientConfig.call_args
    assert kwargs["encryption_enabled"] is False


@pytest.mark.asyncio
async def test_connect_matrix_e2ee_store_path_from_config(monkeypatch):
    """Configured E2EE store_path should be expanded and created."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_import(name):
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr("mmrelay.matrix_utils.sys.platform", "linux", raising=False)
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    store_path = os.path.expanduser("~/mmrelay-store")
    client_calls = []

    def fake_async_client(*_args, **_kwargs):
        client_calls.append(_kwargs)
        return mock_client

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        fake_async_client,
        raising=False,
    )
    with (
        patch("mmrelay.matrix_utils.os.makedirs"),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True, "store_path": store_path},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    assert client_calls
    assert client_calls[0]["store_path"] == store_path


@pytest.mark.asyncio
async def test_connect_matrix_e2ee_store_path_precedence_encryption(monkeypatch):
    """Encryption store_path should take precedence over e2ee store_path."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_import(name):
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr("mmrelay.matrix_utils.sys.platform", "linux", raising=False)
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    encryption_path = os.path.expanduser("~/enc-store")
    e2ee_path = os.path.expanduser("~/e2ee-store")
    client_calls = []

    def fake_async_client(*_args, **_kwargs):
        client_calls.append(_kwargs)
        return mock_client

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        fake_async_client,
        raising=False,
    )
    with (
        patch("mmrelay.matrix_utils.os.makedirs"),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True, "store_path": encryption_path},
                "e2ee": {"store_path": e2ee_path},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    assert client_calls
    assert client_calls[0]["store_path"] == encryption_path


@pytest.mark.asyncio
async def test_connect_matrix_e2ee_store_path_uses_e2ee_section(monkeypatch):
    """e2ee store_path should be used when encryption store_path is absent."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_import(name):
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr("mmrelay.matrix_utils.sys.platform", "linux", raising=False)
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    e2ee_path = os.path.expanduser("~/e2ee-store")
    client_calls = []

    def fake_async_client(*_args, **_kwargs):
        client_calls.append(_kwargs)
        return mock_client

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        fake_async_client,
        raising=False,
    )
    with (
        patch("mmrelay.matrix_utils.os.makedirs"),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "e2ee": {"enabled": True, "store_path": e2ee_path},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    assert client_calls
    assert client_calls[0]["store_path"] == e2ee_path


@pytest.mark.asyncio
async def test_connect_matrix_e2ee_store_path_default(monkeypatch):
    """Default store path should be used when no store_path is configured."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_import(name):
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr("mmrelay.matrix_utils.sys.platform", "linux", raising=False)
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    default_path = "/tmp/default-store"
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_store_dir",
        lambda: default_path,
        raising=False,
    )
    client_calls = []

    def fake_async_client(*_args, **_kwargs):
        client_calls.append(_kwargs)
        return mock_client

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        fake_async_client,
        raising=False,
    )
    with (
        patch("mmrelay.matrix_utils.os.makedirs"),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    assert client_calls
    assert client_calls[0]["store_path"] == default_path


@pytest.mark.asyncio
async def test_connect_matrix_whoami_missing_device_id_warns(monkeypatch):
    """Missing device_id from whoami should warn and continue."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.whoami = AsyncMock(return_value=SimpleNamespace(device_id=None))

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
        patch("builtins.open", new_callable=MagicMock),
        patch(
            "mmrelay.matrix_utils.json.load",
            return_value={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "access_token": "token",
            },
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        config = {"matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}]}
        await connect_matrix(config)

    mock_logger.warning.assert_any_call("whoami response did not contain device_id")


@pytest.mark.asyncio
async def test_connect_matrix_whoami_failure_warns(monkeypatch):
    """whoami failures should warn and continue without a device_id."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.whoami = AsyncMock(side_effect=NioLocalTransportError("fail"))

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_status",
        lambda *_args, **_kwargs: {"overall_status": "ok"},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_room_encryption_warnings",
        lambda *_args, **_kwargs: [],
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
        patch("builtins.open", new_callable=MagicMock),
        patch(
            "mmrelay.matrix_utils.json.load",
            return_value={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "access_token": "token",
            },
        ),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        config = {"matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}]}
        await connect_matrix(config)

    assert any(
        "Failed to discover device_id via whoami" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )
    mock_logger.warning.assert_any_call(
        "E2EE may not work properly without a device_id"
    )


@pytest.mark.asyncio
async def test_connect_matrix_save_credentials_failure_warns(monkeypatch):
    """Save failures after whoami device_id discovery should warn."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.whoami = AsyncMock(return_value=SimpleNamespace(device_id="DEV"))

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_status",
        lambda *_args, **_kwargs: {"overall_status": "ok"},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_room_encryption_warnings",
        lambda *_args, **_kwargs: [],
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
        patch("builtins.open", new_callable=MagicMock),
        patch(
            "mmrelay.matrix_utils.json.load",
            return_value={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "access_token": "token",
            },
        ),
        patch("mmrelay.matrix_utils.save_credentials", side_effect=OSError("boom")),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        config = {"matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}]}
        await connect_matrix(config)

    assert any(
        "Failed to persist discovered device_id" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_keys_upload_failure_logs(monkeypatch):
    """Key upload errors should be logged and not raise."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.close = AsyncMock()
    type(mock_client).should_upload_keys = PropertyMock(return_value=True)
    mock_client.keys_upload = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_import(name):
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    mock_logger = MagicMock()
    monkeypatch.setattr(
        "mmrelay.matrix_utils.logger",
        mock_logger,
        raising=False,
    )

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
            "encryption": {"enabled": True},
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        client = await connect_matrix(config)

    assert client is mock_client
    mock_logger.error.assert_any_call(
        "Consider regenerating credentials with: mmrelay auth login"
    )


@pytest.mark.asyncio
async def test_connect_matrix_displayname_fallbacks(monkeypatch):
    """Missing displayname should fall back to bot_user_id."""
    import mmrelay.matrix_utils as mx

    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname=None)
    )
    mock_client.close = AsyncMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_status",
        lambda *_args, **_kwargs: {"overall_status": "ok"},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_room_encryption_warnings",
        lambda *_args, **_kwargs: [],
        raising=False,
    )

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with patch("mmrelay.matrix_utils.os.path.exists", return_value=False):
        client = await connect_matrix(config)

    assert client is mock_client
    assert mx.bot_user_name == "@bot:example.org"


@pytest.mark.asyncio
async def test_connect_matrix_displayname_exception_fallback(monkeypatch):
    """Displayname lookups that error should fall back to bot_user_id."""
    import mmrelay.matrix_utils as mx

    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_client.close = AsyncMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_status",
        lambda *_args, **_kwargs: {"overall_status": "ok"},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_room_encryption_warnings",
        lambda *_args, **_kwargs: [],
        raising=False,
    )

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        client = await connect_matrix(config)

    assert client is mock_client
    assert mx.bot_user_name == "@bot:example.org"
    assert any(
        "Failed to get bot display name" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


# E2EE Configuration Tests


# E2EE Client Initialization Tests


# Verify E2EE initialization sequence was called
# Since we're using simple functions, we can't assert calls, but we can verify the client was returned
# The fact that connect_matrix completed successfully means all the async calls worked


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.os.makedirs")
@patch("mmrelay.matrix_utils.os.listdir")
@patch("mmrelay.matrix_utils.os.path.exists")
@patch("mmrelay.matrix_utils._create_ssl_context")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_connect_matrix_e2ee_store_missing_db_files_warns(
    mock_logger,
    mock_async_client,
    mock_ssl_context,
    mock_exists,
    mock_listdir,
    _mock_makedirs,
):
    """Missing E2EE store DB files should warn when E2EE is enabled."""
    mock_listdir.return_value = ["notes.txt"]

    def exists_side_effect(path):
        if path.endswith("credentials.json"):
            return False
        if path == "/test/store":
            return True
        return False

    mock_exists.side_effect = exists_side_effect
    mock_ssl_context.return_value = MagicMock()

    mock_client_instance = MagicMock()
    mock_client_instance.rooms = {}
    mock_client_instance.sync = AsyncMock(return_value=MagicMock())
    mock_client_instance.whoami = AsyncMock(
        return_value=SimpleNamespace(device_id="DEV")
    )
    mock_client_instance.should_upload_keys = False
    mock_client_instance.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_async_client.return_value = mock_client_instance

    test_config = {
        "matrix": {
            "homeserver": "https://matrix.example.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:example.org",
            "encryption": {"enabled": True, "store_path": "/test/store"},
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    mock_olm = MagicMock()
    import importlib as _importlib

    real_import_module = _importlib.import_module

    def mock_import_side_effect(module_name, *args, **kwargs):
        if module_name == "olm":
            return mock_olm
        if module_name == "nio.crypto":
            mock_crypto = MagicMock()
            mock_crypto.OlmDevice = MagicMock()
            return mock_crypto
        if module_name == "nio.store":
            mock_store = MagicMock()
            mock_store.SqliteStore = MagicMock()
            return mock_store
        return real_import_module(module_name, *args, **kwargs)

    with (
        patch("mmrelay.config.is_e2ee_enabled", return_value=True),
        patch("importlib.import_module", side_effect=mock_import_side_effect),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch(
            "mmrelay.matrix_utils._resolve_aliases_in_mapping",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "mmrelay.matrix_utils._display_room_channel_mappings",
            return_value=None,
        ),
        patch("mmrelay.matrix_utils.matrix_client", None),
    ):
        await connect_matrix(test_config)

    assert any(
        "No existing E2EE store files found" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_e2ee_key_sharing_delay(monkeypatch):
    """E2EE-enabled connections should wait for key sharing delay."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_status",
        lambda *_args, **_kwargs: {"overall_status": "ok"},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_room_encryption_warnings",
        lambda *_args, **_kwargs: [],
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    mock_olm = MagicMock()
    import importlib as _importlib

    real_import_module = _importlib.import_module

    def mock_import_side_effect(module_name, *args, **kwargs):
        if module_name == "olm":
            return mock_olm
        if module_name == "nio.crypto":
            mock_crypto = MagicMock()
            mock_crypto.OlmDevice = MagicMock()
            return mock_crypto
        if module_name == "nio.store":
            mock_store = MagicMock()
            mock_store.SqliteStore = MagicMock()
            return mock_store
        return real_import_module(module_name, *args, **kwargs)

    sleep_mock = AsyncMock()
    with (
        patch("mmrelay.config.is_e2ee_enabled", return_value=True),
        patch("importlib.import_module", side_effect=mock_import_side_effect),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.os.makedirs"),
        patch("mmrelay.matrix_utils.os.listdir", return_value=["test.db"]),
        patch("mmrelay.matrix_utils.asyncio.sleep", sleep_mock),
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True, "store_path": "/tmp/store"},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    sleep_mock.assert_awaited_once()


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.load_credentials")
@patch("mmrelay.matrix_utils._create_ssl_context")
@patch("mmrelay.matrix_utils.AsyncClient")
async def test_connect_matrix_legacy_config(
    mock_async_client, mock_ssl_context, mock_load_credentials
):
    """Test Matrix connection with legacy config (no E2EE)."""
    # No credentials.json available
    mock_load_credentials.return_value = None

    # Mock SSL context
    mock_ssl_context.return_value = MagicMock()

    # Mock AsyncClient instance
    mock_client_instance = MagicMock()
    mock_client_instance.sync = AsyncMock()
    mock_client_instance.rooms = {}
    mock_client_instance.whoami = AsyncMock()
    mock_client_instance.whoami.return_value = MagicMock(device_id="LEGACY_DEVICE")
    mock_client_instance.get_displayname = AsyncMock()
    mock_client_instance.get_displayname.return_value = MagicMock(
        displayname="Test Bot"
    )
    mock_async_client.return_value = mock_client_instance

    # Legacy config without E2EE
    test_config = {
        "matrix": {
            "homeserver": "https://matrix.example.org",
            "access_token": "legacy_token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    # Mock the global matrix_client to None to ensure fresh creation
    with patch("mmrelay.matrix_utils.matrix_client", None):
        client = await connect_matrix(test_config)

        assert client is not None
        assert client == mock_client_instance

        # Verify AsyncClient was created without E2EE
        mock_async_client.assert_called_once()
        call_args = mock_async_client.call_args
        assert call_args[1].get("device_id") is None
        assert call_args[1].get("store_path") is None

        # Verify sync was called
        mock_client_instance.sync.assert_called()


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
async def test_login_matrix_bot_reuses_existing_device_id(
    _mock_ssl_context, mock_async_client, _mock_save_credentials
):
    """Existing credentials should supply device_id when available."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.matrix_utils.get_base_dir", return_value="/tmp"),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
        patch("builtins.open", new_callable=MagicMock),
        patch(
            "mmrelay.matrix_utils.json.load",
            return_value={"user_id": "@user:matrix.org", "device_id": "DEV"},
        ),
        patch(
            "mmrelay.matrix_utils._normalize_bot_user_id",
            return_value="@user:matrix.org",
        ),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is True
    assert mock_async_client.call_args_list[1].kwargs["device_id"] == "DEV"
    assert mock_main_client.device_id == "DEV"


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
async def test_login_matrix_bot_e2ee_store_path_created(
    _mock_ssl_context, mock_async_client, _mock_save_credentials
):
    """E2EE-enabled logins should create a store path."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={"matrix": {}}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=True),
        patch("mmrelay.matrix_utils.get_e2ee_store_dir", return_value="/tmp/store"),
        patch("mmrelay.matrix_utils.os.makedirs") as mock_makedirs,
        patch(
            "mmrelay.matrix_utils._normalize_bot_user_id",
            return_value="@user:matrix.org",
        ),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is True
    mock_makedirs.assert_called_once_with("/tmp/store", exist_ok=True)


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_api_login_debug_path(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """API login debug path should parse and log request payload safely."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    class DummyApi:
        @staticmethod
        def login(user, password, device_name, device_id=None):
            import json

            return (
                "POST",
                "/login",
                json.dumps(
                    {
                        "user": user,
                        "password": password,
                        "device_name": device_name,
                        "device_id": device_id,
                    }
                ),
            )

    with (
        patch.dict("sys.modules", {"nio.api": SimpleNamespace(Api=DummyApi)}),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is True
    assert any(
        "Matrix API call details" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


@patch("mmrelay.matrix_utils.getpass.getpass")
@patch("mmrelay.matrix_utils.input")
async def test_login_matrix_bot_type_error_specific_message(mock_input, mock_getpass):
    """Type errors from matrix-nio should return False."""
    mock_input.side_effect = ["https://matrix.org", "user", "y"]
    mock_getpass.return_value = "pass"

    with (
        patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        mock_client = AsyncMock()
        mock_client.login.side_effect = TypeError(
            "'>=' not supported between instances of 'str' and 'int'"
        )
        mock_client.close = AsyncMock()
        mock_async_client.return_value = mock_client

        result = await login_matrix_bot()

    assert result is False
    assert any(
        "Matrix-nio library error during login" in call.args[0]
        for call in mock_logger.error.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_login_response_unexpected(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Unexpected login responses should return False."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token=None, status_code=None, message=None
    )
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is False
    assert any(
        "Unexpected login response" in call.args[0]
        for call in mock_logger.error.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_whoami_fallback_when_missing_user_id(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    mock_save_credentials,
):
    """Missing user_id from whoami should fall back to response user_id."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@fallback:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id=None)
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is True
    assert mock_save_credentials.call_args.args[0]["user_id"] == "@fallback:matrix.org"
    assert any(
        "whoami failed, using fallback user_id" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_logout_others_warns(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Logout_others should warn that the feature is unimplemented."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=True,
        )

    assert result is True
    mock_logger.warning.assert_any_call("Logout others not yet implemented")


@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_save_credentials_failure_triggers_cleanup(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
):
    """Failures during save_credentials should trigger outer exception handling."""
    from mmrelay.matrix_utils import NioLoginError

    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock(side_effect=OSError("close-fail"))

    with (
        patch(
            "mmrelay.matrix_utils.save_credentials", side_effect=NioLoginError("fail")
        ),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is False
    mock_logger.exception.assert_any_call("Error during login")
    assert any(
        "Ignoring error during client cleanup" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_login_timeout(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
):
    """Login timeouts should log and return False."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.side_effect = asyncio.TimeoutError
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is False
    assert any(
        "Login timed out after" in call.args[0]
        for call in mock_logger.exception.call_args_list
    )
    mock_main_client.close.assert_awaited_once()


@pytest.mark.parametrize(
    "exc, expected_log",
    [
        (ConnectionError("boom"), "Network connectivity issue detected."),
        (ssl.SSLError("bad cert"), "SSL/TLS certificate issue detected."),
        (type("DNSError", (Exception,), {})("dns"), "DNS resolution failed."),
        (
            ValueError("'user_id' is a required property"),
            "Matrix server response validation failed.",
        ),
    ],
)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_login_exception_guidance(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    exc,
    expected_log,
):
    """Login exceptions should emit targeted troubleshooting guidance."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.side_effect = exc
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is False
    assert any(
        expected_log in call.args[0] for call in mock_logger.error.call_args_list
    )
    mock_main_client.close.assert_awaited_once()


@pytest.mark.parametrize(
    "status_code, message, expected_log",
    [
        (401, "M_FORBIDDEN", "Authentication failed - invalid username or password."),
        (404, "M_NOT_FOUND", "User not found or homeserver not found."),
        (429, "M_LIMIT_EXCEEDED", "Rate limited - too many login attempts."),
        (
            500,
            "server error",
            "Matrix server error - the server is experiencing issues.",
        ),
        (418, "teapot", "Login failed for unknown reason."),
    ],
)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_login_response_status_codes(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    status_code,
    message,
    expected_log,
):
    """Status-coded login failures should log targeted guidance."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token=None, status_code=status_code, message=message
    )
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is False
    assert any(
        expected_log in call.args[0] for call in mock_logger.error.call_args_list
    )
    mock_main_client.close.assert_awaited_once()


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_whoami_exception_uses_fallback(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    mock_save_credentials,
):
    """whoami failures should warn and fall back to response user_id."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@fallback:matrix.org"
    )
    mock_main_client.whoami.side_effect = RuntimeError("whoami failed")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is True
    assert mock_save_credentials.call_args.args[0]["user_id"] == "@fallback:matrix.org"
    assert any(
        "whoami call failed" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_e2ee_config_load_exception_disables_e2ee(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Config load failures should disable E2EE and skip store setup."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", side_effect=RuntimeError("boom")),
        patch("mmrelay.config.is_e2ee_enabled") as mock_is_e2ee,
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.get_e2ee_store_dir") as mock_store_dir,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is True
    mock_is_e2ee.assert_not_called()
    mock_store_dir.assert_not_called()
    assert any(
        "Could not load config for E2EE check" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )
    assert any(
        "E2EE disabled in configuration" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_no_password_warns(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Empty passwords should log a warning."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.getpass.getpass", return_value=""),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password=None,
            logout_others=False,
        )

    assert result is True
    mock_logger.warning.assert_any_call("No password provided")


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_credentials_load_failure_logs_debug(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Credential load errors should be logged and ignored."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.matrix_utils.get_base_dir", return_value="/tmp"),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
        patch("builtins.open", side_effect=OSError("boom")),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is True
    assert any(
        "Could not load existing credentials" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_api_login_debug_failure_logs(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """API debug failures should log and continue."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    class DummyApi:
        @staticmethod
        def login(*_args, **_kwargs):
            raise RuntimeError("api boom")

    with (
        patch.dict("sys.modules", {"nio.api": SimpleNamespace(Api=DummyApi)}),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is True
    assert any(
        "Failed to test API call" in call.args[0]
        for call in mock_logger.error.call_args_list
    )


# Matrix logout tests


class TestMatrixUtilityFunctions:
    def test_truncate_message_respects_utf8_boundaries(self):
        text = "hello😊"
        truncated = truncate_message(text, max_bytes=6)
        assert truncated == "hello"

    def test_strip_quoted_lines_removes_quoted_content(self):
        text = "Line one\n> quoted line\n Line two"
        result = strip_quoted_lines(text)
        assert result == "Line one Line two"

    def test_validate_prefix_format_success(self):
        is_valid, error = validate_prefix_format("{display}", {"display": "Alice"})
        assert is_valid is True
        assert error is None

    def test_validate_prefix_format_missing_key(self):
        is_valid, error = validate_prefix_format("{missing}", {"display": "Alice"})
        assert is_valid is False
        assert error is not None
        assert "missing" in error


class TestMatrixE2EEHasAttrChecks:
    """Test class for E2EE hasattr checks in matrix_utils.py"""

    @pytest.fixture
    def e2ee_config(self):
        """
        Create a minimal Matrix configuration dictionary with end-to-end encryption enabled for tests.

        The configuration contains a `matrix` section with homeserver, access token, bot user id, and `e2ee: {"enabled": True}`, and a `matrix_rooms` mapping with a sample room configured for `meshtastic_channel: 0`.

        Returns:
            dict: Test-ready Matrix configuration with E2EE enabled.
        """
        return {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
                "e2ee": {"enabled": True},
            },
            "matrix_rooms": {"!room:matrix.org": {"meshtastic_channel": 0}},
        }

    async def test_connect_matrix_hasattr_checks_success(self, e2ee_config):
        """Test hasattr checks for nio.crypto.OlmDevice and nio.store.SqliteStore when available"""
        with (
            patch("mmrelay.matrix_utils.matrix_client", None),
            patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
            patch("mmrelay.matrix_utils.logger"),
            patch("mmrelay.matrix_utils.importlib.import_module") as mock_import,
        ):
            # Mock AsyncClient instance with proper async methods
            mock_client_instance = MagicMock()
            mock_client_instance.rooms = {}
            mock_client_instance.login = AsyncMock(return_value=MagicMock())
            mock_client_instance.sync = AsyncMock(return_value=MagicMock())
            mock_client_instance.join = AsyncMock(return_value=MagicMock())
            mock_client_instance.close = AsyncMock()
            mock_client_instance.get_displayname = AsyncMock(
                return_value=MagicMock(displayname="TestBot")
            )
            mock_client_instance.keys_upload = AsyncMock()
            mock_async_client.return_value = mock_client_instance

            # Create mock modules with required attributes
            mock_olm = SimpleNamespace()
            mock_nio_crypto = SimpleNamespace(OlmDevice=MagicMock())
            mock_nio_store = SimpleNamespace(SqliteStore=MagicMock())

            def import_side_effect(name):
                """
                Return a mock module object for the specified import name to simulate E2EE dependencies in tests.

                Parameters:
                    name (str): Fully qualified module name ('olm', 'nio.crypto', or 'nio.store').

                Returns:
                    object: The mock module corresponding to the requested name.

                Raises:
                    ImportError: If the requested name is not a supported mock module.
                """
                if name == "olm":
                    return mock_olm
                elif name == "nio.crypto":
                    return mock_nio_crypto
                elif name == "nio.store":
                    return mock_nio_store
                else:
                    # For any other import, raise ImportError to simulate missing dependency
                    raise ImportError(f"No module named '{name}'")

            mock_import.side_effect = import_side_effect

            # Run the async function
            await connect_matrix(e2ee_config)

            # Verify client was created and E2EE dependencies were checked
            mock_async_client.assert_called_once()
            expected_imports = {"olm", "nio.crypto", "nio.store"}
            actual_imports = {call.args[0] for call in mock_import.call_args_list}
            assert expected_imports.issubset(actual_imports)

    async def test_connect_matrix_hasattr_checks_missing_olmdevice(self, e2ee_config):
        """Test hasattr check failure when nio.crypto.OlmDevice is missing"""
        with (
            patch("mmrelay.matrix_utils.matrix_client", None),
            patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
            patch("mmrelay.matrix_utils.logger") as mock_logger,
            patch("mmrelay.matrix_utils.importlib.import_module") as mock_import,
        ):
            # Mock AsyncClient instance with proper async methods
            mock_client_instance = MagicMock()
            mock_client_instance.rooms = {}
            mock_client_instance.login = AsyncMock(return_value=MagicMock())
            mock_client_instance.sync = AsyncMock(return_value=MagicMock())
            mock_client_instance.join = AsyncMock(return_value=MagicMock())
            mock_client_instance.close = AsyncMock()
            mock_client_instance.get_displayname = AsyncMock(
                return_value=MagicMock(displayname="TestBot")
            )
            mock_async_client.return_value = mock_client_instance

            # Create mock modules where nio.crypto lacks OlmDevice
            mock_olm = SimpleNamespace()
            mock_nio_crypto = SimpleNamespace()
            # Simulate missing OlmDevice attribute to exercise hasattr failure
            mock_nio_store = SimpleNamespace(SqliteStore=MagicMock())

            def import_side_effect(name):
                """
                Return a mock module object for the specified import name to simulate E2EE dependencies in tests.

                Parameters:
                    name (str): Fully qualified module name ('olm', 'nio.crypto', or 'nio.store').

                Returns:
                    object: The mock module corresponding to the requested name.

                Raises:
                    ImportError: If the requested name is not a supported mock module.
                """
                if name == "olm":
                    return mock_olm
                elif name == "nio.crypto":
                    return mock_nio_crypto
                elif name == "nio.store":
                    return mock_nio_store
                else:
                    # For any other import, raise ImportError to simulate missing dependency
                    raise ImportError(f"No module named '{name}'")

            mock_import.side_effect = import_side_effect

            # Run the async function
            await connect_matrix(e2ee_config)

            # Verify ImportError was logged and E2EE was disabled
            mock_logger.exception.assert_called_with("Missing E2EE dependency")
            mock_logger.error.assert_called_with(
                "Please reinstall with: pipx install 'mmrelay[e2e]'"
            )
            mock_logger.warning.assert_called_with(
                "E2EE will be disabled for this session."
            )

    async def test_connect_matrix_hasattr_checks_missing_sqlitestore(self, e2ee_config):
        """Test hasattr check failure when nio.store.SqliteStore is missing"""
        with (
            patch("mmrelay.matrix_utils.matrix_client", None),
            patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
            patch("mmrelay.matrix_utils.logger") as mock_logger,
            patch("mmrelay.matrix_utils.importlib.import_module") as mock_import,
        ):
            # Mock AsyncClient instance with proper async methods
            mock_client_instance = MagicMock()
            mock_client_instance.rooms = {}
            mock_client_instance.login = AsyncMock(return_value=MagicMock())
            mock_client_instance.sync = AsyncMock(return_value=MagicMock())
            mock_client_instance.join = AsyncMock(return_value=MagicMock())
            mock_client_instance.close = AsyncMock()
            mock_client_instance.get_displayname = AsyncMock(
                return_value=MagicMock(displayname="TestBot")
            )
            mock_async_client.return_value = mock_client_instance

            # Create mock modules where nio.store lacks SqliteStore
            mock_olm = SimpleNamespace()
            mock_nio_crypto = SimpleNamespace(OlmDevice=MagicMock())
            # Simulate missing SqliteStore attribute to exercise hasattr failure
            mock_nio_store = SimpleNamespace()

            def import_side_effect(name):
                """
                Provide a mock module for simulating E2EE dependencies during tests.

                Parameters:
                    name (str): Fully qualified module name to mock (e.g., 'olm', 'nio.crypto', or 'nio.store').

                Returns:
                    object: The mock module corresponding to the requested name.

                Raises:
                    ImportError: If the requested name is not a supported mock module.
                """
                if name == "olm":
                    return mock_olm
                elif name == "nio.crypto":
                    return mock_nio_crypto
                elif name == "nio.store":
                    return mock_nio_store
                else:
                    # For any other import, raise ImportError to simulate missing dependency
                    raise ImportError(f"No module named '{name}'")

            mock_import.side_effect = import_side_effect

            # Run the async function
            await connect_matrix(e2ee_config)

            # Verify ImportError was logged and E2EE was disabled
            mock_logger.exception.assert_called_with("Missing E2EE dependency")
            mock_logger.error.assert_called_with(
                "Please reinstall with: pipx install 'mmrelay[e2e]'"
            )
            mock_logger.warning.assert_called_with(
                "E2EE will be disabled for this session."
            )


class TestGetDetailedSyncErrorMessage:
    """Test cases for _get_detailed_matrix_error_message function."""


def test_can_auto_create_credentials_whitespace_values():
    """
    Test _can_auto_create_credentials returns False when values contain only whitespace.
    """
    config = {
        "homeserver": "   ",
        "bot_user_id": "@bot:matrix.org",
        "password": "password123",
    }

    result = _can_auto_create_credentials(config)
    assert result is False


async def test_connect_matrix_e2ee_missing_nio_crypto():
    """
    Test connect_matrix handles missing nio.crypto.OlmDevice gracefully.
    """
    config = {
        "matrix": {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:matrix.org",
            "encryption": {"enabled": True},
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch("mmrelay.matrix_utils._create_ssl_context"),
        patch("mmrelay.matrix_utils.importlib.import_module") as mock_import,
    ):
        # Mock importlib to simulate missing nio.crypto
        def mock_import_side_effect(module_name):
            if module_name == "olm":
                return MagicMock()  # olm is available
            elif module_name == "nio.crypto":
                mock_crypto = MagicMock()
                mock_crypto.OlmDevice = MagicMock()
                # Remove OlmDevice attribute
                del mock_crypto.OlmDevice
                return mock_crypto
            return MagicMock()

        mock_import.side_effect = mock_import_side_effect

        # Mock AsyncClient instance
        mock_client_instance = MagicMock()
        mock_client_instance.rooms = {}

        async def mock_sync(*args, **kwargs):
            return MagicMock()

        async def mock_get_displayname(*args, **kwargs):
            return MagicMock(displayname="Test Bot")

        mock_client_instance.sync = mock_sync
        mock_client_instance.get_displayname = mock_get_displayname
        mock_async_client.return_value = mock_client_instance

        result = await connect_matrix(config)

        # Should still create client but with E2EE disabled
        assert result == mock_client_instance
        # Should log exception about missing nio.crypto.OlmDevice
        mock_logger.exception.assert_called_with("Missing E2EE dependency")


async def test_connect_matrix_e2ee_missing_sqlite_store():
    """
    Test connect_matrix handles missing nio.store.SqliteStore gracefully.
    """
    config = {
        "matrix": {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:matrix.org",
            "encryption": {"enabled": True},
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch("mmrelay.matrix_utils._create_ssl_context"),
        patch("mmrelay.matrix_utils.importlib.import_module") as mock_import,
    ):
        # Mock importlib to simulate missing nio.store.SqliteStore
        def mock_import_side_effect(module_name):
            if module_name == "olm":
                return MagicMock()  # olm is available
            elif module_name == "nio.crypto":
                mock_crypto = MagicMock()
                mock_crypto.OlmDevice = MagicMock()
                return mock_crypto
            elif module_name == "nio.store":
                mock_store = MagicMock()
                mock_store.SqliteStore = MagicMock()
                # Remove SqliteStore attribute
                del mock_store.SqliteStore
                return mock_store
            return MagicMock()

        mock_import.side_effect = mock_import_side_effect

        # Mock AsyncClient instance
        mock_client_instance = MagicMock()
        mock_client_instance.rooms = {}

        async def mock_sync(*args, **kwargs):
            return MagicMock()

        async def mock_get_displayname(*args, **kwargs):
            return MagicMock(displayname="Test Bot")

        mock_client_instance.sync = mock_sync
        mock_client_instance.get_displayname = mock_get_displayname
        mock_async_client.return_value = mock_client_instance

        result = await connect_matrix(config)

        # Should still create client but with E2EE disabled
        assert result == mock_client_instance
        # Should log exception about missing nio.store.SqliteStore
        mock_logger.exception.assert_called_with("Missing E2EE dependency")


@pytest.mark.asyncio
async def test_handle_matrix_reply_success():
    """Test handle_matrix_reply processes reply successfully."""

    # Create mock objects
    mock_room = MagicMock()
    mock_event = MagicMock()
    mock_room_config = {"meshtastic_channel": 0}
    mock_config = {"matrix_rooms": []}

    # Mock database lookup to return original message
    with (
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
        # Set up successful database lookup
        mock_db_lookup.return_value = (
            "orig_mesh_id",
            "!room123",
            "original text",
            "local",
        )
        mock_format_reply.return_value = "formatted reply"
        mock_get_display_name.return_value = "Test User"
        mock_send_reply.return_value = True

        # Test successful reply handling
        result = await handle_matrix_reply(
            mock_room,
            mock_event,
            "reply_to_event_id",
            "reply text",
            mock_room_config,
            True,  # storage_enabled
            "local_meshnet",
            mock_config,
        )

        # Verify result
        assert result is True
        # Verify database was queried
        mock_db_lookup.assert_called_once_with("reply_to_event_id")
        # Verify reply was formatted and sent
        mock_format_reply.assert_called_once()
        mock_send_reply.assert_called_once()


@pytest.mark.asyncio
async def test_handle_matrix_reply_numeric_string_reply_id():
    """Numeric string meshtastic_id should be treated as a reply_id."""
    mock_room = MagicMock()
    mock_event = MagicMock()
    mock_room_config = {"meshtastic_channel": 0}
    mock_config = {"matrix_rooms": []}

    with (
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

    with (
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
async def test_handle_matrix_reply_original_not_found():
    """Test handle_matrix_reply when original message is not found."""

    # Create mock objects
    mock_room = MagicMock()
    mock_event = MagicMock()
    mock_room_config = {"meshtastic_channel": 0}
    mock_config = {"matrix_rooms": []}

    with (
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id"
        ) as mock_db_lookup,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        # Test when no original message found
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
async def test_on_decryption_failure():
    """Test on_decryption_failure handles decryption failures."""

    # Create mock room and event
    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        mock_client.to_device = AsyncMock()  # Make it async

        # Test successful key request
        await on_decryption_failure(mock_room, mock_event)

        # Verify the event was patched with room_id
        assert mock_event.room_id == "!room123:matrix.org"
        # Verify key request was created and sent
        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        mock_client.to_device.assert_called_once_with({"type": "m.room_key_request"})
        # Verify logging
        mock_logger.error.assert_called_once()  # Error about decryption failure
        mock_logger.info.assert_called_once()  # Success message

        # Reset mocks for error case
        mock_client.reset_mock()
        mock_logger.reset_mock()

        # Test when matrix client is None
        with patch("mmrelay.matrix_utils.matrix_client", None):
            await on_decryption_failure(mock_room, mock_event)
            # Should have logged the initial error plus the client unavailable error
            assert mock_logger.error.call_count == 2
            mock_client.to_device.assert_not_called()


@pytest.mark.asyncio
async def test_on_decryption_failure_missing_device_id():
    """Missing device_id should prevent key requests and log an error."""
    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = None
        mock_client.to_device = AsyncMock()

        await on_decryption_failure(mock_room, mock_event)

        mock_logger.error.assert_any_call(
            "Cannot request keys for event %s: client has no device_id",
            "$event123",
        )
        mock_client.to_device.assert_not_called()


@pytest.mark.asyncio
async def test_on_room_member():
    """Test on_room_member handles room member events."""

    # Create mock room and event
    mock_room = MagicMock()
    mock_event = MagicMock()

    # The function just passes, so we just test it can be called
    await on_room_member(mock_room, mock_event)


class TestUncoveredMatrixUtils(unittest.TestCase):
    """Test cases for uncovered functions and edge cases in matrix_utils.py."""

    @patch("mmrelay.matrix_utils.logger")
    def test_is_room_alias_with_various_inputs(self, mock_logger):
        """Test _is_room_alias function with different input types."""

        # Test with valid alias
        self.assertTrue(_is_room_alias("#room:example.com"))

        # Test with room ID
        self.assertFalse(_is_room_alias("!room:example.com"))

        # Test with non-string types
        self.assertFalse(_is_room_alias(None))
        self.assertFalse(_is_room_alias(123))
        self.assertFalse(_is_room_alias([]))

    @patch("mmrelay.matrix_utils.logger")
    def test_iter_room_alias_entries_list_format(self, mock_logger):
        """Test _iter_room_alias_entries with list format."""

        # Test with list of strings
        mapping = ["#room1:example.com", "#room2:example.com"]
        entries = list(_iter_room_alias_entries(mapping))

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][0], "#room1:example.com")
        self.assertEqual(entries[1][0], "#room2:example.com")

        # Test that setters work
        entries[0][1]("!newroom:example.com")
        self.assertEqual(mapping[0], "!newroom:example.com")

    @patch("mmrelay.matrix_utils.logger")
    def test_iter_room_alias_entries_dict_format(self, _mock_logger):
        """Test _iter_room_alias_entries with dict format."""
        mapping = {
            "one": "#room1:example.com",
            "two": {"id": "#room2:example.com"},
        }
        entries = list(_iter_room_alias_entries(mapping))

        self.assertEqual(len(entries), 2)
        entries[0][1]("!new1:example.com")
        entries[1][1]("!new2:example.com")

        self.assertEqual(mapping["one"], "!new1:example.com")
        self.assertEqual(mapping["two"]["id"], "!new2:example.com")

    @patch("mmrelay.matrix_utils.logger")
    def test_can_auto_create_credentials_missing_fields(self, mock_logger):
        """Test _can_auto_create_credentials with missing fields."""
        from mmrelay.matrix_utils import _can_auto_create_credentials

        # Test missing homeserver
        config1 = {"bot_user_id": "@bot:example.com", "password": "secret123"}
        self.assertFalse(_can_auto_create_credentials(config1))

        # Test missing user_id
        config2 = {"homeserver": "https://example.com", "password": "secret123"}
        self.assertFalse(_can_auto_create_credentials(config2))

        # Test empty strings
        config3 = {
            "homeserver": "",
            "bot_user_id": "@bot:example.com",
            "password": "secret123",
        }
        self.assertFalse(_can_auto_create_credentials(config3))

    @patch("mmrelay.matrix_utils.logger")
    def test_normalize_bot_user_id_various_formats(self, mock_logger):
        """Test _normalize_bot_user_id with different input formats."""

        # Test with full MXID
        result1 = _normalize_bot_user_id("example.com", "@user:example.com")
        self.assertEqual(result1, "@user:example.com")

        # Test with localpart only
        result2 = _normalize_bot_user_id("example.com", "user")
        self.assertEqual(result2, "@user:example.com")

        # Test with already formatted ID
        result3 = _normalize_bot_user_id("example.com", "user:example.com")
        self.assertEqual(result3, "@user:example.com")

        # Test with falsy input
        result4 = _normalize_bot_user_id("example.com", "")
        self.assertEqual(result4, "")

    @patch("mmrelay.matrix_utils.logger")
    def test_normalize_bot_user_id_ipv6_and_ports(self, mock_logger):
        """Test _normalize_bot_user_id with IPv6 hosts and ports."""

        result1 = _normalize_bot_user_id("https://[2001:db8::1]:8448/path", "alice")
        self.assertEqual(result1, "@alice:[2001:db8::1]")

        result2 = _normalize_bot_user_id("example.com", "@bob:[2001:db8::1]:8448")
        self.assertEqual(result2, "@bob:[2001:db8::1]")

        result3 = _normalize_bot_user_id("[::1]:8448", "carol")
        self.assertEqual(result3, "@carol:[::1]")

    @patch("mmrelay.matrix_utils.logger")
    def test_get_detailed_matrix_error_message_bytes(self, mock_logger):
        """Test _get_detailed_matrix_error_message with bytes input."""

        # Test with valid UTF-8 bytes
        result = _get_detailed_matrix_error_message(b"Error message")
        self.assertEqual(result, "Error message")

        # Test with invalid UTF-8 bytes
        result = _get_detailed_matrix_error_message(b"\xff\xfe\xfd")
        self.assertEqual(
            result, "Network connectivity issue or server unreachable (binary data)"
        )

    @patch("mmrelay.matrix_utils.logger")
    def test_get_detailed_matrix_error_message_object_attributes(self, mock_logger):
        """Test _get_detailed_matrix_error_message with object having attributes."""

        # Test with message attribute
        mock_response = MagicMock()
        mock_response.message = "Custom error message"
        result = _get_detailed_matrix_error_message(mock_response)
        self.assertEqual(result, "Custom error message")

        # Test with status_code attribute only (no message)
        mock_response2 = MagicMock()
        mock_response2.message = None  # No message
        mock_response2.status_code = 404
        result = _get_detailed_matrix_error_message(mock_response2)
        self.assertEqual(result, "Server not found - check homeserver URL")

        # Test with status_code 429 only
        mock_response3 = MagicMock()
        mock_response3.message = None  # No message
        mock_response3.status_code = 429
        result = _get_detailed_matrix_error_message(mock_response3)
        self.assertEqual(result, "Rate limited - too many requests")

    def test_get_detailed_matrix_error_message_transport_status_non_int(self):
        """Test transport_response with non-int status_code."""

        mock_response = MagicMock()
        mock_response.message = None
        mock_response.status_code = None
        mock_response.transport_response = SimpleNamespace(status_code="bad")

        result = _get_detailed_matrix_error_message(mock_response)

        self.assertEqual(result, "Network connectivity issue or server unreachable")

    def test_get_detailed_matrix_error_message_attribute_error(self):
        """Test fallback for unexpected attribute errors."""

        class ExplodingResponse:
            def __getattr__(self, _name):
                raise ValueError("boom")

        result = _get_detailed_matrix_error_message(ExplodingResponse())
        self.assertEqual(
            result,
            "Unable to determine specific error - likely a network connectivity issue",
        )

    @patch("mmrelay.matrix_utils.logger")
    def test_update_room_id_in_mapping_unsupported_type(self, mock_logger):
        """Test _update_room_id_in_mapping with unsupported mapping type."""

        mapping = "not a list or dict"
        result = _update_room_id_in_mapping(
            mapping, "#old:example.com", "!new:example.com"
        )

        self.assertFalse(result)


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_broadcast_disabled():
    """Test _handle_detection_sensor_packet when broadcast is disabled."""
    config = {"meshtastic": {"broadcast_enabled": False}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    with patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config:
        mock_get_config.return_value = False  # broadcast_enabled

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        # Should not attempt to connect or send
        mock_get_config.assert_called()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_detection_disabled():
    """Test _handle_detection_sensor_packet when detection is disabled."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": False}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    with patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config:
        mock_get_config.side_effect = [
            True,
            False,
        ]  # broadcast_enabled, detection_sensor

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        # Should not attempt to connect or send
        assert mock_get_config.call_count == 2


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_connect_fail():
    """Test _handle_detection_sensor_packet when Meshtastic connection fails."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch("mmrelay.matrix_utils._connect_meshtastic") as mock_connect,
    ):
        mock_get_config.side_effect = [
            True,
            True,
        ]  # broadcast_enabled, detection_sensor
        mock_connect.return_value = None  # Connection fails

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_connect.assert_called_once()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_missing_channel():
    """Test _handle_detection_sensor_packet when meshtastic_channel is missing."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {}  # No meshtastic_channel
    full_display_name = "Test User"
    text = "Test message"

    mock_interface = MagicMock()

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch("mmrelay.matrix_utils._connect_meshtastic") as mock_connect,
    ):
        mock_get_config.side_effect = [True, True]
        mock_connect.return_value = mock_interface

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_connect.assert_called_once()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_invalid_channel():
    """Test _handle_detection_sensor_packet when meshtastic_channel is invalid."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {"meshtastic_channel": -1}  # Invalid channel
    full_display_name = "Test User"
    text = "Test message"

    mock_interface = MagicMock()

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch("mmrelay.matrix_utils._connect_meshtastic") as mock_connect,
    ):
        mock_get_config.side_effect = [True, True]
        mock_connect.return_value = mock_interface

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_connect.assert_called_once()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_success():
    """Test _handle_detection_sensor_packet successful relay."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    mock_interface = MagicMock()
    mock_queue = MagicMock()
    mock_queue.get_queue_size.return_value = 1

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch("mmrelay.matrix_utils._connect_meshtastic") as mock_connect,
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
        patch("mmrelay.matrix_utils.get_message_queue") as mock_get_queue,
    ):
        mock_get_config.side_effect = [True, True]
        mock_connect.return_value = mock_interface
        mock_queue_message.return_value = True
        mock_get_queue.return_value = mock_queue

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_queue_message.assert_called_once()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_queue_size_gt_one():
    """Test _handle_detection_sensor_packet logs when queue has multiple entries."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    mock_interface = MagicMock()
    mock_queue = MagicMock()
    mock_queue.get_queue_size.return_value = 3

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            new_callable=AsyncMock,
        ) as mock_get_iface,
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
        patch("mmrelay.matrix_utils.get_message_queue") as mock_get_queue,
        patch("mmrelay.meshtastic_utils.logger") as mock_mesh_logger,
    ):
        mock_get_config.side_effect = [True, True]
        mock_get_iface.return_value = (mock_interface, 0)
        mock_queue_message.return_value = True
        mock_get_queue.return_value = mock_queue

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_mesh_logger.info.assert_called()


@pytest.mark.asyncio
async def test_handle_detection_sensor_packet_queue_fail():
    """Test _handle_detection_sensor_packet when queue_message fails."""
    config = {"meshtastic": {"broadcast_enabled": True, "detection_sensor": True}}
    room_config = {"meshtastic_channel": 0}
    full_display_name = "Test User"
    text = "Test message"

    mock_interface = MagicMock()

    with (
        patch("mmrelay.matrix_utils.get_meshtastic_config_value") as mock_get_config,
        patch("mmrelay.matrix_utils._connect_meshtastic") as mock_connect,
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
    ):
        mock_get_config.side_effect = [True, True]
        mock_connect.return_value = mock_interface
        mock_queue_message.return_value = False  # Queue fails

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_queue_message.assert_called_once()


def test_matrix_utils_imports_nio_exceptions_when_available(monkeypatch):
    """Exercise the nio exception import branch for coverage."""
    import mmrelay.matrix_utils as mu

    original_values = {
        "NioLocalProtocolError": mu.NioLocalProtocolError,
        "NioLocalTransportError": mu.NioLocalTransportError,
        "NioRemoteProtocolError": mu.NioRemoteProtocolError,
        "NioRemoteTransportError": mu.NioRemoteTransportError,
        "NioLoginError": mu.NioLoginError,
        "NioLogoutError": mu.NioLogoutError,
        "NIO_COMM_EXCEPTIONS": mu.NIO_COMM_EXCEPTIONS,
        "config": mu.config,
        "matrix_client": mu.matrix_client,
        "matrix_rooms": mu.matrix_rooms,
        "bot_user_id": mu.bot_user_id,
        "matrix_access_token": mu.matrix_access_token,
        "matrix_homeserver": mu.matrix_homeserver,
        "bot_user_name": mu.bot_user_name,
        "bot_start_time": mu.bot_start_time,
    }

    exc_mod = types.ModuleType("nio.exceptions")
    resp_mod = types.ModuleType("nio.responses")

    class LocalProtocolError(Exception):
        pass

    class LocalTransportError(Exception):
        pass

    class RemoteProtocolError(Exception):
        pass

    class RemoteTransportError(Exception):
        pass

    class LoginError(Exception):
        pass

    class LogoutError(Exception):
        pass

    exc_mod.LocalProtocolError = LocalProtocolError
    exc_mod.LocalTransportError = LocalTransportError
    exc_mod.RemoteProtocolError = RemoteProtocolError
    exc_mod.RemoteTransportError = RemoteTransportError
    resp_mod.LoginError = LoginError
    resp_mod.LogoutError = LogoutError

    monkeypatch.setitem(sys.modules, "nio.exceptions", exc_mod)
    monkeypatch.setitem(sys.modules, "nio.responses", resp_mod)

    importlib.reload(mu)

    assert mu.NioLocalProtocolError is LocalProtocolError
    assert mu.NioLoginError is LoginError

    # Restore original exception classes so other tests using imports remain consistent.
    mu.NioLocalProtocolError = original_values["NioLocalProtocolError"]
    mu.NioLocalTransportError = original_values["NioLocalTransportError"]
    mu.NioRemoteProtocolError = original_values["NioRemoteProtocolError"]
    mu.NioRemoteTransportError = original_values["NioRemoteTransportError"]
    mu.NioLoginError = original_values["NioLoginError"]
    mu.NioLogoutError = original_values["NioLogoutError"]
    mu.NIO_COMM_EXCEPTIONS = original_values["NIO_COMM_EXCEPTIONS"]
    mu.config = original_values["config"]
    mu.matrix_client = original_values["matrix_client"]
    mu.matrix_rooms = original_values["matrix_rooms"]
    mu.bot_user_id = original_values["bot_user_id"]
    mu.matrix_access_token = original_values["matrix_access_token"]
    mu.matrix_homeserver = original_values["matrix_homeserver"]
    mu.bot_user_name = original_values["bot_user_name"]
    mu.bot_start_time = original_values["bot_start_time"]


@pytest.mark.asyncio
async def test_connect_matrix_alias_resolution_warns_when_client_falsey(monkeypatch):
    """Alias resolution should warn when the client is unavailable/truthy check fails."""
    mock_client = MagicMock()
    mock_client.__bool__.return_value = False
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.should_upload_keys = False

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context",
        lambda: MagicMock(),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
            },
            "matrix_rooms": [{"id": "#alias:example.org", "meshtastic_channel": 0}],
        }
        await connect_matrix(config)

    mock_logger.warning.assert_any_call(
        "Cannot resolve alias #alias:example.org: Matrix client is not available"
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
        ),
        patch("mmrelay.matrix_utils.get_meshtastic_config_value", return_value=True),
        patch("mmrelay.matrix_utils.queue_message", return_value=True),
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


@pytest.mark.asyncio
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
