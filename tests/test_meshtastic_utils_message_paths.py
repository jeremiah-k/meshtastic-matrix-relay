from unittest.mock import AsyncMock, MagicMock, patch

from meshtastic.mesh_interface import BROADCAST_NUM

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.config import CONFIG_KEY_MESHNET_NAME
from mmrelay.constants.formats import EMOJI_FLAG_VALUE, TEXT_MESSAGE_APP
from mmrelay.constants.messages import (
    PORTNUM_DETECTION_SENSOR_APP,
    PORTNUM_TEXT_MESSAGE_APP,
)
from mmrelay.meshtastic_utils import on_meshtastic_message


def _base_config():
    """
    Return a minimal base configuration used by tests.
    
    Returns:
        dict: Configuration with:
            - "meshtastic": dict containing "connection_type" set to "serial" and the meshnet name under CONFIG_KEY_MESHNET_NAME (value "TestNet").
            - "matrix_rooms": list with a single room dict containing "id" set to "!room:test" and "meshtastic_channel" set to 0.
    """
    return {
        "meshtastic": {
            "connection_type": "serial",
            CONFIG_KEY_MESHNET_NAME: "TestNet",
        },
        "matrix_rooms": [{"id": "!room:test", "meshtastic_channel": 0}],
    }


def _base_packet():
    """
    Create a representative Meshtastic packet dictionary used by tests.
    
    Returns:
        dict: A packet containing:
            - fromId: sender node id (123)
            - to: recipient id (BROADCAST_NUM)
            - decoded: payload with `text` ("Hello") and `portnum` (TEXT_MESSAGE_APP)
            - channel: channel index (0)
            - id: message id (999)
    """
    return {
        "fromId": 123,
        "to": BROADCAST_NUM,
        "decoded": {"text": "Hello", "portnum": TEXT_MESSAGE_APP},
        "channel": 0,
        "id": 999,
    }


def _make_interface(node_id=999, nodes=None):
    """
    Create a MagicMock that simulates a Meshtastic interface for tests.
    
    Parameters:
        node_id (int): The node number to assign to interface.myInfo.my_node_num.
        nodes (dict | None): Mapping of node IDs to node info objects to attach to interface.nodes; uses an empty dict if None.
    
    Returns:
        MagicMock: A mock interface with `myInfo.my_node_num` and `nodes` set as provided.
    """
    interface = MagicMock()
    interface.myInfo.my_node_num = node_id
    interface.nodes = nodes or {}
    return interface


def _set_globals(config):
    """
    Assign the provided configuration to meshtastic_utils module globals.
    
    Set mu.config to the given config and mu.matrix_rooms to the value of the config's
    "matrix_rooms" key or an empty list if that key is missing.
    
    Parameters:
        config (dict): Configuration mapping to apply to mmrelay.meshtastic_utils.
    """
    mu.config = config
    mu.matrix_rooms = config.get("matrix_rooms", [])


def test_on_meshtastic_message_filters_reaction_when_disabled(
    reset_meshtastic_globals,
):
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"].update({"emoji": EMOJI_FLAG_VALUE})

    with (
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": True},
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert any(
        "Filtered out reaction packet" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


def test_on_meshtastic_message_reaction_missing_original(reset_meshtastic_globals):
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"].update({"emoji": EMOJI_FLAG_VALUE, "replyId": 42})

    with (
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": True, "replies": True},
        ),
        patch("mmrelay.meshtastic_utils.get_longname", return_value="Long"),
        patch("mmrelay.meshtastic_utils.get_shortname", return_value="Short"),
        patch(
            "mmrelay.meshtastic_utils.get_message_map_by_meshtastic_id",
            return_value=None,
        ),
        patch("mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert any(
        "Original message for reaction not found" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


def test_on_meshtastic_message_reply_missing_original(reset_meshtastic_globals):
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"].update({"replyId": 77})

    with (
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": True, "replies": True},
        ),
        patch("mmrelay.meshtastic_utils.get_longname", return_value="Long"),
        patch("mmrelay.meshtastic_utils.get_shortname", return_value="Short"),
        patch(
            "mmrelay.meshtastic_utils.get_message_map_by_meshtastic_id",
            return_value=None,
        ),
        patch("mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert any(
        "Original message for reply not found" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


def test_on_meshtastic_message_channel_fallback_numeric_portnum(
    reset_meshtastic_globals,
):
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["channel"] = None
    packet["decoded"]["portnum"] = PORTNUM_TEXT_MESSAGE_APP

    with (
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": False},
        ),
        patch("mmrelay.meshtastic_utils.get_longname", return_value="Long"),
        patch("mmrelay.meshtastic_utils.get_shortname", return_value="Short"),
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch("mmrelay.matrix_utils.get_matrix_prefix", return_value="[p] "),
        patch(
            "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
        ) as mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    mock_relay.assert_awaited_once()


def test_on_meshtastic_message_unknown_portnum_logs_debug(
    reset_meshtastic_globals,
):
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["channel"] = None
    packet["decoded"]["portnum"] = 9999

    with (
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": False},
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert any(
        "Unknown portnum" in call.args[0] for call in mock_logger.debug.call_args_list
    )


def test_on_meshtastic_message_detection_sensor_disabled(
    reset_meshtastic_globals,
):
    config = _base_config()
    config["meshtastic"]["detection_sensor"] = False
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["portnum"] = PORTNUM_DETECTION_SENSOR_APP

    with (
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": False},
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert any(
        "Detection sensor packet received" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


def test_on_meshtastic_message_saves_node_names_from_interface(
    reset_meshtastic_globals,
):
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()

    nodes = {
        123: {
            "user": {"longName": "Mesh Long", "shortName": "ML"},
        }
    }

    with (
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": False},
        ),
        patch("mmrelay.meshtastic_utils.get_longname", return_value=None),
        patch("mmrelay.meshtastic_utils.get_shortname", return_value=None),
        patch("mmrelay.meshtastic_utils.save_longname") as mock_save_long,
        patch("mmrelay.meshtastic_utils.save_shortname") as mock_save_short,
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch("mmrelay.matrix_utils.get_matrix_prefix", return_value="[p] "),
        patch("mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock),
    ):
        on_meshtastic_message(packet, _make_interface(nodes=nodes))

    mock_save_long.assert_called_once_with(123, "Mesh Long")
    mock_save_short.assert_called_once_with(123, "ML")


def test_on_meshtastic_message_falls_back_to_sender_id(
    reset_meshtastic_globals,
):
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()

    with (
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": False},
        ),
        patch("mmrelay.meshtastic_utils.get_longname", return_value=None),
        patch("mmrelay.meshtastic_utils.get_shortname", return_value=None),
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch("mmrelay.matrix_utils.get_matrix_prefix") as mock_prefix,
        patch("mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        on_meshtastic_message(packet, _make_interface(nodes={}))

    mock_prefix.assert_called_once_with(config, "123", "123", "TestNet")
    assert any(
        "Node info for sender 123" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


def test_on_meshtastic_message_direct_message_skips_relay(
    reset_meshtastic_globals, fast_async_helpers
):
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["to"] = 999
    interface = _make_interface(node_id=999)

    fast_submit, fast_wait = fast_async_helpers
    plugin = MagicMock()
    plugin.plugin_name = "plugin"
    plugin.handle_meshtastic_message = AsyncMock(return_value=False)

    with (
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": False},
        ),
        patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]),
        patch("mmrelay.meshtastic_utils._submit_coro", side_effect=fast_submit),
        patch("mmrelay.meshtastic_utils._wait_for_result", side_effect=fast_wait),
        patch(
            "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
        ) as mock_relay,
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        on_meshtastic_message(packet, interface)

    mock_relay.assert_not_called()
    assert any(
        "Received a direct message" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


def test_on_meshtastic_message_logs_when_matrix_rooms_falsy(
    reset_meshtastic_globals, fast_async_helpers
):
    class FalsyRooms(list):
        def __bool__(self):
            """
            Indicates that instances of this class are always considered false in boolean contexts.
            
            Returns:
                bool: `False` always.
            """
            return False

    config = _base_config()
    falsy_rooms = FalsyRooms(config["matrix_rooms"])
    mu.config = config
    mu.matrix_rooms = falsy_rooms
    packet = _base_packet()

    fast_submit, fast_wait = fast_async_helpers

    with (
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": False},
        ),
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch("mmrelay.meshtastic_utils._submit_coro", side_effect=fast_submit),
        patch("mmrelay.meshtastic_utils._wait_for_result", side_effect=fast_wait),
        patch("mmrelay.matrix_utils.get_matrix_prefix", return_value="[p] "),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert any(
        "matrix_rooms is empty" in call.args[0]
        for call in mock_logger.error.call_args_list
    )


def test_on_meshtastic_message_skips_non_dict_rooms(reset_meshtastic_globals):
    config = _base_config()
    _set_globals(config)
    mu.matrix_rooms = ["not-a-room", config["matrix_rooms"][0]]
    packet = _base_packet()

    with (
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": False},
        ),
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch("mmrelay.matrix_utils.get_matrix_prefix", return_value="[p] "),
        patch(
            "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
        ) as mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    mock_relay.assert_awaited_once()


def test_on_meshtastic_message_non_text_plugin_returns_none(
    reset_meshtastic_globals,
):
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"].pop("text")

    plugin = MagicMock()
    plugin.plugin_name = "noawait"
    plugin.handle_meshtastic_message.return_value = None

    with (
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": False},
        ),
        patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert any(
        "returned no awaitable" in call.args[0]
        for call in mock_logger.warning.call_args_list
    )


def test_on_meshtastic_message_non_text_plugin_exception(reset_meshtastic_globals):
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"].pop("text")

    plugin = MagicMock()
    plugin.plugin_name = "boom"
    plugin.handle_meshtastic_message.side_effect = RuntimeError("bad")

    with (
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": False},
        ),
        patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert any(
        "Plugin boom failed" in call.args[0]
        for call in mock_logger.exception.call_args_list
    )