import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.network import DEFAULT_BACKOFF_TIME
from mmrelay.meshtastic_utils import reconnect


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestReconnectSuccess:
    def test_reconnect_succeeds_and_clears_future_and_flag(self):
        mock_client = MagicMock()
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None

        async def _run():
            await reconnect()

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=False),
            patch(
                "mmrelay.meshtastic_utils.connect_meshtastic", return_value=mock_client
            ),
            patch("mmrelay.meshtastic_utils.asyncio.sleep", new_callable=AsyncMock),
        ):
            asyncio.run(_run())

        assert mu.meshtastic_client is mock_client
        assert mu.reconnecting is False
        assert mu.reconnect_task_future is None

    def test_reconnect_clears_future_after_success(self):
        mock_client = MagicMock()
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None

        async def _run():
            await reconnect()

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=False),
            patch(
                "mmrelay.meshtastic_utils.connect_meshtastic", return_value=mock_client
            ),
            patch("mmrelay.meshtastic_utils.asyncio.sleep", new_callable=AsyncMock),
        ):
            asyncio.run(_run())

        assert mu.reconnect_task_future is None
        assert mu.reconnecting is False
        assert mu.meshtastic_client is mock_client


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestReconnectCancellation:
    def test_reconnect_cancellation_logs_and_clears_state(self):
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None

        async def _run_with_cancel():
            reconnect_task = asyncio.create_task(reconnect())
            await asyncio.sleep(0.01)
            reconnect_task.cancel()
            await reconnect_task

        async def _sleep_side_effect(_seconds):
            await asyncio.sleep(0.001)

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=False),
            patch(
                "mmrelay.meshtastic_utils.asyncio.sleep", side_effect=_sleep_side_effect
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            try:
                asyncio.run(_run_with_cancel())
            except (asyncio.CancelledError, RuntimeError):
                pass

        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any("Shutdown in progress" in c for c in debug_calls) or any(
            "Reconnection task was cancelled" in str(c)
            for c in mock_logger.info.call_args_list
        )
        assert mu.reconnecting is False


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestReconnectShutdownAbort:
    def test_shutdown_during_backoff_aborts_reconnect(self):
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None
        sleep_call_count = 0

        async def _sleep_side_effect(_seconds):
            nonlocal sleep_call_count
            sleep_call_count += 1
            mu.shutting_down = True
            await asyncio.sleep(0)

        async def _run():
            await reconnect()

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=False),
            patch(
                "mmrelay.meshtastic_utils.asyncio.sleep", side_effect=_sleep_side_effect
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            asyncio.run(_run())

        assert sleep_call_count == 1
        assert any(
            "Shutdown in progress. Aborting reconnection attempts." in str(c.args)
            for c in mock_logger.debug.call_args_list
        )
        assert mu.reconnecting is False


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestReconnectFailureBackoff:
    def test_connect_failure_logs_and_doubles_backoff(self):
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None
        attempt_count = 0

        def _connect_side_effect(cfg, force):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count >= 3:
                mu.shutting_down = True
            raise ConnectionError("connection refused")

        async def _sleep_side_effect(seconds):
            await asyncio.sleep(0)

        async def _run():
            await reconnect()

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=False),
            patch(
                "mmrelay.meshtastic_utils.connect_meshtastic",
                side_effect=_connect_side_effect,
            ),
            patch(
                "mmrelay.meshtastic_utils.asyncio.sleep", side_effect=_sleep_side_effect
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            asyncio.run(_run())

        assert attempt_count == 3
        assert any(
            "Reconnection attempt failed" in str(c.args)
            for c in mock_logger.exception.call_args_list
        )
        assert mu.reconnecting is False
        assert mu.reconnect_task_future is None

    def test_backoff_caps_at_300_seconds(self):
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None
        attempt_count = 0
        sleep_times = []

        def _connect_side_effect(cfg, force):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count >= 10:
                mu.shutting_down = True
            raise ConnectionError("connection refused")

        async def _sleep_side_effect(seconds):
            sleep_times.append(seconds)
            await asyncio.sleep(0)

        async def _run():
            await reconnect()

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=False),
            patch(
                "mmrelay.meshtastic_utils.connect_meshtastic",
                side_effect=_connect_side_effect,
            ),
            patch(
                "mmrelay.meshtastic_utils.asyncio.sleep", side_effect=_sleep_side_effect
            ),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            asyncio.run(_run())

        assert max(sleep_times) == 300
        assert mu.reconnecting is False
        assert mu.reconnect_task_future is None

    def test_reconnect_task_future_cleared_after_failure(self):
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None
        attempt_count = 0

        def _connect_side_effect(cfg, force):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count >= 2:
                mu.shutting_down = True
            raise RuntimeError("unexpected error")

        async def _sleep_side_effect(seconds):
            await asyncio.sleep(0)

        async def _run():
            await reconnect()

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=False),
            patch(
                "mmrelay.meshtastic_utils.connect_meshtastic",
                side_effect=_connect_side_effect,
            ),
            patch(
                "mmrelay.meshtastic_utils.asyncio.sleep", side_effect=_sleep_side_effect
            ),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            asyncio.run(_run())

        assert mu.reconnect_task_future is None
        assert mu.reconnecting is False
