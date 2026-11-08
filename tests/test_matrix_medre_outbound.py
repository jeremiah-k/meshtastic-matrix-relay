"""MMRelay 1.5 radio-to-Matrix sends use MEDRE's closed native rendering contract."""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from medre.adapters.matrix.codec import MatrixCodec
from medre.adapters.matrix.outbound import MatrixOutboundOperation
from medre.config.adapters.matrix import MatrixConfig

from mmrelay.matrix.canonical_outbound import render_matrix_relay_content
from mmrelay.matrix_utils import matrix_relay

ROOM = "!mesh:example.org"
BOT = "@relay:example.org"


@pytest.fixture
def real_markdown():
    """Restore the real markdown module that tests/mocks.py replaces with a MagicMock.

    The global test mocks stub ``markdown`` in ``sys.modules`` before any
    application import, so the presentation path exercised by this contract
    test would otherwise sanitize a MagicMock instead of real rendered HTML.
    """
    mocked = sys.modules.get("markdown")
    sys.modules.pop("markdown", None)
    try:
        yield importlib.import_module("markdown")
    finally:
        if mocked is not None:
            sys.modules["markdown"] = mocked
        else:
            sys.modules.pop("markdown", None)


async def render(**kwargs):
    arguments = {
        "room_id": ROOM,
        "message": "Radio text 🦉",
        "longname": "Radio Alice",
        "shortname": "RA",
        "meshnet_name": "LocalMesh",
        "portnum": 1,
        "meshtastic_id": 123,
        "meshtastic_replyId": 4,
        "meshtastic_text": "original radio text",
        "bot_user_id": BOT,
    }
    arguments.update(kwargs)
    return await render_matrix_relay_content(**arguments)


@pytest.mark.asyncio
async def test_outbound_medre_envelope_and_legacy_fields_are_consistent():
    content = await render()
    envelope = content["medre"]["envelope"]
    assert envelope["schema_version"] == 1
    assert envelope["event_kind"] == "message.created"
    assert envelope["source_adapter"] == "mmrelay.meshtastic"
    assert envelope["source_channel"] == "LocalMesh"
    assert envelope["canonical_event_id"]
    assert content["msgtype"] == "m.text"
    assert content["body"] == "Radio text 🦉"
    assert content["meshtastic_id"] == 123
    assert content["meshtastic_replyId"] == 4
    assert content["meshtastic_text"] == "original radio text"
    assert content["meshtastic_portnum"] == 1
    assert content["meshtastic_longname"] == "Radio Alice"
    assert "_matrix_operation" not in content
    assert "m.relates_to" not in content


@pytest.mark.asyncio
async def test_outbound_matrix_codec_roundtrip_preserves_authored_body_and_native_reply(
    real_markdown,
):
    content = await render(
        message="Answer <b>& 🦉",
        reply_to_event_id="$original",
        reply_preview=("Old <script>\n\nsecond line", "RemoteMesh"),
    )
    assert content["m.relates_to"] == {"m.in_reply_to": {"event_id": "$original"}}
    assert "<script>" not in content["formatted_body"]
    assert "&lt;script&gt;" in content["formatted_body"]
    assert content["body"].startswith("> <@relay:example.org> [RemoteMesh]: Old")
    assert "\n> \n> second line\n\nAnswer <b>& 🦉" in content["body"]
    codec = MatrixCodec(
        "matrix-room",
        MatrixConfig(adapter_id="matrix-room", homeserver="", user_id=""),
    )
    decoded = codec.decode(
        {
            "room_id": ROOM,
            "event_id": "$sent:example.org",
            "sender": BOT,
            "source": {
                "type": "m.room.message",
                "event_id": "$sent:example.org",
                "sender": BOT,
                "content": content,
            },
        },
        room_id=ROOM,
    )
    assert decoded.payload["body"] == "Answer <b>& 🦉"
    assert decoded.relations[0].relation_type == "reply"
    assert decoded.relations[0].target_native_ref.native_message_id == "$original"
    assert decoded.relations[0].target_native_ref.native_channel_id == ROOM


@pytest.mark.asyncio
async def test_outbound_emote_emoji_and_zero_packet_ids_are_not_lost():
    content = await render(
        message="waves",
        emote=True,
        emoji=True,
        meshtastic_id=0,
        meshtastic_replyId=0,
        meshtastic_text="",
        reaction_key="👍",
    )
    assert content["msgtype"] == "m.emote"
    assert content["body"] == "waves"
    assert content["meshtastic_emoji"] == 1
    assert content["meshtastic_id"] == 0
    assert content["meshtastic_replyId"] == 0
    assert content["meshtastic_text"] == ""
    assert content["meshtastic_reaction_key"] == "👍"
    assert content["medre"]["envelope"]["event_kind"] == "message.reacted"
    assert content["medre"]["envelope"]["source_adapter"] == "mmrelay.meshtastic"


@pytest.mark.asyncio
async def test_outbound_reply_without_mapping_keeps_native_relation_without_fabricating_quote():
    content = await render(reply_to_event_id="$unmapped")
    assert content["m.relates_to"] == {"m.in_reply_to": {"event_id": "$unmapped"}}
    assert content["body"] == "Radio text 🦉"
    assert "<mx-reply>" not in content["formatted_body"]


@pytest.mark.asyncio
async def test_outbound_reply_quote_skips_invalid_bot_identity_but_keeps_relation():
    content = await render(
        bot_user_id=None,
        reply_to_event_id="$original",
        reply_preview=("old text", "LocalMesh"),
    )
    assert content["body"] == "Radio text 🦉"
    assert content["m.relates_to"]["m.in_reply_to"]["event_id"] == "$original"


@pytest.mark.asyncio
async def test_outbound_renderer_refuses_wrong_operation_and_body_mutation():
    fake_result = SimpleNamespace(
        payload=MatrixOutboundOperation.redact("$target").to_payload()
    )
    with patch(
        "mmrelay.matrix.canonical_outbound._RENDERER.render",
        new_callable=AsyncMock,
        return_value=fake_result,
    ):
        with pytest.raises(ValueError, match="non-message Matrix operation"):
            await render()

    wrong_body = MatrixOutboundOperation.send_event(
        "m.room.message", {"msgtype": "m.text", "body": "mutated"}
    )
    with patch(
        "mmrelay.matrix.canonical_outbound._RENDERER.render",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(payload=wrong_body.to_payload()),
    ):
        with pytest.raises(ValueError, match="changed the authored message body"):
            await render()


@pytest.mark.asyncio
async def test_outbound_renderer_refuses_wrong_event_type_and_provenance():
    annotation = MatrixOutboundOperation.send_event(
        "m.reaction", {"m.relates_to": {"rel_type": "m.annotation", "key": "🔒"}}
    )
    with patch(
        "mmrelay.matrix.canonical_outbound._RENDERER.render",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(payload=annotation.to_payload()),
    ):
        with pytest.raises(ValueError, match="non-message Matrix operation"):
            await render()

    wrong_origin = MatrixOutboundOperation.send_event(
        "m.room.message",
        {
            "msgtype": "m.text",
            "body": "Radio text 🦉",
            "medre": {
                "envelope": {
                    "schema_version": 1,
                    "canonical_event_id": "$unrelated",
                    "source_adapter": "mmrelay.meshtastic",
                }
            },
        },
    )
    with patch(
        "mmrelay.matrix.canonical_outbound._RENDERER.render",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(payload=wrong_origin.to_payload()),
    ):
        with pytest.raises(ValueError, match="lost canonical v1 provenance"):
            await render()


@pytest.mark.asyncio
async def test_outbound_renderer_refuses_missing_native_reply_relation():
    missing_relation = MatrixOutboundOperation.send_event(
        "m.room.message", {"msgtype": "m.text", "body": "Radio text 🦉"}
    )
    with patch(
        "mmrelay.matrix.canonical_outbound._RENDERER.render",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(payload=missing_relation.to_payload()),
    ):
        with pytest.raises(ValueError, match="lost the native reply relation"):
            await render(reply_to_event_id="$target")


@pytest.mark.asyncio
async def test_matrix_relay_uses_canonical_renderer_without_second_matrix_client():
    client = MagicMock()
    client.rooms = {ROOM: SimpleNamespace(encrypted=False)}
    client.user_id = BOT
    client.room_send = AsyncMock(return_value=SimpleNamespace(event_id="$sent"))
    with (
        patch(
            "mmrelay.matrix_utils.connect_matrix",
            new_callable=AsyncMock,
            return_value=client,
        ),
        patch(
            "mmrelay.matrix_utils.config", {"meshtastic": {"meshnet_name": "LocalMesh"}}
        ),
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"replies": True},
        ),
        patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False),
    ):
        await matrix_relay(ROOM, "hello", "Alice", "A", "LocalMesh", 1, 19)
    client.room_send.assert_awaited_once()
    sent = client.room_send.call_args.kwargs
    assert sent["content"]["medre"]["envelope"]["event_kind"] == "message.created"
    assert sent["content"]["meshtastic_id"] == 19
    assert sent["tx_id"].startswith("mmrelay-")


@pytest.mark.asyncio
async def test_matrix_relay_never_sends_when_renderer_fails():
    client = MagicMock()
    client.rooms = {ROOM: SimpleNamespace(encrypted=False)}
    client.room_send = AsyncMock()
    with (
        patch(
            "mmrelay.matrix_utils.connect_matrix",
            new_callable=AsyncMock,
            return_value=client,
        ),
        patch(
            "mmrelay.matrix_utils.config", {"meshtastic": {"meshnet_name": "LocalMesh"}}
        ),
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"replies": False},
        ),
        patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False),
        patch(
            "mmrelay.matrix.relay.render_matrix_relay_content",
            new_callable=AsyncMock,
            side_effect=ValueError("bad operation"),
        ),
    ):
        await matrix_relay(ROOM, "hello", "Alice", "A", "LocalMesh", 1)
    client.room_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_outbound_reaction_requires_structured_symbol_and_codec_recovers_it():
    with pytest.raises(ValueError, match="explicit reaction_key"):
        await render(emote=True, emoji=True, meshtastic_replyId=4)

    content = await render(
        message='\n [Alice/LocalMesh]: reacted 🎉 to "hello"',
        emote=True,
        emoji=True,
        reaction_key="🎉",
        meshtastic_replyId=4,
    )
    assert "m.relates_to" not in content
    codec = MatrixCodec(
        "matrix-room",
        MatrixConfig(adapter_id="matrix-room", homeserver="", user_id=""),
    )
    decoded = codec.decode(
        {
            "room_id": ROOM,
            "event_id": "$reaction",
            "sender": BOT,
            "source": {
                "type": "m.room.message",
                "event_id": "$reaction",
                "sender": BOT,
                "content": content,
            },
        },
        room_id=ROOM,
    )
    assert decoded.event_kind == "message.reacted"
    assert decoded.relations[0].relation_type == "reaction"
    assert decoded.relations[0].key == "🎉"
