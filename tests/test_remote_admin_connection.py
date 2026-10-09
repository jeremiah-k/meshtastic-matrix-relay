#!/usr/bin/env python3
"""
Tests for the command-scoped admin connection wrapper.

Covers the bounded deadline polling seam, remote-node scoping of getNode
and sends (including the channel-fetch retry loop), ACK/NAK request
registration and bounded waits, and command teardown retiring waits
without closing the shared transport.
"""

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from mmrelay.remote_admin_connection import (
    _AdminConnection,
    _DeadlineTimeout,
)
from tests.remote_admin_test_support import (
    REMOTE_NODE_ID,
    REMOTE_NODE_NUM,
)
from tests.remote_admin_test_support import real_mtjk as real_mtjk


class _MeshInterfaceError(Exception):
    pass


def _fake_interface() -> MagicMock:
    interface = MagicMock()
    interface.MeshInterfaceError = _MeshInterfaceError
    return interface


def _connection(**kwargs: Any) -> _AdminConnection:
    interface = kwargs.pop("interface", None) or _fake_interface()
    return _AdminConnection(
        interface,
        kwargs.pop("destination", REMOTE_NODE_NUM),
        kwargs.pop("timeout", 5.0),
    )


# --- deadline polling seam ---------------------------------------------------


def test_deadline_timeout_reset_bounds_wait_by_deadline() -> None:
    timeout = _DeadlineTimeout(deadline=time.monotonic() + 100.0, timeout=5.0)
    assert timeout.expireTimeout == 5.0
    timeout.reset()
    assert timeout._wait_deadline <= time.monotonic() + 100.0
    assert timeout.expireTime > 0


def test_deadline_timeout_reset_accepts_new_duration() -> None:
    timeout = _DeadlineTimeout(deadline=time.monotonic() + 100.0, timeout=5.0)
    timeout.reset(1.0)
    assert timeout.expireTimeout == 5.0
    assert timeout._wait_deadline <= time.monotonic() + 100.0


def test_wait_for_set_returns_true_when_attribute_sets() -> None:
    timeout = _DeadlineTimeout(deadline=time.monotonic() + 100.0, timeout=5.0)
    target = SimpleNamespace(receivedNak=True)
    assert timeout.waitForSet(target, ("receivedNak",)) is True


def test_wait_for_set_returns_false_on_deadline() -> None:
    timeout = _DeadlineTimeout(deadline=time.monotonic() + 100.0, timeout=5.0)
    timeout.sleepInterval = 0.01
    target = SimpleNamespace()
    assert timeout.waitForSet(target, ("receivedNak",)) is False


def test_wait_for_set_polls_until_attribute_appears() -> None:
    timeout = _DeadlineTimeout(deadline=time.monotonic() + 100.0, timeout=5.0)
    timeout.sleepInterval = 0.01
    target = SimpleNamespace(receivedNak=False)

    def set_later() -> None:
        target.receivedNak = True

    import threading

    timer = threading.Timer(0.02, set_later)
    timer.start()
    try:
        assert timeout.waitForSet(target, ("receivedNak",)) is True
    finally:
        timer.join()


# --- getNode scoping and channel fetch ----------------------------------------


def test_get_node_refuses_other_node() -> None:
    connection = _connection()
    with pytest.raises(ValueError, match="another node"):
        connection.getNode("!00000001")


def _patch_node_view(monkeypatch: pytest.MonkeyPatch, view: Any) -> None:
    import meshtastic.mesh_interface_runtime.node_view as node_view_module
    import meshtastic.mesh_interface_runtime.ports as ports_module

    monkeypatch.setattr(node_view_module, "NodeView", lambda port: view)
    monkeypatch.setattr(ports_module, "_NodeViewPort", lambda interface: None)


def _fake_node(wait_for_config: list[bool]) -> Any:
    node = MagicMock()
    node.partialChannels = []
    node.waitForConfig.side_effect = wait_for_config
    node.requestChannels.return_value = None
    return node


def test_get_node_binds_deadline_and_response_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection(timeout=30.0)
    node = _fake_node([])
    view = SimpleNamespace(get_node=lambda node_id, flag, **kwargs: node)
    _patch_node_view(monkeypatch, view)

    returned = connection.getNode(REMOTE_NODE_ID, False, timeout=30.0)
    assert returned is node
    assert isinstance(node._timeout, _DeadlineTimeout)


def test_get_node_wraps_response_wait_within_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection(timeout=30.0)
    node = _fake_node([])
    view = SimpleNamespace(get_node=lambda node_id, flag, **kwargs: node)
    _patch_node_view(monkeypatch, view)
    captured: dict[str, Any] = {}

    def underlying(*args: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "response"

    monkeypatch.setattr(node, "_request_admin_response", underlying, raising=False)

    result = connection.getNode(REMOTE_NODE_ID, False, timeout=30.0)
    assert result is node
    response = result._request_admin_response("op", response_timeout_seconds=999.0)
    assert response == "response"
    assert captured["response_timeout_seconds"] <= 30.0
    assert captured["response_deadline"] == connection._deadline


def test_get_node_request_channels_success_breaks_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    node = _fake_node([True])
    view = SimpleNamespace(get_node=lambda node_id, flag, **kwargs: node)
    _patch_node_view(monkeypatch, view)

    result = connection.getNode(REMOTE_NODE_ID, True, requestChannelAttempts=3)
    assert result is node
    node.requestChannels.assert_called_once_with()


def test_get_node_request_channels_retries_with_partial_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    node = _fake_node([False, True])
    node.partialChannels = [{"channel": 1}]
    view = SimpleNamespace(get_node=lambda node_id, flag, **kwargs: node)
    _patch_node_view(monkeypatch, view)

    result = connection.getNode(REMOTE_NODE_ID, True, requestChannelAttempts=3)
    assert result is node
    assert node.requestChannels.call_args_list[-1].kwargs["startingIndex"] == 1


def test_get_node_request_channels_gives_up_after_last_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    node = _fake_node([False])
    view = SimpleNamespace(get_node=lambda node_id, flag, **kwargs: node)
    _patch_node_view(monkeypatch, view)

    with pytest.raises(_MeshInterfaceError, match="Timed out waiting for channels"):
        connection.getNode(REMOTE_NODE_ID, True, requestChannelAttempts=1)


def test_get_node_request_channels_without_attempts_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    node = _fake_node([])
    view = SimpleNamespace(get_node=lambda node_id, flag, **kwargs: node)
    _patch_node_view(monkeypatch, view)

    with pytest.raises(_MeshInterfaceError, match="Timed out waiting for channels"):
        connection.getNode(REMOTE_NODE_ID, True, requestChannelAttempts=0)


# --- send scoping and ACK waits -----------------------------------------------


def test_send_data_refuses_after_cleanup() -> None:
    connection = _connection()
    connection._cleanup()
    with pytest.raises(RuntimeError, match="has completed"):
        connection._send_data_with_wait("data", REMOTE_NODE_ID)


def test_send_data_refuses_other_node() -> None:
    connection = _connection()
    with pytest.raises(ValueError, match="another node"):
        connection._send_data_with_wait("data", "!00000001")


def test_send_data_registers_nak_and_response_requests() -> None:
    interface = _fake_interface()
    interface._send_data_with_wait.return_value = SimpleNamespace(id=7)
    connection = _AdminConnection(interface, REMOTE_NODE_NUM, 5.0)

    connection._send_data_with_wait(
        "data",
        REMOTE_NODE_ID,
        response_wait_attr="receivedNak",
        wantResponse=True,
    )
    assert connection._requests == {7}
    assert connection._response_requests == {7}
    interface._retire_wait_request.assert_not_called()


def test_send_data_without_special_kwargs_registers_nothing() -> None:
    interface = _fake_interface()
    interface._send_data_with_wait.return_value = SimpleNamespace(id=8)
    connection = _AdminConnection(interface, REMOTE_NODE_NUM, 5.0)

    connection._send_data_with_wait("data", REMOTE_NODE_ID)
    assert connection._requests == set()
    assert connection._response_requests == set()


def test_send_data_scopes_ack_for_admin_writes() -> None:
    interface = _fake_interface()
    interface._send_data_with_wait.return_value = SimpleNamespace(id=9)
    connection = _AdminConnection(interface, REMOTE_NODE_NUM, 5.0)

    connection.sendData("data", REMOTE_NODE_ID, portNum=1, wantAck=True)
    kwargs = interface._send_data_with_wait.call_args.kwargs
    assert kwargs["portNum"] == 1
    assert kwargs["response_wait_attr"] == "receivedNak"
    assert connection._requests == {9}


def test_wait_for_ack_nak_returns_when_request_not_active() -> None:
    interface = _fake_interface()
    interface._has_active_wait_request.return_value = False
    interface._send_data_with_wait.return_value = SimpleNamespace(id=11)
    connection = _AdminConnection(interface, REMOTE_NODE_NUM, 5.0)
    connection._send_data_with_wait(
        "data", REMOTE_NODE_ID, response_wait_attr="receivedNak"
    )

    connection.waitForAckNak()

    interface._wait_for_request_ack.assert_not_called()
    interface._retire_wait_request.assert_called_once()
    assert connection._requests == set()


def test_wait_for_ack_nak_waits_and_raises_timeout_when_incomplete() -> None:
    interface = _fake_interface()
    interface._has_active_wait_request.return_value = True
    interface._wait_for_request_ack.return_value = False
    interface._send_data_with_wait.return_value = SimpleNamespace(id=12)
    connection = _AdminConnection(interface, REMOTE_NODE_NUM, 5.0)
    connection._send_data_with_wait(
        "data", REMOTE_NODE_ID, response_wait_attr="receivedNak"
    )

    with pytest.raises(TimeoutError, match="no acknowledgment"):
        connection.waitForAckNak()
    interface._retire_wait_request.assert_called_once()


def test_wait_for_ack_nak_raises_wait_error_when_present() -> None:
    interface = _fake_interface()
    interface._has_active_wait_request.return_value = True
    interface._wait_for_request_ack.return_value = True
    interface._raise_wait_error_if_present.side_effect = _MeshInterfaceError("NAK")
    interface._send_data_with_wait.return_value = SimpleNamespace(id=13)
    connection = _AdminConnection(interface, REMOTE_NODE_NUM, 5.0)
    connection._send_data_with_wait(
        "data", REMOTE_NODE_ID, response_wait_attr="receivedNak"
    )

    with pytest.raises(_MeshInterfaceError, match="NAK"):
        connection.waitForAckNak()
    interface._retire_wait_request.assert_called_once()


def test_cleanup_retires_outstanding_waits() -> None:
    interface = _fake_interface()
    interface._send_data_with_wait.return_value = SimpleNamespace(id=14)
    connection = _AdminConnection(interface, REMOTE_NODE_NUM, 5.0)
    connection._send_data_with_wait(
        "data", REMOTE_NODE_ID, response_wait_attr="receivedNak", wantResponse=True
    )

    connection._cleanup()

    assert interface._retire_wait_request.call_count == 1
    assert connection._requests == set()
    assert connection._response_requests == set()
