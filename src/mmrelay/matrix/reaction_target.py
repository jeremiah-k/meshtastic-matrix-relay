"""Determine whether a Matrix emote has a resolvable relay reaction target."""

from __future__ import annotations


def has_emote_reaction_target(
    matrix_event_id: str | None,
    radio_reply_id: object,
    remote_meshnet: object,
    local_meshnet: str,
) -> bool:
    """Require a Matrix event target or a packet ID on a different meshnet.

    Packet ID zero is valid. A bare ``meshtastic_replyId`` in a Matrix
    emote is not evidence of a target and must not suppress authored text.
    """
    return bool(matrix_event_id) or (
        radio_reply_id not in (None, "")
        and isinstance(remote_meshnet, str)
        and bool(remote_meshnet)
        and remote_meshnet != local_meshnet
    )
