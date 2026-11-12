"""Build actual canonical peer events for relay-domain integration tests."""

import importlib
import sys

from mmrelay.matrix.canonical_outbound import render_matrix_relay_content


async def set_peer_content(event, room, config, **overrides):
    values = dict(
        room_id=room.room_id,
        message="Detection data",
        longname="TestUser",
        shortname="TU",
        meshnet_name="Remote",
        portnum=10,
        source_node_id="!000000ab",
        meshtastic_id=0,
    )
    values.update(overrides)
    mocked = sys.modules.pop("markdown", None)
    try:
        importlib.import_module("markdown")
        content = await render_matrix_relay_content(**values)
    finally:
        if mocked is not None:
            sys.modules["markdown"] = mocked
        else:
            sys.modules.pop("markdown", None)
    event.event_id = "$canonical-peer"
    event.body = content["body"]
    event.source = {"type": "m.room.message", "content": content}
    config.setdefault("matrix", {})["trusted_relays"] = [event.sender]
