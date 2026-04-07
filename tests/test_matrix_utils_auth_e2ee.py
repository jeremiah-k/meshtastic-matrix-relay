"""Tests for Matrix utility E2EE (End-to-End Encryption) functions."""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nio import ToDeviceError, ToDeviceResponse

import mmrelay.matrix_utils as matrix_utils_module
from mmrelay.constants.app import CREDENTIALS_FILENAME
from mmrelay.matrix_utils import (
    NioRemoteTransportError,
    connect_matrix,
    login_matrix_bot,
    on_decryption_failure,
)

pytestmark = pytest.mark.asyncio


async def test_on_decryption_failure():
    """Test on_decryption_failure handles decryption failures with retry logic."""

    # Create mock room and event
    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # Create a response mock that satisfies isinstance(response, ToDeviceResponse)
        mock_response = MagicMock(spec=ToDeviceResponse)
        mock_client.to_device = AsyncMock(return_value=mock_response)

        # Test successful key request - should exit after first success
        await on_decryption_failure(mock_room, mock_event)

        # Verify the event was patched with room_id
        assert mock_event.room_id == "!room123:matrix.org"
        # Verify key request was created and sent (only 1 call on success)
        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        assert mock_client.to_device.await_count == 1
        mock_client.to_device.assert_awaited_once_with({"type": "m.room_key_request"})
        # Verify logging - error about decryption failure, 1 info message on success
        assert mock_logger.error.call_count == 1  # Initial decryption failure
        assert mock_logger.info.call_count == 1  # Success message
        # Verify single sleep after success with key-sharing delay.
        assert [call.args[0] for call in mock_sleep.await_args_list] == [
            matrix_utils_module.E2EE_KEY_SHARING_DELAY_SECONDS
        ]


async def test_on_decryption_failure_without_matrix_client_logs_and_returns():
    """Test on_decryption_failure exits early when matrix_client is unavailable."""
    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"

    with (
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        await on_decryption_failure(mock_room, mock_event)

        # Initial decrypt error + unavailable matrix client error.
        assert mock_logger.error.call_count == 2
        mock_event.as_key_request.assert_not_called()
        mock_sleep.assert_not_awaited()


async def test_on_decryption_failure_missing_device_id():
    """Missing device_id should prevent key requests and log an error."""
    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = None
        mock_client.to_device = AsyncMock()

        await on_decryption_failure(mock_room, mock_event)

        mock_logger.error.assert_any_call(
            "Cannot request keys for event %s: client has no device_id",
            "$event123",
        )
        mock_client.to_device.assert_not_awaited()


async def test_on_decryption_failure_retry_on_exception():
    """Test on_decryption_failure retries with exponential backoff on communication exceptions."""

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        max_attempts = matrix_utils_module.E2EE_KEY_REQUEST_MAX_ATTEMPTS
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # Create a response mock that satisfies isinstance(response, ToDeviceResponse)
        mock_response = MagicMock(spec=ToDeviceResponse)
        # Fail until the final allowed attempt, then succeed.
        mock_client.to_device = AsyncMock(
            side_effect=[
                *[
                    NioRemoteTransportError("Network error")
                    for _ in range(max_attempts - 1)
                ],
                mock_response,
            ]
        )

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        # Verify all attempts were made.
        assert mock_client.to_device.await_count == max_attempts
        # Verify warnings were logged for failures before success
        assert mock_logger.warning.call_count == max_attempts - 1
        # Verify success info message (only on final successful attempt)
        assert mock_logger.info.call_count == 1
        # Compute expected sleep list: exponential backoff for first (max_attempts-1) retries, then key-sharing delay
        expected_sleep_list = []
        for attempt in range(1, max_attempts):  # attempts 1 to max_attempts-1
            delay = min(
                matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY * (2 ** (attempt - 1)),
                matrix_utils_module.E2EE_KEY_REQUEST_MAX_DELAY,
            )
            expected_sleep_list.append(delay)
        expected_sleep_list.append(matrix_utils_module.E2EE_KEY_SHARING_DELAY_SECONDS)
        assert [
            call.args[0] for call in mock_sleep.await_args_list
        ] == expected_sleep_list


async def test_on_decryption_failure_all_retries_fail():
    """Test on_decryption_failure logs error after all retries are exhausted."""

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # All attempts fail
        mock_client.to_device = AsyncMock(
            side_effect=NioRemoteTransportError("Network error")
        )

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        # Compute expected values from constants
        max_attempts = matrix_utils_module.E2EE_KEY_REQUEST_MAX_ATTEMPTS
        # Verify all attempts were made
        assert mock_client.to_device.await_count == max_attempts
        # Verify warnings for failures before final attempt
        assert mock_logger.warning.call_count == max_attempts - 1
        # Check that the final error message was logged (f-string format)
        mock_logger.exception.assert_called_once()
        # No info messages since all requests failed
        assert mock_logger.info.call_count == 0
        # Compute expected sleep list: exponential backoff for first (max_attempts-1) retries
        # No backoff after final failure
        expected_sleep_list = []
        for attempt in range(1, max_attempts):  # attempts 1 to max_attempts-1
            delay = min(
                matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY * (2 ** (attempt - 1)),
                matrix_utils_module.E2EE_KEY_REQUEST_MAX_DELAY,
            )
            expected_sleep_list.append(delay)
        assert [
            call.args[0] for call in mock_sleep.await_args_list
        ] == expected_sleep_list


async def test_on_decryption_failure_to_device_error():
    """Test on_decryption_failure handles ToDeviceError (server-side error) with retry."""

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # First returns ToDeviceError (server error), second returns ToDeviceResponse (success)
        mock_error = MagicMock(spec=ToDeviceError)
        mock_response = MagicMock(spec=ToDeviceResponse)
        mock_client.to_device = AsyncMock(
            side_effect=[
                mock_error,  # Server error on first attempt
                mock_response,  # Success on second attempt
            ]
        )

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        # Verify 2 attempts were made (error on first, success on second)
        assert mock_client.to_device.await_count == 2
        # Verify warning for the server error
        assert mock_logger.warning.call_count == 1
        # Verify success info message
        assert mock_logger.info.call_count == 1
        # Verify backoff after error and success key-sharing delay.
        expected_sleep_list = [
            matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY,
            matrix_utils_module.E2EE_KEY_SHARING_DELAY_SECONDS,
        ]
        assert [
            call.args[0] for call in mock_sleep.await_args_list
        ] == expected_sleep_list


async def test_on_decryption_failure_backoff_caps_at_max_delay():
    """
    Verify exponential backoff caps at the configured maximum delay when repeated to-device failures occur.

    This test simulates repeated ToDeviceError responses from the Matrix client's to_device call and asserts that on_decryption_failure schedules retries with asyncio.sleep delays that respect E2EE_KEY_REQUEST_BASE_DELAY and do not exceed E2EE_KEY_REQUEST_MAX_DELAY (expected sleep calls: 20, then capped 30.0). It also verifies the key request is created for the configured bot user and device.
    """

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch("mmrelay.matrix_utils.E2EE_KEY_REQUEST_MAX_ATTEMPTS", 3),
        patch("mmrelay.matrix_utils.E2EE_KEY_REQUEST_BASE_DELAY", 20),
        patch("mmrelay.matrix_utils.E2EE_KEY_REQUEST_MAX_DELAY", 30.0),
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # Keep returning ToDeviceError so retries continue until max attempts.
        mock_error = MagicMock(spec=ToDeviceError)
        mock_client.to_device = AsyncMock(
            side_effect=[mock_error, mock_error, mock_error]
        )

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        assert (
            mock_client.to_device.await_count
            == matrix_utils_module.E2EE_KEY_REQUEST_MAX_ATTEMPTS
        )
        assert [call.args[0] for call in mock_sleep.await_args_list] == [
            matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY,
            min(
                matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY * 2,
                matrix_utils_module.E2EE_KEY_REQUEST_MAX_DELAY,
            ),
        ]
        # Initial decryption error + terminal retry exhaustion error.
        assert mock_logger.error.call_count == 2
        assert mock_logger.info.call_count == 0


async def test_on_decryption_failure_unexpected_response_type():
    """Unexpected to_device response types should be logged and retried with backoff."""

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        # Return an unexpected type for all attempts to exercise fallback handling.
        max_attempts = matrix_utils_module.E2EE_KEY_REQUEST_MAX_ATTEMPTS
        mock_client.to_device = AsyncMock(side_effect=[object()] * max_attempts)

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )
        assert mock_client.to_device.await_count == max_attempts
        # Compute expected sleep list: exponential backoff for first (max_attempts-1) retries
        # No backoff after final failure
        expected_sleep_list = []
        for attempt in range(1, max_attempts):  # attempts 1 to max_attempts-1
            delay = min(
                matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY * (2 ** (attempt - 1)),
                matrix_utils_module.E2EE_KEY_REQUEST_MAX_DELAY,
            )
            expected_sleep_list.append(delay)
        assert [
            call.args[0] for call in mock_sleep.await_args_list
        ] == expected_sleep_list
        # Warning for each unexpected response
        assert mock_logger.warning.call_count == max_attempts
        assert (
            mock_logger.error.call_count == 2
        )  # initial decryption + final retry failure


async def test_on_decryption_failure_timeout_on_to_device():
    """Test on_decryption_failure handles asyncio.TimeoutError from the to-device wrapper."""

    mock_room = MagicMock()
    mock_room.room_id = "!room123:matrix.org"
    mock_event = MagicMock()
    mock_event.event_id = "$event123"
    mock_event.as_key_request.return_value = {"type": "m.room_key_request"}

    with (
        patch("mmrelay.matrix_utils.matrix_client") as mock_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch(
            "mmrelay.matrix_utils.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
        patch("mmrelay.matrix_utils.asyncio.wait_for") as mock_wait_for,
    ):
        mock_client.user_id = "@bot:matrix.org"
        mock_client.device_id = "DEVICE123"
        mock_client.to_device = AsyncMock(return_value=MagicMock(spec=ToDeviceResponse))

        async def mock_wait_for_impl(coro, timeout):
            assert timeout == matrix_utils_module.MATRIX_TO_DEVICE_TIMEOUT
            await coro
            raise asyncio.TimeoutError()

        mock_wait_for.side_effect = mock_wait_for_impl

        await on_decryption_failure(mock_room, mock_event)

        mock_event.as_key_request.assert_called_once_with(
            "@bot:matrix.org", "DEVICE123"
        )

        # Compute expected values from constants
        max_attempts = matrix_utils_module.E2EE_KEY_REQUEST_MAX_ATTEMPTS
        assert mock_wait_for.await_count == max_attempts
        # Verify all attempts were made via to_device calls
        assert mock_client.to_device.await_count == max_attempts
        # Verify warnings for failures before final attempt
        assert mock_logger.warning.call_count == max_attempts - 1
        # Verify final exception was logged
        mock_logger.exception.assert_called_once()
        # No info messages since all requests failed
        assert mock_logger.info.call_count == 0
        # Compute expected sleep list: exponential backoff for first (max_attempts-1) retries
        # No backoff after final failure
        expected_sleep_list = []
        for attempt in range(1, max_attempts):  # attempts 1 to max_attempts-1
            delay = min(
                matrix_utils_module.E2EE_KEY_REQUEST_BASE_DELAY * (2 ** (attempt - 1)),
                matrix_utils_module.E2EE_KEY_REQUEST_MAX_DELAY,
            )
            expected_sleep_list.append(delay)
        assert [
            call.args[0] for call in mock_sleep.await_args_list
        ] == expected_sleep_list


async def test_connect_matrix_e2ee_windows_disables(monkeypatch):
    """E2EE should be disabled on Windows platforms."""
    import mmrelay.matrix_utils as mx

    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    monkeypatch.setattr("mmrelay.matrix_utils.sys.platform", "win32", raising=False)
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr("mmrelay.matrix_utils.AsyncClientConfig", MagicMock())
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
            "encryption": {"enabled": True},
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        await connect_matrix(config)

    _, kwargs = mx.AsyncClientConfig.call_args  # type: ignore[attr-defined]
    assert kwargs["encryption_enabled"] is False


async def test_connect_matrix_e2ee_store_path_from_config(monkeypatch):
    """Configured E2EE store_path should be expanded and created."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_import(name):
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr("mmrelay.matrix_utils.sys.platform", "linux", raising=False)
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    store_path = os.path.expanduser("~/mmrelay-store")
    client_calls = []

    def fake_async_client(*_args, **_kwargs):
        client_calls.append(_kwargs)
        return mock_client

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        fake_async_client,
        raising=False,
    )
    with (
        patch("mmrelay.matrix_utils.os.makedirs"),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True, "store_path": store_path},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    assert client_calls
    assert client_calls[0]["store_path"] == store_path


async def test_connect_matrix_e2ee_store_path_precedence_encryption(monkeypatch):
    """Encryption store_path should take precedence over e2ee store_path."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_import(name):
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr("mmrelay.matrix_utils.sys.platform", "linux", raising=False)
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    encryption_path = os.path.expanduser("~/enc-store")
    e2ee_path = os.path.expanduser("~/e2ee-store")
    client_calls = []

    def fake_async_client(*_args, **_kwargs):
        client_calls.append(_kwargs)
        return mock_client

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        fake_async_client,
        raising=False,
    )
    with (
        patch("mmrelay.matrix_utils.os.makedirs"),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True, "store_path": encryption_path},
                "e2ee": {"enabled": True, "store_path": e2ee_path},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    assert client_calls
    assert client_calls[0]["store_path"] == encryption_path


async def test_connect_matrix_e2ee_store_path_uses_e2ee_section(monkeypatch):
    """e2ee store_path should be used when encryption store_path is absent."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_import(name):
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr("mmrelay.matrix_utils.sys.platform", "linux", raising=False)
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    e2ee_path = os.path.expanduser("~/e2ee-store")
    client_calls = []

    def fake_async_client(*_args, **_kwargs):
        client_calls.append(_kwargs)
        return mock_client

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        fake_async_client,
        raising=False,
    )
    with (
        patch("mmrelay.matrix_utils.os.makedirs"),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "e2ee": {"enabled": True, "store_path": e2ee_path},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    assert client_calls
    assert client_calls[0]["store_path"] == e2ee_path


async def test_connect_matrix_e2ee_store_path_default(monkeypatch, tmp_path):
    """Default store path should be used when no store_path is configured."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_import(name):
        if name == "nio.crypto":
            return SimpleNamespace(OlmDevice=True)
        if name == "nio.store":
            return SimpleNamespace(SqliteStore=True)
        if name == "olm":
            return MagicMock()
        return MagicMock()

    monkeypatch.setattr("mmrelay.matrix_utils.sys.platform", "linux", raising=False)
    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.importlib.import_module", fake_import, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    default_path = str(tmp_path)
    client_calls = []

    def fake_async_client(*_args, **_kwargs):
        client_calls.append(_kwargs)
        return mock_client

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        fake_async_client,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_store_dir",
        lambda: str(tmp_path),
        raising=False,
    )
    with (
        patch("mmrelay.matrix_utils.os.makedirs"),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status",
            return_value={"overall_status": "ok"},
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    assert client_calls
    assert client_calls[0]["store_path"] == default_path


@pytest.fixture
def mock_logger():
    with patch("mmrelay.matrix_utils.logger") as logger:
        yield logger


@pytest.fixture
def mock_listdir():
    with patch("mmrelay.matrix_utils.os.listdir") as listdir:
        yield listdir


@pytest.fixture
def mock_exists():
    with patch("mmrelay.matrix_utils.os.path.exists") as exists:
        yield exists


@pytest.fixture
def mock_async_client():
    with patch("mmrelay.matrix_utils.AsyncClient") as client:
        yield client


@pytest.fixture
def mock_ssl_context():
    with patch("mmrelay.matrix_utils._create_ssl_context") as ssl_context:
        yield ssl_context


@pytest.fixture
def _mock_makedirs():
    with patch("mmrelay.matrix_utils.os.makedirs") as makedirs:
        yield makedirs


@pytest.mark.asyncio
async def test_connect_matrix_e2ee_store_missing_db_files_warns(
    mock_logger,
    mock_async_client,
    mock_ssl_context,
    mock_exists,
    mock_listdir,
    _mock_makedirs,
):
    """Missing E2EE store DB files should warn when E2EE is enabled."""
    mock_listdir.return_value = ["notes.txt"]

    def exists_side_effect(path):
        if path.endswith(CREDENTIALS_FILENAME):
            return False
        if path == "/test/store":
            return True
        return False

    mock_exists.side_effect = exists_side_effect
    mock_ssl_context.return_value = MagicMock()

    mock_client_instance = MagicMock()
    mock_client_instance.rooms = {}
    mock_client_instance.sync = AsyncMock(return_value=MagicMock())
    mock_client_instance.whoami = AsyncMock(
        return_value=SimpleNamespace(device_id="DEV")
    )
    mock_client_instance.should_upload_keys = False
    mock_client_instance.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_async_client.return_value = mock_client_instance

    test_config = {
        "matrix": {
            "homeserver": "https://matrix.example.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:example.org",
            "encryption": {"enabled": True, "store_path": "/test/store"},
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    mock_olm = MagicMock()
    import importlib as _importlib

    real_import_module = _importlib.import_module

    def mock_import_side_effect(module_name, *args, **kwargs):
        if module_name == "olm":
            return mock_olm
        if module_name == "nio.crypto":
            mock_crypto = MagicMock()
            mock_crypto.OlmDevice = MagicMock()
            return mock_crypto
        if module_name == "nio.store":
            mock_store = MagicMock()
            mock_store.SqliteStore = MagicMock()
            return mock_store
        return real_import_module(module_name, *args, **kwargs)

    with (
        patch("mmrelay.config.is_e2ee_enabled", return_value=True),
        patch(
            "mmrelay.matrix_utils.importlib.import_module",
            side_effect=mock_import_side_effect,
        ),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch(
            "mmrelay.matrix_utils._resolve_aliases_in_mapping",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "mmrelay.matrix_utils._display_room_channel_mappings",
            return_value=None,
        ),
        patch("mmrelay.matrix_utils.matrix_client", None),
    ):
        await connect_matrix(test_config)

    assert any(
        "No existing E2EE store files found" in call.args[0]
        for call in mock_logger.info.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_e2ee_key_sharing_delay(monkeypatch, tmp_path):
    """E2EE-enabled connections should wait for key sharing delay."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_status",
        lambda *_args, **_kwargs: {"overall_status": "ok"},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_room_encryption_warnings",
        lambda *_args, **_kwargs: [],
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._resolve_aliases_in_mapping",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    mock_olm = MagicMock()
    import importlib as _importlib

    real_import_module = _importlib.import_module

    def mock_import_side_effect(module_name, *args, **kwargs):
        if module_name == "olm":
            return mock_olm
        if module_name == "nio.crypto":
            mock_crypto = MagicMock()
            mock_crypto.OlmDevice = MagicMock()
            return mock_crypto
        if module_name == "nio.store":
            mock_store = MagicMock()
            mock_store.SqliteStore = MagicMock()
            return mock_store
        return real_import_module(module_name, *args, **kwargs)

    sleep_mock = AsyncMock()
    with (
        patch("mmrelay.config.is_e2ee_enabled", return_value=True),
        patch(
            "mmrelay.matrix_utils.importlib.import_module",
            side_effect=mock_import_side_effect,
        ),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.os.makedirs"),
        patch("mmrelay.matrix_utils.os.listdir", return_value=["test.db"]),
        patch("mmrelay.matrix_utils.asyncio.sleep", sleep_mock),
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {
                    "enabled": True,
                    "store_path": str(tmp_path / "mmrelay-store"),
                },
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    sleep_mock.assert_awaited_once_with(
        matrix_utils_module.E2EE_KEY_SHARING_DELAY_SECONDS
    )


async def test_connect_matrix_e2ee_missing_nio_crypto():
    """
    Test connect_matrix handles missing nio.crypto.OlmDevice gracefully.
    """
    config = {
        "matrix": {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:matrix.org",
            "encryption": {"enabled": True},
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch("mmrelay.matrix_utils._create_ssl_context"),
        patch("mmrelay.matrix_utils.importlib.import_module") as mock_import,
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new=AsyncMock(return_value=None),
        ),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=False),
    ):
        # Mock importlib to simulate missing nio.crypto
        def mock_import_side_effect(module_name):
            if module_name == "olm":
                return MagicMock()  # olm is available
            elif module_name == "nio.crypto":
                mock_crypto = MagicMock()
                mock_crypto.OlmDevice = MagicMock()
                # Remove OlmDevice attribute
                del mock_crypto.OlmDevice
                return mock_crypto
            return MagicMock()

        mock_import.side_effect = mock_import_side_effect

        # Mock AsyncClient instance
        mock_client_instance = MagicMock()
        mock_client_instance.rooms = {}

        async def mock_sync(*args, **kwargs):
            return MagicMock()

        async def mock_get_displayname(*args, **kwargs):
            return MagicMock(displayname="Test Bot")

        mock_client_instance.sync = mock_sync
        mock_client_instance.get_displayname = mock_get_displayname
        mock_async_client.return_value = mock_client_instance

        result = await connect_matrix(config)

        # Should still create client but with E2EE disabled
        assert result == mock_client_instance
        # Should log exception about missing nio.crypto.OlmDevice
        mock_logger.exception.assert_called_with("Missing E2EE dependency")


async def test_connect_matrix_e2ee_missing_sqlite_store():
    """
    Test connect_matrix handles missing nio.store.SqliteStore gracefully.
    """
    config = {
        "matrix": {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:matrix.org",
            "encryption": {"enabled": True},
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
        patch("mmrelay.matrix_utils._create_ssl_context"),
        patch("mmrelay.matrix_utils.importlib.import_module") as mock_import,
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new=AsyncMock(return_value=None),
        ),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=False),
    ):
        # Mock importlib to simulate missing nio.store.SqliteStore
        def mock_import_side_effect(module_name):
            if module_name == "olm":
                return MagicMock()  # olm is available
            elif module_name == "nio.crypto":
                mock_crypto = MagicMock()
                mock_crypto.OlmDevice = MagicMock()
                return mock_crypto
            elif module_name == "nio.store":
                mock_store = MagicMock()
                mock_store.SqliteStore = MagicMock()
                # Remove SqliteStore attribute
                del mock_store.SqliteStore
                return mock_store
            return MagicMock()

        mock_import.side_effect = mock_import_side_effect

        # Mock AsyncClient instance
        mock_client_instance = MagicMock()
        mock_client_instance.rooms = {}

        async def mock_sync(*args, **kwargs):
            return MagicMock()

        async def mock_get_displayname(*args, **kwargs):
            return MagicMock(displayname="Test Bot")

        mock_client_instance.sync = mock_sync
        mock_client_instance.get_displayname = mock_get_displayname
        mock_async_client.return_value = mock_client_instance

        result = await connect_matrix(config)

        # Should still create client but with E2EE disabled
        assert result == mock_client_instance
        # Should log exception about missing nio.store.SqliteStore
        mock_logger.exception.assert_called_with("Missing E2EE dependency")


TEST_HOMESERVER = "https://matrix.org"
TEST_USERNAME = "user"
TEST_PASSWORD = "pass"
TEST_FULL_MXID = "@user:matrix.org"


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
async def test_login_matrix_bot_e2ee_store_path_created(
    _mock_ssl_context, mock_async_client, _mock_save_credentials, tmp_path, monkeypatch
):
    """E2EE-enabled logins should create a store path."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    store_path = str(tmp_path)

    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_e2ee_store_dir",
        lambda: str(tmp_path),
        raising=False,
    )

    with (
        patch("mmrelay.config.load_config", return_value={"matrix": {}}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=True),
        patch("mmrelay.matrix_utils.os.makedirs") as mock_makedirs,
        patch(
            "mmrelay.matrix_utils._normalize_bot_user_id",
            return_value="@user:matrix.org",
        ),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    assert any(
        call.args == (store_path,) and call.kwargs == {"exist_ok": True}
        for call in mock_makedirs.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_e2ee_config_load_exception_disables_e2ee(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Config load failures should disable E2EE and skip store setup."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", side_effect=RuntimeError("boom")),
        patch("mmrelay.config.is_e2ee_enabled") as mock_is_e2ee,
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.get_e2ee_store_dir") as mock_store_dir,
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    mock_is_e2ee.assert_not_called()
    mock_store_dir.assert_not_called()
    assert any(
        "Could not load config for E2EE check" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )
    assert any(
        "E2EE disabled in configuration" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )
