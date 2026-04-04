import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.meshtastic_utils import reconnect


def _sleep_and_mark_shutdown(_seconds: int) -> None:
    mu.shutting_down = True


@pytest.mark.usefixtures("reset_meshtastic_globals")
@pytest.mark.asyncio
class TestReconnectSuccess:
    async def test_reconnect_succeeds_and_clears_future_and_flag(self):
        mock_client = MagicMock()
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None

        def _connect_side_effect(_cfg, _force):
            mu.meshtastic_client = mock_client
            return mock_client

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.connect_meshtastic",
                side_effect=_connect_side_effect,
            ),
            patch("mmrelay.meshtastic_utils.asyncio.sleep", new_callable=AsyncMock),
        ):
            await reconnect()

        assert mu.meshtastic_client is mock_client
        assert mu.reconnecting is False
        assert mu.reconnect_task_future is None

    async def test_reconnect_success_does_not_republish_client_global(self):
        mock_client = MagicMock()
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None
        mu.meshtastic_client = None

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.connect_meshtastic", return_value=mock_client
            ),
            patch("mmrelay.meshtastic_utils.asyncio.sleep", new_callable=AsyncMock),
        ):
            await reconnect()

        assert mu.reconnect_task_future is None
        assert mu.reconnecting is False
        assert mu.meshtastic_client is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
@pytest.mark.asyncio
class TestReconnectCancellation:
    async def test_reconnect_cancellation_logs_and_clears_state(self):
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=asyncio.CancelledError,
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            await reconnect()

        mock_logger.info.assert_any_call("Reconnection task was cancelled.")
        assert mu.reconnecting is False
        assert mu.reconnect_task_future is None


@pytest.mark.usefixtures("reset_meshtastic_globals")
@pytest.mark.asyncio
class TestReconnectShutdownAbort:
    async def test_shutdown_during_backoff_aborts_reconnect(self):
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=_sleep_and_mark_shutdown,
            ),
            patch("mmrelay.meshtastic_utils.connect_meshtastic") as mock_connect,
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            await reconnect()

        mock_connect.assert_not_called()
        mock_logger.debug.assert_any_call(
            "Shutdown in progress. Aborting reconnection attempts."
        )
        assert mu.reconnecting is False


@pytest.mark.usefixtures("reset_meshtastic_globals")
@pytest.mark.asyncio
class TestReconnectFailureBackoff:
    async def test_connect_failure_logs_exception_and_clears_state(self):
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None
        attempt_count = 0

        def _connect_side_effect(_cfg, _force):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count >= 2:
                mu.shutting_down = True
            raise ConnectionError("connection refused")

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.connect_meshtastic",
                side_effect=_connect_side_effect,
            ),
            patch("mmrelay.meshtastic_utils.asyncio.sleep", new_callable=AsyncMock),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            await reconnect()

        assert attempt_count == 2
        assert any(
            "Reconnection attempt failed" in str(c.args)
            for c in mock_logger.exception.call_args_list
        )
        assert mu.reconnecting is False
        assert mu.reconnect_task_future is None

    async def test_reconnect_task_future_cleared_after_failure(self):
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None
        attempt_count = 0

        def _connect_side_effect(_cfg, _force):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count >= 2:
                mu.shutting_down = True
            raise RuntimeError("unexpected error")

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.connect_meshtastic",
                side_effect=_connect_side_effect,
            ),
            patch("mmrelay.meshtastic_utils.asyncio.sleep", new_callable=AsyncMock),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            await reconnect()

        assert attempt_count == 2
        assert mu.reconnect_task_future is None
        assert mu.reconnecting is False
