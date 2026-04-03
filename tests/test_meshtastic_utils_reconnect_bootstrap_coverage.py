import time
from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.network import CONNECTION_TYPE_TCP
from mmrelay.meshtastic_utils import connect_meshtastic


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestConnectionRefusedReconnectBootstrapCleanup:
    def test_connection_refused_reconnect_bootstrap_cleared(self):
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "Node", "hwModel": "HW"}
        }
        now_mono = 1_000.0

        mu._startup_packet_drain_applied = True
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = True

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                return_value=mock_client,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            patch(
                "mmrelay.meshtastic_utils._schedule_connect_time_calibration_probe",
                side_effect=ConnectionRefusedError("test refused"),
            ),
            patch("mmrelay.meshtastic_utils.logger"),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=now_mono),
        ):
            config = {
                "meshtastic": {
                    "connection_type": CONNECTION_TYPE_TCP,
                    "host": "127.0.0.1",
                }
            }
            result = connect_meshtastic(passed_config=config)

        assert result is None
        assert mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs is None
        assert mu.meshtastic_client is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestTimeoutReconnectBootstrapCleanup:
    def test_timeout_reconnect_bootstrap_cleared(self):
        first_client = MagicMock()
        first_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "Node", "hwModel": "HW"}
        }

        mu._startup_packet_drain_applied = True
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = True

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                side_effect=[first_client, TimeoutError("retry timeout")],
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            patch(
                "mmrelay.meshtastic_utils._schedule_connect_time_calibration_probe",
                side_effect=TimeoutError("probe timeout"),
            ),
            patch("mmrelay.meshtastic_utils.logger"),
            patch("mmrelay.meshtastic_utils.time.sleep"),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_000.0),
        ):
            config = {
                "meshtastic": {
                    "connection_type": CONNECTION_TYPE_TCP,
                    "host": "127.0.0.1",
                    "retries": 1,
                }
            }
            result = connect_meshtastic(passed_config=config)

        assert result is None
        assert mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGenericExceptionReconnectBootstrapCleanup:
    def test_generic_exception_reconnect_bootstrap_cleared(self):
        first_client = MagicMock()
        first_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "Node", "hwModel": "HW"}
        }

        mu._startup_packet_drain_applied = True
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = True

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                side_effect=[first_client, RuntimeError("retry boom")],
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            patch(
                "mmrelay.meshtastic_utils._schedule_connect_time_calibration_probe",
                side_effect=RuntimeError("probe boom"),
            ),
            patch("mmrelay.meshtastic_utils.logger"),
            patch("mmrelay.meshtastic_utils.time.sleep"),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_000.0),
        ):
            config = {
                "meshtastic": {
                    "connection_type": CONNECTION_TYPE_TCP,
                    "host": "127.0.0.1",
                    "retries": 1,
                }
            }
            result = connect_meshtastic(passed_config=config)

        assert result is None
        assert mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs is None
