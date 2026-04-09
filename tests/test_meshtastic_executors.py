from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as mu


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestShutdownSharedExecutors:
    def test_shutdown_cancels_pending_ble_future(self):
        from mmrelay.meshtastic.executors import _shutdown_shared_executors

        mock_future = MagicMock()
        mock_future.done.return_value = False
        mu._ble_future = mock_future
        mu._ble_future_address = "AA:BB:CC:DD:EE:FF"
        mu._ble_future_started_at = None
        mu._ble_future_timeout_secs = None

        mock_executor = MagicMock()
        mock_executor._shutdown = False
        mu._ble_executor = mock_executor

        _shutdown_shared_executors()

        mock_future.cancel.assert_called_once()

    def test_shutdown_cancels_pending_metadata_future(self):
        from mmrelay.meshtastic.executors import _shutdown_shared_executors

        mock_future = MagicMock()
        mock_future.done.return_value = False
        mu._metadata_future = mock_future
        mu._metadata_future_started_at = None

        mock_executor = MagicMock()
        mock_executor._shutdown = False
        mu._metadata_executor = mock_executor

        _shutdown_shared_executors()

        mock_future.cancel.assert_called_once()


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGetBleExecutor:
    def test_creates_when_none(self):
        from mmrelay.meshtastic.executors import _get_ble_executor

        mu._ble_executor = None
        with mu._ble_executor_lock:
            executor = _get_ble_executor()
        assert executor is not None
        assert isinstance(executor, ThreadPoolExecutor)
        executor.shutdown(wait=False)

    def test_recreates_when_shutdown(self):
        from mmrelay.meshtastic.executors import _get_ble_executor

        old = ThreadPoolExecutor(max_workers=1)
        old.shutdown(wait=False)
        old._shutdown = True
        mu._ble_executor = old
        with mu._ble_executor_lock:
            executor = _get_ble_executor()
        assert executor is not old
        assert isinstance(executor, ThreadPoolExecutor)
        executor.shutdown(wait=False)


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestGetMetadataExecutor:
    def test_creates_when_none(self):
        from mmrelay.meshtastic.executors import _get_metadata_executor

        mu._metadata_executor = None
        with mu._metadata_future_lock:
            executor = _get_metadata_executor()
        assert executor is not None
        assert isinstance(executor, ThreadPoolExecutor)
        executor.shutdown(wait=False)


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestRecordBleTimeout:
    def test_increments_count(self):
        from mmrelay.meshtastic.executors import _record_ble_timeout

        count = _record_ble_timeout("AA:BB")
        assert count == 1
        count = _record_ble_timeout("AA:BB")
        assert count == 2


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestClearBleFuture:
    def test_clears_matching_future(self):
        from mmrelay.meshtastic.executors import _clear_ble_future

        fut = Future()
        fut.set_result(None)
        mu._ble_future = fut
        mu._ble_future_address = "AA:BB:CC:DD:EE:FF"
        mu._ble_future_started_at = 123.0
        mu._ble_future_timeout_secs = 10.0
        mu._ble_timeout_counts = {"AA:BB:CC:DD:EE:FF": 3}

        _clear_ble_future(fut)

        assert mu._ble_future is None
        assert mu._ble_future_address is None
        assert "AA:BB:CC:DD:EE:FF" not in mu._ble_timeout_counts

    def test_ignores_non_matching_future(self):
        from mmrelay.meshtastic.executors import _clear_ble_future

        other = Future()
        other.set_result(None)
        mu._ble_future = other
        mu._ble_future_address = "AA:BB"
        mu._ble_future_started_at = 123.0

        wrong = Future()
        wrong.set_result(None)

        _clear_ble_future(wrong)

        assert mu._ble_future is other


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestResetMetadataExecutorForStaleProbe:
    def test_degraded_state_refuses_reset(self):
        from mmrelay.meshtastic.executors import (
            _reset_metadata_executor_for_stale_probe,
        )

        mu._metadata_executor_degraded = True
        _reset_metadata_executor_for_stale_probe()
        assert mu._metadata_executor_degraded is True

    def test_enters_degraded_at_threshold(self):
        from mmrelay.meshtastic.executors import (
            _reset_metadata_executor_for_stale_probe,
        )

        mu._metadata_executor_degraded = False
        mu._metadata_executor_orphaned_workers = mu.EXECUTOR_ORPHAN_THRESHOLD - 1
        mock_executor = MagicMock()
        mock_executor._shutdown = False
        mu._metadata_executor = mock_executor

        _reset_metadata_executor_for_stale_probe()

        assert mu._metadata_executor_degraded is True


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestResetExecutorDegradedState:
    def test_reset_all(self):
        from mmrelay.meshtastic.executors import reset_executor_degraded_state

        mu._ble_executor_degraded_addresses = {"AA:BB"}
        mu._ble_executor_orphaned_workers_by_address = {"AA:BB": 5}
        mu._metadata_executor_degraded = True
        mu._metadata_executor_orphaned_workers = 3

        mock_executor = MagicMock()
        mock_executor._shutdown = False
        mu._ble_executor = mock_executor
        mu._metadata_executor = MagicMock()
        mu._metadata_executor._shutdown = False

        result = reset_executor_degraded_state(reset_all=True)
        assert result is True
        assert len(mu._ble_executor_degraded_addresses) == 0
        assert mu._metadata_executor_degraded is False

    def test_reset_specific_address(self):
        from mmrelay.meshtastic.executors import reset_executor_degraded_state

        mu._ble_executor_degraded_addresses = {"AA:BB", "CC:DD"}
        mu._ble_executor_orphaned_workers_by_address = {"AA:BB": 5}
        mu._metadata_executor_degraded = True

        mock_executor = MagicMock()
        mock_executor._shutdown = False
        mu._ble_executor = mock_executor
        mu._metadata_executor = MagicMock()
        mu._metadata_executor._shutdown = False

        result = reset_executor_degraded_state(ble_address="AA:BB")
        assert result is True
        assert "AA:BB" not in mu._ble_executor_degraded_addresses
        assert "CC:DD" in mu._ble_executor_degraded_addresses

    def test_nothing_to_reset(self):
        from mmrelay.meshtastic.executors import reset_executor_degraded_state

        mu._ble_executor_degraded_addresses = set()
        mu._metadata_executor_degraded = False

        result = reset_executor_degraded_state(ble_address="AA:BB")
        assert result is False


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestClearMetadataFutureIfCurrent:
    def test_clears_matching(self):
        from mmrelay.meshtastic.executors import _clear_metadata_future_if_current

        fut = Future()
        fut.set_result(None)
        mu._metadata_future = fut
        mu._metadata_future_started_at = 100.0

        _clear_metadata_future_if_current(fut)
        assert mu._metadata_future is None
        assert mu._metadata_future_started_at is None

    def test_ignores_non_matching(self):
        from mmrelay.meshtastic.executors import _clear_metadata_future_if_current

        other = Future()
        other.set_result(None)
        mu._metadata_future = other
        mu._metadata_future_started_at = 100.0

        wrong = Future()
        wrong.set_result(None)
        _clear_metadata_future_if_current(wrong)
        assert mu._metadata_future is other


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestEnsureBleWorkerAvailable:
    def test_no_active_future_returns(self):
        from mmrelay.meshtastic.executors import _ensure_ble_worker_available

        mu._ble_future = None
        _ensure_ble_worker_available("AA:BB", operation="test")

    def test_done_future_returns(self):
        from mmrelay.meshtastic.executors import _ensure_ble_worker_available

        fut = Future()
        fut.set_result(None)
        mu._ble_future = fut
        _ensure_ble_worker_available("AA:BB", operation="test")

    def test_stale_future_gets_reset(self):
        from mmrelay.meshtastic.executors import _ensure_ble_worker_available

        fut = MagicMock()
        fut.done.return_value = False
        mu._ble_future = fut
        mu._ble_future_started_at = 0.0
        mu._ble_future_timeout_secs = 0.1
        mu._ble_future_address = "AA:BB"

        with (
            patch.object(mu, "_reset_ble_connection_gate_state"),
            patch.object(mu, "_maybe_reset_ble_executor"),
            patch.object(mu, "_record_ble_timeout", return_value=5),
        ):
            with pytest.raises(TimeoutError):
                _ensure_ble_worker_available("AA:BB", operation="test")


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestMaybeResetBleExecutor:
    def test_below_threshold_returns(self):
        from mmrelay.meshtastic.executors import _maybe_reset_ble_executor

        existing_executor = MagicMock()
        existing_executor._shutdown = False
        mu._ble_executor = existing_executor
        mu._ble_executor_degraded_addresses = set()
        _maybe_reset_ble_executor("AA:BB", timeout_count=1)
        assert mu._ble_executor is existing_executor

    def test_at_threshold_recreates(self):
        from mmrelay.meshtastic.executors import _maybe_reset_ble_executor

        threshold = mu.BLE_TIMEOUT_RESET_THRESHOLD
        mu._ble_executor_degraded_addresses = set()
        mu._ble_executor_orphaned_workers_by_address = {"AA:BB": 0}

        mock_executor = MagicMock()
        mock_executor._shutdown = False
        mu._ble_executor = mock_executor

        _maybe_reset_ble_executor("AA:BB", timeout_count=threshold)
        assert mu._ble_executor is not mock_executor

    def test_degraded_address_refuses(self):
        from mmrelay.meshtastic.executors import _maybe_reset_ble_executor

        mu._ble_executor_degraded_addresses = {"AA:BB"}
        old_executor = mu._ble_executor
        _maybe_reset_ble_executor("AA:BB", timeout_count=999)
        assert mu._ble_executor is old_executor


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestSubmitMetadataProbe:
    def test_returns_none_when_already_running(self):
        from mmrelay.meshtastic.executors import _submit_metadata_probe

        fut = MagicMock()
        fut.done.return_value = False
        mu._metadata_future = fut
        mu._metadata_future_started_at = mu.time.monotonic()

        result = _submit_metadata_probe(lambda: None)
        assert result is None

    def test_raises_on_degraded(self):
        from mmrelay.meshtastic.executors import _submit_metadata_probe

        mu._metadata_executor_degraded = True
        mu._metadata_future = None

        with pytest.raises(mu.MetadataExecutorDegradedError):
            _submit_metadata_probe(lambda: None)

    def test_submits_successfully(self):
        from mmrelay.meshtastic.executors import _submit_metadata_probe

        mu._metadata_executor_degraded = False
        mu._metadata_future = None
        mu._metadata_executor = ThreadPoolExecutor(max_workers=1)

        result = _submit_metadata_probe(lambda: 42)
        assert result is not None
        result.result(timeout=2.0)
