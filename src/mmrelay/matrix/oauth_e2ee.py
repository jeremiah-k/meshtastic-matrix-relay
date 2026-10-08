"""Own-device cross-signing with browser approval for native OAuth sessions."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar, cast
from urllib.parse import urlsplit, urlunsplit

from mmrelay.matrix.oauth import (
    OAuthError,
    OAuthSession,
    _has_control_characters,
    read_bounded_response,
    secure_url,
)
from mmrelay.matrix.oauth_async import finish_task
from mmrelay.matrix.oauth_session import OAuthSessionManager, attach_oauth_session
from mmrelay.matrix.oauth_store import OAuthStore

_SIGNING_UPLOAD = "/_matrix/client/v3/keys/device_signing/upload"
_APPROVAL_STAGES = ("m.oauth", "org.matrix.cross_signing_reset")
_APPROVAL_TIMEOUT = 120.0


def _approval_challenge(body: object, issuer: str) -> tuple[str, str]:
    """Accept only a single OAuth stage and an HTTPS URL on the session issuer."""
    if not isinstance(body, dict) or body.get("errcode") not in (
        None,
        "M_UNAUTHORIZED",
    ):
        raise OAuthError("Cross-signing browser authorization was rejected.")
    flows = body.get("flows")
    params = body.get("params")
    session = body.get("session")
    if (
        not isinstance(flows, list)
        or not isinstance(params, dict)
        or not isinstance(session, str)
        or not session
        or _has_control_characters(session)
    ):
        raise OAuthError("Invalid cross-signing browser authorization challenge.")
    for stage in _APPROVAL_STAGES:
        if not any(
            isinstance(flow, dict) and flow.get("stages") == [stage] for flow in flows
        ):
            continue
        entry = params.get(stage)
        url = entry.get("url") if isinstance(entry, dict) else None
        if not isinstance(url, str) or _has_control_characters(url):
            break
        try:
            parsed = urlsplit(url)
            # Account management URLs carry an action query. They are displayed,
            # never fetched with the relay's access token.
            secure_url(urlunsplit(parsed._replace(query="", fragment="")))
            trusted = urlsplit(secure_url(issuer))
            origin = (parsed.hostname, parsed.port or 443)
            trusted_origin = (trusted.hostname, trusted.port or 443)
        except ValueError:
            raise OAuthError("Invalid cross-signing approval URL.") from None
        if origin != trusted_origin:
            raise OAuthError("Cross-signing approval URL does not match the issuer.")
        return session, url
    raise OAuthError("The server does not offer OAuth cross-signing authorization.")


async def _read_challenge(response: Any) -> object:
    raw = await read_bounded_response(
        response, "Cross-signing authorization response exceeds the limit."
    )
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise OAuthError(
            "Invalid cross-signing browser authorization response."
        ) from None


_SendT = TypeVar("_SendT", bound=Callable[..., Any])


def approval_sender(
    send: _SendT,
    issuer: str,
    *,
    reset_cross_signing: bool,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> _SendT:
    """Handle UIA only for signing-key uploads, through the guarded SDK transport."""

    async def request(
        method: str,
        path: str,
        data: Any = None,
        headers: Mapping[str, str] | None = None,
        trace_context: Any = None,
        timeout: float | None = None,
    ) -> Any:
        response = await send(method, path, data, headers, trace_context, timeout)
        if method != "POST" or path != _SIGNING_UPLOAD:
            return response
        if response.status == 200:
            return response
        if response.status != 401:
            raise OAuthError("Cross-signing key upload was rejected.")
        session, url = _approval_challenge(await _read_challenge(response), issuer)
        if not reset_cross_signing:
            raise OAuthError(
                "Browser approval is required; run 'mmrelay auth login --oauth "
                "--reset-cross-signing' to authorize identity replacement explicitly."
            )
        print("Approve the cross-signing identity reset in your account browser:")
        print(url)
        print("Keep this command running while approving the reset.")
        body = json.loads(data)
        body["auth"] = {"session": session}
        approved_data = json.dumps(body)
        deadline = clock() + _APPROVAL_TIMEOUT
        while clock() < deadline:
            await sleep(min(5.0, deadline - clock()))
            if clock() >= deadline:
                break
            response = await send(
                method, path, approved_data, headers, trace_context, timeout
            )
            if response.status == 200:
                return response
            if response.status != 401:
                raise OAuthError("Cross-signing browser authorization was rejected.")
            challenge = _approval_challenge(await _read_challenge(response), issuer)
            if challenge != (session, url):
                raise OAuthError("Cross-signing browser authorization changed; retry.")
        raise OAuthError("Cross-signing browser approval expired; retry the command.")

    # The guard shares the wrapped transport's exact signature, so callers keep
    # the SDK's own ``send`` type.
    return cast("_SendT", request)


async def self_sign_device(
    session: OAuthSession,
    store: OAuthStore,
    config: dict[str, Any],
    *,
    reset_cross_signing: bool = False,
) -> str | None:
    """Initialize the persistent crypto store and sign only the OAuth device."""
    from mmrelay import matrix_utils as facade

    enabled, store_path = await facade._configure_e2ee(
        config, config.get("matrix"), session.device_id
    )
    if not enabled:
        if reset_cross_signing:
            raise OAuthError("Cross-signing reset requires E2EE and its dependencies.")
        return None
    client = facade._initialize_matrix_client(
        homeserver=session.homeserver,
        user_id=session.user_id,
        device_id=session.device_id,
        e2ee_enabled=True,
        e2ee_store_path=store_path,
        ssl_context=facade._create_ssl_context(),
    )
    try:
        manager = OAuthSessionManager(session, store)
        client.restore_login(session.user_id, session.device_id, session.access_token)
        attach_oauth_session(client, manager)
        client.send = approval_sender(
            client.send, session.issuer, reset_cross_signing=reset_cross_signing
        )
        await facade._maybe_upload_e2ee_keys(client)
        return await facade._ensure_own_device_cross_signed(
            client,
            reset_cross_signing=reset_cross_signing,
            oauth_authenticated=True,
        )
    finally:
        await finish_task(asyncio.create_task(client.close()))
