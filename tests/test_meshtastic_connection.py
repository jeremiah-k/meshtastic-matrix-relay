from __future__ import annotations

from concurrent.futures import Future
from typing import Callable
from unittest.mock import ANY, MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu


def _stop_retry_and_mark_shutdown(_seconds: float) -> None:
    mu.shutting_down = True


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

    def test_tcp_connection_returns_none_client_raises_connection_error(self):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        mu.config = {
            "meshtastic": {
                "connection_type": "tcp",
                "host": "192.168.1.1",
                "retries": 1,
            }
        }
        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = None
        mu.meshtastic_iface = None

        with (
            patch.object(
                mu.meshtastic.tcp_interface,
                "TCPInterface",
                return_value=None,
            ),
            patch.object(mu.time, "sleep"),
            patch.object(mu, "logger") as mock_logger,
        ):
            result = _connect_meshtastic_impl()
            assert result is None
            mock_logger.error.assert_any_call(
                "Meshtastic %s connection path completed without a client.",
                "tcp",
            )
            assert mu.meshtastic_client is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestTypedBleRetryHandling:
    def _configure_ble(self) -> str:
        ble_address = "AA:BB:CC:DD:EE:FF"
        mu.config = {
            "meshtastic": {
                "connection_type": "ble",
                "ble_address": ble_address,
                "retries": 1,
            }
        }
        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = None
        mu.meshtastic_iface = None
        return ble_address

    def test_address_mismatch_does_not_retry(self, monkeypatch):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        class FakeAddressMismatchError(Exception):
            pass

        self._configure_ble()
        monkeypatch.setattr(mu, "BLEAddressMismatchError", FakeAddressMismatchError)

        with (
            patch.object(
                mu.meshtastic.ble_interface,
                "BLEInterface",
                side_effect=FakeAddressMismatchError("expected AA got BB"),
            ),
            patch.object(
                mu,
                "_rollback_connect_attempt_state",
                wraps=mu._rollback_connect_attempt_state,
            ) as mock_rollback,
            patch.object(mu.time, "sleep") as mock_sleep,
        ):
            result = _connect_meshtastic_impl()

        assert result is None
        assert mock_rollback.called
        mock_sleep.assert_not_called()

    @pytest.mark.parametrize(
        "attr_name", ["BLEDiscoveryError", "BLEDeviceNotFoundError"]
    )
    def test_discovery_errors_retry_with_backoff(self, monkeypatch, attr_name):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        class FakeDiscoveryError(Exception):
            pass

        self._configure_ble()
        monkeypatch.setattr(mu, attr_name, FakeDiscoveryError)

        with (
            patch.object(
                mu.meshtastic.ble_interface,
                "BLEInterface",
                side_effect=FakeDiscoveryError("not found"),
            ),
            patch.object(
                mu,
                "_rollback_connect_attempt_state",
                wraps=mu._rollback_connect_attempt_state,
            ) as mock_rollback,
            patch.object(
                mu.time, "sleep", side_effect=_stop_retry_and_mark_shutdown
            ) as mock_sleep,
        ):
            result = _connect_meshtastic_impl()

        assert result is None
        assert mock_rollback.called
        assert mock_sleep.called

    def test_connection_timeout_error_uses_timeout_backoff(self, monkeypatch):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        class FakeConnectionTimeoutError(Exception):
            pass

        self._configure_ble()
        monkeypatch.setattr(mu, "BLEConnectionTimeoutError", FakeConnectionTimeoutError)

        with (
            patch.object(
                mu.meshtastic.ble_interface,
                "BLEInterface",
                side_effect=FakeConnectionTimeoutError("library timeout"),
            ),
            patch.object(
                mu,
                "_rollback_connect_attempt_state",
                wraps=mu._rollback_connect_attempt_state,
            ) as mock_rollback,
            patch.object(
                mu.time, "sleep", side_effect=_stop_retry_and_mark_shutdown
            ) as mock_sleep,
            patch.object(mu, "logger") as mock_logger,
        ):
            result = _connect_meshtastic_impl()

        assert result is None
        assert mock_rollback.called
        assert mock_sleep.called
        mock_logger.warning.assert_any_call("BLE library timeout: %s", ANY)

    def test_dbus_transport_error_retries_and_logs_diagnostics(self, monkeypatch):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        class FakeDBusTransportError(Exception):
            dbus_error_name = "org.bluez.Error.Failed"
            dbus_error_body = "busy"

            def __str__(self) -> str:
                return "normalized dbus message"

        self._configure_ble()
        monkeypatch.setattr(mu, "BLEDBusTransportError", FakeDBusTransportError)

        with (
            patch.object(
                mu.meshtastic.ble_interface,
                "BLEInterface",
                side_effect=FakeDBusTransportError(),
            ),
            patch.object(
                mu.time, "sleep", side_effect=_stop_retry_and_mark_shutdown
            ) as mock_sleep,
            patch.object(mu, "logger") as mock_logger,
        ):
            result = _connect_meshtastic_impl()

        assert result is None
        assert mock_sleep.called
        mock_logger.warning.assert_any_call(
            "BLE DBus transport error: %s",
            ANY,
        )
        mock_logger.debug.assert_any_call(
            "BLE DBus diagnostics name=%r body=%r",
            "org.bluez.Error.Failed",
            "busy",
        )

    def test_connection_suppressed_error_resets_gate_and_retries(self, monkeypatch):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        class FakeConnectionSuppressedError(Exception):
            pass

        self._configure_ble()
        monkeypatch.setattr(
            mu,
            "BLEConnectionSuppressedError",
            FakeConnectionSuppressedError,
        )

        with (
            patch.object(
                mu.meshtastic.ble_interface,
                "BLEInterface",
                side_effect=FakeConnectionSuppressedError("suppressed"),
            ),
            patch.object(
                mu, "_reset_ble_connection_gate_state", return_value=True
            ) as mock_reset,
            patch.object(
                mu.time, "sleep", side_effect=_stop_retry_and_mark_shutdown
            ) as mock_sleep,
        ):
            result = _connect_meshtastic_impl()

        assert result is None
        mock_reset.assert_called_once()
        assert mock_sleep.called

    def test_modern_mode_skips_preconnect_cleanup(self, monkeypatch):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        class FakeMeshtasticBLEError(Exception):
            pass

        self._configure_ble()
        monkeypatch.setattr(mu, "MeshtasticBLEError", FakeMeshtasticBLEError)

        with (
            patch.object(
                mu.meshtastic.ble_interface,
                "BLEInterface",
                side_effect=RuntimeError("creation failed"),
            ),
            patch.object(mu, "_disconnect_ble_by_address") as mock_disconnect,
            patch.object(mu.time, "sleep", side_effect=_stop_retry_and_mark_shutdown),
        ):
            result = _connect_meshtastic_impl()

        assert result is None
        mock_disconnect.assert_not_called()

    def test_legacy_mode_keeps_preconnect_cleanup(self, monkeypatch):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        self._configure_ble()
        for attr_name in (
            "MeshtasticBLEError",
            "BLEDiscoveryError",
            "BLEConnectionTimeoutError",
            "BLEConnectionSuppressedError",
            "BLEAddressMismatchError",
            "BLEDBusTransportError",
        ):
            monkeypatch.setattr(mu, attr_name, None)

        with (
            patch.object(
                mu.meshtastic.ble_interface,
                "BLEInterface",
                side_effect=RuntimeError("creation failed"),
            ),
            patch.object(mu, "_disconnect_ble_by_address") as mock_disconnect,
            patch.object(mu.time, "sleep", side_effect=_stop_retry_and_mark_shutdown),
        ):
            result = _connect_meshtastic_impl()

        assert result is None
        mock_disconnect.assert_called_once()

    def test_auto_reconnect_only_still_calls_preconnect_cleanup(self, monkeypatch):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        class FakeAutoReconnectBLE:
            def __init__(
                self,
                *,
                address: str,
                noProto: bool,
                debugOut: object,
                noNodes: bool,
                timeout: int,
                auto_reconnect: bool = True,
            ) -> None:
                raise RuntimeError("creation failed")

        self._configure_ble()
        for attr_name in (
            "MeshtasticBLEError",
            "BLEDiscoveryError",
            "BLEConnectionTimeoutError",
            "BLEConnectionSuppressedError",
            "BLEAddressMismatchError",
            "BLEDBusTransportError",
        ):
            monkeypatch.setattr(mu, attr_name, None)

        with (
            patch.object(
                mu.meshtastic.ble_interface,
                "BLEInterface",
                FakeAutoReconnectBLE,
            ),
            patch.object(mu, "_disconnect_ble_by_address") as mock_disconnect,
            patch.object(mu.time, "sleep", side_effect=_stop_retry_and_mark_shutdown),
        ):
            result = _connect_meshtastic_impl()

        assert result is None
        mock_disconnect.assert_called_once()


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestBleTeardownBarrier:
    def test_blocks_fresh_creation_when_teardown_unresolved(self):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        ble_address = "AA:BB:CC:DD:EE:FF"
        address_key = mu._sanitize_ble_address(ble_address)
        mu._ble_generation_by_address[address_key] = 7
        mu._ble_teardown_unresolved_by_generation[(address_key, 7)] = 1
        mu.config = {
            "meshtastic": {
                "connection_type": "ble",
                "ble_address": ble_address,
                "retries": 1,
            }
        }
        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = None
        mu.meshtastic_iface = None

        with (
            patch.object(mu, "_disconnect_ble_by_address"),
            patch.object(mu.time, "sleep", side_effect=_stop_retry_and_mark_shutdown),
            patch.object(mu.meshtastic.ble_interface, "BLEInterface") as mock_ble_ctor,
        ):
            result = _connect_meshtastic_impl()

        assert result is None
        mock_ble_ctor.assert_not_called()

    def test_allows_fresh_creation_after_teardown_resolves(self):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        ble_address = "11:22:33:44:55:66"
        mu.config = {
            "meshtastic": {
                "connection_type": "ble",
                "ble_address": ble_address,
                "retries": 1,
            }
        }
        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = None
        mu.meshtastic_iface = None

        with (
            patch.object(mu, "_disconnect_ble_by_address"),
            patch.object(mu.time, "sleep", side_effect=_stop_retry_and_mark_shutdown),
            patch.object(
                mu.meshtastic.ble_interface,
                "BLEInterface",
                side_effect=RuntimeError("creation failed"),
            ) as mock_ble_ctor,
        ):
            result = _connect_meshtastic_impl()

        assert result is None
        assert mock_ble_ctor.call_count >= 1

    def test_reconnect_proceeds_after_late_worker_resolution(self):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        ble_address = "22:33:44:55:66:77"
        address_key = mu._sanitize_ble_address(ble_address)
        mu._ble_generation_by_address[address_key] = 3
        mu._ble_teardown_unresolved_by_generation[(address_key, 3)] = 1
        remaining, stale = mu._resolve_ble_teardown_timeout(ble_address, 3)
        assert remaining == 0
        assert stale is False

        mu.config = {
            "meshtastic": {
                "connection_type": "ble",
                "ble_address": ble_address,
                "retries": 1,
            }
        }
        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = None
        mu.meshtastic_iface = None

        with (
            patch.object(mu, "_disconnect_ble_by_address"),
            patch.object(mu.time, "sleep", side_effect=_stop_retry_and_mark_shutdown),
            patch.object(
                mu.meshtastic.ble_interface,
                "BLEInterface",
                side_effect=RuntimeError("creation failed"),
            ) as mock_ble_ctor,
        ):
            result = _connect_meshtastic_impl()

        assert result is None
        assert mock_ble_ctor.call_count >= 1

    def test_late_barrier_after_iface_creation_rolls_back_published_iface(self):
        from mmrelay.meshtastic.connection import _connect_meshtastic_impl

        class BleInterfaceWithConnect:
            def __init__(  # noqa: PLR0913
                self,
                *,
                address: str,
                noProto: bool,  # noqa: N803
                debugOut: object,  # noqa: N803
                noNodes: bool,  # noqa: N803
                timeout: int,
                auto_reconnect: bool = True,
            ) -> None:
                _ = (noProto, debugOut, noNodes, timeout)
                self.address = address
                self.client = object()
                self.auto_reconnect = auto_reconnect
                self.connect = MagicMock()

        ble_address = "44:55:66:77:88:99"
        mu.config = {
            "meshtastic": {
                "connection_type": "ble",
                "ble_address": ble_address,
                "retries": 1,
            }
        }
        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = None
        mu.meshtastic_iface = None

        unresolved_calls = 0

        def _unresolved_teardown_for_late_barrier(
            _address: str,
        ) -> list[tuple[int, int]]:
            nonlocal unresolved_calls
            unresolved_calls += 1
            if unresolved_calls == 1:
                # Allow interface creation pre-check.
                return []
            # Block at the post-creation barrier before connect().
            return [(1, 1)]

        def _sync_submit(
            fn: Callable[..., object], *args: object, **kwargs: object
        ) -> Future[object]:
            future: Future[object] = Future()
            try:
                future.set_result(fn(*args, **kwargs))
            except Exception as exc:  # noqa: BLE001 - test harness helper
                future.set_exception(exc)
            return future

        mock_executor = MagicMock()
        mock_executor.submit.side_effect = _sync_submit

        with (
            patch.object(
                mu, "_get_ble_unresolved_teardown_generations"
            ) as mock_unresolved,
            patch.object(mu, "_disconnect_ble_by_address"),
            patch.object(mu, "_get_ble_executor", return_value=mock_executor),
            patch.object(mu, "_disconnect_ble_interface") as mock_disconnect_iface,
            patch.object(mu.time, "sleep", side_effect=_stop_retry_and_mark_shutdown),
            patch.object(
                mu.meshtastic.ble_interface,
                "BLEInterface",
                BleInterfaceWithConnect,
            ),
        ):
            mock_unresolved.side_effect = _unresolved_teardown_for_late_barrier
            result = _connect_meshtastic_impl()

        assert result is None
        assert unresolved_calls >= 2
        assert mock_disconnect_iface.call_count == 1
        disconnected_iface = mock_disconnect_iface.call_args.args[0]
        assert disconnected_iface is not None
        assert disconnected_iface.address == ble_address
        assert (
            mock_disconnect_iface.call_args.kwargs["reason"] == "connect setup failed"
        )
        assert disconnected_iface.connect.call_count == 0
        assert mu.meshtastic_iface is None
        assert mu.meshtastic_client is None
