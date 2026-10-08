"""Renewal, transport adaptation, and durable-session failure behavior."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mmrelay.matrix.oauth import JsonResponse, OAuthClient, OAuthError
from mmrelay.matrix.oauth_session import OAuthSessionManager, attach_oauth_session
from mmrelay.matrix.oauth_store import OAuthStore
from tests.oauth_helpers import FakeServer, session


async def manager_at(
    tmp_path: Path, clock: float = 1275
) -> tuple[OAuthSessionManager, FakeServer]:
    store = OAuthStore(tmp_path / "credentials.json")
    original = session()
    async with store.locked():
        await store.save(original)
    server = FakeServer()
    server.clock = clock
    return (
        OAuthSessionManager(
            original, store, OAuthClient(server, clock=lambda: server.clock)
        ),
        server,
    )


@pytest.mark.asyncio
async def test_parallel_requests_rotate_once_and_restart_reads_rotated_record(
    tmp_path: Path,
) -> None:
    manager, server = await manager_at(tmp_path)
    tokens = await asyncio.gather(*(manager.access_token() for _ in range(8)))
    assert tokens == ["test-renewed-access"] * 8
    assert len(server.requests) == 1
    restarted = OAuthSessionManager(session(), manager.store, manager.protocol)
    assert await restarted.access_token() == "test-renewed-access"
    assert restarted.session.refresh_token == "test-renewed-refresh"
    assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_persistence_failure_never_exposes_rotated_access_token(
    tmp_path: Path,
) -> None:
    manager, server = await manager_at(tmp_path)
    with patch(
        "mmrelay.matrix.oauth_store.os.replace", side_effect=OSError("disk failure")
    ):
        with pytest.raises(OAuthError, match="could not be saved"):
            await manager.access_token()
    assert manager.session == session()
    assert (await manager.store.load())["access_token"] == session().access_token
    assert server.requests[-1][1].endswith("/revoke")
    assert server.requests[-1][2]["form"] == {
        "token": "test-renewed-refresh",
        "token_type_hint": "refresh_token",
        "client_id": session().client_id,
    }


@pytest.mark.asyncio
async def test_oversized_rotation_is_revoked_without_replacing_credentials(
    tmp_path: Path,
) -> None:
    manager, server = await manager_at(tmp_path)
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
    with pytest.raises(OAuthError, match="could not be saved"):
        await manager.access_token()
    assert manager.session == session()
    assert await manager.store.load() == session().credentials()
    assert server.requests[-1][1].endswith("/revoke")
    assert server.requests[-1][2]["form"]["token"] == "R" * 32500


@pytest.mark.asyncio
async def test_removed_or_replaced_session_stops_requests(tmp_path: Path) -> None:
    manager, server = await manager_at(tmp_path)
    async with manager.store.locked():
        await manager.store.save(session(client_id="other-client"))
    with pytest.raises(OAuthError, match="changed"):
        await manager.access_token()
    await manager.store.remove()
    with pytest.raises(OAuthError, match="removed"):
        await manager.access_token()
    assert not server.requests


@pytest.mark.asyncio
async def test_revoked_refresh_token_stops_without_password_fallback(
    tmp_path: Path,
) -> None:
    manager, server = await manager_at(tmp_path)
    server.polls = [JsonResponse(400, {"error": "invalid_grant"})]
    with pytest.raises(OAuthError, match="Password login was not attempted"):
        await manager.access_token()
    assert len(server.requests) == 1
    assert (await manager.store.load())["access_token"] == session().access_token


@pytest.mark.asyncio
async def test_adapter_updates_each_attempt_and_never_replays_failed_send(
    tmp_path: Path,
) -> None:
    manager, server = await manager_at(tmp_path, clock=1000)
    send = AsyncMock(return_value=SimpleNamespace(status=401))
    client = SimpleNamespace(
        send=send,
        homeserver=session().homeserver,
        access_token="stale",
        config=SimpleNamespace(custom_headers={}),
    )
    attach_oauth_session(client, manager)
    await client.send(
        "POST",
        "/_matrix/client/v3/test?access_token=stale&limit=10",
        headers={"Authorization": "Bearer stale", "X-Test": "yes"},
    )
    assert send.await_count == 1
    assert send.call_args.args[1] == "/_matrix/client/v3/test?limit=10"
    assert send.call_args.kwargs["headers"] == {
        "Authorization": "Bearer test-access-secret",
        "X-Test": "yes",
    }
    server.clock = 1275
    await client.send(
        "GET", "/_matrix/client/v3/sync", headers={"Authorization": "Bearer stale"}
    )
    assert (
        send.call_args.kwargs["headers"]["Authorization"]
        == "Bearer test-renewed-access"
    )
    assert client.access_token == "test-renewed-access"


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["absolute", "network", "headers", "homeserver"])
async def test_adapter_rejects_credential_forwarding(
    tmp_path: Path, problem: str
) -> None:
    manager, _ = await manager_at(tmp_path)
    send = AsyncMock()
    client = SimpleNamespace(
        send=send,
        homeserver=session().homeserver,
        config=SimpleNamespace(custom_headers={}),
    )
    path = "/_matrix/client/v3/sync"
    if problem == "absolute":
        path = "https://evil.example.com/steal"
    elif problem == "network":
        path = "//evil.example.com/steal"
    elif problem == "headers":
        client.config.custom_headers = {"authorization": "Bearer override"}
    else:
        client.homeserver = "https://different.example.com"
    attach_oauth_session(client, manager)
    with pytest.raises(OAuthError):
        await client.send("GET", path)
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_request_finishes_rotation_before_releasing_store(
    tmp_path: Path,
) -> None:
    manager, server = await manager_at(tmp_path)
    entered = asyncio.Event()
    resume = asyncio.Event()
    original_request = server.request

    async def blocked_request(*args, **kwargs):
        entered.set()
        await resume.wait()
        return await original_request(*args, **kwargs)

    server.request = blocked_request
    task = asyncio.create_task(manager.access_token())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    resume.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await manager.store.load())["oauth"][
        "refresh_token"
    ] == "test-renewed-refresh"
    async with OAuthStore(manager.store.path).locked():
        assert await manager.store.load() is not None


@pytest.mark.asyncio
async def test_startup_retains_config_adjacent_oauth_source_path(
    tmp_path: Path,
) -> None:
    import mmrelay.matrix_utils as facade

    store = OAuthStore(tmp_path / "credentials.json")
    async with store.locked():
        await store.save(session())
    info = await facade._resolve_and_load_credentials(
        {}, {}, str(tmp_path / "config.yaml")
    )
    assert info is not None
    assert info.credentials_path == str(store.path)
    assert info.device_id == session().device_id


@pytest.mark.asyncio
async def test_invalid_credentials_path_does_not_crash_startup() -> None:
    import mmrelay.matrix_utils as facade

    info = await facade._resolve_and_load_credentials(
        {"credentials_path": 123}, {}, None
    )
    assert info is None


@pytest.mark.asyncio
async def test_incomplete_oauth_record_does_not_fall_back_to_config_password(
    tmp_path: Path,
) -> None:
    import mmrelay.matrix_utils as facade

    record = session().credentials()
    del record["access_token"]
    (tmp_path / "credentials.json").write_text(json.dumps(record), encoding="utf-8")
    config = {
        "matrix": {
            "homeserver": "https://matrix.example.com",
            "username": "bot",
            "password": "test-password",
        }
    }
    with pytest.raises(OAuthError, match="access_token"):
        await facade._resolve_and_load_credentials(
            config, config["matrix"], str(tmp_path / "config.yaml")
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b'{"auth_type":"oauth",', b"x" * 65537])
async def test_corrupt_saved_credentials_prevent_password_fallback(
    tmp_path: Path, raw: bytes
) -> None:
    import mmrelay.matrix_utils as facade

    (tmp_path / "credentials.json").write_bytes(raw)
    config = {
        "matrix": {
            "homeserver": "https://matrix.example.com",
            "username": "bot",
            "password": "test-password",
        }
    }
    with pytest.raises(OAuthError, match="password login was not attempted"):
        await facade._resolve_and_load_credentials(
            config, config["matrix"], str(tmp_path / "config.yaml")
        )
