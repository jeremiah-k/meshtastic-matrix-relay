"""Operator input normalization preserves protocol endpoint and account boundaries."""

from unittest.mock import Mock

import pytest

from mmrelay.matrix.auth_input import normalize_homeserver
from mmrelay.matrix.oauth import JsonResponse, OAuthClient, OAuthError
from tests.oauth_helpers import FakeServer


@pytest.mark.asyncio
@pytest.mark.parametrize("homeserver", [" example.com ", "https://example.com/"])
@pytest.mark.parametrize("username", ["bot", "@bot", "@bot:example.com"])
async def test_oauth_bare_inputs_follow_delegation_and_bind_the_approved_account(
    homeserver: str, username: str
) -> None:
    server = FakeServer()
    server.delegation = JsonResponse(
        200, {"m.homeserver": {"base_url": "https://matrix.example.com"}}
    )
    client = OAuthClient(
        server, clock=lambda: server.clock, monotonic=lambda: server.clock,
        sleep=server.sleep,
    )
    result = await client.authorize_device(
        homeserver, Mock(), expected_user_id=username
    )
    assert server.requests[0][1] == "https://example.com/.well-known/matrix/client"
    assert result.homeserver == "https://matrix.example.com"
    assert result.user_id == "@bot:example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize("username", ["other", "@bot:other.example.com"])
async def test_oauth_rejects_and_revokes_a_different_approved_account(
    username: str,
) -> None:
    server = FakeServer()
    client = OAuthClient(server, clock=lambda: server.clock, sleep=server.sleep)
    with pytest.raises(OAuthError, match="different account"):
        await client.authorize_device(
            "matrix.example.com", Mock(), expected_user_id=username
        )
    assert server.requests[-1][1].endswith("/revoke")


@pytest.mark.asyncio
@pytest.mark.parametrize("homeserver", [
    "http://example.com", "ftp://example.com", "https://user:secret@example.com",
    "example.com?redirect=other", "example.com#fragment", "example.com\n",
    "https://example.com:0", "https://example.com:invalid", "", " ",
])
async def test_oauth_invalid_server_inputs_make_no_network_request(
    homeserver: str,
) -> None:
    server = FakeServer()
    with pytest.raises(OAuthError):
        await OAuthClient(server).discover(homeserver)
    assert not server.requests


@pytest.mark.parametrize("source, expected", [
    (" SERVER.COM:443/ ", "https://server.com"),
    ("https://SERVER.COM/path/", "https://server.com/path"),
    ("http://localhost:8008/", "http://localhost:8008"),
    ("[::1]:8448", "https://[::1]:8448"),
])
def test_password_server_input_keeps_explicit_scheme_port_and_path(
    source: str, expected: str
) -> None:
    assert normalize_homeserver(source) == expected
