from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestNormalizeFirmwareVersion:
    def test_bytes_decoded(self):
        from mmrelay.meshtastic.metadata import _normalize_firmware_version

        result = _normalize_firmware_version(b"2.3.1 ")
        assert result == "2.3.1"

    def test_unknown_string_returns_none(self):
        from mmrelay.meshtastic.metadata import _normalize_firmware_version

        result = _normalize_firmware_version("unknown")
        assert result is None

    def test_empty_string_returns_none(self):
        from mmrelay.meshtastic.metadata import _normalize_firmware_version

        result = _normalize_firmware_version("")
        assert result is None

    def test_none_returns_none(self):
        from mmrelay.meshtastic.metadata import _normalize_firmware_version

        result = _normalize_firmware_version(None)
        assert result is None

    def test_int_returns_none(self):
        from mmrelay.meshtastic.metadata import _normalize_firmware_version

        result = _normalize_firmware_version(42)
        assert result is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestExtractFirmwareVersionFromMetadata:
    def test_none_source(self):
        from mmrelay.meshtastic.metadata import (
            _extract_firmware_version_from_metadata,
        )

        result = _extract_firmware_version_from_metadata(None)
        assert result is None

    def test_dict_with_firmware_version(self):
        from mmrelay.meshtastic.metadata import (
            _extract_firmware_version_from_metadata,
        )

        result = _extract_firmware_version_from_metadata({"firmware_version": "2.3.1"})
        assert result == "2.3.1"

    def test_dict_with_camel_case(self):
        from mmrelay.meshtastic.metadata import (
            _extract_firmware_version_from_metadata,
        )

        result = _extract_firmware_version_from_metadata({"firmwareVersion": "2.3.1"})
        assert result == "2.3.1"

    def test_object_with_firmware_version(self):
        from mmrelay.meshtastic.metadata import (
            _extract_firmware_version_from_metadata,
        )

        source = MagicMock()
        source.firmware_version = "2.3.1"
        source.firmwareVersion = None
        result = _extract_firmware_version_from_metadata(source)
        assert result == "2.3.1"


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestExtractFirmwareVersionFromClient:
    def test_client_with_metadata(self):
        from mmrelay.meshtastic.metadata import (
            _extract_firmware_version_from_client,
        )

        client = MagicMock()
        client.metadata = {"firmware_version": "2.3.1"}
        client.localNode.metadata = None
        result = _extract_firmware_version_from_client(client)
        assert result == "2.3.1"

    def test_client_no_metadata(self):
        from mmrelay.meshtastic.metadata import (
            _extract_firmware_version_from_client,
        )

        client = MagicMock()
        client.metadata = None
        client.localNode.metadata = None
        client.localNode.iface.metadata = None
        result = _extract_firmware_version_from_client(client)
        assert result is None

    def test_client_no_local_node(self):
        from mmrelay.meshtastic.metadata import (
            _extract_firmware_version_from_client,
        )

        client = MagicMock()
        client.localNode = None
        client.metadata = None
        result = _extract_firmware_version_from_client(client)
        assert result is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGetDeviceMetadata:
    def test_cached_firmware_returns(self):
        from mmrelay.meshtastic.metadata import _get_device_metadata

        client = MagicMock()
        client.metadata = {"firmware_version": "2.3.1"}
        client.localNode = MagicMock()
        result = _get_device_metadata(client)
        assert result["firmware_version"] == "2.3.1"
        assert result["success"] is True

    def test_no_local_node(self):
        from mmrelay.meshtastic.metadata import _get_device_metadata

        client = MagicMock()
        client.localNode = None
        result = _get_device_metadata(client, force_refresh=True)
        assert result["success"] is False

    def test_no_get_metadata_callable(self):
        from mmrelay.meshtastic.metadata import _get_device_metadata

        client = MagicMock()
        client.localNode = MagicMock()
        client.localNode.getMetadata = None
        result = _get_device_metadata(client, force_refresh=True)
        assert result["success"] is False

    def test_raise_on_error(self):
        from mmrelay.meshtastic.metadata import _get_device_metadata

        client = MagicMock()
        client.localNode = None
        with pytest.raises(RuntimeError):
            _get_device_metadata(client, force_refresh=True, raise_on_error=True)


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGetNameSafely:
    def test_function_returns_name(self):
        from mmrelay.meshtastic.metadata import _get_name_safely

        result = _get_name_safely(lambda x: "TestName", "sender")
        assert result == "TestName"

    def test_function_returns_none(self):
        from mmrelay.meshtastic.metadata import _get_name_safely

        result = _get_name_safely(lambda x: None, "sender")
        assert result == "sender"

    def test_function_raises(self):
        from mmrelay.meshtastic.metadata import _get_name_safely

        result = _get_name_safely(lambda x: (_ for _ in ()).throw(TypeError), "sender")
        assert result == "sender"


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGetNameOrNone:
    def test_function_returns_name(self):
        from mmrelay.meshtastic.metadata import _get_name_or_none

        result = _get_name_or_none(lambda x: "TestName", "sender")
        assert result == "TestName"

    def test_function_returns_none(self):
        from mmrelay.meshtastic.metadata import _get_name_or_none

        result = _get_name_or_none(lambda x: None, "sender")
        assert result is None

    def test_function_raises(self):
        from mmrelay.meshtastic.metadata import _get_name_or_none

        result = _get_name_or_none(lambda x: (_ for _ in ()).throw(TypeError), "sender")
        assert result is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGetDeviceMetadataDeep:
    def test_force_refresh_with_success(self):
        from mmrelay.meshtastic.metadata import _get_device_metadata

        client = MagicMock()
        client.localNode.getMetadata.return_value = None
        client.metadata = {"firmware_version": "2.3.1"}
        future = MagicMock()
        future.result.return_value = None
        future.done.return_value = True
        with patch.object(mu, "_submit_metadata_probe", return_value=future):
            result = _get_device_metadata(client, force_refresh=True)
        assert result["firmware_version"] == "2.3.1"
        assert result["success"] is True

    def test_force_refresh_timeout(self):
        from concurrent.futures import TimeoutError as FuturesTimeoutError

        from mmrelay.meshtastic.metadata import _get_device_metadata

        client = MagicMock()
        client.localNode.getMetadata.return_value = None
        client.metadata = None
        client.localNode.metadata = None
        future = MagicMock()
        future.result.side_effect = FuturesTimeoutError()
        future.done.return_value = False
        with patch.object(mu, "_submit_metadata_probe", return_value=future):
            result = _get_device_metadata(client, force_refresh=True)
        assert result["success"] is False

    def test_force_refresh_degraded_executor(self):
        from mmrelay.meshtastic.metadata import _get_device_metadata

        client = MagicMock()
        client.localNode.getMetadata.return_value = None
        with patch.object(
            mu,
            "_submit_metadata_probe",
            side_effect=mu.MetadataExecutorDegradedError("degraded"),
        ):
            result = _get_device_metadata(client, force_refresh=True)
        assert result["success"] is False

    def test_force_refresh_runtime_error_submission(self):
        from mmrelay.meshtastic.metadata import _get_device_metadata

        client = MagicMock()
        client.localNode.getMetadata.return_value = None
        with patch.object(
            mu,
            "_submit_metadata_probe",
            side_effect=RuntimeError("no executor"),
        ):
            result = _get_device_metadata(client, force_refresh=True)
        assert result["success"] is False

    def test_force_refresh_future_none(self):
        from mmrelay.meshtastic.metadata import _get_device_metadata

        client = MagicMock()
        client.localNode.getMetadata.return_value = None
        with patch.object(mu, "_submit_metadata_probe", return_value=None):
            result = _get_device_metadata(client, force_refresh=True)
        assert result["success"] is False

    def test_force_refresh_exception_during_future(self):
        from mmrelay.meshtastic.metadata import _get_device_metadata

        client = MagicMock()
        client.localNode.getMetadata.return_value = None
        client.metadata = None
        client.localNode.metadata = None
        future = MagicMock()
        future.result.side_effect = RuntimeError("probe failed")
        future.done.return_value = True
        with patch.object(mu, "_submit_metadata_probe", return_value=future):
            result = _get_device_metadata(client, force_refresh=True)
        assert result["success"] is False

    def test_raise_on_error_with_degraded(self):
        from mmrelay.meshtastic.metadata import _get_device_metadata

        client = MagicMock()
        client.localNode.getMetadata.return_value = None
        with patch.object(
            mu,
            "_submit_metadata_probe",
            side_effect=mu.MetadataExecutorDegradedError("degraded"),
        ):
            with pytest.raises(mu.MetadataExecutorDegradedError):
                _get_device_metadata(client, force_refresh=True, raise_on_error=True)

    def test_force_refresh_timeout_with_raise(self):
        from concurrent.futures import TimeoutError as FuturesTimeoutError

        from mmrelay.meshtastic.metadata import _get_device_metadata

        client = MagicMock()
        client.localNode.getMetadata.return_value = None
        client.metadata = None
        client.localNode.metadata = None
        future = MagicMock()
        future.result.side_effect = FuturesTimeoutError()
        future.done.return_value = False
        with patch.object(mu, "_submit_metadata_probe", return_value=future):
            with pytest.raises(FuturesTimeoutError):
                _get_device_metadata(client, force_refresh=True, raise_on_error=True)
