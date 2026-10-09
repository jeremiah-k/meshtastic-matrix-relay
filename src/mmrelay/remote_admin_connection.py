"""Command-scoped node access and ACK waits over the relay-owned connection."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterable

    from meshtastic.mesh_interface import MeshInterface
    from meshtastic.node import Node

_WAIT_ATTRIBUTE = "receivedNak"


class _DeadlineTimeout:
    """Implement node polling without extending the command's absolute deadline."""

    def __init__(self, deadline: float, timeout: float) -> None:
        self._deadline = deadline
        self._wait_deadline = deadline
        self.expireTimeout = timeout
        self.expireTime = 0.0
        self.sleepInterval = 0.1

    def reset(self, expireTimeout: float | None = None) -> None:  # noqa: N803
        duration = self.expireTimeout if expireTimeout is None else expireTimeout
        self._wait_deadline = min(self._deadline, time.monotonic() + duration)
        self.expireTime = time.time() + max(0.0, self._wait_deadline - time.monotonic())

    def waitForSet(self, target: Any, attrs: Iterable[str] = ()) -> bool:  # noqa: N802
        attr_names = tuple(attrs)
        self.reset()
        while (remaining := self._wait_deadline - time.monotonic()) > 0:
            if all(getattr(target, name, None) for name in attr_names):
                return True
            time.sleep(min(self.sleepInterval, remaining))
        return False


class _AdminConnection:
    """Keep command waits and request ownership separate from relay traffic."""

    def __init__(
        self, interface: MeshInterface, destination: int, timeout: float
    ) -> None:
        self._interface = interface
        self._destination = destination
        self._deadline = time.monotonic() + timeout
        self._requests: set[int] = set()
        self._response_requests: set[int] = set()
        self._lock = threading.RLock()
        self._closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._interface, name)

    def _remaining(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("remote admin command deadline expired")
        return remaining

    def getNode(  # noqa: N802 - mtjk interface contract
        self, nodeId: str, requestChannels: bool = True, **kwargs: Any  # noqa: N803
    ) -> Node:
        """Reuse mtjk node loading while binding ephemeral nodes to this command."""
        from meshtastic.mesh_interface_runtime.node_view import NodeView
        from meshtastic.mesh_interface_runtime.ports import _NodeViewPort
        from meshtastic.util import toNodeNum

        if toNodeNum(nodeId) != self._destination:
            raise ValueError("admin command attempted to access another node")
        kwargs["timeout"] = min(
            float(kwargs.get("timeout", self._remaining())), self._remaining()
        )
        view = NodeView(_NodeViewPort(cast("MeshInterface", self)))
        node = view.get_node(nodeId, False, **kwargs)
        node._timeout = cast(  # noqa: SLF001 - command-owned node
            "Any", _DeadlineTimeout(self._deadline, kwargs["timeout"])
        )
        request_response = cast(
            "Any", node._request_admin_response  # noqa: SLF001 - version-gated seam
        )

        def bounded_response(
            *args: Any, response_timeout_seconds: float, **options: Any
        ) -> Any:
            return request_response(
                *args,
                response_timeout_seconds=min(
                    response_timeout_seconds, self._remaining()
                ),
                response_deadline=self._deadline,
                **options,
            )

        node._request_admin_response = bounded_response  # type: ignore[method-assign]  # noqa: SLF001
        if requestChannels:
            node.requestChannels()
            attempts = kwargs.get("requestChannelAttempts", 3)
            for attempt in range(attempts):
                if node.waitForConfig():
                    break
                self._remaining()
                if attempt == attempts - 1:
                    raise self._interface.MeshInterfaceError(
                        "Error: Timed out waiting for channels, giving up"
                    )
                node.requestChannels(
                    startingIndex=(
                        len(node.partialChannels) if node.partialChannels else 0
                    )
                )
            else:
                raise self._interface.MeshInterfaceError(
                    "Error: Timed out waiting for channels, giving up"
                )
        return node

    def _send_data_with_wait(
        self, data: Any, destinationId: Any, **kwargs: Any
    ) -> Any:  # noqa: N803
        """Register only remote requests and retain their IDs for bounded waits."""
        from meshtastic.util import toNodeNum

        with self._lock:
            if self._closed:
                raise RuntimeError("remote admin command has completed")
            self._remaining()
            if toNodeNum(destinationId) != self._destination:
                raise ValueError("admin command attempted to send to another node")
            request = (
                self._interface._send_data_with_wait(  # noqa: SLF001 - pinned mtjk seam
                    data, destinationId, **kwargs
                )
            )
            if kwargs.get("response_wait_attr") == _WAIT_ATTRIBUTE:
                self._requests.add(request.id)
            if kwargs.get("wantResponse") or kwargs.get("onResponse") is not None:
                self._response_requests.add(request.id)
        return request

    def sendData(
        self, data: Any, destinationId: Any, portNum: Any = None, **kwargs: Any
    ) -> Any:  # noqa: N802, N803
        """Scope ACKs for admin writes instead of waiting on shared relay flags."""
        if portNum is not None:
            kwargs["portNum"] = portNum
        if kwargs.get("wantAck"):
            kwargs["response_wait_attr"] = _WAIT_ATTRIBUTE
        return self._send_data_with_wait(data, destinationId, **kwargs)

    def _has_active_wait_request(self, attribute: str, request_id: int) -> bool:
        return self._interface._has_active_wait_request(
            attribute, request_id
        )  # noqa: SLF001

    def _wait_for_ack_nak(self, request_id: int) -> None:
        """Wait for this request within the command's remaining budget."""
        try:
            if not self._interface._has_active_wait_request(
                _WAIT_ATTRIBUTE, request_id
            ):  # noqa: SLF001
                return
            completed = self._interface._wait_for_request_ack(  # noqa: SLF001
                _WAIT_ATTRIBUTE, request_id, timeout_seconds=self._remaining()
            )
            self._interface._raise_wait_error_if_present(  # noqa: SLF001
                _WAIT_ATTRIBUTE, request_id=request_id
            )
            if not completed:
                raise TimeoutError("no acknowledgment received before command deadline")
        finally:
            self._interface._retire_wait_request(
                _WAIT_ATTRIBUTE, request_id=request_id
            )  # noqa: SLF001
            with self._lock:
                self._requests.discard(request_id)
                self._response_requests.discard(request_id)

    def waitForAckNak(self) -> None:  # noqa: N802 - mtjk interface contract
        """Await only requests sent by this command, including already received ACKs."""
        with self._lock:
            requests = tuple(self._requests)
        for request_id in requests:
            self._wait_for_ack_nak(request_id)

    def _cleanup(self) -> None:
        """Retire outstanding command waits without closing the shared transport."""
        with self._lock:
            self._closed = True
            requests = self._requests | self._response_requests
            self._requests.clear()
            self._response_requests.clear()
        for request_id in requests:
            self._interface._retire_wait_request(
                _WAIT_ATTRIBUTE, request_id=request_id
            )  # noqa: SLF001
