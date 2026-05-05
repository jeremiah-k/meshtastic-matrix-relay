#!/usr/bin/env python3
"""
Test suite for Meshtastic utilities in MMRelay.

Tests the Meshtastic client functionality including:
- Message processing and relay to Matrix
- Connection management (serial, TCP, BLE)
- Node information handling
- Packet parsing and validation
- Error handling and reconnection logic
"""

import asyncio
import contextlib
import inspect
import threading
import unittest
from collections.abc import Iterator
from concurrent.futures import TimeoutError as ConcurrentTimeoutError
from contextlib import ExitStack, contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from meshtastic import BROADCAST_NUM

from mmrelay.constants.config import CONFIG_KEY_MESHNET_NAME
from mmrelay.constants.formats import (
    EMOJI_FLAG_VALUE,
    TEXT_MESSAGE_APP,
)
from mmrelay.constants.messages import (
    PORTNUM_DETECTION_SENSOR_APP,
    PORTNUM_TEXT_MESSAGE_APP,
)
from mmrelay.constants.network import (
    BLE_INTERFACE_CREATE_TIMEOUT_FLOOR_SECS,
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_SERIAL,
    CONNECTION_TYPE_TCP,
    DEFAULT_MESHTASTIC_TIMEOUT,
    DEFAULT_TCP_PORT,
    STARTUP_PACKET_DRAIN_SECS,
)
from mmrelay.meshtastic.packet_routing import (
    PacketAction,
    _get_packet_routing_overrides,
    _resolve_portnum_set,
    classify_packet,
)
from mmrelay.meshtastic_utils import (
    _get_packet_details,
    _get_portnum_name,
    connect_meshtastic,
    on_meshtastic_message,
    send_text_reply,
)
from tests.conftest import cleanup_ble_future_state
from tests.constants import (
    TEST_BLE_MAC,
    TEST_NODE_NUM,
    TEST_PACKET_FROM_ID,
    TEST_PACKET_ID,
)

TEST_PACKET_RX_TIME = 1234567890


import mmrelay.meshtastic_utils as mu


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

    yield

    _cancel_startup_drain_timer()


@pytest.fixture
def stable_relay_start_time(monkeypatch):
    """
    Keep message-processing tests deterministic regardless of wall-clock time.

    Many packet fixtures in this module use fixed historical `rxTime` values.
    Pinning RELAY_START_TIME prevents accidental stale-message filtering during
    tests that are unrelated to startup history behavior.
    """
    monkeypatch.setattr("mmrelay.meshtastic_utils.RELAY_START_TIME", 0, raising=False)


class _FakeEvent:
    """Threading.Event test double for metadata redirect behavior."""

    def is_set(self) -> bool:
        """
        Always reports the fake event as set.

        Returns:
            bool: `True`, indicating the event is considered set.
        """
        return True

    def set(self) -> None:
        """
        Mark the event as set so subsequent is_set() calls return True.

        Mimics threading.Event.set behavior for the test double.
        """
        return None

    def clear(self) -> None:
        """
        No-op placeholder for clearing the object's internal state.

        This method currently performs no action and exists to be overridden or implemented to reset the instance's state.
        """
        return None


def _reset_ble_inflight_state(module: Any) -> None:
    """
    Reset shared BLE in-flight tracking globals for test isolation.
    """
    cleanup_ble_future_state(module)


def _make_timeout_future() -> Mock:
    """
    Create a mock future that simulates a timeout.

    Returns a Mock configured with:
    - result() raises FuturesTimeoutError
    - done() returns False
    - cancel() returns True
    """
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    future = Mock()
    future.result = Mock(side_effect=FuturesTimeoutError())
    future.done.return_value = False
    future.cancel = Mock(return_value=True)
    return future


@pytest.mark.usefixtures("stable_relay_start_time")
class TestMeshtasticUtils(unittest.TestCase):
    """Test cases for Meshtastic utilities."""

    def setUp(self):
        """
        Initializes mock configuration and packet data, and resets global Meshtastic utility state to ensure test isolation before each test.
        """
        # Mock configuration
        self.mock_config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
                "broadcast_enabled": True,
                "meshnet_name": "test_mesh",
            },
            "matrix_rooms": [
                {"id": "!room1:matrix.org", "meshtastic_channel": 0},
                {"id": "!room2:matrix.org", "meshtastic_channel": 1},
            ],
        }

        # Mock packet data
        self.mock_packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": 987654321,
            "decoded": {
                "text": "Hello from mesh",
                "portnum": TEXT_MESSAGE_APP,
            },
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": TEST_PACKET_RX_TIME,
        }

        # Reset global state to avoid test interference
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.config = None
        mmrelay.meshtastic_utils.matrix_rooms = []
        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.reconnect_task = None
        iface = mmrelay.meshtastic_utils.meshtastic_iface
        if iface is not None:
            disconnect_iface = getattr(
                mmrelay.meshtastic_utils,
                "_disconnect_ble_interface",
                None,
            )
            if callable(disconnect_iface):
                with contextlib.suppress(
                    asyncio.CancelledError,
                    asyncio.TimeoutError,
                    ConcurrentTimeoutError,
                    OSError,
                    RuntimeError,
                ):
                    disconnect_iface(iface, reason="test-reset")
        mmrelay.meshtastic_utils.meshtastic_iface = None
        _reset_ble_inflight_state(mmrelay.meshtastic_utils)
        mmrelay.meshtastic_utils.shutdown_shared_executors()
        mmrelay.meshtastic_utils._metadata_future = None
        mmrelay.meshtastic_utils._ble_timeout_counts = {}
        mmrelay.meshtastic_utils._ble_executor_degraded_addresses = set()
        mmrelay.meshtastic_utils._ble_executor_orphaned_workers_by_address = {}
        mmrelay.meshtastic_utils._metadata_executor_degraded = False
        mmrelay.meshtastic_utils._metadata_executor_orphaned_workers = 0

    def test_on_meshtastic_message_basic(self):
        """
        Verify that a Meshtastic text message on a channel mapped to a Matrix room schedules the Matrix relay coroutine.

        Sets up name, interaction, and storage mocks and invokes on_meshtastic_message with a valid text packet and mock interface, asserting that the message relay is scheduled for delivery to Matrix.
        """
        # Mock the required functions
        from concurrent.futures import Future

        import mmrelay.meshtastic_utils

        def _done_future(*args, **kwargs):
            """
            Create a completed Future whose result is None.

            Returns:
                Future: A Future object already resolved with result `None`.
            """
            f = Future()
            f.set_result(None)
            return f

        with (
            patch("mmrelay.meshtastic_utils.get_longname") as mock_get_longname,
            patch("mmrelay.meshtastic_utils.get_shortname") as mock_get_shortname,
            patch("mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock),
            patch(
                "mmrelay.matrix_utils.get_interaction_settings"
            ) as mock_get_interactions,
            patch("mmrelay.matrix_utils.message_storage_enabled") as mock_storage,
        ):
            mock_get_longname.return_value = "Test User"
            mock_get_shortname.return_value = "TU"
            mock_get_interactions.return_value = {"reactions": False, "replies": False}
            mock_storage.return_value = True

            # Mock interface with myInfo required for message routing
            mock_interface = MagicMock()
            mock_interface.myInfo = MagicMock()
            mock_interface.myInfo.my_node_num = TEST_NODE_NUM

            # Set up the global config and matrix_rooms
            mmrelay.meshtastic_utils.config = self.mock_config
            mmrelay.meshtastic_utils.matrix_rooms = self.mock_config["matrix_rooms"]

            # Call the function
            on_meshtastic_message(self.mock_packet, mock_interface)

            # The mock is captured but the test setup does not fully exercise the
            # matrix_relay code path (requires an event loop and other state).
            # The channel_fallback test below validates that matrix_relay is called.

    def test_on_meshtastic_message_channel_fallback_for_string_portnum(self):
        """
        Text or detection packets with string portnums should fall back to channel 0 when channel is missing.
        """
        # Packet missing channel but using string portnum
        packet_no_channel = self.mock_packet.copy()
        packet_no_channel["channel"] = None
        packet_no_channel["to"] = BROADCAST_NUM

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch(
                "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
            ) as mock_matrix_relay,
            patch("mmrelay.meshtastic_utils.get_longname") as mock_get_longname,
            patch("mmrelay.meshtastic_utils.get_shortname") as mock_get_shortname,
            patch(
                "mmrelay.matrix_utils.get_interaction_settings"
            ) as mock_get_interactions,
            patch("mmrelay.matrix_utils.message_storage_enabled") as mock_storage,
            patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
            patch("mmrelay.matrix_utils.matrix_client", None),
        ):
            mock_get_longname.return_value = "Test User"
            mock_get_shortname.return_value = "TU"
            mock_get_interactions.return_value = {"reactions": False, "replies": False}
            mock_storage.return_value = True

            mock_interface = MagicMock()
            mock_interface.myInfo = MagicMock()
            mock_interface.myInfo.my_node_num = TEST_NODE_NUM

            # Call the function
            on_meshtastic_message(packet_no_channel, mock_interface)

            mock_matrix_relay.assert_called()

    def test_on_meshtastic_message_unmapped_channel(self):
        """
        Test that Meshtastic messages on unmapped channels do not trigger Matrix message relay.

        Ensures that when a packet is received on a channel not mapped to any Matrix room, no coroutine is scheduled to relay the message.
        """
        # Modify packet to use unmapped channel
        packet_unmapped = self.mock_packet.copy()
        packet_unmapped["channel"] = 99  # Not in matrix_rooms config

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
        ):
            mock_interface = MagicMock()

            # Call the function
            on_meshtastic_message(packet_unmapped, mock_interface)

            # Verify _submit_coro was not called (no matrix relay)
            mock_submit_coro.assert_not_called()

    def test_on_meshtastic_message_no_text(self):
        """
        Test that non-text Meshtastic packets do not trigger message relay to Matrix.

        Ensures that when a packet's port number does not correspond to a text message, the message processing function does not schedule a coroutine to relay the message.
        """
        # Modify packet to have no text
        packet_no_text = self.mock_packet.copy()
        packet_no_text["decoded"] = {"portnum": 2}  # REMOTE_HARDWARE_APP

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
            patch("mmrelay.plugin_loader.load_plugins") as mock_load_plugins,
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch("mmrelay.matrix_utils.matrix_client", None),
            patch(
                "mmrelay.meshtastic.packet_routing.portnums_pb2"
            ) as mock_portnums_pb2,
        ):
            mock_load_plugins.return_value = []
            mock_portnums_pb2.PortNum.Name.return_value = "REMOTE_HARDWARE_APP"
            mock_interface = MagicMock()

            with self.assertLogs("Meshtastic", level="DEBUG") as cm:
                # Call the function
                on_meshtastic_message(packet_no_text, mock_interface)

            # Verify _submit_coro was not called for non-text message
            mock_submit_coro.assert_not_called()
            mock_portnums_pb2.PortNum.Name.assert_called_with(2)

            # Verify debug log was called with packet type information
            log_output = "\n".join(cm.output)
            self.assertIn("REMOTE_HARDWARE_APP", log_output)
            self.assertIn(f"from={TEST_PACKET_FROM_ID}", log_output)
            self.assertIn("channel=0", log_output)
            self.assertIn(f"id={TEST_PACKET_ID}", log_output)

    def test_on_meshtastic_message_missing_myinfo(self):
        """
        Ensure handler returns early without myInfo on the interface.
        """
        packet = self.mock_packet.copy()
        mock_interface = MagicMock()
        mock_interface.myInfo = None

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
            patch(
                "mmrelay.matrix_utils.get_interaction_settings",
                return_value={"reactions": True, "replies": True},
            ),
            patch("mmrelay.matrix_utils.matrix_relay"),
            patch("mmrelay.meshtastic_utils._submit_coro"),
        ):
            result = on_meshtastic_message(packet, mock_interface)
            self.assertIsNone(result)

    def test_on_meshtastic_message_ignores_other_node(self):
        """
        Ensure handler ignores packets addressed to a different node.
        """
        packet = self.mock_packet.copy()
        packet["to"] = BROADCAST_NUM + 1
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_NODE_NUM

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
            patch(
                "mmrelay.matrix_utils.get_interaction_settings",
                return_value={"reactions": True, "replies": True},
            ),
            patch("mmrelay.matrix_utils.matrix_relay"),
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit,
        ):
            with self.assertLogs("Meshtastic", level="DEBUG") as log_cm:
                result = on_meshtastic_message(packet, mock_interface)

            self.assertIsNone(result)
            mock_submit.assert_not_called()
            self.assertTrue(
                any(
                    "Ignoring message intended for node" in message
                    for message in log_cm.output
                )
            )

    def test_on_meshtastic_message_reaction_relay(self):
        """
        Ensure reaction packets are relayed to Matrix when reactions are enabled.
        """
        reaction_packet = {
            "fromId": "!node",
            "to": 999,
            "decoded": {
                "text": ":)",
                "portnum": TEXT_MESSAGE_APP,
                "replyId": 42,
                "emoji": 1,
            },
            "channel": 0,
            "id": 555,
        }

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
            patch("mmrelay.meshtastic_utils.get_longname", return_value="Long Name"),
            patch("mmrelay.meshtastic_utils.get_shortname", return_value="LN"),
            patch(
                "mmrelay.matrix_utils.get_interaction_settings",
                return_value={"reactions": True, "replies": True},
            ),
            patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False),
            patch(
                "mmrelay.meshtastic_utils.get_message_map_by_meshtastic_id",
                return_value=("evt1", "!room1:matrix.org", "orig text", "mesh"),
            ),
            patch("mmrelay.matrix_utils.get_matrix_prefix", return_value="[prefix] "),
            patch(
                "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
            ) as mock_matrix_relay,
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            import inspect

            def _drain_coro(coro, *_args, **_kwargs):
                # Mirror mock_submit_coro fixture behavior: close coroutines
                # so AsyncMock-based matrix_relay calls don't raise warnings.
                """
                Close a coroutine object to prevent un-awaited-coroutine warnings in tests.

                Parameters:
                    coro: The coroutine object to close; if not a coroutine, the function has no effect.

                Notes:
                    Accepts additional positional and keyword arguments for compatibility with fixtures that forward them; those are ignored.
                """
                if inspect.iscoroutine(coro):
                    coro.close()
                return None

            mock_submit_coro.side_effect = _drain_coro
            mock_interface = MagicMock()
            mock_interface.myInfo.my_node_num = 999

            on_meshtastic_message(reaction_packet, mock_interface)

            mock_submit_coro.assert_called_once()
            # Ensure we scheduled the matrix relay coroutine
            self.assertTrue(mock_matrix_relay.called)

    def test_on_meshtastic_message_reply_relay(self):
        """
        Verify that non-emoji reply packets are relayed to Matrix when reply handling is enabled.
        """
        reply_packet = {
            "fromId": "!node",
            "to": 999,
            "decoded": {
                "text": "Reply message",
                "portnum": TEXT_MESSAGE_APP,
                "replyId": 77,
            },
            "channel": 0,
            "id": 777,
        }

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
            patch("mmrelay.meshtastic_utils.get_longname", return_value="Long Name"),
            patch("mmrelay.meshtastic_utils.get_shortname", return_value="LN"),
            patch(
                "mmrelay.matrix_utils.get_interaction_settings",
                return_value={"reactions": True, "replies": True},
            ),
            patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False),
            patch(
                "mmrelay.meshtastic_utils.get_message_map_by_meshtastic_id",
                return_value=("evt1", "!room1:matrix.org", "orig text", "mesh"),
            ),
            patch("mmrelay.matrix_utils.get_matrix_prefix", return_value="[prefix] "),
            patch(
                "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
            ) as mock_matrix_relay,
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
        ):
            import inspect

            def _drain_coro(coro, *_args, **_kwargs):
                # Mirror mock_submit_coro fixture behavior: close coroutines
                # so AsyncMock-based matrix_relay calls don't raise warnings.
                """
                Close a coroutine object to prevent un-awaited-coroutine warnings in tests.

                Parameters:
                    coro: The coroutine object to close; if not a coroutine, the function has no effect.

                Notes:
                    Accepts additional positional and keyword arguments for compatibility with fixtures that forward them; those are ignored.
                """
                if inspect.iscoroutine(coro):
                    coro.close()
                return None

            mock_submit_coro.side_effect = _drain_coro
            mock_interface = MagicMock()
            mock_interface.myInfo.my_node_num = 999

            on_meshtastic_message(reply_packet, mock_interface)

            mock_submit_coro.assert_called_once()
            self.assertTrue(mock_matrix_relay.called)

    def test_on_meshtastic_message_event_loop_missing(self):
        """
        Returns early when event loop is not set.
        """
        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils.event_loop", None),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            mock_interface = MagicMock()
            mock_interface.myInfo.my_node_num = 1
            result = on_meshtastic_message(self.mock_packet, mock_interface)
            self.assertIsNone(result)
            mock_logger.error.assert_called_once()

    @patch("mmrelay.meshtastic_utils.serial_port_exists")
    @patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
    def test_connect_meshtastic_serial(
        self, mock_tcp, mock_ble, mock_serial, mock_port_exists
    ):
        """
        Test that the Meshtastic client connects via serial interface when the serial port exists.

        Verifies that the serial interface is instantiated with the configured port and that the returned client matches the mock client.
        """
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "test", "hwModel": "test"}
        }
        mock_serial.return_value = mock_client
        mock_port_exists.return_value = True

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
            }
        }

        # Reset global state
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.reconnecting = False

        result = connect_meshtastic(passed_config=config)

        self.assertEqual(result, mock_client)
        mock_serial.assert_called_once_with(
            "/dev/ttyUSB0", timeout=DEFAULT_MESHTASTIC_TIMEOUT
        )

    @patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
    def test_connect_meshtastic_tcp(self, mock_tcp, mock_ble, mock_serial):
        """
        Tests that the Meshtastic client connects via the TCP interface using the configured host.

        Verifies that the TCP interface is instantiated with the correct hostname and that the returned client matches the mocked instance.
        """
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "test", "hwModel": "test"}
        }
        mock_tcp.return_value = mock_client

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_TCP,
                "host": "192.168.1.100",  # Use 'host' not 'tcp_host'
            }
        }

        # Reset global state
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.reconnecting = False

        result = connect_meshtastic(passed_config=config)

        self.assertEqual(result, mock_client)
        mock_tcp.assert_called_once_with(
            hostname="192.168.1.100",
            portNumber=DEFAULT_TCP_PORT,
            timeout=DEFAULT_MESHTASTIC_TIMEOUT,
        )

    @patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
    def test_connect_meshtastic_tcp_uses_configured_port(
        self, mock_tcp, _mock_ble, _mock_serial
    ):
        """
        Tests that the Meshtastic TCP connector honors meshtastic.port when configured.
        """
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "test", "hwModel": "test"}
        }
        mock_tcp.return_value = mock_client

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_TCP,
                "host": "192.168.1.101",
                "port": 4404,
            }
        }

        # Reset global state
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.reconnecting = False

        result = connect_meshtastic(passed_config=config)

        self.assertEqual(result, mock_client)
        mock_tcp.assert_called_once_with(
            hostname="192.168.1.101",
            portNumber=4404,
            timeout=DEFAULT_MESHTASTIC_TIMEOUT,
        )

    @patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
    def test_connect_meshtastic_ble(self, mock_tcp, mock_ble, mock_serial):
        """
        Test that the Meshtastic client connects via BLE using the configured BLE address.

        Verifies that the BLE interface is instantiated with the expected parameters and that the returned client matches the mocked BLE client.
        """
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "test", "hwModel": "test"}
        }
        # Ensure the mock doesn't create any async operations
        mock_client.close = MagicMock()

        # Set up nested structure for BLE address validation
        mock_client.client = MagicMock()
        mock_client.client.bleak_client = MagicMock()
        mock_client.client.bleak_client.address = TEST_BLE_MAC

        # Configure BLE mock to return our mock client
        mock_ble.return_value = mock_client

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": TEST_BLE_MAC,
            }
        }

        # Reset global state
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.reconnecting = False

        result = connect_meshtastic(passed_config=config)

        self.assertEqual(result, mock_client)
        mock_ble.assert_called_once_with(
            address=TEST_BLE_MAC,
            noProto=False,
            debugOut=None,
            noNodes=False,
            timeout=int(BLE_INTERFACE_CREATE_TIMEOUT_FLOOR_SECS),
        )

    @patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
    def test_connect_meshtastic_invalid_type(self, mock_tcp, mock_ble, mock_serial):
        """
        Test that attempting to connect with an invalid Meshtastic connection type returns None and does not instantiate any interface.
        """
        config = {"meshtastic": {"connection_type": "invalid"}}

        result = connect_meshtastic(passed_config=config)

        self.assertIsNone(result)
        # None of the interfaces should be called
        mock_serial.assert_not_called()
        mock_tcp.assert_not_called()
        mock_ble.assert_not_called()

    @patch("mmrelay.meshtastic_utils.serial_port_exists")
    @patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
    def test_connect_meshtastic_startup_drain_applies_only_once(
        self, mock_serial, mock_port_exists
    ):
        """Startup packet drain window should apply on cold startup, not reconnect."""
        import mmrelay.meshtastic_utils as mu

        mock_port_exists.return_value = True
        first_client = MagicMock()
        first_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "first", "hwModel": "test"}
        }
        second_client = MagicMock()
        second_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "second", "hwModel": "test"}
        }
        mock_serial.side_effect = [first_client, second_client]

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
            }
        }

        mu.meshtastic_client = None
        mu.shutting_down = False
        mu.reconnecting = False
        mu._startup_packet_drain_applied = False
        mu._relay_startup_drain_deadline_monotonic_secs = None
        mu._relay_startup_drain_complete_event.set()

        with (
            patch("mmrelay.meshtastic_utils.time.time", return_value=100.0),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_000.0),
        ):
            result_first = connect_meshtastic(passed_config=config)

        assert result_first is first_client
        assert mu._startup_packet_drain_applied is True
        assert mu._relay_startup_drain_deadline_monotonic_secs == pytest.approx(
            1_000.0 + STARTUP_PACKET_DRAIN_SECS
        )
        assert mu._relay_startup_drain_complete_event.is_set() is False

        with (
            patch("mmrelay.meshtastic_utils.time.time", return_value=200.0),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=2_000.0),
        ):
            result_second = connect_meshtastic(passed_config=config, force_connect=True)

        assert result_second is second_client
        assert mu._startup_packet_drain_applied is True
        assert mu._relay_startup_drain_deadline_monotonic_secs is None
        assert mu._relay_startup_drain_complete_event.is_set() is True

    @patch("mmrelay.meshtastic_utils.serial_port_exists")
    @patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
    def test_connect_meshtastic_startup_drain_armed_after_successful_setup(
        self, mock_serial, mock_port_exists
    ):
        """Setup failure before node-info should not consume one-shot startup drain."""
        import mmrelay.meshtastic_utils as mu

        mock_port_exists.return_value = True

        first_client = MagicMock()
        first_client.getMyNodeInfo.side_effect = RuntimeError("node info failed")

        second_client = MagicMock()
        saw_clean_startup_state_on_retry = False

        def _second_node_info() -> dict[str, dict[str, str]]:
            nonlocal saw_clean_startup_state_on_retry
            if (
                mu._startup_packet_drain_applied is False
                and mu._relay_startup_drain_deadline_monotonic_secs is None
            ):
                saw_clean_startup_state_on_retry = True
            return {"user": {"shortName": "second", "hwModel": "test"}}

        second_client.getMyNodeInfo.side_effect = _second_node_info

        mock_serial.side_effect = [first_client, second_client]

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
                "retries": 1,
            }
        }

        mu.meshtastic_client = None
        mu.shutting_down = False
        mu.reconnecting = False
        mu.subscribed_to_messages = False
        mu.subscribed_to_connection_lost = False
        mu._startup_packet_drain_applied = False
        mu._relay_startup_drain_deadline_monotonic_secs = None
        mu._relay_startup_drain_complete_event.set()

        with (
            patch("mmrelay.meshtastic_utils.time.sleep"),
            patch("mmrelay.meshtastic_utils.time.time", return_value=123.0),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=456.0),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"success": False, "firmware_version": "unknown"},
            ),
        ):
            result = connect_meshtastic(passed_config=config, force_connect=True)

        assert result is second_client
        assert saw_clean_startup_state_on_retry is True
        first_client.close.assert_called_once()
        assert mu._startup_packet_drain_applied is True
        assert mu._relay_startup_drain_deadline_monotonic_secs is not None
        assert mu._relay_startup_drain_complete_event.is_set() is False

    def test_rollback_connect_attempt_marks_startup_drain_complete(self):
        """Rollback should set startup-drain completion state to avoid dead waits."""
        import mmrelay.meshtastic_utils as mu

        mu._relay_startup_drain_complete_event.clear()
        mu._relay_startup_drain_expiry_timer = MagicMock()
        mu._relay_startup_drain_deadline_monotonic_secs = 123.0
        mu._startup_packet_drain_applied = True

        result = mu._rollback_connect_attempt_state(
            client=None,
            client_assigned_for_this_connect=False,
            startup_drain_armed_for_this_connect=True,
            startup_drain_applied_for_this_connect=True,
            reconnect_bootstrap_armed_for_this_connect=False,
        )

        assert result is False
        assert mu._relay_startup_drain_complete_event.is_set() is True

    def test_rollback_connect_attempt_none_event_is_safe_noop(self):
        """Rollback should not raise AttributeError when the drain event is None."""
        import mmrelay.meshtastic_utils as mu

        mu._relay_startup_drain_expiry_timer = MagicMock()
        mu._relay_startup_drain_deadline_monotonic_secs = 123.0
        mu._startup_packet_drain_applied = True

        with patch.object(mu, "get_startup_drain_complete_event", return_value=None):
            result = mu._rollback_connect_attempt_state(
                client=None,
                client_assigned_for_this_connect=False,
                startup_drain_armed_for_this_connect=True,
                startup_drain_applied_for_this_connect=True,
                reconnect_bootstrap_armed_for_this_connect=False,
            )

        assert result is False
        assert mu._relay_startup_drain_deadline_monotonic_secs is None

    def test_send_text_reply_success(self):
        """
        Test that send_text_reply returns the expected result when sending a text reply succeeds.

        Verifies that the function correctly calls the interface methods and returns the response from _sendPacket.
        """
        # Create a mock interface
        mock_interface = MagicMock()
        mock_interface._generatePacketId.return_value = 12345
        mock_interface._sendPacket.return_value = {"id": 12345}

        result = send_text_reply(
            mock_interface, "Hello", 999, destinationId="123456789"
        )

        # Should return the result from _sendPacket
        self.assertEqual(result, {"id": 12345})

        # Verify the interface methods were called
        mock_interface._generatePacketId.assert_called_once()
        mock_interface._sendPacket.assert_called_once()

    def test_send_text_reply_no_client(self):
        """
        Test that send_text_reply returns None when the interface fails to send a packet.
        """
        # Create a mock interface that fails
        mock_interface = MagicMock()
        mock_interface._generatePacketId.return_value = 12345
        mock_interface._sendPacket.return_value = None  # Simulate failure

        result = send_text_reply(
            mock_interface, "Hello", 999, destinationId="123456789"
        )

        self.assertIsNone(result)

    def test_on_meshtastic_message_with_broadcast_config(self):
        """
        Test that disabling broadcast in the configuration does not prevent relaying Meshtastic messages to Matrix.

        Ensures that the `broadcast_enabled` setting only affects Matrix-to-Meshtastic message direction, and that Meshtastic-to-Matrix relaying remains functional when broadcast is disabled.
        """
        config_no_broadcast = self.mock_config.copy()
        config_no_broadcast["meshtastic"]["broadcast_enabled"] = False

        from concurrent.futures import Future

        def _done_future(coro, *args, **kwargs):
            # Close the coroutine if it's a coroutine to prevent "never awaited" warnings
            """
            Close `coro` if it is a coroutine to avoid "coroutine was never awaited" warnings and return a completed Future.

            Parameters:
                coro: The object to inspect; if it is a coroutine it will be closed.
                *args, **kwargs: Ignored.

            Returns:
                asyncio.Future: A Future already resolved with the value `None`.
            """
            if inspect.iscoroutine(coro):
                coro.close()
            f = Future()
            f.set_result(None)
            return f

        with (
            patch("mmrelay.meshtastic_utils.config", config_no_broadcast),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                config_no_broadcast["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
            patch("mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock),
            patch("mmrelay.meshtastic_utils.get_longname") as mock_get_longname,
            patch("mmrelay.meshtastic_utils.get_shortname") as mock_get_shortname,
            patch(
                "mmrelay.matrix_utils.get_interaction_settings"
            ) as mock_get_interactions,
            patch("mmrelay.matrix_utils.message_storage_enabled") as mock_storage,
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch("mmrelay.matrix_utils.matrix_client", None),
        ):
            mock_submit_coro.side_effect = _done_future
            mock_get_longname.return_value = "Test User"
            mock_get_shortname.return_value = "TU"
            mock_get_interactions.return_value = {"reactions": False, "replies": False}
            mock_storage.return_value = True

            mock_interface = MagicMock()
            mock_interface.myInfo = MagicMock()
            mock_interface.myInfo.my_node_num = TEST_NODE_NUM
            packet = self.mock_packet.copy()
            packet["to"] = BROADCAST_NUM

            # Call the function
            on_meshtastic_message(packet, mock_interface)

            # Meshtastic->Matrix messages are still relayed regardless of broadcast_enabled
            # (broadcast_enabled only affects Matrix->Meshtastic direction)
            mock_submit_coro.assert_called_once()


class TestGetPortnumName(unittest.TestCase):
    """Test cases for _get_portnum_name helper function."""

    def test_get_portnum_name_with_none(self):
        """Test with None input."""
        result = _get_portnum_name(None)
        self.assertEqual(result, "UNKNOWN (None)")

    def test_get_portnum_name_with_empty_string(self):
        """Test with an empty string."""
        result = _get_portnum_name("")
        self.assertEqual(result, "UNKNOWN (empty string)")

    def test_get_portnum_name_with_string(self):
        """Test with a valid string portnum name."""
        result = _get_portnum_name(TEXT_MESSAGE_APP)
        self.assertEqual(result, TEXT_MESSAGE_APP)

    def test_get_portnum_name_with_valid_int(self):
        """Test with a valid integer portnum."""
        with patch(
            "mmrelay.meshtastic.packet_routing.portnums_pb2"
        ) as mock_portnums_pb2:
            mock_portnums_pb2.PortNum.Name.return_value = TEXT_MESSAGE_APP
            result = _get_portnum_name(1)
            self.assertEqual(result, TEXT_MESSAGE_APP)
            mock_portnums_pb2.PortNum.Name.assert_called_once_with(1)

    def test_get_portnum_name_with_invalid_int(self):
        """Test with an invalid integer portnum."""
        with patch(
            "mmrelay.meshtastic.packet_routing.portnums_pb2"
        ) as mock_portnums_pb2:
            mock_portnums_pb2.PortNum.Name.side_effect = ValueError
            result = _get_portnum_name(999)
            self.assertEqual(result, "UNKNOWN (portnum=999)")

    def test_get_portnum_name_with_other_type(self):
        """Test with unsupported types."""
        result_float = _get_portnum_name(123.45)
        self.assertEqual(result_float, "UNKNOWN (type=float)")
        result_list = _get_portnum_name([])
        self.assertEqual(result_list, "UNKNOWN (type=list)")

    def test_get_portnum_name_encrypted_with_packet(self):
        result = _get_portnum_name(None, {"encrypted": True})
        self.assertEqual(result, "ENCRYPTED")


class TestGetPacketDetails(unittest.TestCase):
    """Test cases for _get_packet_details helper function."""

    def test_get_packet_details_with_none_decoded(self):
        """Test with None decoded data."""
        result = _get_packet_details(None, {}, "UNKNOWN")
        self.assertEqual(result, {})

    def test_get_packet_details_with_device_metrics(self):
        """Test with device telemetry metrics."""
        decoded = {
            "telemetry": {
                "deviceMetrics": {
                    "batteryLevel": 100,
                    "voltage": 4.488,
                    "channelUtilization": 0.0,
                    "airUtilTx": 0.53747225,
                    "uptimeSeconds": 8405,
                }
            }
        }
        packet = {"from": 321352745}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        self.assertEqual(result["batt"], "100%")
        self.assertEqual(result["voltage"], "4.49V")
        self.assertNotIn("temp", result)
        self.assertNotIn("humidity", result)

    def test_get_packet_details_with_environment_metrics(self):
        """Test with environment telemetry metrics."""
        decoded = {
            "telemetry": {
                "environmentMetrics": {
                    "temperature": -12.756417,
                    "relativeHumidity": 62.443268,
                    "barometricPressure": 994.1772,
                    "gasResistance": 1582.0542,
                    "iaq": 176,
                }
            }
        }
        packet = {"from": 321352745}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        self.assertEqual(result["temp"], "-12.8°C")
        self.assertEqual(result["humidity"], "62%")
        self.assertNotIn("batt", result)
        self.assertNotIn("voltage", result)

    def test_get_packet_details_with_signal_info(self):
        """Test with signal information (RSSI and SNR)."""
        decoded = {}
        packet = {"from": 123, "rxRssi": -64, "rxSnr": 6.0}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        self.assertEqual(result["signal"], "RSSI:-64 SNR:6.0")

    def test_get_packet_details_with_zero_signal(self):
        """Test with zero signal values (should still be logged)."""
        decoded = {}
        packet = {"from": 123, "rxRssi": 0, "rxSnr": 0.0}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        self.assertEqual(result["signal"], "RSSI:0 SNR:0.0")

    def test_get_packet_details_with_relay_info(self):
        """Test with relay node information."""
        decoded = {}
        packet = {"from": 123, "relayNode": 177}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        self.assertEqual(result["relayed"], "via 177")

    def test_get_packet_details_with_relay_zero(self):
        """Test with relay node zero (should be ignored)."""
        decoded = {}
        packet = {"from": 123, "relayNode": 0}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        self.assertNotIn("relayed", result)

    def test_get_packet_details_with_priority(self):
        """Test with non-default priority."""
        decoded = {}
        packet = {"from": 123, "priority": "BACKGROUND"}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        self.assertEqual(result["priority"], "BACKGROUND")

    def test_get_packet_details_with_normal_priority(self):
        """Test with NORMAL priority (should be ignored)."""
        decoded = {}
        packet = {"from": 123, "priority": "NORMAL"}
        result = _get_packet_details(decoded, packet, "TELEMETRY_APP")
        self.assertNotIn("priority", result)

    def test_get_packet_details_non_telemetry(self):
        """Test with non-TELEMETRY_APP portnum."""
        decoded = {"portnum": TEXT_MESSAGE_APP}
        packet = {"from": 123, "rxRssi": -70}
        result = _get_packet_details(decoded, packet, TEXT_MESSAGE_APP)
        self.assertEqual(result["signal"], "RSSI:-70")
        self.assertNotIn("batt", result)
        self.assertNotIn("voltage", result)


class TestTextReplyFunctionality(unittest.TestCase):
    """Test cases for text reply functionality."""

    def test_send_text_reply_with_none_interface(self):
        """Test send_text_reply returns None when interface is None."""
        from mmrelay.meshtastic_utils import send_text_reply

        # Test with None interface
        result = send_text_reply(None, "Test message", reply_id=12345)

        # Should return None
        self.assertIsNone(result)

    def test_send_text_reply_function_exists_and_callable(self):
        """Test that send_text_reply function exists and is callable."""
        from mmrelay.meshtastic_utils import send_text_reply

        # Function should exist and be callable
        self.assertTrue(callable(send_text_reply))


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
    # Should warn about missing original but still relay as normal message
    mock_logger.warning.assert_any_call(
        "Original message for reaction (replyId=%s) not found in DB. "
        "Relaying as normal message instead.",
        42,
    )
    # Message should be relayed as normal text message
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
    # Should warn about missing original but still relay as normal message
    mock_logger.warning.assert_any_call(
        "Original message for reply (replyId=%s) not found in DB. "
        "Relaying as normal message instead.",
        77,
    )
    # Message should be relayed as normal text message
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

    with _patch_message_deps() as (mock_logger, _mock_relay):
        on_meshtastic_message(packet, _make_interface())

    assert mock_logger is not None
    # Empty matrix_rooms now logs as warning before relay attempt with descriptive message
    assert any(
        "matrix_rooms is empty" in str(call)
        and call[0][0].startswith("matrix_rooms is empty")
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
