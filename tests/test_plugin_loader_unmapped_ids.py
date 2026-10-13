"""Tests for collecting plugin-declared unmapped room IDs."""

from typing import Any

from mmrelay.plugin_loader import get_declared_unmapped_room_ids


class _FakePlugin:
    def __init__(
        self,
        name: str,
        handles: bool = True,
        declared: Any = None,
        raise_on_call: bool = False,
    ) -> None:
        self.plugin_name = name
        self.handles_unmapped_rooms = handles
        self._declared = declared
        self._raise_on_call = raise_on_call

    def get_unmapped_room_ids(self) -> Any:
        if self._raise_on_call:
            raise RuntimeError("boom")
        return self._declared


def _collect(plugins: list[Any]) -> list[str]:
    from unittest.mock import patch

    with patch("mmrelay.plugin_loader.load_plugins", return_value=plugins):
        return get_declared_unmapped_room_ids()


def test_collects_declared_room_ids_in_declaration_order() -> None:
    collected = _collect(
        [
            _FakePlugin("a", declared=["!first", "!Ah4tQ7vZn9WkP2mXrB6y"]),
            _FakePlugin("b", declared=("!second:matrix.org",)),
        ]
    )
    assert collected == ["!first", "!Ah4tQ7vZn9WkP2mXrB6y", "!second:matrix.org"]


def test_passes_domain_less_ids_through_verbatim() -> None:
    room_id = "!Ah4tQ7vZn9WkP2mXrB6yJc8sLd3FgUeH5iK1oN0pRwz"
    assert _collect([_FakePlugin("a", declared=[room_id])]) == [room_id]


def test_deduplicates_across_plugins() -> None:
    collected = _collect(
        [
            _FakePlugin("a", declared=["!room"]),
            _FakePlugin("b", declared=["!room", "!other"]),
        ]
    )
    assert collected == ["!room", "!other"]


def test_skips_plugins_not_handling_unmapped_rooms() -> None:
    assert _collect([_FakePlugin("a", handles=False, declared=["!room"])]) == []


def test_skips_non_sequence_and_non_string_declarations() -> None:
    collected = _collect(
        [
            _FakePlugin("a", declared=None),
            _FakePlugin("b", declared="!not-a-sequence"),
            _FakePlugin("c", declared=["!ok", 42, None, ""]),
        ]
    )
    assert collected == ["!ok"]


def test_plugin_errors_are_isolated() -> None:
    collected = _collect(
        [
            _FakePlugin("a", raise_on_call=True),
            _FakePlugin("b", declared=["!room"]),
        ]
    )
    assert collected == ["!room"]


def test_empty_when_no_plugins_declare_rooms() -> None:
    assert _collect([]) == []
