"""Canonicalize Meshtastic node identifiers.

The Meshtastic library represents a node in more than one form. When a node is
known to the NodeDB, packets carry a ``fromId`` of the form ``!f66fb1ac``.
When the node is unknown, has no user payload, or has a non-string ``user.id``,
the library leaves ``fromId`` as ``None`` and callers fall back to the raw mesh
integer, e.g. ``4134515116``.

Those two forms describe the same node but are different strings, so any table
keyed by ``str(meshtastic_id)`` stores one form and looks up the other. This
module normalizes to the ``!hex`` node-id form, the convention the library uses
for known nodes.
"""

from __future__ import annotations

from typing import Any

__all__ = ["normalize_node_id"]

# Meshtastic node numbers are 32-bit unsigned values.
_MAX_NODE_NUM = 0xFFFFFFFF


def normalize_node_id(value: Any) -> Any:
    """
    Normalize a Meshtastic node identifier to its canonical ``!hex`` form.

    Integer node numbers are converted to ``!`` followed by eight lowercase hex
    digits, matching the ``fromId`` the Meshtastic library populates for known
    nodes. Values that are not plain node numbers -- already-formatted ids,
    broadcast and sentinel strings, ``None``, and out-of-range integers -- are
    returned unchanged so callers can keep their existing handling.

    Parameters:
        value: A node identifier of any supported type.

    Returns:
        The canonical ``!hex`` string for integer node numbers, otherwise the
        input unchanged.
    """
    # bool is an int subclass; True must not become "!00000001".
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value < 0 or value > _MAX_NODE_NUM:
            return value
        return f"!{value:08x}"
    return value
