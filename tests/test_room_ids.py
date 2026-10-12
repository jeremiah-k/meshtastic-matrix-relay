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
    ["#room:server.example", "#a:b"],
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
