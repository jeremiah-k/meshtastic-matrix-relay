"""SDK construction cancellation is private to each invocation."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from mmrelay.meshtastic.cancellation import (
    cancel_pending_connections,
    construct_interface,
)


@pytest.mark.usefixtures("reset_meshtastic_globals")
def test_shutdown_wakes_an_active_native_constructor():
    """The SDK signal is set from the shutdown caller's thread."""
    entered = threading.Event()

    def factory(*, cancelEvent=None):
        assert isinstance(cancelEvent, threading.Event)
        entered.set()
        assert cancelEvent.wait(2), "Native constructor was not cancelled"
        return cancelEvent

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(construct_interface, factory)
        try:
            assert entered.wait(2)
            cancel_pending_connections()
            assert pending.result(timeout=2).is_set()
        finally:
            cancel_pending_connections()


@pytest.mark.usefixtures("reset_meshtastic_globals")
def test_adopted_interfaces_do_not_retain_shutdown_ownership():
    """A later shutdown cannot mutate a completed invocation's private signal."""

    def factory(*, cancelEvent=None):
        return cancelEvent

    signal = construct_interface(factory)
    cancel_pending_connections()
    assert not signal.is_set()


@pytest.mark.usefixtures("reset_meshtastic_globals")
def test_legacy_sdk_receives_no_cancellation_keyword():
    """Compatibility does not rely on accepting arbitrary SDK keyword arguments."""

    def factory(*, timeout):
        return timeout

    assert construct_interface(factory, timeout=7) == 7


@pytest.mark.usefixtures("reset_meshtastic_globals")
def test_pre_cancelled_constructor_does_not_open_a_transport():
    """A queued BLE task cannot construct a new client after its deadline."""
    import mmrelay.meshtastic_utils as facade

    signal = threading.Event()
    signal.set()

    def factory():
        pytest.fail("Cancelled construction reached the native transport")

    with pytest.raises(facade.FutureWaitShutdownError):
        construct_interface(factory, cancel_event=signal)


@pytest.mark.usefixtures("reset_meshtastic_globals")
async def test_application_shutdown_cancels_native_construction(
    monkeypatch, mock_config
):
    """Cancelling main during startup signals the real native constructor boundary."""
    import asyncio
    from unittest.mock import Mock

    import mmrelay.main as app
    from tests._test_main_helpers import _thread_backed_to_thread

    monkeypatch.setattr(app.asyncio, "to_thread", _thread_backed_to_thread)
    entered, retired = threading.Event(), threading.Event()

    def factory(*, cancelEvent=None):
        entered.set()
        try:
            assert cancelEvent.wait(3), "Application shutdown did not notify the SDK"
            return Mock()
        finally:
            retired.set()

    def connect(*args, **kwargs):
        return construct_interface(factory)

    for name in (
        "initialize_database",
        "load_plugins",
        "start_message_queue",
        "stop_message_queue",
        "shutdown_plugins",
        "_remove_ready_file",
    ):
        monkeypatch.setattr(app, name, Mock())
    monkeypatch.setattr(app, "connect_meshtastic", connect)
    task = asyncio.create_task(app.main(mock_config))
    try:
        if not await asyncio.to_thread(entered.wait, 3):
            if task.done():
                await task
            pytest.fail("Application never reached construction")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 4)
        assert await asyncio.to_thread(retired.wait, 1)
    finally:
        cancel_pending_connections()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
