import time
from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.network import (
    CONNECTION_TYPE_TCP,
    INFINITE_RETRIES,
    MAX_TIMEOUT_RETRIES_INFINITE,
)
from mmrelay.meshtastic_utils import connect_meshtastic


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestSeedConnectTimeSkewExpiredDeadline:
    def test_expired_reconnect_bootstrap_deadline_is_cleared(self):
        now_wall = 100_000.0
        now_mono = 1_000.0

        mu._relay_rx_time_clock_skew_secs = None
        mu._relay_connection_started_monotonic_secs = now_mono - 5.0
        mu._relay_startup_drain_deadline_monotonic_secs = None
        mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = now_mono - 10.0
        mu.RELAY_START_TIME = now_wall - 100.0
        rx_time = now_wall - 50.0

        with (
            patch("mmrelay.meshtastic_utils.time.time", return_value=now_wall),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=now_mono),
        ):
            result = mu._seed_connect_time_skew(rx_time)

        assert result is True
        assert mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs is None
        assert mu._relay_rx_time_clock_skew_secs == now_wall - rx_time


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestConnectMeshtasticDrainArming:
    def test_arms_startup_drain_on_first_connect(self):
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "Node", "hwModel": "HW"}
        }
        now_mono = 1_000.0

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                return_value=mock_client,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
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

        assert result is mock_client
        assert mu._startup_packet_drain_applied is True
        assert mu._relay_startup_drain_deadline_monotonic_secs == pytest.approx(
            now_mono + mu._STARTUP_PACKET_DRAIN_SECS
        )


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestConnectionRefusedExceptionHandler:
    def test_connection_refused_after_drain_armed_cleans_up(self):
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "Node", "hwModel": "HW"}
        }
        now_mono = 1_000.0

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
        assert mu._relay_startup_drain_deadline_monotonic_secs is None
        assert mu._startup_packet_drain_applied is False
        assert mu.meshtastic_client is None
        assert mu._relay_active_client_id is None
        mock_client.close.assert_called_once()


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestTimeoutExceptionHandler:
    def test_timeout_after_drain_armed_clears_state(self):
        first_client = MagicMock()
        first_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "Node", "hwModel": "HW"}
        }

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
        assert mu._relay_startup_drain_deadline_monotonic_secs is None
        assert mu._startup_packet_drain_applied is False
        first_client.close.assert_called_once()

    def test_timeout_breaks_on_shutdown(self):
        def _timeout_then_shutdown(*_args, **_kwargs):
            mu.shutting_down = True
            raise TimeoutError("timeout")

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                side_effect=_timeout_then_shutdown,
            ),
            patch("mmrelay.meshtastic_utils.time.sleep") as mock_sleep,
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
        mock_sleep.assert_not_called()


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGenericExceptionHandler:
    def test_generic_exception_after_drain_armed_cleans_up(self):
        first_client = MagicMock()
        first_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "Node", "hwModel": "HW"}
        }

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
        assert mu._relay_startup_drain_deadline_monotonic_secs is None
        assert mu._startup_packet_drain_applied is False
        first_client.close.assert_called_once()
