"""Authorize and validate canonical peer claims before relay code consumes them."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import msgspec
from medre.core.events import CanonicalEvent, EventRelation, NativeRef
from medre.core.rendering.bridge_context import normalize_bridge_context

from mmrelay.constants.messages import (
    PORTNUM_DETECTION_SENSOR_APP,
    PORTNUM_TEXT_MESSAGE_APP,
)

_MXID = re.compile(r"@[^\s:\x00-\x1f\x7f*]+:[^\s/*\x00-\x1f\x7f]+\Z")
_PACKET_ID = re.compile(r"[0-9]{1,10}\Z")


@dataclass(frozen=True)
class PeerIdentity:
    """An explicitly configured relay identity; signing keys can extend this record."""

    mxid: str


def load_peer_relays(config: Mapping[str, Any]) -> dict[str, PeerIdentity]:
    """Validate exact MXIDs without case folding, wildcard expansion, or TOFU."""
    matrix = config.get("matrix")
    if matrix is None:
        matrix = {}
    if not isinstance(matrix, Mapping):
        raise ValueError("matrix must be a mapping")
    entries = matrix.get("trusted_relays", [])
    if not isinstance(entries, list) or any(
        not isinstance(item, str) or _MXID.fullmatch(item) is None for item in entries
    ):
        raise ValueError(
            "matrix.trusted_relays must be a list of exact Matrix user IDs"
        )
    return {mxid: PeerIdentity(mxid) for mxid in entries}


def radio_packet_id(value: object) -> int | None:
    """Accept only unsigned 32-bit packet IDs, retaining zero and rejecting bools."""
    if type(value) is int:
        return value if 0 <= value < 2**32 else None
    if isinstance(value, str) and _PACKET_ID.fullmatch(value):
        number = int(value)
        return number if number < 2**32 else None
    return None


@dataclass(frozen=True)
class PeerMetadata:
    """Validated claims from a trusted relay, separate from observed Matrix identity."""

    longname: str | None
    shortname: str | None
    meshnet: str | None
    portnum: int | None
    packet_id: int | None
    sender_id: str | None
    source_adapter: str
    source_channel: str | None
    relation: EventRelation | None


def _reference(value: dict[str, Any] | None) -> NativeRef | None:
    return NativeRef(**value) if value is not None else None


def trusted_peer_metadata(
    event: CanonicalEvent,
    sender: str,
    self_mxid: str | None,
    peers: Mapping[str, PeerIdentity],
) -> PeerMetadata | None:
    """Read only coherent v1 provenance from an authorized Matrix sender.

    The codec preserves wire claims without granting them authority. Sender
    authorization is checked before those claims are considered. The authored
    body is never taken from an envelope or source-native fields.
    """
    if event.source_transport_id != sender or (
        sender != self_mxid and sender not in peers
    ):
        return None
    native = event.metadata.native
    if native is None:
        return None
    matrix = native.data.get("matrix", {})
    relay = matrix.get("relay", {}) if isinstance(matrix, Mapping) else {}
    if not isinstance(relay, Mapping):
        return None
    envelope = relay.get("medre_envelope")
    context = relay.get("bridge_context")
    if not isinstance(envelope, Mapping) or not isinstance(context, Mapping):
        return None
    # Canonical metadata is deeply frozen; JSON restores the actual wire shape.
    context = normalize_bridge_context(
        msgspec.json.decode(msgspec.json.encode(context))
    )
    if context is None:
        return None
    source = context["source"]
    if (
        type(envelope.get("schema_version")) is not int
        or envelope["schema_version"] != 1
        or envelope.get("canonical_event_id") != source["canonical_event_id"]
        or envelope.get("source_adapter") != source["adapter"]
        or envelope.get("source_transport_id") != source["sender_id"]
        or (envelope.get("source_channel") or None) != source["channel"]
        or envelope.get("source_native_ref") != source["native_ref"]
        or envelope.get("event_kind") not in {"message.created", "message.reacted"}
    ):
        return None
    reference = source["native_ref"]
    if reference is not None and (
        reference["adapter"] != source["adapter"]
        or reference["native_channel_id"] != source["channel"]
    ):
        return None
    facts = source.get("native_facts", {})
    radio = facts.get("namespace") == "meshtastic"
    fields = facts.get("fields", {}) if radio else {}
    portnum = fields.get("portnum")
    if isinstance(portnum, str):
        portnum = {
            "TEXT_MESSAGE_APP": PORTNUM_TEXT_MESSAGE_APP,
            "DETECTION_SENSOR_APP": PORTNUM_DETECTION_SENSOR_APP,
        }.get(portnum, radio_packet_id(portnum))
    if portnum is not None and (type(portnum) is not int or not 0 <= portnum <= 511):
        return None
    meshnet = fields.get("meshnet_label", source.get("origin_label"))
    if meshnet is not None and not isinstance(meshnet, str):
        return None
    relations = [
        hint
        for hint in context["relations"]
        if hint["relation_type"] in {"reply", "reaction"}
    ]
    if envelope.get("relation_info") != ",".join(
        sorted({hint["relation_type"] for hint in context["relations"]})
    ):
        return None
    if len(relations) > 1:
        return None
    relation = None
    if relations:
        hint = relations[0]
        kind, key = hint["relation_type"], hint["key"]
        if (kind == "reaction") != (envelope["event_kind"] == "message.reacted"):
            return None
        target = _reference(hint["target_native_ref"])
        if target is not None and (
            kind != "reaction" or isinstance(key, str) and key.strip()
        ):
            relation = EventRelation(
                relation_type=kind,
                target_event_id=None,
                target_native_ref=target,
                key=key,
                fallback_text=None,
            )
    return PeerMetadata(
        longname=source.get("sender_name"),
        shortname=source.get("sender_short_name"),
        meshnet=meshnet,
        portnum=portnum,
        packet_id=(
            radio_packet_id(reference["native_message_id"])
            if radio and reference
            else None
        ),
        sender_id=source["sender_id"],
        source_adapter=source["adapter"],
        source_channel=source["channel"],
        relation=relation,
    )
