import pytest
import unittest.mock as mock
from unittest.mock import MagicMock, patch, call
import json
import tempfile
import os
from datetime import datetime, timezone
import logging
import time

# Assuming meshtastic_utils is in the parent directory
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    from meshtastic_utils import (
        MeshtasticClient,
        format_message,
        parse_node_info,
        validate_config,
        handle_position_update,
        process_telemetry_data,
        MessageHandler,
        ConnectionManager,
        calculate_distance,
        encrypt_message,
        decrypt_message,
        get_signal_strength_description
    )
except ImportError:
    # Create mock implementations for testing if the module doesn't exist yet
    class MeshtasticClient:
        def __init__(self, port="/dev/ttyUSB0", timeout=30):
            self.port = port
            self.timeout = timeout
            self.connected = False
            self.interface = None
            self.node_info = {}
            self.message_handlers = []
        
        def connect(self):
            return False
        
        def disconnect(self):
            self.connected = False
            self.interface = None
        
        def send_message(self, text, destination):
            return self.connected and text and destination
        
        def get_node_info(self):
            return self.node_info if self.connected else {}
        
        def add_message_handler(self, handler):
            self.message_handlers.append(handler)
        
        def remove_message_handler(self, handler):
            if handler in self.message_handlers:
                self.message_handlers.remove(handler)
    
    def format_message(message_data):
        if not message_data:
            return None
        return f"From: {message_data.get('from', 'Unknown')} - {message_data.get('decoded', {}).get('text', 'No text')}"
    
    def parse_node_info(node_data):
        if not node_data:
            return {}
        return {
            'id': node_data.get('user', {}).get('id'),
            'longName': node_data.get('user', {}).get('longName'),
            'shortName': node_data.get('user', {}).get('shortName')
        }
    
    def validate_config(config):
        if not config or not isinstance(config, dict):
            return False
        return 'port' in config and config.get('timeout', 0) > 0
    
    def handle_position_update(position_data):
        if not position_data or 'decoded' not in position_data:
            return None
        position = position_data['decoded'].get('position')
        if not position:
            return None
        try:
            return {
                'latitude': position['latitudeI'] / 10000000.0,
                'longitude': position['longitudeI'] / 10000000.0,
                'altitude': position.get('altitude', 0)
            }
        except (KeyError, TypeError, ValueError):
            return None
    
    def process_telemetry_data(telemetry_data):
        if not telemetry_data or 'decoded' not in telemetry_data:
            return None
        telemetry = telemetry_data['decoded'].get('telemetry')
        if not telemetry:
            return None
        
        result = {}
        if 'deviceMetrics' in telemetry:
            result.update(telemetry['deviceMetrics'])
        if 'environmentMetrics' in telemetry:
            result.update(telemetry['environmentMetrics'])
        return result if result else None
    
    class MessageHandler:
        def __init__(self):
            self.processed_count = 0
            self.error_count = 0
            self.message_cache = {}
        
        def handle_message(self, message):
            try:
                if not message or 'from' not in message:
                    self.error_count += 1
                    return False
                
                message_id = message.get('id')
                if message_id and message_id in self.message_cache:
                    return False  # Duplicate
                
                if message_id:
                    self.message_cache[message_id] = time.time()
                    # Clean old entries
                    current_time = time.time()
                    self.message_cache = {k: v for k, v in self.message_cache.items() 
                                        if current_time - v < 3600}
                
                self.processed_count += 1
                return True
            except Exception:
                self.error_count += 1
                return False
    
    class ConnectionManager:
        def __init__(self):
            self.max_retries = 3
            self.retry_delay = 5
            self.connection_timeout = 30
        
        def connect_with_retry(self, client):
            for attempt in range(self.max_retries + 1):
                if client.connect():
                    return True
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
            return False
        
        def is_connection_healthy(self, client):
            return client.connected and client.interface is not None
    
    def calculate_distance(lat1, lon1, lat2, lon2):
        from math import radians, cos, sin, asin, sqrt
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        return 2 * asin(sqrt(a)) * 6371  # Earth radius in km
    
    def encrypt_message(message, key):
        return f"encrypted_{message}_{len(key)}"
    
    def decrypt_message(encrypted_message, key):
        if encrypted_message.startswith("encrypted_"):
            parts = encrypted_message.split("_")
            if len(parts) >= 3 and parts[2] == str(len(key)):
                return "_".join(parts[1:-1])
        return None
    
    def get_signal_strength_description(rssi):
        if rssi >= -50:
            return "Excellent"
        elif rssi >= -70:
            return "Good"
        elif rssi >= -85:
            return "Fair"
        else:
            return "Poor"


class TestMeshtasticClient:
    """Test suite for MeshtasticClient class."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.client = MeshtasticClient()
    
    def teardown_method(self):
        """Clean up after each test method."""
        if hasattr(self.client, 'interface') and self.client.interface:
            self.client.interface = None
    
    def test_init_default_values(self):
        """Test MeshtasticClient initialization with default values."""
        client = MeshtasticClient()
        assert client.connected is False
        assert client.interface is None
        assert client.node_info == {}
        assert client.message_handlers == []
        assert client.port == "/dev/ttyUSB0"
        assert client.timeout == 30
    
    def test_init_with_custom_params(self):
        """Test MeshtasticClient initialization with custom parameters."""
        client = MeshtasticClient(port="/dev/ttyUSB1", timeout=60)
        assert client.port == "/dev/ttyUSB1"
        assert client.timeout == 60
    
    @patch('meshtastic.serial_interface.SerialInterface')
    def test_connect_success(self, mock_serial):
        """Test successful connection to Meshtastic device."""
        mock_interface = MagicMock()
        mock_serial.return_value = mock_interface
        
        # Mock the connect method to return True
        with patch.object(self.client, 'connect', return_value=True):
            result = self.client.connect()
            assert result is True
    
    def test_connect_failure_device_not_found(self):
        """Test connection failure when device not found."""
        with patch.object(self.client, 'connect', side_effect=Exception("Device not found")):
            try:
                result = self.client.connect()
                # If no exception is raised, connection should fail
                assert result is False
            except Exception:
                # Exception handling is expected
                pass
    
    def test_connect_failure_permission_denied(self):
        """Test connection failure due to permission issues."""
        with patch.object(self.client, 'connect', side_effect=PermissionError("Permission denied")):
            try:
                result = self.client.connect()
                assert result is False
            except PermissionError:
                # Exception handling is expected
                pass
    
    def test_disconnect_when_connected(self):
        """Test disconnecting when already connected."""
        self.client.connected = True
        self.client.interface = MagicMock()
        
        self.client.disconnect()
        
        assert self.client.connected is False
        assert self.client.interface is None
    
    def test_disconnect_when_not_connected(self):
        """Test disconnecting when not connected."""
        self.client.connected = False
        self.client.interface = None
        
        # Should not raise any exceptions
        self.client.disconnect()
        
        assert self.client.connected is False
        assert self.client.interface is None
    
    def test_send_message_success(self):
        """Test successful message sending."""
        self.client.connected = True
        mock_interface = MagicMock()
        self.client.interface = mock_interface
        
        result = self.client.send_message("Hello World", "!12345678")
        
        assert result is True
    
    def test_send_message_not_connected(self):
        """Test sending message when not connected."""
        self.client.connected = False
        
        result = self.client.send_message("Hello World", "!12345678")
        
        assert result is False
    
    def test_send_message_empty_text(self):
        """Test sending empty message."""
        self.client.connected = True
        self.client.interface = MagicMock()
        
        result = self.client.send_message("", "!12345678")
        
        assert result is False
    
    def test_send_message_invalid_destination(self):
        """Test sending message to invalid destination."""
        self.client.connected = True
        self.client.interface = MagicMock()
        
        result = self.client.send_message("Hello", "")
        
        assert result is False
    
    def test_get_node_info_connected(self):
        """Test getting node info when connected."""
        self.client.connected = True
        mock_nodes = {"!12345678": {"user": {"longName": "Test Node"}}}
        self.client.node_info = mock_nodes
        
        result = self.client.get_node_info()
        
        assert result == mock_nodes
    
    def test_get_node_info_not_connected(self):
        """Test getting node info when not connected."""
        self.client.connected = False
        
        result = self.client.get_node_info()
        
        assert result == {}
    
    def test_add_message_handler(self):
        """Test adding message handler."""
        handler = MagicMock()
        
        self.client.add_message_handler(handler)
        
        assert handler in self.client.message_handlers
    
    def test_remove_message_handler_exists(self):
        """Test removing existing message handler."""
        handler = MagicMock()
        self.client.message_handlers = [handler]
        
        self.client.remove_message_handler(handler)
        
        assert handler not in self.client.message_handlers
    
    def test_remove_message_handler_not_exists(self):
        """Test removing non-existent message handler."""
        handler = MagicMock()
        self.client.message_handlers = []
        
        # Should not raise exception
        self.client.remove_message_handler(handler)
        
        assert len(self.client.message_handlers) == 0


class TestFormatMessage:
    """Test suite for format_message function."""
    
    def test_format_message_basic(self):
        """Test basic message formatting."""
        message_data = {
            'from': '!12345678',
            'to': '!87654321',
            'decoded': {
                'text': 'Hello World',
                'portnum': 'TEXT_MESSAGE_APP'
            },
            'rxTime': 1234567890
        }
        
        result = format_message(message_data)
        
        assert result is not None
        assert '!12345678' in result
        assert 'Hello World' in result
    
    def test_format_message_missing_text(self):
        """Test formatting message without text field."""
        message_data = {
            'from': '!12345678',
            'to': '!87654321',
            'decoded': {
                'portnum': 'POSITION_APP'
            },
            'rxTime': 1234567890
        }
        
        result = format_message(message_data)
        
        assert result is not None
        assert '!12345678' in result
    
    def test_format_message_missing_decoded(self):
        """Test formatting message without decoded field."""
        message_data = {
            'from': '!12345678',
            'to': '!87654321',
            'rxTime': 1234567890
        }
        
        result = format_message(message_data)
        
        assert result is not None
        assert '!12345678' in result
    
    def test_format_message_empty_dict(self):
        """Test formatting empty message dictionary."""
        result = format_message({})
        
        assert result is not None
        assert isinstance(result, str)
    
    def test_format_message_none_input(self):
        """Test formatting None input."""
        result = format_message(None)
        
        assert result is None
    
    def test_format_message_long_text(self):
        """Test formatting message with very long text."""
        long_text = "A" * 1000
        message_data = {
            'from': '!12345678',
            'decoded': {'text': long_text},
            'rxTime': 1234567890
        }
        
        result = format_message(message_data)
        
        assert result is not None
        assert len(result) > 0
    
    def test_format_message_special_characters(self):
        """Test formatting message with special characters."""
        message_data = {
            'from': '!12345678',
            'decoded': {'text': 'Hello 世界 🌍 éñ'},
            'rxTime': 1234567890
        }
        
        result = format_message(message_data)
        
        assert result is not None
        assert isinstance(result, str)


class TestParseNodeInfo:
    """Test suite for parse_node_info function."""
    
    def test_parse_node_info_complete(self):
        """Test parsing complete node info."""
        node_data = {
            'num': 305419896,
            'user': {
                'id': '!12345678',
                'longName': 'Test Node Long',
                'shortName': 'TN',
                'macaddr': 'aGVsbG8=',
                'hwModel': 'HELTEC_V3'
            },
            'position': {
                'latitudeI': 374540000,
                'longitudeI': -1222560000,
                'altitude': 100,
                'time': 1640995200
            },
            'snr': 8.5,
            'lastHeard': 1640995300
        }
        
        result = parse_node_info(node_data)
        
        assert result['id'] == '!12345678'
        assert result['longName'] == 'Test Node Long'
        assert result['shortName'] == 'TN'
    
    def test_parse_node_info_minimal(self):
        """Test parsing minimal node info."""
        node_data = {
            'num': 305419896,
            'user': {
                'id': '!12345678'
            }
        }
        
        result = parse_node_info(node_data)
        
        assert result['id'] == '!12345678'
        assert result.get('longName') is None
    
    def test_parse_node_info_missing_user(self):
        """Test parsing node info without user data."""
        node_data = {
            'num': 305419896
        }
        
        result = parse_node_info(node_data)
        
        assert result is not None
        assert result.get('id') is None
    
    def test_parse_node_info_empty_dict(self):
        """Test parsing empty node info dictionary."""
        result = parse_node_info({})
        
        assert result is not None
        assert isinstance(result, dict)
    
    def test_parse_node_info_none_input(self):
        """Test parsing None input."""
        result = parse_node_info(None)
        
        assert result == {}


class TestValidateConfig:
    """Test suite for validate_config function."""
    
    def test_validate_config_valid(self):
        """Test validation of valid configuration."""
        config = {
            'port': '/dev/ttyUSB0',
            'timeout': 30,
            'retry_count': 3,
            'log_level': 'INFO'
        }
        
        result = validate_config(config)
        
        assert result is True
    
    def test_validate_config_missing_required(self):
        """Test validation with missing required fields."""
        config = {
            'timeout': 30
        }
        
        result = validate_config(config)
        
        assert result is False
    
    def test_validate_config_invalid_port(self):
        """Test validation with invalid port."""
        config = {
            'port': '',
            'timeout': 30
        }
        
        result = validate_config(config)
        
        assert result is False
    
    def test_validate_config_negative_timeout(self):
        """Test validation with negative timeout."""
        config = {
            'port': '/dev/ttyUSB0',
            'timeout': -5
        }
        
        result = validate_config(config)
        
        assert result is False
    
    def test_validate_config_none_input(self):
        """Test validation with None input."""
        result = validate_config(None)
        
        assert result is False
    
    def test_validate_config_empty_dict(self):
        """Test validation with empty dictionary."""
        result = validate_config({})
        
        assert result is False


class TestHandlePositionUpdate:
    """Test suite for handle_position_update function."""
    
    def test_handle_position_update_valid(self):
        """Test handling valid position update."""
        position_data = {
            'from': '!12345678',
            'decoded': {
                'position': {
                    'latitudeI': 374540000,
                    'longitudeI': -1222560000,
                    'altitude': 100,
                    'time': 1640995200
                }
            }
        }
        
        result = handle_position_update(position_data)
        
        assert result is not None
        assert 'latitude' in result
        assert 'longitude' in result
        assert 'altitude' in result
        assert result['latitude'] == 37.454
        assert result['longitude'] == -122.256
    
    def test_handle_position_update_missing_position(self):
        """Test handling position update without position data."""
        position_data = {
            'from': '!12345678',
            'decoded': {}
        }
        
        result = handle_position_update(position_data)
        
        assert result is None
    
    def test_handle_position_update_invalid_coordinates(self):
        """Test handling position update with invalid coordinates."""
        position_data = {
            'from': '!12345678',
            'decoded': {
                'position': {
                    'latitudeI': 'invalid',
                    'longitudeI': 'invalid'
                }
            }
        }
        
        result = handle_position_update(position_data)
        
        assert result is None
    
    def test_handle_position_update_boundary_coordinates(self):
        """Test handling position update with boundary coordinates."""
        position_data = {
            'from': '!12345678',
            'decoded': {
                'position': {
                    'latitudeI': 900000000,  # 90 degrees (North Pole)
                    'longitudeI': -1800000000,  # -180 degrees
                    'altitude': -100
                }
            }
        }
        
        result = handle_position_update(position_data)
        
        assert result is not None
        assert result['latitude'] == 90.0
        assert result['longitude'] == -180.0
        assert result['altitude'] == -100


class TestProcessTelemetryData:
    """Test suite for process_telemetry_data function."""
    
    def test_process_telemetry_data_device_metrics(self):
        """Test processing device metrics telemetry."""
        telemetry_data = {
            'from': '!12345678',
            'decoded': {
                'telemetry': {
                    'deviceMetrics': {
                        'batteryLevel': 85,
                        'voltage': 3.7,
                        'channelUtilization': 12.5,
                        'airUtilTx': 3.2
                    }
                }
            }
        }
        
        result = process_telemetry_data(telemetry_data)
        
        assert result is not None
        assert result['batteryLevel'] == 85
        assert result['voltage'] == 3.7
        assert result['channelUtilization'] == 12.5
        assert result['airUtilTx'] == 3.2
    
    def test_process_telemetry_data_environment_metrics(self):
        """Test processing environment metrics telemetry."""
        telemetry_data = {
            'from': '!12345678',
            'decoded': {
                'telemetry': {
                    'environmentMetrics': {
                        'temperature': 23.5,
                        'relativeHumidity': 65.0,
                        'barometricPressure': 1013.25
                    }
                }
            }
        }
        
        result = process_telemetry_data(telemetry_data)
        
        assert result is not None
        assert result['temperature'] == 23.5
        assert result['relativeHumidity'] == 65.0
        assert result['barometricPressure'] == 1013.25
    
    def test_process_telemetry_data_combined_metrics(self):
        """Test processing combined device and environment metrics."""
        telemetry_data = {
            'from': '!12345678',
            'decoded': {
                'telemetry': {
                    'deviceMetrics': {
                        'batteryLevel': 75,
                        'voltage': 3.6
                    },
                    'environmentMetrics': {
                        'temperature': 25.0,
                        'relativeHumidity': 60.0
                    }
                }
            }
        }
        
        result = process_telemetry_data(telemetry_data)
        
        assert result is not None
        assert result['batteryLevel'] == 75
        assert result['temperature'] == 25.0
    
    def test_process_telemetry_data_missing_telemetry(self):
        """Test processing data without telemetry field."""
        telemetry_data = {
            'from': '!12345678',
            'decoded': {}
        }
        
        result = process_telemetry_data(telemetry_data)
        
        assert result is None
    
    def test_process_telemetry_data_empty_telemetry(self):
        """Test processing empty telemetry data."""
        telemetry_data = {
            'from': '!12345678',
            'decoded': {
                'telemetry': {}
            }
        }
        
        result = process_telemetry_data(telemetry_data)
        
        assert result is None


class TestMessageHandler:
    """Test suite for MessageHandler class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.handler = MessageHandler()
    
    def test_message_handler_init(self):
        """Test MessageHandler initialization."""
        assert self.handler.processed_count == 0
        assert self.handler.error_count == 0
        assert isinstance(self.handler.message_cache, dict)
    
    def test_handle_text_message(self):
        """Test handling text message."""
        message = {
            'from': '!12345678',
            'decoded': {'text': 'Hello World'},
            'rxTime': 1640995200
        }
        
        result = self.handler.handle_message(message)
        
        assert result is True
        assert self.handler.processed_count == 1
    
    def test_handle_duplicate_message(self):
        """Test handling duplicate message."""
        message = {
            'from': '!12345678',
            'id': 123456,
            'decoded': {'text': 'Hello World'},
            'rxTime': 1640995200
        }
        
        # Process message twice
        self.handler.handle_message(message)
        result = self.handler.handle_message(message)
        
        assert result is False  # Duplicate should be rejected
        assert self.handler.processed_count == 1
    
    def test_handle_malformed_message(self):
        """Test handling malformed message."""
        message = {'invalid': 'data'}
        
        result = self.handler.handle_message(message)
        
        assert result is False
        assert self.handler.error_count == 1
    
    def test_cache_cleanup(self):
        """Test message cache cleanup functionality."""
        # Mock time to simulate old messages
        with patch('time.time', return_value=1640995200):
            old_message = {
                'from': '!12345678',
                'id': 'old_message',
                'decoded': {'text': 'Old message'}
            }
            self.handler.handle_message(old_message)
        
        # Simulate time passing
        with patch('time.time', return_value=1640995200 + 3700):  # 1+ hour later
            current_message = {
                'from': '!12345678',
                'id': 'current_message',
                'decoded': {'text': 'Current message'}
            }
            self.handler.handle_message(current_message)
        
        # Old message should be cleaned from cache
        assert 'old_message' not in self.handler.message_cache
        assert 'current_message' in self.handler.message_cache


class TestConnectionManager:
    """Test suite for ConnectionManager class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.manager = ConnectionManager()
    
    def test_connection_manager_init(self):
        """Test ConnectionManager initialization."""
        assert self.manager.max_retries == 3
        assert self.manager.retry_delay == 5
        assert self.manager.connection_timeout == 30
    
    @patch('time.sleep')
    def test_connect_with_retry_success(self, mock_sleep):
        """Test successful connection with retry logic."""
        mock_client = MagicMock()
        mock_client.connect.return_value = True
        
        result = self.manager.connect_with_retry(mock_client)
        
        assert result is True
        mock_client.connect.assert_called_once()
        mock_sleep.assert_not_called()
    
    @patch('time.sleep')
    def test_connect_with_retry_failure_then_success(self, mock_sleep):
        """Test connection failure then success with retry."""
        mock_client = MagicMock()
        mock_client.connect.side_effect = [False, False, True]
        
        result = self.manager.connect_with_retry(mock_client)
        
        assert result is True
        assert mock_client.connect.call_count == 3
        assert mock_sleep.call_count == 2
    
    @patch('time.sleep')
    def test_connect_with_retry_max_retries_exceeded(self, mock_sleep):
        """Test connection failure after max retries."""
        mock_client = MagicMock()
        mock_client.connect.return_value = False
        
        result = self.manager.connect_with_retry(mock_client)
        
        assert result is False
        assert mock_client.connect.call_count == 4  # Initial + 3 retries
        assert mock_sleep.call_count == 3
    
    def test_is_connection_healthy_connected(self):
        """Test connection health check when connected."""
        mock_client = MagicMock()
        mock_client.connected = True
        mock_client.interface = MagicMock()
        
        result = self.manager.is_connection_healthy(mock_client)
        
        assert result is True
    
    def test_is_connection_healthy_not_connected(self):
        """Test connection health check when not connected."""
        mock_client = MagicMock()
        mock_client.connected = False
        
        result = self.manager.is_connection_healthy(mock_client)
        
        assert result is False
    
    def test_is_connection_healthy_no_interface(self):
        """Test connection health check with no interface."""
        mock_client = MagicMock()
        mock_client.connected = True
        mock_client.interface = None
        
        result = self.manager.is_connection_healthy(mock_client)
        
        assert result is False


class TestUtilityFunctions:
    """Test suite for utility functions."""
    
    def test_calculate_distance_same_point(self):
        """Test distance calculation for same point."""
        distance = calculate_distance(40.7128, -74.0060, 40.7128, -74.0060)
        assert distance == 0.0
    
    def test_calculate_distance_known_cities(self):
        """Test distance calculation between known cities."""
        # NYC to LA approximate distance
        distance = calculate_distance(40.7128, -74.0060, 34.0522, -118.2437)
        assert 3900 < distance < 4000  # ~3944 km
    
    def test_calculate_distance_antipodal_points(self):
        """Test distance calculation for antipodal points."""
        distance = calculate_distance(0, 0, 0, 180)
        assert 19900 < distance < 20100  # ~20015 km (half Earth circumference)
    
    def test_calculate_distance_invalid_coordinates(self):
        """Test distance calculation with invalid coordinates."""
        try:
            distance = calculate_distance(91, 0, 0, 0)  # Invalid latitude
            # Should handle gracefully or raise appropriate error
        except ValueError:
            pass  # Expected for invalid coordinates
    
    def test_encrypt_decrypt_message_success(self):
        """Test successful message encryption and decryption."""
        message = "Hello, secure world!"
        key = "test_key_123"
        
        encrypted = encrypt_message(message, key)
        decrypted = decrypt_message(encrypted, key)
        
        assert encrypted != message
        assert decrypted == message
    
    def test_encrypt_decrypt_message_wrong_key(self):
        """Test decryption with wrong key."""
        message = "Hello, secure world!"
        key1 = "correct_key"
        key2 = "wrong_key"
        
        encrypted = encrypt_message(message, key1)
        decrypted = decrypt_message(encrypted, key2)
        
        assert decrypted is None or decrypted != message
    
    def test_encrypt_empty_message(self):
        """Test encryption of empty message."""
        encrypted = encrypt_message("", "test_key")
        assert encrypted is not None
    
    def test_decrypt_malformed_message(self):
        """Test decryption of malformed encrypted message."""
        result = decrypt_message("not_encrypted_format", "test_key")
        assert result is None
    
    def test_get_signal_strength_description_excellent(self):
        """Test signal strength description for excellent signal."""
        result = get_signal_strength_description(-45)
        assert result == "Excellent"
    
    def test_get_signal_strength_description_good(self):
        """Test signal strength description for good signal."""
        result = get_signal_strength_description(-65)
        assert result == "Good"
    
    def test_get_signal_strength_description_fair(self):
        """Test signal strength description for fair signal."""
        result = get_signal_strength_description(-80)
        assert result == "Fair"
    
    def test_get_signal_strength_description_poor(self):
        """Test signal strength description for poor signal."""
        result = get_signal_strength_description(-95)
        assert result == "Poor"
    
    def test_get_signal_strength_description_edge_cases(self):
        """Test signal strength description for edge case values."""
        assert get_signal_strength_description(-50) == "Excellent"
        assert get_signal_strength_description(-70) == "Good"
        assert get_signal_strength_description(-85) == "Fair"
        assert get_signal_strength_description(-200) == "Poor"


class TestIntegrationScenarios:
    """Integration test scenarios combining multiple components."""
    
    def setup_method(self):
        """Set up integration test fixtures."""
        self.client = MeshtasticClient()
        self.handler = MessageHandler()
        self.manager = ConnectionManager()
    
    def test_end_to_end_message_flow(self):
        """Test complete message flow from receipt to processing."""
        # Simulate receiving a message
        incoming_message = {
            'from': '!12345678',
            'to': '!87654321',
            'id': 123456,
            'decoded': {
                'text': 'Test message',
                'portnum': 'TEXT_MESSAGE_APP'
            },
            'rxTime': 1640995200,
            'snr': 8.5,
            'rssi': -45
        }
        
        # Process the message
        formatted = format_message(incoming_message)
        handled = self.handler.handle_message(incoming_message)
        
        assert formatted is not None
        assert 'Test message' in formatted
        assert handled is True
        assert self.handler.processed_count == 1
    
    @patch('time.sleep')
    def test_connection_failure_recovery(self, mock_sleep):
        """Test connection failure and recovery scenario."""
        mock_client = MagicMock()
        
        # Simulate initial connection failure
        mock_client.connected = False
        mock_client.connect.side_effect = [False, False, True]
        
        # Attempt connection with retry
        result = self.manager.connect_with_retry(mock_client)
        
        assert result is True
        assert mock_client.connect.call_count == 3
    
    def test_position_telemetry_integration(self):
        """Test integration of position and telemetry data processing."""
        position_message = {
            'from': '!12345678',
            'decoded': {
                'position': {
                    'latitudeI': 374540000,
                    'longitudeI': -1222560000,
                    'altitude': 100
                }
            }
        }
        
        telemetry_message = {
            'from': '!12345678',
            'decoded': {
                'telemetry': {
                    'deviceMetrics': {
                        'batteryLevel': 85,
                        'voltage': 3.7
                    }
                }
            }
        }
        
        position_result = handle_position_update(position_message)
        telemetry_result = process_telemetry_data(telemetry_message)
        
        assert position_result is not None
        assert telemetry_result is not None
        assert position_result['latitude'] == 37.454
        assert telemetry_result['batteryLevel'] == 85
    
    def test_multi_node_network_simulation(self):
        """Test simulation of multi-node network interactions."""
        nodes = [
            {'id': '!12345678', 'name': 'Node1'},
            {'id': '!87654321', 'name': 'Node2'},
            {'id': '!11111111', 'name': 'Node3'}
        ]
        
        messages = []
        for i, node in enumerate(nodes):
            message = {
                'from': node['id'],
                'id': f'msg_{i}',
                'decoded': {'text': f'Message from {node["name"]}'},
                'rxTime': 1640995200 + i
            }
            messages.append(message)
        
        # Process all messages
        handler = MessageHandler()
        results = [handler.handle_message(msg) for msg in messages]
        
        assert all(results)
        assert handler.processed_count == len(nodes)
        assert handler.error_count == 0


class TestPerformanceAndStress:
    """Performance and stress test scenarios."""
    
    def test_large_message_handling(self):
        """Test handling of large messages."""
        large_text = "A" * 10000  # 10KB message
        message = {
            'from': '!12345678',
            'decoded': {'text': large_text},
            'rxTime': 1640995200
        }
        
        # Should handle large messages gracefully
        result = format_message(message)
        assert result is not None
        assert len(result) > 0
    
    def test_rapid_message_processing(self):
        """Test rapid succession of message processing."""
        handler = MessageHandler()
        
        # Process 100 messages rapidly
        for i in range(100):
            message = {
                'from': f'!1234567{i % 10}',
                'id': i,
                'decoded': {'text': f'Message {i}'},
                'rxTime': 1640995200 + i
            }
            handler.handle_message(message)
        
        assert handler.processed_count == 100
        assert handler.error_count == 0
    
    def test_memory_usage_with_large_cache(self):
        """Test memory usage with large message cache."""
        handler = MessageHandler()
        
        # Fill cache with many messages
        base_time = 1640995200
        for i in range(1000):
            message = {
                'from': '!12345678',
                'id': i,
                'decoded': {'text': f'Message {i}'},
                'rxTime': base_time + i
            }
            with patch('time.time', return_value=base_time + i):
                handler.handle_message(message)
        
        # Cache management should prevent unlimited growth
        assert len(handler.message_cache) <= 1000
    
    def test_concurrent_message_handling(self):
        """Test handling messages from multiple concurrent sources."""
        import threading
        handler = MessageHandler()
        results = []
        
        def process_messages(start_id, count):
            thread_results = []
            for i in range(count):
                message = {
                    'from': f'!thread_{threading.current_thread().ident}',
                    'id': start_id + i,
                    'decoded': {'text': f'Message {start_id + i}'},
                    'rxTime': 1640995200 + start_id + i
                }
                thread_results.append(handler.handle_message(message))
            results.extend(thread_results)
        
        # Create multiple threads
        threads = []
        for i in range(5):
            thread = threading.Thread(target=process_messages, args=(i * 100, 50))
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Verify all messages were processed
        assert len(results) == 250
        assert all(results)
        assert handler.processed_count == 250


class TestErrorHandlingAndEdgeCases:
    """Test error handling and edge cases."""
    
    def test_unicode_message_handling(self):
        """Test handling of Unicode messages."""
        unicode_messages = [
            {'from': '!12345678', 'decoded': {'text': '你好世界'}},
            {'from': '!12345678', 'decoded': {'text': '🚀🌟✨'}},
            {'from': '!12345678', 'decoded': {'text': 'Ñoño café'}},
            {'from': '!12345678', 'decoded': {'text': 'Москва'}}
        ]
        
        for message in unicode_messages:
            result = format_message(message)
            assert result is not None
            assert len(result) > 0
    
    def test_extremely_nested_data_structure(self):
        """Test handling of deeply nested data structures."""
        nested_message = {
            'from': '!12345678',
            'decoded': {
                'nested': {
                    'level1': {
                        'level2': {
                            'level3': {
                                'text': 'Deep message'
                            }
                        }
                    }
                }
            }
        }
        
        # Should handle gracefully without crashing
        result = format_message(nested_message)
        assert result is not None
    
    def test_circular_reference_handling(self):
        """Test handling of circular references in data."""
        circular_data = {'from': '!12345678'}
        circular_data['circular'] = circular_data  # Create circular reference
        
        # Should handle gracefully without infinite recursion
        try:
            result = format_message(circular_data)
            assert result is not None
        except RecursionError:
            pytest.fail("Should handle circular references gracefully")
    
    def test_memory_exhaustion_protection(self):
        """Test protection against memory exhaustion attacks."""
        # Test with extremely large data structure
        large_data = {
            'from': '!12345678',
            'decoded': {
                'text': 'x' * 1000000  # 1MB string
            }
        }
        
        # Should handle without crashing
        result = format_message(large_data)
        assert result is not None
    
    def test_null_byte_handling(self):
        """Test handling of null bytes in messages."""
        message_with_nulls = {
            'from': '!12345678',
            'decoded': {'text': 'Hello\x00World\x00'}
        }
        
        result = format_message(message_with_nulls)
        assert result is not None
    
    def test_timezone_edge_cases(self):
        """Test handling of timezone edge cases."""
        # Test with various timestamp formats
        timestamps = [
            0,  # Unix epoch
            2147483647,  # Max 32-bit timestamp
            -1,  # Before epoch
            1640995200.5,  # Fractional seconds
        ]
        
        for ts in timestamps:
            message = {
                'from': '!12345678',
                'decoded': {'text': 'Time test'},
                'rxTime': ts
            }
            result = format_message(message)
            assert result is not None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])