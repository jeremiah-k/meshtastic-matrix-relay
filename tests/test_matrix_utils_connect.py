"""Tests for Matrix connect/bootstrap/config behavior.

This module tests general connection setup, config validation,
and legacy config handling during Matrix connection establishment.
"""

from collections.abc import Generator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from mmrelay.matrix_utils import MatrixAuthInfo, MissingMatrixRoomsError, connect_matrix


class _ImmediateAwaitable:
    """Awaitable that resolves immediately without creating coroutine objects."""

    def __init__(self, value: Any = None) -> None:
        self._value = value

    def __await__(self) -> Generator[None, None, Any]:
        if False:  # pragma: no cover
            yield
        return self._value


@pytest.mark.asyncio
async def test_connect_matrix_missing_matrix_section_returns_none():
    """Missing matrix config should log and return None."""
    config = {"matrix_rooms": []}

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await connect_matrix(config)

    assert result is None
    mock_logger.error.assert_any_call(
        "No Matrix authentication available. Neither credentials.json nor matrix section in config found."
    )


@pytest.mark.asyncio
async def test_connect_matrix_missing_required_fields_returns_none():
    """Missing required fields in matrix section should return None."""
    config = {
        "matrix": {"homeserver": "https://example.org", "bot_user_id": "@bot:example"},
        "matrix_rooms": [],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await connect_matrix(config)

    assert result is None
    assert any(
        "Matrix section is missing required field"
        in " ".join(str(arg) for arg in call.args)
        and "access_token" in " ".join(str(arg) for arg in call.args)
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_missing_matrix_rooms_raises():
    """Missing matrix_rooms should raise MissingMatrixRoomsError."""
    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        }
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        pytest.raises(MissingMatrixRoomsError, match="matrix_rooms"),
    ):
        await connect_matrix(config)


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context")
@patch("mmrelay.matrix_utils.AsyncClient")
async def test_connect_matrix_legacy_config(mock_async_client, mock_ssl_context):
    """Test Matrix connection with legacy config (no E2EE)."""
    # Mock SSL context
    mock_ssl_context.return_value = MagicMock()

    # Mock AsyncClient instance
    mock_client_instance = MagicMock()
    mock_client_instance.sync = Mock(
        return_value=_ImmediateAwaitable(SimpleNamespace())
    )
    mock_client_instance.rooms = {}
    mock_client_instance.whoami = Mock(
        return_value=_ImmediateAwaitable(MagicMock(device_id="LEGACY_DEVICE"))
    )
    mock_client_instance.get_displayname = Mock(
        return_value=_ImmediateAwaitable(MagicMock(displayname="Test Bot"))
    )
    mock_async_client.return_value = mock_client_instance

    # Legacy config without E2EE
    test_config = {
        "matrix": {
            "homeserver": "https://matrix.example.org",
            "access_token": "legacy_token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }

    # Mock the global matrix_client to None to ensure fresh creation
    with (
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.config.os.path.exists", return_value=False),
        patch("mmrelay.matrix.credentials.os.path.isfile", return_value=False),
        patch(
            "mmrelay.matrix_utils._resolve_aliases_in_mapping",
            Mock(return_value=_ImmediateAwaitable(None)),
        ),
        patch(
            "mmrelay.matrix_utils._display_room_channel_mappings",
            lambda *_a, **_k: None,
        ),
    ):
        client = await connect_matrix(test_config)

        assert client is not None
        assert client == mock_client_instance

        # Verify AsyncClient was created without E2EE
        mock_async_client.assert_called_once()
        call_args = mock_async_client.call_args
        assert "device_id" not in call_args[1]
        assert call_args[1].get("store_path") is None

        # Verify sync was called
        mock_client_instance.sync.assert_called()


@pytest.mark.asyncio
async def test_connect_matrix_uses_ssl_context_object(monkeypatch):
    """Ensure AsyncClient receives the actual SSLContext object, not a bool."""
    ssl_ctx = object()
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.should_upload_keys = False
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock()

    client_calls = []

    def fake_async_client(*_args, **_kwargs):
        """Create a fake async Matrix client for tests that records the passed SSL value and returns a predefined mock client.

        Parameters:
            *_args: Ignored positional arguments.
            **_kwargs: Keyword arguments; the `ssl` key, if present, is recorded into `client_calls`.

        Returns:
            mock_client: The predefined mock client object used by tests.
        """
        client_calls.append(_kwargs.get("ssl"))
        return mock_client

    monkeypatch.setattr("mmrelay.matrix_utils.AsyncClient", fake_async_client)
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context", lambda: ssl_ctx, raising=False
    )
    # Stub helpers to avoid extra work
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

    client = await connect_matrix()

    assert client is mock_client
    assert client_calls and client_calls[0] is ssl_ctx


@pytest.mark.asyncio
async def test_connect_matrix_duplicate_caller_returns_existing_client():
    """A second call should return the already-established client without re-running setup."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock()

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    ssl_ctx = MagicMock()
    init_mock = MagicMock(return_value=mock_client)

    with (
        patch("mmrelay.matrix_utils.AsyncClient", init_mock),
        patch("mmrelay.matrix_utils._create_ssl_context", return_value=ssl_ctx),
        patch("mmrelay.matrix_utils.matrix_client", None, create=True),
        patch("mmrelay.matrix_utils.config", config, create=True),
        patch(
            "mmrelay.matrix_utils._resolve_aliases_in_mapping",
            AsyncMock(return_value=None),
        ),
        patch(
            "mmrelay.matrix_utils._display_room_channel_mappings",
            lambda *_a, **_k: None,
        ),
    ):
        client1 = await connect_matrix(config)
        assert client1 is mock_client
        init_mock.assert_called_once()

        client2 = await connect_matrix(config)
        assert client2 is mock_client
        init_mock.assert_called_once()
