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
