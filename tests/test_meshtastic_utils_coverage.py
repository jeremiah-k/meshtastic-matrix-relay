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
import time
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import Mock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.network import METADATA_WATCHDOG_SECS


@pytest.fixture(autouse=True)
def reset_meshtastic_state(reset_meshtastic_globals):
    """Ensure clean state for each test."""
    # Additional resets specific to these tests
    mu._metadata_future = None
    mu._metadata_future_started_at = None
    mu._ble_future = None
    mu._ble_future_address = None
    mu._health_probe_request_deadlines = {}
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
        mu._ble_future_address = "AA:BB:CC:DD:EE:FF"
        mu._ble_timeout_counts["AA:BB:CC:DD:EE:FF"] = 3

        # Create a mock executor
        mock_executor = Mock(spec=ThreadPoolExecutor)
        mock_executor._shutdown = False
        mu._ble_executor = mock_executor

        mu._shutdown_shared_executors()

        # Verify future was cancelled
        mock_future.cancel.assert_called_once()
        # Verify address was cleared from timeout counts
        assert "AA:BB:CC:DD:EE:FF" not in mu._ble_timeout_counts
        # Verify executor was shut down
        mock_executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)

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
                "connection_type": "tcp",
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
                "connection_type": "tcp",
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
                "connection_type": "tcp",
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
                "connection_type": "tcp",
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
                "connection_type": "tcp",
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
