"""Tests for shared room identifier semantics."""

import pytest

from mmrelay.matrix.room_ids import is_room_alias, is_room_id


@pytest.mark.parametrize(
    "value",
    [
        "!Ah4tQ7vZn9WkP2mXrB6yJc8sLd3FgUeH5iK1oN0pRwz",  # room version 12+
        "!opaque:server.example",
        "!opaque:matrix.org",
        "!another:server.example",
        "!unicode-Ω:server.example",  # legacy localparts remain opaque
        "!abc:[2001:db8::1]:8448",
    ],
)
def test_room_ids_accept_both_forms(value: str) -> None:
    assert is_room_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "!",
        "!room:",  # colon without a domain
        "  !opaque:server.example  ",  # exact configured ID must match
        "!opaque:server.example\n",  # no silent normalization
        "!opaque:server.example with space",
        "!:server",  # empty localpart
        "!opaque:bad/server",
        "!opaque:bad:port",
        "!opaque:bad:999999",
        "!opaque:[not-an-ipv6]",
        "!opaque:[fe80::1%eth0]",  # IPv6 zone IDs are not Matrix server names
        "!\ud800:server.example",  # cannot encode as UTF-8
        "!opaque:server..example",
        "!opaque:example.-bad",
        "!opaque:example.org:0",
        "!opaque:example.org?query",
        "!opaque:example.org#fragment",
        "!bad?local",  # not a v12 URL-safe base64 localpart
        "!" + "a" * 255,  # 256 bytes including the sigil
        "#room:server",  # alias
        "room:server",
        "https://matrix.to/#/!room:server",
        None,
        42,
        ["!room:server"],
    ],
)
def test_room_ids_reject_non_ids(value: object) -> None:
    assert not is_room_id(value)


@pytest.mark.parametrize(
    "value",
    ["#room:server.example", "#a:b", "#room:[::1]:443"],
)
def test_room_aliases_accept_valid_forms(value: str) -> None:
    assert is_room_alias(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "#",
        "#room",
        "#:server",
        "#alias:bad/path",
        "#alias:[fe80::1%eth0]",
        "#\ud800:server.example",
        "#alias:bad:port",
        "#alias:example.org:123456",
        "#alias:example..org",
        "#" + "x" * 255 + ":s",
        "!room:server",
        "room:server",
        "  #room:server.example  ",
        "#room:server.example\t",
        None,
        42,
    ],
)
def test_room_aliases_reject_invalid_forms(value: object) -> None:
    assert not is_room_alias(value)


@pytest.mark.parametrize(
    "value",
    ["\t!room:server", "!room:server\r", "!room:ser ver", "!room:ser\nver"],
)
def test_room_ids_never_normalize_configured_whitespace(value: str) -> None:
    assert not is_room_id(value)
