"""
Comprehensive unit tests for mmrelay.meshtastic_utils module.
Testing framework: pytest

This test suite covers all functions in the meshtastic_utils module including:
- Connection management functions
- Message processing and relay functionality  
- Service detection utilities
- Serial port validation
- Async reconnection logic
- Error handling and edge cases
"""

import pytest
import asyncio
import threading
import time
import os
from unittest.mock import Mock, patch, AsyncMock
from unittest import mock

# Import the module under test
import mmrelay.meshtastic_utils as meshtastic_utils


class TestIsRunningAsService:
    """Test cases for is_running_as_service() function."""

    def test_service_detected_via_invocation_id(self):
        """Test service detection via INVOCATION_ID environment variable."""
        with patch.dict(os.environ, {'INVOCATION_ID': '123456'}):
            result = meshtastic_utils.is_running_as_service()
            assert result is True

    def test_service_not_detected_no_invocation_id(self):
        """Test service not detected when INVOCATION_ID is not set."""
        with patch.dict(os.environ, {}, clear=True):
            with patch('builtins.open', side_effect=FileNotFoundError):
                result = meshtastic_utils.is_running_as_service()
                assert result is False

    def test_service_detected_via_systemd_parent(self):
        """Test service detection via systemd parent process."""
        mock_status_content = "Name:\ttest_process\nPPid:\t1\nState:\tS"
        mock_comm_content = "systemd"
        
        with patch.dict(os.environ, {}, clear=True):
            with patch('builtins.open') as mock_open:
                mock_open.side_effect = [
                    mock.mock_open(read_data=mock_status_content).return_value,
                    mock.mock_open(read_data=mock_comm_content).return_value
                ]
                result = meshtastic_utils.is_running_as_service()
                assert result is True

    def test_service_not_detected_non_systemd_parent(self):
        """Test service not detected when parent is not systemd."""
        mock_status_content = "Name:\ttest_process\nPPid:\t1000\nState:\tS"
        mock_comm_content = "bash"
        
        with patch.dict(os.environ, {}, clear=True):
            with patch('builtins.open') as mock_open:
                mock_open.side_effect = [
                    mock.mock_open(read_data=mock_status_content).return_value,
                    mock.mock_open(read_data=mock_comm_content).return_value
                ]
                result = meshtastic_utils.is_running_as_service()
                assert result is False

    def test_service_detection_file_permission_error(self):
        """Test service detection handles file permission errors gracefully."""
        with patch.dict(os.environ, {}, clear=True):
            with patch('builtins.open', side_effect=PermissionError):
                result = meshtastic_utils.is_running_as_service()
                assert result is False

    def test_service_detection_value_error(self):
        """Test service detection handles value errors gracefully."""
        mock_status_content = "Name:\ttest_process\nPPid:\tinvalid_pid\nState:\tS"
        
        with patch.dict(os.environ, {}, clear=True):
            with patch('builtins.open') as mock_open:
                mock_open.return_value = mock.mock_open(read_data=mock_status_content).return_value
                result = meshtastic_utils.is_running_as_service()
                assert result is False


class TestSerialPortExists:
    """Test cases for serial_port_exists() function."""

    @patch('serial.tools.list_ports.comports')
    def test_port_exists(self, mock_comports):
        """Test that existing port is detected correctly."""
        mock_port = Mock()
        mock_port.device = '/dev/ttyUSB0'
        mock_comports.return_value = [mock_port]
        
        result = meshtastic_utils.serial_port_exists('/dev/ttyUSB0')
        assert result is True

    @patch('serial.tools.list_ports.comports')
    def test_port_does_not_exist(self, mock_comports):
        """Test that non-existing port is detected correctly."""
        mock_port = Mock()
        mock_port.device = '/dev/ttyUSB1'
        mock_comports.return_value = [mock_port]
        
        result = meshtastic_utils.serial_port_exists('/dev/ttyUSB0')
        assert result is False

    @patch('serial.tools.list_ports.comports')
    def test_multiple_ports(self, mock_comports):
        """Test detection with multiple available ports."""
        mock_ports = []
        for i in range(3):
            mock_port = Mock()
            mock_port.device = f'/dev/ttyUSB{i}'
            mock_ports.append(mock_port)
        mock_comports.return_value = mock_ports
        
        assert meshtastic_utils.serial_port_exists('/dev/ttyUSB1') is True
        assert meshtastic_utils.serial_port_exists('/dev/ttyUSB5') is False

    @patch('serial.tools.list_ports.comports')
    def test_no_ports_available(self, mock_comports):
        """Test behavior when no ports are available."""
        mock_comports.return_value = []
        
        result = meshtastic_utils.serial_port_exists('/dev/ttyUSB0')
        assert result is False

    @patch('serial.tools.list_ports.comports')
    def test_port_exists_case_sensitive(self, mock_comports):
        """Test that port matching is case sensitive."""
        mock_port = Mock()
        mock_port.device = '/dev/ttyUSB0'
        mock_comports.return_value = [mock_port]
        
        assert meshtastic_utils.serial_port_exists('/dev/ttyUSB0') is True
        assert meshtastic_utils.serial_port_exists('/dev/TTYUSB0') is False


class TestConnectMeshtastic:
    """Test cases for connect_meshtastic() function."""

    def setup_method(self):
        """Set up test fixtures."""
        # Reset global variables
        meshtastic_utils.meshtastic_client = None
        meshtastic_utils.shutting_down = False
        meshtastic_utils.config = None
        meshtastic_utils.matrix_rooms = []

    def teardown_method(self):
        """Clean up after tests."""
        # Reset global variables
        meshtastic_utils.meshtastic_client = None
        meshtastic_utils.shutting_down = False
        meshtastic_utils.config = None
        meshtastic_utils.matrix_rooms = []

    def test_connect_when_shutting_down(self):
        """Test that connection is not attempted when shutting down."""
        meshtastic_utils.shutting_down = True
        result = meshtastic_utils.connect_meshtastic()
        assert result is None

    def test_return_existing_client_when_connected(self):
        """Test that existing client is returned when already connected."""
        mock_client = Mock()
        meshtastic_utils.meshtastic_client = mock_client
        
        result = meshtastic_utils.connect_meshtastic()
        assert result == mock_client

    def test_force_connect_closes_existing_client(self):
        """Test that force_connect closes existing client and creates new one."""
        mock_existing_client = Mock()
        meshtastic_utils.meshtastic_client = mock_existing_client
        
        test_config = {
            'meshtastic': {
                'connection_type': 'serial',
                'serial_port': '/dev/ttyUSB0'
            }
        }
        
        with patch('mmrelay.meshtastic_utils.serial_port_exists', return_value=True):
            with patch('meshtastic.serial_interface.SerialInterface') as mock_serial:
                mock_new_client = Mock()
                mock_new_client.getMyNodeInfo.return_value = {
                    'user': {'shortName': 'TEST', 'hwModel': 'TEST_HW'}
                }
                mock_serial.return_value = mock_new_client
                
                with patch('pubsub.pub.subscribe'):
                    result = meshtastic_utils.connect_meshtastic(
                        passed_config=test_config, 
                        force_connect=True
                    )
                
                mock_existing_client.close.assert_called_once()
                assert result == mock_new_client

    def test_connect_serial_success(self):
        """Test successful serial connection."""
        test_config = {
            'meshtastic': {
                'connection_type': 'serial',
                'serial_port': '/dev/ttyUSB0'
            }
        }
        
        with patch('mmrelay.meshtastic_utils.serial_port_exists', return_value=True):
            with patch('meshtastic.serial_interface.SerialInterface') as mock_serial:
                mock_client = Mock()
                mock_client.getMyNodeInfo.return_value = {
                    'user': {'shortName': 'TEST', 'hwModel': 'TEST_HW'}
                }
                mock_serial.return_value = mock_client
                
                with patch('pubsub.pub.subscribe'):
                    result = meshtastic_utils.connect_meshtastic(passed_config=test_config)
                
                assert result == mock_client
                mock_serial.assert_called_once_with('/dev/ttyUSB0')

    def test_connect_serial_port_does_not_exist(self):
        """Test serial connection when port doesn't exist."""
        test_config = {
            'meshtastic': {
                'connection_type': 'serial',
                'serial_port': '/dev/ttyUSB0'
            }
        }
        
        with patch('mmrelay.meshtastic_utils.serial_port_exists', return_value=False):
            with patch('time.sleep') as mock_sleep:
                # Mock shutting_down to be True after first iteration to break loop
                def side_effect():
                    meshtastic_utils.shutting_down = True
                mock_sleep.side_effect = lambda x: side_effect()
                
                result = meshtastic_utils.connect_meshtastic(passed_config=test_config)
                assert result is None
                mock_sleep.assert_called()

    def test_connect_ble_success(self):
        """Test successful BLE connection."""
        test_config = {
            'meshtastic': {
                'connection_type': 'ble',
                'ble_address': '12:34:56:78:9A:BC'
            }
        }
        
        with patch('meshtastic.ble_interface.BLEInterface') as mock_ble:
            mock_client = Mock()
            mock_client.getMyNodeInfo.return_value = {
                'user': {'shortName': 'TEST', 'hwModel': 'TEST_HW'}
            }
            mock_ble.return_value = mock_client
            
            with patch('pubsub.pub.subscribe'):
                result = meshtastic_utils.connect_meshtastic(passed_config=test_config)
            
            assert result == mock_client
            mock_ble.assert_called_once_with(
                address='12:34:56:78:9A:BC',
                noProto=False,
                debugOut=None,
                noNodes=False
            )

    def test_connect_ble_no_address(self):
        """Test BLE connection without address."""
        test_config = {
            'meshtastic': {
                'connection_type': 'ble'
            }
        }
        
        result = meshtastic_utils.connect_meshtastic(passed_config=test_config)
        assert result is None

    def test_connect_tcp_success(self):
        """Test successful TCP connection."""
        test_config = {
            'meshtastic': {
                'connection_type': 'tcp',
                'host': 'meshtastic.local'
            }
        }
        
        with patch('meshtastic.tcp_interface.TCPInterface') as mock_tcp:
            mock_client = Mock()
            mock_client.getMyNodeInfo.return_value = {
                'user': {'shortName': 'TEST', 'hwModel': 'TEST_HW'}
            }
            mock_tcp.return_value = mock_client
            
            with patch('pubsub.pub.subscribe'):
                result = meshtastic_utils.connect_meshtastic(passed_config=test_config)
            
            assert result == mock_client
            mock_tcp.assert_called_once_with(hostname='meshtastic.local')

    def test_connect_legacy_network_type(self):
        """Test legacy 'network' connection type maps to 'tcp'."""
        test_config = {
            'meshtastic': {
                'connection_type': 'network',
                'host': 'meshtastic.local'
            }
        }
        
        with patch('meshtastic.tcp_interface.TCPInterface') as mock_tcp:
            mock_client = Mock()
            mock_client.getMyNodeInfo.return_value = {
                'user': {'shortName': 'TEST', 'hwModel': 'TEST_HW'}
            }
            mock_tcp.return_value = mock_client
            
            with patch('pubsub.pub.subscribe'):
                result = meshtastic_utils.connect_meshtastic(passed_config=test_config)
            
            assert result == mock_client

    def test_connect_unknown_connection_type(self):
        """Test connection with unknown connection type."""
        test_config = {
            'meshtastic': {
                'connection_type': 'unknown'
            }
        }
        
        result = meshtastic_utils.connect_meshtastic(passed_config=test_config)
        assert result is None

    def test_connect_no_config(self):
        """Test connection attempt without config."""
        result = meshtastic_utils.connect_meshtastic()
        assert result is None

    def test_connect_with_matrix_rooms_config(self):
        """Test that matrix_rooms are updated when config is passed."""
        test_config = {
            'meshtastic': {
                'connection_type': 'serial',
                'serial_port': '/dev/ttyUSB0'
            },
            'matrix_rooms': [
                {'id': '!room1:matrix.org', 'meshtastic_channel': 0}
            ]
        }
        
        with patch('mmrelay.meshtastic_utils.serial_port_exists', return_value=True):
            with patch('meshtastic.serial_interface.SerialInterface') as mock_serial:
                mock_client = Mock()
                mock_client.getMyNodeInfo.return_value = {
                    'user': {'shortName': 'TEST', 'hwModel': 'TEST_HW'}
                }
                mock_serial.return_value = mock_client
                
                with patch('pubsub.pub.subscribe'):
                    meshtastic_utils.connect_meshtastic(passed_config=test_config)
                
                assert len(meshtastic_utils.matrix_rooms) == 1
                assert meshtastic_utils.matrix_rooms[0]['id'] == '!room1:matrix.org'

    def test_connect_retry_on_exception(self):
        """Test retry logic on connection exceptions."""
        test_config = {
            'meshtastic': {
                'connection_type': 'serial',
                'serial_port': '/dev/ttyUSB0'
            }
        }
        
        with patch('mmrelay.meshtastic_utils.serial_port_exists', return_value=True):
            with patch('meshtastic.serial_interface.SerialInterface') as mock_serial:
                # First call raises exception, second succeeds
                mock_client = Mock()
                mock_client.getMyNodeInfo.return_value = {
                    'user': {'shortName': 'TEST', 'hwModel': 'TEST_HW'}
                }
                mock_serial.side_effect = [Exception("Connection failed"), mock_client]
                
                with patch('time.sleep'):
                    with patch('pubsub.pub.subscribe'):
                        result = meshtastic_utils.connect_meshtastic(passed_config=test_config)
                
                assert result == mock_client
                assert mock_serial.call_count == 2


class TestOnLostMeshtasticConnection:
    """Test cases for on_lost_meshtastic_connection() function."""

    def setup_method(self):
        """Set up test fixtures."""
        meshtastic_utils.meshtastic_client = None
        meshtastic_utils.shutting_down = False
        meshtastic_utils.reconnecting = False
        meshtastic_utils.event_loop = None
        meshtastic_utils.reconnect_task = None

    def teardown_method(self):
        """Clean up after tests."""
        meshtastic_utils.meshtastic_client = None
        meshtastic_utils.shutting_down = False
        meshtastic_utils.reconnecting = False
        meshtastic_utils.event_loop = None
        meshtastic_utils.reconnect_task = None

    def test_no_reconnect_when_shutting_down(self):
        """Test that reconnection is not attempted when shutting down."""
        meshtastic_utils.shutting_down = True
        
        meshtastic_utils.on_lost_meshtastic_connection()
        
        assert meshtastic_utils.reconnecting is False

    def test_no_reconnect_when_already_reconnecting(self):
        """Test that additional reconnection is not started when already reconnecting."""
        meshtastic_utils.reconnecting = True
        
        meshtastic_utils.on_lost_meshtastic_connection()
        
        # Should remain True, not reset
        assert meshtastic_utils.reconnecting is True

    def test_closes_existing_client(self):
        """Test that existing client is closed on connection loss."""
        mock_client = Mock()
        meshtastic_utils.meshtastic_client = mock_client
        
        meshtastic_utils.on_lost_meshtastic_connection()
        
        mock_client.close.assert_called_once()
        assert meshtastic_utils.meshtastic_client is None

    def test_handles_oserror_bad_file_descriptor(self):
        """Test handling of OSError with errno 9 (bad file descriptor)."""
        mock_client = Mock()
        mock_client.close.side_effect = OSError(9, "Bad file descriptor")
        meshtastic_utils.meshtastic_client = mock_client
        
        # Should not raise exception
        meshtastic_utils.on_lost_meshtastic_connection()
        
        assert meshtastic_utils.meshtastic_client is None

    def test_handles_other_oserrors(self):
        """Test handling of other OSErrors during client close."""
        mock_client = Mock()
        mock_client.close.side_effect = OSError(5, "Other error")
        meshtastic_utils.meshtastic_client = mock_client
        
        # Should not raise exception
        meshtastic_utils.on_lost_meshtastic_connection()
        
        assert meshtastic_utils.meshtastic_client is None

    def test_handles_general_exceptions(self):
        """Test handling of general exceptions during client close."""
        mock_client = Mock()
        mock_client.close.side_effect = Exception("General error")
        meshtastic_utils.meshtastic_client = mock_client
        
        # Should not raise exception
        meshtastic_utils.on_lost_meshtastic_connection()
        
        assert meshtastic_utils.meshtastic_client is None

    def test_starts_reconnect_task_with_event_loop(self):
        """Test that reconnect task is started when event loop is available."""
        mock_loop = Mock()
        mock_task = Mock()
        mock_loop.is_running.return_value = True
        meshtastic_utils.event_loop = mock_loop
        
        with patch('asyncio.run_coroutine_threadsafe', return_value=mock_task) as mock_run:
            meshtastic_utils.on_lost_meshtastic_connection()
        
        mock_run.assert_called_once()
        assert meshtastic_utils.reconnect_task == mock_task
        assert meshtastic_utils.reconnecting is True


class TestReconnect:
    """Test cases for reconnect() async function."""

    def setup_method(self):
        """Set up test fixtures."""
        meshtastic_utils.meshtastic_client = None
        meshtastic_utils.shutting_down = False
        meshtastic_utils.reconnecting = False

    def teardown_method(self):
        """Clean up after tests."""
        meshtastic_utils.meshtastic_client = None
        meshtastic_utils.shutting_down = False
        meshtastic_utils.reconnecting = False

    @pytest.mark.asyncio
    async def test_reconnect_success(self):
        """Test successful reconnection."""
        mock_client = Mock()
        
        with patch('mmrelay.meshtastic_utils.connect_meshtastic', return_value=mock_client):
            with patch('asyncio.sleep'):
                with patch('mmrelay.meshtastic_utils.is_running_as_service', return_value=True):
                    await meshtastic_utils.reconnect()
        
        assert meshtastic_utils.reconnecting is False

    @pytest.mark.asyncio
    async def test_reconnect_stops_when_shutting_down(self):
        """Test that reconnection stops when shutting_down is set."""
        meshtastic_utils.shutting_down = True
        
        with patch('asyncio.sleep') as mock_sleep:
            await meshtastic_utils.reconnect()
        
        # Should not wait/sleep if shutting down immediately
        mock_sleep.assert_not_called()
        assert meshtastic_utils.reconnecting is False

    @pytest.mark.asyncio
    async def test_reconnect_with_rich_progress(self):
        """Test reconnection with Rich progress display."""
        with patch('mmrelay.meshtastic_utils.is_running_as_service', return_value=False):
            with patch('mmrelay.meshtastic_utils.connect_meshtastic', return_value=Mock()):
                with patch('rich.progress.Progress') as mock_progress:
                    mock_progress_instance = Mock()
                    mock_progress_instance.__enter__ = Mock(return_value=mock_progress_instance)
                    mock_progress_instance.__exit__ = Mock(return_value=None)
                    mock_progress_instance.add_task.return_value = 'task_id'
                    mock_progress.return_value = mock_progress_instance
                    
                    with patch('asyncio.sleep'):
                        await meshtastic_utils.reconnect()
        
        assert meshtastic_utils.reconnecting is False

    @pytest.mark.asyncio
    async def test_reconnect_exponential_backoff(self):
        """Test exponential backoff on reconnection failures."""
        call_count = 0
        
        def mock_connect_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return None  # Fail first 2 attempts
            return Mock()  # Succeed on 3rd attempt
        
        with patch('mmrelay.meshtastic_utils.connect_meshtastic', side_effect=mock_connect_side_effect):
            with patch('asyncio.sleep') as mock_sleep:
                with patch('mmrelay.meshtastic_utils.is_running_as_service', return_value=True):
                    await meshtastic_utils.reconnect()
        
        # Should have attempted multiple times with increasing backoff
        assert mock_sleep.call_count >= 2
        assert meshtastic_utils.reconnecting is False

    @pytest.mark.asyncio
    async def test_reconnect_cancelled_task(self):
        """Test handling of cancelled reconnection task."""
        with patch('asyncio.sleep', side_effect=asyncio.CancelledError):
            await meshtastic_utils.reconnect()
        
        assert meshtastic_utils.reconnecting is False

    @pytest.mark.asyncio
    async def test_reconnect_max_backoff_cap(self):
        """Test that backoff time is capped at maximum."""
        call_count = 0
        
        def mock_connect_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 10:  # Fail many times to trigger max backoff
                return None
            return Mock()
        
        with patch('mmrelay.meshtastic_utils.connect_meshtastic', side_effect=mock_connect_side_effect):
            with patch('asyncio.sleep') as mock_sleep:
                with patch('mmrelay.meshtastic_utils.is_running_as_service', return_value=True):
                    await meshtastic_utils.reconnect()
        
        # Check that sleep was called with capped value (300 seconds)
        sleep_calls = [call.args[0] for call in mock_sleep.call_args_list]
        assert max(sleep_calls) <= 300


class TestOnMeshtasticMessage:
    """Test cases for on_meshtastic_message() function."""

    def setup_method(self):
        """Set up test fixtures."""
        meshtastic_utils.config = None
        meshtastic_utils.matrix_rooms = []
        meshtastic_utils.shutting_down = False
        meshtastic_utils.event_loop = asyncio.new_event_loop()

    def teardown_method(self):
        """Clean up after tests."""
        meshtastic_utils.config = None
        meshtastic_utils.matrix_rooms = []
        meshtastic_utils.shutting_down = False
        if meshtastic_utils.event_loop:
            meshtastic_utils.event_loop.close()
        meshtastic_utils.event_loop = None

    def test_message_processing_no_config(self):
        """Test message processing when no config is available."""
        packet = {'decoded': {'text': 'test message'}}
        interface = Mock()
        
        # Should return early without processing
        meshtastic_utils.on_meshtastic_message(packet, interface)
        # No assertions needed - just ensuring no exceptions

    def test_message_processing_when_shutting_down(self):
        """Test message processing when shutting down."""
        meshtastic_utils.shutting_down = True
        meshtastic_utils.config = {'meshtastic': {'meshnet_name': 'test'}}
        
        packet = {'decoded': {'text': 'test message'}}
        interface = Mock()
        
        # Should return early without processing
        meshtastic_utils.on_meshtastic_message(packet, interface)
        # No assertions needed - just ensuring no exceptions

    def test_message_processing_no_event_loop(self):
        """Test message processing when event loop is not set."""
        meshtastic_utils.event_loop = None
        meshtastic_utils.config = {'meshtastic': {'meshnet_name': 'test'}}
        
        packet = {'decoded': {'text': 'test message'}}
        interface = Mock()
        
        # Should return early without processing
        meshtastic_utils.on_meshtastic_message(packet, interface)
        # No assertions needed - just ensuring no exceptions

    def test_text_message_relay_to_matrix(self):
        """Test relaying text message to Matrix."""
        test_config = {
            'meshtastic': {
                'meshnet_name': 'TestMesh',
                'detection_sensor': False
            }
        }
        meshtastic_utils.config = test_config
        meshtastic_utils.matrix_rooms = [
            {'id': '!room1:matrix.org', 'meshtastic_channel': 0}
        ]
        
        packet = {
            'decoded': {
                'text': 'Hello World',
                'portnum': 'TEXT_MESSAGE_APP'
            },
            'fromId': 123456789,
            'from': 123456789,
            'to': 4294967295,  # BROADCAST_NUM
            'channel': 0,
            'id': 'msg123'
        }
        
        interface = Mock()
        interface.myInfo.my_node_num = 987654321
        interface.nodes = {}
        
        with patch('mmrelay.meshtastic_utils.get_longname', return_value='TestNode'):
            with patch('mmrelay.meshtastic_utils.get_shortname', return_value='TN'):
                with patch('mmrelay.matrix_utils.get_interaction_settings') as mock_interactions:
                    mock_interactions.return_value = {
                        'reactions': True,
                        'replies': True
                    }
                    with patch('mmrelay.matrix_utils.message_storage_enabled', return_value=True):
                        with patch('mmrelay.plugin_loader.load_plugins', return_value=[]):
                            with patch('asyncio.run_coroutine_threadsafe') as mock_run:
                                meshtastic_utils.on_meshtastic_message(packet, interface)
                                
                                # Should attempt to relay to Matrix
                                mock_run.assert_called()

    def test_reaction_message_handling(self):
        """Test handling of reaction messages."""
        test_config = {
            'meshtastic': {
                'meshnet_name': 'TestMesh'
            }
        }
        meshtastic_utils.config = test_config
        
        packet = {
            'decoded': {
                'text': '👍',
                'portnum': 'TEXT_MESSAGE_APP',
                'replyId': 'original_msg_id',
                'emoji': 1
            },
            'fromId': 123456789,
            'id': 'reaction123'
        }
        
        interface = Mock()
        
        with patch('mmrelay.matrix_utils.get_interaction_settings') as mock_interactions:
            mock_interactions.return_value = {
                'reactions': True,
                'replies': True
            }
            with patch('mmrelay.matrix_utils.message_storage_enabled', return_value=True):
                with patch('mmrelay.meshtastic_utils.get_longname', return_value='TestNode'):
                    with patch('mmrelay.meshtastic_utils.get_shortname', return_value='TN'):
                        with patch('mmrelay.meshtastic_utils.get_message_map_by_meshtastic_id') as mock_get_map:
                            mock_get_map.return_value = (
                                'matrix_event_id', 
                                '!room:matrix.org', 
                                'Original message',
                                'TestMesh'
                            )
                            with patch('asyncio.run_coroutine_threadsafe') as mock_run:
                                meshtastic_utils.on_meshtastic_message(packet, interface)
                                
                                # Should relay reaction to Matrix
                                mock_run.assert_called()

    def test_reply_message_handling(self):
        """Test handling of reply messages."""
        test_config = {
            'meshtastic': {
                'meshnet_name': 'TestMesh'
            }
        }
        meshtastic_utils.config = test_config
        
        packet = {
            'decoded': {
                'text': 'This is a reply',
                'portnum': 'TEXT_MESSAGE_APP',
                'replyId': 'original_msg_id'
                # No emoji field, so not a reaction
            },
            'fromId': 123456789,
            'id': 'reply123'
        }
        
        interface = Mock()
        
        with patch('mmrelay.matrix_utils.get_interaction_settings') as mock_interactions:
            mock_interactions.return_value = {
                'reactions': True,
                'replies': True
            }
            with patch('mmrelay.matrix_utils.message_storage_enabled', return_value=True):
                with patch('mmrelay.meshtastic_utils.get_longname', return_value='TestNode'):
                    with patch('mmrelay.meshtastic_utils.get_shortname', return_value='TN'):
                        with patch('mmrelay.meshtastic_utils.get_message_map_by_meshtastic_id') as mock_get_map:
                            mock_get_map.return_value = (
                                'matrix_event_id',
                                '!room:matrix.org',
                                'Original message',
                                'TestMesh'
                            )
                            with patch('asyncio.run_coroutine_threadsafe') as mock_run:
                                meshtastic_utils.on_meshtastic_message(packet, interface)
                                
                                # Should relay reply to Matrix
                                mock_run.assert_called()

    def test_direct_message_not_relayed(self):
        """Test that direct messages are not relayed to Matrix."""
        test_config = {
            'meshtastic': {
                'meshnet_name': 'TestMesh'
            }
        }
        meshtastic_utils.config = test_config
        
        packet = {
            'decoded': {
                'text': 'Direct message',
                'portnum': 'TEXT_MESSAGE_APP'
            },
            'fromId': 123456789,
            'to': 987654321,  # Direct message to relay node
            'channel': 0
        }
        
        interface = Mock()
        interface.myInfo.my_node_num = 987654321  # Same as 'to' field
        
        with patch('mmrelay.matrix_utils.get_interaction_settings') as mock_interactions:
            mock_interactions.return_value = {
                'reactions': True,
                'replies': True
            }
            with patch('mmrelay.matrix_utils.message_storage_enabled', return_value=True):
                with patch('mmrelay.meshtastic_utils.get_longname', return_value='TestNode'):
                    with patch('mmrelay.meshtastic_utils.get_shortname', return_value='TN'):
                        with patch('mmrelay.plugin_loader.load_plugins', return_value=[]):
                            with patch('asyncio.run_coroutine_threadsafe') as mock_run:
                                meshtastic_utils.on_meshtastic_message(packet, interface)
                                
                                # Should not relay direct messages
                                mock_run.assert_not_called()

    def test_detection_sensor_disabled(self):
        """Test that detection sensor messages are filtered when disabled."""
        test_config = {
            'meshtastic': {
                'meshnet_name': 'TestMesh',
                'detection_sensor': False
            }
        }
        meshtastic_utils.config = test_config
        meshtastic_utils.matrix_rooms = [
            {'id': '!room1:matrix.org', 'meshtastic_channel': 0}
        ]
        
        packet = {
            'decoded': {
                'text': 'Motion detected',
                'portnum': 'DETECTION_SENSOR_APP'
            },
            'fromId': 123456789,
            'channel': 0
        }
        
        interface = Mock()
        
        with patch('mmrelay.matrix_utils.get_interaction_settings') as mock_interactions:
            mock_interactions.return_value = {
                'reactions': True,
                'replies': True
            }
            with patch('mmrelay.matrix_utils.message_storage_enabled', return_value=True):
                with patch('asyncio.run_coroutine_threadsafe') as mock_run:
                    meshtastic_utils.on_meshtastic_message(packet, interface)
                    
                    # Should not relay detection sensor messages when disabled
                    mock_run.assert_not_called()

    def test_unmapped_channel_filtered(self):
        """Test that messages from unmapped channels are filtered."""
        test_config = {
            'meshtastic': {
                'meshnet_name': 'TestMesh'
            }
        }
        meshtastic_utils.config = test_config
        meshtastic_utils.matrix_rooms = [
            {'id': '!room1:matrix.org', 'meshtastic_channel': 0}
        ]
        
        packet = {
            'decoded': {
                'text': 'Message on unmapped channel',
                'portnum': 'TEXT_MESSAGE_APP'
            },
            'fromId': 123456789,
            'channel': 5  # Channel not mapped to any Matrix room
        }
        
        interface = Mock()
        
        with patch('mmrelay.matrix_utils.get_interaction_settings') as mock_interactions:
            mock_interactions.return_value = {
                'reactions': True,
                'replies': True
            }
            with patch('mmrelay.matrix_utils.message_storage_enabled', return_value=True):
                with patch('asyncio.run_coroutine_threadsafe') as mock_run:
                    meshtastic_utils.on_meshtastic_message(packet, interface)
                    
                    # Should not relay messages from unmapped channels
                    mock_run.assert_not_called()

    def test_plugin_handles_message(self):
        """Test that plugin-handled messages are not relayed to Matrix."""
        test_config = {
            'meshtastic': {
                'meshnet_name': 'TestMesh'
            }
        }
        meshtastic_utils.config = test_config
        meshtastic_utils.matrix_rooms = [
            {'id': '!room1:matrix.org', 'meshtastic_channel': 0}
        ]
        
        packet = {
            'decoded': {
                'text': 'Plugin command',
                'portnum': 'TEXT_MESSAGE_APP'
            },
            'fromId': 123456789,
            'to': 4294967295,  # BROADCAST_NUM
            'channel': 0
        }
        
        interface = Mock()
        interface.myInfo.my_node_num = 987654321
        
        # Mock plugin that handles the message
        mock_plugin = Mock()
        mock_plugin.handle_meshtastic_message = AsyncMock(return_value=True)
        mock_plugin.plugin_name = 'test_plugin'
        
        with patch('mmrelay.matrix_utils.get_interaction_settings') as mock_interactions:
            mock_interactions.return_value = {
                'reactions': True,
                'replies': True
            }
            with patch('mmrelay.matrix_utils.message_storage_enabled', return_value=True):
                with patch('mmrelay.meshtastic_utils.get_longname', return_value='TestNode'):
                    with patch('mmrelay.meshtastic_utils.get_shortname', return_value='TN'):
                        with patch('mmrelay.plugin_loader.load_plugins', return_value=[mock_plugin]):
                            with patch('asyncio.run_coroutine_threadsafe') as mock_run:
                                # Mock the plugin result
                                mock_result = Mock()
                                mock_result.result.return_value = True
                                mock_run.return_value = mock_result
                                
                                meshtastic_utils.on_meshtastic_message(packet, interface)
                                
                                # Plugin should be called but Matrix relay should not happen
                                mock_run.assert_called_once()


class TestCheckConnection:
    """Test cases for check_connection() async function."""

    def setup_method(self):
        """Set up test fixtures."""
        meshtastic_utils.meshtastic_client = None
        meshtastic_utils.shutting_down = False
        meshtastic_utils.config = None

    def teardown_method(self):
        """Clean up after tests."""
        meshtastic_utils.meshtastic_client = None
        meshtastic_utils.shutting_down = False
        meshtastic_utils.config = None

    @pytest.mark.asyncio
    async def test_check_connection_no_config(self):
        """Test connection check when no config is available."""
        # Should return early without doing anything
        await meshtastic_utils.check_connection()
        # No assertions needed - just ensuring no exceptions

    @pytest.mark.asyncio
    async def test_check_connection_no_client(self):
        """Test connection check when no client is connected."""
        meshtastic_utils.config = {
            'meshtastic': {'connection_type': 'serial'}
        }
        
        with patch('asyncio.sleep', side_effect=asyncio.CancelledError):
            # Should complete one iteration then exit
            with pytest.raises(asyncio.CancelledError):
                await meshtastic_utils.check_connection()

    @pytest.mark.asyncio
    async def test_check_connection_success(self):
        """Test successful connection check."""
        meshtastic_utils.config = {
            'meshtastic': {'connection_type': 'serial'}
        }
        
        mock_client = Mock()
        mock_client.localNode.getMetadata = Mock()
        meshtastic_utils.meshtastic_client = mock_client
        
        # Mock the stdout/stderr capture to return firmware_version
        with patch('contextlib.redirect_stdout'):
            with patch('contextlib.redirect_stderr'):
                with patch('io.StringIO') as mock_stringio:
                    mock_stringio.return_value.getvalue.return_value = "firmware_version: 1.2.3"
                    
                    with patch('asyncio.sleep', side_effect=asyncio.CancelledError):
                        with pytest.raises(asyncio.CancelledError):
                            await meshtastic_utils.check_connection()
        
        # Should call getMetadata to check connection
        mock_client.localNode.getMetadata.assert_called()

    @pytest.mark.asyncio
    async def test_check_connection_failure_triggers_reconnect(self):
        """Test that connection failure triggers reconnection."""
        meshtastic_utils.config = {
            'meshtastic': {'connection_type': 'serial'}
        }
        
        mock_client = Mock()
        mock_client.localNode.getMetadata.side_effect = Exception("Connection lost")
        meshtastic_utils.meshtastic_client = mock_client
        
        with patch('mmrelay.meshtastic_utils.on_lost_meshtastic_connection') as mock_lost:
            with patch('asyncio.sleep', side_effect=asyncio.CancelledError):
                with pytest.raises(asyncio.CancelledError):
                    await meshtastic_utils.check_connection()
        
        # Should trigger reconnection on failure
        mock_lost.assert_called_once_with(mock_client)

    @pytest.mark.asyncio
    async def test_check_connection_no_firmware_version(self):
        """Test connection check when metadata doesn't contain firmware_version."""
        meshtastic_utils.config = {
            'meshtastic': {'connection_type': 'tcp'}
        }
        
        mock_client = Mock()
        mock_client.localNode.getMetadata = Mock()
        meshtastic_utils.meshtastic_client = mock_client
        
        # Mock the stdout capture to return output without firmware_version
        with patch('contextlib.redirect_stdout'):
            with patch('contextlib.redirect_stderr'):
                with patch('io.StringIO') as mock_stringio:
                    mock_stringio.return_value.getvalue.return_value = "other_info: value"
                    
                    with patch('mmrelay.meshtastic_utils.on_lost_meshtastic_connection') as mock_lost:
                        with patch('asyncio.sleep', side_effect=asyncio.CancelledError):
                            with pytest.raises(asyncio.CancelledError):
                                await meshtastic_utils.check_connection()
        
        # Should trigger reconnection when firmware_version not found
        mock_lost.assert_called_once_with(mock_client)


class TestEdgeCasesAndErrorHandling:
    """Test edge cases, error conditions and boundary scenarios."""

    def test_thread_safety_meshtastic_lock(self):
        """Test that meshtastic_lock prevents race conditions."""
        
        results = []
        
        def connect_attempt(attempt_id):
            """Simulate concurrent connection attempts."""
            test_config = {
                'meshtastic': {
                    'connection_type': 'serial',
                    'serial_port': '/dev/ttyUSB0'
                }
            }
            
            with patch('mmrelay.meshtastic_utils.serial_port_exists', return_value=True):
                with patch('meshtastic.serial_interface.SerialInterface') as mock_serial:
                    mock_client = Mock()
                    mock_client.getMyNodeInfo.return_value = {
                        'user': {'shortName': f'TEST{attempt_id}', 'hwModel': 'TEST_HW'}
                    }
                    mock_serial.return_value = mock_client
                    
                    with patch('pubsub.pub.subscribe'):
                        result = meshtastic_utils.connect_meshtastic(
                            passed_config=test_config,
                            force_connect=True
                        )
                        results.append((attempt_id, result))
        
        # Start multiple threads
        threads = []
        for i in range(5):
            thread = threading.Thread(target=connect_attempt, args=(i,))
            threads.append(thread)
            thread.start()
        
        # Wait for all threads
        for thread in threads:
            thread.join()
        
        # All attempts should have completed
        assert len(results) == 5

    def test_memory_usage_with_large_messages(self):
        """Test memory usage with large message payloads."""
        # Create a large packet (approaching Meshtastic limits)
        large_text = "A" * 200  # Large but valid message
        
        packet = {
            'decoded': {
                'text': large_text,
                'portnum': 'TEXT_MESSAGE_APP'
            },
            'fromId': 123456789,
            'to': 4294967295,
            'channel': 0
        }
        
        meshtastic_utils.config = {
            'meshtastic': {'meshnet_name': 'TestMesh'}
        }
        meshtastic_utils.matrix_rooms = [
            {'id': '!room1:matrix.org', 'meshtastic_channel': 0}
        ]
        meshtastic_utils.event_loop = asyncio.new_event_loop()
        
        interface = Mock()
        interface.myInfo.my_node_num = 987654321
        
        with patch('mmrelay.matrix_utils.get_interaction_settings') as mock_interactions:
            mock_interactions.return_value = {'reactions': True, 'replies': True}
            with patch('mmrelay.matrix_utils.message_storage_enabled', return_value=True):
                with patch('mmrelay.meshtastic_utils.get_longname', return_value='TestNode'):
                    with patch('mmrelay.meshtastic_utils.get_shortname', return_value='TN'):
                        with patch('mmrelay.plugin_loader.load_plugins', return_value=[]):
                            with patch('asyncio.run_coroutine_threadsafe'):
                                # Should handle large messages without issues
                                meshtastic_utils.on_meshtastic_message(packet, interface)

    def test_unicode_handling_in_messages(self):
        """Test proper handling of Unicode characters in messages."""
        unicode_messages = [
            "Hello 世界",  # Chinese characters
            "Café naïve résumé",  # Accented characters  
            "🚀🌟✨",  # Emoji
            "Ω≈ç√∫µ≤≥",  # Mathematical symbols
            "אבגדהוזחטיכלמנסעפצקרשת"  # Hebrew
        ]
        
        meshtastic_utils.config = {
            'meshtastic': {'meshnet_name': 'TestMesh'}
        }
        meshtastic_utils.matrix_rooms = [
            {'id': '!room1:matrix.org', 'meshtastic_channel': 0}
        ]
        meshtastic_utils.event_loop = asyncio.new_event_loop()
        
        interface = Mock()
        interface.myInfo.my_node_num = 987654321
        
        for unicode_text in unicode_messages:
            packet = {
                'decoded': {
                    'text': unicode_text,
                    'portnum': 'TEXT_MESSAGE_APP'
                },
                'fromId': 123456789,
                'to': 4294967295,
                'channel': 0
            }
            
            with patch('mmrelay.matrix_utils.get_interaction_settings') as mock_interactions:
                mock_interactions.return_value = {'reactions': True, 'replies': True}
                with patch('mmrelay.matrix_utils.message_storage_enabled', return_value=True):
                    with patch('mmrelay.meshtastic_utils.get_longname', return_value='TestNode'):
                        with patch('mmrelay.meshtastic_utils.get_shortname', return_value='TN'):
                            with patch('mmrelay.plugin_loader.load_plugins', return_value=[]):
                                with patch('asyncio.run_coroutine_threadsafe'):
                                    # Should handle Unicode without exceptions
                                    meshtastic_utils.on_meshtastic_message(packet, interface)

    def test_malformed_packet_handling(self):
        """Test handling of malformed or incomplete packets."""
        malformed_packets = [
            {},  # Empty packet
            {'decoded': {}},  # Missing text
            {'decoded': {'text': 'test'}},  # Missing fromId
            {'fromId': 123, 'decoded': None},  # None decoded
            {'fromId': 123, 'decoded': {'portnum': 'UNKNOWN_TYPE'}},  # Unknown portnum
        ]
        
        meshtastic_utils.config = {
            'meshtastic': {'meshnet_name': 'TestMesh'}
        }
        meshtastic_utils.event_loop = asyncio.new_event_loop()
        interface = Mock()
        
        for packet in malformed_packets:
            # Should not raise exceptions on malformed packets
            try:
                meshtastic_utils.on_meshtastic_message(packet, interface)
            except Exception as e:
                pytest.fail(f"Exception raised for malformed packet {packet}: {e}")

    def test_performance_with_high_message_volume(self):
        """Test performance with high volume of messages."""
        import time
        
        meshtastic_utils.config = {
            'meshtastic': {'meshnet_name': 'TestMesh'}
        }
        meshtastic_utils.matrix_rooms = [
            {'id': '!room1:matrix.org', 'meshtastic_channel': 0}
        ]
        meshtastic_utils.event_loop = asyncio.new_event_loop()
        
        interface = Mock()
        interface.myInfo.my_node_num = 987654321
        
        # Process many messages quickly
        start_time = time.time()
        
        with patch('mmrelay.matrix_utils.get_interaction_settings') as mock_interactions:
            mock_interactions.return_value = {'reactions': True, 'replies': True}
            with patch('mmrelay.matrix_utils.message_storage_enabled', return_value=True):
                with patch('mmrelay.meshtastic_utils.get_longname', return_value='TestNode'):
                    with patch('mmrelay.meshtastic_utils.get_shortname', return_value='TN'):
                        with patch('mmrelay.plugin_loader.load_plugins', return_value=[]):
                            with patch('asyncio.run_coroutine_threadsafe'):
                                for i in range(100):
                                    packet = {
                                        'decoded': {
                                            'text': f'Message {i}',
                                            'portnum': 'TEXT_MESSAGE_APP'
                                        },
                                        'fromId': 123456789 + i,
                                        'to': 4294967295,
                                        'channel': 0,
                                        'id': f'msg_{i}'
                                    }
                                    meshtastic_utils.on_meshtastic_message(packet, interface)
        
        end_time = time.time()
        processing_time = end_time - start_time
        
        # Should process 100 messages in reasonable time (< 5 seconds)
        assert processing_time < 5.0, f"Processing took too long: {processing_time}s"


if __name__ == '__main__':
    # Run with pytest for comprehensive testing
    pytest.main([__file__, '-v', '--tb=short', '--durations=10'])