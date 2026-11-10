"""MMRelay 1.5 canonical Matrix ingress contract against the pinned MEDRE codec."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from medre.adapters.matrix.event_shape import (
    MATRIX_NATIVE_SCHEMA_VERSION,
    matrix_namespace,
)
from medre.core.events.kinds import EventKind

from mmrelay.matrix.canonical_ingress import canonicalize_matrix_event


def _message(
    content: dict[str, object], *, source_type: str = "m.room.message"
) -> tuple[SimpleNamespace, SimpleNamespace]:
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


@pytest.mark.parametrize("verified", [True, False, None])
def test_decrypted_event_records_only_supported_crypto_facts(verified) -> None:
    """Do not turn encrypted-room state into proof of decryption or verification."""
    room, event = _message({"msgtype": "m.text", "body": "decrypted text"})
    room.encrypted = True
    event.decrypted = True
    event.verified = verified
    canonical = canonicalize_matrix_event(room, event)
    native = matrix_namespace(canonical.metadata.native.data)
    expected = {
        "event_encrypted": True,
        "decrypted": True,
        "room_encrypted": True,
    }
    if verified is not None:
        expected["verified"] = verified
    assert native["encryption"] == expected
