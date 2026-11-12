"""Detection-sensor dispatch from Matrix events to the radio transport."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from mmrelay.matrix_utils import on_room_message
from tests.matrix_peer_fixtures import set_peer_content

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("reset_matrix_utils_globals"),
]


async def test_on_room_message_detection_sensor_enabled(
    mock_room, mock_event, test_config
):
    """
    Test that a detection sensor message is processed and queued with the correct port number when detection_sensor is enabled.

    The canonical peer portnum must be queued using the numeric protobuf port.
    """
    # Arrange - Set up event as detection sensor message
    mock_event.body = "Detection data"

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
    await set_peer_content(mock_event, mock_room, test_config)

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

    assert call_args.kwargs["portNum"] == 10
    assert (
        call_args.kwargs["description"] == "Detection sensor data from TestUser/Remote"
    )
    # The data should be raw text without prefix for detection sensor packets
    assert call_args.kwargs["data"] == b"Detection data"


async def test_on_room_message_detection_sensor_disabled(
    mock_room, mock_event, test_config
):
    """
    Test that a detection sensor message is ignored when detection_sensor is disabled in config.
    """
    # Arrange - Set up event as detection sensor message but disable detection sensor

    # Disable detection sensor in config
    test_config["meshtastic"]["detection_sensor"] = False
    test_config["meshtastic"]["broadcast_enabled"] = True

    # Act - Process the detection sensor message
    await set_peer_content(mock_event, mock_room, test_config)

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
    test_config["meshtastic"]["detection_sensor"] = True
    test_config["meshtastic"]["broadcast_enabled"] = False

    await set_peer_content(mock_event, mock_room, test_config)

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
    test_config["meshtastic"]["detection_sensor"] = True
    test_config["meshtastic"]["broadcast_enabled"] = True

    await set_peer_content(mock_event, mock_room, test_config)

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
