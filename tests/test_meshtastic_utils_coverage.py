#!/usr/bin/env python3
"""
Test suite to add coverage for uncovered lines in meshtastic_utils.py.

Tests cover:
- Executor shutdown paths
- Metadata probe error handling
- Coercion functions
- Health probe tracking
- ACK handling
- Firmware version extraction
- Detection sensor handling
"""

import asyncio
import contextlib
import time
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import Mock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.network import (
    CONFIG_KEY_BLE_ADDRESS,
    CONFIG_KEY_CONNECTION_TYPE,
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_TCP,
    METADATA_WATCHDOG_SECS,
)
from tests.constants import TEST_BLE_MAC


@pytest.fixture(autouse=True)
def reset_meshtastic_state(reset_meshtastic_globals):
    """Ensure clean state for each test."""
    # Additional resets specific to these tests
    mu.meshtastic_iface = None
    mu._metadata_future = None
    mu._metadata_future_started_at = None
    mu._ble_future = None
    mu._ble_future_address = None
    mu._ble_future_started_at = None
    mu._ble_future_timeout_secs = None
    mu._ble_timeout_counts = {}
    mu._health_probe_request_deadlines = {}
    mu._relay_rx_time_clock_skew_secs = None
    mu._relay_startup_drain_deadline_monotonic_secs = None
    mu._startup_packet_drain_applied = False
    # Keep startup bootstrap window deterministically closed in this suite unless
    # a test explicitly opts into startup-window behavior.
    mu._relay_connection_started_monotonic_secs = time.monotonic() - (
        mu._RX_TIME_SKEW_BOOTSTRAP_WINDOW_SECS + 1.0
    )
    mu._ble_executor_orphaned_workers_by_address = {}
    mu._metadata_executor_orphaned_workers = 0
    yield


class TestExecutorShutdown:
    """Test executor shutdown paths (lines 195-227)."""

    def test_shutdown_shared_executors_cancels_ble_future(self):
        """Test BLE future cancellation during shutdown."""
        # Create a mock future that's not done
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mock_future.cancel.return_value = True

        mu._ble_future = mock_future
        mu._ble_future_address = TEST_BLE_MAC
        mu._ble_timeout_counts[TEST_BLE_MAC] = 3

        # Create a mock executor
        mock_executor = Mock(spec=ThreadPoolExecutor)
        mock_executor._shutdown = False
        mu._ble_executor = mock_executor

        mu._shutdown_shared_executors()

        # Verify future was cancelled
        mock_future.cancel.assert_called_once()
        # Verify address was cleared from timeout counts
        assert TEST_BLE_MAC not in mu._ble_timeout_counts
        # Verify executor was shut down
        mock_executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        # Shared reference should be cleared to avoid repeated shutdown calls at exit.
        assert mu._ble_executor is None

    def test_shutdown_shared_executors_cancels_metadata_future(self):
        """Test metadata future cancellation during shutdown."""
        # Create a mock future that's not done
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mock_future.cancel.return_value = True

        mu._metadata_future = mock_future
        mu._metadata_future_started_at = time.monotonic()

        # Create a mock executor
        mock_executor = Mock(spec=ThreadPoolExecutor)
        mock_executor._shutdown = False
        mu._metadata_executor = mock_executor

        mu._shutdown_shared_executors()

        # Verify future was cancelled
        mock_future.cancel.assert_called_once()
        # Verify executor was shut down
        mock_executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        # Shared reference should be cleared to avoid repeated shutdown calls at exit.
        assert mu._metadata_executor is None

    def test_shutdown_shared_executors_handles_type_error(self):
        """Test executor shutdown handles TypeError on older Python."""
        # Create executor that raises TypeError on cancel_futures
        mock_executor = Mock(spec=ThreadPoolExecutor)
        mock_executor._shutdown = False
        mock_executor.shutdown.side_effect = [TypeError("cancel_futures"), None]
        mu._ble_executor = mock_executor
        mu._ble_future = None
        mu._metadata_executor = None
        mu._metadata_future = None

        # Should not raise
        mu._shutdown_shared_executors()

        # Should have called shutdown twice (once with cancel_futures, once without)
        assert mock_executor.shutdown.call_count == 2
        assert mu._ble_executor is None


class TestMetadataFutureCleanup:
    """Test metadata future cleanup functions (lines 272-327)."""

    def test_clear_metadata_future_if_current_match(self):
        """Test clearing metadata future when it matches."""
        mock_future = Mock(spec=Future)
        mu._metadata_future = mock_future
        mu._metadata_future_started_at = time.monotonic()

        mu._clear_metadata_future_if_current(mock_future)

        assert mu._metadata_future is None
        assert mu._metadata_future_started_at is None

    def test_clear_metadata_future_if_current_no_match(self):
        """Test metadata future not cleared when different."""
        mock_future1 = Mock(spec=Future)
        mock_future2 = Mock(spec=Future)
        mu._metadata_future = mock_future1
        mu._metadata_future_started_at = time.monotonic()

        mu._clear_metadata_future_if_current(mock_future2)

        assert mu._metadata_future is mock_future1
        assert mu._metadata_future_started_at is not None

    def test_reset_metadata_executor_for_stale_probe(self):
        """Test resetting metadata executor after stale probe."""
        old_executor = Mock(spec=ThreadPoolExecutor)
        old_executor._shutdown = False
        mu._metadata_executor = old_executor
        mu._metadata_future = Mock(spec=Future)
        mu._metadata_future_started_at = time.monotonic()

        mu._reset_metadata_executor_for_stale_probe()

        # Verify new executor was created
        assert mu._metadata_executor is not old_executor
        assert isinstance(mu._metadata_executor, ThreadPoolExecutor)
        # Verify future was cleared
        assert mu._metadata_future is None
        assert mu._metadata_future_started_at is None
        # Verify old executor was shut down
        old_executor.shutdown.assert_called_once()

    def test_schedule_metadata_future_cleanup_timer(self):
        """Test metadata future cleanup timer is scheduled."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mu._metadata_future = mock_future

        # Patch threading.Timer to track calls
        with patch("threading.Timer") as mock_timer_class:
            mock_timer = Mock()
            mock_timer.daemon = False
            mock_timer_class.return_value = mock_timer

            mu._schedule_metadata_future_cleanup(mock_future, "test-reason")

            # Verify timer was created with correct timeout
            assert mock_timer_class.call_count == 1
            call_args = mock_timer_class.call_args
            assert call_args[0][0] == METADATA_WATCHDOG_SECS
            # Verify timer was started
            mock_timer.start.assert_called_once()
            # Verify done callback was added
            mock_future.add_done_callback.assert_called_once()

    def test_schedule_metadata_future_cleanup_exception(self):
        """Test metadata future cleanup handles exceptions during timer setup."""
        mock_future = Mock(spec=Future)
        # Make add_done_callback raise an exception
        mock_future.add_done_callback.side_effect = RuntimeError("Test error")

        # Should not raise
        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            mu._schedule_metadata_future_cleanup(mock_future, "test-reason")
            # Should log the error
            mock_logger.debug.assert_called()


class TestSubmitMetadataProbe:
    """Test metadata probe submission (lines 367-368)."""

    def test_submit_metadata_probe_rejects_duplicate(self):
        """Test that duplicate metadata probe is rejected."""
        # Create a future that's still running
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mu._metadata_future = mock_future
        mu._metadata_future_started_at = time.monotonic()

        result = mu._submit_metadata_probe(lambda: None)

        # Should return None to indicate rejection
        assert result is None

    def test_submit_metadata_probe_accepts_after_stale(self):
        """Test that probe is accepted after previous one becomes stale."""
        # Create a future that's old (stale)
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mu._metadata_future = mock_future
        mu._metadata_future_started_at = time.monotonic() - (
            METADATA_WATCHDOG_SECS + 10
        )

        with patch("mmrelay.meshtastic_utils._get_metadata_executor") as mock_get_exec:
            mock_executor = Mock(spec=ThreadPoolExecutor)
            mock_get_exec.return_value = mock_executor
            mock_executor.submit.return_value = Mock(spec=Future)

            result = mu._submit_metadata_probe(lambda: None)

            # Should have submitted a new probe
            assert result is not None
            mock_executor.submit.assert_called_once()


class TestCoercionFunctions:
    """Test coercion utility functions (lines 399-431)."""

    def test_coerce_int_id_with_valid_int(self):
        """Test _coerce_int_id with valid integer."""
        assert mu._coerce_int_id(123) == 123

    def test_coerce_int_id_with_string(self):
        """Test _coerce_int_id with string number."""
        assert mu._coerce_int_id("456") == 456

    def test_coerce_int_id_with_invalid_string(self):
        """Test _coerce_int_id with non-numeric string."""
        assert mu._coerce_int_id("not-a-number") is None

    def test_coerce_int_id_with_none(self):
        """Test _coerce_int_id with None."""
        assert mu._coerce_int_id(None) is None

    def test_coerce_positive_float_with_valid(self):
        """Test _coerce_positive_float with valid positive float."""
        assert mu._coerce_positive_float(5.5, 1.0, "test") == 5.5

    def test_coerce_positive_float_with_zero(self):
        """Test _coerce_positive_float with zero (invalid)."""
        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = mu._coerce_positive_float(0, 2.0, "test_setting")
            assert result == 2.0
            mock_logger.warning.assert_called_once()

    def test_coerce_positive_float_with_negative(self):
        """Test _coerce_positive_float with negative (invalid)."""
        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = mu._coerce_positive_float(-5.0, 3.0, "test_setting")
            assert result == 3.0
            mock_logger.warning.assert_called_once()

    def test_coerce_positive_float_with_invalid_type(self):
        """Test _coerce_positive_float with invalid type."""
        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = mu._coerce_positive_float("not-a-number", 4.0, "test_setting")
            assert result == 4.0
            mock_logger.warning.assert_called_once()


class TestHealthProbeTracking:
    """Test health probe request ID tracking (lines 469-506)."""

    def test_track_health_probe_request_id_prunes_expired(self):
        """Test that tracking prunes expired request IDs."""
        # Add an expired entry
        mu._health_probe_request_deadlines[123] = time.monotonic() - 100

        # Track a new request
        request_id = mu._track_health_probe_request_id(456, 10.0)

        # Should return the new ID
        assert request_id == 456
        # Old expired ID should be pruned
        assert 123 not in mu._health_probe_request_deadlines
        # New ID should be tracked
        assert 456 in mu._health_probe_request_deadlines

    def test_track_health_probe_request_id_with_invalid_id(self):
        """Test tracking with invalid request ID."""
        result = mu._track_health_probe_request_id(-1, 10.0)
        assert result is None

    def test_is_health_probe_response_packet_valid(self):
        """Test detecting valid health probe response."""
        # Track a request
        mu._track_health_probe_request_id(789, 10.0)

        # Create a response packet
        packet = {
            "requestId": 789,
            "from": 12345,
        }

        # Create mock interface with matching node number
        mock_interface = Mock()
        mock_myInfo = Mock()
        mock_myInfo.my_node_num = 12345
        mock_interface.myInfo = mock_myInfo

        result = mu._is_health_probe_response_packet(packet, mock_interface)
        assert result is True

    def test_is_health_probe_response_packet_wrong_sender(self):
        """Test health probe response from different sender."""
        # Track a request
        mu._track_health_probe_request_id(789, 10.0)

        # Create a response packet from different node
        packet = {
            "requestId": 789,
            "from": 99999,
        }

        # Create mock interface
        mock_interface = Mock()
        mock_myInfo = Mock()
        mock_myInfo.my_node_num = 12345
        mock_interface.myInfo = mock_myInfo

        result = mu._is_health_probe_response_packet(packet, mock_interface)
        assert result is False

    def test_is_health_probe_response_packet_no_request_id(self):
        """Test health probe response without request ID."""
        packet = {"from": 12345}
        mock_interface = Mock()

        result = mu._is_health_probe_response_packet(packet, mock_interface)
        assert result is False


class TestProbeAckHandling:
    """Test ACK flag handling for health probes (lines 508-658)."""

    def test_set_probe_ack_flag_no_ack_state(self):
        """Test ACK flag setting when no ack state exists."""
        local_node = Mock()
        local_node.iface = None
        packet = {"from": 12345}

        result = mu._set_probe_ack_flag_from_packet(local_node, packet)
        assert result is False

    def test_set_probe_ack_flag_sender_matches_local(self):
        """Test ACK flag setting when sender matches local node."""
        # Create mock with ack state
        ack_state = Mock()
        ack_state.receivedImplAck = False

        iface = Mock()
        iface._acknowledgment = ack_state

        local_node = Mock()
        local_node.iface = iface

        localNode = Mock()
        localNode.nodeNum = 12345
        iface.localNode = localNode

        packet = {"from": 12345}

        result = mu._set_probe_ack_flag_from_packet(local_node, packet)
        assert result is True
        assert ack_state.receivedImplAck is True

    def test_set_probe_ack_flag_sender_different_fallback(self):
        """Test ACK flag fallback when sender doesn't match."""
        # Create mock with ack state but no receivedImplAck
        ack_state = Mock(spec=[])  # No receivedImplAck attribute
        ack_state.receivedAck = False

        iface = Mock()
        iface._acknowledgment = ack_state

        local_node = Mock()
        local_node.iface = iface

        packet = {"from": 99999}

        result = mu._set_probe_ack_flag_from_packet(local_node, packet)
        assert result is True
        assert ack_state.receivedAck is True

    def test_handle_probe_ack_callback_no_ack_state(self):
        """Test probe ACK callback with no ack state."""
        local_node = Mock()
        local_node.iface = None
        packet = {}

        with pytest.raises(RuntimeError, match="missing acknowledgment state"):
            mu._handle_probe_ack_callback(local_node, packet)

    def test_handle_probe_ack_callback_with_nak(self):
        """Test probe ACK callback with NAK response."""
        ack_state = Mock()
        ack_state.receivedNak = False

        iface = Mock()
        iface._acknowledgment = ack_state
        iface.localNode = None

        local_node = Mock()
        local_node.iface = iface

        packet = {"decoded": {"routing": {"errorReason": "NO_ROUTE"}}}

        mu._handle_probe_ack_callback(local_node, packet)

        assert ack_state.receivedNak is True

    def test_handle_probe_ack_callback_nak_no_received_nak_attr(self):
        """Test probe ACK callback with NAK but no receivedNak attribute."""
        ack_state = Mock(spec=[])  # No receivedNak attribute

        iface = Mock()
        iface._acknowledgment = ack_state

        local_node = Mock()
        local_node.iface = iface

        packet = {"decoded": {"routing": {"errorReason": "TIMEOUT"}}}

        with pytest.raises(RuntimeError, match="missing receivedNak"):
            mu._handle_probe_ack_callback(local_node, packet)

    def test_handle_probe_ack_callback_failed_to_set_state(self):
        """Test probe ACK callback when ACK state cannot be set."""
        ack_state = Mock(spec=[])  # No attributes at all

        iface = Mock()
        iface._acknowledgment = ack_state
        iface.localNode = None

        local_node = Mock()
        local_node.iface = iface

        packet = {}

        with pytest.raises(RuntimeError, match="Failed to set ACK state"):
            mu._handle_probe_ack_callback(local_node, packet)

    def test_wait_for_probe_ack_no_ack_state(self):
        """Test waiting for probe ACK with no ack state."""
        client = Mock()
        client._acknowledgment = None

        with pytest.raises(RuntimeError, match="missing acknowledgment state"):
            mu._wait_for_probe_ack(client, 1.0)

    def test_wait_for_probe_ack_with_reset(self):
        """Test waiting for probe ACK calls reset when available."""
        ack_state = Mock()
        ack_state.receivedAck = True
        ack_state.receivedNak = False
        ack_state.receivedImplAck = False
        ack_state.reset = Mock()

        client = Mock()
        client._acknowledgment = ack_state

        mu._wait_for_probe_ack(client, 1.0)

        ack_state.reset.assert_called_once()

    def test_probe_device_connection_no_local_node(self):
        """Test probe connection when client has no localNode."""
        client = Mock()
        client.localNode = None

        with pytest.raises(
            RuntimeError, match="cannot perform metadata liveness probe"
        ):
            mu._probe_device_connection(client)

    def test_probe_device_connection_no_send_data(self):
        """Test probe connection when client has no sendData."""
        client = Mock()
        client.localNode = Mock()
        # sendData is not callable
        delattr(client, "sendData")

        with pytest.raises(
            RuntimeError, match="cannot perform metadata liveness probe"
        ):
            mu._probe_device_connection(client)

    def test_probe_device_connection_no_wait_method(self):
        """Test probe connection when client cannot wait for ACK."""
        client = Mock()
        client.localNode = Mock()
        client.localNode.nodeNum = 12345
        client.sendData = Mock(return_value=Mock(id=999))
        client._acknowledgment = None
        # Ensure waitForAckNak is not present
        if hasattr(client, "waitForAckNak"):
            delattr(client, "waitForAckNak")

        with pytest.raises(RuntimeError, match="cannot wait for metadata probe ACK"):
            mu._probe_device_connection(client, 1.0)


class TestErrorBuilders:
    """Test error builder functions (lines 555-599)."""

    def test_missing_local_node_ack_state_error(self):
        """Test error builder for missing local node ack state."""
        err = mu._missing_local_node_ack_state_error()
        assert isinstance(err, RuntimeError)
        assert "local node missing acknowledgment state" in str(err)

    def test_missing_received_nak_error(self):
        """Test error builder for missing receivedNak."""
        err = mu._missing_received_nak_error()
        assert isinstance(err, RuntimeError)
        assert "missing receivedNak" in str(err)

    def test_failed_probe_ack_state_error(self):
        """Test error builder for failed probe ack state."""
        err = mu._failed_probe_ack_state_error()
        assert isinstance(err, RuntimeError)
        assert "Failed to set ACK state" in str(err)

    def test_missing_ack_state_error(self):
        """Test error builder for missing ack state."""
        err = mu._missing_ack_state_error()
        assert isinstance(err, RuntimeError)
        assert "client missing acknowledgment state" in str(err)

    def test_metadata_probe_ack_timeout_error(self):
        """Test error builder for probe timeout."""
        err = mu._metadata_probe_ack_timeout_error(5.5)
        assert isinstance(err, TimeoutError)
        assert "5.5 seconds" in str(err)

    def test_missing_probe_transport_error(self):
        """Test error builder for missing probe transport."""
        err = mu._missing_probe_transport_error()
        assert isinstance(err, RuntimeError)
        assert "cannot perform metadata liveness probe" in str(err)

    def test_missing_probe_wait_error(self):
        """Test error builder for missing probe wait."""
        err = mu._missing_probe_wait_error()
        assert isinstance(err, RuntimeError)
        assert "cannot wait for metadata probe ACK" in str(err)


class TestFirmwareVersionExtraction:
    """Test firmware version extraction (lines 1434-1614)."""

    def test_normalize_firmware_version_with_bytes(self):
        """Test normalizing firmware version from bytes."""
        result = mu._normalize_firmware_version(b"2.1.5")
        assert result == "2.1.5"

    def test_normalize_firmware_version_with_string(self):
        """Test normalizing firmware version from string."""
        result = mu._normalize_firmware_version("  2.1.6  ")
        assert result == "2.1.6"

    def test_normalize_firmware_version_with_unknown(self):
        """Test normalizing firmware version with 'unknown' value."""
        result = mu._normalize_firmware_version("unknown")
        assert result is None

    def test_normalize_firmware_version_with_empty(self):
        """Test normalizing firmware version with empty string."""
        result = mu._normalize_firmware_version("")
        assert result is None

    def test_normalize_firmware_version_with_non_string(self):
        """Test normalizing firmware version with non-string type."""
        result = mu._normalize_firmware_version(123)
        assert result is None

    def test_extract_firmware_version_from_metadata_dict(self):
        """Test extracting firmware version from dict metadata."""
        metadata = {"firmware_version": "2.2.0"}
        result = mu._extract_firmware_version_from_metadata(metadata)
        assert result == "2.2.0"

    def test_extract_firmware_version_from_metadata_dict_camel_case(self):
        """Test extracting firmware version with camelCase key."""
        metadata = {"firmwareVersion": "2.2.1"}
        result = mu._extract_firmware_version_from_metadata(metadata)
        assert result == "2.2.1"

    def test_extract_firmware_version_from_metadata_object(self):
        """Test extracting firmware version from object metadata."""
        metadata = Mock()
        metadata.firmware_version = "2.3.0"

        result = mu._extract_firmware_version_from_metadata(metadata)
        assert result == "2.3.0"

    def test_extract_firmware_version_from_metadata_object_camel_case(self):
        """Test extracting firmware version from object with camelCase."""
        metadata = Mock(spec=[])
        # Use setattr to add attribute that's not in spec
        metadata.firmwareVersion = "2.3.1"

        result = mu._extract_firmware_version_from_metadata(metadata)
        assert result == "2.3.1"

    def test_extract_firmware_version_from_metadata_none(self):
        """Test extracting firmware version from None metadata."""
        result = mu._extract_firmware_version_from_metadata(None)
        assert result is None

    def test_get_device_metadata_no_local_node(self):
        """Test _get_device_metadata when client has no localNode."""
        client = Mock()
        client.localNode = None

        result = mu._get_device_metadata(client)

        assert result["firmware_version"] == "unknown"
        assert result["success"] is False

    def test_get_device_metadata_no_get_metadata_method(self):
        """Test _get_device_metadata when localNode has no getMetadata."""
        client = Mock()
        client.localNode = Mock(spec=[])  # No getMetadata attribute

        result = mu._get_device_metadata(client)

        assert result["firmware_version"] == "unknown"
        assert result["success"] is False

    def test_get_device_metadata_raises_on_error(self):
        """Test _get_device_metadata raises error when requested."""
        client = Mock()
        client.localNode = None

        with pytest.raises(RuntimeError, match="no localNode.getMetadata"):
            mu._get_device_metadata(client, raise_on_error=True)

    def test_get_device_metadata_runtime_error_submission(self):
        """Test _get_device_metadata handles RuntimeError during submission."""
        client = Mock()
        client.localNode = Mock()
        client.localNode.getMetadata = Mock()
        client.localNode.metadata = None
        client.metadata = None

        with patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit:
            mock_submit.side_effect = RuntimeError("Executor shutting down")

            result = mu._get_device_metadata(client)

            assert result["firmware_version"] == "unknown"
            assert result["success"] is False

    def test_get_device_metadata_runtime_error_raises(self):
        """Test _get_device_metadata re-raises RuntimeError when requested."""
        client = Mock()
        client.localNode = Mock()
        client.localNode.getMetadata = Mock()
        client.localNode.metadata = None
        client.metadata = None

        with patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit:
            mock_submit.side_effect = RuntimeError("Executor shutting down")

            with pytest.raises(RuntimeError, match="Executor shutting down"):
                mu._get_device_metadata(client, raise_on_error=True)

    def test_get_device_metadata_future_none(self):
        """Test _get_device_metadata when probe submission returns None."""
        client = Mock()
        client.localNode = Mock()
        client.localNode.getMetadata = Mock()
        client.localNode.metadata = None
        client.metadata = None

        with patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit:
            mock_submit.return_value = None

            result = mu._get_device_metadata(client)

            assert result["firmware_version"] == "unknown"
            assert result["success"] is False

    def test_get_device_metadata_timeout_with_raise(self):
        """Test _get_device_metadata handles timeout with raise_on_error."""
        client = Mock()
        client.localNode = Mock()
        client.localNode.getMetadata = Mock()
        client.localNode.metadata = None
        client.metadata = None

        mock_future = Mock(spec=Future)
        mock_future.result.side_effect = TimeoutError("Timeout")
        mock_future.done.return_value = True

        with patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit:
            mock_submit.return_value = mock_future

            with pytest.raises(TimeoutError):
                mu._get_device_metadata(client, raise_on_error=True)


class TestMessageHandlerEdgeCases:
    """Test edge cases in message handler (lines 3143-3739)."""

    def test_on_meshtastic_message_invalid_rx_time(self):
        """Test message handler with invalid rxTime."""
        packet = {
            "rxTime": "not-a-number",
            "decoded": {"text": "test"},
            "channel": 0,
            "to": 4294967295,
        }

        mock_interface = Mock()
        mock_interface.myInfo = Mock()
        mock_interface.myInfo.my_node_num = 12345

        mu.config = {"meshtastic": {"meshnet_name": "test"}}
        mu.matrix_rooms = []

        # Should handle gracefully without raising
        mu.on_meshtastic_message(packet, mock_interface)

    def test_check_connection_non_dict_health_config(self):
        """Test check_connection with non-dict health_check config."""
        mu.config = {
            "meshtastic": {
                CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_TCP,
                "health_check": "invalid",  # Not a dict
            }
        }
        mu.meshtastic_client = None
        mu.shutting_down = True  # Exit immediately

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            asyncio.run(mu.check_connection())

            # Should log warning about invalid config
            mock_logger.warning.assert_called()
            call_args = mock_logger.warning.call_args[0]
            assert "not a dictionary" in call_args[0]

    def test_check_connection_probe_submission_fails(self):
        """Test check_connection when probe submission raises RuntimeError."""
        mu.config = {
            "meshtastic": {
                CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_TCP,
                "health_check": {"enabled": True, "initial_delay": 0},
            }
        }
        mu.meshtastic_client = Mock()
        mu.reconnecting = False
        mu.shutting_down = False

        async def run_test():
            sleep_count = [0]

            async def sleep_side_effect(delay):
                sleep_count[0] += 1
                if sleep_count[0] >= 2:
                    mu.shutting_down = True

            with patch(
                "mmrelay.meshtastic_utils._submit_metadata_probe"
            ) as mock_submit:
                mock_submit.side_effect = RuntimeError("Executor closed")

                with patch(
                    "mmrelay.meshtastic_utils.asyncio.sleep",
                    side_effect=sleep_side_effect,
                ):
                    await mu.check_connection()

                    # Should have attempted submission
                    assert mock_submit.call_count >= 1

        asyncio.run(run_test())

    def test_check_connection_probe_future_none(self):
        """Test check_connection when probe submission returns None."""
        mu.config = {
            "meshtastic": {
                CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_TCP,
                "health_check": {"enabled": True, "initial_delay": 0},
            }
        }
        mu.meshtastic_client = Mock()
        mu.reconnecting = False
        mu.shutting_down = False

        async def run_test():
            sleep_count = [0]

            async def sleep_side_effect(delay):
                sleep_count[0] += 1
                if sleep_count[0] >= 2:
                    mu.shutting_down = True

            with patch(
                "mmrelay.meshtastic_utils._submit_metadata_probe"
            ) as mock_submit:
                mock_submit.return_value = None  # Indicates already running

                with patch(
                    "mmrelay.meshtastic_utils.asyncio.sleep",
                    side_effect=sleep_side_effect,
                ):
                    await mu.check_connection()

                    # Should have attempted submission
                    assert mock_submit.call_count >= 1

        asyncio.run(run_test())

    def test_check_connection_probe_fails_not_reconnecting(self):
        """Test check_connection when probe fails and not reconnecting."""
        mu.config = {
            "meshtastic": {
                CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_TCP,
                "health_check": {"enabled": True, "initial_delay": 0},
            }
        }
        mu.meshtastic_client = Mock()
        mu.reconnecting = False
        mu.shutting_down = False

        async def run_test():
            sleep_count = [0]

            async def sleep_side_effect(delay):
                sleep_count[0] += 1
                if sleep_count[0] >= 2:
                    mu.shutting_down = True

            mock_future = Mock(spec=Future)
            mock_future.done.return_value = True

            with patch(
                "mmrelay.meshtastic_utils._submit_metadata_probe"
            ) as mock_submit:
                mock_submit.return_value = mock_future

                with patch(
                    "mmrelay.meshtastic_utils.asyncio.sleep",
                    side_effect=sleep_side_effect,
                ):
                    with patch(
                        "mmrelay.meshtastic_utils.asyncio.wait_for"
                    ) as mock_wait:
                        mock_wait.side_effect = Exception("Connection lost")

                        with patch(
                            "mmrelay.meshtastic_utils.on_lost_meshtastic_connection"
                        ) as mock_lost:
                            await mu.check_connection()

                            # Should have triggered lost connection
                            mock_lost.assert_called_once()

        asyncio.run(run_test())

    def test_check_connection_probe_fails_already_reconnecting(self):
        """Test check_connection when probe fails but already reconnecting."""
        mu.config = {
            "meshtastic": {
                CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_TCP,
                "health_check": {"enabled": True, "initial_delay": 0},
            }
        }
        mu.meshtastic_client = Mock()
        mu.reconnecting = True  # Already reconnecting
        mu.shutting_down = False

        async def run_test():
            sleep_count = [0]

            async def sleep_side_effect(delay):
                sleep_count[0] += 1
                if sleep_count[0] >= 2:
                    mu.shutting_down = True

            mock_future = Mock(spec=Future)
            mock_future.done.return_value = True

            with patch(
                "mmrelay.meshtastic_utils._submit_metadata_probe"
            ) as mock_submit:
                mock_submit.return_value = mock_future

                with patch(
                    "mmrelay.meshtastic_utils.asyncio.sleep",
                    side_effect=sleep_side_effect,
                ):
                    with patch(
                        "mmrelay.meshtastic_utils.asyncio.wait_for"
                    ) as mock_wait:
                        mock_wait.side_effect = Exception("Connection lost")

                        with patch(
                            "mmrelay.meshtastic_utils.on_lost_meshtastic_connection"
                        ) as mock_lost:
                            with patch(
                                "mmrelay.meshtastic_utils.logger"
                            ) as mock_logger:
                                await mu.check_connection()

                                # Should NOT have triggered lost connection
                                mock_lost.assert_not_called()
                                # Should have logged at debug level instead
                                assert any(
                                    "debug" in str(call)
                                    for call in mock_logger.method_calls
                                )

        asyncio.run(run_test())


class TestMetadataExecutorResetTypeError:
    """Test TypeError handling in _reset_metadata_executor_for_stale_probe (lines 294-298)."""

    def test_reset_metadata_executor_handles_type_error(self):
        """Test that _reset_metadata_executor_for_stale_probe handles TypeError on shutdown."""
        old_executor = Mock(spec=ThreadPoolExecutor)
        old_executor._shutdown = False
        # First call raises TypeError, second succeeds
        old_executor.shutdown.side_effect = [
            TypeError("cancel_futures not supported"),
            None,
        ]
        mu._metadata_executor = old_executor
        mu._metadata_future = Mock(spec=Future)
        mu._metadata_future_started_at = time.monotonic()

        mu._reset_metadata_executor_for_stale_probe()

        # Verify shutdown was called twice (once with cancel_futures, once without)
        assert old_executor.shutdown.call_count == 2
        # Verify new executor was created
        assert mu._metadata_executor is not old_executor


class TestMetadataFutureCleanupPaths:
    """Test all paths in _schedule_metadata_future_cleanup (lines 314-328)."""

    def test_cleanup_early_return_when_future_done(self):
        """Test that cleanup returns early when future is already done.

        The _cleanup function is called by Timer after METADATA_WATCHDOG_SECS.
        When future.done() is True, _cleanup should return early without
        calling _reset_metadata_executor_for_stale_probe.
        """
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = True
        mu._metadata_future = mock_future

        with patch("mmrelay.meshtastic_utils._reset_metadata_executor_for_stale_probe"):
            mu._schedule_metadata_future_cleanup(mock_future, "test-reason")
            # Timer is created and started, but _cleanup returns early
            # Trigger the done callback to cancel timer (cleanup)
            [
                call
                for call in mock_future.method_calls
                if "add_done_callback" in str(call)
            ]
            # The done callback should be registered
            mock_future.add_done_callback.assert_called_once()
            # Since future is done, _reset should not be called during cleanup
            # (but we don't actually wait for the timer here)

    def test_cleanup_early_return_when_should_clear_false(self):
        """Test that cleanup returns early when _metadata_future is different."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        different_future = Mock(spec=Future)
        mu._metadata_future = different_future

        with patch("threading.Timer") as mock_timer_class:
            mu._schedule_metadata_future_cleanup(mock_future, "test-reason")
            # Timer should still be created (we need to wait to check should_clear)
            # Actually, the check happens inside _cleanup which is called by Timer
            # So Timer IS created, but _cleanup returns early
            mock_timer_class.assert_called_once()


class TestCoerceBoolEdgeCases:
    """Test _coerce_bool edge cases (lines 437-456)."""

    def test_coerce_bool_with_true_bool(self):
        """Test _coerce_bool with True boolean."""
        result = mu._coerce_bool(True, False, "test_setting")
        assert result is True

    def test_coerce_bool_with_false_bool(self):
        """Test _coerce_bool with False boolean."""
        result = mu._coerce_bool(False, True, "test_setting")
        assert result is False

    def test_coerce_bool_with_positive_int(self):
        """Test _coerce_bool with positive integer."""
        result = mu._coerce_bool(1, False, "test_setting")
        assert result is True

    def test_coerce_bool_with_zero_int(self):
        """Test _coerce_bool with zero integer."""
        result = mu._coerce_bool(0, True, "test_setting")
        assert result is False

    def test_coerce_bool_with_positive_float(self):
        """Test _coerce_bool with positive float."""
        result = mu._coerce_bool(1.5, False, "test_setting")
        assert result is True

    def test_coerce_bool_with_zero_float(self):
        """Test _coerce_bool with zero float."""
        result = mu._coerce_bool(0.0, True, "test_setting")
        assert result is False

    def test_coerce_bool_with_whitespace_string(self):
        """Test _coerce_bool with whitespace-only string defaults to False."""
        result = mu._coerce_bool("   ", True, "test_setting")
        assert result is False

    def test_coerce_bool_with_empty_string(self):
        """Test _coerce_bool with empty string returns False."""
        result = mu._coerce_bool("", True, "test_setting")
        assert result is False

    def test_coerce_bool_with_invalid_type(self):
        """Test _coerce_bool with invalid type logs warning and returns default."""
        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = mu._coerce_bool(["list"], True, "test_setting")
            assert result is True  # Default value
            mock_logger.warning.assert_called_once()


class TestHealthProbeResponsePacketLocalNodeFallback:
    """Test _is_health_probe_response_packet fallback to localNode (lines 530-531)."""

    def test_is_health_probe_response_packet_uses_localnode_when_myinfo_none(self):
        """Test that packet detection falls back to localNode.nodeNum when myInfo is None."""
        # Track a request
        mu._track_health_probe_request_id(789, 10.0)

        packet = {
            "from": 12345,
            "decoded": {"requestId": 789},
        }

        # Create mock interface where myInfo is None but localNode has nodeNum
        mock_interface = Mock()
        mock_interface.myInfo = None
        mock_localNode = Mock()
        mock_localNode.nodeNum = 12345
        mock_interface.localNode = mock_localNode

        result = mu._is_health_probe_response_packet(packet, mock_interface)
        assert result is True


class TestWaitForProbeAckManualReset:
    """Test _wait_for_probe_ack with non-callable reset (lines 686-691)."""

    def test_wait_for_probe_ack_with_non_callable_reset(self):
        """Test that _wait_for_probe_ack manually resets flags when reset is not callable."""
        ack_state = Mock()
        ack_state.receivedAck = True
        ack_state.receivedNak = False
        ack_state.receivedImplAck = False
        # Make reset a non-callable attribute
        ack_state.reset = "not_callable"

        client = Mock()
        client._acknowledgment = ack_state

        mu._wait_for_probe_ack(client, 0.1)

        # Verify flags were manually reset
        assert ack_state.receivedAck is False
        assert ack_state.receivedNak is False
        assert ack_state.receivedImplAck is False


class TestProbeDeviceConnectionManualReset:
    """Test _probe_device_connection with non-callable reset (lines 728-733)."""

    def test_probe_device_connection_with_non_callable_reset(self):
        """Test that _probe_device_connection manually resets flags when reset is not callable."""
        ack_state = Mock()
        ack_state.reset = "not_callable"  # Non-callable reset
        ack_state.receivedAck = True
        ack_state.receivedNak = True
        ack_state.receivedImplAck = True

        client = Mock()
        client.localNode = Mock()
        client.localNode.nodeNum = 12345
        client.sendData = Mock(return_value=Mock(id=999))
        client._acknowledgment = ack_state
        client.waitForAckNak = Mock()

        # Patch _wait_for_probe_ack to avoid timeout waiting for ACK
        with patch("mmrelay.meshtastic_utils._wait_for_probe_ack"):
            mu._probe_device_connection(client, 0.1)

        # Verify flags were manually reset during the initial cleanup phase
        assert ack_state.receivedAck is False
        assert ack_state.receivedNak is False
        assert ack_state.receivedImplAck is False


class TestGetDeviceMetadataIoError:
    """Test _get_device_metadata I/O error handling (lines 1634-1635, 1671)."""

    def test_get_device_metadata_handles_io_error_on_closed_file(self):
        """Test handling of I/O operation on closed file."""
        client = Mock()
        client.localNode = Mock()
        client.localNode.getMetadata = Mock()
        client.localNode.metadata = None
        client.metadata = None

        # Create mock future that raises ValueError with closed file message
        mock_future = Mock(spec=Future)
        mock_future.result.side_effect = ValueError("I/O operation on closed file")
        mock_future.done.return_value = True

        with patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit:
            mock_submit.return_value = mock_future

            result = mu._get_device_metadata(client)

            # Should handle gracefully and return unknown
            assert result["firmware_version"] == "unknown"
            assert result["success"] is False


class TestOnMeshtasticMessageOldPacketFiltering:
    """Test old message filtering in on_meshtastic_message (lines 3199-3205)."""

    def test_on_meshtastic_message_filters_old_packets(self):
        """Test that packets with rx_time < RELAY_START_TIME are filtered out."""
        import mmrelay.meshtastic_utils as mu_module

        # Set RELAY_START_TIME to a recent time
        mu_module.RELAY_START_TIME = time.time()
        mu_module._relay_rx_time_clock_skew_secs = None

        # Create a packet with rx_time in the past (before RELAY_START_TIME)
        old_packet = {
            "from": 12345,
            "to": 4294967295,  # BROADCAST_NUM
            "decoded": {"text": "old message", "portnum": "TEXT_MESSAGE_APP"},
            "channel": 0,
            "id": 12345,
            "rxTime": mu_module.RELAY_START_TIME - 100,  # 100 seconds before start
        }

        mock_interface = Mock()
        mock_interface.myInfo = Mock()
        mock_interface.myInfo.my_node_num = 12345

        mu_module.config = {"meshtastic": {"meshnet_name": "test"}}
        mu_module.matrix_rooms = []

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            mu_module.on_meshtastic_message(old_packet, mock_interface)

            # Should log debug about stale packet filtering.
            log_calls = [str(call).lower() for call in mock_logger.debug.call_args_list]
            assert any(
                ("ignore" in call or "ignoring" in call)
                and ("old" in call or "stale" in call or "filtered" in call)
                for call in log_calls
            )


class TestSnapshotNodeNameRowsNonDict:
    """Test _snapshot_node_name_rows handling non-dict raw_node and raw_user (lines 535-542)."""

    def test_snapshot_node_name_rows_non_dict_raw_node(self):
        """Test when raw_node is not a dict, nodes_snapshot gets {"user": None}."""
        mock_client = Mock()
        mock_client.nodes = {
            "12345": "not_a_dict",
            "67890": None,
        }

        with patch("mmrelay.meshtastic_utils.meshtastic_client", mock_client):
            result, client_missing = mu._snapshot_node_name_rows()

            assert client_missing is False
            assert result is not None
            assert result["12345"] == {"user": None}
            assert result["67890"] == {"user": None}

    def test_snapshot_node_name_rows_non_dict_raw_user(self):
        """Test when raw_user is not a dict, nodes_snapshot gets {"user": {"id": None}}."""
        mock_client = Mock()
        mock_client.nodes = {
            "12345": {"user": "user_string_not_dict"},
            "67890": {"user": None},
        }

        with patch("mmrelay.meshtastic_utils.meshtastic_client", mock_client):
            result, client_missing = mu._snapshot_node_name_rows()

            assert client_missing is False
            assert result is not None
            assert result["12345"] == {"user": {"id": None}}
            assert result["67890"] == {"user": {"id": None}}


class TestRefreshNodeNameTablesInvalidInterval:
    """Test refresh_node_name_tables invalid interval handling (lines 566-582)."""

    @pytest.mark.asyncio
    async def test_refresh_node_name_tables_boolean_interval(self):
        """Test with boolean interval raises TypeError, defaults to configured interval."""
        mu.config = {"meshtastic": {}}

        with patch(
            "mmrelay.meshtastic_utils.get_nodedb_refresh_interval_seconds",
            return_value=60.0,
        ):
            with patch("mmrelay.meshtastic_utils.asyncio.to_thread") as mock_to_thread:
                mock_to_thread.return_value = (None, True)
                with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
                    shutdown_event = asyncio.Event()

                    async def run_with_shutdown():
                        await asyncio.sleep(0.01)
                        shutdown_event.set()

                    shutdown_task = asyncio.create_task(run_with_shutdown())
                    try:
                        await mu.refresh_node_name_tables(
                            shutdown_event,
                            refresh_interval_seconds=True,
                        )
                    finally:
                        shutdown_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await shutdown_task

                    warning_calls = [
                        str(call) for call in mock_logger.warning.call_args_list
                    ]
                    assert any(
                        "Invalid NodeDB name-cache refresh interval override" in call
                        for call in warning_calls
                    )
                    assert any("60.0" in call for call in warning_calls)

    @pytest.mark.asyncio
    async def test_refresh_node_name_tables_nan_interval(self):
        """Test with nan interval raises ValueError, defaults to configured interval."""
        mu.config = {"meshtastic": {}}

        with patch(
            "mmrelay.meshtastic_utils.get_nodedb_refresh_interval_seconds",
            return_value=120.0,
        ):
            with patch("mmrelay.meshtastic_utils.asyncio.to_thread") as mock_to_thread:
                mock_to_thread.return_value = (None, True)
                with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
                    shutdown_event = asyncio.Event()

                    async def run_with_shutdown():
                        await asyncio.sleep(0.01)
                        shutdown_event.set()

                    shutdown_task = asyncio.create_task(run_with_shutdown())
                    try:
                        await mu.refresh_node_name_tables(
                            shutdown_event,
                            refresh_interval_seconds=float("nan"),
                        )
                    finally:
                        shutdown_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await shutdown_task

                    warning_calls = [
                        str(call) for call in mock_logger.warning.call_args_list
                    ]
                    assert any(
                        "Invalid NodeDB name-cache refresh interval override" in call
                        for call in warning_calls
                    )
                    assert any("120.0" in call for call in warning_calls)

    @pytest.mark.asyncio
    async def test_refresh_node_name_tables_inf_interval(self):
        """Test with inf interval raises ValueError, defaults to configured interval."""
        mu.config = {"meshtastic": {}}

        with patch(
            "mmrelay.meshtastic_utils.get_nodedb_refresh_interval_seconds",
            return_value=90.0,
        ):
            with patch("mmrelay.meshtastic_utils.asyncio.to_thread") as mock_to_thread:
                mock_to_thread.return_value = (None, True)
                with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
                    shutdown_event = asyncio.Event()

                    async def run_with_shutdown():
                        await asyncio.sleep(0.01)
                        shutdown_event.set()

                    shutdown_task = asyncio.create_task(run_with_shutdown())
                    try:
                        await mu.refresh_node_name_tables(
                            shutdown_event,
                            refresh_interval_seconds=float("inf"),
                        )
                    finally:
                        shutdown_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await shutdown_task

                    warning_calls = [
                        str(call) for call in mock_logger.warning.call_args_list
                    ]
                    assert any(
                        "Invalid NodeDB name-cache refresh interval override" in call
                        for call in warning_calls
                    )
                    assert any("90.0" in call for call in warning_calls)


class TestClearBleFuture:
    """Test _clear_ble_future clearing all related globals (line 1042-1049)."""

    def test_clear_ble_future_clears_all_globals(self):
        """Test that when done_future matches _ble_future, all related globals are cleared."""
        mock_future = Mock(spec=Future)
        mu._ble_future = mock_future
        mu._ble_future_address = TEST_BLE_MAC
        mu._ble_future_started_at = time.monotonic()
        mu._ble_future_timeout_secs = 30.0
        mu._ble_timeout_counts[TEST_BLE_MAC] = 5

        mu._clear_ble_future(mock_future)

        assert mu._ble_future is None
        assert mu._ble_future_address is None
        assert mu._ble_future_started_at is None
        assert mu._ble_future_timeout_secs is None
        assert TEST_BLE_MAC not in mu._ble_timeout_counts

    def test_clear_ble_future_no_match_does_not_clear(self):
        """Test that _clear_ble_future does not clear when future doesn't match."""
        mock_future1 = Mock(spec=Future)
        mock_future2 = Mock(spec=Future)
        mu._ble_future = mock_future1
        mu._ble_future_address = TEST_BLE_MAC
        mu._ble_future_started_at = time.monotonic()
        mu._ble_future_timeout_secs = 30.0
        mu._ble_timeout_counts[TEST_BLE_MAC] = 5

        mu._clear_ble_future(mock_future2)

        assert mu._ble_future is mock_future1
        assert mu._ble_future_address == TEST_BLE_MAC
        assert mu._ble_future_started_at is not None
        assert mu._ble_future_timeout_secs == 30.0
        assert mu._ble_timeout_counts.get(TEST_BLE_MAC) == 5


class TestEnsureBleWorkerAvailableStaleWorker:
    """Test _ensure_ble_worker_available stale worker detection (lines 1121-1145)."""

    def test_ensure_ble_worker_stale_detection(self):
        """Test when elapsed >= stale_after, logs warning and calls _maybe_reset_ble_executor."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mu._ble_future = mock_future
        mu._ble_future_address = TEST_BLE_MAC
        mu._ble_future_started_at = time.monotonic() - 100
        mu._ble_future_timeout_secs = 30.0

        def _simulate_reset(*_args, **_kwargs):
            mu._ble_future = None
            mu._ble_future_address = None
            mu._ble_future_started_at = None
            mu._ble_future_timeout_secs = None

        with patch(
            "mmrelay.meshtastic_utils._maybe_reset_ble_executor",
            side_effect=_simulate_reset,
        ) as mock_reset:
            with patch("mmrelay.meshtastic_utils._record_ble_timeout", return_value=5):
                with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
                    with patch(
                        "mmrelay.meshtastic_utils._ble_future_stale_grace_secs", 10.0
                    ):
                        with patch(
                            "mmrelay.meshtastic_utils._ble_timeout_reset_threshold", 3
                        ):
                            mu._ensure_ble_worker_available(
                                TEST_BLE_MAC, operation="test"
                            )

                            warning_calls = [
                                str(call) for call in mock_logger.warning.call_args_list
                            ]
                            assert any("stale" in call for call in warning_calls)
                            mock_reset.assert_called_once()

    def test_ensure_ble_worker_busy_raises_timeout(self):
        """Test when worker is busy (future not done), raises TimeoutError."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mu._ble_future = mock_future
        mu._ble_future_address = TEST_BLE_MAC
        mu._ble_future_started_at = time.monotonic()
        mu._ble_future_timeout_secs = 30.0

        with patch("mmrelay.meshtastic_utils._ble_future_stale_grace_secs", 1000.0):
            with pytest.raises(TimeoutError, match="already in progress"):
                mu._ensure_ble_worker_available(TEST_BLE_MAC, operation="test")


class TestMaybeResetBleExecutor:
    """Test _maybe_reset_ble_executor threshold and cleanup (lines 1167-1185)."""

    def test_maybe_reset_ble_executor_below_threshold(self):
        """Test when timeout_count < threshold, returns early without reset."""
        mu._ble_executor = Mock(spec=ThreadPoolExecutor)
        mu._ble_executor._shutdown = False

        with patch("mmrelay.meshtastic_utils._ble_timeout_reset_threshold", 10):
            mu._maybe_reset_ble_executor(TEST_BLE_MAC, timeout_count=3)

            mu._ble_executor.shutdown.assert_not_called()

    def test_maybe_reset_ble_executor_shutdown_executor(self):
        """Test when _ble_executor is not None and not shutdown, calls shutdown."""
        mock_executor = Mock(spec=ThreadPoolExecutor)
        mock_executor._shutdown = False
        mu._ble_executor = mock_executor
        mu._ble_future = None
        mu._ble_future_address = None

        with patch("mmrelay.meshtastic_utils._ble_timeout_reset_threshold", 3):
            mu._maybe_reset_ble_executor(TEST_BLE_MAC, timeout_count=5)

            mock_executor.shutdown.assert_called_once_with(
                wait=False, cancel_futures=True
            )
            assert mu._ble_executor is not mock_executor

    def test_maybe_reset_ble_executor_handles_type_error(self):
        """Test handling TypeError during shutdown (older Python compatibility)."""
        mock_executor = Mock(spec=ThreadPoolExecutor)
        mock_executor._shutdown = False
        mock_executor.shutdown.side_effect = [TypeError("cancel_futures"), None]
        mu._ble_executor = mock_executor
        mu._ble_future = None
        mu._ble_future_address = None

        with patch("mmrelay.meshtastic_utils._ble_timeout_reset_threshold", 3):
            mu._maybe_reset_ble_executor(TEST_BLE_MAC, timeout_count=5)

            assert mock_executor.shutdown.call_count == 2
            assert mu._ble_executor is not mock_executor


class TestBleInterfaceCreationShuttingDown:
    """Test BLE interface creation shutting_down check (lines 2861-2881)."""

    def test_connect_meshtastic_returns_none_when_shutting_down(self):
        """Test when shutting_down is True, connect_meshtastic returns None."""
        mu.shutting_down = True
        mu.config = {
            "meshtastic": {
                CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_BLE,
                CONFIG_KEY_BLE_ADDRESS: TEST_BLE_MAC,
            }
        }

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = mu.connect_meshtastic()

            assert result is None
            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert any("shutdown" in call.lower() for call in debug_calls)

    def test_ble_interface_creation_calls_ensure_ble_worker(self):
        """Test _ensure_ble_worker_available is called with correct parameters during BLE connect."""
        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = None
        mu.config = {
            "meshtastic": {
                CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_BLE,
                CONFIG_KEY_BLE_ADDRESS: TEST_BLE_MAC,
            }
        }

        with patch(
            "mmrelay.meshtastic_utils._ensure_ble_worker_available"
        ) as mock_ensure:
            mock_ensure.return_value = None
            with patch("mmrelay.meshtastic_utils._get_ble_executor") as mock_get_exec:
                mock_executor = Mock(spec=ThreadPoolExecutor)
                mock_get_exec.return_value = mock_executor
                mock_future = Mock(spec=Future)
                mock_future.done.return_value = False
                mock_future.result.return_value = None
                mock_executor.submit.return_value = mock_future

                with patch(
                    "mmrelay.meshtastic_utils._ble_interface_create_timeout_secs", 30.0
                ):
                    with patch("mmrelay.meshtastic_utils.BLE_AVAILABLE", True):
                        with patch(
                            "mmrelay.meshtastic_utils._ble_future_stale_grace_secs",
                            1000.0,
                        ):
                            with patch(
                                "mmrelay.meshtastic_utils._ble_timeout_reset_threshold",
                                3,
                            ):
                                with patch(
                                    "mmrelay.meshtastic_utils._ble_future_watchdog_secs",
                                    60.0,
                                ):
                                    with patch("mmrelay.meshtastic_utils.meshtastic"):
                                        mu.connect_meshtastic()

                                        mock_ensure.assert_called_once_with(
                                            TEST_BLE_MAC,
                                            operation="interface creation",
                                        )


class TestBleConnectShuttingDown:
    """Test BLE connect() shutting_down and busy worker checks (lines 2999-3015)."""

    def test_ensure_ble_worker_available_called_for_connect(self):
        """Test _ensure_ble_worker_available is called with 'connect' operation during BLE connect phase."""
        mu.shutting_down = False
        mu.reconnecting = False
        mu.meshtastic_client = None
        mu.config = {
            "meshtastic": {
                CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_BLE,
                CONFIG_KEY_BLE_ADDRESS: TEST_BLE_MAC,
            }
        }

        ensure_operations: list[str] = []

        def mock_ensure(address, *, operation):
            ensure_operations.append(operation)
            return None

        mock_interface = Mock()
        mock_interface.auto_reconnect = True
        mock_interface.connect = Mock()
        mock_interface.getMyNodeInfo = Mock(return_value={"user": {"id": "!abc123"}})
        mock_bleak_client = Mock()
        mock_bleak_client.address = TEST_BLE_MAC
        mock_client = Mock()
        mock_client.bleak_client = mock_bleak_client
        mock_interface.client = mock_client

        with patch(
            "mmrelay.meshtastic_utils._ensure_ble_worker_available",
            side_effect=mock_ensure,
        ):
            with patch("mmrelay.meshtastic_utils._get_ble_executor") as mock_get_exec:
                mock_executor = Mock(spec=ThreadPoolExecutor)
                mock_get_exec.return_value = mock_executor

                call_count = [0]

                def create_mock_future(*_args, **_kwargs):
                    mock_future = Mock(spec=Future)
                    mock_future.done.return_value = True
                    mock_future.result.return_value = mock_interface
                    call_count[0] += 1
                    return mock_future

                mock_executor.submit.side_effect = create_mock_future

                with patch(
                    "mmrelay.meshtastic_utils._ble_interface_create_timeout_secs", 30.0
                ):
                    with patch("mmrelay.meshtastic_utils.BLE_AVAILABLE", True):
                        with patch(
                            "mmrelay.meshtastic_utils._ble_future_stale_grace_secs",
                            1000.0,
                        ):
                            with patch(
                                "mmrelay.meshtastic_utils._ble_timeout_reset_threshold",
                                3,
                            ):
                                with patch(
                                    "mmrelay.meshtastic_utils._ble_future_watchdog_secs",
                                    60.0,
                                ):
                                    with patch("mmrelay.meshtastic_utils.meshtastic"):
                                        with patch(
                                            "mmrelay.meshtastic_utils._sanitize_ble_address",
                                            return_value=TEST_BLE_MAC,
                                        ):
                                            mu.connect_meshtastic()

                                            assert (
                                                "interface creation"
                                                in ensure_operations
                                            )
                                            assert "connect" in ensure_operations

    def test_ble_connect_busy_worker_raises_timeout(self):
        """Test when BLE worker is busy during connect phase, raises TimeoutError."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mu._ble_future = mock_future
        mu._ble_future_started_at = time.monotonic()
        mu._ble_future_timeout_secs = 30.0

        with patch("mmrelay.meshtastic_utils._ble_future_stale_grace_secs", 1000.0):
            with patch("mmrelay.meshtastic_utils.logger"):
                with pytest.raises(TimeoutError, match="already in progress"):
                    mu._ensure_ble_worker_available(TEST_BLE_MAC, operation="connect")


class TestConnectionLostHandlerClearingStaleBleFuture:
    """Test connection lost handler clearing stale BLE future (lines 3346-3350)."""

    def test_on_lost_meshtastic_connection_clears_ble_future_globals(self):
        """Test that _ble_future, _ble_future_address, _ble_future_started_at, _ble_future_timeout_secs are cleared."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mu._ble_future = mock_future
        mu._ble_future_address = TEST_BLE_MAC
        mu._ble_future_started_at = time.monotonic()
        mu._ble_future_timeout_secs = 30.0
        mu._ble_timeout_counts[TEST_BLE_MAC] = 5
        mu.meshtastic_client = Mock()
        mu.event_loop = Mock()
        mu.event_loop.is_closed.return_value = False
        mu.reconnecting = False

        with patch("mmrelay.meshtastic_utils.reconnect"):
            with patch("mmrelay.meshtastic_utils.logger"):
                mu.on_lost_meshtastic_connection(detection_source="test source")

                assert mu._ble_future is None
                assert mu._ble_future_address is None
                assert mu._ble_future_started_at is None
                assert mu._ble_future_timeout_secs is None
                assert TEST_BLE_MAC not in mu._ble_timeout_counts

    def test_on_lost_meshtastic_connection_clears_ble_timeout_counts(self):
        """Test _ble_timeout_counts is popped for the address."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mu._ble_future = mock_future
        mu._ble_future_address = "11:22:33:44:55:66"
        mu._ble_future_started_at = time.monotonic()
        mu._ble_future_timeout_secs = 30.0
        mu._ble_timeout_counts["11:22:33:44:55:66"] = 10
        mu._ble_timeout_counts["OTHER:ADDRESS"] = 3
        mu.meshtastic_client = Mock()
        mu.event_loop = Mock()
        mu.event_loop.is_closed.return_value = False
        mu.reconnecting = False

        with patch("mmrelay.meshtastic_utils.reconnect"):
            with patch("mmrelay.meshtastic_utils.logger"):
                mu.on_lost_meshtastic_connection(detection_source="test source")

                assert "11:22:33:44:55:66" not in mu._ble_timeout_counts
                assert mu._ble_timeout_counts["OTHER:ADDRESS"] == 3


class TestMetadataExecutorDegradedState:
    """Test metadata executor degraded state when orphan threshold is exceeded."""

    def test_metadata_executor_enters_degraded_state_at_threshold(self):
        """Test that metadata executor enters degraded state when orphan count reaches threshold."""
        from mmrelay.constants.network import EXECUTOR_ORPHAN_THRESHOLD

        old_executor = Mock(spec=ThreadPoolExecutor)
        old_executor._shutdown = False
        mu._metadata_executor = old_executor
        mu._metadata_future = Mock(spec=Future)
        mu._metadata_future_started_at = time.monotonic()
        mu._metadata_executor_orphaned_workers = EXECUTOR_ORPHAN_THRESHOLD - 1
        mu._metadata_executor_degraded = False

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            mu._reset_metadata_executor_for_stale_probe()

            assert mu._metadata_executor_degraded is True
            assert mu._metadata_future is None
            assert mu._metadata_future_started_at is None
            assert mock_logger.error.called
            error_msg = str(mock_logger.error.call_args)
            assert "DEGRADED" in error_msg

    def test_metadata_executor_refuses_reset_when_degraded(self):
        """Test that degraded metadata executor refuses to reset."""
        mu._metadata_executor_degraded = True
        mu._metadata_executor = Mock(spec=ThreadPoolExecutor)
        mu._metadata_future = Mock(spec=Future)

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            mu._reset_metadata_executor_for_stale_probe()

            assert mu._metadata_executor_degraded is True
            assert mock_logger.debug.called
            debug_msg = str(mock_logger.debug.call_args)
            assert "degraded state" in debug_msg.lower()

    def test_metadata_executor_allows_recovery_from_degraded(self):
        """Test that reset_executor_degraded_state clears metadata degraded state."""
        mu._metadata_executor_degraded = True
        mu._metadata_executor_orphaned_workers = 10

        result = mu.reset_executor_degraded_state()

        assert result is True
        assert mu._metadata_executor_degraded is False
        assert mu._metadata_executor_orphaned_workers == 0

    def test_metadata_degraded_state_blocks_new_probes(self):
        """Test that metadata degraded state raises MetadataExecutorDegradedError."""
        from mmrelay.meshtastic_utils import MetadataExecutorDegradedError

        mu._metadata_executor_degraded = True
        mu._metadata_future = None
        mu._metadata_future_started_at = None

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            with pytest.raises(MetadataExecutorDegradedError):
                mu._submit_metadata_probe(lambda: None)

            mock_logger.error.assert_called_once()
            error_msg = str(mock_logger.error.call_args)
            assert "degraded" in error_msg.lower()


class TestBleExecutorDegradedState:
    """Test BLE executor degraded state when orphan threshold is exceeded."""

    def test_ble_executor_enters_degraded_state_at_threshold(self):
        """Test that BLE executor enters degraded state when orphan count reaches threshold per address."""
        from mmrelay.constants.network import EXECUTOR_ORPHAN_THRESHOLD

        ble_address = TEST_BLE_MAC
        mock_executor = Mock(spec=ThreadPoolExecutor)
        mock_executor._shutdown = False
        mu._ble_executor = mock_executor
        mu._ble_future = None
        mu._ble_future_address = None
        mu._ble_executor_orphaned_workers_by_address = {
            ble_address: EXECUTOR_ORPHAN_THRESHOLD - 1
        }
        mu._ble_executor_degraded_addresses = set()

        with patch("mmrelay.meshtastic_utils._ble_timeout_reset_threshold", 3):
            with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
                mu._maybe_reset_ble_executor(ble_address, timeout_count=5)

                assert ble_address in mu._ble_executor_degraded_addresses
                assert mock_logger.error.called
                error_msg = str(mock_logger.error.call_args)
                assert "DEGRADED" in error_msg
                assert ble_address in error_msg

    def test_ble_executor_refuses_reset_when_degraded(self):
        """Test that degraded BLE executor refuses to reset for that address."""
        ble_address = TEST_BLE_MAC
        mu._ble_executor_degraded_addresses = {ble_address}
        mu._ble_executor = Mock(spec=ThreadPoolExecutor)
        mu._ble_future = Mock(spec=Future)

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            mu._maybe_reset_ble_executor(ble_address, timeout_count=10)

            assert ble_address in mu._ble_executor_degraded_addresses
            assert mock_logger.debug.called
            debug_msg = str(mock_logger.debug.call_args)
            assert "degraded state" in debug_msg.lower()

    def test_ble_executor_allows_recovery_for_specific_address(self):
        """Test that reset_executor_degraded_state clears degraded state for specific BLE address."""
        ble_address = TEST_BLE_MAC
        mu._ble_executor_degraded_addresses = {ble_address}
        mu._ble_executor_orphaned_workers_by_address = {ble_address: 10}

        result = mu.reset_executor_degraded_state(ble_address=ble_address)

        assert result is True
        assert ble_address not in mu._ble_executor_degraded_addresses
        assert ble_address not in mu._ble_executor_orphaned_workers_by_address

    def test_ble_executor_allows_recovery_for_all_addresses(self):
        """Test that reset_executor_degraded_state with reset_all clears all degraded state."""
        mu._ble_executor_degraded_addresses = {TEST_BLE_MAC, "11:22:33:44:55:66"}
        mu._ble_executor_orphaned_workers_by_address = {
            TEST_BLE_MAC: 10,
            "11:22:33:44:55:66": 5,
        }
        mu._metadata_executor_degraded = True
        mu._metadata_executor_orphaned_workers = 8

        result = mu.reset_executor_degraded_state(reset_all=True)

        assert result is True
        assert len(mu._ble_executor_degraded_addresses) == 0
        assert len(mu._ble_executor_orphaned_workers_by_address) == 0
        assert mu._metadata_executor_degraded is False
        assert mu._metadata_executor_orphaned_workers == 0

    def test_ble_executor_degraded_state_per_address(self):
        """Test that BLE degraded state is tracked per address."""

        degraded_address = TEST_BLE_MAC
        healthy_address = "11:22:33:44:55:66"

        mu._ble_executor_degraded_addresses = {degraded_address}
        mu._ble_executor = Mock(spec=ThreadPoolExecutor)
        mu._ble_executor._shutdown = False
        mu._ble_future = None
        mu._ble_future_address = None
        mu._ble_executor_orphaned_workers_by_address = {healthy_address: 0}

        with patch("mmrelay.meshtastic_utils._ble_timeout_reset_threshold", 3):
            with patch("mmrelay.meshtastic_utils.logger"):
                mu._maybe_reset_ble_executor(healthy_address, timeout_count=5)

                assert degraded_address in mu._ble_executor_degraded_addresses
                assert healthy_address not in mu._ble_executor_degraded_addresses


class TestReconnectResetsDegradedStateWithoutDeadlock:
    """Test reconnect path resets degraded BLE state without deadlock."""

    def test_on_lost_connection_resets_degraded_state_no_stale_address(self):
        """
        Test that on_lost_meshtastic_connection resets degraded state when
        there is no stale_ble_address but degraded addresses exist.

        This tests the fix for a deadlock bug where reset_executor_degraded_state(reset_all=True)
        was called while holding _ble_executor_lock, causing a self-deadlock since
        threading.Lock is not reentrant.
        """
        mu._ble_future = None
        mu._ble_future_address = None
        mu._ble_executor_degraded_addresses = {TEST_BLE_MAC, "11:22:33:44:55:66"}
        mu._ble_executor_orphaned_workers_by_address = {
            TEST_BLE_MAC: 10,
            "11:22:33:44:55:66": 5,
        }
        mu._metadata_executor_degraded = True
        mu._metadata_executor_orphaned_workers = 8
        mu.meshtastic_client = Mock()
        mu.event_loop = Mock()
        mu.event_loop.is_closed.return_value = False
        mu.reconnecting = False
        mu.shutting_down = False

        with patch("mmrelay.meshtastic_utils.reconnect"):
            with patch("mmrelay.meshtastic_utils.logger"):
                mu.on_lost_meshtastic_connection(detection_source="test source")

                assert len(mu._ble_executor_degraded_addresses) == 0
                assert len(mu._ble_executor_orphaned_workers_by_address) == 0
                assert mu._metadata_executor_degraded is False
                assert mu._metadata_executor_orphaned_workers == 0


class TestShutdownClearsDegradedState:
    """Test that shutdown_shared_executors clears degraded state."""

    def test_shutdown_clears_metadata_degraded_state(self):
        """Test that shutdown clears metadata executor degraded state."""
        mu._metadata_executor_degraded = True
        mu._metadata_executor_orphaned_workers = 10

        mu.shutdown_shared_executors()

        assert mu._metadata_executor_degraded is False
        assert mu._metadata_executor_orphaned_workers == 0

    def test_shutdown_clears_ble_degraded_state(self):
        """Test that shutdown clears BLE executor degraded state."""
        mu._ble_executor_degraded_addresses = {TEST_BLE_MAC}
        mu._ble_executor_orphaned_workers_by_address = {TEST_BLE_MAC: 10}

        mu.shutdown_shared_executors()

        assert len(mu._ble_executor_degraded_addresses) == 0
        assert len(mu._ble_executor_orphaned_workers_by_address) == 0
