from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGetConnectionRetryWaitTime:
    def test_zero_attempts(self):
        from mmrelay.meshtastic.connection import _get_connection_retry_wait_time

        result = _get_connection_retry_wait_time(0)
        assert result == 0.0

    def test_negative_attempts(self):
        from mmrelay.meshtastic.connection import _get_connection_retry_wait_time

        result = _get_connection_retry_wait_time(-1)
        assert result == 0.0

    def test_positive_attempts(self):
        from mmrelay.meshtastic.connection import _get_connection_retry_wait_time

        result = _get_connection_retry_wait_time(2)
        assert result > 0


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestSerialPortExists:
    def test_existing_port(self):
        from mmrelay.meshtastic.connection import serial_port_exists

        mock_port = MagicMock()
        mock_port.device = "/dev/ttyUSB0"
        with patch.object(
            mu.serial.tools.list_ports, "comports", return_value=[mock_port]
        ):
            assert serial_port_exists("/dev/ttyUSB0") is True

    def test_non_existing_port(self):
        from mmrelay.meshtastic.connection import serial_port_exists

        with patch.object(mu.serial.tools.list_ports, "comports", return_value=[]):
            assert serial_port_exists("/dev/ttyUSB0") is False


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGetConnectTimeProbeSettings:
    def test_none_config_returns_defaults(self):
        from mmrelay.meshtastic.connection import _get_connect_time_probe_settings

        enabled, timeout = _get_connect_time_probe_settings(None, "tcp")
        assert isinstance(enabled, bool)
        assert isinstance(timeout, float)

    def test_non_dict_config_returns_defaults(self):
        from mmrelay.meshtastic.connection import _get_connect_time_probe_settings

        enabled, timeout = _get_connect_time_probe_settings("not_a_dict", "tcp")
        assert isinstance(enabled, bool)

    def test_valid_config(self):
        from mmrelay.meshtastic.connection import _get_connect_time_probe_settings

        config = {
            "meshtastic": {
                "health_check": {
                    "enabled": True,
                    "connect_probe_enabled": True,
                    "probe_timeout": 30.0,
                }
            }
        }
        enabled, timeout = _get_connect_time_probe_settings(config, "tcp")
        assert enabled is True
        assert timeout == 30.0


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestScheduleConnectTimeCalibrationProbe:
    def test_disabled_probe(self):
        from mmrelay.meshtastic.connection import (
            _schedule_connect_time_calibration_probe,
        )

        config = {
            "meshtastic": {
                "health_check": {
                    "enabled": False,
                    "connect_probe_enabled": False,
                }
            }
        }
        client = MagicMock()
        client.localNode = MagicMock()
        _schedule_connect_time_calibration_probe(
            client, connection_type="tcp", active_config=config
        )

    def test_no_local_node(self):
        from mmrelay.meshtastic.connection import (
            _schedule_connect_time_calibration_probe,
        )

        config = {
            "meshtastic": {
                "health_check": {"enabled": True, "connect_probe_enabled": True}
            }
        }
        client = MagicMock()
        client.localNode = None
        _schedule_connect_time_calibration_probe(
            client, connection_type="tcp", active_config=config
        )

    def test_degraded_executor_skips(self):
        from mmrelay.meshtastic.connection import (
            _schedule_connect_time_calibration_probe,
        )

        config = {
            "meshtastic": {
                "health_check": {"enabled": True, "connect_probe_enabled": True}
            }
        }
        client = MagicMock()
        client.localNode = MagicMock()
        client.sendData = MagicMock()

        with patch.object(
            mu,
            "_submit_metadata_probe",
            side_effect=mu.MetadataExecutorDegradedError("degraded"),
        ):
            _schedule_connect_time_calibration_probe(
                client, connection_type="tcp", active_config=config
            )


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestRollbackConnectAttemptState:
    def test_rollback_with_none_client(self):
        from mmrelay.meshtastic.connection import _rollback_connect_attempt_state

        result = _rollback_connect_attempt_state(
            client=None,
            client_assigned_for_this_connect=False,
            startup_drain_armed_for_this_connect=False,
            startup_drain_applied_for_this_connect=False,
            reconnect_bootstrap_armed_for_this_connect=False,
        )
        assert result is False

    def test_rollback_with_assigned_client(self):
        from mmrelay.meshtastic.connection import _rollback_connect_attempt_state

        mock_client = MagicMock()
        mu.meshtastic_client = mock_client
        mu.meshtastic_iface = None
        mu._relay_active_client_id = id(mock_client)

        result = _rollback_connect_attempt_state(
            client=mock_client,
            client_assigned_for_this_connect=True,
            startup_drain_armed_for_this_connect=False,
            startup_drain_applied_for_this_connect=False,
            reconnect_bootstrap_armed_for_this_connect=False,
        )
        assert result is False
        assert mu.meshtastic_client is None

    def test_rollback_drain_state(self):
        from mmrelay.meshtastic.connection import _rollback_connect_attempt_state

        mock_timer = MagicMock()
        mu._relay_startup_drain_expiry_timer = mock_timer
        mu._relay_startup_drain_deadline_monotonic_secs = 999.0
        mu._startup_packet_drain_applied = True
        mu._relay_startup_drain_complete_event = MagicMock()

        result = _rollback_connect_attempt_state(
            client=None,
            client_assigned_for_this_connect=False,
            startup_drain_armed_for_this_connect=True,
            startup_drain_applied_for_this_connect=True,
            reconnect_bootstrap_armed_for_this_connect=False,
        )
        assert result is False
        assert mu._relay_startup_drain_deadline_monotonic_secs is None

    def test_rollback_reconnect_bootstrap(self):
        from mmrelay.meshtastic.connection import _rollback_connect_attempt_state

        mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = 999.0

        result = _rollback_connect_attempt_state(
            client=None,
            client_assigned_for_this_connect=False,
            startup_drain_armed_for_this_connect=False,
            startup_drain_applied_for_this_connect=False,
            reconnect_bootstrap_armed_for_this_connect=True,
        )
        assert result is False
        assert mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestConnectMeshtastic:
    def test_shutdown_returns_none(self):
        from mmrelay.meshtastic.connection import connect_meshtastic

        mu.shutting_down = True
        result = connect_meshtastic()
        assert result is None
        mu.shutting_down = False

    def test_reconnecting_returns_none(self):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        mu.shutting_down = False
        mu.reconnecting = True
        result = _connect_meshtastic_impl()
        assert result is None
        mu.reconnecting = False

    def test_no_config_returns_none(self):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        mu.config = None
        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = None
        result = _connect_meshtastic_impl()
        assert result is None

    def test_no_meshtastic_section(self):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        mu.config = {}
        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = None
        result = _connect_meshtastic_impl()
        assert result is None

    def test_no_connection_type(self):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        mu.config = {"meshtastic": {}}
        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = None
        result = _connect_meshtastic_impl()
        assert result is None

    def test_unknown_connection_type(self):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        mu.config = {"meshtastic": {"connection_type": "unknown"}}
        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = None
        result = _connect_meshtastic_impl()
        assert result is None

    def test_existing_client_returned(self):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        existing = MagicMock()
        mu.config = {"meshtastic": {"connection_type": "tcp"}}
        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = existing
        mu.meshtastic_iface = None
        result = _connect_meshtastic_impl()
        assert result is existing
