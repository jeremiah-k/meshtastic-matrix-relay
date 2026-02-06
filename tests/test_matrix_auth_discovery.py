"""
Tests for Matrix authentication discovery path correctness and resilience.
"""

import asyncio
import secrets
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.matrix_utils import MatrixAuthInfo, _perform_matrix_login


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.access_token = None
    client.user_id = None
    client.device_id = None
    client.restore_login = MagicMock()
    client.whoami = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_whoami_discovers_missing_device_id(mock_client, tmp_path):
    """Test that whoami is called to discover missing device_id and credentials are updated."""
    access_token = secrets.token_hex(16)
    credentials_path = str(tmp_path / "credentials.json")
    auth_info = MatrixAuthInfo(
        homeserver="https://matrix.org",
        access_token=access_token,
        user_id="@test:matrix.org",
        device_id=None,  # Missing device_id
        credentials={
            "homeserver": "https://matrix.org",
            "user_id": "@test:matrix.org",
            "access_token": access_token,
        },
        credentials_path=credentials_path,
    )

    # Mock whoami response
    mock_whoami_resp = MagicMock()
    mock_whoami_resp.device_id = "DISCOVERED_DEVICE"
    mock_whoami_resp.user_id = "@test:matrix.org"
    mock_client.whoami.return_value = mock_whoami_resp

    with patch("mmrelay.matrix_utils.save_credentials") as mock_save:
        with patch(
            "mmrelay.matrix_utils.asyncio.to_thread",
            side_effect=lambda f, *args, **kwargs: f(*args, **kwargs),
        ):
            discovered_device_id = await _perform_matrix_login(mock_client, auth_info)

            assert discovered_device_id == "DISCOVERED_DEVICE"
            mock_client.whoami.assert_awaited_once()
            # Credentials should be updated with device_id
            assert auth_info.credentials["device_id"] == "DISCOVERED_DEVICE"
            mock_save.assert_called_once_with(
                auth_info.credentials, credentials_path=auth_info.credentials_path
            )
            # Login should be restored
            mock_client.restore_login.assert_called_once_with(
                user_id="@test:matrix.org",
                device_id="DISCOVERED_DEVICE",
                access_token=access_token,
            )


@pytest.mark.asyncio
async def test_user_id_mismatch_handles_gracefully(mock_client, tmp_path):
    """Test that user_id mismatch between credentials and whoami is handled by preferring whoami."""
    access_token = secrets.token_hex(16)
    credentials_path = str(tmp_path / "credentials.json")
    auth_info = MatrixAuthInfo(
        homeserver="https://matrix.org",
        access_token=access_token,
        user_id="@wrong:matrix.org",  # Mismatching user_id
        device_id=None,
        credentials={
            "homeserver": "https://matrix.org",
            "user_id": "@wrong:matrix.org",
            "access_token": access_token,
        },
        credentials_path=credentials_path,
    )

    mock_whoami_resp = MagicMock()
    mock_whoami_resp.user_id = "@correct:matrix.org"
    mock_whoami_resp.device_id = "DEV"
    mock_client.whoami.return_value = mock_whoami_resp

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        with patch("mmrelay.matrix_utils.save_credentials"):
            with patch(
                "mmrelay.matrix_utils.asyncio.to_thread",
                side_effect=lambda f, *args, **kwargs: f(*args, **kwargs),
            ):
                await _perform_matrix_login(mock_client, auth_info)

                # Should log a warning
                assert any(
                    "Matrix user_id mismatch" in call.args[0]
                    for call in mock_logger.warning.call_args_list
                )
                # Credentials should be updated to correct user_id
                assert auth_info.credentials["user_id"] == "@correct:matrix.org"
                # client.user_id should be updated
                assert mock_client.user_id == "@correct:matrix.org"


@pytest.mark.asyncio
async def test_whoami_failure_handles_gracefully(mock_client, tmp_path):
    """Test that whoami failure is handled gracefully with a warning."""
    access_token = secrets.token_hex(16)
    credentials_path = str(tmp_path / "credentials.json")
    auth_info = MatrixAuthInfo(
        homeserver="https://matrix.org",
        access_token=access_token,
        user_id="@test:matrix.org",
        device_id=None,
        credentials={
            "homeserver": "https://matrix.org",
            "user_id": "@test:matrix.org",
            "access_token": access_token,
        },
        credentials_path=credentials_path,
    )

    # Mock whoami failure
    mock_client.whoami.side_effect = asyncio.TimeoutError()

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        await _perform_matrix_login(mock_client, auth_info)

        # Should log a warning about failure
        assert any(
            "Failed to discover device_id via whoami" in call.args[0]
            for call in mock_logger.warning.call_args_list
        )
        # Login session restoration should NOT be called because we don't have a device_id
        mock_client.restore_login.assert_not_called()


@pytest.mark.asyncio
async def test_no_credentials_whoami_discovers_user_id(mock_client):
    """Test that when no credentials exist, whoami is used to discover user_id from config access_token."""
    access_token = secrets.token_hex(16)
    auth_info = MatrixAuthInfo(
        homeserver="https://matrix.org",
        access_token=access_token,
        user_id="",  # Unknown user_id
        device_id=None,
        credentials=None,
        credentials_path=None,
    )

    mock_whoami_resp = MagicMock()
    mock_whoami_resp.user_id = "@discovered:matrix.org"
    mock_client.whoami.return_value = mock_whoami_resp

    await _perform_matrix_login(mock_client, auth_info)

    assert mock_client.user_id == "@discovered:matrix.org"
    mock_client.whoami.assert_awaited_once()
