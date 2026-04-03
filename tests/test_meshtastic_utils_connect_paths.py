import time
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import serial

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.network import (
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_NETWORK,
    CONNECTION_TYPE_SERIAL,
    CONNECTION_TYPE_TCP,
    DEFAULT_MESHTASTIC_OPERATION_TIMEOUT,
    DEFAULT_MESHTASTIC_TIMEOUT,
    DEFAULT_TCP_PORT,
    ERRNO_BAD_FILE_DESCRIPTOR,
)
from mmrelay.meshtastic_utils import connect_meshtastic, on_lost_meshtastic_connection


def test_connect_meshtastic_returns_existing_client(reset_meshtastic_globals):
    mock_client = MagicMock()
    mu.meshtastic_client = mock_client

    config = {
        "meshtastic": {"connection_type": CONNECTION_TYPE_TCP, "host": "127.0.0.1"}
    }

    with patch(
        "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface"
    ) as mock_tcp:
        result = connect_meshtastic(passed_config=config)

    assert result is mock_client
    mock_tcp.assert_not_called()


def test_connect_meshtastic_network_alias_warns_and_uses_tcp(reset_meshtastic_globals):
    mock_client = MagicMock()
    mock_client.getMyNodeInfo.return_value = {
        "user": {"shortName": "Node", "hwModel": "HW"}
    }

    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
            return_value=mock_client,
        ) as mock_tcp,
        patch(
            "mmrelay.meshtastic_utils._get_device_metadata",
            return_value={"firmware_version": "unknown", "success": False},
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_NETWORK,
                "host": "127.0.0.1",
            }
        }
        result = connect_meshtastic(passed_config=config)

    assert result is mock_client
    mock_tcp.assert_called_once_with(
        hostname="127.0.0.1",
        portNumber=DEFAULT_TCP_PORT,
        timeout=DEFAULT_MESHTASTIC_TIMEOUT,
    )
    mock_logger.warning.assert_any_call(
        "Using 'network' connection type (legacy). 'tcp' is now the preferred name and 'network' will be deprecated in a future version."
    )


def test_connect_meshtastic_retry_limit_deprecated_warning(reset_meshtastic_globals):
    config = {"meshtastic": {"connection_type": CONNECTION_TYPE_TCP, "retry_limit": 1}}

    with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
        result = connect_meshtastic(passed_config=config)

    assert result is None
    mock_logger.warning.assert_any_call(
        "'retry_limit' is deprecated in meshtastic config; use 'retries' instead"
    )


def test_connect_meshtastic_invalid_retries_falls_back(reset_meshtastic_globals):
    config = {
        "meshtastic": {
            "connection_type": CONNECTION_TYPE_TCP,
            "host": "127.0.0.1",
            "retries": "bad",
        }
    }

    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
            side_effect=ConnectionRefusedError("nope"),
        ),
        patch("mmrelay.meshtastic_utils.logger"),
    ):
        result = connect_meshtastic(passed_config=config)

    assert result is None


def test_connect_meshtastic_serial_missing_port_returns_none(
    reset_meshtastic_globals,
):
    config = {"meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL}}

    with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
        result = connect_meshtastic(passed_config=config)

    assert result is None
    mock_logger.error.assert_any_call(
        "No serial port specified in Meshtastic configuration."
    )


@patch("mmrelay.meshtastic_utils.serial_port_exists", return_value=False)
@patch("mmrelay.meshtastic_utils.time.sleep")
def test_connect_meshtastic_serial_port_not_found_retries(
    mock_sleep, mock_exists, reset_meshtastic_globals
):
    config = {
        "meshtastic": {
            "connection_type": CONNECTION_TYPE_SERIAL,
            "serial_port": "/dev/ttyUSB0",
            "retries": 1,
        }
    }

    result = connect_meshtastic(passed_config=config)

    assert result is None
    assert mock_sleep.call_count == 1


def test_connect_meshtastic_ble_missing_address_returns_none(
    reset_meshtastic_globals,
):
    config = {"meshtastic": {"connection_type": CONNECTION_TYPE_BLE}}

    with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
        result = connect_meshtastic(passed_config=config)

    assert result is None
    mock_logger.error.assert_called_with("No BLE address provided.")


def test_connect_meshtastic_ble_recovers_from_stale_worker(
    reset_meshtastic_globals,
):
    """Stale in-flight BLE worker futures should be reset so retries can proceed."""
    ble_address = "AA:BB:CC:DD:EE:FF"
    config = {
        "meshtastic": {
            "connection_type": CONNECTION_TYPE_BLE,
            "ble_address": ble_address,
            "retries": 1,
        }
    }

    stale_future = MagicMock()
    stale_future.done.return_value = False
    stale_future.cancel.return_value = True

    class _FakeBLEInterface:
        def __init__(self, **kwargs: object) -> None:
            self.address = kwargs.get("address")
            self.auto_reconnect = kwargs.get("auto_reconnect")

        def connect(self) -> None:
            return None

        def getMyNodeInfo(self) -> dict[str, dict[str, str]]:
            return {"user": {"shortName": "Node", "hwModel": "HW"}}

    mu._ble_future = stale_future
    mu._ble_future_address = ble_address
    mu._ble_future_started_at = time.monotonic() - 60.0
    mu._ble_future_timeout_secs = 1.0

    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
            new=_FakeBLEInterface,
        ),
        patch(
            "mmrelay.meshtastic_utils._ble_gate_reset_callable",
        ),
        patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
        patch(
            "mmrelay.meshtastic_utils._validate_ble_connection_address",
            return_value=True,
        ),
        patch(
            "mmrelay.meshtastic_utils._get_device_metadata",
            return_value={"firmware_version": "unknown", "success": False},
        ),
        patch("mmrelay.meshtastic_utils.pub.subscribe"),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        result = connect_meshtastic(passed_config=config)

    assert result is not None
    assert result is mu.meshtastic_client
    assert stale_future.cancel.called
    stale_warning_calls = [
        call
        for call in mock_logger.warning.call_args_list
        if call.args
        and "BLE worker appears stale during %s for %s" in str(call.args[0])
        and len(call.args) >= 3
        and call.args[2] == ble_address
    ]
    assert stale_warning_calls, "Expected stale BLE worker recovery warning"
    assert stale_warning_calls[0].args[1] in {"interface creation", "connect"}
    # Verify gate seam was invoked through the real helper (observable via warning)
    gate_reset_calls = [
        call
        for call in mock_logger.warning.call_args_list
        if call.args
        and "Reset BLE connection state for %s" in str(call.args[0])
        and len(call.args) >= 2
        and call.args[1] == ble_address
    ]
    assert gate_reset_calls, "Expected BLE connection state reset warning"


def test_connect_meshtastic_duplicate_suppression_clears_fork_gates(
    reset_meshtastic_globals,
):
    ble_address = "AA:BB:CC:DD:EE:FF"
    config = {
        "meshtastic": {
            "connection_type": CONNECTION_TYPE_BLE,
            "ble_address": ble_address,
            "retries": 1,
        }
    }

    class _SuppressedBLEInterface:
        def __init__(self, **_kwargs: object) -> None:
            raise RuntimeError("Connection suppressed: recently connected elsewhere")

    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
            new=_SuppressedBLEInterface,
        ),
        patch(
            "mmrelay.meshtastic_utils._ble_gate_reset_callable",
        ),
        patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
        patch("mmrelay.meshtastic_utils.time.sleep"),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        result = connect_meshtastic(passed_config=config)

    assert result is None
    # Verify gate seam was invoked through the real helper (observable via warning)
    gate_reset_calls = [
        call
        for call in mock_logger.warning.call_args_list
        if call.args
        and "Reset BLE connection state for %s" in str(call.args[0])
        and len(call.args) >= 2
        and call.args[1] == ble_address
    ]
    assert gate_reset_calls, "Expected BLE connection state reset warning"
    assert any(
        call.args and "Detected duplicate BLE connect suppression" in str(call.args[0])
        for call in mock_logger.warning.call_args_list
    )


def test_connect_meshtastic_tcp_missing_host_returns_none(
    reset_meshtastic_globals,
):
    config = {"meshtastic": {"connection_type": CONNECTION_TYPE_TCP}}

    with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
        result = connect_meshtastic(passed_config=config)

    assert result is None
    mock_logger.error.assert_any_call(
        "No host specified in Meshtastic configuration for TCP connection."
    )


def test_connect_meshtastic_tcp_invalid_port_uses_default(reset_meshtastic_globals):
    mock_client = MagicMock()
    mock_client.getMyNodeInfo.return_value = {
        "user": {"shortName": "Node", "hwModel": "HW"}
    }

    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
            return_value=mock_client,
        ) as mock_tcp,
        patch(
            "mmrelay.meshtastic_utils._get_device_metadata",
            return_value={"firmware_version": "unknown", "success": False},
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_TCP,
                "host": "127.0.0.1",
                "port": 70000,
            }
        }
        result = connect_meshtastic(passed_config=config)

    assert result is mock_client
    mock_tcp.assert_called_once_with(
        hostname="127.0.0.1",
        portNumber=DEFAULT_TCP_PORT,
        timeout=DEFAULT_MESHTASTIC_TIMEOUT,
    )
    mock_logger.warning.assert_any_call(
        "Invalid meshtastic.port value %r; using default TCP port %s",
        70000,
        DEFAULT_TCP_PORT,
    )


def test_connect_meshtastic_logs_firmware_version_on_success(
    reset_meshtastic_globals,
):
    mock_client = MagicMock()
    mock_client.getMyNodeInfo.return_value = {
        "user": {"shortName": "Node", "hwModel": "HW"}
    }

    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
            return_value=mock_client,
        ),
        patch(
            "mmrelay.meshtastic_utils._get_device_metadata",
            return_value={"firmware_version": "1.2.3", "success": True},
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        config = {
            "meshtastic": {"connection_type": CONNECTION_TYPE_TCP, "host": "127.0.0.1"}
        }
        result = connect_meshtastic(passed_config=config)

    assert result is mock_client
    mock_logger.info.assert_any_call(
        "Connected to Node / HW / Meshtastic Firmware version 1.2.3"
    )


def test_connect_meshtastic_bootstraps_skew_for_fast_receive_during_subscribe(
    reset_meshtastic_globals,
):
    """Fast inbound packets during receive subscribe should still seed startup skew."""
    mock_client = MagicMock()
    mock_client.getMyNodeInfo.return_value = {
        "user": {"shortName": "Node", "hwModel": "HW"}
    }
    startup_packet = {
        "from": 123,
        "to": 456,
        "decoded": {"text": "startup packet", "portnum": "TEXT_MESSAGE_APP"},
        "channel": 0,
        "id": 999,
        "rxTime": 94_900.0,
    }

    def _subscribe_side_effect(
        callback: Callable[[dict[str, Any], Any], None], topic: str
    ) -> None:
        if topic == "meshtastic.receive":
            callback(startup_packet, mock_client)

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
            "mmrelay.meshtastic_utils.pub.subscribe",
            side_effect=_subscribe_side_effect,
        ),
        patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
        patch("mmrelay.meshtastic_utils.time.time", return_value=100_000.0),
        patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_000.0),
    ):
        config = {
            "meshtastic": {"connection_type": CONNECTION_TYPE_TCP, "host": "127.0.0.1"}
        }
        result = connect_meshtastic(passed_config=config)

    assert result is mock_client
    assert mu._relay_rx_time_clock_skew_secs == 5_100.0
    assert mu._relay_startup_drain_deadline_monotonic_secs == (
        1_000.0 + mu._STARTUP_PACKET_DRAIN_SECS
    )
    mock_submit_coro.assert_not_called()


def test_connect_meshtastic_arms_startup_drain_after_setup_completes(
    reset_meshtastic_globals,
):
    """Startup drain should begin at subscribe readiness, not before slow setup."""
    mock_client = MagicMock()
    mock_client.getMyNodeInfo.return_value = {
        "user": {"shortName": "Node", "hwModel": "HW"}
    }

    startup_deadline_seen_during_metadata: float | None = None
    startup_deadline_seen_at_subscribe: float | None = None

    monotonic_values = [1_000.0, 1_030.0]

    def _monotonic_side_effect() -> float:
        if monotonic_values:
            return monotonic_values.pop(0)
        return 1_030.0

    def _metadata_side_effect(_client: object) -> dict[str, object]:
        nonlocal startup_deadline_seen_during_metadata
        startup_deadline_seen_during_metadata = (
            mu._relay_startup_drain_deadline_monotonic_secs
        )
        return {"firmware_version": "unknown", "success": False}

    def _subscribe_side_effect(
        _callback: Callable[[dict[str, Any], Any], None], topic: str
    ) -> None:
        nonlocal startup_deadline_seen_at_subscribe
        if topic == "meshtastic.receive":
            startup_deadline_seen_at_subscribe = (
                mu._relay_startup_drain_deadline_monotonic_secs
            )

    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
            return_value=mock_client,
        ),
        patch(
            "mmrelay.meshtastic_utils._get_device_metadata",
            side_effect=_metadata_side_effect,
        ),
        patch(
            "mmrelay.meshtastic_utils.pub.subscribe",
            side_effect=_subscribe_side_effect,
        ),
        patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
        patch("mmrelay.meshtastic_utils.time.time", return_value=100_000.0),
        patch(
            "mmrelay.meshtastic_utils.time.monotonic",
            side_effect=_monotonic_side_effect,
        ),
    ):
        config = {
            "meshtastic": {"connection_type": CONNECTION_TYPE_TCP, "host": "127.0.0.1"}
        }
        result = connect_meshtastic(passed_config=config)

    assert result is mock_client
    assert startup_deadline_seen_during_metadata is None
    assert startup_deadline_seen_at_subscribe == (
        1_030.0 + mu._STARTUP_PACKET_DRAIN_SECS
    )
    assert mu._relay_startup_drain_deadline_monotonic_secs == (
        1_030.0 + mu._STARTUP_PACKET_DRAIN_SECS
    )
    mock_submit_coro.assert_not_called()


def test_connect_meshtastic_bootstraps_skew_for_fast_receive_during_metadata_reconnect(
    reset_meshtastic_globals,
):
    """Reconnect path should bootstrap skew even when receive is already subscribed."""
    mock_client = MagicMock()
    mock_client.getMyNodeInfo.return_value = {
        "user": {"shortName": "Node", "hwModel": "HW"}
    }
    startup_packet = {
        "from": 123,
        "to": 456,
        "decoded": {"text": "startup packet", "portnum": "TEXT_MESSAGE_APP"},
        "channel": 0,
        "id": 1000,
        "rxTime": 94_900.0,
    }

    # Simulate reconnect state: handlers were already subscribed on a prior connect.
    mu.subscribed_to_messages = True
    mu.subscribed_to_connection_lost = True
    mu.RELAY_START_TIME = 200_000.0
    mu._relay_rx_time_clock_skew_secs = None
    mu._startup_packet_drain_applied = True
    mu._relay_startup_drain_deadline_monotonic_secs = None

    def _metadata_side_effect(_client: object) -> dict[str, object]:
        mu.on_meshtastic_message(startup_packet, mock_client)
        return {"firmware_version": "unknown", "success": False}

    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
            return_value=mock_client,
        ),
        patch(
            "mmrelay.meshtastic_utils._get_device_metadata",
            side_effect=_metadata_side_effect,
        ),
        patch("mmrelay.meshtastic_utils.pub.subscribe") as mock_subscribe,
        patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
        patch("mmrelay.meshtastic_utils.time.time", return_value=100_000.0),
        patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_000.0),
    ):
        config = {
            "meshtastic": {"connection_type": CONNECTION_TYPE_TCP, "host": "127.0.0.1"}
        }
        result = connect_meshtastic(passed_config=config)

    assert result is mock_client
    assert mu._relay_rx_time_clock_skew_secs == 5_100.0
    assert mu._relay_startup_drain_deadline_monotonic_secs is None
    assert mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs is None
    mock_subscribe.assert_not_called()
    mock_submit_coro.assert_not_called()


def test_connect_meshtastic_resets_timing_before_get_my_node_info_on_reconnect(
    reset_meshtastic_globals,
):
    """Reconnect should reset timing state before node-info fetch can process packets."""
    mock_client = MagicMock()
    startup_packet = {
        "from": 123,
        "to": 456,
        "decoded": {"text": "startup packet", "portnum": "TEXT_MESSAGE_APP"},
        "channel": 0,
        "id": 1001,
        "rxTime": 95_000.0,
    }

    mu.subscribed_to_messages = True
    mu.subscribed_to_connection_lost = True
    mu.RELAY_START_TIME = 50_000.0
    mu._relay_connection_started_monotonic_secs = 500.0
    mu._relay_rx_time_clock_skew_secs = 123.0
    mu._startup_packet_drain_applied = True
    mu._relay_startup_drain_deadline_monotonic_secs = 999.0
    mu._health_probe_request_deadlines[4242] = 9_999_999.0

    saw_reset_state_during_get_node_info = False

    def _get_node_info_side_effect() -> dict[str, dict[str, str]]:
        nonlocal saw_reset_state_during_get_node_info
        if (
            mu.RELAY_START_TIME == 100_000.0
            and mu._relay_connection_started_monotonic_secs == 1_000.0
            and mu._relay_rx_time_clock_skew_secs is None
            and mu._relay_startup_drain_deadline_monotonic_secs is None
            and mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs
            == 1_000.0 + mu._RECONNECT_PRESTART_BOOTSTRAP_WINDOW_SECS
            and not mu._health_probe_request_deadlines
        ):
            saw_reset_state_during_get_node_info = True
        mu.on_meshtastic_message(startup_packet, mock_client)
        return {"user": {"shortName": "Node", "hwModel": "HW"}}

    mock_client.getMyNodeInfo.side_effect = _get_node_info_side_effect

    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
            return_value=mock_client,
        ),
        patch(
            "mmrelay.meshtastic_utils._get_device_metadata",
            return_value={"firmware_version": "unknown", "success": False},
        ),
        patch("mmrelay.meshtastic_utils.pub.subscribe") as mock_subscribe,
        patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
        patch("mmrelay.meshtastic_utils.logger"),
        patch("mmrelay.meshtastic_utils.time.time", return_value=100_000.0),
        patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_000.0),
    ):
        config = {
            "meshtastic": {"connection_type": CONNECTION_TYPE_TCP, "host": "127.0.0.1"}
        }
        result = connect_meshtastic(passed_config=config)

    assert result is mock_client
    assert saw_reset_state_during_get_node_info is True
    assert mu._relay_rx_time_clock_skew_secs == 5_000.0
    assert mu._relay_startup_drain_deadline_monotonic_secs is None
    assert mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs is None
    assert not mu._health_probe_request_deadlines
    mock_subscribe.assert_not_called()
    mock_submit_coro.assert_not_called()


def test_connect_meshtastic_schedules_one_shot_probe_when_periodic_health_disabled(
    reset_meshtastic_globals,
):
    """Connect should schedule one-shot probe when explicitly enabled, even with periodic checks off."""
    mock_client = MagicMock()
    mock_client.localNode = MagicMock()
    mock_client.sendData = MagicMock()
    mock_client.getMyNodeInfo.return_value = {
        "user": {"shortName": "Node", "hwModel": "HW"}
    }

    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
            return_value=mock_client,
        ),
        patch(
            "mmrelay.meshtastic_utils._get_device_metadata",
            return_value={"firmware_version": "unknown", "success": False},
        ),
        patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit_probe,
        patch(
            "mmrelay.meshtastic_utils._probe_device_connection"
        ) as mock_probe_device_connection,
    ):
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_TCP,
                "host": "127.0.0.1",
                "health_check": {"enabled": False, "connect_probe_enabled": True},
            }
        }
        result = connect_meshtastic(passed_config=config)
        submitted_probe = mock_submit_probe.call_args.args[0]
        assert callable(submitted_probe)
        submitted_probe()

    assert result is mock_client
    mock_submit_probe.assert_called_once()
    mock_probe_device_connection.assert_called_once_with(
        mock_client, float(DEFAULT_MESHTASTIC_OPERATION_TIMEOUT)
    )


def test_connect_meshtastic_probe_invalid_override_inherits_parent_enabled(
    reset_meshtastic_globals,
):
    """Invalid connect_probe_enabled should inherit health_check.enabled."""
    mock_client = MagicMock()
    mock_client.localNode = MagicMock()
    mock_client.sendData = MagicMock()
    mock_client.getMyNodeInfo.return_value = {
        "user": {"shortName": "Node", "hwModel": "HW"}
    }

    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
            return_value=mock_client,
        ),
        patch(
            "mmrelay.meshtastic_utils._get_device_metadata",
            return_value={"firmware_version": "unknown", "success": False},
        ),
        patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit_probe,
    ):
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_TCP,
                "host": "127.0.0.1",
                "health_check": {
                    "enabled": True,
                    "connect_probe_enabled": "not-a-bool",
                },
            }
        }
        result = connect_meshtastic(passed_config=config)

    assert result is mock_client
    mock_submit_probe.assert_called_once()


def test_connect_meshtastic_does_not_schedule_one_shot_probe_by_default(
    reset_meshtastic_globals,
):
    """Connect-time probe should remain opt-in when periodic health checks are disabled."""
    mock_client = MagicMock()
    mock_client.localNode = MagicMock()
    mock_client.sendData = MagicMock()
    mock_client.getMyNodeInfo.return_value = {
        "user": {"shortName": "Node", "hwModel": "HW"}
    }

    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
            return_value=mock_client,
        ),
        patch(
            "mmrelay.meshtastic_utils._get_device_metadata",
            return_value={"firmware_version": "unknown", "success": False},
        ),
        patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit_probe,
    ):
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_TCP,
                "host": "127.0.0.1",
                "health_check": {"enabled": False},
            }
        }
        result = connect_meshtastic(passed_config=config)

    assert result is mock_client
    mock_submit_probe.assert_not_called()


def test_connect_meshtastic_clears_active_client_after_setup_failure_before_retry(
    reset_meshtastic_globals,
):
    """Setup failure after client assignment should clear active client state before retry."""
    first_client = MagicMock()
    first_client.getMyNodeInfo.side_effect = RuntimeError("node info failed")

    second_client = MagicMock()
    second_client.getMyNodeInfo.return_value = {
        "user": {"shortName": "Node", "hwModel": "HW"}
    }

    saw_cleanup_before_retry = False

    def _sleep_side_effect(_seconds: float) -> None:
        nonlocal saw_cleanup_before_retry
        if mu.meshtastic_client is None and mu._relay_active_client_id is None:
            saw_cleanup_before_retry = True

    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
            side_effect=[first_client, second_client],
        ),
        patch(
            "mmrelay.meshtastic_utils._get_device_metadata",
            return_value={"firmware_version": "unknown", "success": False},
        ),
        patch("mmrelay.meshtastic_utils.time.sleep", side_effect=_sleep_side_effect),
    ):
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_TCP,
                "host": "127.0.0.1",
                "retries": 1,
            }
        }
        result = connect_meshtastic(passed_config=config, force_connect=True)

    assert result is second_client
    assert saw_cleanup_before_retry is True
    first_client.close.assert_called_once()
    assert mu._relay_active_client_id == id(second_client)


def test_connect_meshtastic_timeout_breaks_on_shutdown(reset_meshtastic_globals):
    def _timeout_then_shutdown(*_args, **_kwargs):
        """Set shutting_down flag and raise TimeoutError."""
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


@patch("mmrelay.meshtastic_utils.time.sleep")
def test_connect_meshtastic_timeout_respects_retry_limit(
    mock_sleep, reset_meshtastic_globals
):
    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
            side_effect=TimeoutError("timeout"),
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
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
    assert mock_sleep.call_count == 1
    mock_logger.exception.assert_any_call("Connection failed after %s attempts", 2)


@patch("mmrelay.meshtastic_utils.serial_port_exists", return_value=True)
@patch("mmrelay.meshtastic_utils.time.sleep")
def test_connect_meshtastic_serial_exception_retries_then_fails(
    mock_sleep, mock_exists, reset_meshtastic_globals
):
    serial_error = serial.SerialException("serial")

    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface",
            side_effect=[serial_error, serial_error],
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
                "retries": 1,
            }
        }
        result = connect_meshtastic(passed_config=config)

    assert result is None
    assert mock_sleep.call_count == 1
    mock_logger.exception.assert_any_call("Connection failed after %s attempts", 2)


@patch("mmrelay.meshtastic_utils.time.sleep")
def test_connect_meshtastic_unexpected_exception_exhausts_retries(
    mock_sleep, reset_meshtastic_globals
):
    with (
        patch(
            "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
            side_effect=Exception("boom"),
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
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
    assert mock_sleep.call_count == 1
    mock_logger.exception.assert_any_call("Connection failed after %s attempts", 2)


def test_on_lost_meshtastic_connection_ignores_bad_fd(reset_meshtastic_globals):
    mock_client = MagicMock()
    error = OSError("bad fd")
    error.errno = ERRNO_BAD_FILE_DESCRIPTOR
    mock_client.close.side_effect = error
    mu.meshtastic_client = mock_client

    with (
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        patch("mmrelay.meshtastic_utils.event_loop") as mock_loop,
    ):
        mock_loop.is_closed.return_value = False
        on_lost_meshtastic_connection(detection_source="test")

    mock_logger.warning.assert_not_called()


def test_on_lost_meshtastic_connection_logs_close_error(reset_meshtastic_globals):
    mock_client = MagicMock()
    mock_client.close.side_effect = OSError(5, "close")
    mu.meshtastic_client = mock_client

    with (
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        patch("mmrelay.meshtastic_utils.event_loop") as mock_loop,
    ):
        mock_loop.is_closed.return_value = True
        on_lost_meshtastic_connection(detection_source="test")

    mock_logger.warning.assert_any_call(
        "Error closing Meshtastic client: [Errno 5] close"
    )


def test_on_lost_meshtastic_connection_logs_unexpected_close_error(
    reset_meshtastic_globals,
):
    mock_client = MagicMock()
    mock_client.close.side_effect = RuntimeError("boom")
    mu.meshtastic_client = mock_client

    with (
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        patch("mmrelay.meshtastic_utils.event_loop") as mock_loop,
    ):
        mock_loop.is_closed.return_value = True
        on_lost_meshtastic_connection(detection_source="test")

    mock_logger.warning.assert_any_call("Error closing Meshtastic client: boom")


def test_on_lost_meshtastic_connection_ignores_stale_interface_callback(
    reset_meshtastic_globals,
):
    active_client = MagicMock()
    stale_interface = MagicMock()
    mu.meshtastic_client = active_client
    mu._relay_active_client_id = id(active_client)
    mu.reconnecting = False
    mu.shutting_down = False

    with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
        on_lost_meshtastic_connection(
            interface=stale_interface, detection_source="test"
        )

    assert mu.reconnecting is False
    assert mu.meshtastic_client is active_client
    active_client.close.assert_not_called()
    debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
    assert any("stale Meshtastic interface" in call for call in debug_calls)


class TestBleDegradedStateSubmissionBlocking:
    """Test that degraded BLE state properly blocks work submission."""

    def test_ble_degraded_state_blocks_constructor_path(self, reset_meshtastic_globals):
        """Degraded BLE state should block work submission in compatibility (constructor-only) path."""
        ble_address = "AA:BB:CC:DD:EE:FF"
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": ble_address,
                "retries": 1,
            }
        }

        mu._ble_executor_degraded_addresses.add(ble_address)

        class _FakeBLEInterfaceNoAutoReconnect:
            def __init__(self, **kwargs: object) -> None:
                self.address = kwargs.get("address")

            def getMyNodeInfo(self) -> dict[str, dict[str, str]]:
                return {"user": {"shortName": "Node", "hwModel": "HW"}}

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEInterfaceNoAutoReconnect,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                return_value=True,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            result = connect_meshtastic(passed_config=config)

        assert result is None
        error_calls = [
            call
            for call in mock_logger.error.call_args_list
            if call.args and "degraded" in str(call.args).lower()
        ]
        assert error_calls, "Expected error about degraded BLE executor"
        assert ble_address in str(error_calls[0].args)

    def test_ble_degraded_state_blocks_explicit_connect_path(
        self, reset_meshtastic_globals
    ):
        """Degraded BLE state should block work submission in explicit connect() path."""
        ble_address = "AA:BB:CC:DD:EE:FF"
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": ble_address,
                "retries": 1,
            }
        }

        mu._ble_executor_degraded_addresses.add(ble_address)

        class _FakeBLEInterfaceWithAutoReconnect:
            def __init__(self, **kwargs: object) -> None:
                self.address = kwargs.get("address")
                self.auto_reconnect = False

            def connect(self) -> None:
                return None

            def getMyNodeInfo(self) -> dict[str, dict[str, str]]:
                return {"user": {"shortName": "Node", "hwModel": "HW"}}

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEInterfaceWithAutoReconnect,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                return_value=True,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            result = connect_meshtastic(passed_config=config)

        assert result is None
        error_calls = [
            call
            for call in mock_logger.error.call_args_list
            if call.args and "degraded" in str(call.args).lower()
        ]
        assert error_calls, "Expected error about degraded BLE executor"
        assert ble_address in str(error_calls[0].args)

    def test_ble_degraded_state_not_present_after_constructor_success_compatibility_mode(
        self, reset_meshtastic_globals
    ):
        """In compatibility mode, successful connection should not leave degraded state.

        This verifies that for interfaces without auto_reconnect support (where constructor
        success IS the full connection), the degraded state is not present after a successful
        connection. Note: This test does not verify clearing behavior - see
        test_ble_degraded_state_blocks_constructor_path for blocking behavior.
        """
        ble_address = "AA:BB:CC:DD:EE:FF"
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": ble_address,
                "retries": 1,
            }
        }

        class _FakeBLEInterfaceNoAutoReconnect:
            def __init__(self, **kwargs: object) -> None:
                self.address = kwargs.get("address")

            def getMyNodeInfo(self) -> dict[str, dict[str, str]]:
                return {"user": {"shortName": "Node", "hwModel": "HW"}}

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEInterfaceNoAutoReconnect,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                return_value=True,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            result = connect_meshtastic(passed_config=config)

        assert result is not None
        assert ble_address not in mu._ble_executor_degraded_addresses

    def test_ble_degraded_state_not_present_after_full_connection_auto_reconnect_mode(
        self, reset_meshtastic_globals
    ):
        """In auto_reconnect mode, successful connection should not leave degraded state.

        This verifies that for interfaces with auto_reconnect support, the degraded
        state is not present after both constructor and connect() succeed.
        Note: This test does not verify clearing behavior - see
        test_ble_degraded_state_blocks_explicit_connect_path for blocking behavior.
        """
        ble_address = "AA:BB:CC:DD:EE:FF"
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": ble_address,
                "retries": 1,
            }
        }

        class _FakeBLEInterfaceWithAutoReconnect:
            def __init__(self, **kwargs: object) -> None:
                self.address = kwargs.get("address")
                self.auto_reconnect = False

            def connect(self) -> None:
                return None

            def getMyNodeInfo(self) -> dict[str, dict[str, str]]:
                return {"user": {"shortName": "Node", "hwModel": "HW"}}

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEInterfaceWithAutoReconnect,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                return_value=True,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            result = connect_meshtastic(passed_config=config)

        assert result is not None
        assert ble_address not in mu._ble_executor_degraded_addresses

        info_calls = [
            call
            for call in mock_logger.info.call_args_list
            if call.args and "BLE connection established" in str(call.args[0])
        ]
        assert info_calls, "Expected 'BLE connection established' log message"
