from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestIsBleDuplicateConnectSuppressedError:
    def test_empty_message(self):
        from mmrelay.meshtastic.ble import _is_ble_duplicate_connect_suppressed_error

        exc = Exception("")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is False

    def test_matching_suppressed_token(self):
        from mmrelay.meshtastic.ble import _is_ble_duplicate_connect_suppressed_error

        exc = Exception("Connection suppressed: recently connected elsewhere")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is True

    def test_matching_dup_connect_token(self):
        from mmrelay.meshtastic.ble import _is_ble_duplicate_connect_suppressed_error

        exc = Exception("Connection suppressed: recently connected elsewhere")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is True

    def test_non_matching(self):
        from mmrelay.meshtastic.ble import _is_ble_duplicate_connect_suppressed_error

        exc = Exception("some other error")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is False


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestResetBleConnectionGateState:
    def test_no_callable_returns_false(self):
        from mmrelay.meshtastic.ble import _reset_ble_connection_gate_state

        mu._ble_gate_reset_callable = None
        result = _reset_ble_connection_gate_state("AA:BB", reason="test")
        assert result is False

    def test_callable_succeeds(self):
        from mmrelay.meshtastic.ble import _reset_ble_connection_gate_state

        mock_callable = MagicMock()
        mu._ble_gate_reset_callable = mock_callable
        result = _reset_ble_connection_gate_state("AA:BB", reason="test")
        assert result is True
        mock_callable.assert_called_once()

    def test_callable_exception_returns_false(self):
        from mmrelay.meshtastic.ble import _reset_ble_connection_gate_state

        mock_callable = MagicMock(side_effect=RuntimeError("fail"))
        mu._ble_gate_reset_callable = mock_callable
        result = _reset_ble_connection_gate_state("AA:BB", reason="test")
        assert result is False


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestAttachLateBleInterfaceDisposer:
    def test_cancelled_future_noop(self):
        from mmrelay.meshtastic.ble import _attach_late_ble_interface_disposer

        mock_future = MagicMock()
        mock_future.cancelled.return_value = True
        _attach_late_ble_interface_disposer(mock_future, "AA:BB", reason="test")
        mock_future.add_done_callback.assert_called_once()

    def test_successful_future_with_active_iface_skips(self):
        from mmrelay.meshtastic.ble import _attach_late_ble_interface_disposer

        mock_future = MagicMock()
        mock_future.cancelled.return_value = False

        late_iface = MagicMock()
        mock_future.result.return_value = late_iface

        mu.meshtastic_iface = late_iface
        with mu.meshtastic_iface_lock:
            pass

        _attach_late_ble_interface_disposer(mock_future, "AA:BB", reason="test")
        mock_future.add_done_callback.assert_called_once()


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestSanitizeBleAddress:
    def test_normal_address(self):
        from mmrelay.meshtastic.ble import _sanitize_ble_address

        assert _sanitize_ble_address("AA:BB:CC:DD:EE:FF") == "aabbccddeeff"

    def test_empty_address(self):
        from mmrelay.meshtastic.ble import _sanitize_ble_address

        assert _sanitize_ble_address("") == ""

    def test_dash_separated(self):
        from mmrelay.meshtastic.ble import _sanitize_ble_address

        assert _sanitize_ble_address("AA-BB-CC") == "aabbcc"

    def test_underscore_separated(self):
        from mmrelay.meshtastic.ble import _sanitize_ble_address

        assert _sanitize_ble_address("AA_BB_CC") == "aabbcc"

    def test_mixed_separators(self):
        from mmrelay.meshtastic.ble import _sanitize_ble_address

        assert _sanitize_ble_address("AA:BB_CC-DD") == "aabbccdd"


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestIsBleDiscoveryError:
    def test_no_peripheral_message(self):
        from mmrelay.meshtastic.ble import _is_ble_discovery_error

        exc = Exception("No Meshtastic BLE peripheral found")
        assert _is_ble_discovery_error(exc) is True

    def test_timed_out_message(self):
        from mmrelay.meshtastic.ble import _is_ble_discovery_error

        exc = Exception("Timed out waiting for connection completion")
        assert _is_ble_discovery_error(exc) is True

    def test_key_error_with_path(self):
        from mmrelay.meshtastic.ble import _is_ble_discovery_error

        exc = KeyError("path")
        assert _is_ble_discovery_error(exc) is True

    def test_key_error_without_path(self):
        from mmrelay.meshtastic.ble import _is_ble_discovery_error

        exc = KeyError("other_key")
        assert _is_ble_discovery_error(exc) is False

    def test_regular_exception(self):
        from mmrelay.meshtastic.ble import _is_ble_discovery_error

        exc = Exception("some other error")
        assert _is_ble_discovery_error(exc) is False


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestValidateBleConnectionAddress:
    def test_no_client_returns_true(self):
        from mmrelay.meshtastic.ble import _validate_ble_connection_address

        iface = MagicMock()
        iface.client = None
        result = _validate_ble_connection_address(iface, "AA:BB:CC:DD:EE:FF")
        assert result is True

    def test_matching_address_returns_true(self):
        from mmrelay.meshtastic.ble import _validate_ble_connection_address

        iface = MagicMock()
        iface.client.bleak_client.address = "AA:BB:CC:DD:EE:FF"
        result = _validate_ble_connection_address(iface, "AA:BB:CC:DD:EE:FF")
        assert result is True

    def test_mismatched_address_returns_false(self):
        from mmrelay.meshtastic.ble import _validate_ble_connection_address

        iface = MagicMock()
        iface.client.bleak_client.address = "11:22:33:44:55:66"
        result = _validate_ble_connection_address(iface, "AA:BB:CC:DD:EE:FF")
        assert result is False

    def test_no_bleak_client_returns_true(self):
        from mmrelay.meshtastic.ble import _validate_ble_connection_address

        iface = MagicMock()
        iface.client.bleak_client = None
        iface.client.address = None
        result = _validate_ble_connection_address(iface, "AA:BB:CC:DD:EE:FF")
        assert result is True

    def test_exception_returns_true(self):
        from mmrelay.meshtastic.ble import _validate_ble_connection_address

        iface = MagicMock()
        type(iface.client).bleak_client = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("fail"))
        )
        result = _validate_ble_connection_address(iface, "AA:BB")
        assert result is True


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestDisconnectBleInterface:
    def test_none_iface(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        _disconnect_ble_interface(None, reason="test")

    def test_interface_with_disconnect(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock()
        iface._exit_handler = None
        iface.disconnect.return_value = None
        iface.client = None
        iface.close.return_value = None

        with patch.object(mu.time, "sleep"):
            _disconnect_ble_interface(iface, reason="test")

        iface.disconnect.assert_called()

    def test_interface_disconnect_raises_then_retries(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock()
        iface._exit_handler = None
        iface.disconnect.side_effect = [Exception("fail"), None]
        iface.client = None
        iface.close.return_value = None

        with patch.object(mu.time, "sleep"):
            _disconnect_ble_interface(iface, reason="test")

    def test_interface_with_client_disconnect(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock()
        iface._exit_handler = None
        iface.disconnect.return_value = None
        iface.client.disconnect.return_value = None
        iface.client._exit_handler = None
        iface.close.return_value = None

        with patch.object(mu.time, "sleep"):
            _disconnect_ble_interface(iface, reason="test")

    def test_shutdown_reason(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock()
        iface._exit_handler = None
        iface.disconnect.return_value = None
        iface.client = None
        iface.close.return_value = None

        with patch.object(mu.time, "sleep"):
            _disconnect_ble_interface(iface, reason="shutdown")
