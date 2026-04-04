from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.network import (
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_TCP,
)
from mmrelay.meshtastic_utils import connect_meshtastic


class _FakeBLEInterfaceCompat:
    def __init__(self, **kwargs: object) -> None:
        self.address = kwargs.get("address")

    def getMyNodeInfo(self) -> dict[str, dict[str, str]]:
        return {"user": {"shortName": "Node", "hwModel": "HW"}}


def _ble_config(ble_address: str = "AA:BB:CC:DD:EE:FF", retries: int = 1) -> dict:
    return {
        "meshtastic": {
            "connection_type": CONNECTION_TYPE_BLE,
            "ble_address": ble_address,
            "retries": retries,
        }
    }


def _tcp_config(host: str = "127.0.0.1", retries: int = 1) -> dict:
    return {
        "meshtastic": {
            "connection_type": CONNECTION_TYPE_TCP,
            "host": host,
            "retries": retries,
        }
    }


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestBleValidationFailure:
    def test_ble_validation_failure_disconnects_and_returns_none(self):
        config = _ble_config()

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEInterfaceCompat,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                return_value=False,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_interface") as mock_disc,
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            result = connect_meshtastic(passed_config=config)

        assert result is None
        mock_disc.assert_called_once()
        call_args = mock_disc.call_args
        assert call_args.kwargs.get("reason") == "address validation failed"
        assert call_args.args[0] is not None
        error_calls = [
            call
            for call in mock_logger.error.call_args_list
            if call.args and "BLE connection validation failed" in str(call.args[0])
        ]
        assert error_calls

    def test_ble_validation_failure_non_iface_client_closes(self):
        config = _ble_config()

        other_client = MagicMock()

        class _FakeBLEWithSideEffect(_FakeBLEInterfaceCompat):
            def __init__(self, **kwargs: object) -> None:
                super().__init__(**kwargs)
                self._other = other_client

        orig_init = _FakeBLEWithSideEffect.__init__

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEWithSideEffect,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                return_value=False,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_interface"),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            saved_iface = None

            def _capture_iface(self: Any, **kwargs: object) -> None:
                nonlocal saved_iface
                orig_init(self, **kwargs)
                saved_iface = mu.meshtastic_iface

            _FakeBLEWithSideEffect.__init__ = _capture_iface

            mu.meshtastic_iface = None

            result = connect_meshtastic(passed_config=config)

        _FakeBLEWithSideEffect.__init__ = orig_init

        assert result is None

    def test_ble_validation_failure_client_not_iface_uses_close(self):
        config = _ble_config()

        def _validate_and_clear_iface(client: object, addr: str) -> bool:
            mu.meshtastic_iface = None
            return False

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEInterfaceCompat,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                side_effect=_validate_and_clear_iface,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_interface"),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            result = connect_meshtastic(passed_config=config)

        assert result is None

    def test_ble_validation_disconnect_exception_handled(self):
        config = _ble_config()

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEInterfaceCompat,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                return_value=False,
            ),
            patch(
                "mmrelay.meshtastic_utils._disconnect_ble_interface",
                side_effect=RuntimeError("disconnect failed"),
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            result = connect_meshtastic(passed_config=config)

        assert result is None
        warning_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if call.args and "Error closing invalid BLE connection" in str(call.args[0])
        ]
        assert warning_calls


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestCleanupFailedAssignedClient:
    def test_tcp_getMyNodeInfo_failure_triggers_close_cleanup(self):
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.side_effect = RuntimeError("node info failed")

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
        ):
            result = connect_meshtastic(passed_config=_tcp_config())

        assert result is None
        mock_client.close.assert_called()
        assert mu.meshtastic_client is None
        assert mu._relay_active_client_id is None

    def test_ble_getMyNodeInfo_failure_triggers_ble_disconnect_cleanup(self):
        config = _ble_config()

        class _FakeBLEFailsNodeInfo(_FakeBLEInterfaceCompat):
            def getMyNodeInfo(self) -> dict[str, dict[str, str]]:
                raise RuntimeError("node info failed")

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEFailsNodeInfo,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                return_value=True,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_interface") as mock_disc,
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            result = connect_meshtastic(passed_config=config)

        assert result is None
        assert mock_disc.call_count >= 1
        assert mu.meshtastic_client is None
        assert mu._relay_active_client_id is None

    def test_cleanup_early_return_when_client_changed(self):
        mock_client = MagicMock()

        other_client = MagicMock()

        def _change_client_side_effect() -> dict[str, dict[str, str]]:
            mu.meshtastic_client = other_client
            mu._relay_active_client_id = id(other_client)
            raise RuntimeError("trigger cleanup")

        mock_client.getMyNodeInfo.side_effect = _change_client_side_effect

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
        ):
            result = connect_meshtastic(passed_config=_tcp_config())

        assert result is None
        mock_client.close.assert_not_called()
        assert mu.meshtastic_client is other_client

    def test_cleanup_close_exception_logs_warning_and_clears_globals(self):
        mock_client = MagicMock()
        mock_client.close.side_effect = RuntimeError("close failed")
        mock_client.getMyNodeInfo.side_effect = RuntimeError("trigger cleanup")

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                return_value=mock_client,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            result = connect_meshtastic(passed_config=_tcp_config())

        assert result is None
        warning_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if call.args
            and "Error closing Meshtastic client after setup failure"
            in str(call.args[0])
        ]
        assert warning_calls
        assert mu.meshtastic_client is None
        assert mu._relay_active_client_id is None

    def test_ble_disconnect_cleanup_exception_logs_warning_and_clears_globals(self):
        config = _ble_config()

        class _FakeBLEFailsNodeInfo(_FakeBLEInterfaceCompat):
            def getMyNodeInfo(self) -> dict[str, dict[str, str]]:
                raise RuntimeError("node info failed")

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEFailsNodeInfo,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                return_value=True,
            ),
            patch(
                "mmrelay.meshtastic_utils._disconnect_ble_interface",
                side_effect=RuntimeError("disconnect cleanup failed"),
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            result = connect_meshtastic(passed_config=config)

        assert result is None
        warning_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if call.args
            and "Error closing Meshtastic client after setup failure"
            in str(call.args[0])
        ]
        assert warning_calls
        assert mu.meshtastic_client is None
        assert mu._relay_active_client_id is None

    def test_tcp_metadata_failure_triggers_close_cleanup(self):
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
                side_effect=RuntimeError("metadata failed"),
            ),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            result = connect_meshtastic(passed_config=_tcp_config())

        assert result is None
        mock_client.close.assert_called()
        assert mu.meshtastic_client is None
        assert mu._relay_active_client_id is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestInconsistentRelayState:
    def test_on_meshtastic_message_logs_error_when_client_none_but_id_set(self):
        mu.meshtastic_client = None
        mu._relay_active_client_id = 12345

        mock_interface = MagicMock()
        packet = {
            "decoded": {"text": "hello", "portnum": "TEXT_MESSAGE_APP"},
            "from": "!abc12345",
            "to": 4294967295,
            "id": 0x12345678,
        }

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            mu.on_meshtastic_message(packet, mock_interface)

        error_calls = [
            call
            for call in mock_logger.error.call_args_list
            if call.args and "Inconsistent relay state" in str(call.args[0])
        ]
        assert error_calls
