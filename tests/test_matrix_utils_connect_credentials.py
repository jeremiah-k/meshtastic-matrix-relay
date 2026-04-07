"""Tests for Matrix connect-time credentials handling.

This module tests credentials loading, auto-login behavior,
reload/save operations during Matrix connection establishment.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.constants.app import CREDENTIALS_FILENAME
from mmrelay.matrix_utils import connect_matrix


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.matrix_client", None)
@patch("mmrelay.matrix_utils.logger")
@patch(
    "mmrelay.matrix_utils.login_matrix_bot", new_callable=AsyncMock, return_value=True
)
@patch("mmrelay.matrix_utils.async_load_credentials", new_callable=AsyncMock)
async def test_connect_matrix_auto_login_reload_json_decode_error(
    mock_load_credentials, mock_login_bot, mock_logger
):
    mock_load_credentials.side_effect = json.JSONDecodeError("test", "", 0)

    config = {
        "matrix": {
            "homeserver": "https://matrix.org",
            "bot_user_id": "@test:matrix.org",
            "password": "test_password",
        },
        "matrix_rooms": [],
    }

    with (
        patch("mmrelay.matrix_utils.get_credentials_path", side_effect=OSError("test")),
        patch("mmrelay.matrix_utils.get_explicit_credentials_path", return_value=None),
    ):
        result = await connect_matrix(config)

    assert result is None
    assert any(
        "Failed to reload newly created credentials" in call.args[0]
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_credentials_load_exception_uses_config(
    monkeypatch, tmp_path
):
    """Credential load errors should warn and fall back to config auth."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock()

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
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    candidate_path = tmp_path / CREDENTIALS_FILENAME
    candidate_path.write_text('{"invalid": true}', encoding="utf-8")

    with (
        patch(
            "mmrelay.config.get_credentials_search_paths",
            return_value=[str(candidate_path)],
        ),
        patch(
            "mmrelay.matrix_utils.get_credentials_path",
            return_value=tmp_path / CREDENTIALS_FILENAME,
        ),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        client = await connect_matrix(config)

    assert client is mock_client
    assert any(
        "Ignoring invalid credentials file" in str(call.args[0])
        or "Ignoring credentials.json missing required keys" in str(call.args[0])
        for call in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_explicit_credentials_path_is_used(tmp_path):
    """Explicit credentials_path should be expanded and used first."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock()
    mock_client.restore_login = MagicMock()

    access_value = "token"
    expanded_path = tmp_path / "explicit_credentials.json"
    expanded_path_str = str(expanded_path)
    expanded_path.write_text(
        '{"homeserver": "https://matrix.example.org", '
        f'"access_token": "{access_value}", '
        '"user_id": "@bot:example.org", '
        '"device_id": "DEVICE123"}',
        encoding="utf-8",
    )

    config = {
        "credentials_path": "~/explicit_credentials.json",
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "ignored",
            "bot_user_id": "@ignored:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.config.relay_config", config),
        patch("mmrelay.config.os.getenv", return_value=None),
        patch(
            "mmrelay.config.os.path.expanduser",
            return_value=expanded_path_str,
        ) as mock_expanduser,
        patch("mmrelay.matrix_utils.AsyncClient", lambda *_a, **_k: mock_client),
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils._create_ssl_context", lambda: MagicMock()),
        patch(
            "mmrelay.matrix_utils._resolve_aliases_in_mapping",
            AsyncMock(return_value=None),
        ),
        patch(
            "mmrelay.matrix_utils._display_room_channel_mappings",
            lambda *_args, **_kwargs: None,
        ),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status",
            return_value={"overall_status": "ok"},
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
    ):
        client = await connect_matrix(config)

    assert client is mock_client
    mock_expanduser.assert_any_call("~/explicit_credentials.json")
    mock_client.restore_login.assert_called_once_with(
        user_id="@bot:example.org",
        device_id="DEVICE123",
        access_token=access_value,
    )


@pytest.mark.asyncio
async def test_connect_matrix_ignores_config_access_token_when_credentials_present(
    monkeypatch,
):
    """Credentials should take precedence over config access_token."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.close = AsyncMock()

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
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "access_token": "config_token",
            "bot_user_id": "@bot:example.org",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=True),
        patch("builtins.open", new_callable=MagicMock),
        patch(
            "mmrelay.config.json.load",
            return_value={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "access_token": "creds_token",
                "device_id": "DEV",
            },
        ),
        patch(
            "mmrelay.e2ee_utils.get_e2ee_status", return_value={"overall_status": "ok"}
        ),
        patch("mmrelay.e2ee_utils.get_room_encryption_warnings", return_value=[]),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        client = await connect_matrix(config)

    assert client is mock_client
    mock_logger.info.assert_any_call(
        "NOTE: Ignoring Matrix login details in config.yaml in favor of credentials.json"
    )


@pytest.mark.asyncio
async def test_connect_matrix_auto_login_load_credentials_failure():
    """Automatic login should return None if new credentials cannot be loaded."""
    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "bot_user_id": "@bot:example.org",
            "password": "secret",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.login_matrix_bot", return_value=True),
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new=AsyncMock(return_value=None),
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await connect_matrix(config)

    assert result is None
    mock_logger.error.assert_called_with("Failed to load newly created credentials")


@pytest.mark.asyncio
async def test_connect_matrix_auto_login_failure():
    """Automatic login failures should return None."""
    config = {
        "matrix": {
            "homeserver": "https://example.org",
            "bot_user_id": "@bot:example.org",
            "password": "secret",
        },
        "matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}],
    }

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.login_matrix_bot", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await connect_matrix(config)

    assert result is None
    assert any(
        "Automatic login failed" in call.args[0]
        for call in mock_logger.error.call_args_list
    )
