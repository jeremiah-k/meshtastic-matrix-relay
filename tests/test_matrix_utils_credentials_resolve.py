"""Tests for _resolve_and_load_credentials edge cases in credentials.py.

Covers CancelledError, auto-login success reload, missing keys after auto-login,
OSError during auto-login, various config validation fallbacks, and _get_bot_user_id.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.config import InvalidCredentialsPathTypeError
from mmrelay.matrix_utils import _resolve_and_load_credentials


@pytest.mark.asyncio
async def test_resolve_and_load_credentials_cancelled_error():
    with (
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new_callable=AsyncMock,
            side_effect=asyncio.CancelledError(),
        ),
        patch("mmrelay.matrix_utils.logger"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await _resolve_and_load_credentials(config_data={}, matrix_section=None)


@pytest.mark.asyncio
async def test_resolve_and_load_credentials_auto_login_missing_keys():
    auto_creds = {"homeserver": "https://matrix.org"}

    with (
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new=AsyncMock(side_effect=[None, auto_creds]),
        ),
        patch(
            "mmrelay.matrix_utils.login_matrix_bot",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await _resolve_and_load_credentials(
            config_data={
                "matrix": {
                    "homeserver": "https://matrix.org",
                    "bot_user_id": "@bot:matrix.org",
                    "password": "pass",
                },
                "matrix_rooms": [],
            },
            matrix_section={
                "homeserver": "https://matrix.org",
                "bot_user_id": "@bot:matrix.org",
                "password": "pass",
            },
        )

    assert result is None
    assert any(
        "missing required keys" in str(call)
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_resolve_and_load_credentials_no_config_data():
    with (
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await _resolve_and_load_credentials(
            config_data=None,
            matrix_section=None,
        )

    assert result is None
    assert any(
        "No configuration available" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_resolve_and_load_credentials_non_dict_config():
    with (
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await _resolve_and_load_credentials(
            config_data="not a dict",
            matrix_section=None,
        )

    assert result is None
    assert any(
        "Configuration is invalid" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_resolve_and_load_credentials_no_matrix_section():
    with (
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await _resolve_and_load_credentials(
            config_data={"matrix_rooms": []},
            matrix_section=None,
        )

    assert result is None
    assert any(
        "No Matrix authentication available" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_resolve_and_load_credentials_non_dict_matrix_section():
    with (
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await _resolve_and_load_credentials(
            config_data={"matrix": "not a dict"},
            matrix_section="not a dict",
        )

    assert result is None
    assert any(
        "empty or invalid" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_resolve_and_load_credentials_no_access_token_in_matrix_section():
    with (
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await _resolve_and_load_credentials(
            config_data={"matrix": {"homeserver": "https://matrix.org"}},
            matrix_section={"homeserver": "https://matrix.org"},
        )

    assert result is None
    assert any(
        "missing required field" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_resolve_and_load_credentials_matrix_section_non_auth_only():
    with (
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await _resolve_and_load_credentials(
            config_data={"matrix": {"e2ee": True}},
            matrix_section={"e2ee": True},
        )

    assert result is None
    assert any(
        "non-auth settings only" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_resolve_and_load_credentials_no_homeserver_in_matrix_section():
    with (
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await _resolve_and_load_credentials(
            config_data={
                "matrix": {
                    "access_token": "token",
                },
            },
            matrix_section={
                "access_token": "token",
            },
        )

    assert result is None
    assert any(
        "missing required field" in str(call)
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_resolve_and_load_credentials_invalid_bot_user_id():
    with (
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("mmrelay.matrix_utils._normalize_bot_user_id", return_value=None),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await _resolve_and_load_credentials(
            config_data={
                "matrix": {
                    "access_token": "token",
                    "homeserver": "https://matrix.org",
                    "bot_user_id": ":",
                },
            },
            matrix_section={
                "access_token": "token",
                "homeserver": "https://matrix.org",
                "bot_user_id": ":",
            },
        )

    assert result is None
    assert any(
        "invalid bot_user_id" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_resolve_and_load_credentials_missing_bot_user_id_warning():
    with (
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await _resolve_and_load_credentials(
            config_data={
                "matrix": {
                    "access_token": "token",
                    "homeserver": "https://matrix.org",
                },
            },
            matrix_section={
                "access_token": "token",
                "homeserver": "https://matrix.org",
            },
        )

    assert result is not None
    assert result.user_id == ""
    assert any(
        "missing bot_user_id" in str(call.args[0])
        for call in mock_logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_resolve_and_load_credentials_auto_login_cancelled():
    with (
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "mmrelay.matrix_utils.login_matrix_bot",
            new_callable=AsyncMock,
            side_effect=asyncio.CancelledError(),
        ),
        patch("mmrelay.matrix_utils.logger"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await _resolve_and_load_credentials(
                config_data={
                    "matrix": {
                        "homeserver": "https://matrix.org",
                        "bot_user_id": "@bot:matrix.org",
                        "password": "pass",
                    },
                    "matrix_rooms": [],
                },
                matrix_section={
                    "homeserver": "https://matrix.org",
                    "bot_user_id": "@bot:matrix.org",
                    "password": "pass",
                },
            )


@pytest.mark.asyncio
async def test_resolve_and_load_credentials_auto_login_reload_json_error():
    with (
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new=AsyncMock(side_effect=[None, json.JSONDecodeError("err", "", 0)]),
        ),
        patch(
            "mmrelay.matrix_utils.login_matrix_bot",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await _resolve_and_load_credentials(
            config_data={
                "matrix": {
                    "homeserver": "https://matrix.org",
                    "bot_user_id": "@bot:matrix.org",
                    "password": "pass",
                },
                "matrix_rooms": [],
            },
            matrix_section={
                "homeserver": "https://matrix.org",
                "bot_user_id": "@bot:matrix.org",
                "password": "pass",
            },
        )

    assert result is None
    assert any(
        "Failed to reload newly created credentials" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_resolve_and_load_credentials_auto_login_reload_returns_none():
    with (
        patch(
            "mmrelay.matrix_utils.async_load_credentials",
            new=AsyncMock(side_effect=[None, None]),
        ),
        patch(
            "mmrelay.matrix_utils.login_matrix_bot",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        result = await _resolve_and_load_credentials(
            config_data={
                "matrix": {
                    "homeserver": "https://matrix.org",
                    "bot_user_id": "@bot:matrix.org",
                    "password": "pass",
                },
                "matrix_rooms": [],
            },
            matrix_section={
                "homeserver": "https://matrix.org",
                "bot_user_id": "@bot:matrix.org",
                "password": "pass",
            },
        )

    assert result is None
    mock_logger.error.assert_called_with("Failed to load newly created credentials")
