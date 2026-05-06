#!/usr/bin/env python3
"""
Test suite for Meshtastic packet routing and plugin dispatch in MMRelay.

Tests packet routing, portnum classification, plugin dispatch, and related
message filtering behavior within the Meshtastic-to-Matrix relay path.
"""

import asyncio
import contextlib
import threading
from collections.abc import Iterator
from concurrent.futures import Future as FuturesFuture
from concurrent.futures import TimeoutError as ConcurrentTimeoutError
from contextlib import ExitStack, contextmanager
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, Mock, patch

import pytest
from meshtastic import BROADCAST_NUM

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.config import CONFIG_KEY_MESHNET_NAME
from mmrelay.constants.formats import EMOJI_FLAG_VALUE, TEXT_MESSAGE_APP
from mmrelay.constants.messages import (
    PORTNUM_DETECTION_SENSOR_APP,
    PORTNUM_TEXT_MESSAGE_APP,
)
from mmrelay.constants.network import DEFAULT_PLUGIN_TIMEOUT_SECS
from mmrelay.meshtastic.packet_routing import (
    PacketAction,
    _get_packet_routing_overrides,
    _resolve_portnum_set,
    classify_packet,
)
from mmrelay.meshtastic_utils import on_meshtastic_message

# ---------------------------------------------------------------------------
# Test helpers (functionally identical to those in
# test_meshtastic_utils_messages.py to keep each file self-contained)
# ---------------------------------------------------------------------------


def _base_config():
    """
    Return a minimal base configuration used by tests.

    Returns:
        dict: Configuration with:
            - "meshtastic": dict containing "connection_type" set to "serial"
              and the meshnet name under CONFIG_KEY_MESHNET_NAME (value "TestNet").
            - "matrix_rooms": list with a single room dict containing "id" set
              to "!room:test" and "meshtastic_channel" set to 0.
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
        nodes (dict | None): Mapping of node IDs to node info objects to attach
            to interface.nodes; uses an empty dict if None.

    Returns:
        MagicMock: A mock interface with `myInfo.my_node_num` and `nodes` set
        as provided.
    """
    interface = MagicMock()
    interface.myInfo.my_node_num = node_id
    interface.nodes = nodes or {}
    return interface


def _set_globals(config):
    """
    Assign the provided configuration to meshtastic_utils module globals.

    Set mu.config to the given config and mu.matrix_rooms to the value of the
    config's "matrix_rooms" key or an empty list if that key is missing.

    Parameters:
        config (dict): Configuration mapping to apply to mmrelay.meshtastic_utils.
    """
    mu.config = config
    mu.matrix_rooms = config.get("matrix_rooms", [])


@contextmanager
def _patch_message_deps(
    interaction_settings=None,
    longname: str | None = "Long",
    shortname: str | None = "Short",
    message_map=None,
    plugins=None,
    matrix_prefix="[p] ",
    patch_logger=True,
    patch_relay=True,
) -> Iterator[tuple[Any | None, Any | None]]:
    if interaction_settings is None:
        interaction_settings = {"reactions": False, "replies": False}
    if plugins is None:
        plugins = []

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "mmrelay.matrix_utils.get_interaction_settings",
                return_value=interaction_settings,
            )
        )
        stack.enter_context(
            patch("mmrelay.meshtastic_utils.get_longname", return_value=longname)
        )
        stack.enter_context(
            patch("mmrelay.meshtastic_utils.get_shortname", return_value=shortname)
        )
        stack.enter_context(
            patch(
                "mmrelay.meshtastic_utils.get_message_map_by_meshtastic_id",
                return_value=message_map,
            )
        )
        stack.enter_context(
            patch("mmrelay.plugin_loader.load_plugins", return_value=plugins)
        )
        stack.enter_context(
            patch("mmrelay.matrix_utils.get_matrix_prefix", return_value=matrix_prefix)
        )
        mock_relay = (
            stack.enter_context(
                patch("mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock)
            )
            if patch_relay
            else None
        )
        mock_logger = (
            stack.enter_context(patch("mmrelay.meshtastic_utils.logger"))
            if patch_logger
            else None
        )
        yield mock_logger, mock_relay


class _DummyFuture:
    """Helper class to simulate a future that records timeout values and raises an exception.

    Parameters:
        exc (BaseException): The exception to raise when result() is called.
    """

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.calls: list[float | None] = []

    def result(self, timeout: float | None = None) -> None:
        self.calls.append(timeout)
        raise self.exc


def _make_submit_side_effect(future):
    """Return a side effect function that closes coroutines and returns the given future."""

    def _submit(coro, **_kwargs):
        if asyncio.iscoroutine(coro):
            coro.close()
        return future

    return _submit


def _base_config_with_routing(
    chat_portnums: list[Any] | str | None = None,
    disabled_portnums: list[Any] | str | None = None,
    encrypted_action: str | None = None,
) -> dict[str, Any]:
    config = _base_config()
    routing: dict[str, Any] = {}
    if chat_portnums is not None:
        routing["chat_portnums"] = chat_portnums
    if disabled_portnums is not None:
        routing["disabled_portnums"] = disabled_portnums
    if encrypted_action is not None:
        routing["encrypted_action"] = encrypted_action
    if routing:
        config["meshtastic"]["packet_routing"] = routing
    return config


# ---------------------------------------------------------------------------
# Autouse fixture -- mirrors the one in test_meshtastic_utils_messages.py
# so that isolated test state is guaranteed regardless of which file a given
# test lives in.
# ---------------------------------------------------------------------------


def _cancel_startup_drain_timer() -> None:
    """Best-effort cancellation and join of the startup-drain expiry timer."""
    import mmrelay.meshtastic_utils as _mu

    _timer = getattr(_mu, "_relay_startup_drain_expiry_timer", None)
    if _timer is None:
        return
    with contextlib.suppress(AttributeError, RuntimeError, TypeError):
        _timer.cancel()
    _join = getattr(_timer, "join", None)
    if callable(_join):
        with contextlib.suppress(AttributeError, RuntimeError, TypeError):
            _join(0.2)
    with contextlib.suppress(AttributeError):
        _mu._relay_startup_drain_expiry_timer = None


@pytest.fixture(autouse=True)
def reset_meshtastic_relay_state(monkeypatch):
    """Reset all Meshtastic relay module globals to prevent cross-test leakage."""

    _cancel_startup_drain_timer()

    startup_drain_complete_event = threading.Event()
    startup_drain_complete_event.set()
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_active_client_id",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_rx_time_clock_skew_secs",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_startup_drain_deadline_monotonic_secs",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_startup_drain_expiry_timer",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_startup_drain_complete_event",
        startup_drain_complete_event,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._startup_packet_drain_applied",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_connection_started_monotonic_secs",
        0.0,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.subscribed_to_messages",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.subscribed_to_connection_lost",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._health_probe_request_deadlines",
        {},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.config",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.matrix_rooms",
        [],
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.meshtastic_client",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.meshtastic_iface",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.shutting_down",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.reconnecting",
        False,
        raising=False,
    )

    yield

    _cancel_startup_drain_timer()


# ---------------------------------------------------------------------------
# Test functions -- plugin dispatch / packet routing / portnum classification
# ---------------------------------------------------------------------------


def test_on_meshtastic_message_filters_reaction_when_disabled():
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"].update({"emoji": EMOJI_FLAG_VALUE, "replyId": 42})

    with (
        patch(
            "mmrelay.matrix_utils.get_interaction_settings",
            return_value={"reactions": False, "replies": True},
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        on_meshtastic_message(packet, _make_interface())

    mock_logger.debug.assert_any_call(
        "Filtered out reaction packet due to reactions being disabled."
    )


def test_on_meshtastic_message_does_not_filter_plain_emoji_message_when_reactions_disabled():
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"].update({"text": "👏", "emoji": EMOJI_FLAG_VALUE})

    with _patch_message_deps(
        interaction_settings={"reactions": False, "replies": True},
        patch_logger=False,
    ) as (_mock_logger, mock_relay):
        on_meshtastic_message(packet, _make_interface(nodes={}))

    assert mock_relay is not None
    mock_relay.assert_awaited_once()


def test_on_meshtastic_message_reaction_missing_original():
    """Test that reactions with missing originals are relayed as normal messages."""
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"].update({"emoji": EMOJI_FLAG_VALUE, "replyId": 42})

    with _patch_message_deps(
        interaction_settings={"reactions": True, "replies": True},
    ) as (mock_logger, mock_relay):
        on_meshtastic_message(packet, _make_interface())

    assert mock_logger is not None
    mock_logger.warning.assert_any_call(
        "Original message for reaction (replyId=%s) not found in DB. "
        "Relaying as normal message instead.",
        42,
    )
    assert mock_relay is not None
    mock_relay.assert_awaited_once()


def test_on_meshtastic_message_reply_missing_original():
    """Test that replies with missing originals are relayed as normal messages."""
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"].update({"replyId": 77})

    with _patch_message_deps(
        interaction_settings={"reactions": True, "replies": True},
    ) as (mock_logger, mock_relay):
        on_meshtastic_message(packet, _make_interface())

    assert mock_logger is not None
    mock_logger.warning.assert_any_call(
        "Original message for reply (replyId=%s) not found in DB. "
        "Relaying as normal message instead.",
        77,
    )
    assert mock_relay is not None
    mock_relay.assert_awaited_once()


def test_on_meshtastic_message_channel_fallback_numeric_portnum():
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["channel"] = None
    packet["decoded"]["portnum"] = PORTNUM_TEXT_MESSAGE_APP

    with _patch_message_deps(patch_logger=False) as (_mock_logger, mock_relay):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_awaited_once()


def test_on_meshtastic_message_unknown_portnum_plugin_only():
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["channel"] = None
    packet["decoded"]["portnum"] = 9999

    plugin = MagicMock()
    plugin.plugin_name = "observer"
    plugin.handle_meshtastic_message.return_value = False

    with _patch_message_deps(plugins=[plugin], patch_logger=False) as (
        _mock_logger,
        mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_not_called()
    plugin.handle_meshtastic_message.assert_called_once()


def test_on_meshtastic_message_unknown_numeric_portnum_plugin_only():
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["portnum"] = 9999

    plugin = MagicMock()
    plugin.plugin_name = "observer"
    plugin.handle_meshtastic_message.return_value = False

    with _patch_message_deps(plugins=[plugin], patch_logger=False) as (
        _mock_logger,
        mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_not_called()
    plugin.handle_meshtastic_message.assert_called_once()


def test_on_meshtastic_message_range_test_app_plugin_only():
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["portnum"] = "RANGE_TEST_APP"

    plugin = MagicMock()
    plugin.plugin_name = "range-observer"
    plugin.handle_meshtastic_message.return_value = False

    with _patch_message_deps(plugins=[plugin], patch_logger=False) as (
        _mock_logger,
        mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_not_called()
    plugin.handle_meshtastic_message.assert_called_once()
    args = plugin.handle_meshtastic_message.call_args[0]
    assert args[1] == "[p] Hello"
    assert args[2] == "Long"
    assert args[3] == "TestNet"


def test_on_meshtastic_message_detection_sensor_disabled():
    config = _base_config()
    config["meshtastic"]["detection_sensor"] = False
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["portnum"] = PORTNUM_DETECTION_SENSOR_APP

    plugin = MagicMock()
    plugin.plugin_name = "sensor-plugin"
    plugin.handle_meshtastic_message.return_value = False

    with _patch_message_deps(plugins=[plugin], patch_logger=False) as (
        _mock_logger,
        mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_not_called()
    plugin.handle_meshtastic_message.assert_called_once()


def test_on_meshtastic_message_detection_sensor_enabled_relays():
    config = _base_config()
    config["meshtastic"]["detection_sensor"] = True
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["portnum"] = PORTNUM_DETECTION_SENSOR_APP

    with _patch_message_deps(patch_logger=False) as (_mock_logger, mock_relay):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_awaited_once()


def test_on_meshtastic_message_saves_node_names_from_interface():
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()

    nodes = {
        123: {
            "user": {"longName": "Mesh Long", "shortName": "ML"},
        }
    }

    with (
        _patch_message_deps(
            longname=None,
            shortname=None,
            patch_logger=False,
        ),
        patch("mmrelay.meshtastic_utils.save_longname") as mock_save_long,
        patch("mmrelay.meshtastic_utils.save_shortname") as mock_save_short,
    ):
        on_meshtastic_message(packet, _make_interface(nodes=nodes))

    mock_save_long.assert_called_once_with(123, "Mesh Long")
    mock_save_short.assert_called_once_with(123, "ML")


def test_on_meshtastic_message_falls_back_to_sender_id():
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
    mock_logger.debug.assert_any_call("Node info for sender 123 not available yet.")


def test_on_meshtastic_message_direct_message_skips_relay():
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["to"] = 999
    interface = _make_interface(node_id=999)

    with _patch_message_deps() as (mock_logger, mock_relay):
        on_meshtastic_message(packet, interface)

    assert mock_relay is not None
    assert mock_logger is not None
    mock_relay.assert_not_called()
    mock_logger.debug.assert_any_call(
        "Received a direct message from Long: Hello. Not relaying to Matrix."
    )


def test_on_meshtastic_message_ignores_messages_for_other_nodes():
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["to"] = 1000
    interface = _make_interface(node_id=999)

    with _patch_message_deps() as (mock_logger, mock_relay):
        on_meshtastic_message(packet, interface)

    assert mock_relay is not None
    assert mock_logger is not None
    mock_relay.assert_not_called()
    mock_logger.debug.assert_any_call(
        "Ignoring message intended for node %s (not broadcast or relay).", 1000
    )


def test_on_meshtastic_message_logs_when_matrix_rooms_falsy():
    class FalsyRooms(list):
        def __bool__(self):
            """Make list instances always falsy in boolean context."""
            return False

    config = _base_config()
    falsy_rooms = FalsyRooms(config["matrix_rooms"])
    mu.config = config
    mu.matrix_rooms = falsy_rooms
    packet = _base_packet()

    with _patch_message_deps() as (mock_logger, _mock_relay):
        on_meshtastic_message(packet, _make_interface())

    assert mock_logger is not None
    assert any(
        "matrix_rooms is empty" in str(call)
        for call in mock_logger.warning.call_args_list
    ), f"Expected warning about empty matrix_rooms, got: {mock_logger.warning.call_args_list}"


def test_on_meshtastic_message_skips_non_dict_rooms():
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


def test_on_meshtastic_message_non_text_plugin_returns_none():
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
        patch("mmrelay.meshtastic_utils.logger"),
    ):
        on_meshtastic_message(packet, _make_interface())

    plugin.handle_meshtastic_message.assert_called_once_with(
        packet, formatted_message=None, longname=None, meshnet_name=None
    )


def test_on_meshtastic_message_non_text_plugin_exception():
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

    mock_logger.exception.assert_any_call("Plugin %s failed", "boom")


def test_on_meshtastic_message_chat_portnums_override_promotes_range_test():
    config = _base_config_with_routing(chat_portnums=["RANGE_TEST_APP"])
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["portnum"] = "RANGE_TEST_APP"

    with _patch_message_deps(plugins=[], patch_logger=False) as (
        _mock_logger,
        mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_awaited_once()


def test_on_meshtastic_message_chat_portnums_override_promotes_via_numeric_config():
    RANGE_TEST_NUM = 70
    config = _base_config_with_routing(chat_portnums=[RANGE_TEST_NUM])
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["portnum"] = RANGE_TEST_NUM

    with (
        patch(
            "mmrelay.meshtastic.packet_routing.portnums_pb2.PortNum.Name",
            return_value="RANGE_TEST_APP",
        ),
        _patch_message_deps(plugins=[], patch_logger=False) as (
            _mock_logger,
            mock_relay,
        ),
    ):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_awaited_once()


def test_on_meshtastic_message_chat_portnums_string_value():
    config = _base_config_with_routing(chat_portnums="RANGE_TEST_APP")
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["portnum"] = "RANGE_TEST_APP"

    with _patch_message_deps(plugins=[], patch_logger=False) as (
        _mock_logger,
        mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_awaited_once()


def test_on_meshtastic_message_disabled_portnums_drops_packet():
    config = _base_config_with_routing(disabled_portnums=["RANGE_TEST_APP"])
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["portnum"] = "RANGE_TEST_APP"

    plugin = MagicMock()
    plugin.plugin_name = "observer"
    plugin.handle_meshtastic_message.return_value = False

    with _patch_message_deps(plugins=[plugin], patch_logger=False) as (
        _mock_logger,
        mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_not_called()
    plugin.handle_meshtastic_message.assert_not_called()


def test_on_meshtastic_message_disabled_portnums_does_not_affect_text_message():
    config = _base_config_with_routing(disabled_portnums=["RANGE_TEST_APP"])
    _set_globals(config)
    packet = _base_packet()

    with _patch_message_deps(plugins=[], patch_logger=False) as (
        _mock_logger,
        mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_awaited_once()


def test_on_meshtastic_message_disabled_portnums_takes_precedence_over_chat():
    config = _base_config_with_routing(
        chat_portnums=["RANGE_TEST_APP"],
        disabled_portnums=["RANGE_TEST_APP"],
    )
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["portnum"] = "RANGE_TEST_APP"

    plugin = MagicMock()
    plugin.plugin_name = "observer"
    plugin.handle_meshtastic_message.return_value = False

    with _patch_message_deps(plugins=[plugin], patch_logger=False) as (
        _mock_logger,
        mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_not_called()
    plugin.handle_meshtastic_message.assert_not_called()


def test_on_meshtastic_message_chat_portnums_detection_sensor_still_gated():
    config = _base_config_with_routing(chat_portnums=["DETECTION_SENSOR_APP"])
    config["meshtastic"]["detection_sensor"] = False
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["portnum"] = PORTNUM_DETECTION_SENSOR_APP

    plugin = MagicMock()
    plugin.plugin_name = "sensor"
    plugin.handle_meshtastic_message.return_value = False

    with _patch_message_deps(plugins=[plugin], patch_logger=False) as (
        _mock_logger,
        mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_not_called()
    plugin.handle_meshtastic_message.assert_called_once()


def test_resolve_portnum_set_with_list():
    result = _resolve_portnum_set(["TEXT_MESSAGE_APP", "RANGE_TEST_APP"])
    assert result == frozenset({"TEXT_MESSAGE_APP", "RANGE_TEST_APP"})


def test_resolve_portnum_set_with_string():
    result = _resolve_portnum_set("RANGE_TEST_APP")
    assert result == frozenset({"RANGE_TEST_APP"})


def test_resolve_portnum_set_with_none():
    result = _resolve_portnum_set(None)
    assert result == frozenset()


def test_resolve_portnum_set_with_empty_list():
    result = _resolve_portnum_set([])
    assert result == frozenset()


def test_resolve_portnum_set_filters_unknown():
    result = _resolve_portnum_set(["TEXT_MESSAGE_APP", None])
    assert result == frozenset({"TEXT_MESSAGE_APP"})


def test_get_packet_routing_overrides_empty_config():
    chat, disabled = _get_packet_routing_overrides({})
    assert chat == frozenset()
    assert disabled == frozenset()


def test_get_packet_routing_overrides_none_config():
    chat, disabled = _get_packet_routing_overrides(None)
    assert chat == frozenset()
    assert disabled == frozenset()


def test_get_packet_routing_overrides_with_values():
    config = {
        "meshtastic": {
            "packet_routing": {
                "chat_portnums": ["RANGE_TEST_APP"],
                "disabled_portnums": ["TELEMETRY_APP"],
            }
        }
    }
    chat, disabled = _get_packet_routing_overrides(config)
    assert chat == frozenset({"RANGE_TEST_APP"})
    assert disabled == frozenset({"TELEMETRY_APP"})


def test_on_meshtastic_message_non_chat_text_with_no_channel_reaches_plugins():
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["portnum"] = "RANGE_TEST_APP"
    packet["decoded"]["text"] = "range test payload"
    packet.pop("channel", None)

    plugin = MagicMock()
    plugin.plugin_name = "observer"
    plugin.handle_meshtastic_message.return_value = False

    with _patch_message_deps(plugins=[plugin], patch_logger=False) as (
        _mock_logger,
        mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_not_called()
    plugin.handle_meshtastic_message.assert_called_once()


def test_on_meshtastic_message_disabled_text_message_reaction_does_not_relay():
    config = _base_config_with_routing(disabled_portnums=["TEXT_MESSAGE_APP"])
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"].update({"emoji": EMOJI_FLAG_VALUE, "replyId": 42})

    plugin = MagicMock()
    plugin.plugin_name = "observer"
    plugin.handle_meshtastic_message.return_value = False

    with _patch_message_deps(
        interaction_settings={"reactions": True, "replies": True},
        plugins=[plugin],
        patch_logger=False,
    ) as (_mock_logger, mock_relay):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_not_called()
    plugin.handle_meshtastic_message.assert_not_called()


def test_on_meshtastic_message_disabled_text_message_reply_does_not_relay():
    config = _base_config_with_routing(disabled_portnums=["TEXT_MESSAGE_APP"])
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["replyId"] = 77

    plugin = MagicMock()
    plugin.plugin_name = "observer"
    plugin.handle_meshtastic_message.return_value = False

    with _patch_message_deps(
        interaction_settings={"reactions": True, "replies": True},
        plugins=[plugin],
        patch_logger=False,
    ) as (_mock_logger, mock_relay):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_not_called()
    plugin.handle_meshtastic_message.assert_not_called()


def test_on_meshtastic_message_plugin_only_packet_with_replyId_does_not_leak_to_matrix():
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["portnum"] = "RANGE_TEST_APP"
    packet["decoded"]["replyId"] = 42
    packet["decoded"]["emoji"] = EMOJI_FLAG_VALUE

    plugin = MagicMock()
    plugin.plugin_name = "observer"
    plugin.handle_meshtastic_message.return_value = False

    with _patch_message_deps(
        interaction_settings={"reactions": True, "replies": True},
        plugins=[plugin],
        patch_logger=False,
    ) as (_mock_logger, mock_relay):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_not_called()
    plugin.handle_meshtastic_message.assert_called_once()


def test_classify_packet_encrypted_default_is_plugin_only():
    config = _base_config()
    packet = {"encrypted": True}
    action = classify_packet(None, config, packet)
    assert action == PacketAction.PLUGIN_ONLY


def test_classify_packet_encrypted_action_drop():
    config = _base_config_with_routing(encrypted_action="drop")
    packet = {"encrypted": True}
    action = classify_packet(None, config, packet)
    assert action == PacketAction.DROP


def test_classify_packet_encrypted_action_plugin_only():
    config = _base_config_with_routing(encrypted_action="plugin_only")
    packet = {"encrypted": True}
    action = classify_packet(None, config, packet)
    assert action == PacketAction.PLUGIN_ONLY


def test_classify_packet_encrypted_never_relays():
    config = _base_config_with_routing(
        chat_portnums=["ENCRYPTED"],
        encrypted_action="plugin_only",
    )
    packet = {"encrypted": True, "decoded": {"text": "secret"}}
    action = classify_packet(None, config, packet)
    assert action == PacketAction.PLUGIN_ONLY


def test_classify_packet_encrypted_ignores_disabled_portnums():
    config = _base_config_with_routing(
        disabled_portnums=["ENCRYPTED"],
        encrypted_action="plugin_only",
    )
    packet = {"encrypted": True}
    action = classify_packet(None, config, packet)
    assert action == PacketAction.PLUGIN_ONLY


def test_classify_packet_without_packet_kwarg_still_works():
    config = _base_config()
    action = classify_packet(TEXT_MESSAGE_APP, config)
    assert action == PacketAction.RELAY


def test_on_meshtastic_message_chat_portnums_promoted_no_channel_runs_plugins():
    config = _base_config_with_routing(chat_portnums=["RANGE_TEST_APP"])
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["portnum"] = "RANGE_TEST_APP"
    packet["decoded"]["text"] = "range test payload"
    packet.pop("channel", None)

    plugin = MagicMock()
    plugin.plugin_name = "observer"
    plugin.handle_meshtastic_message.return_value = False

    with _patch_message_deps(plugins=[plugin], patch_logger=False) as (
        _mock_logger,
        mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_not_called()
    plugin.handle_meshtastic_message.assert_called_once()


def test_on_meshtastic_message_encrypted_action_drop_drops_before_plugins():
    config = _base_config_with_routing(encrypted_action="drop")
    _set_globals(config)
    packet = {
        "fromId": 123,
        "to": BROADCAST_NUM,
        "id": 999,
        "encrypted": True,
    }

    plugin = MagicMock()
    plugin.plugin_name = "observer"
    plugin.handle_meshtastic_message.return_value = False

    with _patch_message_deps(plugins=[plugin], patch_logger=False) as (
        _mock_logger,
        mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_not_called()
    plugin.handle_meshtastic_message.assert_not_called()


def test_on_meshtastic_message_encrypted_default_runs_plugins():
    config = _base_config()
    _set_globals(config)
    packet = {
        "fromId": 123,
        "to": BROADCAST_NUM,
        "id": 999,
        "encrypted": True,
    }

    plugin = MagicMock()
    plugin.plugin_name = "observer"
    plugin.handle_meshtastic_message.return_value = False

    with _patch_message_deps(plugins=[plugin], patch_logger=False) as (
        _mock_logger,
        mock_relay,
    ):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_not_called()
    plugin.handle_meshtastic_message.assert_called_once()


def test_on_meshtastic_message_text_app_malformed_channel_defaults_to_zero():
    config = _base_config()
    _set_globals(config)
    packet = _base_packet()
    packet["channel"] = "abc"

    with _patch_message_deps(patch_logger=False) as (_mock_logger, mock_relay):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_awaited_once()


def test_on_meshtastic_message_promoted_non_chat_malformed_channel_skips_relay():
    config = _base_config_with_routing(chat_portnums=["RANGE_TEST_APP"])
    _set_globals(config)
    packet = _base_packet()
    packet["decoded"]["portnum"] = "RANGE_TEST_APP"
    packet["decoded"]["text"] = "range test payload"
    packet["channel"] = "abc"

    plugin = MagicMock()
    plugin.plugin_name = "observer"
    plugin.handle_meshtastic_message.return_value = False

    with _patch_message_deps(plugins=[plugin]) as (mock_logger, mock_relay):
        on_meshtastic_message(packet, _make_interface())

    assert mock_relay is not None
    mock_relay.assert_not_called()
    plugin.handle_meshtastic_message.assert_called_once()
    assert mock_logger is not None
    mock_logger.warning.assert_any_call(
        "Invalid channel value %r (type: %s) for promoted %s; "
        "plugins will run, Matrix relay skipped.",
        "abc",
        "str",
        "RANGE_TEST_APP",
    )


# ---------------------------------------------------------------------------
# Tests absorbed from test_meshtastic_utils_edge_cases.py (routing/plugin/DM domain)
# ---------------------------------------------------------------------------


def test_on_meshtastic_message_plugin_timeout_with_dm():
    """Plugin timeout with DM prevents message from being relayed to Matrix."""
    packet = {
        "decoded": {"text": "!test", "portnum": PORTNUM_TEXT_MESSAGE_APP},
        "fromId": "!67890",
        "channel": 0,
        "to": BROADCAST_NUM,
    }
    interface = MagicMock()
    interface.nodes = {"!67890": {"user": {"id": "!67890", "longName": "TestNode"}}}
    interface.myInfo = MagicMock()
    interface.myInfo.my_node_num = 99999

    future = _DummyFuture(ConcurrentTimeoutError("Plugin timeout"))

    with (
        patch(
            "mmrelay.plugin_loader.load_plugins",
            return_value=[_make_plugin_routing("test_plugin")],
        ),
        patch(
            "mmrelay.meshtastic_utils._submit_coro",
            side_effect=_make_submit_side_effect(future),
        ),
        patch(
            "mmrelay.meshtastic_utils.config",
            {
                "meshtastic": {"meshnet_name": "test"},
                "matrix_rooms": [{"meshtastic_channel": 0, "id": "!room:matrix"}],
            },
        ),
        patch(
            "mmrelay.meshtastic_utils.matrix_rooms",
            [{"meshtastic_channel": 0, "id": "!room:matrix"}],
        ),
        patch("mmrelay.meshtastic_utils.get_longname", return_value="TestNode"),
        patch("mmrelay.meshtastic_utils.get_shortname", return_value="TN"),
        patch("mmrelay.matrix_utils.get_matrix_prefix", return_value=""),
        patch("mmrelay.matrix_utils.matrix_relay", Mock()) as mock_matrix_relay,
        patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        on_meshtastic_message(packet, interface)

        mock_logger.warning.assert_any_call(
            "Plugin %s did not respond within %ss: %s",
            "test_plugin",
            DEFAULT_PLUGIN_TIMEOUT_SECS,
            ANY,
        )
        mock_matrix_relay.assert_not_called()


def test_on_meshtastic_message_non_text_plugin_timeout_prevents_relay():
    """Non-text plugin timeout prevents relaying telemetry to Matrix."""
    packet = {
        "decoded": {
            "portnum": "TELEMETRY_APP",
            "telemetry": {
                "deviceMetrics": {
                    "batteryLevel": 85,
                    "voltage": 4.1,
                },
            },
        },
        "fromId": "!12345678",
        "channel": 0,
        "to": BROADCAST_NUM,
    }
    interface = MagicMock()
    interface.nodes = {
        "!12345678": {"user": {"id": "!12345678", "longName": "TestNode"}}
    }

    future = _DummyFuture(ConcurrentTimeoutError("Plugin timeout"))

    with (
        patch(
            "mmrelay.plugin_loader.load_plugins",
            return_value=[_make_plugin_routing("telemetry_plugin", handled=True)],
        ),
        patch(
            "mmrelay.meshtastic_utils._submit_coro",
            side_effect=_make_submit_side_effect(future),
        ),
        patch(
            "mmrelay.meshtastic_utils.config",
            {
                "meshtastic": {"meshnet_name": "test"},
                "matrix_rooms": [{"meshtastic_channel": 0, "id": "!room:matrix"}],
            },
        ),
        patch(
            "mmrelay.meshtastic_utils.matrix_rooms",
            [{"meshtastic_channel": 0, "id": "!room:matrix"}],
        ),
        patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        on_meshtastic_message(packet, interface)

        mock_logger.warning.assert_any_call(
            "Plugin %s did not respond within %ss: %s",
            "telemetry_plugin",
            DEFAULT_PLUGIN_TIMEOUT_SECS,
            ANY,
        )
        mock_logger.debug.assert_any_call(
            "Processed %s with plugin %s", "TELEMETRY_APP", "telemetry_plugin"
        )


def test_on_meshtastic_message_non_text_plugin_no_match_continues():
    """Non-text message continues to subsequent plugins when first does not handle."""
    packet = {
        "decoded": {
            "portnum": "POSITION_APP",
            "position": {
                "latitudeI": 377711000,
                "longitudeI": -1224200000,
            },
        },
        "fromId": "!12345678",
        "channel": 0,
        "to": BROADCAST_NUM,
    }
    interface = MagicMock()
    interface.nodes = {
        "!12345678": {"user": {"id": "!12345678", "longName": "TestNode"}}
    }

    plugin1 = MagicMock()
    plugin1.plugin_name = "first_plugin"
    plugin1.handle_meshtastic_message = Mock(return_value=False)

    plugin2 = MagicMock()
    plugin2.plugin_name = "second_plugin"
    plugin2.handle_meshtastic_message = Mock(return_value=True)

    with (
        patch(
            "mmrelay.plugin_loader.load_plugins",
            return_value=[plugin1, plugin2],
        ),
        patch(
            "mmrelay.meshtastic_utils.config",
            {
                "meshtastic": {"meshnet_name": "test"},
                "matrix_rooms": [{"meshtastic_channel": 0, "id": "!room:matrix"}],
            },
        ),
        patch(
            "mmrelay.meshtastic_utils.matrix_rooms",
            [{"meshtastic_channel": 0, "id": "!room:matrix"}],
        ),
        patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
        patch("mmrelay.matrix_utils.matrix_relay", Mock()) as mock_matrix_relay,
    ):
        on_meshtastic_message(packet, interface)

        plugin1.handle_meshtastic_message.assert_called_once()
        plugin2.handle_meshtastic_message.assert_called_once()
        mock_matrix_relay.assert_not_called()


def test_on_meshtastic_message_non_text_plugin_match_skips_remaining():
    """Non-text message handled by first plugin skips remaining plugins."""
    packet = {
        "decoded": {
            "portnum": "POSITION_APP",
            "position": {
                "latitudeI": 377711000,
                "longitudeI": -1224200000,
            },
        },
        "fromId": "!12345678",
        "channel": 0,
        "to": BROADCAST_NUM,
    }
    interface = MagicMock()
    interface.nodes = {
        "!12345678": {"user": {"id": "!12345678", "longName": "TestNode"}}
    }

    plugin1 = MagicMock()
    plugin1.plugin_name = "position_plugin"
    plugin1.handle_meshtastic_message = Mock(return_value=True)

    plugin2 = MagicMock()
    plugin2.plugin_name = "other_plugin"
    plugin2.handle_meshtastic_message = Mock(return_value=False)

    with (
        patch(
            "mmrelay.plugin_loader.load_plugins",
            return_value=[plugin1, plugin2],
        ),
        patch(
            "mmrelay.meshtastic_utils.config",
            {
                "meshtastic": {"meshnet_name": "test"},
                "matrix_rooms": [{"meshtastic_channel": 0, "id": "!room:matrix"}],
            },
        ),
        patch(
            "mmrelay.meshtastic_utils.matrix_rooms",
            [{"meshtastic_channel": 0, "id": "!room:matrix"}],
        ),
        patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
        patch("mmrelay.matrix_utils.matrix_relay", Mock()) as mock_matrix_relay,
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        on_meshtastic_message(packet, interface)

        plugin1.handle_meshtastic_message.assert_called_once()
        plugin2.handle_meshtastic_message.assert_not_called()
        mock_logger.debug.assert_any_call(
            "Processed %s with plugin %s", "POSITION_APP", "position_plugin"
        )
        mock_matrix_relay.assert_not_called()


def test_on_meshtastic_message_large_node_list():
    """Handles packet when interface has a very large number of nodes.

    This test verifies the function completes without error or timeout when
    processing messages with a large node list in the interface.
    """
    packet = {
        "decoded": {"text": "test message", "portnum": PORTNUM_TEXT_MESSAGE_APP},
        "fromId": "!12345678",
        "channel": 0,
        "to": BROADCAST_NUM,
        "id": 999,
    }

    mock_interface = MagicMock()
    large_nodes = {}
    for i in range(10000):
        large_nodes[f"node_{i}"] = {
            "user": {
                "id": f"!{i:08x}",
                "longName": f"Node {i}",
                "shortName": f"N{i}",
            }
        }
    mock_interface.nodes = large_nodes

    def _done_future(coro, **_kwargs):
        if asyncio.iscoroutine(coro):
            coro.close()
        f = FuturesFuture()
        f.set_result(None)
        return f

    with (
        patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
        patch("mmrelay.meshtastic_utils.logger"),
        patch("mmrelay.meshtastic_utils._submit_coro", side_effect=_done_future),
        patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch("mmrelay.matrix_utils.matrix_relay", Mock(return_value=None)),
    ):
        # Primary assertion: function completes without exception for 10,000 nodes
        on_meshtastic_message(packet, mock_interface)


def _make_plugin_routing(name, handled=False):
    """Create a MagicMock plugin for routing tests."""
    plugin = MagicMock()
    plugin.plugin_name = name
    plugin.handle_meshtastic_message = AsyncMock(return_value=handled)
    return plugin
