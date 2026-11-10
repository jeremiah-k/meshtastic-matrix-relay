"""Canonical Matrix reply relay and legacy fallback behavior for shared rooms."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.matrix.canonical_ingress import canonicalize_matrix_event


@contextlib.contextmanager
def _patched_relay_facade(reactions_enabled):
    """Yield ``(room, enqueue)`` with the relay facade patched for one event."""
    room = SimpleNamespace(room_id="!room:example.org", encrypted=False)
    room_config = {"id": room.room_id, "meshtastic_channel": 0}
    config = {
        "meshtastic": {
            "meshnet_name": "LocalMesh",
            "prefix_enabled": False,
            "broadcast_enabled": True,
        },
        "matrix_rooms": [room_config],
    }
    queue = MagicMock()
    queue.get_queue_size.return_value = 0
    with (
        patch("mmrelay.matrix_utils.config", config),
        patch("mmrelay.matrix_utils.matrix_rooms", [room_config]),
        patch("mmrelay.matrix_utils.bot_start_time", 0),
        patch("mmrelay.matrix_utils.bot_user_id", "@relay:example.org"),
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"replies": False, "reactions": reactions_enabled},
        ),
        patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False),
        patch(
            "mmrelay.matrix_utils.get_user_display_name",
            new_callable=AsyncMock,
            return_value="Alice",
        ),
        patch("mmrelay.matrix_utils.get_meshtastic_prefix", return_value="Local[M]: "),
        patch("mmrelay.matrix_utils.truncate_message", side_effect=lambda s: s),
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            new_callable=AsyncMock,
            return_value=(MagicMock(), 0),
        ),
        patch("mmrelay.matrix_utils.get_message_queue", return_value=queue),
        patch("mmrelay.matrix_utils.queue_message", return_value=True) as enqueue,
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
    ):
        yield room, enqueue


def _reply_event(content: dict[str, object]) -> tuple[SimpleNamespace, SimpleNamespace]:
    source = {
        "type": "m.room.message",
        "event_id": "$message:example.org",
        "sender": "@sender:example.org",
        "origin_server_ts": 1_700_000_000_123,
        "content": content,
    }
    event = SimpleNamespace(
        source=source,
        sender="@sender:example.org",
        event_id="$message:example.org",
        server_timestamp=1_700_000_000_123,
        body=content.get("body", ""),
    )
    room = SimpleNamespace(room_id="!room:example.org", encrypted=False)
    return room, event


@pytest.mark.asyncio
@pytest.mark.parametrize("replies_enabled", [False, True])
@pytest.mark.parametrize("mesh_text_override", [None, "> radio quote\n\nradio answer"])
@pytest.mark.parametrize("codec_fails", [False, True])
async def test_relay_preserves_canonical_quotes_and_legacy_codec_failure_behavior(
    replies_enabled, mesh_text_override, codec_fails
):
    from mmrelay.matrix_utils import on_room_message

    authored = "> authored quote\n\nactual answer"
    room, event = _reply_event(
        {
            "msgtype": "m.text",
            "body": "> <@bob:example.org> fallback\n\n" + authored,
            "m.relates_to": {"m.in_reply_to": {"event_id": "$original"}},
            **(
                {"meshtastic_text": mesh_text_override}
                if mesh_text_override is not None
                else {}
            ),
        }
    )
    room_config = {"id": room.room_id, "meshtastic_channel": 0}
    config = {
        "meshtastic": {
            "meshnet_name": "LocalMesh",
            "prefix_enabled": False,
            "broadcast_enabled": True,
        },
        "matrix_rooms": [room_config],
    }
    queue = MagicMock()
    queue.get_queue_size.return_value = 1
    with (
        patch("mmrelay.matrix_utils.config", config),
        patch("mmrelay.matrix_utils.matrix_rooms", [room_config]),
        patch("mmrelay.matrix_utils.bot_start_time", 0),
        patch("mmrelay.matrix_utils.bot_user_id", "@relay:example.org"),
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"replies": replies_enabled, "reactions": False},
        ),
        patch("mmrelay.matrix_utils.message_storage_enabled", return_value=True),
        patch("mmrelay.matrix_utils._get_msgs_to_keep_config", return_value=20),
        patch(
            "mmrelay.matrix_utils.get_message_map_by_matrix_event_id",
            return_value=(42, room.room_id, "old text", "LocalMesh"),
        ),
        patch(
            "mmrelay.matrix_utils.get_user_display_name",
            new_callable=AsyncMock,
            return_value="Alice",
        ),
        patch(
            "mmrelay.matrix_utils._get_meshtastic_interface_and_channel",
            new_callable=AsyncMock,
            return_value=(MagicMock(), 0),
        ),
        patch("mmrelay.matrix_utils.get_message_queue", return_value=queue),
        patch("mmrelay.matrix_utils.queue_message", return_value=True) as enqueue,
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch(
            "mmrelay.matrix.events.canonicalize_matrix_event",
            wraps=canonicalize_matrix_event,
            side_effect=RuntimeError("codec failure") if codec_fails else None,
        ),
    ):
        await on_room_message(room, event)

    enqueue.assert_called_once()
    expected = mesh_text_override if mesh_text_override is not None else authored
    mapping_text = expected
    if codec_fails:
        mapping_text = (
            "radio answer" if mesh_text_override is not None else "actual answer"
        )
        expected = (
            mapping_text
            if replies_enabled
            else mesh_text_override or event.source["content"]["body"]
        )
    assert enqueue.call_args.kwargs["text"] == expected
    assert enqueue.call_args.kwargs["mapping_info"]["text"] == mapping_text
    if replies_enabled:
        assert enqueue.call_args.kwargs["reply_id"] == 42


@pytest.mark.asyncio
async def test_codec_failure_preserves_legacy_reply_target_and_authoritative_mode():
    """A codec exception must not lose the Matrix target or claim canonical text."""
    from mmrelay.matrix_utils import on_room_message

    room, event = _reply_event(
        {
            "msgtype": "m.text",
            "body": "> <@bob:example.org> quote\n\nactual answer",
            "m.relates_to": {"m.in_reply_to": {"event_id": "$original"}},
        }
    )
    room_config = {"id": room.room_id, "meshtastic_channel": 0}
    config = {
        "meshtastic": {"meshnet_name": "LocalMesh", "prefix_enabled": False},
        "matrix_rooms": [room_config],
    }
    with (
        patch("mmrelay.matrix_utils.config", config),
        patch("mmrelay.matrix_utils.matrix_rooms", [room_config]),
        patch("mmrelay.matrix_utils.bot_start_time", 0),
        patch("mmrelay.matrix_utils.bot_user_id", "@relay:example.org"),
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"replies": True, "reactions": False},
        ),
        patch("mmrelay.matrix_utils.message_storage_enabled", return_value=True),
        patch("mmrelay.matrix_utils._get_msgs_to_keep_config", return_value=20),
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch(
            "mmrelay.matrix.events.canonicalize_matrix_event",
            side_effect=RuntimeError("codec failure"),
        ),
        patch(
            "mmrelay.matrix_utils.handle_matrix_reply",
            new_callable=AsyncMock,
            return_value=True,
        ) as reply,
    ):
        await on_room_message(room, event)
    reply.assert_awaited_once()
    args, kwargs = reply.call_args
    assert args[2] == "$original"
    assert kwargs["text_is_canonical"] is False
    assert args[3].endswith("actual answer")


ROOM = "!mesh:example.org"
BOT = "@relay:example.org"


@pytest.mark.asyncio
async def test_matrix_relay_uses_canonical_renderer_without_second_matrix_client():
    from mmrelay.matrix_utils import matrix_relay

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
        await matrix_relay(
            ROOM,
            "hello",
            "Alice",
            "A",
            "LocalMesh",
            1,
            19,
            source_node_id="!0000007b",
        )
    client.room_send.assert_awaited_once()
    sent = client.room_send.call_args.kwargs
    assert sent["content"]["medre"]["envelope"]["event_kind"] == "message.created"
    assert sent["content"]["medre"]["envelope"]["source_transport_id"] == "!0000007b"
    assert sent["content"]["meshtastic_id"] == 19
    assert sent["tx_id"].startswith("mmrelay-")


@pytest.mark.asyncio
async def test_matrix_relay_never_sends_when_renderer_fails():
    from mmrelay.matrix_utils import matrix_relay

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
@pytest.mark.parametrize("body", ["waves hello", ""])
@pytest.mark.parametrize("reactions_enabled", [False, True])
async def test_emote_with_packet_id_zero_and_no_target_preserves_authored_body(
    reactions_enabled, body
):
    """A bare packet ID zero cannot produce an empty relay message."""
    from nio import RoomMessageEmote

    from mmrelay.matrix_utils import on_room_message

    class BareEmote(RoomMessageEmote):
        def __init__(self):
            self.source = {
                "type": "m.room.message",
                "event_id": "$emote:example.org",
                "sender": "@alice:example.org",
                "origin_server_ts": 1_700_000_000_123,
                "content": {
                    "msgtype": "m.emote",
                    "body": body,
                    "meshtastic_replyId": 0,
                },
            }
            self.sender = "@alice:example.org"
            self.server_timestamp = 1_700_000_000_123
            self.event_id = "$emote:example.org"
            self.body = body

    with _patched_relay_facade(reactions_enabled) as (room, enqueue):
        await on_room_message(room, BareEmote())

    if body:
        enqueue.assert_called_once()
        assert enqueue.call_args.kwargs["text"] == f"Local[M]: {body}"
    else:
        enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_reaction_without_resolvable_target_is_never_relayed_as_empty():
    """A native reaction with no target is dropped, not queued as an empty body."""
    from nio import ReactionEvent

    from mmrelay.matrix_utils import on_room_message

    class UntargetedReaction(ReactionEvent):
        def __init__(self):
            self.source = {
                "type": "m.reaction",
                "event_id": "$reaction:example.org",
                "sender": "@alice:example.org",
                "origin_server_ts": 1_700_000_000_123,
                "content": {
                    "m.relates_to": {"rel_type": "m.annotation", "key": "\U0001f44d"}
                },
            }
            self.sender = "@alice:example.org"
            self.server_timestamp = 1_700_000_000_123

    with _patched_relay_facade(reactions_enabled=True) as (room, enqueue):
        await on_room_message(room, UntargetedReaction())

    enqueue.assert_not_called()
