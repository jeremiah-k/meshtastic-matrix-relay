"""Normalize a Meshtastic sender to one canonical node-id form.

``events.py`` resolves the sender as ``packet.get("fromId") or
packet.get("from")``. mtjk populates ``fromId`` with the ``!hex`` node id but
sets it to ``None`` whenever the node is missing from ``nodes_by_num``, has no
``user`` payload, or ``user.id`` is not a ``str``. The fallback is then the raw
mesh integer.

The name tables are keyed with ``id_key = str(meshtastic_id)`` and no
normalization, so a sender arriving as ``4134515116`` is looked up and saved
under a key that can never collide with the ``!f66fb1ac`` form used for every
named node. Normalizing once, before any lookup or save, keeps both paths
keying identically.
"""

from __future__ import annotations

import pytest

from mmrelay.meshtastic.node_identity import normalize_node_id


class TestNormalizeNodeId:
    """Canonicalize raw numeric senders to the !hex node-id form."""

    def test_int_becomes_hex_form(self) -> None:
        assert normalize_node_id(4134515116) == "!f66fb1ac"

    def test_zero_pad_to_eight_digits(self) -> None:
        assert normalize_node_id(0x50208197) == "!50208197"

    def test_hex_string_is_returned_unchanged(self) -> None:
        assert normalize_node_id("!f66fb1ac") == "!f66fb1ac"

    @pytest.mark.parametrize("special", ["^all", "Unknown", "!ffffffff"])
    def test_special_ids_pass_through(self, special: str) -> None:
        """Broadcast and sentinel forms are already node-id shaped."""
        assert normalize_node_id(special) == special

    def test_none_passes_through(self) -> None:
        assert normalize_node_id(None) is None

    @pytest.mark.parametrize(
        "value", ["", "not-an-id", "  ", "0x1f", "12.5", b"1234", [], {}]
    )
    def test_non_numeric_values_pass_through(self, value: object) -> None:
        """Anything that is not a clean node number is left alone."""
        assert normalize_node_id(value) == value

    def test_bool_is_not_treated_as_a_node_number(self) -> None:
        """bool is an int subclass; it must not become !00000001."""
        assert normalize_node_id(True) is True

    def test_out_of_range_int_passes_through(self) -> None:
        """Node numbers are 32-bit; anything larger is not a node id."""
        assert normalize_node_id(2**32) == 2**32

    def test_raw_int_and_hex_form_reconcile(self) -> None:
        """The two representations of one node must key identically.

        db_utils keys the name tables with str(meshtastic_id). A sender
        arriving as a raw int must resolve to the same key as the same node
        arriving in !hex form, or the cache can never satisfy the lookup.
        """
        raw = 4134515116
        hexed = "!f66fb1ac"

        assert normalize_node_id(raw) == hexed
        # str() is how db_utils derives the primary key from either form.
        assert str(normalize_node_id(raw)) == str(normalize_node_id(hexed))

    def test_matches_nodeNumToId_convention(self) -> None:
        """Matches the !hex convention mtjk uses for known nodes."""
        assert normalize_node_id(0x67CFFEC0) == "!67cffec0"
        assert normalize_node_id(0x67CFFEC0) == normalize_node_id("!67cffec0")
