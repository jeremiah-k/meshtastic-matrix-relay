"""Exercise the installed SDK outside pytest's global nio doubles."""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.mark.integration
def test_installed_sdk_restores_renews_and_sends_without_stale_headers(
    tmp_path: Path,
) -> None:
    script = textwrap.dedent("""
        import asyncio
        import sys
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from nio import AsyncClient, AsyncClientConfig
        import mmrelay.matrix_utils as facade
        from mmrelay.matrix.auth import _perform_matrix_login
        from mmrelay.matrix.oauth import OAuthClient
        from mmrelay.matrix.oauth_session import OAuthSessionManager
        from mmrelay.matrix.oauth_store import OAuthStore
        from mmrelay.matrix_utils import MatrixAuthInfo
        from tests.oauth_helpers import FakeServer, session

        async def main():
            original = session()
            store = OAuthStore(Path(sys.argv[1]) / "credentials.json")
            async with store.locked():
                await store.save(original)
            server = FakeServer()
            server.clock = 1275
            protocol = OAuthClient(server, clock=lambda: server.clock)
            manager = OAuthSessionManager(original, store, protocol)
            client = AsyncClient(original.homeserver, config=AsyncClientConfig(encryption_enabled=False))
            request = AsyncMock(return_value=SimpleNamespace(status=401))
            client.client_session = SimpleNamespace(request=request, close=AsyncMock())
            info = MatrixAuthInfo(homeserver=original.homeserver, access_token=original.access_token, user_id=original.user_id, device_id=original.device_id, credentials=original.credentials(), credentials_path=str(store.path))
            with patch("mmrelay.matrix.oauth_session.OAuthSessionManager", return_value=manager):
                restored = await _perform_matrix_login(client, info)
            assert restored == original.device_id
            assert client.device_id == original.device_id
            assert client.user_id == original.user_id
            assert info.access_token == "test-renewed-access"
            response = await client.send("POST", "/_matrix/client/v3/test?access_token=stale", headers={"Authorization": "Bearer stale"})
            assert response.status == 401
            assert request.await_count == 1
            assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer test-renewed-access"
            assert "access_token=" not in request.call_args.args[1]
            assert len(server.requests) == 1
            assert (await store.load())["oauth"]["refresh_token"] == "test-renewed-refresh"
            await client.close()
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_installed_sdk_oauth_self_signs_and_recovers_missing_sidecar(
    tmp_path: Path,
) -> None:
    script = textwrap.dedent("""
        import asyncio
        import json
        import sys
        import time
        from pathlib import Path
        from unittest.mock import patch
        from nio import AsyncClient
        import mmrelay.matrix_utils as facade
        from mmrelay.matrix.oauth_e2ee import self_sign_device
        from mmrelay.matrix.oauth_store import OAuthStore
        from tests.oauth_helpers import session

        class Response:
            def __init__(self, status, body):
                self.status = status
                self.body = body
                self.content = self
            async def json(self, **kwargs):
                return self.body
            async def text(self):
                return json.dumps(self.body)
            async def iter_chunked(self, size):
                yield json.dumps(self.body).encode()
            def release(self):
                pass

        async def main():
            root = Path(sys.argv[1])
            active = session(expires_at=time.time()+3600, refresh_at=time.time()+3500)
            store = OAuthStore(root / "credentials.json")
            async with store.locked():
                await store.save(active)
            credentials_before = store.path.read_bytes()
            config = {"matrix": {"e2ee": {"enabled": True, "store_path": str(root / "crypto")}}}
            state = {"master": {"keys": {"ed25519:previous": "previous-public"}}, "self": None, "device": None}
            uploads = []
            clients = []

            def factory(**kwargs):
                client = AsyncClient(**kwargs)
                clients.append(client)
                async def upload_device_keys():
                    # The SDK's actual Olm keys are signed; this substitutes
                    # only the server upload, not signing or persistence.
                    return None
                async def send(method, path, data=None, headers=None, **kwargs):
                    assert headers["Authorization"] == "Bearer " + active.access_token
                    body = json.loads(data)
                    if path.endswith("/keys/query"):
                        return Response(200, {
                            "master_keys": {active.user_id: state["master"]},
                            "self_signing_keys": {active.user_id: state["self"]} if state["self"] else {},
                            "device_keys": {active.user_id: {active.device_id: state["device"]}} if state["device"] else {},
                        })
                    if path.endswith("/keys/device_signing/upload"):
                        uploads.append(body)
                        if body.get("auth") != {"session": "test-uia-session"}:
                            return Response(401, {
                                "session": "test-uia-session",
                                "flows": [{"stages": ["m.oauth"]}],
                                "params": {"m.oauth": {"url": "https://auth.example.com/account?action=org.matrix.cross_signing_reset"}},
                            })
                        state["master"] = body["master_key"]
                        state["self"] = body["self_signing_key"]
                        return Response(200, {})
                    if path.endswith("/keys/signatures/upload"):
                        assert set(body) == {active.user_id}
                        assert set(body[active.user_id]) == {active.device_id}
                        state["device"] = body[active.user_id][active.device_id]
                        return Response(200, {})
                    raise AssertionError(path)
                client.send = send
                client.keys_upload = upload_device_keys
                return client

            with patch.object(facade, "AsyncClient", side_effect=factory):
                # Losing private signing keys cannot authorize identity rotation.
                assert await self_sign_device(active, store, config) is None
                assert uploads == []
                assert await self_sign_device(active, store, config, reset_cross_signing=True) == "uploaded_and_signed"
                identity = clients[-1].cross_signing_identity
                assert identity is not None and identity.uploaded
                assert identity.signed_devices == [active.device_id]
                master = identity.master_public_key
                assert state["master"]["keys"] == {"ed25519:" + master: master}
                assert "ed25519:" + identity.self_signing_public_key in state["device"]["signatures"][active.user_id]
                assert await self_sign_device(active, store, config) == "already_signed"
                assert clients[-1].cross_signing_identity.master_public_key == master
            assert len(uploads) == 2
            assert uploads[0]["master_key"] == uploads[1]["master_key"]
            assert store.path.read_bytes() == credentials_before
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
