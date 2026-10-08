"""Browser cross-signing approval stays bounded and never uses password UIA."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest

from mmrelay.matrix.oauth import OAuthError
from mmrelay.matrix.oauth_e2ee import approval_sender

_UPLOAD = "/_matrix/client/v3/keys/device_signing/upload"
_URL = "https://auth.example.com/account?action=org.matrix.cross_signing_reset"
_KEYS = {"master_key": {"keys": {"ed25519:test": "test-public-key"}}}


def challenge(stage: str = "m.oauth") -> dict[str, Any]:
    return {
        "session": "test-uia-session",
        "flows": [{"stages": [stage]}],
        "params": {stage: {"url": _URL}},
    }


class Response:
    def __init__(self, status: int, body: object) -> None:
        self.status = status
        self.raw = json.dumps(body).encode()
        self.content = self
        self.released = False

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for start in range(0, len(self.raw), size):
            yield self.raw[start : start + size]

    def release(self) -> None:
        self.released = True


class Server:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.sleeps: list[float] = []
        self.now = 0.0

    async def send(
        self,
        method: str,
        path: str,
        data: str,
        headers: object = None,
        trace_context: object = None,
        timeout: object = None,
    ) -> Response:
        self.requests.append((method, path, json.loads(data)))
        return self.responses.pop(0)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def sender(self, *, reset: bool = True) -> Callable[..., Awaitable[Response]]:
        return approval_sender(
            self.send,
            "https://auth.example.com/",
            reset_cross_signing=reset,
            clock=lambda: self.now,
            sleep=self.sleep,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["m.oauth", "org.matrix.cross_signing_reset"])
async def test_browser_approval_retries_same_keys_with_only_uia_session(
    stage: str, capsys
) -> None:
    server = Server(
        [
            Response(401, challenge(stage)),
            Response(401, challenge(stage)),
            Response(200, {}),
        ]
    )

    result = await server.sender()("POST", _UPLOAD, json.dumps(_KEYS))

    assert result.status == 200
    assert server.sleeps == [5.0, 5.0]
    assert server.requests[0][2] == _KEYS
    assert all(
        body == {**_KEYS, "auth": {"session": "test-uia-session"}}
        for _, _, body in server.requests[1:]
    )
    assert all(path == _UPLOAD for _, path, _ in server.requests)
    output = capsys.readouterr().out
    assert _URL in output
    assert "test-uia-session" not in output


@pytest.mark.asyncio
async def test_upload_challenge_requires_explicit_identity_reset() -> None:
    server = Server([Response(401, challenge())])

    with pytest.raises(OAuthError, match="explicitly"):
        await server.sender(reset=False)("POST", _UPLOAD, json.dumps(_KEYS))

    assert len(server.requests) == 1
    assert server.sleeps == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://auth.example.com/account",
        "https://untrusted.example.com/account",
        "https://user:secret@auth.example.com/account",
        "https://auth.example.com/account\nunsafe",
    ],
)
async def test_invalid_approval_url_is_not_displayed(url: str, capsys) -> None:
    body = challenge()
    body["params"]["m.oauth"]["url"] = url
    server = Server([Response(401, body)])

    with pytest.raises(OAuthError):
        await server.sender()("POST", _UPLOAD, json.dumps(_KEYS))

    assert capsys.readouterr().out == ""
    assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_password_challenge_does_not_fall_back_to_password() -> None:
    server = Server([Response(401, challenge("m.login.password"))])

    with pytest.raises(OAuthError, match="does not offer OAuth"):
        await server.sender()("POST", _UPLOAD, json.dumps(_KEYS))

    assert len(server.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_denied_browser_approval_stops_without_more_polling(status: int) -> None:
    server = Server(
        [
            Response(401, challenge()),
            Response(status, {"errcode": "M_FORBIDDEN", "error": "test-secret"}),
        ]
    )

    with pytest.raises(OAuthError, match="rejected") as error:
        await server.sender()("POST", _UPLOAD, json.dumps(_KEYS))

    assert "test-secret" not in str(error.value)
    assert len(server.requests) == 2
    assert server.sleeps == [5.0]


@pytest.mark.asyncio
async def test_browser_approval_deadline_stops_pending_responses() -> None:
    server = Server([Response(401, challenge()) for _ in range(30)])

    with pytest.raises(OAuthError, match="expired"):
        await server.sender()("POST", _UPLOAD, json.dumps(_KEYS))

    assert sum(server.sleeps) == 120.0
    assert len(server.requests) == 24


@pytest.mark.asyncio
async def test_changed_browser_challenge_stops_polling() -> None:
    replacement = challenge()
    replacement["session"] = "other-session"
    server = Server([Response(401, challenge()), Response(401, replacement)])

    with pytest.raises(OAuthError, match="changed"):
        await server.sender()("POST", _UPLOAD, json.dumps(_KEYS))

    assert len(server.requests) == 2


@pytest.mark.asyncio
async def test_oversized_challenge_releases_response_without_display(capsys) -> None:
    response = Response(401, {**challenge(), "extra": "X" * 65536})
    server = Server([response])

    with pytest.raises(OAuthError, match="exceeds"):
        await server.sender()("POST", _UPLOAD, json.dumps(_KEYS))

    assert response.released
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
async def test_browser_approval_cancellation_does_not_submit_more_keys() -> None:
    server = Server([Response(401, challenge())])

    async def cancel(seconds: float) -> None:
        raise asyncio.CancelledError

    sender = approval_sender(
        server.send,
        "https://auth.example.com/",
        reset_cross_signing=True,
        sleep=cancel,
    )
    with pytest.raises(asyncio.CancelledError):
        await sender("POST", _UPLOAD, json.dumps(_KEYS))

    assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_unrelated_401_is_returned_without_uia_replay() -> None:
    response = Response(401, challenge())
    server = Server([response])

    result = await server.sender()("POST", "/_matrix/client/v3/keys/query", "{}")

    assert result is response
    assert len(server.requests) == 1
    assert server.sleeps == []
