#!/usr/bin/env python3
"""Coverage test for main() ready-task shutdown cleanup path."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import mmrelay.main as main_module


class _ImmediateEvent:
    """Event that starts set so main() exits its sync loop immediately."""

    def is_set(self) -> bool:
        return True

    def set(self) -> None:
        return None

    async def wait(self) -> None:
        return None


async def _async_noop(*_args, **_kwargs) -> None:
    """Async no-op helper for patched callbacks."""
    return None


def _make_async_return(value):
    async def _async_return(*_args, **_kwargs):
        return value

    return _async_return


def _make_patched_get_running_loop():
    """Return a helper that patches run_in_executor to execute inline."""
    original_get_running_loop = asyncio.get_running_loop

    def _patched_get_running_loop():
        loop = original_get_running_loop()

        async def _mock_run_in_executor(_executor, func, *args):
            return func(*args)

        loop.run_in_executor = _mock_run_in_executor  # type: ignore[assignment]
        return loop

    return _patched_get_running_loop


def test_coerce_config_bool_normalizes_common_values() -> None:
    """wipe_on_restart parsing should normalize booleans, strings, and numerics."""
    assert main_module._coerce_config_bool(True) is True
    assert main_module._coerce_config_bool(False) is False
    assert main_module._coerce_config_bool("true") is True
    assert main_module._coerce_config_bool("false") is False
    assert main_module._coerce_config_bool("1") is True
    assert main_module._coerce_config_bool("0") is False
    assert main_module._coerce_config_bool("not-a-bool") is False
    assert main_module._coerce_config_bool(1) is True
    assert main_module._coerce_config_bool(0) is False


def test_main_cleans_up_ready_task_on_shutdown(tmp_path, monkeypatch) -> None:
    """Configured ready heartbeat task should be cancelled/awaited during shutdown."""
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": "serial"},
    }

    ready_path = tmp_path / "ready"
    monkeypatch.setattr(main_module, "_ready_file_path", str(ready_path))
    monkeypatch.setattr(main_module, "_ready_heartbeat_seconds", 1)

    mock_matrix_client = AsyncMock()
    mock_matrix_client.add_event_callback = MagicMock()
    mock_matrix_client.close = AsyncMock()

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
        patch("mmrelay.main.shutdown_plugins"),
        patch("mmrelay.main.stop_message_queue"),
        patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
        patch("mmrelay.main.sys.platform", main_module.WINDOWS_PLATFORM),
    ):
        mock_queue = MagicMock()
        mock_queue.ensure_processor_started = MagicMock()
        mock_get_queue.return_value = mock_queue
        asyncio.run(main_module.main(config))

    mock_matrix_client.close.assert_awaited_once()
    assert not ready_path.exists()
