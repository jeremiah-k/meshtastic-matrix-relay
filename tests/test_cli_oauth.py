"""CLI authorization uses its own session and never imports client tokens."""

import argparse
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from mmrelay.cli import handle_auth_login, handle_auth_logout, parse_arguments
from mmrelay.matrix.oauth import JsonResponse, OAuthClient, OAuthError
from mmrelay.matrix.oauth_cli import (
    credential_store,
)
from mmrelay.matrix.oauth_cli import handle_login as handle_oauth_login
from mmrelay.matrix.oauth_cli import handle_logout as handle_oauth_logout
from mmrelay.matrix.oauth_cli import (
    login,
    logout,
)
from mmrelay.matrix.oauth_store import OAuthStore
from tests.oauth_helpers import FakeServer, session


def test_parser_accepts_native_oauth_without_password() -> None:
    with patch(
        "sys.argv",
        ["mmrelay", "auth", "login", "--oauth", "--homeserver", "https://example.com"],
    ):
        args = parse_arguments()
    assert args.oauth is True
    assert args.password is None


@pytest.mark.parametrize("argument", ["password", "reset_cross_signing"])
def test_cli_rejects_password_or_identity_reset_for_oauth(argument: str) -> None:
    args = argparse.Namespace(oauth=True, password=None, reset_cross_signing=False)
    setattr(args, argument, "test-password" if argument == "password" else True)
    assert handle_auth_login(args) == 1


@pytest.mark.asyncio
async def test_login_refuses_to_overwrite_existing_session(tmp_path: Path) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    async with store.locked():
        await store.save(session())
    server = FakeServer()
    with pytest.raises(OAuthError, match="already exist"):
        await login("https://example.com", store, protocol=OAuthClient(server))
    assert not server.requests
    assert (await store.load())["access_token"] == session().access_token


@pytest.mark.integration
def test_cli_creates_dedicated_session_then_revokes_without_password(
    tmp_path: Path, capsys
) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    server = FakeServer()
    protocol = OAuthClient(
        server,
        clock=lambda: server.clock,
        monotonic=lambda: server.clock,
        sleep=server.sleep,
    )
    args = argparse.Namespace(
        oauth=True,
        homeserver="https://matrix.example.com",
        username="@bot:example.com",
        password=None,
        reset_cross_signing=False,
        yes=True,
    )
    with (
        patch("mmrelay.matrix.oauth_cli.credential_store", return_value=store),
        patch("mmrelay.matrix.oauth_cli.OAuthClient", return_value=protocol),
        patch("getpass.getpass", side_effect=AssertionError("Password prompt")),
    ):
        assert handle_auth_login(args) == 0
        assert store.path.exists()
        assert handle_auth_login(argparse.Namespace(oauth=False)) == 1
        assert handle_auth_logout(args) == 0
        assert not store.path.exists()
    output = capsys.readouterr().out
    assert "TEST-CODE" in output
    assert "test-renewed-access" not in output
    assert "test-renewed-refresh" not in output
    assert "test-device-secret" not in output
    assert "Encryption keys retained" in output


@pytest.mark.asyncio
async def test_revocation_failure_retains_credentials_and_crypto_store(
    tmp_path: Path,
) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    async with store.locked():
        await store.save(session())
    crypto = tmp_path / "store"
    crypto.mkdir()
    server = Mock()
    server.request = AsyncMock(return_value=JsonResponse(500, {}))
    with pytest.raises(OAuthError):
        await logout(store, OAuthClient(server))
    assert store.path.exists()
    assert crypto.exists()


@pytest.mark.asyncio
async def test_unsaved_session_is_revoked(tmp_path: Path) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    server = FakeServer()
    protocol = OAuthClient(
        server,
        clock=lambda: server.clock,
        monotonic=lambda: server.clock,
        sleep=server.sleep,
    )
    with patch(
        "mmrelay.matrix.oauth_store.os.replace", side_effect=OSError("disk failure")
    ):
        with pytest.raises(OSError):
            await login("https://matrix.example.com", store, protocol=protocol)
    assert server.requests[-1][1].endswith("/revoke")
    assert not store.path.exists()


@pytest.mark.asyncio
async def test_oversized_login_credentials_are_revoked_before_failure(
    tmp_path: Path,
) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    server = FakeServer()
    server.polls = [
        JsonResponse(
            200,
            {
                "access_token": "A" * 32500,
                "refresh_token": "R" * 32500,
                "token_type": "Bearer",
                "expires_in": 300,
            },
        )
    ]
    protocol = OAuthClient(
        server,
        clock=lambda: server.clock,
        monotonic=lambda: server.clock,
        sleep=server.sleep,
    )
    with pytest.raises(OAuthError, match="size limit"):
        await login("https://matrix.example.com", store, protocol=protocol)
    assert server.requests[-1][1].endswith("/revoke")
    assert server.requests[-1][2]["form"]["token"] == "R" * 32500
    assert not store.path.exists()


def test_oauth_cli_surfaces_operator_safe_protocol_errors(
    tmp_path: Path, capsys
) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    args = argparse.Namespace(
        homeserver="https://matrix.example.com",
        username=None,
        password=None,
        reset_cross_signing=False,
    )
    with (
        patch("mmrelay.matrix.oauth_cli.credential_store", return_value=store),
        patch(
            "mmrelay.matrix.oauth_cli.login",
            AsyncMock(side_effect=OAuthError("OAuth device authorization expired.")),
        ),
    ):
        assert handle_oauth_login(args) == 1
    assert "OAuth device authorization expired." in capsys.readouterr().out


def test_oauth_logout_confirmation_accepts_yes(tmp_path: Path, capsys) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    args = argparse.Namespace(password=None, yes=False)
    with (
        patch("builtins.input", return_value="yes"),
        patch(
            "mmrelay.matrix.oauth_cli.logout", AsyncMock(return_value=None)
        ) as revoke,
    ):
        assert handle_oauth_logout(args, store) == 0
    revoke.assert_awaited_once_with(store)
    assert "Logout cancelled" not in capsys.readouterr().out


def test_oauth_credential_store_honors_alternate_trailing_separator(
    tmp_path: Path,
) -> None:
    explicit = str(tmp_path / "oauth-dir") + "\\"
    with (
        patch("mmrelay.config.get_explicit_credentials_path", return_value=explicit),
        patch("mmrelay.config.get_credentials_search_paths", return_value=[]),
        patch("mmrelay.config.get_config_paths", return_value=[]),
        patch("mmrelay.matrix.oauth_cli.os.altsep", "\\"),
    ):
        store = credential_store(argparse.Namespace(), {"credentials_path": explicit})
    assert store.path.name == "credentials.json"
