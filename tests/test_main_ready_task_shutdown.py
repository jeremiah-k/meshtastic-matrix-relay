#!/usr/bin/env python3
"""Coverage test for main() ready-task shutdown cleanup path."""

import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import mmrelay.main as main_module
from tests.helpers import InlineExecutorLoop


class _ControllableEvent:
    """Minimal asyncio.Event-compatible object with explicit set/clear control."""

    def __init__(self) -> None:
        self._set = False
        self._waiters: list[asyncio.Future[None]] = []

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True
        waiters = self._waiters[:]
        self._waiters.clear()
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)

    def clear(self) -> None:
        self._set = False

    async def wait(self) -> None:
        if self._set:
            return
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        await waiter


class _EventFactory:
    """Factory that returns fresh controllable events and records instances."""

    def __init__(self) -> None:
        self.created: list[_ControllableEvent] = []

    def __call__(self) -> _ControllableEvent:
        event = _ControllableEvent()
        self.created.append(event)
        return event


async def _async_noop(*_args, **_kwargs) -> None:
    """Async no-op helper for patched callbacks."""
    return None


def _make_async_return(value):
    async def _async_return(*_args, **_kwargs):
        return value

    return _async_return


def _make_patched_get_running_loop():
    """Wrap the running loop so run_in_executor executes inline."""
    real_get_running_loop = asyncio.get_running_loop

    def _patched_get_running_loop():
        loop = real_get_running_loop()
        if isinstance(loop, InlineExecutorLoop):
            return loop
        return InlineExecutorLoop(loop)

    return _patched_get_running_loop


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float = 2.0,
    poll_interval_seconds: float = 0.01,
) -> None:
    """Poll until predicate returns truthy or raise on timeout."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(poll_interval_seconds)
    raise AssertionError("Timed out waiting for test condition")


def test_coerce_config_bool_normalizes_common_values() -> None:
    """wipe_on_restart parsing should normalize booleans, strings, and numerics."""
    assert main_module._coerce_config_bool(True) is True
    assert main_module._coerce_config_bool(False) is False
    assert main_module._coerce_config_bool("true") is True
    assert main_module._coerce_config_bool("false") is False
    assert main_module._coerce_config_bool("True") is True
    assert main_module._coerce_config_bool("FALSE") is False
    assert main_module._coerce_config_bool("1") is True
    assert main_module._coerce_config_bool("0") is False
    assert main_module._coerce_config_bool("not-a-bool") is False
    assert main_module._coerce_config_bool(1) is True
    assert main_module._coerce_config_bool(0) is False
    assert main_module._coerce_config_bool(2) is False
    assert main_module._coerce_config_bool(-1) is False
    assert main_module._coerce_config_bool(1.5) is False
    assert main_module._coerce_config_bool(float("nan")) is False
    assert main_module._coerce_config_bool(None) is False


@pytest.mark.asyncio
async def test_main_cleans_up_ready_task_on_shutdown(tmp_path, monkeypatch) -> None:
    """Configured ready heartbeat task should be cancelled/awaited during shutdown."""
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": "serial"},
    }

    ready_path = tmp_path / "ready"
    monkeypatch.setattr(main_module, "_ready_file_path", str(ready_path))
    monkeypatch.setattr(main_module, "_ready_heartbeat_seconds", 0.01)

    mock_matrix_client = AsyncMock()
    mock_matrix_client.add_event_callback = MagicMock()
    mock_matrix_client.close = AsyncMock()
    event_factory = _EventFactory()
    captured_ready_event: _ControllableEvent | None = None

    async def _sync_forever_wait(*_args, **_kwargs) -> None:
        await asyncio.sleep(3600)

    real_ready_heartbeat = main_module._ready_heartbeat

    async def _capture_ready_heartbeat(event: _ControllableEvent) -> None:
        nonlocal captured_ready_event
        captured_ready_event = event
        await real_ready_heartbeat(event)

    mock_matrix_client.sync_forever = AsyncMock(side_effect=_sync_forever_wait)

    with (
        patch(
            "mmrelay.main.asyncio.get_running_loop",
            side_effect=_make_patched_get_running_loop(),
        ),
        patch("mmrelay.main.initialize_database"),
        patch("mmrelay.main.load_plugins"),
        patch("mmrelay.main.start_message_queue"),
        patch("mmrelay.main.connect_meshtastic", return_value=None),
        patch(
            "mmrelay.main.connect_matrix",
            side_effect=_make_async_return(mock_matrix_client),
        ),
        patch("mmrelay.main.join_matrix_room", side_effect=_async_noop),
        patch("mmrelay.main.get_message_queue") as mock_get_queue,
        patch(
            "mmrelay.main.meshtastic_utils.check_connection", side_effect=_async_noop
        ),
        patch(
            "mmrelay.main.meshtastic_utils.refresh_node_name_tables",
            side_effect=_async_noop,
        ),
        patch(
            "mmrelay.main._touch_ready_file", wraps=main_module._touch_ready_file
        ) as mock_touch_ready_file,
        patch("mmrelay.main._ready_heartbeat", side_effect=_capture_ready_heartbeat),
        patch("mmrelay.main.shutdown_plugins"),
        patch("mmrelay.main.stop_message_queue"),
        patch("mmrelay.main.asyncio.Event", side_effect=event_factory),
        patch("mmrelay.main.sys.platform", main_module.WINDOWS_PLATFORM),
    ):
        mock_queue = MagicMock()
        mock_queue.ensure_processor_started = MagicMock()
        mock_get_queue.return_value = mock_queue

        main_task = asyncio.create_task(main_module.main(config))
        await _wait_until(lambda: bool(event_factory.created))
        await _wait_until(lambda: captured_ready_event is not None)
        await _wait_until(
            lambda: ready_path.exists() and mock_touch_ready_file.call_count > 0
        )
        assert captured_ready_event is not None
        captured_ready_event.set()
        await asyncio.wait_for(main_task, timeout=5)

    mock_matrix_client.close.assert_awaited_once()
    assert not ready_path.exists()
