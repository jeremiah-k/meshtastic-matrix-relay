from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestIsBleDuplicateConnectSuppressedError:
    def test_typed_suppressed_error(self, monkeypatch):
        from mmrelay.meshtastic.ble import _is_ble_duplicate_connect_suppressed_error

        class FakeSuppressedError(Exception):
            pass

        monkeypatch.setattr(mu, "BLEConnectionSuppressedError", FakeSuppressedError)
        assert (
            _is_ble_duplicate_connect_suppressed_error(FakeSuppressedError("typed"))
            is True
        )

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
    def test_delegates_to_facade_sanitize_address(self, monkeypatch):
        from mmrelay.meshtastic.ble import _sanitize_ble_address

        sanitizer = MagicMock(return_value="facadevalue")
        monkeypatch.setattr(mu, "sanitize_address", sanitizer)

        assert _sanitize_ble_address("AA:BB") == "facadevalue"
        sanitizer.assert_called_once_with("AA:BB")

    def test_falls_back_when_facade_sanitize_address_raises(self, monkeypatch):
        from mmrelay.meshtastic.ble import _sanitize_ble_address

        monkeypatch.setattr(
            mu,
            "sanitize_address",
            MagicMock(side_effect=RuntimeError("boom")),
        )

        assert _sanitize_ble_address("AA:BB_CC-DD") == "aabbccdd"

    def test_falls_back_when_facade_sanitize_address_bad_return(self, monkeypatch):
        from mmrelay.meshtastic.ble import _sanitize_ble_address

        monkeypatch.setattr(mu, "sanitize_address", MagicMock(return_value=object()))

        assert _sanitize_ble_address("AA:BB_CC-DD") == "aabbccdd"

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
    def test_typed_discovery_errors(self, monkeypatch):
        from mmrelay.meshtastic.ble import _is_ble_discovery_error

        class FakeDiscoveryError(Exception):
            pass

        class FakeDeviceNotFoundError(Exception):
            pass

        monkeypatch.setattr(mu, "BLEDiscoveryError", FakeDiscoveryError)
        monkeypatch.setattr(mu, "BLEDeviceNotFoundError", FakeDeviceNotFoundError)

        assert _is_ble_discovery_error(FakeDiscoveryError("discovery")) is True
        assert _is_ble_discovery_error(FakeDeviceNotFoundError("missing")) is True

    @pytest.mark.parametrize(
        "attr_name",
        [
            "BLEAddressMismatchError",
            "BLEConnectionTimeoutError",
            "BLEDBusTransportError",
            "BLEConnectionSuppressedError",
        ],
    )
    def test_typed_non_discovery_errors(self, monkeypatch, attr_name):
        from mmrelay.meshtastic.ble import _is_ble_discovery_error

        class FakeNonDiscoveryError(Exception):
            pass

        monkeypatch.setattr(mu, attr_name, FakeNonDiscoveryError)

        assert (
            _is_ble_discovery_error(
                FakeNonDiscoveryError("No Meshtastic BLE peripheral")
            )
            is False
        )

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
class TestExtractBleAddressFromInterface:
    def test_prefers_ble_address_properties(self):
        from mmrelay.meshtastic.ble import _extract_ble_address_from_interface

        iface = MagicMock()
        iface.bleAddress = "AA:BB:CC:DD:EE:FF"
        iface.ble_address = "11:22:33:44:55:66"
        iface.address = "Device Name"

        assert _extract_ble_address_from_interface(iface) == "aabbccddeeff"

    def test_uses_ble_address_shim_when_camel_missing(self):
        from mmrelay.meshtastic.ble import _extract_ble_address_from_interface

        iface = MagicMock()
        iface.bleAddress = None
        iface.ble_address = "11:22:33:44:55:66"
        iface.address = "Device Name"

        assert _extract_ble_address_from_interface(iface) == "112233445566"

    def test_ignores_non_mac_device_names(self):
        from mmrelay.meshtastic.ble import _extract_ble_address_from_interface

        iface = MagicMock()
        iface.bleAddress = "living-room-node"
        iface.ble_address = None
        iface.address = "another-device-name"
        iface.client = None

        assert _extract_ble_address_from_interface(iface) is None

    def test_falls_back_to_client_bleak_mac(self):
        from mmrelay.meshtastic.ble import _extract_ble_address_from_interface

        iface = MagicMock()
        iface.bleAddress = None
        iface.ble_address = None
        iface.address = "device-name"
        iface.client.address = "also-name"
        iface.client.bleak_client.address = "22:33:44:55:66:77"

        assert _extract_ble_address_from_interface(iface) == "223344556677"


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

    def test_modern_timeout_disconnect_and_close(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock()
        iface._exit_handler = None
        iface.client = MagicMock()
        iface.disconnect.return_value = None
        iface.close.return_value = None

        with patch.object(mu.time, "sleep"):
            _disconnect_ble_interface(iface, reason="test")

        iface.disconnect.assert_called_once_with(timeout=3.0)
        iface.close.assert_called_once_with(timeout=5.0)
        iface.client.disconnect.assert_not_called()

    def test_timeout_kwarg_typeerror_falls_back_to_legacy_calls(self):
        from mmrelay.meshtastic.ble import _disconnect_ble_interface

        iface = MagicMock()
        iface._exit_handler = None
        iface.client = None
        iface.disconnect.side_effect = [TypeError("unexpected keyword 'timeout'"), None]
        iface.close.side_effect = [TypeError("unexpected keyword 'timeout'"), None]

        with patch.object(mu.time, "sleep"):
            _disconnect_ble_interface(iface, reason="test")

        assert iface.disconnect.call_args_list[0].kwargs == {"timeout": 3.0}
        assert iface.disconnect.call_args_list[1].args == ()
        assert iface.close.call_args_list[0].kwargs == {"timeout": 5.0}
        assert iface.close.call_args_list[1].args == ()
