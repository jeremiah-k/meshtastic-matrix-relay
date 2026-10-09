"""Exercise Matrix plugin dispatch with the real mtjk command machinery."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from tests.remote_admin_test_support import (
    REMOTE_NODE_ID,
    REMOTE_NODE_NUM,
    real_interface,
)
from tests.remote_admin_test_support import real_mtjk as real_mtjk


@pytest.mark.integration
@pytest.mark.no_global_mocks
async def test_unmapped_admin_room_captures_get_through_plugin_and_real_dispatch() -> (
    None
):
    import inspect
    from unittest.mock import AsyncMock

    from meshtastic import __main__ as cli_main
    from meshtastic.protobuf import admin_pb2, mesh_pb2

    from mmrelay.matrix_utils import _dispatch_unmapped_room_message
    from mmrelay.plugins.remote_admin_plugin import Plugin

    if "cli_print" not in inspect.signature(cli_main.getPref).parameters:
        pytest.skip("the released mtjk pin predates the preference sink")
    interface = real_interface()
    plugin = Plugin()
    plugin.config = {"active": True, "admin_room": "!admin:matrix.org", "timeout": 3}
    plugin.send_matrix_message = AsyncMock()
    plugin.send_matrix_reaction = AsyncMock()
    room = SimpleNamespace(
        room_id="!admin:matrix.org",
        power_levels=SimpleNamespace(
            users={"@admin:matrix.org": 100}, defaults=SimpleNamespace(users_default=0)
        ),
    )
    body = f"!admin --dest {REMOTE_NODE_ID} --get lora.region"
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
        patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=interface),
        patch.object(interface, "_send_data_with_wait", side_effect=respond),
        patch.object(interface, "_has_active_wait_request", return_value=True),
        patch.object(interface, "_wait_for_request_ack", return_value=True),
        patch.object(interface, "_retire_wait_request"),
        patch.object(interface, "_raise_wait_error_if_present"),
        patch.object(interface, "close") as close,
    ):
        await _dispatch_unmapped_room_message(room, event)
    reply = plugin.send_matrix_message.call_args.args[1]
    assert "exit code 0" in reply
    assert "lora.region: 1" in reply
    close.assert_not_called()
