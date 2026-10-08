"""CLI authorization uses its own session and never imports client tokens."""

import argparse
import asyncio
import json
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


def test_parser_does_not_abbreviate_home_into_homeserver(capsys) -> None:
    """A trailing global --home is ignored, never parsed as --homeserver."""
    with patch(
        "sys.argv",
        [
            "mmrelay",
            "auth",
            "login",
            "--homeserver",
            "https://example.com",
            "--home",
            "/tmp/mmrelay-data",
        ],
    ):
        args = parse_arguments()
    assert args.homeserver == "https://example.com"
    assert "Unknown arguments ignored" in capsys.readouterr().err


def test_cli_rejects_password_for_oauth() -> None:
    args = argparse.Namespace(oauth=True, password=None, reset_cross_signing=False)
    args.password = "test-password"
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
@pytest.mark.parametrize("pending_status", [400, 403])
def test_cli_creates_dedicated_session_then_revokes_without_password(
    tmp_path: Path, capsys, pending_status: int
) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    server = FakeServer()
    server.polls = [JsonResponse(pending_status, {"error": "authorization_pending"})]
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
        record = store.load_sync()
        assert record is not None
        assert record["auth_type"] == "oauth"
        assert server.sleeps == [5, 5]
        assert handle_auth_login(argparse.Namespace(oauth=False)) == 1
        assert handle_auth_logout(args) == 0
        assert not store.path.exists()
    output = capsys.readouterr().out
    assert "TEST-CODE" in output
    assert "Keep this command running until credentials are saved." in output
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


@pytest.mark.integration
@pytest.mark.parametrize("oauth", [False, True])
@pytest.mark.parametrize("signed", [None, "device_signed"])
def test_cli_reuses_oauth_device_for_explicit_reset_and_retains_credentials(
    tmp_path: Path, capsys, oauth: bool, signed: str | None
) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    original = session()

    async def save() -> None:
        async with store.locked():
            await store.save(original)

    asyncio.run(save())
    before = store.path.read_bytes()
    config = {"matrix": {"e2ee": {"enabled": True}}}
    calls: list[tuple[str, bool]] = []

    async def sign(active, active_store, active_config, *, reset_cross_signing=False):
        assert active_store.path == store.path
        assert active_config == config
        calls.append((active.device_id, reset_cross_signing))
        return signed

    args = argparse.Namespace(
        oauth=oauth,
        homeserver=None,
        username=None,
        password=None,
        reset_cross_signing=True,
    )
    with (
        patch("mmrelay.matrix.oauth_cli.credential_store", return_value=store),
        patch("mmrelay.config.load_config_silently", return_value=config),
        patch("mmrelay.matrix.oauth_cli.self_sign_device", side_effect=sign),
        patch(
            "mmrelay.matrix.oauth_cli.OAuthClient",
            side_effect=AssertionError("Relogin"),
        ),
        patch("builtins.input", side_effect=AssertionError("Login prompt")),
        patch("getpass.getpass", side_effect=AssertionError("Password prompt")),
        patch("mmrelay.cli.ensure_directories"),
    ):
        assert handle_auth_login(args) == (0 if signed else 1)

    assert calls == [(original.device_id, True)]
    assert store.path.read_bytes() == before
    output = capsys.readouterr().out
    assert "test-access-secret" not in output
    assert "test-refresh-secret" not in output
    assert ("own device is cross-signed" in output) == (signed is not None)


def test_oauth_signing_reuse_rejects_a_different_account(tmp_path: Path) -> None:
    store = OAuthStore(tmp_path / "credentials.json")

    async def save() -> None:
        async with store.locked():
            await store.save(session())

    asyncio.run(save())
    before = store.path.read_bytes()
    args = argparse.Namespace(
        username="@other:example.com", password=None, homeserver=None
    )
    with (
        patch("mmrelay.matrix.oauth_cli.credential_store", return_value=store),
        patch(
            "mmrelay.matrix.oauth_cli.OAuthClient",
            side_effect=AssertionError("Relogin"),
        ),
    ):
        assert handle_oauth_login(args) == 1
    assert store.path.read_bytes() == before


@pytest.mark.integration
@pytest.mark.parametrize(
    "homeserver", ["matrix.example.com", "https://matrix.example.com/"]
)
@pytest.mark.parametrize("username", ["bot", "@bot:example.com"])
def test_oauth_signing_reuse_accepts_equivalent_server_and_username_inputs(
    tmp_path: Path, homeserver: str, username: str
) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    store.path.write_text(json.dumps(session().credentials()))
    before = store.path.read_bytes()
    args = argparse.Namespace(homeserver=homeserver, username=username, password=None)
    with (
        patch("mmrelay.matrix.oauth_cli.credential_store", return_value=store),
        patch("mmrelay.config.load_config_silently", return_value={}),
        patch("builtins.input", side_effect=AssertionError("Login prompt")),
        patch(
            "mmrelay.matrix.oauth_cli.OAuthClient",
            side_effect=AssertionError("Relogin"),
        ),
    ):
        assert handle_oauth_login(args) == 0
    assert store.path.read_bytes() == before


@pytest.mark.integration
@pytest.mark.parametrize("homeserver", ["example.com", "https://example.com"])
def test_oauth_signing_reuse_accepts_the_saved_account_server_name(
    tmp_path: Path, homeserver: str
) -> None:
    """A bare server name matches the delegated homeserver that owns the MXID."""
    store = OAuthStore(tmp_path / "credentials.json")
    store.path.write_text(json.dumps(session().credentials()))
    before = store.path.read_bytes()
    args = argparse.Namespace(homeserver=homeserver, username=None, password=None)
    with (
        patch("mmrelay.matrix.oauth_cli.credential_store", return_value=store),
        patch("mmrelay.config.load_config_silently", return_value={}),
        patch("builtins.input", side_effect=AssertionError("Login prompt")),
        patch(
            "mmrelay.matrix.oauth_cli.OAuthClient",
            side_effect=AssertionError("Relogin"),
        ),
    ):
        assert handle_oauth_login(args) == 0
    assert store.path.read_bytes() == before


@pytest.mark.integration
def test_oauth_signing_reuse_still_rejects_a_foreign_server(
    tmp_path: Path,
) -> None:
    store = OAuthStore(tmp_path / "credentials.json")

    async def save() -> None:
        async with store.locked():
            await store.save(session())

    asyncio.run(save())
    before = store.path.read_bytes()
    args = argparse.Namespace(homeserver="other.example", username=None, password=None)
    with (
        patch("mmrelay.matrix.oauth_cli.credential_store", return_value=store),
        patch(
            "mmrelay.matrix.oauth_cli.OAuthClient",
            side_effect=AssertionError("Relogin"),
        ),
    ):
        assert handle_oauth_login(args) == 1
    assert store.path.read_bytes() == before


@pytest.mark.integration
@pytest.mark.parametrize("failure", [None, "timeout", "rejected"])
def test_password_logout_retains_keys_and_uses_selected_session(
    tmp_path: Path, capsys, failure: str | None
) -> None:
    store = OAuthStore(tmp_path / "configured-credentials.json")
    record = {
        "homeserver": "https://matrix.example.com",
        "user_id": "@bot:example.com",
        "device_id": "PASSWORD-DEVICE",
        "access_token": "test-password-session-token",
    }
    store.path.write_text(json.dumps(record))
    signing = tmp_path / "store" / "cross_signing.json"
    signing.parent.mkdir()
    signing.write_text("test-private-signing-keys")
    device = signing.with_name("device.db")
    device.write_text("test-private-device-keys")
    client = Mock()
    client.close = AsyncMock()
    client.logout = AsyncMock(return_value=Mock(transport_response=True))
    client.login = AsyncMock(side_effect=AssertionError("Password verification"))
    if failure == "timeout":
        client.logout.side_effect = asyncio.TimeoutError()
    elif failure == "rejected":
        from mmrelay.cli_utils import LogoutError

        client.logout.return_value = Mock(spec=LogoutError, errcode="M_FORBIDDEN")
    args = argparse.Namespace(password=None, yes=True, config="selected.yaml")
    with (
        patch("mmrelay.matrix.oauth_cli.credential_store", return_value=store),
        patch("mmrelay.cli_utils.AsyncClient", return_value=client),
        patch("mmrelay.cli_utils._create_ssl_context", return_value=None),
        patch("getpass.getpass", side_effect=AssertionError("Password prompt")),
        patch(
            "mmrelay.config.async_load_credentials",
            side_effect=AssertionError("Other credentials"),
        ),
    ):
        assert handle_auth_logout(args) == (1 if failure else 0)
    client.restore_login.assert_called_once_with(
        user_id=record["user_id"],
        device_id=record["device_id"],
        access_token=record["access_token"],
    )
    client.logout.assert_awaited_once_with()
    client.login.assert_not_awaited()
    client.close.assert_awaited_once()
    assert signing.read_text() == "test-private-signing-keys"
    assert device.read_text() == "test-private-device-keys"
    assert store.path.exists() == (failure is not None)
    assert record["access_token"] not in capsys.readouterr().out


@pytest.mark.integration
def test_auth_method_switch_preserves_the_local_signing_sidecar(tmp_path: Path) -> None:
    store = OAuthStore(tmp_path / "credentials.json")
    store.path.write_text(json.dumps(session().credentials()))
    signing = tmp_path / "store" / "cross_signing.json"
    signing.parent.mkdir()
    signing.write_text("test-private-signing-keys")
    protocol = OAuthClient(FakeServer())
    args = argparse.Namespace(password=None, yes=True)
    with (
        patch("mmrelay.matrix.oauth_cli.credential_store", return_value=store),
        patch("mmrelay.matrix.oauth_cli.OAuthClient", return_value=protocol),
        patch("getpass.getpass", side_effect=AssertionError("Password prompt")),
    ):
        assert handle_auth_logout(args) == 0
        assert signing.read_text() == "test-private-signing-keys"
        # Model the saved password device between the two logout operations.
        store.path.write_text(
            json.dumps(
                {
                    "homeserver": "https://matrix.example.com",
                    "user_id": "@bot:example.com",
                    "device_id": "PASSWORD-DEVICE",
                    "access_token": "test-password-token",
                }
            )
        )
        client = Mock()
        client.logout = AsyncMock(return_value=Mock(transport_response=True))
        client.close = AsyncMock()
        with patch("mmrelay.cli_utils.AsyncClient", return_value=client):
            assert handle_auth_logout(args) == 0
    assert not store.path.exists()
    assert signing.read_text() == "test-private-signing-keys"
