"""Installed mindroom-nio decrypted-event SDK contract guard.

Ports the no-homeserver decrypted-event contract from the MEDRE
windows-e2ee-contract recipe: the parse helpers MMRelay's message
dispatch depends on (``Event.parse_decrypted_event``,
``RedactionEvent.from_dict``) keep a stable shape, and the event classes
``mmrelay.matrix.events`` dispatches on exist in the installed SDK. The
real SDK runs in an isolated interpreter because the in-process test
suite mocks ``nio``.
"""

from __future__ import annotations

from tests._matrix_sdk_contract_helpers import run_sdk_script, skip_unless_e2ee_ready

# Kept module-level so tests/test_matrix_sdk_contract_enforcement.py can
# monkeypatch the runner per contract module.
_run_sdk_script = run_sdk_script


def _skip_unless_e2ee_ready() -> None:
    """Skip on capability: the e2e extra with an enabled crypto backend must be installed."""
    skip_unless_e2ee_ready(_run_sdk_script)


# Exercises the same SDK objects the production event dispatch consumes:
# decrypted events carry the inner event type in ``.source``, encrypted
# media parses into RoomEncrypted* subclasses, redactions preserve
# ``redacts``, and the dispatched classes exist on the installed SDK.
_DECRYPTED_EVENT_SCRIPT = """
import nio

encrypted_media = {
    "type": "m.room.message",
    "event_id": "$evt-1",
    "sender": "@alice:example.com",
    "origin_server_ts": 1_700_000_000_000,
    "room_id": "!room:example.com",
    "content": {
        "msgtype": "m.image",
        "body": "photo.jpg",
        "file": {
            "url": "mxc://example.com/media",
            "hashes": {"sha256": "abcd"},
            "iv": "ivbytes",
            "key": {"alg": "A256CTR", "k": "keybytes"},
        },
    },
}

event = nio.Event.parse_decrypted_event(encrypted_media)
assert isinstance(event, nio.RoomEncryptedImage)
assert event.source["type"] == "m.room.message"

redaction = {
    "type": "m.room.redaction",
    "event_id": "$re-1",
    "sender": "@alice:example.com",
    "origin_server_ts": 1_700_000_000_000,
    "room_id": "!room:example.com",
    "redacts": "$target",
    "content": {"reason": "cleanup"},
}
redaction_event = nio.RedactionEvent.from_dict(redaction)
assert redaction_event.source["type"] == "m.room.redaction"
assert redaction_event.source["redacts"] == "$target"

# The event classes mmrelay.matrix.events dispatches on exist and keep
# their SDK hierarchy: room messages subclass RoomMessage; reactions and
# redactions subclass Event.
for event_cls in (nio.RoomMessageText, nio.RoomMessageNotice, nio.RoomMessageEmote):
    assert issubclass(event_cls, nio.RoomMessage), event_cls
for event_cls in (nio.ReactionEvent, nio.RedactionEvent):
    assert issubclass(event_cls, nio.Event), event_cls
"""


def test_mindroom_nio_decrypted_event_contract() -> None:
    """Decrypted-event parsing keeps the shape MMRelay's dispatch consumes."""
    _skip_unless_e2ee_ready()

    result = _run_sdk_script(_DECRYPTED_EVENT_SCRIPT)

    assert result.returncode == 0, result.stderr or result.stdout
