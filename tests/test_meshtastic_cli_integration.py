"""Exercise Matrix plugin dispatch with the real mtjk command machinery."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from tests.meshtastic_cli_test_support import (
    REMOTE_NODE_ID,
    REMOTE_NODE_NUM,
    real_interface,
)
from tests.meshtastic_cli_test_support import real_mtjk as real_mtjk


@pytest.mark.integration
@pytest.mark.no_global_mocks
async def test_unmapped_admin_room_captures_get_through_plugin_and_real_dispatch() -> (
    None
):
    from unittest.mock import AsyncMock

    from nio import RoomGetStateResponse

    pytest.importorskip("meshtastic.commands")
    from meshtastic.protobuf import admin_pb2, mesh_pb2

    from mmrelay.matrix_utils import _dispatch_unmapped_room_message
    from mmrelay.plugins.meshtastic_cli_plugin import Plugin

    interface = real_interface()
    interface.isConnected.set()
    plugin = Plugin()
    plugin.config = {"active": True, "admin_room": "!admin:matrix.org", "timeout": 3}
    plugin.send_matrix_reaction = AsyncMock()
    matrix_client = SimpleNamespace(
        room_send=AsyncMock(),
        room_get_state=AsyncMock(
            return_value=RoomGetStateResponse(
                events=[
                    {
                        "type": "m.room.power_levels",
                        "state_key": "",
                        "content": {"users": {"@admin:matrix.org": 100}},
                    }
                ],
                room_id="!admin:matrix.org",
            )
        ),
    )
    room = SimpleNamespace(
        room_id="!admin:matrix.org",
        power_levels=SimpleNamespace(
            users={"@admin:matrix.org": 100}, defaults=SimpleNamespace(users_default=0)
        ),
    )
    body = f"!meshtastic --dest {REMOTE_NODE_ID} --get lora.region"
    event = SimpleNamespace(
        body=body,
        sender="@admin:matrix.org",
        event_id="$command",
        source={"content": {"body": body}},
    )

    def respond(data: Any, destination: Any, **kwargs: Any) -> Any:
        response = admin_pb2.AdminMessage()
        response.get_config_response.lora.region = 1
        kwargs["onResponse"](
            {
                "from": REMOTE_NODE_NUM,
                "decoded": {
                    "requestId": 17,
                    "admin": {
                        "getConfigResponse": {"lora": {"region": "US"}},
                        "raw": response,
                    },
                },
            }
        )
        return mesh_pb2.MeshPacket(id=17)

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]),
        patch("mmrelay.matrix_utils.connect_matrix", return_value=matrix_client),
        patch("mmrelay.matrix_utils.matrix_client", matrix_client),
        patch("mmrelay.meshtastic_utils.meshtastic_client", interface),
        patch("mmrelay.meshtastic_utils.reconnecting", False),
        patch("mmrelay.meshtastic_utils.shutting_down", False),
        patch("mmrelay.meshtastic_utils.connect_meshtastic") as connect_again,
        patch.object(interface, "_send_data_with_wait", side_effect=respond),
        patch.object(interface, "_has_active_wait_request", return_value=True),
        patch.object(interface, "_wait_for_request_ack", return_value=True),
        patch.object(interface, "_retire_wait_request"),
        patch.object(interface, "_raise_wait_error_if_present"),
        patch.object(interface, "close") as close,
    ):
        await _dispatch_unmapped_room_message(room, event)
    sent = matrix_client.room_send.call_args.kwargs
    reply = sent["content"]["body"]
    assert "exit code 0" in reply
    assert "lora.region: 1" in reply
    assert sent["ignore_unverified_devices"] is True
    assert sent["content"]["m.relates_to"] == {
        "m.in_reply_to": {"event_id": "$command"}
    }
    close.assert_not_called()
    connect_again.assert_not_called()
