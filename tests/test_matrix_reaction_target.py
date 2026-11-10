"""Guard against treating unresolvable Matrix emotes as radio reactions."""

import pytest

from mmrelay.matrix.reaction_target import has_emote_reaction_target


@pytest.mark.parametrize("packet_id", [0, 1, "0"])
def test_bare_reply_id_is_not_an_addressable_reaction(packet_id):
    assert not has_emote_reaction_target(None, packet_id, None, "LocalMesh")
    assert not has_emote_reaction_target(None, packet_id, "LocalMesh", "LocalMesh")


@pytest.mark.parametrize("packet_id", [0, 1, "0"])
def test_remote_meshnet_can_resolve_reaction_to_radio_packet(packet_id):
    assert has_emote_reaction_target(None, packet_id, "RemoteMesh", "LocalMesh")


def test_native_matrix_target_is_enough_even_without_radio_packet():
    assert has_emote_reaction_target("$original", None, None, "LocalMesh")


def test_missing_or_empty_radio_id_cannot_supply_target():
    assert not has_emote_reaction_target(None, None, "RemoteMesh", "LocalMesh")
    assert not has_emote_reaction_target(None, "", "RemoteMesh", "LocalMesh")
