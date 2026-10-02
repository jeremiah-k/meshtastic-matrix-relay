"""Native Matrix OAuth device authorization, without borrowed client tokens."""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol
from urllib.parse import urlsplit

import aiohttp

from mmrelay.matrix.auth_input import (
    matches_username,
    normalize_homeserver,
    normalize_username,
)
from mmrelay.matrix.oauth_async import finish_task

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
API_SCOPE = "urn:matrix:client:api:*"
DEVICE_SCOPE = "urn:matrix:client:device:"
_MAX_JSON_BYTES = 65536
_SAFE_ERRORS = frozenset(
    {
        "authorization_pending",
        "slow_down",
        "access_denied",
        "expired_token",
        "invalid_grant",
        "invalid_client",
        "invalid_scope",
        "unsupported_grant_type",
    }
)


class OAuthError(RuntimeError):
    """An operator-safe OAuth failure; never contains a server response body."""


class OAuthTransportError(OAuthError):
    """A transient transport failure without credentials in its message."""


class OAuthHTTPError(OAuthError):
    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code if code in _SAFE_ERRORS else "request_rejected"
        super().__init__(f"OAuth request rejected (HTTP {status}; {self.code}).")


@dataclass(frozen=True)
class JsonResponse:
    status: int
    body: Mapping[str, Any] = field(repr=False)


class JsonTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        form: Mapping[str, str] | None = None,
        access_token: str | None = None,
    ) -> JsonResponse: ...


class HttpTransport:
    """Bounded HTTPS JSON transport; redirects cannot forward credentials."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        form: Mapping[str, str] | None = None,
        access_token: str | None = None,
    ) -> JsonResponse:
        secure_url(url)
        headers = {"Accept": "application/json"}
        if access_token is not None:
            headers["Authorization"] = f"Bearer {access_token}"
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20), trust_env=False
            ) as session:
                async with session.request(
                    method,
                    url,
                    json=dict(json_body) if json_body is not None else None,
                    data=dict(form) if form is not None else None,
                    headers=headers,
                    allow_redirects=False,
                ) as response:
                    if 300 <= response.status < 400:
                        raise OAuthError("OAuth HTTP redirects are not accepted.")
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        raw.extend(chunk)
                        if len(raw) > _MAX_JSON_BYTES:
                            raise OAuthError("OAuth response exceeds the size limit.")
                    if not raw and 200 <= response.status < 300:
                        return JsonResponse(response.status, {})
                    try:
                        body = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        if not 200 <= response.status < 300:
                            # Missing well-known documents and proxy failures may
                            # be HTML. Preserve status without echoing that body.
                            return JsonResponse(response.status, {})
                        raise OAuthError(
                            "OAuth endpoint returned invalid JSON."
                        ) from None
                    if not isinstance(body, dict):
                        raise OAuthError(
                            "OAuth endpoint returned a non-object response."
                        )
                    return JsonResponse(response.status, body)
        except (aiohttp.ClientError, TimeoutError):
            raise OAuthTransportError("Could not contact the OAuth endpoint.") from None


def secure_url(value: str) -> str:
    """Require an absolute HTTPS URL without userinfo, query, or fragment."""
    if _has_control_characters(value):
        raise OAuthError("Invalid OAuth endpoint URL.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise OAuthError("Invalid OAuth endpoint URL.") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in value)
    ):
        raise OAuthError("OAuth endpoints must be HTTPS URLs without credentials.")
    if port is not None and port == 0:
        raise OAuthError("Invalid OAuth endpoint port.")
    return value


def _origin(url: str) -> tuple[str, str | None, int]:
    parsed = urlsplit(secure_url(url))
    return parsed.scheme, parsed.hostname, parsed.port or 443


def _endpoint(value: str, issuer: str) -> str:
    if _origin(value) != _origin(issuer):
        raise OAuthError("OAuth endpoints outside the issuer origin are unsupported.")
    return value


def _text(body: Mapping[str, Any], name: str) -> str:
    value = body.get(name)
    if (
        not isinstance(value, str)
        or not value.strip()
        or _has_control_characters(value)
    ):
        raise OAuthError(f"OAuth response is missing a valid {name}.")
    return value


def _has_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _number(body: Mapping[str, Any], name: str) -> float:
    value = body.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OAuthError(f"OAuth response is missing a positive {name}.")
    try:
        number = float(value)
    except OverflowError:
        raise OAuthError(f"OAuth response is missing a positive {name}.") from None
    if not math.isfinite(number) or number <= 0:
        raise OAuthError(f"OAuth response is missing a positive {name}.")
    return number


def _success(response: JsonResponse) -> Mapping[str, Any]:
    if not 200 <= response.status < 300:
        code = response.body.get("error", "request_rejected")
        raise OAuthHTTPError(
            response.status, code if isinstance(code, str) else "request_rejected"
        )
    return response.body


@dataclass(frozen=True)
class OAuthMetadata:
    issuer: str
    token_endpoint: str
    registration_endpoint: str
    device_authorization_endpoint: str
    revocation_endpoint: str

    @classmethod
    def parse(cls, body: Mapping[str, Any]) -> OAuthMetadata:
        issuer = secure_url(_text(body, "issuer"))
        grants = body.get("grant_types_supported")
        if (
            not isinstance(grants, list)
            or not all(isinstance(grant, str) for grant in grants)
            or not {
                DEVICE_GRANT,
                "refresh_token",
            }.issubset(grants)
        ):
            raise OAuthError(
                "Homeserver does not advertise OAuth device authorization."
            )
        methods = body.get("token_endpoint_auth_methods_supported")
        if methods is not None and (
            not isinstance(methods, list) or "none" not in methods
        ):
            raise OAuthError("OAuth server does not support public clients.")
        return cls(
            issuer,
            *(
                _endpoint(_text(body, name), issuer)
                for name in (
                    "token_endpoint",
                    "registration_endpoint",
                    "device_authorization_endpoint",
                    "revocation_endpoint",
                )
            ),
        )


@dataclass(frozen=True)
class OAuthSession:
    homeserver: str
    user_id: str
    device_id: str
    issuer: str
    token_endpoint: str
    revocation_endpoint: str
    client_id: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float
    refresh_at: float
    scope: str

    def credentials(self) -> dict[str, Any]:
        return {
            "auth_type": "oauth",
            "homeserver": self.homeserver,
            "user_id": self.user_id,
            "device_id": self.device_id,
            "access_token": self.access_token,
            "oauth": {
                "version": 1,
                "issuer": self.issuer,
                "token_endpoint": self.token_endpoint,
                "revocation_endpoint": self.revocation_endpoint,
                "client_id": self.client_id,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
                "refresh_at": self.refresh_at,
                "scope": self.scope,
            },
        }

    @classmethod
    def parse(cls, credentials: Mapping[str, Any]) -> OAuthSession:
        record = credentials.get("oauth")
        if credentials.get("auth_type") != "oauth" or not isinstance(record, dict):
            raise OAuthError("Credentials do not contain a native OAuth session.")
        if type(record.get("version")) is not int or record["version"] != 1:
            raise OAuthError("Unsupported OAuth credential version.")
        issuer = secure_url(_text(record, "issuer"))
        device_id = _text(credentials, "device_id")
        scope = _text(record, "scope")
        _check_scope(scope, device_id)
        expires_at = _number(record, "expires_at")
        refresh_at = _number(record, "refresh_at")
        if refresh_at >= expires_at:
            raise OAuthError("Invalid OAuth refresh deadline.")
        return cls(
            secure_url(_text(credentials, "homeserver")),
            _text(credentials, "user_id"),
            device_id,
            issuer,
            _endpoint(_text(record, "token_endpoint"), issuer),
            _endpoint(_text(record, "revocation_endpoint"), issuer),
            _text(record, "client_id"),
            _text(credentials, "access_token"),
            _text(record, "refresh_token"),
            expires_at,
            refresh_at,
            scope,
        )


def _check_scope(scope: str, device_id: str) -> None:
    if _has_control_characters(scope) or set(scope.split()) != {
        API_SCOPE,
        DEVICE_SCOPE + device_id,
    }:
        raise OAuthError("OAuth response does not match the requested device scopes.")


@dataclass(frozen=True)
class TokenSet:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float
    refresh_at: float
    scope: str


def _tokens(
    body: Mapping[str, Any],
    device_id: str,
    requested_scope: str,
    started_at: float,
    previous_refresh: str | None = None,
) -> TokenSet:
    if _text(body, "token_type").lower() != "bearer":
        raise OAuthError("OAuth server returned an unsupported token type.")
    scope = body.get("scope", requested_scope)
    if not isinstance(scope, str):
        raise OAuthError("OAuth server returned invalid scopes.")
    _check_scope(scope, device_id)
    lifetime = _number(body, "expires_in")
    expiry = started_at + lifetime
    refresh_at = started_at + lifetime - min(30, lifetime / 5)
    if not math.isfinite(expiry) or not started_at < refresh_at < expiry:
        raise OAuthError("OAuth server returned an invalid token lifetime.")
    refresh = body.get("refresh_token", previous_refresh)
    refresh = _text({"refresh_token": refresh}, "refresh_token")
    return TokenSet(
        _text(body, "access_token"),
        refresh,
        expiry,
        refresh_at,
        scope,
    )


@dataclass(frozen=True)
class DevicePrompt:
    verification_uri: str
    user_code: str = field(repr=False)


class OAuthClient:
    """Own native authorization and renewal separately from the Matrix SDK."""

    def __init__(
        self,
        transport: JsonTransport | None = None,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.transport = transport if transport is not None else HttpTransport()
        self.clock = clock
        self.monotonic = monotonic
        self.sleep = sleep

    async def discover(self, homeserver: str) -> tuple[str, OAuthMetadata]:
        try:
            homeserver = normalize_homeserver(homeserver)
        except ValueError as exc:
            raise OAuthError(str(exc)) from None
        homeserver = secure_url(homeserver)
        # Matrix delegation applies before authentication-server discovery.
        parsed = urlsplit(homeserver)
        well_known = await self.transport.request(
            "GET", f"{parsed.scheme}://{parsed.netloc}/.well-known/matrix/client"
        )
        if well_known.status == 200:
            delegated = well_known.body.get("m.homeserver")
            if isinstance(delegated, dict) and "base_url" in delegated:
                homeserver = secure_url(_text(delegated, "base_url").rstrip("/"))
        elif well_known.status != 404:
            _success(well_known)
        metadata_response = await self.transport.request(
            "GET", homeserver + "/_matrix/client/v1/auth_metadata"
        )
        if metadata_response.status == 404:
            metadata_response = await self.transport.request(
                "GET",
                homeserver
                + "/_matrix/client/unstable/org.matrix.msc2965/auth_metadata",
            )
        if metadata_response.status == 404:
            raise OAuthError("Homeserver does not advertise native OAuth metadata.")
        metadata = OAuthMetadata.parse(_success(metadata_response))
        return homeserver, metadata

    async def authorize_device(
        self,
        homeserver: str,
        display: Callable[[DevicePrompt], None],
        *,
        expected_user_id: str | None = None,
    ) -> OAuthSession:
        if expected_user_id is not None:
            try:
                expected_user_id = normalize_username(expected_user_id)
            except ValueError as exc:
                raise OAuthError(str(exc)) from None
        homeserver, metadata = await self.discover(homeserver)
        device_id = "MMRELAY-" + uuid.uuid4().hex
        scope = API_SCOPE + " " + DEVICE_SCOPE + device_id
        registration = _success(
            await self.transport.request(
                "POST",
                metadata.registration_endpoint,
                json_body={
                    "client_name": "MMRelay",
                    "client_uri": "https://github.com/jeremiah-k/meshtastic-matrix-relay",
                    "application_type": "native",
                    "token_endpoint_auth_method": "none",
                    "grant_types": [DEVICE_GRANT, "refresh_token"],
                },
            )
        )
        if registration.get("token_endpoint_auth_method", "none") != "none":
            raise OAuthError(
                "OAuth registration requires an unsupported client secret."
            )
        client_id = _text(registration, "client_id")
        device = _success(
            await self.transport.request(
                "POST",
                metadata.device_authorization_endpoint,
                form={"client_id": client_id, "scope": scope},
            )
        )
        device_code = _text(device, "device_code")
        uri = _endpoint(_text(device, "verification_uri"), metadata.issuer)
        deadline = self.monotonic() + min(_number(device, "expires_in"), 1800)
        interval = _number({"interval": device.get("interval", 5)}, "interval")
        display(DevicePrompt(uri, _text(device, "user_code")))
        while self.monotonic() < deadline:
            await self.sleep(min(interval, deadline - self.monotonic()))
            if self.monotonic() >= deadline:
                break
            started_at = self.clock()
            try:
                response = await self.transport.request(
                    "POST",
                    metadata.token_endpoint,
                    form={
                        "grant_type": DEVICE_GRANT,
                        "device_code": device_code,
                        "client_id": client_id,
                    },
                )
            except OAuthTransportError:
                interval = max(5, interval * 2)
                continue
            code = response.body.get("error")
            # MAS reports pending device authorization with HTTP 403.
            if response.status in (400, 403) and code == "authorization_pending":
                continue
            if response.status in (400, 403) and code == "slow_down":
                interval += 5
                continue
            if response.status == 429 or response.status >= 500:
                interval += 5
                continue
            token_body = _success(response)
            tokens = await self._validate_issued_tokens(
                token_body,
                device_id,
                scope,
                started_at,
                metadata.revocation_endpoint,
                client_id,
            )
            session = OAuthSession(
                homeserver,
                "",
                device_id,
                metadata.issuer,
                metadata.token_endpoint,
                metadata.revocation_endpoint,
                client_id,
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                expires_at=tokens.expires_at,
                refresh_at=tokens.refresh_at,
                scope=tokens.scope,
            )
            try:
                identity = _success(
                    await self.transport.request(
                        "GET",
                        homeserver + "/_matrix/client/v3/account/whoami",
                        access_token=session.access_token,
                    )
                )
                user_id = _text(identity, "user_id")
                if (
                    not user_id.startswith("@")
                    or ":" not in user_id
                    or identity.get("device_id") != device_id
                    or identity.get("is_guest") is True
                    or (
                        expected_user_id is not None
                        and not matches_username(user_id, expected_user_id)
                    )
                ):
                    raise OAuthError(
                        "OAuth login returned a different account or device."
                    )
                return replace(session, user_id=user_id)
            except (OAuthError, asyncio.CancelledError):
                # Do not leave a rejected, authenticated session orphaned.
                try:
                    await finish_task(asyncio.create_task(self.revoke(session)))
                except OAuthError:
                    # The original error takes precedence over failed cleanup.
                    # Operators can revoke the device through their account UI.
                    pass
                raise
        raise OAuthError("OAuth device authorization expired; start login again.")

    async def refresh(self, session: OAuthSession) -> OAuthSession:
        started_at = self.clock()
        body = _success(
            await self.transport.request(
                "POST",
                session.token_endpoint,
                form={
                    "grant_type": "refresh_token",
                    "refresh_token": session.refresh_token,
                    "client_id": session.client_id,
                },
            )
        )
        tokens = await self._validate_issued_tokens(
            body,
            session.device_id,
            session.scope,
            started_at,
            session.revocation_endpoint,
            session.client_id,
            session.refresh_token,
        )
        return replace(
            session,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
            refresh_at=tokens.refresh_at,
            scope=tokens.scope,
        )

    async def _validate_issued_tokens(
        self,
        body: Mapping[str, Any],
        device_id: str,
        requested_scope: str,
        started_at: float,
        revocation_endpoint: str,
        client_id: str,
        previous_refresh: str | None = None,
    ) -> TokenSet:
        try:
            return _tokens(
                body, device_id, requested_scope, started_at, previous_refresh
            )
        except OAuthError:
            refresh_token = body.get("refresh_token")
            access_token = body.get("access_token")
            if isinstance(refresh_token, str) and refresh_token:
                rejected_token = refresh_token
                token_type_hint = "refresh_token"
            elif isinstance(access_token, str) and access_token:
                rejected_token = access_token
                token_type_hint = "access_token"
            else:
                rejected_token = None
                token_type_hint = "access_token"
            if rejected_token is not None:
                try:
                    await finish_task(
                        asyncio.create_task(
                            self._revoke_token(
                                revocation_endpoint,
                                client_id,
                                rejected_token,
                                token_type_hint,
                            )
                        )
                    )
                except OAuthError:
                    pass
            raise

    async def _revoke_token(
        self,
        revocation_endpoint: str,
        client_id: str,
        token: str,
        token_type_hint: str,
    ) -> None:
        _success(
            await self.transport.request(
                "POST",
                revocation_endpoint,
                form={
                    "token": token,
                    "token_type_hint": token_type_hint,
                    "client_id": client_id,
                },
            )
        )

    async def revoke(self, session: OAuthSession) -> None:
        await self._revoke_token(
            session.revocation_endpoint,
            session.client_id,
            session.refresh_token,
            "refresh_token",
        )
