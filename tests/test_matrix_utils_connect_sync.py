"""Tests for Matrix connect-time sync and retry behavior.

This module tests initial sync handling, timeout/retry logic,
and startup robustness during Matrix connection establishment.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nio import SyncError

import mmrelay.matrix_utils as matrix_utils_module
from mmrelay.matrix_utils import NioLocalTransportError, connect_matrix


@pytest.mark.asyncio
async def test_connect_matrix_sync_timeout_closes_client(monkeypatch):
    """Initial sync timeout should close the client and raise ConnectionError."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_client.close = AsyncMock()
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    # Capture AsyncClient ssl argument for separate test
    def fake_async_client(*_args, **_kwargs):
        """Provide a preconfigured mock Matrix client for use in tests.

        Ignores all positional and keyword arguments and always returns the shared test mock client.

        Returns:
            mock_client: The preconfigured mock Matrix client instance used by tests.
        """
        return mock_client

    monkeypatch.setattr("mmrelay.matrix_utils.AsyncClient", fake_async_client)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config",
        {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        },
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.MATRIX_INITIAL_SYNC_MAX_ATTEMPTS", 3, raising=False
    )
    mock_sleep = AsyncMock()
    monkeypatch.setattr("mmrelay.matrix_utils.asyncio.sleep", mock_sleep)

    with pytest.raises(ConnectionError):
        await connect_matrix()

    mock_client.close.assert_awaited_once()
    assert mock_client.sync.await_count == 3
    assert [call.args[0] for call in mock_sleep.await_args_list] == [
        matrix_utils_module.MATRIX_SYNC_RETRY_DELAY_SECS,
        min(
            matrix_utils_module.MATRIX_SYNC_RETRY_DELAY_SECS * 2.0,
            matrix_utils_module.MATRIX_INITIAL_SYNC_RETRY_MAX_DELAY_SECS,
        ),
    ]
    import mmrelay.matrix_utils as mx

    assert mx.matrix_client is None


@pytest.mark.asyncio
async def test_connect_matrix_sync_timeout_retry_then_success(monkeypatch):
    """A transient initial-sync timeout should retry and succeed without failing startup."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(
        side_effect=[asyncio.TimeoutError(), SimpleNamespace()]
    )
    mock_client.close = AsyncMock()
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_async_client(*_args, **_kwargs):
        return mock_client

    monkeypatch.setattr("mmrelay.matrix_utils.AsyncClient", fake_async_client)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config",
        {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
                "encryption": {"enabled": True},
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        },
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.MATRIX_INITIAL_SYNC_MAX_ATTEMPTS", 0, raising=False
    )
    mock_sleep = AsyncMock()
    monkeypatch.setattr("mmrelay.matrix_utils.asyncio.sleep", mock_sleep)

    client = await connect_matrix()

    assert client is mock_client
    assert mock_client.sync.await_count == 2
    mock_sleep.assert_awaited_once_with(
        matrix_utils_module.MATRIX_SYNC_RETRY_DELAY_SECS
    )


@pytest.mark.asyncio
async def test_connect_matrix_sync_error_closes_client(monkeypatch):
    """If initial sync returns an error response, the client should close and raise."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    error_response = SyncError("sync failed")
    mock_client.sync = AsyncMock(return_value=error_response)
    mock_client.close = AsyncMock()
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_async_client(*_args, **_kwargs):
        """Provide a preconfigured mock Matrix client for use in tests.

        Ignores all positional and keyword arguments and always returns the shared test mock client.

        Returns:
            mock_client: The preconfigured mock Matrix client instance used by tests.
        """
        return mock_client

    monkeypatch.setattr("mmrelay.matrix_utils.AsyncClient", fake_async_client)
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config",
        {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        },
        raising=False,
    )

    with pytest.raises(ConnectionError):
        await connect_matrix()

    mock_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_matrix_sync_error_close_failure_logs():
    """Sync error handling should ignore close failures and still raise."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    error_response = SyncError("sync failed")
    mock_client.sync = AsyncMock(return_value=error_response)
    mock_client.close = AsyncMock(side_effect=NioLocalTransportError("close failed"))
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    def fake_async_client(*_args, **_kwargs):
        """Return the preconfigured mock Matrix client, ignoring all positional and keyword arguments.

        This helper supplies the shared mock client instance for tests that expect an async client factory.

        Returns:
            mock_client: The mock Matrix client instance used by the test suite.
        """
        return mock_client

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.AsyncClient", fake_async_client),
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock()),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        with pytest.raises(ConnectionError):
            await connect_matrix(config)

    assert mock_client.close.await_count == 1
    assert any(
        call.args[:2]
        == ("Ignoring error while closing client after %s", "connect_matrix setup")
        for call in mock_logger.debug.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_sync_validation_error_retries_with_invite_safe_filter():
    """ValidationError from invite events triggers invite-safe sync retry."""
    import jsonschema

    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock()
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock()

    # Set up two sync calls: first fails with ValidationError, second succeeds
    call_count = [0]

    async def mock_sync(*_args, **_kwargs):
        """Test helper that simulates a sync operation failing once with a ValidationError and succeeding thereafter.

        On each invocation this increments the enclosing `call_count[0]` counter. The first call raises a
        jsonschema.exceptions.ValidationError to simulate an invite-safe filtering error; subsequent calls
        return a simple success sentinel.

        Raises:
            jsonschema.exceptions.ValidationError: on the first invocation.

        Returns:
            SimpleNamespace: A success sentinel object on invocations after the first.
        """
        call_count[0] += 1
        if call_count[0] == 1:
            # First sync raises ValidationError (caught, triggers invite-safe filter)
            raise jsonschema.ValidationError(  # type: ignore[attr-defined]
                message="Invalid schema",
                path=(),
                schema_path=(),
            )
        # Second sync succeeds (with invite-safe filter)
        return SimpleNamespace()

    mock_client.sync = mock_sync

    # Set up mocks for connect_matrix
    def fake_async_client(*_args, **_kwargs):
        """Return the preconfigured mock Matrix client, ignoring all positional and keyword arguments.

        This helper supplies the shared mock client instance for tests that expect an async client factory.

        Returns:
            mock_client: The mock Matrix client instance used by the test suite.
        """
        return mock_client

    # Patch jsonschema.exceptions to simulate ImportError for ValidationError only
    with (
        patch("mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock()),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=False),
        patch(
            "mmrelay.matrix_utils._resolve_aliases_in_mapping",
            AsyncMock(return_value=None),
        ),
        patch(
            "mmrelay.matrix_utils._display_room_channel_mappings",
            lambda *_args, **_kwargs: None,
        ),
        patch("mmrelay.matrix_utils.AsyncClient", fake_async_client),
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        config = {
            "matrix": {
                "homeserver": "https://example.org",
                "access_token": "token",
                "bot_user_id": "@bot:example.org",
            },
            "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
        }

        await connect_matrix(config)

    # Verify that sync was called twice (initial failed, retry with invite-safe filter)
    assert call_count[0] == 2

    # Verify logging of retry behavior
    mock_logger.warning.assert_any_call(
        "Retrying initial sync without invites to tolerate invalid invite_state payloads."
    )

    # Verify client attributes were set with invite-safe filter
    assert hasattr(mock_client, "mmrelay_sync_filter")
    assert hasattr(mock_client, "mmrelay_first_sync_filter")


@pytest.mark.asyncio
async def test_connect_matrix_sync_validation_error_retry_failure_closes_client():
    """Failed invite-safe retry should close the client and raise ConnectionError."""
    import jsonschema

    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock(side_effect=NioLocalTransportError("close failed"))

    call_count = {"count": 0}

    async def mock_sync(*_args, **_kwargs):
        """Simulate a sync operation that increments a shared call counter and fails with controlled exceptions.

        Increments call_count["count"] each invocation. On the first invocation raises jsonschema.exceptions.ValidationError with message "Invalid schema"; on every subsequent invocation raises NioLocalTransportError("retry failed"). Positional and keyword arguments are ignored.
        """
        call_count["count"] += 1
        if call_count["count"] == 1:
            raise jsonschema.ValidationError(  # type: ignore[attr-defined]
                message="Invalid schema",
                path=(),
                schema_path=(),
            )
        raise NioLocalTransportError("retry failed")

    mock_client.sync = mock_sync

    def fake_async_client(*_args, **_kwargs):
        """Return the preconfigured mock Matrix client, ignoring all positional and keyword arguments.

        This helper supplies the shared mock client instance for tests that expect an async client factory.

        Returns:
            mock_client: The mock Matrix client instance used by the test suite.
        """
        return mock_client

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.AsyncClient", fake_async_client),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock()),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=False),
    ):
        with pytest.raises(ConnectionError):
            await connect_matrix(config)

    assert call_count["count"] == 2
    assert mock_client.close.await_count == 1
