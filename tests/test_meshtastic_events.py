import time
from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestDeriveDisconnectDetectionSource:
    def test_known_source_returned(self):
        from mmrelay.meshtastic.events import _derive_disconnect_detection_source

        result = _derive_disconnect_detection_source(MagicMock(), "tcp_error", None)
        assert result == "tcp_error"

    def test_interface_source_used(self):
        from mmrelay.meshtastic.events import _derive_disconnect_detection_source

        iface = MagicMock()
        iface._last_disconnect_source = "ble.disconnect"
        result = _derive_disconnect_detection_source(iface, "unknown", None)
        assert result == "disconnect"

    def test_topic_name_used(self):
        from mmrelay.meshtastic.events import _derive_disconnect_detection_source

        topic = MagicMock()
        topic.getName.return_value = "meshtastic.connection.lost"
        result = _derive_disconnect_detection_source(MagicMock(), "unknown", topic)
        assert result == "meshtastic.connection.lost"

    def test_fallback_default(self):
        from mmrelay.meshtastic.events import _derive_disconnect_detection_source

        iface = MagicMock(spec=[])
        result = _derive_disconnect_detection_source(
            iface, "unknown", mu.pub.AUTO_TOPIC
        )
        assert result == "meshtastic.connection.lost"


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestTearDownMeshtasticClientForDisconnect:
    def test_no_client_returns(self):
        from mmrelay.meshtastic.events import (
            _tear_down_meshtastic_client_for_disconnect,
        )

        mu.meshtastic_client = None
        _tear_down_meshtastic_client_for_disconnect("test")

    def test_ble_interface_disconnect(self):
        from mmrelay.meshtastic.events import (
            _tear_down_meshtastic_client_for_disconnect,
        )

        mock_iface = MagicMock()
        mu.meshtastic_client = mock_iface
        mu.meshtastic_iface = mock_iface

        with patch.object(mu, "_disconnect_ble_interface"):
            _tear_down_meshtastic_client_for_disconnect("test")
        assert mu.meshtastic_iface is None

    def test_non_ble_client_close(self):
        from mmrelay.meshtastic.events import (
            _tear_down_meshtastic_client_for_disconnect,
        )

        client = MagicMock()
        mu.meshtastic_client = client
        mu.meshtastic_iface = None

        _tear_down_meshtastic_client_for_disconnect("test")
        client.close.assert_called_once()

    def test_os_error_on_close(self):
        from mmrelay.meshtastic.events import (
            _tear_down_meshtastic_client_for_disconnect,
        )

        client = MagicMock()
        client.close.side_effect = OSError()
        client.close.side_effect.errno = mu.ERRNO_BAD_FILE_DESCRIPTOR
        mu.meshtastic_client = client
        mu.meshtastic_iface = None

        _tear_down_meshtastic_client_for_disconnect("test")


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestOnLostMeshtasticConnection:
    def test_shutdown_returns(self):
        from mmrelay.meshtastic.events import on_lost_meshtastic_connection

        mu.shutting_down = True
        on_lost_meshtastic_connection()
        mu.shutting_down = False

    def test_reconnecting_returns(self):
        from mmrelay.meshtastic.events import on_lost_meshtastic_connection

        mu.shutting_down = False
        mu.reconnecting = True
        mu.meshtastic_client = MagicMock()
        on_lost_meshtastic_connection()
        mu.reconnecting = False

    def test_stale_interface_ignored(self):
        from mmrelay.meshtastic.events import on_lost_meshtastic_connection

        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = MagicMock()
        mu._relay_active_client_id = 99999

        stale_iface = MagicMock()
        on_lost_meshtastic_connection(interface=stale_iface)

    def test_no_client_with_callbacks_tearing_down(self):
        from mmrelay.meshtastic.events import on_lost_meshtastic_connection

        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = None
        mu._relay_active_client_id = None
        mu._callbacks_tearing_down = True
        mu.subscribed_to_connection_lost = False

        iface = MagicMock()
        on_lost_meshtastic_connection(interface=iface)

    def test_triggers_reconnect(self):
        from mmrelay.meshtastic.events import on_lost_meshtastic_connection

        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = MagicMock()
        mu._relay_active_client_id = None
        mu.meshtastic_iface = None
        mu._ble_future = None
        mu._ble_future_address = None
        mu._ble_executor_degraded_addresses = set()

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        mock_loop.is_running.return_value = True
        mu.event_loop = mock_loop

        scheduled_reconnect_task = MagicMock()

        def _schedule_coroutine(coro, _loop):
            coro.close()
            return scheduled_reconnect_task

        with (
            patch.object(mu, "_disconnect_ble_interface"),
            patch.object(mu, "reset_executor_degraded_state"),
            patch.object(
                mu.asyncio,
                "run_coroutine_threadsafe",
                side_effect=_schedule_coroutine,
            ),
        ):
            on_lost_meshtastic_connection()
        assert mu.reconnecting is True
        assert mu.reconnect_task is scheduled_reconnect_task
        mu.reconnecting = False

    def test_reconnect_schedule_runtime_error_closes_coroutine(self):
        import mmrelay.meshtastic.events as events

        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = MagicMock()
        mu._relay_active_client_id = None
        mu.meshtastic_iface = None
        mu._ble_future = None
        mu._ble_future_address = None
        mu._ble_executor_degraded_addresses = set()

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        mu.event_loop = mock_loop

        reconnect_coro = MagicMock()

        with (
            patch.object(events.facade, "_disconnect_ble_interface"),
            patch.object(events.facade, "reset_executor_degraded_state"),
            patch.object(
                events.facade,
                "reconnect",
                new=MagicMock(return_value=reconnect_coro),
            ),
            patch.object(
                events.facade.asyncio,
                "run_coroutine_threadsafe",
                side_effect=RuntimeError("loop unavailable"),
            ) as mock_schedule,
            patch.object(events.facade.logger, "error") as mock_logger_error,
        ):
            events.on_lost_meshtastic_connection()

        assert mu.reconnecting is False
        reconnect_coro.close.assert_called_once()
        mock_schedule.assert_called_once_with(reconnect_coro, mock_loop)
        assert any(
            call.args
            and call.args[0]
            == "Failed to schedule reconnect; event loop became unavailable"
            for call in mock_logger_error.call_args_list
        )

    def test_cancels_pending_connect_time_probe_timer(self):
        from mmrelay.meshtastic.events import on_lost_meshtastic_connection

        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = MagicMock()
        mu._relay_active_client_id = None
        mu.meshtastic_iface = None
        mu._ble_future = None
        mu._ble_future_address = None
        mu._ble_executor_degraded_addresses = set()

        mock_timer = MagicMock()
        mu._pending_connect_time_probe_timer = mock_timer

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        mock_loop.is_running.return_value = True
        mu.event_loop = mock_loop

        scheduled_reconnect_task = MagicMock()

        def _schedule_coroutine(coro, _loop):
            coro.close()
            return scheduled_reconnect_task

        with (
            patch.object(mu, "_disconnect_ble_interface"),
            patch.object(mu, "reset_executor_degraded_state"),
            patch.object(
                mu.asyncio,
                "run_coroutine_threadsafe",
                side_effect=_schedule_coroutine,
            ),
        ):
            on_lost_meshtastic_connection()

        mock_timer.cancel.assert_called_once()
        assert mu._pending_connect_time_probe_timer is None
        mu.reconnecting = False


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestOnMeshtasticMessage:
    def test_empty_packet_returns(self):
        from mmrelay.meshtastic.events import on_meshtastic_message

        on_meshtastic_message({}, MagicMock())

    def test_none_packet_returns(self):
        from mmrelay.meshtastic.events import on_meshtastic_message

        on_meshtastic_message(None, MagicMock())

    def test_shutdown_returns(self):
        from mmrelay.meshtastic.events import on_meshtastic_message

        mu.shutting_down = True
        on_meshtastic_message({"decoded": {}}, MagicMock())
        mu.shutting_down = False

    def test_no_client_returns(self):
        from mmrelay.meshtastic.events import on_meshtastic_message

        mu.meshtastic_client = None
        mu._relay_active_client_id = None
        mu._callbacks_tearing_down = True
        mu.subscribed_to_messages = False
        mu.reconnecting = False
        mu.shutting_down = False
        on_meshtastic_message({"decoded": {}}, MagicMock())

    def test_stale_interface_ignored(self):
        from mmrelay.meshtastic.events import on_meshtastic_message

        mu.meshtastic_client = MagicMock()
        mu._relay_active_client_id = 99999
        mu.shutting_down = False
        mu._callbacks_tearing_down = False
        mu.subscribed_to_messages = False
        mu.reconnecting = False

        stale_iface = MagicMock()
        on_meshtastic_message({"decoded": {}}, stale_iface)

    def test_no_config_returns(self):
        from mmrelay.meshtastic.events import on_meshtastic_message

        iface = MagicMock()
        iface.myInfo.my_node_num = 12345
        mu.meshtastic_client = iface
        mu._relay_active_client_id = id(iface)
        mu.config = None
        mu.shutting_down = False
        mu._callbacks_tearing_down = False
        mu.subscribed_to_messages = False
        mu.reconnecting = False
        mu._relay_startup_drain_deadline_monotonic_secs = None
        mu._relay_rx_time_clock_skew_secs = None

        packet = {
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "hello"},
            "fromId": "!abc",
            "to": 4294967295,
            "rxTime": 0,
        }
        on_meshtastic_message(packet, iface)


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestScheduleStartupDrainDeadlineCleanup:
    def test_schedule_with_future_deadline(self):
        from mmrelay.meshtastic.events import _schedule_startup_drain_deadline_cleanup

        deadline = time.monotonic() + 10.0
        mu._relay_startup_drain_expiry_timer = None
        mu._relay_startup_drain_deadline_monotonic_secs = deadline

        _schedule_startup_drain_deadline_cleanup(deadline)
        assert mu._relay_startup_drain_expiry_timer is not None
        mu._relay_startup_drain_expiry_timer.cancel()
        mu._relay_startup_drain_expiry_timer = None

    def test_schedule_replaces_existing_timer(self):
        from mmrelay.meshtastic.events import _schedule_startup_drain_deadline_cleanup

        old_timer = MagicMock()
        mu._relay_startup_drain_expiry_timer = old_timer

        deadline = time.monotonic() + 10.0
        # Only the current deadline's timer may displace the registered one.
        mu._relay_startup_drain_deadline_monotonic_secs = deadline
        with patch("threading.Timer") as MockTimer:
            mock_timer = MagicMock()
            MockTimer.return_value = mock_timer
            _schedule_startup_drain_deadline_cleanup(deadline)
            old_timer.cancel.assert_called_once()
            assert mu._relay_startup_drain_expiry_timer is mock_timer

    def test_schedule_stale_deadline_does_not_replace_existing_timer(self):
        from mmrelay.meshtastic.events import _schedule_startup_drain_deadline_cleanup

        current_timer = MagicMock()
        mu._relay_startup_drain_expiry_timer = current_timer

        mu._relay_startup_drain_deadline_monotonic_secs = time.monotonic() + 20.0
        with patch("threading.Timer") as MockTimer:
            _schedule_startup_drain_deadline_cleanup(time.monotonic() + 10.0)
        current_timer.cancel.assert_not_called()
        assert mu._relay_startup_drain_expiry_timer is current_timer
        MockTimer.return_value.start.assert_not_called()


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestReconnect:
    @pytest.mark.asyncio
    async def test_shutdown_skips(self):
        from mmrelay.meshtastic.events import reconnect

        mu.shutting_down = True
        await reconnect()
        assert mu.reconnecting is False


def test_stopped_loop_reconnect_does_not_claim_owner(
    reset_meshtastic_globals, monkeypatch
):
    from mmrelay.meshtastic.events import _schedule_reconnect_after_disconnect

    loop = MagicMock()
    loop.is_closed.return_value = False
    loop.is_running.return_value = False
    monkeypatch.setattr(mu, "event_loop", loop)
    mu.reconnecting = True
    with patch.object(mu, "reconnect") as reconnect:
        _schedule_reconnect_after_disconnect()
    assert mu.reconnecting is False
    reconnect.assert_not_called()


def test_accepted_reconnect_loses_owner_when_loop_stops(
    reset_meshtastic_globals, monkeypatch
):
    import asyncio
    import threading
    from concurrent.futures import Future

    from mmrelay.meshtastic.events import _schedule_reconnect_after_disconnect

    loop = MagicMock()
    loop.is_closed.return_value = False
    loop.is_running.return_value = True
    monkeypatch.setattr(mu, "event_loop", loop)
    future = Future()

    async def reconnect(*, _owner=None):
        pass

    def fake_submit(coroutine, _loop):
        coroutine.close()  # Simulate the loop accepting but never running it.
        return future

    with (
        patch.object(mu, "reconnect", reconnect),
        patch.object(asyncio, "run_coroutine_threadsafe", side_effect=fake_submit),
    ):
        released_owner = threading.Event()
        with patch.object(
            mu.logger, "warning", side_effect=lambda *_: released_owner.set()
        ):
            _schedule_reconnect_after_disconnect()
            assert mu.reconnecting is True
            loop.is_running.return_value = False
            assert released_owner.wait(
                2
            ), "Stopped-loop reconnect owner was not released"
        assert future.cancelled()
        assert mu.reconnecting is False
        assert mu.reconnect_task is None


def test_expired_startup_drain_signals_inside_ownership_lock(
    reset_meshtastic_globals, monkeypatch
):
    import threading

    from mmrelay.meshtastic.events import _schedule_startup_drain_deadline_cleanup

    completed = threading.Event()
    armed = []
    mu._relay_startup_drain_deadline_monotonic_secs = 100.0
    fake_time = MagicMock()
    fake_time.monotonic.return_value = 100.0
    monkeypatch.setattr(mu, "time", fake_time)

    class Completion:
        def set(self):
            assert mu._relay_rx_time_clock_skew_lock.locked()
            completed.set()

    class Timer:
        def __init__(self, _delay, action):
            self.action = action
            armed.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    with (
        patch.object(mu, "get_startup_drain_complete_event", return_value=Completion()),
        patch("mmrelay.meshtastic.events.threading.Timer", Timer),
    ):
        _schedule_startup_drain_deadline_cleanup(100.0)
        _schedule_startup_drain_deadline_cleanup(100.0)
        armed[0].action()
        assert not completed.is_set()
        assert mu._relay_startup_drain_expiry_timer is armed[1]
        armed[1].action()
    assert completed.is_set()
    assert mu._relay_startup_drain_expiry_timer is None


def test_stale_startup_drain_scheduler_does_not_replace_new_owner(
    reset_meshtastic_globals, monkeypatch
):
    from mmrelay.meshtastic.events import _schedule_startup_drain_deadline_cleanup

    current_timer = MagicMock()
    mu._relay_startup_drain_expiry_timer = current_timer
    mu._relay_startup_drain_deadline_monotonic_secs = 200.0
    fake_time = MagicMock()
    fake_time.monotonic.return_value = 100.0
    monkeypatch.setattr(mu, "time", fake_time)
    with patch("mmrelay.meshtastic.events.threading.Timer") as timer_cls:
        _schedule_startup_drain_deadline_cleanup(100.0)
    assert mu._relay_startup_drain_expiry_timer is current_timer
    current_timer.cancel.assert_not_called()
    timer_cls.return_value.start.assert_not_called()


def test_reconnect_monitor_start_failure_releases_owner(
    reset_meshtastic_globals, monkeypatch
):
    from concurrent.futures import Future

    from mmrelay.meshtastic.events import _schedule_reconnect_after_disconnect

    loop = MagicMock()
    loop.is_closed.return_value = False
    loop.is_running.return_value = True
    monkeypatch.setattr(mu, "event_loop", loop)
    pending = Future()

    async def reconnect(*, _owner=None):
        pass

    def accepted_but_not_started(coroutine, _loop):
        coroutine.close()
        return pending

    worker = MagicMock()
    worker.start.side_effect = RuntimeError("can't start new thread")
    with (
        patch.object(mu, "reconnect", reconnect),
        patch.object(
            mu.asyncio, "run_coroutine_threadsafe", side_effect=accepted_but_not_started
        ),
        patch("mmrelay.meshtastic.events.threading.Thread", return_value=worker),
    ):
        _schedule_reconnect_after_disconnect()
    assert pending.cancelled()
    assert mu.reconnect_task is None
    assert not mu.reconnecting


@pytest.mark.usefixtures("reset_meshtastic_globals")
def test_failed_startup_drain_timer_does_not_open_packet_gate(monkeypatch):
    """A missing timer must not admit historical packets before the deadline."""
    from mmrelay.meshtastic.events import _schedule_startup_drain_deadline_cleanup

    deadline = time.monotonic() + 10
    mu._relay_startup_drain_deadline_monotonic_secs = deadline
    readiness = MagicMock()
    timer = MagicMock()
    timer.start.side_effect = RuntimeError("can't start new thread")
    with (
        patch("mmrelay.meshtastic.events.threading.Timer", return_value=timer),
        patch.object(mu, "get_startup_drain_complete_event", return_value=readiness),
    ):
        _schedule_startup_drain_deadline_cleanup(deadline)
    assert mu._relay_startup_drain_deadline_monotonic_secs == deadline
    assert mu._relay_startup_drain_expiry_timer is None
    readiness.set.assert_not_called()


@pytest.mark.usefixtures("reset_meshtastic_globals")
@pytest.mark.asyncio
async def test_old_reconnect_finally_cannot_clear_new_attempt():
    """An old loop may resume after its reconnect owner has been replaced."""
    from mmrelay.meshtastic.events import reconnect

    old_owner, new_owner = object(), object()
    mu.shutting_down = True
    mu._relay_reconnect_owner = new_owner
    mu.reconnecting = True
    await reconnect(_owner=old_owner)
    assert mu.reconnecting is True
    assert mu._relay_reconnect_owner is new_owner
    # An unowned direct call must not clear an owned attempt either.
    await reconnect()
    assert mu.reconnecting is True

    await reconnect(_owner=new_owner)
    assert mu.reconnecting is False
    assert mu._relay_reconnect_owner is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
def test_cancelled_before_start_reconnect_releases_owner(monkeypatch):
    """Cancellation can complete the concurrent Future before the coroutine runs."""
    import threading
    from concurrent.futures import Future

    from mmrelay.meshtastic.events import _schedule_reconnect_after_disconnect

    loop = MagicMock()
    loop.is_closed.return_value = False
    loop.is_running.return_value = True
    monkeypatch.setattr(mu, "event_loop", loop)
    future = Future()
    future.cancel()
    released = threading.Event()

    def fake_submit(coroutine, _loop):
        coroutine.close()
        return future

    with (
        patch.object(mu.asyncio, "run_coroutine_threadsafe", side_effect=fake_submit),
        patch.object(mu.logger, "warning", side_effect=lambda *_: released.set()),
    ):
        _schedule_reconnect_after_disconnect()
        assert released.wait(2)
    assert mu.reconnect_task is None
    assert mu._relay_reconnect_owner is None
    assert mu.reconnecting is False
