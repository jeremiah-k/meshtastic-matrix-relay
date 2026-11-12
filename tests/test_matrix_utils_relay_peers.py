"""Canonical peers pass through the actual codec, trust gate and radio queue."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mmrelay.matrix.canonical_outbound import render_matrix_relay_content

ROOM = "!room:example.org"
PEER = "@peer:example.org"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("reset_matrix_utils_globals"),
]


@pytest.fixture
def relay(monkeypatch):
    import mmrelay.matrix_utils as facade

    interface = MagicMock()
    queue = MagicMock(return_value=True)
    config = {
        "matrix": {"trusted_relays": [PEER]},
        "meshtastic": {"meshnet_name": "Local", "broadcast_enabled": True},
    }
    room = SimpleNamespace(room_id=ROOM, encrypted=False)
    for name, value in {
        "config": config,
        "matrix_rooms": [{"id": ROOM, "meshtastic_channel": 0}],
        "bot_start_time": 0,
        "bot_user_id": "@relay:example.org",
        "get_interaction_settings": lambda _: {"replies": True, "reactions": True},
        "message_storage_enabled": lambda _: False,
        "get_user_display_name": AsyncMock(return_value="Matrix user"),
        "get_meshtastic_prefix": lambda *args: "Human: ",
        "get_matrix_prefix": lambda cfg, long, short, mesh: f"{short}/{mesh}: ",
        "_get_meshtastic_interface_and_channel": AsyncMock(return_value=(interface, 0)),
        "queue_message": queue,
        "get_message_queue": MagicMock(
            return_value=SimpleNamespace(get_queue_size=lambda: 0)
        ),
    }.items():
        monkeypatch.setattr(facade, name, value)
    monkeypatch.setattr("mmrelay.plugin_loader.load_plugins", lambda: [])

    async def deliver(content, sender=PEER, reaction=False):
        event = SimpleNamespace(
            sender=sender,
            event_id="$incoming",
            server_timestamp=1700000000000,
            body=content.get("body", ""),
            source={
                "type": "m.reaction" if reaction else "m.room.message",
                "content": content,
                "sender": sender,
                "event_id": "$incoming",
                "origin_server_ts": 1700000000000,
            },
        )
        if reaction:
            from nio import ReactionEvent

            relation = content.get("m.relates_to", {})
            native_event = ReactionEvent(
                source=event.source,
                reacts_to=relation.get("event_id", "$target"),
                key=relation.get("key", ""),
            )
            vars(native_event).update(vars(event))
            native_event.source["type"] = "m.reaction"
            event = native_event

        await facade.on_room_message(room, event)

    return SimpleNamespace(
        deliver=deliver, queue=queue, config=config, interface=interface
    )


async def content(**overrides):
    values = dict(
        room_id=ROOM,
        message="Visible radio text",
        longname="Alice",
        shortname="AL",
        meshnet_name="Remote",
        portnum=1,
        source_node_id="!000000ab",
        meshtastic_id=0,
    )
    values.update(overrides)
    return await render_matrix_relay_content(**values)


@pytest.mark.parametrize(
    "sender,trusted", [(PEER, True), ("@human:example.org", False)]
)
async def test_only_allowlisted_peers_supply_names_and_routing(relay, sender, trusted):
    wire = await content()
    wire.update(
        meshtastic_text="HIDDEN",
        meshtastic_longname="Spoof",
        meshtastic_meshnet="Local",
    )
    await relay.deliver(wire, sender)
    relay.queue.assert_called_once()
    text = relay.queue.call_args.kwargs["text"]
    assert text == (
        "AL/Remo: Visible radio text" if trusted else "Human: Visible radio text"
    )
    assert "HIDDEN" not in text and "Spoof" not in text


@pytest.mark.parametrize("character", ["A", "🔥"])
async def test_remote_peer_prefix_fits_radio_byte_budget(relay, character):
    """Truncate the prefixed radio payload, not only the authored peer body."""
    from mmrelay.matrix_utils import DEFAULT_MESSAGE_TRUNCATE_BYTES

    wire = await content(message=character * 400)
    await relay.deliver(wire)

    relay.queue.assert_called_once()
    queued = relay.queue.call_args.kwargs["text"]
    assert queued.startswith("AL/Remo: ")
    assert len(queued.encode("utf-8")) <= DEFAULT_MESSAGE_TRUNCATE_BYTES


async def test_peer_on_local_mesh_is_suppressed_but_untrusted_claim_is_ignored(relay):
    wire = await content(meshnet_name="Local")
    await relay.deliver(wire)
    relay.queue.assert_not_called()
    await relay.deliver(wire, "@human:example.org")
    relay.queue.assert_called_once()


@pytest.mark.parametrize(
    ("authored_body", "radio_body"),
    [
        ("Alice reacted 🔥", "Alice reacted 🔥"),
        (
            '\n [AL/Remote]: reacted 🔥 to "hello"',
            '[AL/Remote]: reacted 🔥 to "hello"',
        ),
        (
            'custom-origin-prefix: reacted 🔥 to "hello"',
            'custom-origin-prefix: reacted 🔥 to "hello"',
        ),
    ],
)
@pytest.mark.parametrize("target_namespace", ["radio", "matrix"])
async def test_remote_packet_zero_reaction_preserves_authored_visible_text(
    relay, authored_body, radio_body, target_namespace, monkeypatch
):
    """Do not double-prefix MEDRE radio reaction bodies, regardless of format."""
    wire = await content(
        message=authored_body,
        emoji=True,
        meshtastic_replyId=0,
        reaction_key="🔥",
    )
    if target_namespace == "matrix":
        wire["medre"]["metadata"]["bridge_context"]["relations"][0][
            "target_native_ref"
        ] = {
            "adapter": "mmrelay.matrix",
            "native_channel_id": ROOM,
            "native_message_id": "$original",
            "native_thread_id": None,
        }
        monkeypatch.setattr(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
            lambda event_id: (
                (0, ROOM, "stored preview", "Local")
                if event_id == "$original"
                else None
            ),
        )
    await relay.deliver(wire)
    relay.queue.assert_called_once()
    assert relay.queue.call_args.kwargs["text"] == radio_body
    if target_namespace == "matrix":
        from mmrelay.matrix_utils import send_text_reply

        assert relay.queue.call_args.args[0] is send_text_reply
        assert relay.queue.call_args.kwargs["reply_id"] == 0
    else:
        assert relay.queue.call_args.args[0] == relay.interface.sendText
        assert "reply_id" not in relay.queue.call_args.kwargs


@pytest.mark.parametrize("hint_adapter", ["medre", "other.bridge.example"])
async def test_peer_reaction_matrix_native_target_from_peer_adapter_resolves_through_message_map(
    relay, monkeypatch, hint_adapter
):
    """A peer-namespaced Matrix-native reaction target resolves via the local map.

    Trusted peers hint relation targets under their own adapter id (MEDRE
    emits ``adapter: "medre"`` with the tapped packet resolved to its
    canonical Matrix event). Recognition is by the "$"-prefixed event id in
    the current room, resolved against this relay's own message map: the
    authored body is relayed verbatim as a structured radio reply to the
    original packet, with no locally inferred prefix.
    """
    from mmrelay.matrix_utils import send_text_reply

    # Bracketed prefix format like the default radio prefix the live MEDRE
    # body was authored with ([meshnet label]: ...).
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_matrix_prefix",
        lambda _cfg, _long, short, mesh: f"[{short}/{mesh}]: ",
        raising=False,
    )
    # The live MEDRE reacted fallback body renders with a leading newline and
    # the authored radio prefix.
    authored_body = '\n [AL/Remote]: reacted 🦄 to ""'
    wire = await content(
        message=authored_body,
        emoji=True,
        meshtastic_replyId=0,
        reaction_key="🦄",
    )
    wire["medre"]["metadata"]["bridge_context"]["relations"][0]["target_native_ref"] = {
        "adapter": hint_adapter,
        "native_channel_id": ROOM,
        "native_message_id": "$original",
        "native_thread_id": None,
    }
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
        lambda event_id: (
            # 1448844033 is the relay-side packet id observed in the live
            # interop evidence for this reaction shape.
            (1448844033, ROOM, "stored preview", "Local")
            if event_id == "$original"
            else None
        ),
    )
    await relay.deliver(wire)
    relay.queue.assert_called_once()
    assert relay.queue.call_args.args[0] is send_text_reply
    assert relay.queue.call_args.kwargs["reply_id"] == 1448844033
    assert relay.queue.call_args.kwargs["text"] == '[AL/Remote]: reacted 🦄 to ""'


async def test_unmapped_matrix_native_peer_reaction_does_not_reprefix_or_relay(
    relay, monkeypatch
):
    """An unresolvable Matrix-native peer reaction target must not re-prefix.

    When the hinted "$" event id has no row in this relay's message map the
    reaction follows the existing unmapped-target behavior: it is dropped,
    never silently re-prefixed through the plain remote-meshnet path.
    """
    logger = MagicMock()
    monkeypatch.setattr("mmrelay.matrix_utils.logger", logger, raising=False)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_matrix_prefix",
        lambda _cfg, _long, short, mesh: f"[{short}/{mesh}]: ",
        raising=False,
    )
    authored_body = '\n [AL/Remote]: reacted 🦄 to ""'
    wire = await content(
        message=authored_body,
        emoji=True,
        meshtastic_replyId=0,
        reaction_key="🦄",
    )
    wire["medre"]["metadata"]["bridge_context"]["relations"][0]["target_native_ref"] = {
        "adapter": "medre",
        "native_channel_id": ROOM,
        "native_message_id": "$unmapped",
        "native_thread_id": None,
    }
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
        lambda _event_id: None,
    )
    await relay.deliver(wire)
    relay.queue.assert_not_called()
    assert any(
        "Reaction target is not mapped" in call.args[0]
        for call in logger.debug.call_args_list
    )


async def test_peer_reaction_target_outside_room_keeps_plain_prefixed_remote_relay(
    relay,
    monkeypatch,
):
    """A Matrix-native hint outside this room stays an unrecognized reaction.

    Documented existing behavior: a reaction target the relay cannot bind
    (here a "$" event id scoped to a different room) falls through to the
    ordinary remote-meshnet path, which strips the authored prefix and
    re-prefixes with the local abbreviated meshnet label.
    """
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_matrix_prefix",
        lambda _cfg, _long, short, mesh: f"[{short}/{mesh}]: ",
        raising=False,
    )
    authored_body = '\n [AL/Remote]: reacted 🦄 to ""'
    wire = await content(
        message=authored_body,
        emoji=True,
        meshtastic_replyId=0,
        reaction_key="🦄",
    )
    wire["medre"]["metadata"]["bridge_context"]["relations"][0]["target_native_ref"] = {
        "adapter": "medre",
        "native_channel_id": "!elsewhere:example.org",
        "native_message_id": "$original",
        "native_thread_id": None,
    }
    await relay.deliver(wire)
    relay.queue.assert_called_once()
    assert relay.queue.call_args.args[0] == relay.interface.sendText
    assert "reply_id" not in relay.queue.call_args.kwargs
    assert relay.queue.call_args.kwargs["text"] == '[AL/Remo]: reacted 🦄 to ""'


@pytest.mark.parametrize("body", ["", " \t ", "\n"])
@pytest.mark.parametrize("matrix_target", [False, True])
async def test_empty_peer_reaction_never_sends_radio_reply(
    relay, monkeypatch, body, matrix_target
):
    """A trusted reaction key does not authorize an empty radio payload."""
    wire = await content(
        message="   ", emoji=True, meshtastic_replyId=0, reaction_key="👍"
    )
    wire["body"] = body
    wire.pop("formatted_body", None)
    wire.pop("format", None)
    if matrix_target:
        wire["medre"]["metadata"]["bridge_context"]["relations"][0][
            "target_native_ref"
        ] = {
            "adapter": "mmrelay.matrix",
            "native_channel_id": ROOM,
            "native_message_id": "$original",
            "native_thread_id": None,
        }
    lookup = MagicMock(return_value=(0, ROOM, "stored preview", "Local"))
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_message_map_by_matrix_event_id", lookup
    )
    await relay.deliver(wire)
    lookup.assert_not_called()
    relay.queue.assert_not_called()


@pytest.mark.parametrize("enabled", [False, True])
async def test_untargeted_emote_keeps_body_with_reactions_enabled_or_disabled(
    relay, enabled, monkeypatch
):
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_interaction_settings",
        lambda _: {"replies": True, "reactions": enabled},
    )
    wire = await content(message="waves hello", emote=True)
    wire.update(meshtastic_replyId=0, meshtastic_emoji=1, meshtastic_reaction_key="🔥")
    await relay.deliver(wire)
    assert relay.queue.call_args.kwargs["text"].endswith("waves hello")


async def test_conflicting_envelope_cannot_supply_authoritative_names(relay):
    wire = await content()
    wire["medre"]["envelope"]["source_transport_id"] = "forged"
    await relay.deliver(wire)
    assert relay.queue.call_args.kwargs["text"] == "Human: Visible radio text"


async def test_untrusted_detection_claim_stays_ordinary_text(relay, monkeypatch):
    detect = AsyncMock()
    monkeypatch.setattr("mmrelay.matrix_utils._handle_detection_sensor_packet", detect)
    wire = await content(portnum=10)
    await relay.deliver(deepcopy(wire), "@human:example.org")
    detect.assert_not_awaited()
    await relay.deliver(wire)
    detect.assert_awaited_once()


async def test_plugin_can_claim_canonical_peer_message_before_radio_send(
    relay, monkeypatch
):
    plugin = SimpleNamespace(
        handle_room_message=AsyncMock(return_value=True), plugin_name="owner"
    )
    monkeypatch.setattr("mmrelay.plugin_loader.load_plugins", lambda: [plugin])
    await relay.deliver(await content())
    plugin.handle_room_message.assert_awaited_once()
    assert plugin.handle_room_message.call_args.args[2] == "Visible radio text"
    relay.queue.assert_not_called()


@pytest.mark.parametrize(
    "scope", ["adapter", "channel", "missing_key", "relation_info"]
)
async def test_peer_reaction_cannot_bind_conflicting_or_out_of_scope_target(
    relay, scope
):
    wire = await content(
        message="Visible reaction", emoji=True, meshtastic_replyId=0, reaction_key="🔥"
    )
    relation = wire["medre"]["metadata"]["bridge_context"]["relations"][0]
    if scope == "adapter":
        relation["target_native_ref"]["adapter"] = "unrelated"
    elif scope == "channel":
        relation["target_native_ref"]["native_channel_id"] = "unrelated"
    elif scope == "missing_key":
        relation["key"] = None
    else:
        wire["medre"]["envelope"]["relation_info"] = "reply"
    await relay.deliver(wire)
    relay.queue.assert_called_once()
    assert relay.queue.call_args.args[0] == relay.interface.sendText
    assert relay.queue.call_args.kwargs["text"].endswith("Visible reaction")
    assert "reply_id" not in relay.queue.call_args.kwargs


@pytest.mark.parametrize(
    "mapping_room,expected", [(ROOM, True), ("!other:example.org", False)]
)
async def test_native_reaction_requires_mapping_in_actual_room_and_retains_zero(
    relay, monkeypatch, mapping_room, expected
):
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
        lambda _: ("0", mapping_room, "preview", "Local"),
    )
    await relay.deliver(
        {
            "m.relates_to": {
                "rel_type": "m.annotation",
                "event_id": "$target",
                "key": "❤️",
            }
        },
        sender="@human:example.org",
        reaction=True,
    )
    assert relay.queue.called is expected
    if expected:
        assert relay.queue.call_args.kwargs["reply_id"] == 0


async def test_self_echo_is_not_sent_to_radio(relay):
    await relay.deliver(await content(), sender="@relay:example.org")
    relay.queue.assert_not_called()


@pytest.mark.parametrize("target_namespace", ["radio", "matrix"])
async def test_peer_reaction_truncates_unicode_before_radio_queue(
    relay, monkeypatch, target_namespace
):
    """Both structured replies and plain radio sends respect the byte budget."""
    from mmrelay.matrix_utils import send_text_reply, truncate_message

    authored_body = "🔥" * 180
    wire = await content(
        message=authored_body, emoji=True, meshtastic_replyId=0, reaction_key="🔥"
    )
    if target_namespace == "matrix":
        wire["medre"]["metadata"]["bridge_context"]["relations"][0][
            "target_native_ref"
        ] = {
            "adapter": "mmrelay.matrix",
            "native_channel_id": ROOM,
            "native_message_id": "$original",
            "native_thread_id": None,
        }
        monkeypatch.setattr(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
            lambda event_id: (
                (0, ROOM, "stored preview", "Local")
                if event_id == "$original"
                else None
            ),
        )
    await relay.deliver(wire)
    relay.queue.assert_called_once()
    queued = relay.queue.call_args
    assert queued.kwargs["text"] == truncate_message(authored_body)
    from mmrelay.matrix_utils import DEFAULT_MESSAGE_TRUNCATE_BYTES

    assert len(queued.kwargs["text"].encode("utf-8")) <= DEFAULT_MESSAGE_TRUNCATE_BYTES
    if target_namespace == "matrix":
        assert queued.args[0] is send_text_reply
        assert queued.kwargs["reply_id"] == 0
    else:
        assert queued.args[0] is relay.interface.sendText


@pytest.mark.parametrize("codec_fails", [False, True])
async def test_thread_without_reply_marker_preserves_authored_quote(
    relay, monkeypatch, codec_fails
):
    if codec_fails:
        monkeypatch.setattr(
            "mmrelay.matrix.events.canonicalize_matrix_event",
            MagicMock(side_effect=ValueError("codec failure")),
        )
    await relay.deliver(
        {
            "msgtype": "m.text",
            "body": "> authored quote",
            "m.relates_to": {"rel_type": "m.thread", "event_id": "$root"},
        }
    )
    relay.queue.assert_called_once()
    assert relay.queue.call_args.kwargs["text"].endswith("> authored quote")
    assert "reply_id" not in relay.queue.call_args.kwargs


@pytest.mark.parametrize("codec_fails", [False, True])
@pytest.mark.parametrize("fallback", [False, True, None])
async def test_thread_explicit_reply_retains_target_but_fallback_does_not(
    relay, monkeypatch, fallback, codec_fails
):
    handler = AsyncMock(return_value=True)
    monkeypatch.setattr("mmrelay.matrix_utils.handle_matrix_reply", handler)
    if codec_fails:
        monkeypatch.setattr(
            "mmrelay.matrix.events.canonicalize_matrix_event",
            MagicMock(side_effect=ValueError("codec failure")),
        )
    relation = {
        "rel_type": "m.thread",
        "event_id": "$root",
        "m.in_reply_to": {"event_id": "$explicit"},
    }
    if fallback is not None:
        relation["is_falling_back"] = fallback
    await relay.deliver(
        {
            "msgtype": "m.text",
            "body": "> <@bob:example.org> preview\n\n> authored quote\nanswer",
            "m.relates_to": relation,
        }
    )
    if fallback is True:
        handler.assert_not_awaited()
        relay.queue.assert_called_once()
    else:
        handler.assert_awaited_once()
        assert handler.call_args.args[2:4] == ("$explicit", "> authored quote\nanswer")
        relay.queue.assert_not_called()


@pytest.mark.parametrize("native_reply", [False, True])
async def test_peer_namespaced_reply_hint_uses_local_scope_without_overriding_native_reply(
    relay, monkeypatch, native_reply
):
    wire = await content(message="answer", meshtastic_replyId=0)
    wire["medre"]["metadata"]["bridge_context"]["relations"][0]["target_native_ref"] = {
        "adapter": "medre",
        "native_channel_id": ROOM,
        "native_message_id": "$hint",
        "native_thread_id": None,
    }
    if native_reply:
        wire["m.relates_to"] = {"m.in_reply_to": {"event_id": "$native"}}
    handler = AsyncMock(return_value=True)
    monkeypatch.setattr("mmrelay.matrix_utils.handle_matrix_reply", handler)
    await relay.deliver(wire)
    handler.assert_awaited_once()
    assert handler.call_args.args[2] == ("$native" if native_reply else "$hint")
    relay.queue.assert_not_called()


async def test_native_reaction_keeps_target_and_key_over_peer_hint(relay, monkeypatch):
    wire = await content(
        message="Peer presentation", emoji=True, meshtastic_replyId=0, reaction_key="🔥"
    )
    wire["m.relates_to"] = {
        "rel_type": "m.annotation",
        "event_id": "$native",
        "key": "🐈",
    }
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
        lambda event_id: (
            10 if event_id == "$native" else 20,
            ROOM,
            "preview",
            "Local",
        ),
    )
    await relay.deliver(wire, reaction=True)
    relay.queue.assert_called_once()
    assert relay.queue.call_args.kwargs["reply_id"] == 10
    assert "🐈" in relay.queue.call_args.kwargs["text"]
    assert "🔥" not in relay.queue.call_args.kwargs["text"]
