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
    from mmrelay.matrix.peer_context import PeerClaimWarningLimiter

    # Rejections are rate-limited per process; isolate warning windows per test.
    monkeypatch.setattr(
        "mmrelay.matrix.events._peer_claim_warnings", PeerClaimWarningLimiter()
    )

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
    if not trusted:
        relay.queue.assert_not_called()
        return
    relay.queue.assert_called_once()
    text = relay.queue.call_args.kwargs["text"]
    assert text == "AL/Remo: Visible radio text"
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


async def test_peer_on_local_mesh_and_untrusted_claim_are_both_suppressed(relay):
    wire = await content(meshnet_name="Local")
    await relay.deliver(wire)
    relay.queue.assert_not_called()
    await relay.deliver(wire, "@human:example.org")
    relay.queue.assert_not_called()


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
    relay.queue.assert_not_called()


async def test_untrusted_detection_claim_is_dropped(relay, monkeypatch):
    detect = AsyncMock()
    monkeypatch.setattr("mmrelay.matrix_utils._handle_detection_sensor_packet", detect)
    wire = await content(portnum=10)
    await relay.deliver(deepcopy(wire), "@human:example.org")
    detect.assert_not_awaited()
    await relay.deliver(wire)
    detect.assert_awaited_once()


@pytest.mark.parametrize("sender", ["@human:example.org", "@peer:EXAMPLE.org"])
async def test_untrusted_envelope_is_dropped_before_plugins_or_radio(
    relay, monkeypatch, sender
):
    logger = MagicMock()
    plugin = SimpleNamespace(
        handle_room_message=AsyncMock(return_value=True), plugin_name="owner"
    )
    monkeypatch.setattr("mmrelay.matrix_utils.logger", logger)
    monkeypatch.setattr("mmrelay.plugin_loader.load_plugins", lambda: [plugin])
    await relay.deliver(await content(), sender)
    relay.queue.assert_not_called()
    plugin.handle_room_message.assert_not_awaited()
    logger.warning.assert_called_once()
    fmt, observed_sender, observed_room, observed_event_id = (
        logger.warning.call_args.args
    )
    assert "matrix.trusted_relays" in fmt
    assert observed_sender == sender
    assert observed_room == ROOM
    assert observed_event_id == "$incoming"


@pytest.mark.parametrize(
    "medre", [None, False, "invalid", {}, {"envelope": None}, {"envelope": {}}]
)
async def test_broken_medre_namespace_is_not_plain_radio_text(relay, medre):
    await relay.deliver({"body": "User text", "medre": medre}, "@human:example.org")
    relay.queue.assert_not_called()


async def test_plain_matrix_user_message_remains_relayable(relay):
    await relay.deliver({"body": "Ordinary user message"}, "@human:example.org")
    relay.queue.assert_called_once()
    assert relay.queue.call_args.kwargs["text"] == "Human: Ordinary user message"


async def test_legacy_flat_metadata_without_medre_has_no_authority(relay):
    await relay.deliver(
        {
            "body": "Visible human text",
            "meshtastic_text": "HIDDEN",
            "meshtastic_longname": "Forged Name",
            "meshtastic_meshnet": "Local",
        },
        "@human:example.org",
    )
    relay.queue.assert_called_once()
    assert relay.queue.call_args.kwargs["text"] == "Human: Visible human text"


async def test_allowlisted_sender_without_medre_namespace_remains_ordinary(relay):
    await relay.deliver({"body": "Human text"}, PEER)
    relay.queue.assert_called_once()
    assert relay.queue.call_args.kwargs["text"] == "Human: Human text"


@pytest.mark.parametrize(
    "fault",
    ["source_transport_id", "schema_version", "missing_context", "missing_envelope"],
)
async def test_trusted_peer_broken_claim_is_dropped_not_downgraded(
    relay, monkeypatch, fault
):
    logger = MagicMock()
    monkeypatch.setattr("mmrelay.matrix_utils.logger", logger)
    wire = await content()
    if fault == "missing_context":
        wire["medre"]["metadata"].pop("bridge_context")
    elif fault == "missing_envelope":
        wire["medre"].pop("envelope")
    elif fault == "schema_version":
        wire["medre"]["envelope"]["schema_version"] = True
    else:
        wire["medre"]["envelope"]["source_transport_id"] = "@forged:example.org"
    await relay.deliver(wire)
    relay.queue.assert_not_called()
    logger.warning.assert_called_once()
    assert "invalid/incoherent" in logger.warning.call_args.args[0]


async def test_trusted_claim_validator_exception_drops_without_crashing(
    relay, monkeypatch
):
    validator = MagicMock(side_effect=ValueError("untrusted wire details"))
    monkeypatch.setattr("mmrelay.matrix_utils.trusted_peer_metadata", validator)
    logger = MagicMock()
    monkeypatch.setattr("mmrelay.matrix_utils.logger", logger)
    await relay.deliver(await content())
    relay.queue.assert_not_called()
    validator.assert_called_once()
    logger.warning.assert_called_once()
    assert "untrusted wire details" not in str(logger.mock_calls)


async def test_medre_claim_in_plugin_only_room_is_not_dispatched(relay, monkeypatch):
    plugin = SimpleNamespace(
        handles_unmapped_rooms=True,
        get_unmapped_room_ids=lambda: [ROOM],
        handle_room_message=AsyncMock(return_value=True),
        plugin_name="owner",
    )
    monkeypatch.setattr("mmrelay.plugin_loader.load_plugins", lambda: [plugin])
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_rooms", [])
    await relay.deliver(await content())
    relay.queue.assert_not_called()
    plugin.handle_room_message.assert_not_awaited()


async def test_ordinary_message_in_plugin_only_room_still_dispatches(
    relay, monkeypatch
):
    plugin = SimpleNamespace(
        handles_unmapped_rooms=True,
        get_unmapped_room_ids=lambda: [ROOM],
        handle_room_message=AsyncMock(return_value=True),
        plugin_name="owner",
    )
    monkeypatch.setattr("mmrelay.plugin_loader.load_plugins", lambda: [plugin])
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_rooms", [])
    await relay.deliver({"body": "admin command"}, "@human:example.org")
    relay.queue.assert_not_called()
    plugin.handle_room_message.assert_awaited_once()


async def test_untrusted_claim_cannot_suppress_its_security_warning(relay, monkeypatch):
    from mmrelay.constants.formats import MATRIX_SUPPRESS_KEY

    logger = MagicMock()
    monkeypatch.setattr("mmrelay.matrix_utils.logger", logger)
    await relay.deliver(
        {"body": "claim", "medre": None, MATRIX_SUPPRESS_KEY: True},
        "@human:example.org",
    )
    relay.queue.assert_not_called()
    logger.warning.assert_called_once()
    assert "matrix.trusted_relays" in logger.warning.call_args.args[0]


async def test_untrusted_malformed_claim_drops_before_codec_failure(relay, monkeypatch):
    codec = MagicMock(side_effect=ValueError("malformed envelope"))
    monkeypatch.setattr("mmrelay.matrix_utils.canonicalize_matrix_event", codec)
    await relay.deliver({"body": "authored", "medre": "invalid"}, "@human:example.org")
    codec.assert_not_called()
    relay.queue.assert_not_called()


async def test_trusted_claim_codec_error_does_not_forward_plain_text(
    relay, monkeypatch
):
    codec = MagicMock(side_effect=ValueError("malformed envelope"))
    monkeypatch.setattr("mmrelay.matrix_utils.canonicalize_matrix_event", codec)
    await relay.deliver(await content(), PEER)
    codec.assert_called_once()
    relay.queue.assert_not_called()


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


@pytest.mark.parametrize("scope", ["adapter", "channel", "relation_info"])
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
    else:
        wire["medre"]["envelope"]["relation_info"] = "reply"
    await relay.deliver(wire)
    if scope == "relation_info":
        relay.queue.assert_not_called()
        return
    relay.queue.assert_called_once()
    assert relay.queue.call_args.args[0] == relay.interface.sendText
    assert relay.queue.call_args.kwargs["text"].endswith("Visible reaction")
    assert "reply_id" not in relay.queue.call_args.kwargs


@pytest.mark.parametrize("missing", ["key", "target", "relation"])
async def test_trusted_reaction_with_missing_metadata_is_dropped(
    relay, monkeypatch, missing
):
    logger = MagicMock()
    monkeypatch.setattr("mmrelay.matrix_utils.logger", logger)
    wire = await content(emoji=True, meshtastic_replyId=0, reaction_key="🔥")
    context = wire["medre"]["metadata"]["bridge_context"]
    if missing == "relation":
        context["relations"] = []
        wire["medre"]["envelope"]["relation_info"] = ""
    else:
        field = "target_native_ref" if missing == "target" else "key"
        context["relations"][0][field] = None

    await relay.deliver(wire)

    relay.queue.assert_not_called()
    logger.warning.assert_called_once()
    assert "invalid/incoherent" in logger.warning.call_args.args[0]


async def test_disabled_peer_reaction_does_not_relay_out_of_scope_fallback(
    relay, monkeypatch
):
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_interaction_settings",
        lambda _: {"replies": True, "reactions": False},
    )
    wire = await content(emoji=True, meshtastic_replyId=0, reaction_key="🔥")
    wire["medre"]["metadata"]["bridge_context"]["relations"][0]["target_native_ref"][
        "native_channel_id"
    ] = "other-channel"

    await relay.deliver(wire)

    relay.queue.assert_not_called()


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
            "mmrelay.matrix_utils.canonicalize_matrix_event",
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
            "mmrelay.matrix_utils.canonicalize_matrix_event",
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
