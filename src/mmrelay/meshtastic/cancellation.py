"""Invocation-owned cancellation for SDK construction, including legacy SDKs."""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from typing import Any, TypeVar

_T = TypeVar("_T")
_lock = threading.Lock()
_pending: set[threading.Event] = set()


def cancel_pending_connections() -> None:
    """Notify active constructors without waiting for native teardown."""
    with _lock:
        for event in _pending:
            event.set()


def construct_interface(
    factory: Callable[..., _T],
    *args: Any,
    cancel_event: threading.Event | None = None,
    **kwargs: Any,
) -> _T:
    """Pass a private signal only to SDKs declaring cancelEvent explicitly.

    The signal belongs to this invocation, never to an adopted interface.
    Older SDKs retain their existing cleanup and late-result ownership paths.
    """
    import mmrelay.meshtastic_utils as facade
    from mmrelay.meshtastic.async_utils import FutureWaitShutdownError

    event = cancel_event if cancel_event is not None else threading.Event()
    with _lock:
        _pending.add(event)
        if facade.shutting_down:
            event.set()
    try:
        if event.is_set():
            raise FutureWaitShutdownError("Connection cancelled before construction")
        try:
            parameter = inspect.signature(factory).parameters.get("cancelEvent")
        except (TypeError, ValueError):
            parameter = None
        if (
            parameter is not None
            and parameter.kind is not inspect.Parameter.POSITIONAL_ONLY
        ):
            kwargs["cancelEvent"] = event
        return factory(*args, **kwargs)
    finally:
        with _lock:
            _pending.discard(event)
