"""Peer authorization and canonical source claims at the Matrix boundary."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from mmrelay.matrix.canonical_ingress import canonicalize_matrix_event
from mmrelay.matrix.canonical_outbound import render_matrix_relay_content
from mmrelay.matrix.peer_context import (
    load_peer_relays,
    radio_packet_id,
    trusted_peer_metadata,
)

PEER = "@peer:example.org"
SELF = "@relay:example.org"
ROOM = "!room:example.org"


@pytest.fixture
async def peer_content():
    return await render_matrix_relay_content(
        room_id=ROOM,
        message="Visible message",
        longname="Alice",
        shortname="AL",
        meshnet_name="Remote",
        portnum=1,
        source_node_id="!000000ab",
        meshtastic_id=0,
    )


def canonical(content, sender=PEER):
    event = SimpleNamespace(
        sender=sender,
        event_id="$event",
        server_timestamp=1700000000000,
        body=content["body"],
        source={
            "type": "m.room.message",
            "content": content,
            "sender": sender,
            "event_id": "$event",
        },
    )
    return canonicalize_matrix_event(SimpleNamespace(room_id=ROOM), event)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "@peer:example.org",
        ["*"],
        ["@*:example.org"],
        [" @peer:example.org"],
        [True],
        [{}],
        ["@peer"],
        ["@peer:server/path"],
        ["@peer:*.example.org"],
        ["@peer:server\x00"],
    ],
)
def test_peer_allowlist_rejects_ambiguous_or_invalid_entries(value):
    with pytest.raises(ValueError, match="trusted_relays"):
        load_peer_relays({"matrix": {"trusted_relays": value}})


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, 0),
        ("0", 0),
        (2**32 - 1, 2**32 - 1),
        (True, None),
        (-1, None),
        (2**32, None),
        ("-1", None),
        ("1.0", None),
        (" 1", None),
        ("١", None),
        (None, None),
    ],
)
def test_radio_packet_ids_are_exact_unsigned_integers(value, expected):
    assert radio_packet_id(value) == expected


async def test_only_exact_authorized_matrix_sender_can_supply_peer_metadata(
    peer_content,
):
    peers = load_peer_relays({"matrix": {"trusted_relays": [PEER]}})
    for sender in ["@human:example.org", PEER.upper()]:
        assert (
            trusted_peer_metadata(canonical(peer_content, sender), sender, SELF, peers)
            is None
        )
    for sender in [PEER, SELF]:
        metadata = trusted_peer_metadata(
            canonical(peer_content, sender), sender, SELF, peers
        )
        assert metadata is not None
        assert (metadata.longname, metadata.shortname) == ("Alice", "AL")
        assert metadata.packet_id == 0
        assert metadata.portnum == 1
        assert metadata.meshnet == "Remote"


@pytest.mark.parametrize(
    "field,value",
    [
        ("canonical_event_id", "forged"),
        ("source_adapter", "other"),
        ("source_transport_id", "other"),
        ("source_channel", "other"),
        ("source_native_ref", None),
        ("event_kind", "message.deleted"),
        ("schema_version", True),
    ],
)
async def test_conflicting_provenance_never_becomes_trusted(peer_content, field, value):
    content = deepcopy(peer_content)
    content["medre"]["envelope"][field] = value
    assert (
        trusted_peer_metadata(
            canonical(content),
            PEER,
            SELF,
            load_peer_relays({"matrix": {"trusted_relays": [PEER]}}),
        )
        is None
    )


@pytest.mark.parametrize("key", ["🔥", "❤️", "👍🏽"])
async def test_radio_reaction_carries_exact_key_and_packet_zero(key):
    content = await render_matrix_relay_content(
        room_id=ROOM,
        message="Visible reaction",
        longname="Alice",
        shortname="AL",
        meshnet_name="Remote",
        portnum=1,
        source_node_id="!000000ab",
        meshtastic_id=3,
        meshtastic_replyId=0,
        emoji=True,
        reaction_key=key,
    )
    metadata = trusted_peer_metadata(
        canonical(content),
        PEER,
        SELF,
        load_peer_relays({"matrix": {"trusted_relays": [PEER]}}),
    )
    assert metadata is not None
    assert metadata.relation.key == key
    assert metadata.relation.target_native_ref.native_message_id == "0"


@pytest.mark.parametrize("matrix", [[], False, 0, ""])
def test_non_mapping_matrix_section_rejects_peer_configuration(matrix):
    with pytest.raises(ValueError, match="matrix must be a mapping"):
        load_peer_relays({"matrix": matrix})


def test_startup_rejects_invalid_peers_before_mutating_matrix_module_state():
    from mmrelay.config import set_config

    module = SimpleNamespace(
        __name__="mmrelay.matrix_utils",
        config={"original": True},
        matrix_rooms=["original"],
    )
    with pytest.raises(ValueError, match="trusted_relays"):
        set_config(module, {"matrix": {"trusted_relays": ["*"]}, "matrix_rooms": []})
    assert module.config == {"original": True}
    assert module.matrix_rooms == ["original"]


async def test_malformed_peer_claim_text_cannot_abort_matrix_relay(peer_content):
    """Reject unencodable claims without treating them as authenticated metadata."""
    import msgspec
    from msgspec.structs import replace
    from medre.core.events import NativeMetadata

    event = canonical(peer_content)
    native = msgspec.json.decode(msgspec.json.encode(event.metadata.native.data))
    native["matrix"]["relay"]["bridge_context"]["source"][
        "sender_name"
    ] = "bad\ud800name"
    malformed = replace(
        event,
        metadata=replace(event.metadata, native=NativeMetadata(data=native)),
    )
    assert (
        trusted_peer_metadata(
            malformed,
            PEER,
            SELF,
            load_peer_relays({"matrix": {"trusted_relays": [PEER]}}),
        )
        is None
    )
