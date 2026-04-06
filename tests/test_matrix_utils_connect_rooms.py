"""Tests for Matrix connect-time room/alias/displayname setup.

This module tests room alias resolution, displayname handling,
and room setup behavior during Matrix connection establishment.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import mmrelay.matrix_utils as mx
from mmrelay.matrix_utils import connect_matrix


@patch("mmrelay.matrix_utils.matrix_client", None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
@patch("mmrelay.matrix_utils.login_matrix_bot")
@patch("mmrelay.matrix_utils.async_load_credentials", new_callable=AsyncMock)
async def test_connect_matrix_alias_resolution_exception(
    mock_load_credentials, mock_login_bot, _mock_logger, mock_async_client
):
    """Test that connect_matrix handles alias resolution exceptions gracefully."""
    with patch("mmrelay.matrix_utils._create_ssl_context") as mock_ssl_context:
        # Mock SSL context creation
        mock_ssl_context.return_value = MagicMock()

        # Mock login_matrix_bot to return True (successful automatic login)
        mock_login_bot.return_value = True

        # Mock load_credentials to return valid credentials
        mock_load_credentials.return_value = {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "user_id": "@test:matrix.org",
            "device_id": "test_device_id",
        }

        # Mock the AsyncClient instance
        mock_client_instance = MagicMock()
        mock_client_instance.rooms = {}

        # Create proper async mock methods
        async def mock_whoami():
            """Simulate a Matrix client's `whoami()` response for tests.

            Returns:
                unittest.mock.MagicMock: Mock object with a `device_id` attribute set to "test_device_id".
            """
            return MagicMock(device_id="test_device_id")

        async def mock_sync(*_args, **_kwargs):
            """Return a new unittest.mock.MagicMock instance each time the coroutine is awaited.

            Returns:
                unittest.mock.MagicMock: A fresh MagicMock suitable as a mocked async client's `sync`-like result in tests.
            """
            return MagicMock()

        async def mock_get_displayname(*_args, **_kwargs):
            """Return a MagicMock representing a user's display name for asynchronous tests.

            Returns:
                MagicMock: with a 'displayname' attribute set to 'Test Bot'.
            """
            return MagicMock(displayname="Test Bot")

        # Create a mock for room_resolve_alias that raises an exception
        mock_room_resolve_alias = MagicMock()

        async def mock_room_resolve_alias_impl(_alias):
            """Mock async implementation that simulates a network failure when resolving a Matrix room alias.

            Parameters:
                _alias (str): The room alias to resolve (ignored by this mock).

            Raises:
                OSError: Always raised to simulate a network error during alias resolution.
            """
            raise OSError("Simulated network failure")

        mock_room_resolve_alias.side_effect = mock_room_resolve_alias_impl

        mock_client_instance.whoami = mock_whoami
        mock_client_instance.sync = mock_sync
        mock_client_instance.get_displayname = mock_get_displayname
        mock_client_instance.room_resolve_alias = mock_room_resolve_alias

        # Mock async close method for cleanup
        async def mock_close():
            """Simulate async client close."""
            pass

        mock_client_instance.close = mock_close
        mock_async_client.return_value = mock_client_instance

        # Create config with room aliases
        config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "bot_user_id": "@test:matrix.org",
                "password": "test_password",
            },
            "matrix_rooms": [{"id": "#error:matrix.org", "meshtastic_channel": 1}],
        }

        result = await connect_matrix(config)

        # Verify client was created
        mock_async_client.assert_called_once()
        assert result == mock_client_instance

        # Verify alias resolution was called
        mock_client_instance.room_resolve_alias.assert_called_once_with(
            "#error:matrix.org"
        )

        # Verify exception was logged
        _mock_logger.exception.assert_called_with(
            "Error resolving alias #error:matrix.org"
        )

        # Verify config was not modified (still contains alias)
        assert config["matrix_rooms"][0]["id"] == "#error:matrix.org"


@pytest.mark.asyncio
async def test_connect_matrix_alias_resolution_warns_when_client_falsey(monkeypatch):
    """Alias resolution should warn when the client is unavailable/truthy check fails."""
    mock_client = MagicMock()
    mock_client.__bool__.return_value = False
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.should_upload_keys = False

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._create_ssl_context",
        lambda: MagicMock(),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils._display_room_channel_mappings",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
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
            "matrix_rooms": [{"id": "#alias:example.org", "meshtastic_channel": 0}],
        }
        await connect_matrix(config)

    mock_logger.warning.assert_any_call(
        "Cannot resolve alias #alias:example.org: Matrix client is not available"
    )


async def test_connect_matrix_displayname_fallbacks(monkeypatch):
    """Missing displayname should fall back to bot_user_id."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname=None)
    )
    mock_client.close = AsyncMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
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

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with patch("mmrelay.matrix_utils.os.path.exists", return_value=False):
        client = await connect_matrix(config)

    assert client is mock_client
    assert mx.bot_user_name == "@bot:example.org"


async def test_connect_matrix_displayname_exception_fallback(monkeypatch):
    """Displayname lookups that error should fall back to bot_user_id."""
    import mmrelay.matrix_utils as mx

    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_client.close = AsyncMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
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

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        client = await connect_matrix(config)

    assert client is mock_client
    assert mx.bot_user_name == "@bot:example.org"
    assert any(
        "Failed to get bot display name" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )
