from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.meshtastic_utils import check_connection


def _make_health_config(connection_type="tcp", enabled=True, heartbeat=60):
    """
    Builds a nested configuration dictionary for meshtastic connection health checks.

    Parameters:
        connection_type (str): Connection transport type (e.g., "tcp" or "ble"). Defaults to "tcp".
        enabled (bool): Whether health checks are enabled. Defaults to True.
        heartbeat (int): Heartbeat interval in seconds used for health check scheduling. Defaults to 60.

    Returns:
        dict: Configuration mapping with keys "meshtastic" -> {"connection_type", "health_check": {"enabled", "heartbeat_interval"}}.
    """
    return {
        "meshtastic": {
            "connection_type": connection_type,
            "health_check": {"enabled": enabled, "heartbeat_interval": heartbeat},
        }
    }


def _sleep_and_shutdown(_seconds):
    """
    Mark the application as shutting down; intended as a placeholder to use where asyncio.sleep is expected.

    Parameters:
        _seconds (float): Ignored — present only to match the asyncio.sleep signature.
    """
    mu.shutting_down = True
    return None


@pytest.mark.asyncio
async def test_check_connection_health_disabled_returns(reset_meshtastic_globals):
    mu.config = _make_health_config(enabled=False)

    with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
        await check_connection()

    mock_logger.info.assert_called_with(
        "Connection health checks are disabled in configuration"
    )


@pytest.mark.asyncio
async def test_check_connection_ble_skips_health_checks(reset_meshtastic_globals):
    mu.config = _make_health_config(connection_type="ble")
    mu.meshtastic_client = MagicMock()

    with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
        await check_connection()

    mock_logger.debug.assert_any_call(
        "BLE connection uses real-time disconnection detection; periodic health checks disabled"
    )


@pytest.mark.asyncio
async def test_check_connection_metadata_probe_succeeds(reset_meshtastic_globals):
    mu.config = _make_health_config(connection_type="tcp")
    mu.meshtastic_client = MagicMock()

    loop = MagicMock()
    loop.run_in_executor = AsyncMock(return_value={})

    with (
        patch("mmrelay.meshtastic_utils.asyncio.get_running_loop", return_value=loop),
        patch(
            "mmrelay.meshtastic_utils.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=_sleep_and_shutdown,
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        await check_connection()

    loop.run_in_executor.assert_called_once()
    _, probe = loop.run_in_executor.call_args.args
    assert probe.func is mu._get_device_metadata
    assert probe.args == (mu.meshtastic_client,)
    assert probe.keywords == {"force_refresh": True, "raise_on_error": True}
    mock_logger.error.assert_not_called()


@pytest.mark.asyncio
async def test_check_connection_triggers_reconnect_on_probe_failure(
    reset_meshtastic_globals,
):
    mu.config = _make_health_config(connection_type="tcp")
    mu.meshtastic_client = MagicMock()

    loop = MagicMock()
    loop.run_in_executor = AsyncMock(side_effect=Exception("probe failed"))

    with (
        patch("mmrelay.meshtastic_utils.asyncio.get_running_loop", return_value=loop),
        patch("mmrelay.meshtastic_utils.on_lost_meshtastic_connection") as mock_lost,
        patch(
            "mmrelay.meshtastic_utils.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=_sleep_and_shutdown,
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        await check_connection()

    mock_lost.assert_called_once()
    mock_logger.error.assert_any_call(
        "Tcp connection health check failed: probe failed"
    )


@pytest.mark.asyncio
async def test_check_connection_skips_when_reconnecting(reset_meshtastic_globals):
    mu.config = _make_health_config(connection_type="tcp")
    mu.meshtastic_client = MagicMock()
    mu.reconnecting = True

    with (
        patch(
            "mmrelay.meshtastic_utils.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=_sleep_and_shutdown,
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        await check_connection()

    mock_logger.debug.assert_any_call(
        "Skipping connection check - reconnection in progress"
    )


@pytest.mark.asyncio
async def test_check_connection_skips_when_no_client(reset_meshtastic_globals):
    mu.config = _make_health_config(connection_type="tcp")
    mu.meshtastic_client = None

    with (
        patch(
            "mmrelay.meshtastic_utils.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=_sleep_and_shutdown,
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        await check_connection()

    mock_logger.debug.assert_any_call("Skipping connection check - no client available")


@pytest.mark.asyncio
async def test_check_connection_uses_legacy_heartbeat_interval(
    reset_meshtastic_globals,
):
    mu.config = _make_health_config(connection_type="tcp")
    mu.config["meshtastic"]["heartbeat_interval"] = 5
    mu.meshtastic_client = None

    with patch(
        "mmrelay.meshtastic_utils.asyncio.sleep",
        new_callable=AsyncMock,
        side_effect=_sleep_and_shutdown,
    ) as mock_sleep:
        await check_connection()

    mock_sleep.assert_awaited_once_with(5)
