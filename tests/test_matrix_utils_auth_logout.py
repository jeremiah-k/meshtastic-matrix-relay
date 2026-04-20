"""Tests for Matrix logout functionality."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.cli_utils import logout_matrix_bot
from mmrelay.constants.config import CONFIG_KEY_DEVICE_ID


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
        CONFIG_KEY_DEVICE_ID: "test_device",
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
        CONFIG_KEY_DEVICE_ID: "test_device",
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
        CONFIG_KEY_DEVICE_ID: "test_device",
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


@pytest.mark.asyncio
async def test_logout_matrix_bot_missing_user_id_fetch_success():
    """Test logout when user_id is missing but can be fetched via whoami()."""
    mock_credentials = {
        "homeserver": "https://matrix.org",
        "access_token": "test_token",
        CONFIG_KEY_DEVICE_ID: "test_device",
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
        CONFIG_KEY_DEVICE_ID: "test_device",
    }

    with (
        patch(
            "mmrelay.config.async_load_credentials",
            new=AsyncMock(return_value=mock_credentials),
        ),
        patch("mmrelay.cli_utils.AsyncClient") as mock_async_client,
        patch("mmrelay.cli_utils.asyncio.wait_for") as mock_wait_for,
        patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
    ):
        mock_temp_client = AsyncMock()
        mock_temp_client.close = AsyncMock()
        mock_async_client.return_value = mock_temp_client

        def _timeout_wait_for(awaitable, timeout=None, **kwargs):
            """
            Simulate an asyncio.wait_for timeout by closing a coroutine awaitable (if provided) and raising asyncio.TimeoutError.
            
            Parameters:
                awaitable: The awaitable or coroutine that would have been awaited; if it is a coroutine it will be closed.
                timeout: Ignored. Present to match the wait_for signature.
            
            Raises:
                asyncio.TimeoutError: Always raised to simulate a timeout.
            """
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise asyncio.TimeoutError()

        mock_wait_for.side_effect = _timeout_wait_for

        result = await logout_matrix_bot(password="test_password")

    assert result is False
    mock_temp_client.close.assert_called_once()
