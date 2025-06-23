"""
Comprehensive unit tests for meshtastic_utils module.
Tests cover happy paths, edge cases, failure conditions, and all public interfaces.
Testing Framework: pytest
"""

import pytest
import time
import math
from datetime import datetime
from typing import Dict, Any, List
from unittest.mock import patch, MagicMock

from mmrelay import meshtastic_utils


class TestParseMeshtasticMessage:
    """Test suite for parse_meshtastic_message function."""
    
    @pytest.fixture
    def valid_text_message(self) -> Dict[str, Any]:
        """Sample valid text message."""
        return {
            'id': '1234567890',
            'timestamp': 1699000000,
            'from': '!aabbccdd',
            'to': '!eeffgghh',
            'channel': 0,
            'hopLimit': 3,
            'wantAck': True,
            'priority': 64,
            'rxTime': 1699000001,
            'rxSnr': 8.5,
            'rxRssi': -45,
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'text': 'Hello, world!'
            }
        }
    
    @pytest.fixture
    def valid_position_message(self) -> Dict[str, Any]:
        """Sample valid position message."""
        return {
            'id': '1234567891',
            'timestamp': 1699000002,
            'from': '!aabbccdd',
            'to': '^all',
            'decoded': {
                'portnum': 'POSITION_APP',
                'position': {
                    'latitude': 37.7749,
                    'longitude': -122.4194,
                    'altitude': 50,
                    'time': 1699000002,
                    'precisionBits': 32
                }
            }
        }
    
    @pytest.fixture
    def valid_nodeinfo_message(self) -> Dict[str, Any]:
        """Sample valid nodeinfo message."""
        return {
            'id': '1234567892',
            'timestamp': 1699000003,
            'from': '!aabbccdd',
            'to': '^all',
            'decoded': {
                'portnum': 'NODEINFO_APP',
                'user': {
                    'id': '!aabbccdd',
                    'longName': 'Test Node',
                    'shortName': 'TEST',
                    'macaddr': 'aabbccddeeff',
                    'hwModel': 'TBEAM',
                    'isLicensed': True
                }
            }
        }
    
    @pytest.fixture
    def valid_telemetry_message(self) -> Dict[str, Any]:
        """Sample valid telemetry message."""
        return {
            'id': '1234567893',
            'timestamp': 1699000004,
            'from': '!aabbccdd',
            'to': '^all',
            'decoded': {
                'portnum': 'TELEMETRY_APP',
                'telemetry': {
                    'deviceMetrics': {
                        'batteryLevel': 85,
                        'voltage': 4.12,
                        'channelUtilization': 15.5,
                        'airUtilTx': 2.3
                    }
                }
            }
        }
    
    def test_parse_text_message_success(self, valid_text_message):
        """Test successful parsing of text message."""
        result = meshtastic_utils.parse_meshtastic_message(valid_text_message)
        
        assert result is not None
        assert result['id'] == '1234567890'
        assert result['timestamp'] == 1699000000
        assert result['from_id'] == '!aabbccdd'
        assert result['to_id'] == '!eeffgghh'
        assert result['type'] == 'text'
        assert result['text'] == 'Hello, world!'
        assert result['channel'] == 0
        assert result['hop_limit'] == 3
        assert result['want_ack'] is True
        assert result['rx_snr'] == 8.5
        assert result['rx_rssi'] == -45
    
    def test_parse_position_message_success(self, valid_position_message):
        """Test successful parsing of position message."""
        result = meshtastic_utils.parse_meshtastic_message(valid_position_message)
        
        assert result is not None
        assert result['type'] == 'position'
        assert result['position']['latitude'] == 37.7749
        assert result['position']['longitude'] == -122.4194
        assert result['position']['altitude'] == 50
        assert result['position']['time'] == 1699000002
        assert result['position']['precision_bits'] == 32
    
    def test_parse_nodeinfo_message_success(self, valid_nodeinfo_message):
        """Test successful parsing of nodeinfo message."""
        result = meshtastic_utils.parse_meshtastic_message(valid_nodeinfo_message)
        
        assert result is not None
        assert result['type'] == 'nodeinfo'
        assert result['nodeinfo']['id'] == '!aabbccdd'
        assert result['nodeinfo']['long_name'] == 'Test Node'
        assert result['nodeinfo']['short_name'] == 'TEST'
        assert result['nodeinfo']['hw_model'] == 'TBEAM'
        assert result['nodeinfo']['is_licensed'] is True
    
    def test_parse_telemetry_message_success(self, valid_telemetry_message):
        """Test successful parsing of telemetry message."""
        result = meshtastic_utils.parse_meshtastic_message(valid_telemetry_message)
        
        assert result is not None
        assert result['type'] == 'telemetry'
        assert 'deviceMetrics' in result['telemetry']
        assert result['telemetry']['deviceMetrics']['batteryLevel'] == 85
        assert result['telemetry']['deviceMetrics']['voltage'] == 4.12
    
    def test_parse_unknown_portnum_message(self):
        """Test parsing message with unknown portnum."""
        message = {
            'id': '1234567894',
            'timestamp': 1699000005,
            'from': '!aabbccdd',
            'to': '^all',
            'decoded': {
                'portnum': 'UNKNOWN_APP',
                'data': 'some binary data'
            }
        }
        
        result = meshtastic_utils.parse_meshtastic_message(message)
        
        assert result is not None
        assert result['type'] == 'unknown'
        assert 'raw_decoded' in result
        assert result['raw_decoded']['portnum'] == 'UNKNOWN_APP'
    
    def test_parse_message_with_defaults(self):
        """Test parsing message with minimal fields (should use defaults)."""
        message = {
            'from': '!aabbccdd',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'text': 'Simple message'
            }
        }
        
        with patch('time.time', return_value=1699000000):
            result = meshtastic_utils.parse_meshtastic_message(message)
        
        assert result is not None
        assert result['timestamp'] == 1699000000  # Should use current time
        assert result['channel'] == 0  # Default value
        assert result['hop_limit'] == 0  # Default value
        assert result['want_ack'] is False  # Default value
        assert result['rx_snr'] == 0.0  # Default value
    
    def test_parse_message_empty_text(self):
        """Test parsing text message with empty text."""
        message = {
            'from': '!aabbccdd',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'text': ''
            }
        }
        
        result = meshtastic_utils.parse_meshtastic_message(message)
        
        assert result is not None
        assert result['type'] == 'text'
        assert result['text'] == ''
    
    def test_parse_message_missing_text_field(self):
        """Test parsing text message without text field."""
        message = {
            'from': '!aabbccdd',
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP'
            }
        }
        
        result = meshtastic_utils.parse_meshtastic_message(message)
        
        assert result is not None
        assert result['type'] == 'text'
        assert result['text'] == ''  # Should default to empty string
    
    def test_parse_message_none_input(self):
        """Test parsing with None input."""
        result = meshtastic_utils.parse_meshtastic_message(None)
        assert result is None
    
    def test_parse_message_empty_dict(self):
        """Test parsing with empty dictionary."""
        result = meshtastic_utils.parse_meshtastic_message({})
        
        assert result is not None  # Should still create basic structure
        assert result['from_id'] is None
        assert result['to_id'] is None
    
    def test_parse_message_invalid_type(self):
        """Test parsing with invalid input type."""
        result = meshtastic_utils.parse_meshtastic_message("invalid")
        assert result is None
        
        result = meshtastic_utils.parse_meshtastic_message(123)
        assert result is None
        
        result = meshtastic_utils.parse_meshtastic_message([])
        assert result is None
    
    def test_parse_message_no_decoded_field(self):
        """Test parsing message without decoded field."""
        message = {
            'id': '1234567890',
            'from': '!aabbccdd',
            'to': '!eeffgghh'
        }
        
        result = meshtastic_utils.parse_meshtastic_message(message)
        
        assert result is not None
        assert result['id'] == '1234567890'
        assert result['from_id'] == '!aabbccdd'
        # Should still return basic message structure
    
    def test_parse_message_exception_handling(self):
        """Test exception handling during parsing."""
        # Create a message that will cause an exception during processing
        message = {
            'timestamp': 'invalid_timestamp',  # Should be int
            'decoded': {
                'portnum': 'TEXT_MESSAGE_APP',
                'text': 'Test'
            }
        }
        
        result = meshtastic_utils.parse_meshtastic_message(message)
        # Should handle gracefully and return None on exception
        assert result is None


class TestFormatNodeId:
    """Test suite for format_node_id function."""
    
    def test_format_valid_node_id(self):
        """Test formatting valid node IDs."""
        assert meshtastic_utils.format_node_id(0xaabbccdd) == "!aabbccdd"
        assert meshtastic_utils.format_node_id(0x12345678) == "!12345678"
        assert meshtastic_utils.format_node_id(0) == "!00000000"
        assert meshtastic_utils.format_node_id(0xffffffff) == "!ffffffff"
    
    def test_format_node_id_small_numbers(self):
        """Test formatting small node numbers."""
        assert meshtastic_utils.format_node_id(1) == "!00000001"
        assert meshtastic_utils.format_node_id(255) == "!000000ff"
        assert meshtastic_utils.format_node_id(65535) == "!0000ffff"
    
    def test_format_node_id_invalid_input(self):
        """Test formatting with invalid inputs."""
        assert meshtastic_utils.format_node_id(None) == ""
        assert meshtastic_utils.format_node_id("string") == ""
        assert meshtastic_utils.format_node_id(-1) == ""
        assert meshtastic_utils.format_node_id(-100) == ""
        assert meshtastic_utils.format_node_id(3.14) == ""
        assert meshtastic_utils.format_node_id([]) == ""
        assert meshtastic_utils.format_node_id({}) == ""


class TestParseNodeId:
    """Test suite for parse_node_id function."""
    
    def test_parse_valid_node_ids_with_prefix(self):
        """Test parsing valid node IDs with ! prefix."""
        assert meshtastic_utils.parse_node_id("!aabbccdd") == 0xaabbccdd
        assert meshtastic_utils.parse_node_id("!12345678") == 0x12345678
        assert meshtastic_utils.parse_node_id("!00000000") == 0
        assert meshtastic_utils.parse_node_id("!ffffffff") == 0xffffffff
    
    def test_parse_valid_node_ids_without_prefix(self):
        """Test parsing valid node IDs without ! prefix."""
        assert meshtastic_utils.parse_node_id("aabbccdd") == 0xaabbccdd
        assert meshtastic_utils.parse_node_id("12345678") == 0x12345678
        assert meshtastic_utils.parse_node_id("00000000") == 0
        assert meshtastic_utils.parse_node_id("ffffffff") == 0xffffffff
    
    def test_parse_node_id_case_insensitive(self):
        """Test parsing node IDs is case insensitive."""
        assert meshtastic_utils.parse_node_id("!AABBCCDD") == 0xaabbccdd
        assert meshtastic_utils.parse_node_id("!AaBbCcDd") == 0xaabbccdd
        assert meshtastic_utils.parse_node_id("AABBCCDD") == 0xaabbccdd
    
    def test_parse_node_id_invalid_hex(self):
        """Test parsing invalid hex strings."""
        assert meshtastic_utils.parse_node_id("!invalid") is None
        assert meshtastic_utils.parse_node_id("!gggggggg") is None
        assert meshtastic_utils.parse_node_id("!12345xyz") is None
        assert meshtastic_utils.parse_node_id("invalid") is None
    
    def test_parse_node_id_invalid_input_types(self):
        """Test parsing with invalid input types."""
        assert meshtastic_utils.parse_node_id(None) is None
        assert meshtastic_utils.parse_node_id(123) is None
        assert meshtastic_utils.parse_node_id([]) is None
        assert meshtastic_utils.parse_node_id({}) is None
    
    def test_parse_node_id_empty_string(self):
        """Test parsing empty or whitespace strings."""
        assert meshtastic_utils.parse_node_id("") is None
        assert meshtastic_utils.parse_node_id("!") is None
        assert meshtastic_utils.parse_node_id("   ") is None
    
    def test_parse_node_id_wrong_length(self):
        """Test parsing node IDs with wrong length."""
        assert meshtastic_utils.parse_node_id("!123") is None  # Too short
        assert meshtastic_utils.parse_node_id("!123456789") is None  # Too long
        assert meshtastic_utils.parse_node_id("123") is None  # Too short


class TestCalculateDistance:
    """Test suite for calculate_distance function."""
    
    def test_calculate_distance_same_point(self):
        """Test distance calculation for same point."""
        distance = meshtastic_utils.calculate_distance(37.7749, -122.4194, 37.7749, -122.4194)
        assert distance == 0.0
    
    def test_calculate_distance_known_locations(self):
        """Test distance calculation between known locations."""
        # San Francisco to Los Angeles (approximately 559 km)
        sf_lat, sf_lon = 37.7749, -122.4194
        la_lat, la_lon = 34.0522, -118.2437
        
        distance = meshtastic_utils.calculate_distance(sf_lat, sf_lon, la_lat, la_lon)
        
        # Allow for reasonable margin of error in Haversine calculation
        assert 550 < distance < 570
    
    def test_calculate_distance_short_distance(self):
        """Test distance calculation for short distances."""
        # Two points very close together
        lat1, lon1 = 37.7749, -122.4194
        lat2, lon2 = 37.7750, -122.4195  # About 100m apart
        
        distance = meshtastic_utils.calculate_distance(lat1, lon1, lat2, lon2)
        
        # Should be a very small distance (less than 1 km)
        assert 0 < distance < 1
    
    def test_calculate_distance_across_equator(self):
        """Test distance calculation across equator."""
        distance = meshtastic_utils.calculate_distance(10.0, 0.0, -10.0, 0.0)
        
        # Should be approximately 2223 km (20 degrees of latitude)
        assert 2200 < distance < 2250
    
    def test_calculate_distance_across_dateline(self):
        """Test distance calculation across international dateline."""
        distance = meshtastic_utils.calculate_distance(0.0, 179.0, 0.0, -179.0)
        
        # Should be approximately 222 km (2 degrees of longitude at equator)
        assert 200 < distance < 250
    
    def test_calculate_distance_invalid_coordinates(self):
        """Test distance calculation with invalid coordinates."""
        # Invalid latitude (> 90)
        assert meshtastic_utils.calculate_distance(91.0, 0.0, 0.0, 0.0) is None
        assert meshtastic_utils.calculate_distance(-91.0, 0.0, 0.0, 0.0) is None
        
        # Invalid longitude (> 180)
        assert meshtastic_utils.calculate_distance(0.0, 181.0, 0.0, 0.0) is None
        assert meshtastic_utils.calculate_distance(0.0, -181.0, 0.0, 0.0) is None
    
    def test_calculate_distance_invalid_input_types(self):
        """Test distance calculation with invalid input types."""
        assert meshtastic_utils.calculate_distance(None, 0.0, 0.0, 0.0) is None
        assert meshtastic_utils.calculate_distance("invalid", 0.0, 0.0, 0.0) is None
        assert meshtastic_utils.calculate_distance(0.0, 0.0, [], 0.0) is None
        assert meshtastic_utils.calculate_distance(0.0, 0.0, 0.0, {}) is None
    
    def test_calculate_distance_edge_coordinates(self):
        """Test distance calculation at coordinate extremes."""
        # North Pole to South Pole
        distance = meshtastic_utils.calculate_distance(90.0, 0.0, -90.0, 0.0)
        assert 19900 < distance < 20100  # Approximately half Earth's circumference
        
        # Valid edge coordinates
        distance = meshtastic_utils.calculate_distance(-90.0, -180.0, 90.0, 180.0)
        assert distance is not None and distance > 0


class TestFormatSignalStrength:
    """Test suite for format_signal_strength function."""
    
    def test_format_excellent_signal(self):
        """Test formatting excellent signal strength."""
        result = meshtastic_utils.format_signal_strength(12.0, -30)
        assert "Excellent" in result
        assert "SNR: 12.0dB" in result
        assert "RSSI: -30dBm" in result
    
    def test_format_good_signal(self):
        """Test formatting good signal strength."""
        result = meshtastic_utils.format_signal_strength(7.5, -50)
        assert "Good" in result
        assert "SNR: 7.5dB" in result
        assert "RSSI: -50dBm" in result
    
    def test_format_fair_signal(self):
        """Test formatting fair signal strength."""
        result = meshtastic_utils.format_signal_strength(2.0, -70)
        assert "Fair" in result
        assert "SNR: 2.0dB" in result
        assert "RSSI: -70dBm" in result
    
    def test_format_poor_signal(self):
        """Test formatting poor signal strength."""
        result = meshtastic_utils.format_signal_strength(-3.0, -85)
        assert "Poor" in result
        assert "SNR: -3.0dB" in result
        assert "RSSI: -85dBm" in result
    
    def test_format_very_poor_signal(self):
        """Test formatting very poor signal strength."""
        result = meshtastic_utils.format_signal_strength(-10.0, -95)
        assert "Very Poor" in result
        assert "SNR: -10.0dB" in result
        assert "RSSI: -95dBm" in result
    
    def test_format_signal_boundary_values(self):
        """Test formatting at boundary SNR values."""
        # Exactly 10.0 should be Excellent
        result = meshtastic_utils.format_signal_strength(10.0, -40)
        assert "Excellent" in result
        
        # Exactly 5.0 should be Good
        result = meshtastic_utils.format_signal_strength(5.0, -60)
        assert "Good" in result
        
        # Exactly 0.0 should be Fair
        result = meshtastic_utils.format_signal_strength(0.0, -75)
        assert "Fair" in result
        
        # Exactly -5.0 should be Poor
        result = meshtastic_utils.format_signal_strength(-5.0, -90)
        assert "Poor" in result
    
    def test_format_signal_negative_rssi(self):
        """Test formatting with various RSSI values."""
        result = meshtastic_utils.format_signal_strength(5.0, -120)
        assert "RSSI: -120dBm" in result
        
        result = meshtastic_utils.format_signal_strength(5.0, 0)
        assert "RSSI: 0dBm" in result


class TestValidateCoordinates:
    """Test suite for validate_coordinates function."""
    
    def test_validate_valid_coordinates(self):
        """Test validation of valid coordinates."""
        assert meshtastic_utils.validate_coordinates(0.0, 0.0) is True
        assert meshtastic_utils.validate_coordinates(37.7749, -122.4194) is True
        assert meshtastic_utils.validate_coordinates(-37.7749, 122.4194) is True
        assert meshtastic_utils.validate_coordinates(90.0, 180.0) is True
        assert meshtastic_utils.validate_coordinates(-90.0, -180.0) is True
    
    def test_validate_edge_coordinates(self):
        """Test validation of edge case coordinates."""
        # Valid edge cases
        assert meshtastic_utils.validate_coordinates(90.0, 0.0) is True  # North Pole
        assert meshtastic_utils.validate_coordinates(-90.0, 0.0) is True  # South Pole
        assert meshtastic_utils.validate_coordinates(0.0, 180.0) is True  # Date line
        assert meshtastic_utils.validate_coordinates(0.0, -180.0) is True  # Date line
    
    def test_validate_invalid_latitude(self):
        """Test validation of invalid latitudes."""
        assert meshtastic_utils.validate_coordinates(90.1, 0.0) is False
        assert meshtastic_utils.validate_coordinates(-90.1, 0.0) is False
        assert meshtastic_utils.validate_coordinates(180.0, 0.0) is False
        assert meshtastic_utils.validate_coordinates(-180.0, 0.0) is False
    
    def test_validate_invalid_longitude(self):
        """Test validation of invalid longitudes."""
        assert meshtastic_utils.validate_coordinates(0.0, 180.1) is False
        assert meshtastic_utils.validate_coordinates(0.0, -180.1) is False
        assert meshtastic_utils.validate_coordinates(0.0, 360.0) is False
        assert meshtastic_utils.validate_coordinates(0.0, -360.0) is False
    
    def test_validate_invalid_input_types(self):
        """Test validation with invalid input types."""
        assert meshtastic_utils.validate_coordinates(None, 0.0) is False
        assert meshtastic_utils.validate_coordinates(0.0, None) is False
        assert meshtastic_utils.validate_coordinates("37.7", "-122.4") is False
        assert meshtastic_utils.validate_coordinates([], []) is False
        assert meshtastic_utils.validate_coordinates({}, {}) is False
    
    def test_validate_integer_coordinates(self):
        """Test validation with integer coordinates."""
        assert meshtastic_utils.validate_coordinates(45, -120) is True
        assert meshtastic_utils.validate_coordinates(-45, 120) is True
        assert meshtastic_utils.validate_coordinates(91, 0) is False
        assert meshtastic_utils.validate_coordinates(0, 181) is False


class TestFormatTimestamp:
    """Test suite for format_timestamp function."""
    
    def test_format_valid_timestamp(self):
        """Test formatting valid timestamps."""
        # Known timestamp: 2023-11-03 12:00:00 UTC
        timestamp = 1699012800
        result = meshtastic_utils.format_timestamp(timestamp)
        
        assert isinstance(result, str)
        assert "2023" in result
        assert "11" in result or "Nov" in result
        assert "03" in result
    
    def test_format_recent_timestamp(self):
        """Test formatting recent timestamp."""
        recent_timestamp = int(time.time()) - 3600  # 1 hour ago
        result = meshtastic_utils.format_timestamp(recent_timestamp)
        
        assert isinstance(result, str)
        assert result != "Unknown"
        assert result != "Invalid timestamp"
    
    def test_format_zero_timestamp(self):
        """Test formatting zero timestamp."""
        result = meshtastic_utils.format_timestamp(0)
        assert result == "Unknown"
    
    def test_format_negative_timestamp(self):
        """Test formatting negative timestamp."""
        result = meshtastic_utils.format_timestamp(-1)
        assert result == "Unknown"
    
    def test_format_invalid_timestamp_type(self):
        """Test formatting with invalid timestamp types."""
        assert meshtastic_utils.format_timestamp(None) == "Unknown"
        assert meshtastic_utils.format_timestamp("string") == "Unknown"
        assert meshtastic_utils.format_timestamp(3.14) == "Unknown"
        assert meshtastic_utils.format_timestamp([]) == "Unknown"
    
    def test_format_very_large_timestamp(self):
        """Test formatting very large timestamp that might cause overflow."""
        # Year 2100 timestamp
        large_timestamp = 4102444800
        result = meshtastic_utils.format_timestamp(large_timestamp)
        
        # Should handle gracefully, either format correctly or return "Invalid timestamp"
        assert isinstance(result, str)
        assert result in ["Invalid timestamp"] or "2100" in result
    
    def test_format_timestamp_with_mocked_datetime(self):
        """Test timestamp formatting behavior with mocked datetime."""
        valid_timestamp = 1699012800
        
        # Test normal case
        result = meshtastic_utils.format_timestamp(valid_timestamp)
        assert isinstance(result, str)
        assert result != "Invalid timestamp"
        
        # Test exception handling
        with patch('meshtastic_utils.datetime') as mock_datetime:
            mock_datetime.fromtimestamp.side_effect = ValueError("Invalid timestamp")
            result = meshtastic_utils.format_timestamp(valid_timestamp)
            assert result == "Invalid timestamp"


class TestExtractMessageInfo:
    """Test suite for extract_message_info function."""
    
    @pytest.fixture
    def sample_text_message(self):
        """Sample parsed text message."""
        return {
            'from_id': '!aabbccdd',
            'to_id': '!eeffgghh',
            'type': 'text',
            'text': 'Hello, this is a test message with some extra content to test truncation behavior',
            'timestamp': 1699012800,
            'rx_snr': 8.5,
            'rx_rssi': -45
        }
    
    @pytest.fixture
    def sample_position_message(self):
        """Sample parsed position message."""
        return {
            'from_id': '!aabbccdd',
            'to_id': '^all',
            'type': 'position',
            'position': {
                'latitude': 37.7749,
                'longitude': -122.4194,
                'altitude': 50
            },
            'timestamp': 1699012800
        }
    
    @pytest.fixture
    def sample_nodeinfo_message(self):
        """Sample parsed nodeinfo message."""
        return {
            'from_id': '!aabbccdd',
            'to_id': '^all',
            'type': 'nodeinfo',
            'nodeinfo': {
                'long_name': 'Test Node',
                'short_name': 'TEST'
            },
            'timestamp': 1699012800
        }
    
    def test_extract_text_message_info(self, sample_text_message):
        """Test extracting info from text message."""
        info = meshtastic_utils.extract_message_info(sample_text_message)
        
        assert info['from'] == '!aabbccdd'
        assert info['to'] == '!eeffgghh'
        assert info['type'] == 'text'
        assert len(info['text']) <= 100  # Should be truncated
        assert 'Hello, this is a test message' in info['text']
        assert 'signal' in info
        assert 'Excellent' in info['signal'] or 'Good' in info['signal']
    
    def test_extract_position_message_info(self, sample_position_message):
        """Test extracting info from position message."""
        info = meshtastic_utils.extract_message_info(sample_position_message)
        
        assert info['from'] == '!aabbccdd'
        assert info['to'] == '^all'
        assert info['type'] == 'position'
        assert info['location'] == '37.7749, -122.4194'
    
    def test_extract_nodeinfo_message_info(self, sample_nodeinfo_message):
        """Test extracting info from nodeinfo message."""
        info = meshtastic_utils.extract_message_info(sample_nodeinfo_message)
        
        assert info['from'] == '!aabbccdd'
        assert info['to'] == '^all'
        assert info['type'] == 'nodeinfo'
        assert info['node_name'] == 'Test Node'
    
    def test_extract_info_empty_message(self):
        """Test extracting info from empty message."""
        info = meshtastic_utils.extract_message_info({})
        
        assert info['from'] == 'Unknown'
        assert info['to'] == 'Unknown'
        assert info['type'] == 'unknown'
    
    def test_extract_info_none_message(self):
        """Test extracting info from None message."""
        info = meshtastic_utils.extract_message_info(None)
        
        assert info == {}
    
    def test_extract_info_missing_signal_data(self):
        """Test extracting info when signal data is missing."""
        message = {
            'from_id': '!aabbccdd',
            'to_id': '!eeffgghh',
            'type': 'text',
            'text': 'Test message'
        }
        
        info = meshtastic_utils.extract_message_info(message)
        
        assert 'signal' not in info
        assert info['from'] == '!aabbccdd'
        assert info['type'] == 'text'
    
    def test_extract_info_position_missing_coordinates(self):
        """Test extracting info from position message missing coordinates."""
        message = {
            'from_id': '!aabbccdd',
            'type': 'position',
            'position': {}  # Empty position data
        }
        
        info = meshtastic_utils.extract_message_info(message)
        
        assert 'location' not in info
        assert info['type'] == 'position'
    
    def test_extract_info_text_truncation(self):
        """Test text truncation in extracted info."""
        long_text = 'A' * 150  # 150 character string
        message = {
            'from_id': '!aabbccdd',
            'type': 'text',
            'text': long_text
        }
        
        info = meshtastic_utils.extract_message_info(message)
        
        assert len(info['text']) <= 100
        assert info['text'].startswith('A')


class TestIsBroadcastMessage:
    """Test suite for is_broadcast_message function."""
    
    def test_is_broadcast_all_recipients(self):
        """Test detection of ^all broadcast messages."""
        message = {'to_id': '^all'}
        assert meshtastic_utils.is_broadcast_message(message) is True
    
    def test_is_broadcast_hex_all_recipients(self):
        """Test detection of !ffffffff broadcast messages."""
        message = {'to_id': '!ffffffff'}
        assert meshtastic_utils.is_broadcast_message(message) is True
    
    def test_is_not_broadcast_specific_recipient(self):
        """Test detection of non-broadcast messages."""
        message = {'to_id': '!aabbccdd'}
        assert meshtastic_utils.is_broadcast_message(message) is False
    
    def test_is_broadcast_missing_to_id(self):
        """Test broadcast detection when to_id is missing."""
        message = {}
        assert meshtastic_utils.is_broadcast_message(message) is False
    
    def test_is_broadcast_none_to_id(self):
        """Test broadcast detection when to_id is None."""
        message = {'to_id': None}
        assert meshtastic_utils.is_broadcast_message(message) is False
    
    def test_is_broadcast_empty_string_to_id(self):
        """Test broadcast detection when to_id is empty string."""
        message = {'to_id': ''}
        assert meshtastic_utils.is_broadcast_message(message) is False


class TestGetMessageAgeSeconds:
    """Test suite for get_message_age_seconds function."""
    
    def test_get_age_recent_message(self):
        """Test getting age of recent message."""
        current_time = int(time.time())
        message = {'timestamp': current_time - 60}  # 1 minute ago
        
        age = meshtastic_utils.get_message_age_seconds(message)
        
        # Should be approximately 60 seconds, allow some variance for test execution time
        assert 59 <= age <= 65
    
    def test_get_age_old_message(self):
        """Test getting age of old message."""
        current_time = int(time.time())
        message = {'timestamp': current_time - 3600}  # 1 hour ago
        
        age = meshtastic_utils.get_message_age_seconds(message)
        
        # Should be approximately 3600 seconds
        assert 3595 <= age <= 3605
    
    def test_get_age_future_message(self):
        """Test getting age of message from future."""
        current_time = int(time.time())
        message = {'timestamp': current_time + 60}  # 1 minute in future
        
        age = meshtastic_utils.get_message_age_seconds(message)
        
        # Should return 0 for future messages
        assert age == 0
    
    def test_get_age_missing_timestamp(self):
        """Test getting age when timestamp is missing."""
        message = {}
        age = meshtastic_utils.get_message_age_seconds(message)
        assert age == 0
    
    def test_get_age_invalid_timestamp_type(self):
        """Test getting age with invalid timestamp types."""
        message = {'timestamp': 'invalid'}
        age = meshtastic_utils.get_message_age_seconds(message)
        assert age == 0
        
        message = {'timestamp': None}
        age = meshtastic_utils.get_message_age_seconds(message)
        assert age == 0
    
    def test_get_age_zero_timestamp(self):
        """Test getting age with zero timestamp."""
        message = {'timestamp': 0}
        age = meshtastic_utils.get_message_age_seconds(message)
        assert age == 0
    
    def test_get_age_negative_timestamp(self):
        """Test getting age with negative timestamp."""
        message = {'timestamp': -100}
        age = meshtastic_utils.get_message_age_seconds(message)
        assert age == 0


class TestFilterRecentMessages:
    """Test suite for filter_recent_messages function."""
    
    def test_filter_recent_messages_all_recent(self):
        """Test filtering when all messages are recent."""
        current_time = int(time.time())
        messages = [
            {'timestamp': current_time - 60},   # 1 minute ago
            {'timestamp': current_time - 300},  # 5 minutes ago
            {'timestamp': current_time - 1800}, # 30 minutes ago
        ]
        
        filtered = meshtastic_utils.filter_recent_messages(messages, max_age_seconds=3600)
        
        assert len(filtered) == 3
        assert filtered == messages
    
    def test_filter_recent_messages_some_old(self):
        """Test filtering when some messages are old."""
        current_time = int(time.time())
        messages = [
            {'timestamp': current_time - 60},    # 1 minute ago (recent)
            {'timestamp': current_time - 3700},  # > 1 hour ago (old)
            {'timestamp': current_time - 1800},  # 30 minutes ago (recent)
            {'timestamp': current_time - 7200},  # 2 hours ago (old)
        ]
        
        filtered = meshtastic_utils.filter_recent_messages(messages, max_age_seconds=3600)
        
        assert len(filtered) == 2
        assert filtered[0]['timestamp'] == current_time - 60
        assert filtered[1]['timestamp'] == current_time - 1800
    
    def test_filter_recent_messages_all_old(self):
        """Test filtering when all messages are old."""
        current_time = int(time.time())
        messages = [
            {'timestamp': current_time - 7200},  # 2 hours ago
            {'timestamp': current_time - 10800}, # 3 hours ago
        ]
        
        filtered = meshtastic_utils.filter_recent_messages(messages, max_age_seconds=3600)
        
        assert len(filtered) == 0
    
    def test_filter_recent_messages_empty_list(self):
        """Test filtering empty message list."""
        filtered = meshtastic_utils.filter_recent_messages([])
        assert filtered == []
    
    def test_filter_recent_messages_invalid_input(self):
        """Test filtering with invalid input."""
        assert meshtastic_utils.filter_recent_messages(None) == []
        assert meshtastic_utils.filter_recent_messages("invalid") == []
        assert meshtastic_utils.filter_recent_messages(123) == []
    
    def test_filter_recent_messages_default_max_age(self):
        """Test filtering with default max age (1 hour)."""
        current_time = int(time.time())
        messages = [
            {'timestamp': current_time - 1800},  # 30 minutes ago (should be included)
            {'timestamp': current_time - 7200},  # 2 hours ago (should be excluded)
        ]
        
        filtered = meshtastic_utils.filter_recent_messages(messages)
        
        assert len(filtered) == 1
        assert filtered[0]['timestamp'] == current_time - 1800
    
    def test_filter_recent_messages_missing_timestamps(self):
        """Test filtering messages with missing timestamps."""
        current_time = int(time.time())
        messages = [
            {'timestamp': current_time - 60},  # Recent message
            {},  # Missing timestamp
            {'timestamp': None},  # None timestamp
            {'text': 'message without timestamp'},  # Message without timestamp field
        ]
        
        filtered = meshtastic_utils.filter_recent_messages(messages, max_age_seconds=3600)
        
        # Only the recent message should be included
        assert len(filtered) == 1
        assert filtered[0]['timestamp'] == current_time - 60


class TestDeduplicateMessages:
    """Test suite for deduplicate_messages function."""
    
    def test_deduplicate_unique_messages(self):
        """Test deduplication with all unique messages."""
        messages = [
            {'id': 'msg1', 'text': 'First message'},
            {'id': 'msg2', 'text': 'Second message'},
            {'id': 'msg3', 'text': 'Third message'},
        ]
        
        deduplicated = meshtastic_utils.deduplicate_messages(messages)
        
        assert len(deduplicated) == 3
        assert deduplicated == messages
    
    def test_deduplicate_duplicate_messages(self):
        """Test deduplication with duplicate messages."""
        messages = [
            {'id': 'msg1', 'text': 'First message'},
            {'id': 'msg2', 'text': 'Second message'},
            {'id': 'msg1', 'text': 'Duplicate first message'},  # Duplicate ID
            {'id': 'msg3', 'text': 'Third message'},
            {'id': 'msg2', 'text': 'Another duplicate'},  # Another duplicate
        ]
        
        deduplicated = meshtastic_utils.deduplicate_messages(messages)
        
        assert len(deduplicated) == 3
        # Should keep the first occurrence of each ID
        assert deduplicated[0]['text'] == 'First message'
        assert deduplicated[1]['text'] == 'Second message'
        assert deduplicated[2]['text'] == 'Third message'
    
    def test_deduplicate_messages_without_ids(self):
        """Test deduplication with messages without IDs."""
        messages = [
            {'text': 'Message without ID'},
            {'id': 'msg1', 'text': 'Message with ID'},
            {'text': 'Another message without ID'},
            {'id': None, 'text': 'Message with None ID'},
        ]
        
        deduplicated = meshtastic_utils.deduplicate_messages(messages)
        
        # All messages should be kept since they don't have valid IDs for deduplication
        assert len(deduplicated) == 4
    
    def test_deduplicate_empty_list(self):
        """Test deduplication with empty list."""
        deduplicated = meshtastic_utils.deduplicate_messages([])
        assert deduplicated == []
    
    def test_deduplicate_invalid_input(self):
        """Test deduplication with invalid input."""
        assert meshtastic_utils.deduplicate_messages(None) == []
        assert meshtastic_utils.deduplicate_messages("invalid") == []
        assert meshtastic_utils.deduplicate_messages(123) == []
    
    def test_deduplicate_mixed_id_types(self):
        """Test deduplication with mixed ID types."""
        messages = [
            {'id': 'string_id', 'text': 'String ID'},
            {'id': 123, 'text': 'Integer ID'},
            {'id': 'string_id', 'text': 'Duplicate string ID'},
            {'id': 123, 'text': 'Duplicate integer ID'},
        ]
        
        deduplicated = meshtastic_utils.deduplicate_messages(messages)
        
        # Should deduplicate based on ID regardless of type
        assert len(deduplicated) == 2
        assert deduplicated[0]['text'] == 'String ID'
        assert deduplicated[1]['text'] == 'Integer ID'
    
    def test_deduplicate_preserve_order(self):
        """Test that deduplication preserves order of first occurrences."""
        messages = [
            {'id': 'c', 'text': 'Third'},
            {'id': 'a', 'text': 'First'},
            {'id': 'b', 'text': 'Second'},
            {'id': 'a', 'text': 'Duplicate First'},
            {'id': 'c', 'text': 'Duplicate Third'},
        ]
        
        deduplicated = meshtastic_utils.deduplicate_messages(messages)
        
        assert len(deduplicated) == 3
        # Should preserve the order of first occurrences
        assert deduplicated[0]['text'] == 'Third'
        assert deduplicated[1]['text'] == 'First'
        assert deduplicated[2]['text'] == 'Second'


class TestIntegrationScenarios:
    """Integration tests combining multiple utility functions."""
    
    def test_complete_message_processing_pipeline(self):
        """Test complete message processing from raw to filtered."""
        # Raw Meshtastic messages
        current_time = int(time.time())
        raw_messages = [
            {
                'id': 'msg1',
                'timestamp': current_time - 60,
                'from': '!aabbccdd',
                'to': '^all',
                'decoded': {
                    'portnum': 'TEXT_MESSAGE_APP',
                    'text': 'Hello everyone!'
                }
            },
            {
                'id': 'msg2',
                'timestamp': current_time - 3700,  # Too old
                'from': '!eeffgghh',
                'to': '!aabbccdd',
                'decoded': {
                    'portnum': 'TEXT_MESSAGE_APP',
                    'text': 'Old message'
                }
            },
            {
                'id': 'msg1',  # Duplicate
                'timestamp': current_time - 120,
                'from': '!aabbccdd',
                'to': '^all',
                'decoded': {
                    'portnum': 'TEXT_MESSAGE_APP',
                    'text': 'Duplicate message'
                }
            }
        ]
        
        # Parse messages
        parsed_messages = []
        for raw_msg in raw_messages:
            parsed = meshtastic_utils.parse_meshtastic_message(raw_msg)
            if parsed:
                parsed_messages.append(parsed)
        
        # Filter recent messages (1 hour)
        recent_messages = meshtastic_utils.filter_recent_messages(
            parsed_messages, max_age_seconds=3600
        )
        
        # Deduplicate messages
        unique_messages = meshtastic_utils.deduplicate_messages(recent_messages)
        
        # Should have only 1 message (recent, unique)
        assert len(unique_messages) == 1
        assert unique_messages[0]['text'] == 'Hello everyone!'
        assert meshtastic_utils.is_broadcast_message(unique_messages[0]) is True
    
    def test_node_id_formatting_roundtrip(self):
        """Test node ID formatting and parsing roundtrip."""
        original_ids = [0x12345678, 0xaabbccdd, 0xffffffff, 0x00000000, 0x11111111]
        
        for original_id in original_ids:
            # Format to string
            formatted = meshtastic_utils.format_node_id(original_id)
            
            # Parse back to int
            parsed = meshtastic_utils.parse_node_id(formatted)
            
            # Should match original
            assert parsed == original_id
    
    def test_distance_calculation_with_validation(self):
        """Test distance calculation with coordinate validation."""
        # Valid coordinates
        valid_coords = [
            (37.7749, -122.4194),  # San Francisco
            (34.0522, -118.2437),  # Los Angeles
        ]
        
        for lat, lon in valid_coords:
            assert meshtastic_utils.validate_coordinates(lat, lon) is True
        
        # Calculate distance
        distance = meshtastic_utils.calculate_distance(
            valid_coords[0][0], valid_coords[0][1],
            valid_coords[1][0], valid_coords[1][1]
        )
        
        assert distance is not None
        assert 550 < distance < 570  # Known distance between SF and LA
        
        # Invalid coordinates should fail validation and distance calculation
        invalid_lat, invalid_lon = 91.0, 181.0
        assert meshtastic_utils.validate_coordinates(invalid_lat, invalid_lon) is False
        
        invalid_distance = meshtastic_utils.calculate_distance(
            invalid_lat, invalid_lon, 0.0, 0.0
        )
        assert invalid_distance is None


class TestPerformanceAndStress:
    """Performance and stress tests for utility functions."""
    
    def test_parse_large_batch_of_messages(self):
        """Test parsing performance with large batch of messages."""
        # Generate 1000 test messages
        messages = []
        for i in range(1000):
            message = {
                'id': f'msg_{i}',
                'timestamp': int(time.time()) - i,
                'from': f'!node{i:04x}',
                'to': '^all',
                'decoded': {
                    'portnum': 'TEXT_MESSAGE_APP',
                    'text': f'Test message number {i}'
                }
            }
            messages.append(message)
        
        # Parse all messages and measure time
        start_time = time.time()
        parsed_count = 0
        
        for message in messages:
            result = meshtastic_utils.parse_meshtastic_message(message)
            if result:
                parsed_count += 1
        
        end_time = time.time()
        processing_time = end_time - start_time
        
        # Should process quickly (less than 1 second)
        assert processing_time < 1.0
        assert parsed_count == 1000
    
    def test_distance_calculation_performance(self):
        """Test distance calculation performance."""
        # Generate coordinate pairs
        coord_pairs = []
        for i in range(1000):
            lat1 = (i % 180) - 90
            lon1 = (i % 360) - 180
            lat2 = ((i + 1) % 180) - 90
            lon2 = ((i + 1) % 360) - 180
            coord_pairs.append((lat1, lon1, lat2, lon2))
        
        # Calculate distances
        start_time = time.time()
        valid_distances = 0
        
        for lat1, lon1, lat2, lon2 in coord_pairs:
            distance = meshtastic_utils.calculate_distance(lat1, lon1, lat2, lon2)
            if distance is not None:
                valid_distances += 1
        
        end_time = time.time()
        processing_time = end_time - start_time
        
        # Should be fast (less than 1 second for 1000 calculations)
        assert processing_time < 1.0
        assert valid_distances > 900  # Most should be valid
    
    def test_deduplication_performance(self):
        """Test deduplication performance with large dataset."""
        # Generate messages with some duplicates
        messages = []
        for i in range(10000):
            # Create some duplicates by repeating every 100th ID
            msg_id = f'msg_{i // 10}'
            message = {
                'id': msg_id,
                'text': f'Message {i}',
                'timestamp': int(time.time()) - i
            }
            messages.append(message)
        
        # Deduplicate
        start_time = time.time()
        unique_messages = meshtastic_utils.deduplicate_messages(messages)
        end_time = time.time()
        
        processing_time = end_time - start_time
        
        # Should be fast and effective
        assert processing_time < 1.0
        assert len(unique_messages) == 1000  # Should have 1000 unique IDs
        assert len(unique_messages) < len(messages)  # Should have removed duplicates


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])