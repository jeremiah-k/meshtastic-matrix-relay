"""Keep OAuth rotation and persistence outside Matrix message handling."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mmrelay.matrix.oauth import OAuthClient, OAuthError, OAuthSession
from mmrelay.matrix.oauth_async import finish_task
from mmrelay.matrix.oauth_store import OAuthStore


def _record_stamp(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size


class OAuthSessionManager:
    """Serialize rotation and expose tokens only after durable persistence."""

    def __init__(
        self,
        session: OAuthSession,
        store: OAuthStore,
        protocol: OAuthClient | None = None,
    ) -> None:
        self.session = session
        self.store = store
        self.protocol = protocol if protocol is not None else OAuthClient()
        self._lock = asyncio.Lock()
        # The supplied session may predate a cross-process replacement. Only
        # cache a token after reading its record under the credentials lock.
        self._stamp: tuple[int, int, int, int] | None = None

    async def access_token(self) -> str:
        if (
            self.protocol.clock() < self.session.refresh_at
            and self._stamp is not None
            and self._stamp == _record_stamp(self.store.path)
        ):
            return self.session.access_token
        async with self._lock, self.store.locked(wait_timeout=45.0):
            record = await self.store.load()
            if record is None:
                raise OAuthError("OAuth credentials were removed; authenticate again.")
            session = OAuthSession.parse(record)
            identity = (
                "homeserver",
                "user_id",
                "device_id",
                "issuer",
                "client_id",
                "token_endpoint",
                "revocation_endpoint",
            )
            if any(
                getattr(session, name) != getattr(self.session, name)
                for name in identity
            ):
                raise OAuthError("OAuth session changed; restart the relay.")
            if self.protocol.clock() >= session.refresh_at:
                # Finish rotation and storage even if a Matrix request is cancelled.
                task = asyncio.create_task(self._rotate(session))
                session = await finish_task(task)
            self.session = session
            self._stamp = _record_stamp(self.store.path)
            return session.access_token

    async def _rotate(self, session: OAuthSession) -> OAuthSession:
        try:
            refreshed = await self.protocol.refresh(session)
        except OAuthError as exc:
            raise OAuthError(
                "OAuth renewal failed; check connectivity or authenticate again. "
                "Password login was not attempted."
            ) from exc
        try:
            await self.store.save(refreshed)
        except (OSError, OAuthError):
            # A refresh may rotate the server-side credential before local
            # persistence fails. Revoke the unpersisted result so MMRelay does
            # not leave an active session whose refresh token it has lost.
            try:
                await finish_task(
                    asyncio.create_task(
                        self.protocol.revoke_unpersisted(refreshed, session)
                    )
                )
            except OAuthError:
                pass
            raise OAuthError(
                "OAuth renewal succeeded but the rotated credentials could not be "
                "saved; authenticate again. Password login was not attempted."
            ) from None
        return refreshed


def attach_oauth_session(
    client: Any,
    manager: OAuthSessionManager,
    on_token: Callable[[str], None] | None = None,
) -> None:
    """Guard each SDK transport attempt, including SDK-managed retries.

    The SDK builds its headers before retrying. Wrapping send rather than _send
    prevents a retry from reusing a token that expired during a backoff.
    Failed HTTP requests are not replayed by this adapter.
    """
    original_send = client.send

    async def send(
        method: str,
        path: str,
        data: Any = None,
        headers: Mapping[str, str] | None = None,
        trace_context: Any = None,
        timeout: float | None = None,
    ) -> Any:
        parsed = urlsplit(path)
        if (
            parsed.scheme
            or parsed.netloc
            or not path.startswith("/")
            or path.startswith("//")
            or "\\" in path
        ):
            raise OAuthError(
                "OAuth Matrix requests must use homeserver-relative paths."
            )
        custom_headers = getattr(client.config, "custom_headers", None) or {}
        if any(name.lower() == "authorization" for name in custom_headers):
            raise OAuthError(
                "Custom Authorization headers cannot override OAuth sessions."
            )
        if client.homeserver.rstrip("/") != manager.session.homeserver.rstrip("/"):
            raise OAuthError("OAuth credentials cannot be sent to another homeserver.")
        token = await manager.access_token()
        client.access_token = token
        if on_token is not None:
            on_token(token)
        updated = {
            name: value
            for name, value in (headers or {}).items()
            if name.lower() != "authorization"
        }
        updated["Authorization"] = f"Bearer {token}"
        query = [
            (name, value)
            for name, value in parse_qsl(parsed.query, keep_blank_values=True)
            if name != "access_token"
        ]
        path = urlunsplit(("", "", parsed.path, urlencode(query), ""))
        return await original_send(
            method,
            path,
            data=data,
            headers=updated,
            trace_context=trace_context,
            timeout=timeout,
        )

    client.send = send
