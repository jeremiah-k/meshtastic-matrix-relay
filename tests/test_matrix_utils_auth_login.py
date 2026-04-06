"""Tests for login_matrix_bot function."""

import asyncio
import logging
import ssl
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import mmrelay.matrix_utils as matrix_utils_module
from mmrelay.constants.app import CREDENTIALS_FILENAME
from mmrelay.matrix_utils import (
    NioLocalTransportError,
    NioLoginError,
    login_matrix_bot,
)

TEST_HOMESERVER = "https://matrix.org"
TEST_USERNAME = "user"
TEST_PASSWORD = "pass"
TEST_FULL_MXID = "@user:matrix.org"


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
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
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
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


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_uses_loaded_config_for_save_path(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    mock_save_credentials,
):
    """Both credentials path resolutions should use the loaded config mapping."""
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

    tmpdir = tempfile.gettempdir()
    credentials_path = os.path.join(tmpdir, "test-creds.json")
    loaded_config = {"matrix": {"credentials_path": credentials_path}}
    resolved_configs = []

    def _capture_explicit_path(config):
        """
        Capture the config passed to get_explicit_credentials_path and return the test path.

        This verifies that _resolve_credentials_save_path passes the correct config to
        get_explicit_credentials_path, which is the real behavior being tested.
        """
        resolved_configs.append(config)
        return credentials_path

    with (
        patch("mmrelay.config.load_config", return_value=loaded_config),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch(
            "mmrelay.matrix_utils.get_explicit_credentials_path",
            side_effect=_capture_explicit_path,
        ),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
            config_for_paths=None,
        )

    assert result is True
    assert resolved_configs == [loaded_config, loaded_config]
    assert (
        mock_save_credentials.call_args.kwargs["credentials_path"] == credentials_path
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_existing_credentials_and_e2ee_check_exceptions(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Credential load and E2EE-check failures should be logged and continue."""
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

    credentials_path = os.path.join(tempfile.gettempdir(), "creds.json")

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", side_effect=ValueError("bad-e2ee")),
        patch(
            "mmrelay.matrix_utils.get_credentials_path",
            return_value=Path(credentials_path),
        ),
        patch(
            "mmrelay.matrix_utils.os.path.exists", side_effect=OSError("exists-fail")
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="user",
            password="pass",
            logout_others=False,
            config_for_paths=None,
        )

    assert result is True
    assert any(
        "Could not load existing credentials" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )
    assert any(
        "Could not load config for E2EE check" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


import os  # noqa: E402


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_api_login_debug_path(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """API login debug path should parse and log request payload safely."""
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

    class DummyApi:
        @staticmethod
        def login(user, password, device_name, device_id=None):
            import json

            return (
                "POST",
                "/login",
                json.dumps(
                    {
                        "user": user,
                        "password": password,
                        "device_name": device_name,
                        "device_id": device_id,
                    }
                ),
            )

    with (
        patch.dict("sys.modules", {"nio.api": SimpleNamespace(Api=DummyApi)}),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    assert any(
        "Matrix API call details" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


@patch("mmrelay.matrix_utils.getpass.getpass")
@patch("mmrelay.matrix_utils.input")
async def test_login_matrix_bot_type_error_specific_message(mock_input, mock_getpass):
    """Type errors from matrix-nio should return False."""
    mock_input.side_effect = ["https://matrix.org", "user", "y"]
    mock_getpass.return_value = "pass"

    with (
        patch("mmrelay.matrix_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        mock_client = AsyncMock()
        mock_client.login.side_effect = TypeError(
            "'>=' not supported between instances of 'str' and 'int'"
        )
        mock_client.close = AsyncMock()
        mock_async_client.return_value = mock_client

        result = await login_matrix_bot()

    assert result is False
    assert any(
        "Matrix-nio library error during login" in call.args[0]
        for call in mock_logger.error.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_login_response_unexpected(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Unexpected login responses should return False."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token=None, status_code=None, message=None
    )
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is False
    assert any(
        "Unexpected login response" in call.args[0]
        for call in mock_logger.error.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_whoami_fallback_when_missing_user_id(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    mock_save_credentials,
):
    """Missing user_id from whoami should fall back to response user_id."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@fallback:matrix.org"
    )
    mock_main_client.whoami.return_value = MagicMock(user_id=None)
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    assert mock_save_credentials.call_args.args[0]["user_id"] == "@fallback:matrix.org"
    assert any(
        "whoami response did not include user_id; using login response user_id"
        in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_logout_others_warns(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Logout_others should warn that the feature is unimplemented."""
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
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=True,
        )

    assert result is True
    mock_logger.warning.assert_any_call("Logout others not yet implemented")


@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_save_credentials_failure_triggers_cleanup(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
):
    """Failures during save_credentials should trigger outer exception handling."""

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
    mock_main_client.close = AsyncMock(side_effect=OSError("close-fail"))

    with (
        patch(
            "mmrelay.matrix_utils.save_credentials", side_effect=NioLoginError("fail")
        ),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is False
    mock_logger.exception.assert_any_call("Error during login")
    assert any(
        "Ignoring error during client cleanup" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_login_timeout(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
):
    """Login timeouts should log and return False."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.side_effect = asyncio.TimeoutError
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is False
    assert any(
        "Login timed out after" in call.args[0]
        for call in mock_logger.exception.call_args_list
    )
    mock_main_client.close.assert_awaited_once()


@pytest.mark.parametrize(
    "exc, expected_log",
    [
        (ConnectionError("boom"), "Network connectivity issue detected."),
        (ssl.SSLError("bad cert"), "SSL/TLS certificate issue detected."),
        (type("DNSError", (Exception,), {})("dns"), "DNS resolution failed."),
        (
            ValueError("'user_id' is a required property"),
            "Matrix server response validation failed.",
        ),
    ],
)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_login_exception_guidance(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    exc,
    expected_log,
):
    """Login exceptions should emit targeted troubleshooting guidance."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.side_effect = exc
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is False
    assert any(
        expected_log in call.args[0] for call in mock_logger.error.call_args_list
    )
    mock_main_client.close.assert_awaited_once()


@pytest.mark.parametrize(
    "status_code, message, expected_log",
    [
        (401, "M_FORBIDDEN", "Authentication failed - invalid username or password."),
        (404, "M_NOT_FOUND", "User not found or homeserver not found."),
        (429, "M_LIMIT_EXCEEDED", "Rate limited - too many login attempts."),
        (
            500,
            "server error",
            "Matrix server error - the server is experiencing issues.",
        ),
        (
            "M_FORBIDDEN",
            "Invalid username or password",
            "Authentication failed - invalid username or password.",
        ),
        (418, "teapot", "Login failed for unknown reason."),
    ],
)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_login_response_status_codes(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    status_code,
    message,
    expected_log,
):
    """Status-coded login failures should log targeted guidance."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token=None, status_code=status_code, message=message
    )
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is False
    assert any(
        expected_log in call.args[0] for call in mock_logger.error.call_args_list
    )
    mock_main_client.close.assert_awaited_once()


@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
async def test_login_matrix_bot_forbidden_with_localpart_suggests_full_mxid(
    _mock_ssl_context,
    mock_async_client,
):
    """401/M_FORBIDDEN guidance should suggest full MXID when localpart was used."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token=None, status_code=401, message="M_FORBIDDEN"
    )
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is False
    assert any(
        "retry with a full Matrix ID" in call.args[0]
        for call in mock_logger.error.call_args_list
    )
    mock_main_client.close.assert_awaited_once()


@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
async def test_login_matrix_bot_forbidden_with_full_mxid_skips_full_mxid_hint(
    _mock_ssl_context,
    mock_async_client,
):
    """Full MXID input should not log redundant full-MXID retry guidance."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token=None, status_code=401, message="M_FORBIDDEN"
    )
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_FULL_MXID,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is False
    assert not any(
        "retry with a full Matrix ID" in call.args[0]
        for call in mock_logger.error.call_args_list
    )
    mock_main_client.close.assert_awaited_once()


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_whoami_exception_uses_fallback(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    mock_save_credentials,
):
    """whoami failures should warn and fall back to response user_id."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@fallback:matrix.org"
    )
    mock_main_client.whoami.side_effect = RuntimeError("whoami failed")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    assert mock_save_credentials.call_args.args[0]["user_id"] == "@fallback:matrix.org"
    assert any(
        "whoami call failed" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_whoami_oserror_uses_fallback(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    mock_save_credentials,
):
    """OSError from whoami should warn and fall back to login response user_id."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id="@fallback:matrix.org"
    )
    mock_main_client.whoami.side_effect = OSError("network down")
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    assert mock_save_credentials.call_args.args[0]["user_id"] == "@fallback:matrix.org"
    assert any(
        "whoami call failed" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
async def test_login_matrix_bot_saves_credentials_without_user_id_when_unknown(
    _mock_ssl_context,
    mock_async_client,
    mock_save_credentials,
):
    """If whoami and login response omit user_id, credentials should still save."""
    mock_discovery_client = AsyncMock()
    mock_main_client = AsyncMock()
    mock_async_client.side_effect = [mock_discovery_client, mock_main_client]

    mock_discovery_client.discovery_info.return_value = SimpleNamespace(
        homeserver_url="https://matrix.org"
    )
    mock_discovery_client.close = AsyncMock()
    mock_main_client.login.return_value = MagicMock(
        access_token="token", device_id="DEV", user_id=None
    )
    mock_main_client.whoami.return_value = MagicMock(user_id=None)
    mock_main_client.close = AsyncMock()

    with (
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    saved_credentials = mock_save_credentials.call_args.args[0]
    assert "user_id" not in saved_credentials
    assert any(
        "saving credentials without user_id" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_no_password_warns(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """Empty passwords should log a warning."""
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
        patch("mmrelay.matrix_utils.getpass.getpass", return_value=""),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=None,
            logout_others=False,
        )

    assert result is True
    mock_logger.warning.assert_any_call("No password provided")


@patch("mmrelay.matrix_utils.save_credentials")
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value="@user:matrix.org")
async def test_login_matrix_bot_api_login_debug_failure_logs(
    _mock_normalize,
    _mock_ssl_context,
    mock_async_client,
    _mock_save_credentials,
):
    """API debug failures should log and continue."""
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

    class DummyApi:
        @staticmethod
        def login(*_args, **_kwargs):
            raise RuntimeError("api boom")

    with (
        patch.dict("sys.modules", {"nio.api": SimpleNamespace(Api=DummyApi)}),
        patch("mmrelay.config.load_config", return_value={}),
        patch("mmrelay.config.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.os.path.exists", return_value=False),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await login_matrix_bot(
            homeserver=TEST_HOMESERVER,
            username=TEST_USERNAME,
            password=TEST_PASSWORD,
            logout_others=False,
        )

    assert result is True
    assert any(
        "Failed to test API call" in call.args[0]
        for call in mock_logger.error.call_args_list
    )
