"""MMRelay 1.5 canonical Matrix ingress contract against the pinned MEDRE codec."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from medre.adapters.matrix.event_shape import (
    MATRIX_NATIVE_SCHEMA_VERSION,
    matrix_namespace,
)
from medre.core.events.kinds import EventKind

from mmrelay.matrix.canonical_ingress import canonicalize_matrix_event


def _message(content: dict[str, object], *, source_type: str = "m.room.message"):
    source = {
        "type": source_type,
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


def test_matrix_text_uses_frozen_canonical_native_projection() -> None:
    room, event = _message({"msgtype": "m.text", "body": "hello"})
    result = canonicalize_matrix_event(room, event)
    native = matrix_namespace(result.metadata.native.data)
    assert result.schema_version == 1
    assert result.event_kind == EventKind.MESSAGE_CREATED
    assert result.payload["body"] == "hello"
    assert result.source_channel_id == room.room_id
    assert result.source_native_ref.native_message_id == event.event_id
    assert native["schema_version"] == MATRIX_NATIVE_SCHEMA_VERSION == 1
    assert native["room_id"] == room.room_id
    assert native["sender"] == event.sender
    assert native["encryption"] == {
        "event_encrypted": False,
        "decrypted": False,
        "room_encrypted": False,
    }
    assert event.source["content"]["body"] == "hello"


def test_matrix_reply_strips_wire_fallback_but_keeps_native_target() -> None:
    room, event = _message(
        {
            "msgtype": "m.text",
            "body": "> <@bob:example.org> quoted\n\nactual reply",
            "m.relates_to": {"m.in_reply_to": {"event_id": "$original"}},
        }
    )
    canonical = canonicalize_matrix_event(room, event)
    assert canonical.payload["body"] == "actual reply"
    assert canonical.relations[0].relation_type == "reply"
    assert canonical.relations[0].target_native_ref.native_message_id == "$original"
    assert event.body.startswith(">")  # Do not mutate nio/plugin input.


def test_matrix_thread_fallback_is_not_an_explicit_reply() -> None:
    room, event = _message(
        {
            "msgtype": "m.text",
            "body": "> <@bob:example.org> quote\n\nthread message",
            "m.relates_to": {
                "rel_type": "m.thread",
                "event_id": "$root",
                "is_falling_back": True,
                "m.in_reply_to": {"event_id": "$latest"},
            },
        }
    )
    canonical = canonicalize_matrix_event(room, event)
    assert [relation.relation_type for relation in canonical.relations] == ["thread"]
    assert canonical.relations[0].target_native_ref.native_message_id == "$root"


def test_matrix_reaction_uses_native_annotation_shape() -> None:
    room, event = _message(
        {
            "m.relates_to": {
                "rel_type": "m.annotation",
                "event_id": "$original",
                "key": "👍",
            }
        },
        source_type="m.reaction",
    )
    canonical = canonicalize_matrix_event(room, event, reaction=True)
    assert canonical.event_kind == EventKind.MESSAGE_REACTED
    assert canonical.relations[0].relation_type == "reaction"
    assert canonical.relations[0].target_native_ref.native_message_id == "$original"
    assert canonical.relations[0].key == "👍"


def test_encrypted_room_does_not_fabricate_event_decryption_or_leak_keys() -> None:
    room, event = _message(
        {
            "msgtype": "m.image",
            "body": "image.jpg",
            "file": {
                "url": "mxc://example.org/pic",
                "key": {"k": "SECRET"},
                "iv": "IV",
            },
            "info": {"size": 123, "mimetype": "image/jpeg"},
        }
    )
    room.encrypted = True
    canonical = canonicalize_matrix_event(room, event)
    native = matrix_namespace(canonical.metadata.native.data)
    assert native["encryption"] == {
        "event_encrypted": False,
        "decrypted": False,
        "room_encrypted": True,
    }
    assert native["media"]["encrypted"] is True
    assert native["media"]["mxc_uri"] == "mxc://example.org/pic"
    assert "SECRET" not in str(canonical.metadata.native.data)
    assert "IV" not in str(canonical.metadata.native.data)


def test_release_150_pins_both_source_packages_to_immutable_commits() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())[
        "project"
    ]
    assert project["version"] == "1.5.0"
    assert project["requires-python"] == ">=3.12"
    pins = {
        dep.partition(" @ ")[0]: dep for dep in project["dependencies"] if " @ " in dep
    }
    assert re.fullmatch(
        r"medre @ git\+https://github\.com/jeremiah-k/medre\.git@[0-9a-f]{40}",
        pins["medre"],
    )
    assert pins["medre-adapter-matrix"].endswith(
        "@241e9590035bb18750527c3ea1f02d26f28acfb5"
    )
    assert "medre-adapter-meshtastic" not in pins


@pytest.mark.asyncio
@pytest.mark.parametrize("replies_enabled", [False, True])
@pytest.mark.parametrize("mesh_text_override", [None, "> radio quote\n\nradio answer"])
@pytest.mark.parametrize("codec_fails", [False, True])
async def test_relay_preserves_canonical_quotes_and_legacy_codec_failure_behavior(
    replies_enabled, mesh_text_override, codec_fails
):
    from mmrelay.matrix_utils import on_room_message

    authored = "> authored quote\n\nactual answer"
    room, event = _message(
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
