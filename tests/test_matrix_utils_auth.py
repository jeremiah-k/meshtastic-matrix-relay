import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.cli_utils import _cleanup_local_session_data, logout_matrix_bot
from mmrelay.config import get_e2ee_store_dir, load_credentials, save_credentials
from mmrelay.matrix_utils import (
    NioLocalTransportError,
    _can_auto_create_credentials,
    connect_matrix,
    login_matrix_bot,
)


@pytest.fixture
def matrix_config(test_config):
    """
    Create a test configuration dictionary that includes Matrix credentials.

    Parameters:
        test_config (dict): Base configuration to copy and extend.

    Returns:
        config (dict): A shallow copy of `test_config` with a "matrix" key containing test homeserver, access token, and bot user id.
    """
    config = dict(test_config)
    config["matrix"] = {
        "homeserver": "https://matrix.org",
        "access_token": "test_token",
        "bot_user_id": "@test:matrix.org",
    }
    return config


# Matrix Connection Tests


@pytest.mark.asyncio
async def test_connect_matrix_success(matrix_config):
    """
    Test that a Matrix client connects successfully using the provided configuration.
    """
    with (
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.matrix_utils.logger") as _mock_logger,
        patch("mmrelay.matrix_utils._create_ssl_context") as mock_ssl_context,
    ):
        mock_ssl_context.return_value = MagicMock()
        mock_client_instance = MagicMock()
        mock_client_instance.rooms = {}

        async def mock_whoami():
            """
            Create a fake whoami response for tests.

            Returns:
                MagicMock: A mock object with a `device_id` attribute set to `"test_device_id"`.
            """
            return MagicMock(device_id="test_device_id")

        async def mock_sync(*args, **kwargs):
            """
            Provide an asynchronous MagicMock for use in tests.

            Returns:
                A MagicMock instance.
            """
            return MagicMock()

        async def mock_get_displayname(*args, **kwargs):
            """
            Create an async mock that simulates a client's get_displayname response.

            Returns:
                MagicMock: mock object with a `displayname` attribute set to "Test Bot".
            """
            return MagicMock(displayname="Test Bot")

        mock_client_instance.whoami = mock_whoami
        mock_client_instance.sync = mock_sync
        mock_client_instance.get_displayname = mock_get_displayname
        mock_async_client.return_value = mock_client_instance

        result = await connect_matrix(matrix_config)

        mock_async_client.assert_called_once()
        assert result == mock_client_instance


@pytest.mark.asyncio
async def test_connect_matrix_without_credentials(matrix_config):
    """
    Test that `connect_matrix` returns the Matrix client successfully when using legacy config without credentials.json.
    """
    with (
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.matrix_utils.logger") as _mock_logger,
        patch("mmrelay.matrix_utils._create_ssl_context") as mock_ssl_context,
    ):
        mock_ssl_context.return_value = MagicMock()
        mock_client_instance = MagicMock()
        mock_client_instance.rooms = {}
        mock_client_instance.device_id = None

        async def mock_sync(*args, **kwargs):
            """
            Provide an asynchronous MagicMock for use in tests.

            Returns:
                A MagicMock instance.
            """
            return MagicMock()

        async def mock_get_displayname(*args, **kwargs):
            """
            Create an async mock that simulates a client's get_displayname response.

            Returns:
                MagicMock: mock object with a `displayname` attribute set to "Test Bot".
            """
            return MagicMock(displayname="Test Bot")

        mock_client_instance.sync = mock_sync
        mock_client_instance.get_displayname = mock_get_displayname
        mock_async_client.return_value = mock_client_instance

        result = await connect_matrix(matrix_config)

        assert result == mock_client_instance


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.matrix_client", None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
@patch("mmrelay.matrix_utils.login_matrix_bot")
@patch("mmrelay.matrix_utils.async_load_credentials", new_callable=AsyncMock)
async def test_connect_matrix_alias_resolution_success(
    mock_load_credentials, mock_login_bot, _mock_logger, mock_async_client
):
    """
    Test that connect_matrix successfully resolves room aliases to room IDs.
    """
    with patch("mmrelay.matrix_utils._create_ssl_context") as mock_ssl_context:
        mock_ssl_context.return_value = MagicMock()
        mock_login_bot.return_value = True
        mock_load_credentials.return_value = {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "user_id": "@test:matrix.org",
            "device_id": "test_device_id",
        }

        mock_client_instance = MagicMock()
        mock_client_instance.rooms = {}

        async def mock_whoami():
            """
            Create a fake whoami response for tests.

            Returns:
                MagicMock: A mock object with a `device_id` attribute set to `"test_device_id"`.
            """
            return MagicMock(device_id="test_device_id")

        async def mock_sync(*_args, **_kwargs):
            """
            Provide an async-compatible replacement for a sync operation that yields a MagicMock.

            Returns:
                MagicMock: a mock object representing the result of the asynchronous operation.
            """
            return MagicMock()

        async def mock_get_displayname(*_args, **_kwargs):
            """
            Return a mock object exposing a `displayname` attribute set to "Test Bot".

            Returns:
                mock (MagicMock): A mock object whose `displayname` attribute equals "Test Bot".
            """
            return MagicMock(displayname="Test Bot")

        mock_room_resolve_alias = MagicMock()

        async def mock_room_resolve_alias_impl(_alias):
            """
            Create a mock response for resolving a room alias with a fixed resolved room ID.

            Parameters:
                _alias (str): Alias to resolve (ignored by this mock implementation).

            Returns:
                response: A mock object with `room_id` set to "!resolved:matrix.org" and `message` set to an empty string.
            """
            response = MagicMock()
            response.room_id = "!resolved:matrix.org"
            response.message = ""
            return response

        mock_room_resolve_alias.side_effect = mock_room_resolve_alias_impl

        mock_client_instance.whoami = mock_whoami
        mock_client_instance.sync = mock_sync
        mock_client_instance.get_displayname = mock_get_displayname
        mock_client_instance.room_resolve_alias = mock_room_resolve_alias
        mock_async_client.return_value = mock_client_instance

        config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "bot_user_id": "@test:matrix.org",
                "password": "test_password",
            },
            "matrix_rooms": [
                {"id": "#alias1:matrix.org", "meshtastic_channel": 1},
                {"id": "#alias2:matrix.org", "meshtastic_channel": 2},
            ],
        }

        result = await connect_matrix(config)

        mock_async_client.assert_called_once()
        assert result == mock_client_instance
        assert mock_client_instance.room_resolve_alias.call_count == 2
        assert config["matrix_rooms"][0]["id"] == "!resolved:matrix.org"
        assert config["matrix_rooms"][1]["id"] == "!resolved:matrix.org"


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.matrix_client", None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
@patch("mmrelay.matrix_utils.login_matrix_bot")
@patch("mmrelay.matrix_utils.async_load_credentials", new_callable=AsyncMock)
async def test_connect_matrix_alias_resolution_failure(
    mock_load_credentials, mock_login_bot, _mock_logger, mock_async_client
):
    """
    Test that connect_matrix handles alias resolution failures gracefully.
    """
    with patch("mmrelay.matrix_utils._create_ssl_context") as mock_ssl_context:
        mock_ssl_context.return_value = MagicMock()
        mock_login_bot.return_value = True
        mock_load_credentials.return_value = {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "user_id": "@test:matrix.org",
            "device_id": "test_device_id",
        }

        mock_client_instance = MagicMock()
        mock_client_instance.rooms = {}

        async def mock_whoami():
            """
            Create a fake whoami response for tests.

            Returns:
                MagicMock: A mock object with a `device_id` attribute set to `"test_device_id"`.
            """
            return MagicMock(device_id="test_device_id")

        async def mock_sync(*_args, **_kwargs):
            """
            Provide an async-compatible replacement for a sync operation that yields a MagicMock.

            Returns:
                MagicMock: a mock object representing the result of the asynchronous operation.
            """
            return MagicMock()

        async def mock_get_displayname(*_args, **_kwargs):
            """
            Return a mock object exposing a `displayname` attribute set to "Test Bot".

            Returns:
                mock (MagicMock): A mock object whose `displayname` attribute equals "Test Bot".
            """
            return MagicMock(displayname="Test Bot")

        mock_room_resolve_alias = MagicMock()

        async def mock_room_resolve_alias_impl(_alias):
            """
            Simulate a failed room alias resolution by returning a mock response with no room_id and an error message.

            Parameters:
                _alias (str): The alias to resolve (ignored by this implementation).

            Returns:
                MagicMock: A mock response object with `room_id` set to `None` and `message` set to "Room not found".
            """
            response = MagicMock()
            response.room_id = None
            response.message = "Room not found"
            return response

        mock_room_resolve_alias.side_effect = mock_room_resolve_alias_impl

        mock_client_instance.whoami = mock_whoami
        mock_client_instance.sync = mock_sync
        mock_client_instance.get_displayname = mock_get_displayname
        mock_client_instance.room_resolve_alias = mock_room_resolve_alias
        mock_async_client.return_value = mock_client_instance

        config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "bot_user_id": "@test:matrix.org",
                "password": "test_password",
            },
            "matrix_rooms": [{"id": "#invalid:matrix.org", "meshtastic_channel": 1}],
        }

        result = await connect_matrix(config)

        mock_async_client.assert_called_once()
        assert result == mock_client_instance
        mock_client_instance.room_resolve_alias.assert_called_once_with(
            "#invalid:matrix.org"
        )
        assert any(
            "Could not resolve alias #invalid:matrix.org" in call.args[0]
            for call in _mock_logger.warning.call_args_list
        )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.os.makedirs")
@patch("mmrelay.matrix_utils.os.listdir")
@patch("mmrelay.matrix_utils.os.path.exists")
@patch("mmrelay.matrix_utils.os.path.isfile")
@patch("builtins.open")
@patch("mmrelay.config.json.load")
@patch("mmrelay.matrix_utils._create_ssl_context")
@patch("mmrelay.matrix_utils.matrix_client", None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_connect_matrix_with_e2ee_credentials(
    _mock_logger,
    mock_async_client,
    mock_ssl_context,
    mock_json_load,
    mock_open,
    mock_exists,
    mock_isfile,
    mock_listdir,
    _mock_makedirs,
):
    """Test Matrix connection with E2EE credentials."""
    mock_exists.return_value = True
    mock_isfile.return_value = True
    mock_json_load.return_value = {
        "homeserver": "https://matrix.example.org",
        "user_id": "@bot:example.org",
        "access_token": "test_token",
        "device_id": "TEST_DEVICE",
    }
    mock_listdir.return_value = ["test.db"]
    mock_ssl_context.return_value = MagicMock()

    mock_client_instance = MagicMock()
    mock_client_instance.rooms = {}

    async def mock_sync(*args, **kwargs):
        """
        Async test helper that provides a MagicMock instance as a stand-in for a synchronous sync result.

        Returns:
            MagicMock: A MagicMock instance.
        """
        return MagicMock()

    async def mock_whoami(*args, **kwargs):
        """
        Provide a mocked 'whoami' response for tests with a fixed device identifier.

        Returns:
            MagicMock: An object with a `device_id` attribute set to "TEST_DEVICE".
        """
        return MagicMock(device_id="TEST_DEVICE")

    async def mock_keys_upload(*args, **kwargs):
        """
        Create an awaitable used in tests to simulate a keys upload operation.

        Returns:
            MagicMock: mock object representing the result of the upload.
        """
        return MagicMock()

    async def mock_get_displayname(*args, **kwargs):
        """
        Provide a MagicMock object with a fixed display name of "Test Bot".

        Returns:
            MagicMock: A MagicMock instance whose `displayname` attribute is "Test Bot".
        """
        return MagicMock(displayname="Test Bot")

    mock_client_instance.sync = mock_sync
    mock_client_instance.whoami = mock_whoami
    mock_client_instance.load_store = MagicMock()
    mock_client_instance.should_upload_keys = True
    mock_client_instance.keys_upload = mock_keys_upload
    mock_client_instance.get_displayname = mock_get_displayname
    mock_async_client.return_value = mock_client_instance

    config = {
        "matrix": {
            "homeserver": "https://matrix.example.org",
            "bot_user_id": "@bot:example.org",
            "e2ee": {"enabled": True},
        },
        "matrix_rooms": [],
    }

    await connect_matrix(config)
    mock_async_client.assert_called_once()


# Login Tests


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.input")
@patch("mmrelay.cli_utils._create_ssl_context")
@patch("mmrelay.matrix_utils.getpass.getpass")
@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
async def test_login_matrix_bot_success(
    mock_async_client,
    mock_save_credentials,
    _mock_getpass,
    mock_ssl_context,
    _mock_input,
):
    """Test successful login_matrix_bot execution."""
    _mock_input.side_effect = [
        "https://matrix.org",  # homeserver
        "testuser",  # username
        "y",  # logout_others
    ]
    _mock_getpass.return_value = "testpass"
    mock_ssl_context.return_value = None

    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = MagicMock(
        homeserver_url="https://matrix.org"
    )
    mock_main_client.login.return_value = MagicMock(
        access_token="test_token",
        device_id="test_device",
        user_id="@testuser:matrix.org",
    )

    result = await login_matrix_bot()

    assert result is True
    mock_save_credentials.assert_called_once()
    mock_discovery_client.discovery_info.assert_awaited_once()
    mock_discovery_client.close.assert_awaited_once()
    mock_main_client.login.assert_awaited_once()
    mock_main_client.close.assert_awaited_once()
    assert mock_async_client.call_count == 2


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.input")
async def test_login_matrix_bot_with_parameters(mock_input):
    """Test login_matrix_bot with provided parameters."""
    with (
        patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
    ):
        mock_client = AsyncMock()
        mock_client.login.return_value = MagicMock(
            access_token="test_token",
            device_id="test_device",
            user_id="@testuser:matrix.org",
        )
        mock_client.whoami.return_value = MagicMock(user_id="@testuser:matrix.org")
        mock_client.close = AsyncMock()
        mock_async_client.return_value = mock_client

        with patch("mmrelay.matrix_utils.save_credentials"):
            result = await login_matrix_bot(
                homeserver="https://matrix.org",
                username="testuser",
                password="testpass",
            )
            assert result is True
            mock_input.assert_not_called()


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.getpass.getpass")
@patch("mmrelay.matrix_utils.input")
async def test_login_matrix_bot_login_failure(mock_input, mock_getpass):
    """Test login_matrix_bot when login fails."""
    mock_input.side_effect = ["https://matrix.org", "testuser", "y"]
    mock_getpass.return_value = "wrongpass"

    with (
        patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
    ):
        mock_client = AsyncMock()
        mock_client.login.side_effect = Exception("Login failed")
        mock_client.close = AsyncMock()
        mock_async_client.return_value = mock_client

        result = await login_matrix_bot()

        assert result is False
        assert mock_client.close.call_count == 2


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_adds_scheme_and_discovery_timeout(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Homeserver should gain https:// prefix and discovery timeout should fall back."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.side_effect = asyncio.TimeoutError
    mock_discovery_client.close = AsyncMock()

    mock_main_client.login.return_value = MagicMock(
        access_token="token",
        device_id="dev",
        user_id="@user:matrix.org",
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is True
    assert mock_async_client.call_args_list[0].args[0] == "https://matrix.org"
    assert mock_async_client.call_args_list[1].args[0] == "https://matrix.org"


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_discovery_response_with_homeserver_url_attribute(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Discovery responses with homeserver_url attribute should update homeserver."""

    class DummyResponse:
        pass

    class DummyError:
        pass

    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://actual.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="dev", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.matrix_utils.DiscoveryInfoResponse", DummyResponse),
        patch("mmrelay.matrix_utils.DiscoveryInfoError", DummyError),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is True
    assert mock_async_client.call_args_list[1].args[0] == "https://actual.org"


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_discovery_response_unexpected_no_attr(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Unexpected discovery responses without homeserver_url should warn and continue."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = object()
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="dev", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.matrix_utils.DiscoveryInfoResponse", type("Resp", (), {})),
        patch("mmrelay.matrix_utils.DiscoveryInfoError", type("Err", (), {})),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is True
    assert any(
        "Server discovery returned unexpected response type" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.cli_utils._create_ssl_context", return_value=None)
async def test_login_matrix_bot_username_normalization_failure_returns_false(
    _mock_ssl_context, mock_async_client
):
    """Normalization failures should return False early."""
    mock_discovery_client = AsyncMock()
    mock_async_client.return_value = mock_discovery_client
    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()

    with (
        patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value=None),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is False
    mock_logger.error.assert_any_call("Username normalization failed")


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_debug_env_sets_log_levels(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """MMRELAY_DEBUG_NIO should enable debug logging for nio/aiohttp loggers."""
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

    logger_instances = {}

    def fake_get_logger(name):
        """
        Return a mock logger instance associated with the given name.

        Parameters:
            name (str): The logger name/key to retrieve.

        Returns:
            MagicMock: A MagicMock acting as a logger for `name`. The instance is cached and the same object is returned on subsequent calls with the same name.
        """
        logger = logger_instances.setdefault(name, MagicMock())
        return logger

    with (
        patch("mmrelay.matrix_utils.os.getenv", return_value="1"),
        patch("mmrelay.matrix_utils.logging.getLogger", side_effect=fake_get_logger),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is True
    for name in ("nio", "nio.client", "nio.http_client", "nio.responses", "aiohttp"):
        logger_instances[name].setLevel.assert_called_once_with(logging.DEBUG)


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_discovery_type_error_logs_warning(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Type errors during discovery response handling should warn and continue."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = object()
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@user:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id="@user:matrix.org")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.matrix_utils.DiscoveryInfoResponse", "not-a-type"),
        patch("mmrelay.matrix_utils.DiscoveryInfoError", "not-a-type"),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is True
    assert any(
        "Server discovery error" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_cleanup_error_logs_debug(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
):
    """Cleanup errors during login failure should be logged at debug."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.side_effect = NioLocalTransportError("fail")
    mock_main_client.close = AsyncMock(side_effect=ConnectionError("cleanup fail"))

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
        )

    assert result is False
    assert any(
        "Ignoring error during client cleanup" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
async def test_login_matrix_bot_username_warnings(
    _mock_ssl_context, mock_async_client, _mock_save_credentials
):
    """Usernames with unusual characters should emit warnings."""
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
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user!bad",
            password="pass",
            logout_others=False,
        )
        assert any(
            "Username contains unusual characters" in call.args[0]
            for call in mock_logger.warning.call_args_list
        )


# Logout Tests


@pytest.mark.asyncio
@patch("mmrelay.cli_utils.AsyncClient", MagicMock(spec=True))
async def test_logout_matrix_bot_no_credentials():
    """Test logout when no credentials exist."""
    with patch(
        "mmrelay.config.async_load_credentials", new=AsyncMock(return_value=None)
    ):
        result = await logout_matrix_bot(password="test_password")
        assert result is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "credentials",
    [
        pytest.param({"user_id": "test"}, id="missing_homeserver"),
        pytest.param({"homeserver": "matrix.org"}, id="missing_user_id"),
    ],
)
@patch("mmrelay.cli_utils.AsyncClient", MagicMock(spec=True))
@patch("mmrelay.cli_utils._cleanup_local_session_data", return_value=True)
async def test_logout_matrix_bot_invalid_credentials(mock_cleanup, credentials):
    """Test logout with invalid/incomplete credentials falls back to local cleanup."""
    with patch(
        "mmrelay.config.async_load_credentials", new=AsyncMock(return_value=credentials)
    ):
        result = await logout_matrix_bot(password="test_password")
        assert result is True
        mock_cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_logout_matrix_bot_password_verification_success():
    """Test successful logout with password verification."""
    mock_credentials = {
        "homeserver": "https://matrix.org",
        "user_id": "@test:matrix.org",
        "access_token": "test_token",
        "device_id": "test_device",
    }

    with (
        patch(
            "mmrelay.config.async_load_credentials",
            new=AsyncMock(return_value=mock_credentials),
        ),
        patch("mmrelay.cli_utils.AsyncClient") as mock_async_client,
        patch(
            "mmrelay.cli_utils._cleanup_local_session_data", return_value=True
        ) as mock_cleanup,
        patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
    ):
        mock_temp_client = AsyncMock()
        mock_temp_client.login.return_value = MagicMock(access_token="temp_token")
        mock_temp_client.logout = AsyncMock()
        mock_temp_client.close = AsyncMock()

        mock_main_client = AsyncMock()
        mock_main_client.restore_login = MagicMock()
        mock_main_client.logout.return_value = MagicMock(transport_response=True)
        mock_main_client.close = AsyncMock()

        mock_async_client.side_effect = [mock_temp_client, mock_main_client]

        result = await logout_matrix_bot(password="test_password")

        assert result is True
        mock_temp_client.login.assert_called_once()
        mock_temp_client.logout.assert_called_once()
        mock_main_client.logout.assert_called_once()
        mock_cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_logout_matrix_bot_password_verification_failure():
    """Test logout with failed password verification."""
    mock_credentials = {
        "homeserver": "https://matrix.org",
        "user_id": "@test:matrix.org",
        "access_token": "test_token",
        "device_id": "test_device",
    }

    with (
        patch(
            "mmrelay.config.async_load_credentials",
            new=AsyncMock(return_value=mock_credentials),
        ),
        patch("mmrelay.cli_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
    ):
        mock_temp_client = AsyncMock()
        mock_temp_client.login.side_effect = Exception("Invalid password")
        mock_temp_client.close = AsyncMock()
        mock_async_client.return_value = mock_temp_client

        result = await logout_matrix_bot(password="wrong_password")

        assert result is False
        mock_temp_client.login.assert_called_once()
        mock_temp_client.close.assert_called_once()


@pytest.mark.asyncio
async def test_logout_matrix_bot_server_logout_failure():
    """Test logout when server logout fails but local cleanup succeeds."""
    mock_credentials = {
        "homeserver": "https://matrix.org",
        "user_id": "@test:matrix.org",
        "access_token": "test_token",
        "device_id": "test_device",
    }

    with (
        patch(
            "mmrelay.config.async_load_credentials",
            new=AsyncMock(return_value=mock_credentials),
        ),
        patch("mmrelay.cli_utils.AsyncClient") as mock_async_client,
        patch(
            "mmrelay.cli_utils._cleanup_local_session_data", return_value=True
        ) as mock_cleanup,
        patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
    ):
        mock_temp_client = AsyncMock()
        mock_temp_client.login.return_value = MagicMock(access_token="temp_token")
        mock_temp_client.logout = AsyncMock()
        mock_temp_client.close = AsyncMock()

        mock_main_client = AsyncMock()
        mock_main_client.restore_login = MagicMock()
        mock_main_client.logout.side_effect = Exception("Server error")
        mock_main_client.close = AsyncMock()

        mock_async_client.side_effect = [mock_temp_client, mock_main_client]

        result = await logout_matrix_bot(password="test_password")

        assert result is True
        mock_cleanup.assert_called_once()


# Credential Management Tests


@patch("mmrelay.config.os.makedirs")
def test_get_e2ee_store_dir(mock_makedirs):
    """Test E2EE store directory creation."""
    store_dir = get_e2ee_store_dir()
    assert store_dir is not None
    assert "store" in store_dir
    mock_makedirs.assert_called_once()


@patch("mmrelay.config.get_credentials_path")
@patch("os.path.exists")
@patch("builtins.open")
@patch("mmrelay.config.json.load")
def test_load_credentials_success(
    mock_json_load, _mock_open, mock_exists, mock_get_credentials_path
):
    """Test successful credentials loading."""
    mock_get_credentials_path.return_value = Path("/test/config/credentials.json")
    mock_exists.return_value = True
    mock_json_load.return_value = {
        "homeserver": "https://matrix.example.org",
        "user_id": "@bot:example.org",
        "access_token": "test_token",
        "device_id": "TEST_DEVICE",
    }

    credentials = load_credentials()

    assert credentials is not None
    assert credentials["homeserver"] == "https://matrix.example.org"
    assert credentials["user_id"] == "@bot:example.org"
    assert credentials["access_token"] == "test_token"
    assert credentials["device_id"] == "TEST_DEVICE"


@patch("mmrelay.config.get_credentials_path")
@patch("os.path.exists")
def test_load_credentials_file_not_exists(mock_exists, mock_get_credentials_path):
    """Test credentials loading when file doesn't exist."""
    mock_get_credentials_path.return_value = Path("/test/config/credentials.json")
    mock_exists.return_value = False

    credentials = load_credentials()

    assert credentials is None


@patch("mmrelay.config.get_credentials_path")
@patch("builtins.open")
@patch("mmrelay.config.json.dump")
@patch("os.makedirs")  # Mock the directory creation
@patch("os.path.exists", return_value=True)  # Mock file existence check
def test_save_credentials(
    _mock_exists,
    _mock_makedirs,
    mock_json_dump,
    _mock_open,
    mock_get_credentials_path,
):
    """
    Verify that save_credentials writes the provided credentials JSON to the resolved config directory.

    This test sets the module-level config_path to None to force resolution via the base directory fixture, then calls save_credentials with a credentials dict and asserts that the target directory is created, the credentials file is opened, and json.dump is called with the credentials and an indent of 2.
    """
    mock_get_credentials_path.return_value = Path("/test/config/credentials.json")
    import mmrelay.config as config_module

    original_config_path = config_module.config_path
    config_module.config_path = None

    test_credentials = {
        "homeserver": "https://matrix.example.org",
        "user_id": "@bot:example.org",
        "access_token": "test_token",
        "device_id": "TEST_DEVICE",
    }

    try:
        save_credentials(test_credentials)
    finally:
        config_module.config_path = original_config_path

    _mock_makedirs.assert_called_once_with("/test/config", exist_ok=True)
    _mock_open.assert_called_once()
    mock_json_dump.assert_called_once_with(
        test_credentials, _mock_open().__enter__(), indent=2
    )


# Cleanup & Auto-create Tests


def test_cleanup_local_session_data_success():
    """Test successful cleanup of local session data."""
    with (
        patch(
            "mmrelay.paths.resolve_all_paths",
            return_value={
                "credentials_path": "/test/config/credentials.json",
                "store_dir": "/test/store",
            },
        ),
        patch("mmrelay.config.load_config", return_value={}),
        patch("os.path.exists") as mock_exists,
        patch("os.remove") as mock_remove,
        patch("shutil.rmtree") as mock_rmtree,
    ):
        mock_exists.return_value = True

        result = _cleanup_local_session_data()

        assert result is True
        mock_remove.assert_called_once_with("/test/config/credentials.json")
        mock_rmtree.assert_called_once_with("/test/store")


def test_cleanup_local_session_data_files_not_exist():
    """Test cleanup when files don't exist."""
    with (
        patch(
            "mmrelay.paths.resolve_all_paths",
            return_value={
                "credentials_path": "/test/config/credentials.json",
                "store_dir": "/test/store",
            },
        ),
        patch("mmrelay.config.load_config", return_value={}),
        patch("os.path.exists", return_value=False),
    ):
        result = _cleanup_local_session_data()

        assert result is True


def test_cleanup_local_session_data_permission_error():
    """Test cleanup with permission errors."""
    with (
        patch(
            "mmrelay.paths.resolve_all_paths",
            return_value={
                "credentials_path": "/test/config/credentials.json",
                "store_dir": "/test/store",
            },
        ),
        patch("mmrelay.config.load_config", return_value={}),
        patch("os.path.exists", return_value=True),
        patch("os.remove", side_effect=PermissionError("Access denied")),
        patch("shutil.rmtree", side_effect=PermissionError("Access denied")),
    ):
        result = _cleanup_local_session_data()

        assert result is False


def test_can_auto_create_credentials_success():
    """Test successful detection of auto-create capability."""
    matrix_config = {
        "homeserver": "https://matrix.example.org",
        "bot_user_id": "@bot:example.org",
        "password": "test_password",
    }

    result = _can_auto_create_credentials(matrix_config)
    assert result is True


@pytest.mark.parametrize(
    "invalid_config",
    [
        pytest.param(
            {
                "homeserver": "https://matrix.example.org",
                "bot_user_id": None,
                "password": "test_password",
            },
            id="none_bot_user_id",
        ),
        pytest.param(
            {
                "homeserver": None,
                "bot_user_id": "@bot:matrix.org",
                "password": "password123",
            },
            id="none_homeserver",
        ),
        pytest.param(
            {
                "homeserver": "https://matrix.org",
                "bot_user_id": None,
                "password": "password123",
            },
            id="none_bot_user_id_alt",
        ),
    ],
)
def test_can_auto_create_credentials_with_invalid_values(invalid_config):
    """
    Test _can_auto_create_credentials returns False when values are None.
    """
    result = _can_auto_create_credentials(invalid_config)
    assert result is False


@pytest.mark.asyncio
async def test_logout_matrix_bot_missing_user_id_fetch_success():
    """Test logout when user_id is missing but can be fetched via whoami()."""
    mock_credentials = {
        "homeserver": "https://matrix.org",
        "access_token": "test_token",
        "device_id": "test_device",
    }

    with (
        patch(
            "mmrelay.config.async_load_credentials",
            new=AsyncMock(return_value=mock_credentials.copy()),
        ),
        patch("mmrelay.cli_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.config.save_credentials") as mock_save_credentials,
        patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
        patch(
            "mmrelay.cli_utils._cleanup_local_session_data", return_value=True
        ) as mock_cleanup,
    ):
        mock_whoami_client = AsyncMock()
        mock_whoami_client.close = AsyncMock()
        mock_whoami_response = MagicMock()
        mock_whoami_response.user_id = "@fetched:matrix.org"
        mock_whoami_client.whoami.return_value = mock_whoami_response

        mock_password_client = AsyncMock()
        mock_password_client.close = AsyncMock()
        mock_password_client.login = AsyncMock(
            return_value=MagicMock(access_token="temp_token")
        )
        mock_password_client.logout = AsyncMock()

        mock_main_client = AsyncMock()
        mock_main_client.restore_login = MagicMock()
        mock_main_client.logout = AsyncMock(
            return_value=MagicMock(transport_response="success")
        )
        mock_main_client.close = AsyncMock()

        mock_async_client.side_effect = [
            mock_whoami_client,
            mock_password_client,
            mock_main_client,
        ]

        result = await logout_matrix_bot(password="test_password")

        assert result is True
        mock_whoami_client.whoami.assert_called_once()
        expected_credentials = mock_credentials.copy()
        expected_credentials["user_id"] = "@fetched:matrix.org"
        mock_save_credentials.assert_called_once_with(expected_credentials)
        mock_password_client.login.assert_called_once()
        mock_main_client.logout.assert_called_once()
        mock_cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_logout_matrix_bot_timeout():
    """Test logout with timeout during password verification."""
    mock_credentials = {
        "homeserver": "https://matrix.org",
        "user_id": "@test:matrix.org",
        "access_token": "test_token",
        "device_id": "test_device",
    }

    with (
        patch(
            "mmrelay.config.async_load_credentials",
            new=AsyncMock(return_value=mock_credentials),
        ),
        patch("mmrelay.cli_utils.AsyncClient") as mock_async_client,
        patch("asyncio.wait_for") as mock_wait_for,
        patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
    ):
        mock_temp_client = AsyncMock()
        mock_temp_client.close = AsyncMock()
        mock_async_client.return_value = mock_temp_client
        mock_wait_for.side_effect = asyncio.TimeoutError()

        result = await logout_matrix_bot(password="test_password")

    assert result is False
    mock_temp_client.close.assert_called_once()
