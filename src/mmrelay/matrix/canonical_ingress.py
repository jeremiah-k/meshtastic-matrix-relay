"""MEDRE 0.1 Matrix ingress normalization for the MMRelay 1.5 boundary.

MMRelay owns the nio session, trust policy, plugin callbacks and Meshtastic
queue. MEDRE's Matrix codec owns only the canonical event and Matrix-native
metadata schema. Neither a second Matrix connection nor MEDRE's routing
pipeline is started here.
"""

from __future__ import annotations

from typing import Any

from medre.adapters.matrix.codec import MatrixCodec
from medre.config.adapters.matrix import MatrixConfig
from medre.core.events.canonical import CanonicalEvent

# MatrixCodec only uses the adapter ID and does not validate/read its MatrixConfig.
# This intentionally does not use or duplicate MMRelay's login credentials.
_CODEC = MatrixCodec(
    "mmrelay.matrix",
    MatrixConfig(adapter_id="mmrelay.matrix", homeserver="", user_id=""),
)


def canonicalize_matrix_event(
    room: Any, event: Any, *, reaction: bool = False
) -> CanonicalEvent:
    """Project a nio room event to MEDRE's v1 canonical Matrix event shape.

    Explicitly supply verified native facts and no invented crypto provenance.
    A room being encrypted does not prove this particular event was encrypted;
    the codec records that distinction and strips raw crypto material. The
    original nio event stays untouched for plugin and transport operations.
    """
    source = getattr(event, "source", None)
    if not isinstance(source, dict):
        raise TypeError("Matrix ingress requires a nio source mapping")
    content = source.get("content")
    if not isinstance(content, dict):
        raise TypeError("Matrix ingress requires a content mapping")

    def text_field(name: str) -> str | None:
        value = getattr(event, name, None)
        return value if isinstance(value, str) else None

    timestamp = getattr(event, "server_timestamp", None)
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float, str)):
        timestamp = source.get("origin_server_ts")
    room_encrypted = getattr(room, "encrypted", None)
    room_encrypted = room_encrypted if isinstance(room_encrypted, bool) else None
    decrypted = getattr(event, "decrypted", False) is True
    verified = getattr(event, "verified", None) if decrypted else None
    verified = verified if isinstance(verified, bool) else None
    return _CODEC.decode(
        {
            "source": source,
            "room_id": room.room_id,
            "event_type": "m.reaction" if reaction else source.get("type"),
            "event_id": text_field("event_id") or source.get("event_id"),
            "sender": text_field("sender") or source.get("sender"),
            "sender_display_name": text_field("sender_display_name"),
            "body": text_field("body") or content.get("body", ""),
            "msgtype": text_field("msgtype") or content.get("msgtype"),
            "server_timestamp": timestamp,
            "room_encrypted": room_encrypted,
            "event_encrypted": decrypted,
            "decrypted": decrypted,
            "verified": verified,
        },
        room_id=room.room_id,
    )
