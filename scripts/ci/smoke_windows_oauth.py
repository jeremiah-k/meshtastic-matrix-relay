"""Exercise native OAuth login, token rotation, and logout without a live account."""

import argparse
import asyncio
import contextlib
import io
import tempfile
from pathlib import Path
from unittest.mock import patch

from mmrelay.cli import handle_auth_login, handle_auth_logout
from mmrelay.matrix.oauth import OAuthClient, OAuthError, OAuthSession
from mmrelay.matrix.oauth_session import OAuthSessionManager
from mmrelay.matrix.oauth_store import OAuthStore
from tests.oauth_helpers import FakeServer


def verify() -> None:
    """Use real credential persistence and OS locks; replace only server I/O."""
    with tempfile.TemporaryDirectory(prefix="MMRelay OAuth smoke ") as temporary:
        store = OAuthStore(Path(temporary) / "matrix" / "credentials.json")
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
        output = io.StringIO()
        with (
            patch("mmrelay.matrix.oauth_cli.credential_store", return_value=store),
            patch("mmrelay.config.load_config_silently", return_value={}),
            patch("mmrelay.matrix.oauth_cli.OAuthClient", return_value=protocol),
            patch("getpass.getpass", side_effect=AssertionError("Password prompt")),
            contextlib.redirect_stdout(output),
        ):
            if handle_auth_login(args) != 0:
                raise RuntimeError("Native OAuth login failed")
            record = store.load_sync()
            if record is None:
                raise RuntimeError("Native OAuth credentials were not saved")
            session = OAuthSession.parse(record)

            async def rotate() -> None:
                async with store.locked():
                    try:
                        async with OAuthStore(store.path).locked():
                            raise RuntimeError(
                                "Concurrent OAuth owner acquired the lock"
                            )
                    except OAuthError:
                        pass
                server.clock = session.expires_at
                manager = OAuthSessionManager(session, store, protocol)
                await manager.access_token()
                saved = await store.load()
                if (
                    saved is None
                    or OAuthSession.parse(saved).expires_at <= session.expires_at
                ):
                    raise RuntimeError("OAuth renewal was not persisted")
                if manager.session.device_id != session.device_id:
                    raise RuntimeError("OAuth renewal replaced the device")

            asyncio.run(rotate())
            if handle_auth_logout(args) != 0 or store.path.exists():
                raise RuntimeError("Native OAuth logout failed")
        rendered = output.getvalue()
        if "TEST-CODE" not in rendered:
            raise RuntimeError("OAuth approval code was not displayed")
        if any(
            secret in rendered
            for secret in (
                "test-renewed-access",
                "test-renewed-refresh",
                "test-device-secret",
            )
        ):
            raise RuntimeError("OAuth output exposed session secrets")
    print("Native OAuth login, locking, renewal, and logout smoke check passed")


if __name__ == "__main__":
    verify()
