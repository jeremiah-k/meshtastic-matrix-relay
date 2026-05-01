"""Tests for Matrix connect-time E2EE/device/whoami setup.

This module tests E2EE dependency checks, device ID discovery,
key uploads, and whoami behavior during Matrix connection establishment.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from mmrelay.constants.app import CREDENTIALS_FILENAME
from mmrelay.matrix_utils import connect_matrix


def _matrix_capabilities(
    *,
    encryption_available: bool = True,
    provider_distribution: str = "matrix-nio",
    crypto_backend: str = "olm",
    install_hint: str = "install matrix-nio[e2e] / python-olm",
    recommended_e2ee_extra: str = "matrix-nio[e2e]",
    both_known_providers_installed: bool = False,
):
    return SimpleNamespace(
        encryption_available=encryption_available,
        provider_distribution=provider_distribution,
        provider_name=provider_distribution,
        provider_version="0.25.2",
        crypto_backend=crypto_backend,
        install_hint=install_hint,
        recommended_e2ee_extra=recommended_e2ee_extra,
        both_known_providers_installed=both_known_providers_installed,
    )


@pytest.fixture(autouse=True)
def matrix_capabilities_available(monkeypatch):
    """Keep E2EE connect tests independent from the developer environment."""

    monkeypatch.setattr(
        "mmrelay.matrix.auth.get_matrix_capabilities",
        lambda: _matrix_capabilities(),
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.os.makedirs")
@patch("mmrelay.matrix_utils.os.listdir")
@patch("mmrelay.matrix_utils.os.path.exists")
@patch("mmrelay.matrix_utils.os.path.isfile")
@patch("builtins.open")
@patch("mmrelay.config.json.load")
@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils._create_ssl_context")
@patch("mmrelay.matrix_utils.matrix_client", None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_connect_matrix_missing_device_id_uses_direct_assignment(
    _mock_logger,
    mock_async_client,
    mock_ssl_context,
    mock_save_credentials,
    mock_json_load,
    _mock_open,
    _mock_isfile,
    _mock_exists,
    _mock_listdir,
    _mock_makedirs,
    monkeypatch,
):
    """When credentials are missing device_id, the client should discover it via whoami
    and then restore the session using the discovered device_id.
    """
    _mock_exists.return_value = True
    _mock_isfile.return_value = True
    mock_json_load.return_value = {
        "homeserver": "https://matrix.example.org",
        "user_id": "@bot:example.org",
        "access_token": "test_token",
    }
    _mock_listdir.return_value = []
    mock_ssl_context.return_value = MagicMock()

    mock_client_instance = MagicMock()
    mock_client_instance.rooms = {}

    async def mock_sync(*_args, **_kwargs):
        """Create and return a MagicMock to simulate a sync operation result.

        Any positional and keyword arguments are accepted and ignored.

        Returns:
            MagicMock: A new MagicMock instance representing the mocked sync result.
        """
        return MagicMock()

    def mock_restore_login(user_id, device_id, access_token):
        """Set the mocked Matrix client's login state by assigning user, device, and token attributes.

        Parameters:
            user_id (str): Matrix user ID to set on the mock client.
            device_id (str): Device ID to set on the mock client.
            access_token (str): Access token to set on the mock client.
        """
        mock_client_instance.access_token = access_token
        mock_client_instance.user_id = user_id
        mock_client_instance.device_id = device_id

    discovered_device_id = "DISCOVERED_DEVICE"

    mock_client_instance.sync = AsyncMock(side_effect=mock_sync)
    mock_client_instance.restore_login = MagicMock(side_effect=mock_restore_login)
    mock_client_instance.whoami = AsyncMock(
        return_value=SimpleNamespace(device_id=discovered_device_id)
    )
    mock_client_instance.should_upload_keys = False
    mock_client_instance.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    mock_async_client.return_value = mock_client_instance
    # Minimal config needed for matrix_rooms
    monkeypatch.setattr(
        "mmrelay.matrix_utils.config",
        {"matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}]},
        raising=False,
    )

    client = await connect_matrix()

    assert client is mock_client_instance
    # restore_login should use the discovered device_id from whoami
    mock_client_instance.restore_login.assert_called_once_with(
        user_id="@bot:example.org",
        device_id=discovered_device_id,
        access_token="test_token",
    )
    # Access token should still be set via restore_login
    assert mock_client_instance.access_token == "test_token"
    assert mock_client_instance.user_id == "@bot:example.org"
    assert mock_client_instance.device_id == discovered_device_id
    mock_save_credentials.assert_called_once()
    call_args = mock_save_credentials.call_args
    assert call_args[0][0] == {
        "homeserver": "https://matrix.example.org",
        "user_id": "@bot:example.org",
        "access_token": "test_token",
        "device_id": discovered_device_id,
    }
    assert call_args[1]["credentials_path"].endswith(CREDENTIALS_FILENAME)


@pytest.mark.asyncio
async def test_connect_matrix_uploads_keys_when_needed(monkeypatch):
    """Verify that the Matrix client uploads keys when the client's key-upload flag is enabled.

    Asserts that connect_matrix returns the created client and that the client's `keys_upload` coroutine is awaited exactly once when `should_upload_keys` is truthy.
    """
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.close = AsyncMock()
    type(mock_client).should_upload_keys = PropertyMock(return_value=True)
    mock_client.keys_upload = AsyncMock()
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
        "mmrelay.matrix_utils.is_e2ee_enabled", lambda _cfg: True, raising=False
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

    client = await connect_matrix()

    assert client is mock_client
    mock_client.keys_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_matrix_whoami_missing_device_id_warns(monkeypatch):
    """Missing device_id from whoami should warn and continue."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.whoami = AsyncMock(return_value=SimpleNamespace(device_id=None))

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
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
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=True),
        patch("builtins.open", new_callable=MagicMock),
        patch(
            "mmrelay.config.json.load",
            return_value={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "access_token": "token",
            },
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        config = {"matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}]}
        await connect_matrix(config)

    mock_logger.warning.assert_any_call("whoami response did not contain device_id")


@pytest.mark.asyncio
async def test_connect_matrix_whoami_failure_warns(monkeypatch):
    """whoami failures should warn and continue without a device_id."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.whoami = AsyncMock(side_effect=OSError("fail"))
    mock_client.close = AsyncMock()

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
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
        "mmrelay.matrix_utils.get_e2ee_status",
        lambda *_args, **_kwargs: {"overall_status": "ok"},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_room_encryption_warnings",
        lambda *_args, **_kwargs: [],
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=True),
        patch("builtins.open", new_callable=MagicMock),
        patch(
            "mmrelay.config.json.load",
            return_value={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "access_token": "token",
            },
        ),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        config = {"matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}]}
        await connect_matrix(config)

    assert any(
        "Failed to discover device_id via whoami" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )
    mock_logger.warning.assert_any_call(
        "E2EE may not work properly without a device_id"
    )


@pytest.mark.asyncio
async def test_connect_matrix_save_credentials_failure_warns(monkeypatch):
    """Save failures after whoami device_id discovery should warn."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.should_upload_keys = False
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )
    mock_client.whoami = AsyncMock(return_value=SimpleNamespace(device_id="DEV"))

    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
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
        "mmrelay.matrix_utils.get_e2ee_status",
        lambda *_args, **_kwargs: {"overall_status": "ok"},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_room_encryption_warnings",
        lambda *_args, **_kwargs: [],
        raising=False,
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)

    with (
        patch("mmrelay.matrix_utils.os.path.exists", return_value=True),
        patch("mmrelay.matrix_utils.os.path.isfile", return_value=True),
        patch("builtins.open", new_callable=MagicMock),
        patch(
            "mmrelay.config.json.load",
            return_value={
                "homeserver": "https://matrix.example.org",
                "user_id": "@bot:example.org",
                "access_token": "token",
            },
        ),
        patch("mmrelay.matrix_utils.save_credentials", side_effect=OSError("boom")),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        config = {"matrix_rooms": [{"id": "!room:example", "meshtastic_channel": 0}]}
        await connect_matrix(config)

    assert any(
        "Failed to persist updated session information" in call.args[0]
        for call in mock_logger.exception.call_args_list
    )


@pytest.mark.asyncio
async def test_connect_matrix_keys_upload_failure_logs(monkeypatch):
    """Key upload errors should be logged and not raise."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.sync = AsyncMock(return_value=SimpleNamespace())
    mock_client.close = AsyncMock()
    type(mock_client).should_upload_keys = PropertyMock(return_value=True)
    mock_client.keys_upload = AsyncMock(side_effect=asyncio.TimeoutError)
    mock_client.get_displayname = AsyncMock(
        return_value=SimpleNamespace(displayname="Bot")
    )

    monkeypatch.setattr(
        "mmrelay.config.is_e2ee_enabled", lambda _cfg: True, raising=False
    )
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_client", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.AsyncClient",
        lambda *_args, **_kwargs: mock_client,
    )
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
    mock_logger = MagicMock()
    monkeypatch.setattr(
        "mmrelay.matrix_utils.logger",
        mock_logger,
        raising=False,
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
        client = await connect_matrix(config)

    assert client is mock_client
    mock_logger.exception.assert_any_call(
        "Failed to upload E2EE keys. Consider regenerating credentials with: mmrelay auth login"
    )


class TestMatrixE2EEHasAttrChecks:
    """Test class for E2EE hasattr checks in matrix_utils.py"""

    @pytest.fixture
    def e2ee_config(self):
        """Create a minimal Matrix configuration dictionary with end-to-end encryption enabled for tests.

        The configuration contains a `matrix` section with homeserver, access token, bot user id, and `e2ee: {"enabled": True}`, and a `matrix_rooms` mapping with a sample room configured for `meshtastic_channel: 0`.

        Returns:
            dict: Test-ready Matrix configuration with E2EE enabled.
        """
        return {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
                "e2ee": {"enabled": True},
            },
            "matrix_rooms": {"!room:matrix.org": {"meshtastic_channel": 0}},
        }

    async def test_connect_matrix_hasattr_checks_success(self, e2ee_config):
        """Test hasattr checks for nio.crypto.OlmDevice and nio.store.SqliteStore when available"""
        with (
            patch("mmrelay.matrix_utils.matrix_client", None),
            patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
            patch("mmrelay.matrix_utils.logger"),
            patch(
                "mmrelay.matrix.auth.get_matrix_capabilities",
                return_value=_matrix_capabilities(),
            ) as mock_capabilities,
        ):
            # Mock AsyncClient instance with proper async methods
            mock_client_instance = MagicMock()
            mock_client_instance.rooms = {}
            mock_client_instance.login = AsyncMock(return_value=MagicMock())
            mock_client_instance.sync = AsyncMock(return_value=MagicMock())
            mock_client_instance.join = AsyncMock(return_value=MagicMock())
            mock_client_instance.close = AsyncMock()
            mock_client_instance.get_displayname = AsyncMock(
                return_value=MagicMock(displayname="TestBot")
            )
            mock_client_instance.keys_upload = AsyncMock()
            mock_async_client.return_value = mock_client_instance

            # Run the async function
            await connect_matrix(e2ee_config)

            # Verify client was created and E2EE dependencies were checked
            mock_async_client.assert_called_once()
            mock_capabilities.assert_called()

    async def test_connect_matrix_hasattr_checks_missing_olmdevice(self, e2ee_config):
        """Test hasattr check failure when nio.crypto.OlmDevice is missing"""
        with (
            patch("mmrelay.matrix_utils.matrix_client", None),
            patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
            patch("mmrelay.matrix_utils.logger") as mock_logger,
            patch(
                "mmrelay.matrix.auth.get_matrix_capabilities",
                return_value=_matrix_capabilities(encryption_available=False),
            ),
        ):
            # Mock AsyncClient instance with proper async methods
            mock_client_instance = MagicMock()
            mock_client_instance.rooms = {}
            mock_client_instance.login = AsyncMock(return_value=MagicMock())
            mock_client_instance.sync = AsyncMock(return_value=MagicMock())
            mock_client_instance.join = AsyncMock(return_value=MagicMock())
            mock_client_instance.close = AsyncMock()
            mock_client_instance.get_displayname = AsyncMock(
                return_value=MagicMock(displayname="TestBot")
            )
            mock_async_client.return_value = mock_client_instance

            # Run the async function
            await connect_matrix(e2ee_config)

            # Verify ImportError was logged and E2EE was disabled
            mock_logger.error.assert_any_call("Missing E2EE dependency")
            mock_logger.error.assert_any_call(
                "Install matrix-nio E2EE in a controlled replacement environment: "
                "pip install 'matrix-nio[e2e]==0.25.2'. "
                "Do not install mmrelay[e2e] (it uses mindroom-nio)."
            )
            mock_logger.warning.assert_called_with(
                "E2EE will be disabled for this session."
            )

    async def test_connect_matrix_hasattr_checks_missing_sqlitestore(self, e2ee_config):
        """Test hasattr check failure when nio.store.SqliteStore is missing"""
        with (
            patch("mmrelay.matrix_utils.matrix_client", None),
            patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
            patch("mmrelay.matrix_utils.logger") as mock_logger,
            patch(
                "mmrelay.matrix.auth.get_matrix_capabilities",
                return_value=_matrix_capabilities(encryption_available=False),
            ),
        ):
            # Mock AsyncClient instance with proper async methods
            mock_client_instance = MagicMock()
            mock_client_instance.rooms = {}
            mock_client_instance.login = AsyncMock(return_value=MagicMock())
            mock_client_instance.sync = AsyncMock(return_value=MagicMock())
            mock_client_instance.join = AsyncMock(return_value=MagicMock())
            mock_client_instance.close = AsyncMock()
            mock_client_instance.get_displayname = AsyncMock(
                return_value=MagicMock(displayname="TestBot")
            )
            mock_async_client.return_value = mock_client_instance

            # Run the async function
            await connect_matrix(e2ee_config)

            # Verify ImportError was logged and E2EE was disabled
            mock_logger.error.assert_any_call("Missing E2EE dependency")
            mock_logger.error.assert_any_call(
                "Install matrix-nio E2EE in a controlled replacement environment: "
                "pip install 'matrix-nio[e2e]==0.25.2'. "
                "Do not install mmrelay[e2e] (it uses mindroom-nio)."
            )
            mock_logger.warning.assert_called_with(
                "E2EE will be disabled for this session."
            )
