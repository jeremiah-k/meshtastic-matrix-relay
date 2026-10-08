"""Tests for MESHTASTIC_READY_TOPIC publication during connection setup.

Covers the shutdown race around the readiness publish that follows a
completed connection attempt. The happy path lives in
test_meshtastic_utils_connect.py.
"""

from unittest.mock import MagicMock, patch

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.network import CONNECTION_TYPE_SERIAL
from mmrelay.meshtastic_utils import connect_meshtastic


def test_connect_meshtastic_skips_ready_publish_when_shutdown_starts_during_setup(
    reset_meshtastic_globals,
):
    """A shutdown racing the setup window must not publish plugin readiness."""
    mock_client = MagicMock()
    mock_client.getMyNodeInfo.return_value = {
        "user": {"shortName": "test", "hwModel": "test"}
    }
    config = {
        "meshtastic": {
            "connection_type": CONNECTION_TYPE_SERIAL,
            "serial_port": "/dev/ttyUSB0",
            "retries": 1,
        }
    }
    original_shutdown = mu.shutting_down

    def _shutdown_during_setup(*_args, **_kwargs):
        mu.shutting_down = True

    try:
        with (
            patch("mmrelay.meshtastic_utils.serial_port_exists", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface",
                return_value=mock_client,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "2.8.1", "success": True},
            ),
            patch(
                "mmrelay.meshtastic_utils._schedule_connect_time_calibration_probe",
                side_effect=_shutdown_during_setup,
            ),
            patch("mmrelay.meshtastic_utils.pub.sendMessage") as send_message,
        ):
            result = connect_meshtastic(passed_config=config)
    finally:
        mu.shutting_down = original_shutdown

    assert result is mock_client
    send_message.assert_not_called()
