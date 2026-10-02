"""Native authorization and renewal protocol contracts."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from mmrelay.matrix.oauth import (
    API_SCOPE,
    DEVICE_GRANT,
    DevicePrompt,
    HttpTransport,
    JsonResponse,
    OAuthClient,
    OAuthError,
    OAuthHTTPError,
    OAuthMetadata,
    OAuthSession,
    OAuthTransportError,
    secure_url,
)
from tests.oauth_helpers import FakeServer, metadata, session


def protocol(server: FakeServer) -> OAuthClient:
    return OAuthClient(
        server,
        clock=lambda: server.clock,
        monotonic=lambda: server.clock,
        sleep=server.sleep,
    )


@pytest.mark.asyncio
async def test_device_authorization_creates_and_checks_its_own_device() -> None:
    server = FakeServer()
    display = Mock()
    result = await protocol(server).authorize_device(
        "https://matrix.example.com", display, expected_user_id="@bot:example.com"
    )
    assert result.user_id == "@bot:example.com"
    assert result.device_id.startswith("MMRELAY-")
    registration = next(
        args for _, url, args in server.requests if url.endswith("/register")
    )
    assert registration["json_body"]["grant_types"] == [DEVICE_GRANT, "refresh_token"]
    assert registration["json_body"]["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in registration["json_body"]
    assert server.scope.startswith(API_SCOPE + " ")
    assert result.device_id in server.scope
    assert server.sleeps == [5]
    display.assert_called_once_with(
        DevicePrompt("https://auth.example.com/verify", "TEST-CODE")
    )
    assert OAuthSession.parse(result.credentials()) == result
    assert "test-renewed-access" not in repr(result)
    assert "test-renewed-refresh" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403])
async def test_pending_and_slow_down_obey_poll_intervals(status: int) -> None:
    server = FakeServer()
    server.polls = [
        JsonResponse(status, {"error": "authorization_pending"}),
        JsonResponse(status, {"error": "slow_down"}),
        OAuthTransportError("unavailable"),
    ]
    result = await protocol(server).authorize_device("https://matrix.example.com", Mock())
    assert server.sleeps == [5, 5, 10, 20]
    assert result.user_id == "@bot:example.com"
    assert server.requests[-1][1].endswith("/whoami")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403])
@pytest.mark.parametrize("code", ["access_denied", "expired_token", "invalid_client"])
async def test_rejected_grants_do_not_attempt_password_login(
    code: str, status: int
) -> None:
    server = FakeServer()
    server.polls = [
        JsonResponse(status, {"error": code, "error_description": "test-secret"})
    ]
    with pytest.raises(OAuthHTTPError) as error:
        await protocol(server).authorize_device("https://matrix.example.com", Mock())
    assert code in str(error.value)
    assert error.value.status == status
    assert server.sleeps == [5]
    assert "test-secret" not in str(error.value)
    assert not any(url.endswith("/login") for _, url, _ in server.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403])
async def test_authorization_expires_without_busy_polling(status: int) -> None:
    server = FakeServer()
    server.polls = [JsonResponse(status, {"error": "authorization_pending"})] * 100
    with pytest.raises(OAuthError, match="expired"):
        await protocol(server).authorize_device("https://matrix.example.com", Mock())
    assert sum(server.sleeps) == 300


@pytest.mark.asyncio
async def test_delegation_and_unstable_metadata_fallback() -> None:
    server = FakeServer()
    server.delegation = JsonResponse(
        200, {"m.homeserver": {"base_url": "https://delegated.example.com"}}
    )
    server.stable_status = 404
    result = await protocol(server).authorize_device("https://example.com", Mock())
    assert result.homeserver == "https://delegated.example.com"
    assert any(
        url
        == "https://delegated.example.com/_matrix/client/unstable/org.matrix.msc2965/auth_metadata"
        for _, url, _ in server.requests
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["user", "device"])
async def test_mismatched_identity_is_revoked_before_return(change: str) -> None:
    server = FakeServer()
    if change == "user":
        server.identity_user = "@different:example.com"
    else:
        server.identity_device = "borrowed-device"
    with pytest.raises(OAuthError, match="different account or device"):
        await protocol(server).authorize_device(
            "https://matrix.example.com", Mock(), expected_user_id="@bot:example.com"
        )
    assert server.requests[-1][1].endswith("/revoke")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "expected_token", "expected_hint"),
    [
        ({"token_type": "MAC"}, "test-refresh", "refresh_token"),
        ({"refresh_token": None}, "test-access", "access_token"),
        ({"expires_in": 10**1000}, "test-refresh", "refresh_token"),
        ({"expires_in": 1e-100}, "test-refresh", "refresh_token"),
        ({"expires_in": 1e308}, "test-refresh", "refresh_token"),
        ({"access_token": "test\r\naccess"}, "test-refresh", "refresh_token"),
        ({"refresh_token": "test\r\nrefresh"}, "test\r\nrefresh", "refresh_token"),
        ({"refresh_token": " "}, " ", "refresh_token"),
    ],
)
async def test_invalid_issued_tokens_are_revoked_before_failure(
    changes: dict[str, object], expected_token: str, expected_hint: str
) -> None:
    server = FakeServer()
    body: dict[str, object] = {
        "access_token": "test-access",
        "refresh_token": "test-refresh",
        "token_type": "Bearer",
        "expires_in": 300,
    }
    body.update(changes)
    server.polls = [JsonResponse(200, body)]

    with pytest.raises(OAuthError):
        await protocol(server).authorize_device("https://matrix.example.com", Mock())

    assert server.requests[-1][1].endswith("/revoke")
    assert server.requests[-1][2]["form"] == {
        "token": expected_token,
        "token_type_hint": expected_hint,
        "client_id": "mmrelay-client",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"token_type": "MAC"},
        {"expires_in": 10**1000},
        {"expires_in": 1e-100},
        {"expires_in": 1e308},
        {"access_token": "test\r\naccess"},
        {"refresh_token": "test\r\nrefresh"},
        {"refresh_token": " "},
    ],
)
async def test_invalid_refreshed_tokens_are_revoked_before_failure(
    changes: dict[str, object],
) -> None:
    server = FakeServer()
    current = session()
    server.polls = [
        JsonResponse(
            200,
            {
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
                "token_type": "Bearer",
                "expires_in": 300,
                "scope": current.scope,
                **changes,
            },
        )
    ]

    with pytest.raises(OAuthError):
        await protocol(server).refresh(current)

    assert server.requests[-1][1].endswith("/revoke")
    assert server.requests[-1][2]["form"] == {
        "token": changes.get("refresh_token", "rotated-refresh"),
        "token_type_hint": "refresh_token",
        "client_id": current.client_id,
    }


@pytest.mark.asyncio
async def test_refresh_rotates_credentials_and_revocation_uses_refresh_token() -> None:
    server = FakeServer()
    client = protocol(server)
    result = await client.refresh(session())
    assert result.access_token == "test-renewed-access"
    assert result.refresh_token == "test-renewed-refresh"
    assert result.device_id == session().device_id
    await client.revoke(result)
    assert server.requests[-1][2]["form"] == {
        "token": "test-renewed-refresh",
        "token_type_hint": "refresh_token",
        "client_id": "mmrelay-client",
    }


@pytest.mark.asyncio
async def test_refresh_can_retain_unrotated_refresh_token() -> None:
    server = FakeServer()
    server.polls = [
        JsonResponse(
            200,
            {"access_token": "test-access", "token_type": "Bearer", "expires_in": 300},
        )
    ]
    result = await protocol(server).refresh(session())
    assert result.refresh_token == session().refresh_token


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com/#secret",
        "https://example.com/?token=secret",
        "https://example.com:0",
        "https://example.com:bad",
        "\x00https://example.com/verify",
        "https://example.com/verify\x00",
        "https://example.com/verify\x1b[2J",
        "https://example.com/verify\x7f",
    ],
)
def test_endpoints_reject_unsafe_urls(url: str) -> None:
    with pytest.raises(OAuthError):
        secure_url(url)


@pytest.mark.parametrize(
    "field,value",
    [
        ("grant_types_supported", [{}]),
        ("grant_types_supported", ["authorization_code"]),
        ("token_endpoint_auth_methods_supported", ["client_secret_basic"]),
        ("token_endpoint", "https://other.example.com/token"),
    ],
)
def test_discovery_rejects_unsupported_capabilities(field: str, value: object) -> None:
    record = metadata()
    record[field] = value
    with pytest.raises(OAuthError):
        OAuthMetadata.parse(record)


def test_public_client_method_may_be_confirmed_during_registration() -> None:
    record = metadata()
    del record["token_endpoint_auth_methods_supported"]
    assert OAuthMetadata.parse(record).issuer == "https://auth.example.com/"


@pytest.mark.parametrize(
    "field,value",
    [
        ("expires_in", True),
        ("expires_in", -1),
        ("expires_in", float("inf")),
        ("token_type", "MAC"),
        ("scope", "admin"),
        ("scope", session().scope.replace(" ", "\n")),
    ],
)
@pytest.mark.asyncio
async def test_refresh_rejects_invalid_token_response(
    field: str, value: object
) -> None:
    server = FakeServer()
    body = {
        "access_token": "test-access",
        "refresh_token": "test-refresh",
        "expires_in": 300,
        "token_type": "Bearer",
    }
    body[field] = value
    server.polls = [JsonResponse(200, body)]
    with pytest.raises(OAuthError):
        await protocol(server).refresh(session())


@pytest.mark.asyncio
async def test_transport_reads_all_chunks_and_rejects_redirects() -> None:
    async def chunks(size: int):
        yield b'{"result":'
        yield b'"ok"}'

    response = Mock(status=200)
    response.content.iter_chunked = chunks
    request = Mock()
    request.__aenter__ = AsyncMock(return_value=response)
    request.__aexit__ = AsyncMock(return_value=False)
    transport_session = Mock()
    transport_session.request.return_value = request
    context = Mock()
    context.__aenter__ = AsyncMock(return_value=transport_session)
    context.__aexit__ = AsyncMock(return_value=False)
    with patch("mmrelay.matrix.oauth.aiohttp.ClientSession", return_value=context):
        result = await HttpTransport().request(
            "POST", "https://auth.example.com/token", form={"token": "test-secret"}
        )
        assert result.body == {"result": "ok"}
        assert transport_session.request.call_args.kwargs["allow_redirects"] is False
        response.status = 302
        with pytest.raises(OAuthError, match="redirects"):
            await HttpTransport().request("GET", "https://auth.example.com/token")


@pytest.mark.asyncio
async def test_html_error_preserves_status_without_server_body() -> None:
    async def chunks(size: int):
        yield b"<html>not found: test-secret</html>"

    response = Mock(status=404)
    response.content.iter_chunked = chunks
    request = Mock()
    request.__aenter__ = AsyncMock(return_value=response)
    request.__aexit__ = AsyncMock(return_value=False)
    session_context = Mock()
    session_context.__aenter__ = AsyncMock(
        return_value=Mock(request=Mock(return_value=request))
    )
    session_context.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "mmrelay.matrix.oauth.aiohttp.ClientSession", return_value=session_context
    ):
        result = await HttpTransport().request(
            "GET", "https://matrix.example.com/.well-known/matrix/client"
        )
        assert result == JsonResponse(404, {})
