from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.matrix_utils import _handle_detection_sensor_packet


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
        patch(
            "mmrelay.matrix_utils._connect_meshtastic", new_callable=AsyncMock
        ) as mock_connect,
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
        patch(
            "mmrelay.matrix_utils._connect_meshtastic", new_callable=AsyncMock
        ) as mock_connect,
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
        patch(
            "mmrelay.matrix_utils._connect_meshtastic", new_callable=AsyncMock
        ) as mock_connect,
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
        patch(
            "mmrelay.matrix_utils._connect_meshtastic", new_callable=AsyncMock
        ) as mock_connect,
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
        patch(
            "mmrelay.matrix_utils._connect_meshtastic", new_callable=AsyncMock
        ) as mock_connect,
        patch("mmrelay.matrix_utils.queue_message") as mock_queue_message,
    ):
        mock_get_config.side_effect = [True, True]
        mock_connect.return_value = mock_interface
        mock_queue_message.return_value = False  # Queue fails

        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )

        mock_queue_message.assert_called_once()
