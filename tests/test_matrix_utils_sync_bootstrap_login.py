"""Tests for login_matrix_bot edge cases in sync_bootstrap.

Covers discovery, credential reuse, login error paths,
and various error handling branches.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.matrix_utils import login_matrix_bot

TEST_CREDS_PATH = str(Path(tempfile.gettempdir()) / "creds.json")
TEST_E2EE_STORE_PATH = str(Path(tempfile.gettempdir()) / "e2ee_store")


def _make_login_bot_mocks():
    mock_temp = MagicMock()
    mock_temp.discovery_info = AsyncMock(side_effect=Exception("skip discovery"))
    mock_temp.close = AsyncMock()

    return mock_temp


def _make_logged_in_client(**login_overrides):
    mock_client = MagicMock()
    defaults = {
        "access_token": "token",
        "user_id": "@bot:matrix.org",
        "device_id": "DEV1",
    }
    defaults.update(login_overrides)
    mock_response = MagicMock(**defaults)
    mock_client.login = AsyncMock(return_value=mock_response)
    mock_client.whoami = AsyncMock(return_value=MagicMock(user_id="@bot:matrix.org"))
    mock_client.close = AsyncMock()
    return mock_client, mock_response


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_timeout_error(mock_logger, mock_async_client, mock_ssl):
    mock_client = MagicMock()
    mock_client.login = AsyncMock(side_effect=asyncio.TimeoutError())
    mock_client.close = AsyncMock()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is False
    mock_client.close.assert_awaited()


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_type_error_known_issue(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client = MagicMock()
    mock_client.login = AsyncMock(
        side_effect=TypeError("'>=' not supported between instances of 'str' and 'int'")
    )
    mock_client.close = AsyncMock()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is False
    assert any(
        "known issue" in str(call.args[0]) for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_type_error_other(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client = MagicMock()
    mock_client.login = AsyncMock(side_effect=TypeError("some other type error"))
    mock_client.close = AsyncMock()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is False


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_ssl_error(mock_logger, mock_async_client, mock_ssl):
    import ssl

    mock_client = MagicMock()
    mock_client.login = AsyncMock(side_effect=ssl.SSLError("SSL error"))
    mock_client.close = AsyncMock()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is False
    assert any(
        "SSL" in str(call.args[0]) or "TLS" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_generic_exception(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client = MagicMock()
    mock_client.login = AsyncMock(side_effect=RuntimeError("unexpected"))
    mock_client.close = AsyncMock()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is False


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_no_access_token_401(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.access_token = None
    mock_response.status_code = 401
    mock_response.message = "Invalid password"
    mock_client.login = AsyncMock(return_value=mock_response)
    mock_client.close = AsyncMock()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is False
    assert any(
        "Authentication failed" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_login_failed_404(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.access_token = None
    mock_response.status_code = 404
    mock_response.message = "Not found"
    mock_client.login = AsyncMock(return_value=mock_response)
    mock_client.close = AsyncMock()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is False
    assert any(
        "not found" in str(call.args[0]).lower()
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_login_failed_429(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.access_token = None
    mock_response.status_code = 429
    mock_response.message = "Rate limited"
    mock_client.login = AsyncMock(return_value=mock_response)
    mock_client.close = AsyncMock()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is False
    assert any(
        "Rate limited" in str(call.args[0]) for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_login_failed_500(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.access_token = None
    mock_response.status_code = 500
    mock_response.message = "Internal server error"
    mock_client.login = AsyncMock(return_value=mock_response)
    mock_client.close = AsyncMock()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is False
    assert any(
        "server error" in str(call.args[0]).lower()
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_login_failed_no_status_code(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.access_token = None
    del mock_response.status_code
    del mock_response.message
    mock_client.login = AsyncMock(return_value=mock_response)
    mock_client.close = AsyncMock()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is False
    assert any(
        "Unexpected login response" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_m_forbidden_status(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.access_token = None
    mock_response.status_code = "M_FORBIDDEN"
    mock_response.message = "M_FORBIDDEN"
    mock_client.login = AsyncMock(return_value=mock_response)
    mock_client.close = AsyncMock()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="bot",
            password="wrong",
            logout_others=False,
            config_for_paths={},
        )

    assert result is False
    assert any(
        "Authentication failed" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_whoami_fallback(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client, _ = _make_logged_in_client()
    mock_client.whoami = AsyncMock(
        return_value=MagicMock(user_id="@discovered:matrix.org")
    )
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is True


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_no_credentials_path(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client, _ = _make_logged_in_client()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch("mmrelay.matrix_utils._resolve_credentials_save_path", return_value=None),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is False
    mock_logger.error.assert_any_call("Could not resolve credentials save path")


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_discovery_timeout(
    mock_logger, mock_async_client, mock_ssl
):
    mock_temp = MagicMock()
    mock_temp.discovery_info = AsyncMock(side_effect=asyncio.TimeoutError())
    mock_temp.close = AsyncMock()

    mock_client, _ = _make_logged_in_client()
    mock_async_client.side_effect = [mock_temp, mock_client]

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is True
    assert any(
        "timed out" in str(call.args[0]).lower()
        for call in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_discovery_exception(
    mock_logger, mock_async_client, mock_ssl
):
    mock_temp = MagicMock()
    mock_temp.discovery_info = AsyncMock(side_effect=Exception("network error"))
    mock_temp.close = AsyncMock()

    mock_client, _ = _make_logged_in_client()
    mock_async_client.side_effect = [mock_temp, mock_client]

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is True


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_discovery_info_success(
    mock_logger, mock_async_client, mock_ssl
):
    mock_temp = MagicMock()
    mock_temp.discovery_info = AsyncMock(
        return_value=MagicMock(homeserver_url="https://federated.matrix.org")
    )
    mock_temp.close = AsyncMock()

    mock_client, _ = _make_logged_in_client()
    mock_async_client.side_effect = [mock_temp, mock_client]

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is True


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_discovery_info_error_response(
    mock_logger, mock_async_client, mock_ssl
):
    mock_temp = MagicMock()
    mock_error_resp = MagicMock()
    mock_temp.discovery_info = AsyncMock(return_value=mock_error_resp)
    mock_temp.close = AsyncMock()

    mock_client, _ = _make_logged_in_client()
    mock_async_client.side_effect = [mock_temp, mock_client]

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch(
            "mmrelay.matrix_utils.DiscoveryInfoResponse",
            type("DiscoveryInfoResponse", (), {}),
        ),
        patch(
            "mmrelay.matrix_utils.DiscoveryInfoError",
            type("DiscoveryInfoError", (), {}),
        ),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is True


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_e2ee_store_path_creation(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client, _ = _make_logged_in_client()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=True),
        patch(
            "mmrelay.matrix_utils.get_e2ee_store_dir", return_value=TEST_E2EE_STORE_PATH
        ),
        patch("os.makedirs") as mock_makedirs,
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is True
    mock_makedirs.assert_any_call(TEST_E2EE_STORE_PATH, exist_ok=True)


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_existing_device_id_reuse(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client, _ = _make_logged_in_client()
    mock_async_client.return_value = mock_client

    existing_creds = {
        "homeserver": "https://matrix.org",
        "user_id": "@bot:matrix.org",
        "device_id": "EXISTING_DEV",
        "access_token": "old_token",
    }

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("os.path.exists", return_value=True),
        patch("builtins.open", MagicMock()),
        patch("json.load", return_value=existing_creds),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is True
    assert any(
        "Reusing existing device_id" in str(call.args[0])
        for call in mock_logger.info.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_ssl_context_none_warning(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client, _ = _make_logged_in_client()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is True
    assert any(
        "Failed to create SSL context" in str(call.args[0])
        for call in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_homeserver_with_no_scheme(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client, _ = _make_logged_in_client()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is True


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.input")
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_interactive_prompt(
    mock_logger, mock_async_client, mock_ssl, mock_input
):
    mock_input.side_effect = [
        "https://matrix.org",
        "@bot:matrix.org",
        "n",
    ]

    mock_temp = MagicMock()
    mock_temp.discovery_info = AsyncMock(side_effect=Exception("skip"))
    mock_temp.close = AsyncMock()

    mock_client, _ = _make_logged_in_client()
    mock_async_client.side_effect = [mock_temp, mock_client]

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
        patch("mmrelay.matrix_utils.getpass.getpass", return_value="password"),
    ):
        result = await login_matrix_bot(config_for_paths={})

    assert result is True


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_user_id_required_property_error(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client = MagicMock()
    mock_client.login = AsyncMock(
        side_effect=Exception("'user_id' is a required property")
    )
    mock_client.close = AsyncMock()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is False
    assert any(
        "server response validation failed" in str(call.args[0]).lower()
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_whoami_no_user_id_fallback(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.access_token = "token"
    mock_response.user_id = "@bot:matrix.org"
    mock_response.device_id = "DEV"
    mock_client.login = AsyncMock(return_value=mock_response)
    mock_client.whoami = AsyncMock(return_value=MagicMock(user_id=None))
    mock_client.close = AsyncMock()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is True
    assert any(
        "whoami response did not include user_id" in str(call.args[0])
        for call in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_whoami_exception_fallback(
    mock_logger, mock_async_client, mock_ssl
):
    from mmrelay.matrix_utils import NioLocalTransportError

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.access_token = "token"
    mock_response.user_id = "@bot:matrix.org"
    mock_response.device_id = "DEV"
    mock_client.login = AsyncMock(return_value=mock_response)
    mock_client.whoami = AsyncMock(side_effect=NioLocalTransportError("whoami fail"))
    mock_client.close = AsyncMock()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is True
    assert any(
        "whoami call failed" in str(call.args[0])
        for call in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_e2ee_not_supported(
    mock_logger, mock_async_client, mock_ssl
):
    mock_client, _ = _make_logged_in_client()
    mock_async_client.return_value = mock_client

    from mmrelay.paths import E2EENotSupportedError

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=True),
        patch(
            "mmrelay.matrix_utils.get_e2ee_store_dir",
            side_effect=E2EENotSupportedError(),
        ),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is True
    assert any(
        "E2EE is not supported" in str(call.args[0])
        for call in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_e2ee_os_error(mock_logger, mock_async_client, mock_ssl):
    mock_client, _ = _make_logged_in_client()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=True),
        patch(
            "mmrelay.matrix_utils.get_e2ee_store_dir",
            side_effect=OSError("no store"),
        ),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is True
    assert any(
        "Could not resolve E2EE store path" in str(call.args[0])
        for call in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_discovery_has_homeserver_url(
    mock_logger, mock_async_client, mock_ssl
):
    mock_temp = MagicMock()
    mock_resp = MagicMock()
    del mock_resp.homeserver_url
    mock_temp.discovery_info = AsyncMock(return_value=mock_resp)
    mock_temp.close = AsyncMock()

    mock_client, _ = _make_logged_in_client()
    mock_async_client.side_effect = [mock_temp, mock_client]

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch(
            "mmrelay.matrix_utils.DiscoveryInfoResponse",
            type("DIR", (), {}),
        ),
        patch(
            "mmrelay.matrix_utils.DiscoveryInfoError",
            type("DIE", (), {}),
        ),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=False,
            config_for_paths={},
        )

    assert result is True


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils._create_ssl_context", return_value=None)
@patch("mmrelay.matrix_utils.AsyncClient")
@patch("mmrelay.matrix_utils.logger")
async def test_login_matrix_bot_logout_others(mock_logger, mock_async_client, mock_ssl):
    mock_client, _ = _make_logged_in_client()
    mock_async_client.return_value = mock_client

    with (
        patch("mmrelay.matrix_utils.config_module.load_config", return_value={}),
        patch(
            "mmrelay.matrix_utils._resolve_credentials_save_path",
            return_value=TEST_CREDS_PATH,
        ),
        patch("mmrelay.matrix_utils.save_credentials"),
        patch("mmrelay.matrix_utils.is_e2ee_enabled", return_value=False),
    ):
        result = await login_matrix_bot(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="password",
            logout_others=True,
            config_for_paths={},
        )

    assert result is True
    assert any(
        "Logging out other sessions" in str(call.args[0])
        for call in mock_logger.info.call_args_list
    )
