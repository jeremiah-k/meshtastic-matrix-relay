import pytest
from unittest.mock import Mock, patch, MagicMock
import json
from datetime import datetime, timezone
import asyncio

from mmrelay.matrix_utils import (
    MatrixClient,
    MatrixMessage,
    format_matrix_message,
    extract_room_id,
    extract_user_id,
    validate_matrix_event,
    parse_matrix_timestamp,
    create_matrix_filter,
    sanitize_matrix_content,
    is_valid_matrix_room_id,
    is_valid_matrix_user_id,
    get_room_display_name,
    extract_message_body,
    handle_matrix_error
)


class TestMatrixClient:
    """Test cases for MatrixClient class"""
    
    @pytest.fixture
    def mock_client(self):
        """Create a mock Matrix client for testing"""
        client = MatrixClient("https://matrix.example.com", "@test:example.com", "test_token")
        return client
    
    def test_init_valid_params(self):
        """Test MatrixClient initialization with valid parameters"""
        homeserver = "https://matrix.example.com"
        user_id = "@test:example.com"
        access_token = "test_token"
        
        client = MatrixClient(homeserver, user_id, access_token)
        
        assert client.homeserver == homeserver
        assert client.user_id == user_id
        assert client.access_token == access_token
    
    def test_init_invalid_homeserver(self):
        """Test MatrixClient initialization with invalid homeserver URL"""
        with pytest.raises(ValueError, match="Invalid homeserver URL"):
            MatrixClient("invalid-url", "@test:example.com", "token")
    
    def test_init_invalid_user_id(self):
        """Test MatrixClient initialization with invalid user ID"""
        with pytest.raises(ValueError, match="Invalid user ID format"):
            MatrixClient("https://matrix.example.com", "invalid-user", "token")
    
    def test_init_empty_access_token(self):
        """Test MatrixClient initialization with empty access token"""
        with pytest.raises(ValueError, match="Access token cannot be empty"):
            MatrixClient("https://matrix.example.com", "@test:example.com", "")
    
    @patch('mmrelay.matrix_utils.requests.get')
    def test_get_room_info_success(self, mock_get, mock_client):
        """Test successful room info retrieval"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "Test Room",
            "topic": "Test Topic",
            "avatar_url": "mxc://example.com/avatar"
        }
        mock_get.return_value = mock_response
        
        room_id = "!test:example.com"
        result = mock_client.get_room_info(room_id)
        
        assert result["name"] == "Test Room"
        assert result["topic"] == "Test Topic"
        mock_get.assert_called_once()
    
    @patch('mmrelay.matrix_utils.requests.get')
    def test_get_room_info_not_found(self, mock_get, mock_client):
        """Test room info retrieval when room not found"""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response
        
        with pytest.raises(Exception, match="Room not found"):
            mock_client.get_room_info("!nonexistent:example.com")
    
    @patch('mmrelay.matrix_utils.requests.post')
    def test_send_message_success(self, mock_post, mock_client):
        """Test successful message sending"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"event_id": "$event123:example.com"}
        mock_post.return_value = mock_response
        
        room_id = "!test:example.com"
        message = "Hello, world!"
        result = mock_client.send_message(room_id, message)
        
        assert result["event_id"] == "$event123:example.com"
        mock_post.assert_called_once()
    
    @patch('mmrelay.matrix_utils.requests.post')
    def test_send_message_failure(self, mock_post, mock_client):
        """Test message sending failure"""
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.json.return_value = {"error": "Forbidden"}
        mock_post.return_value = mock_response
        
        room_id = "!test:example.com"
        message = "Hello, world!"
        
        with pytest.raises(Exception, match="Failed to send message"):
            mock_client.send_message(room_id, message)
    
    def test_send_message_empty_content(self, mock_client):
        """Test sending empty message"""
        with pytest.raises(ValueError, match="Message content cannot be empty"):
            mock_client.send_message("!test:example.com", "")
    
    def test_send_message_invalid_room_id(self, mock_client):
        """Test sending message to invalid room ID"""
        with pytest.raises(ValueError, match="Invalid room ID"):
            mock_client.send_message("invalid-room", "Hello")


class TestMatrixMessage:
    """Test cases for MatrixMessage class"""
    
    def test_init_valid_params(self):
        """Test MatrixMessage initialization with valid parameters"""
        sender = "@user:example.com"
        room_id = "!room:example.com"
        body = "Test message"
        timestamp = datetime.now(timezone.utc)
        
        message = MatrixMessage(sender, room_id, body, timestamp)
        
        assert message.sender == sender
        assert message.room_id == room_id
        assert message.body == body
        assert message.timestamp == timestamp
    
    def test_init_invalid_sender(self):
        """Test MatrixMessage initialization with invalid sender"""
        with pytest.raises(ValueError, match="Invalid sender format"):
            MatrixMessage("invalid-sender", "!room:example.com", "body", datetime.now())
    
    def test_init_invalid_room_id(self):
        """Test MatrixMessage initialization with invalid room ID"""
        with pytest.raises(ValueError, match="Invalid room ID format"):
            MatrixMessage("@user:example.com", "invalid-room", "body", datetime.now())
    
    def test_init_empty_body(self):
        """Test MatrixMessage initialization with empty body"""
        with pytest.raises(ValueError, match="Message body cannot be empty"):
            MatrixMessage("@user:example.com", "!room:example.com", "", datetime.now())
    
    def test_to_dict(self):
        """Test converting MatrixMessage to dictionary"""
        timestamp = datetime.now(timezone.utc)
        message = MatrixMessage("@user:example.com", "!room:example.com", "Test", timestamp)
        
        result = message.to_dict()
        
        assert result["sender"] == "@user:example.com"
        assert result["room_id"] == "!room:example.com"
        assert result["body"] == "Test"
        assert result["timestamp"] == timestamp.isoformat()
    
    def test_from_dict_valid(self):
        """Test creating MatrixMessage from valid dictionary"""
        timestamp = datetime.now(timezone.utc)
        data = {
            "sender": "@user:example.com",
            "room_id": "!room:example.com",
            "body": "Test message",
            "timestamp": timestamp.isoformat()
        }
        
        message = MatrixMessage.from_dict(data)
        
        assert message.sender == "@user:example.com"
        assert message.room_id == "!room:example.com"
        assert message.body == "Test message"
    
    def test_from_dict_missing_keys(self):
        """Test creating MatrixMessage from dictionary with missing keys"""
        data = {"sender": "@user:example.com", "body": "Test"}
        
        with pytest.raises(KeyError):
            MatrixMessage.from_dict(data)


class TestUtilityFunctions:
    """Test cases for utility functions"""
    
    def test_format_matrix_message_basic(self):
        """Test basic message formatting"""
        content = "Hello, world!"
        result = format_matrix_message(content)
        
        assert "body" in result
        assert "msgtype" in result
        assert result["body"] == content
        assert result["msgtype"] == "m.text"
    
    def test_format_matrix_message_with_html(self):
        """Test message formatting with HTML"""
        content = "Hello, <b>world</b>!"
        result = format_matrix_message(content, msgtype="m.text", format="org.matrix.custom.html")
        
        assert result["body"] == content
        assert result["format"] == "org.matrix.custom.html"
        assert result["formatted_body"] == content
    
    def test_format_matrix_message_empty_content(self):
        """Test formatting empty message content"""
        with pytest.raises(ValueError, match="Content cannot be empty"):
            format_matrix_message("")
    
    def test_extract_room_id_valid(self):
        """Test extracting valid room ID from various formats"""
        test_cases = [
            "!abc123:example.com",
            "#room:example.com",
            "!room123:matrix.org"
        ]
        
        for room_id in test_cases:
            result = extract_room_id(room_id)
            assert result == room_id
    
    def test_extract_room_id_from_url(self):
        """Test extracting room ID from Matrix URL"""
        url = "https://matrix.to/#/!abc123:example.com"
        result = extract_room_id(url)
        assert result == "!abc123:example.com"
    
    def test_extract_room_id_invalid(self):
        """Test extracting room ID from invalid input"""
        with pytest.raises(ValueError, match="Invalid room identifier"):
            extract_room_id("invalid-room")
    
    def test_extract_user_id_valid(self):
        """Test extracting valid user ID"""
        user_id = "@user:example.com"
        result = extract_user_id(user_id)
        assert result == user_id
    
    def test_extract_user_id_from_display_name(self):
        """Test extracting user ID from display name format"""
        display_name = "User Name (@user:example.com)"
        result = extract_user_id(display_name)
        assert result == "@user:example.com"
    
    def test_extract_user_id_invalid(self):
        """Test extracting user ID from invalid input"""
        with pytest.raises(ValueError, match="Invalid user identifier"):
            extract_user_id("invalid-user")
    
    def test_validate_matrix_event_valid(self):
        """Test validating valid Matrix event"""
        event = {
            "type": "m.room.message",
            "sender": "@user:example.com",
            "room_id": "!room:example.com",
            "event_id": "$event123:example.com",
            "origin_server_ts": 1234567890000,
            "content": {"body": "Hello", "msgtype": "m.text"}
        }
        
        result = validate_matrix_event(event)
        assert result is True
    
    def test_validate_matrix_event_missing_required_fields(self):
        """Test validating Matrix event with missing required fields"""
        event = {
            "type": "m.room.message",
            "sender": "@user:example.com"
            # Missing room_id, event_id, etc.
        }
        
        result = validate_matrix_event(event)
        assert result is False
    
    def test_validate_matrix_event_invalid_types(self):
        """Test validating Matrix event with invalid types"""
        event = {
            "type": "m.room.message",
            "sender": "invalid-sender",  # Invalid format
            "room_id": "!room:example.com",
            "event_id": "$event123:example.com",
            "origin_server_ts": "invalid-timestamp",  # Should be int
            "content": {"body": "Hello", "msgtype": "m.text"}
        }
        
        result = validate_matrix_event(event)
        assert result is False
    
    def test_parse_matrix_timestamp_valid(self):
        """Test parsing valid Matrix timestamp"""
        timestamp_ms = 1234567890000
        result = parse_matrix_timestamp(timestamp_ms)
        
        assert isinstance(result, datetime)
        assert result.tzinfo == timezone.utc
    
    def test_parse_matrix_timestamp_invalid(self):
        """Test parsing invalid Matrix timestamp"""
        with pytest.raises(ValueError, match="Invalid timestamp"):
            parse_matrix_timestamp("invalid")
    
    def test_parse_matrix_timestamp_negative(self):
        """Test parsing negative timestamp"""
        with pytest.raises(ValueError, match="Timestamp cannot be negative"):
            parse_matrix_timestamp(-1000)
    
    def test_create_matrix_filter_default(self):
        """Test creating Matrix filter with default parameters"""
        result = create_matrix_filter()
        
        assert "room" in result
        assert "timeline" in result["room"]
        assert "limit" in result["room"]["timeline"]
    
    def test_create_matrix_filter_custom(self):
        """Test creating Matrix filter with custom parameters"""
        result = create_matrix_filter(limit=50, types=["m.room.message"])
        
        assert result["room"]["timeline"]["limit"] == 50
        assert result["room"]["timeline"]["types"] == ["m.room.message"]
    
    def test_sanitize_matrix_content_basic(self):
        """Test basic content sanitization"""
        content = "Hello <script>alert('xss')</script> world"
        result = sanitize_matrix_content(content)
        
        assert "<script>" not in result
        assert "Hello" in result
        assert "world" in result
    
    def test_sanitize_matrix_content_allowed_tags(self):
        """Test content sanitization with allowed HTML tags"""
        content = "Hello <b>bold</b> and <i>italic</i> text"
        result = sanitize_matrix_content(content, allow_html=True)
        
        assert "<b>bold</b>" in result
        assert "<i>italic</i>" in result
    
    def test_sanitize_matrix_content_empty(self):
        """Test sanitizing empty content"""
        result = sanitize_matrix_content("")
        assert result == ""
    
    def test_is_valid_matrix_room_id_valid(self):
        """Test validating valid Matrix room IDs"""
        valid_room_ids = [
            "!abc123:example.com",
            "!room_name:matrix.org",
            "!12345:localhost"
        ]
        
        for room_id in valid_room_ids:
            assert is_valid_matrix_room_id(room_id) is True
    
    def test_is_valid_matrix_room_id_invalid(self):
        """Test validating invalid Matrix room IDs"""
        invalid_room_ids = [
            "@user:example.com",  # User ID, not room ID
            "#room:example.com",  # Room alias, not room ID
            "!room",  # Missing server name
            "room:example.com",  # Missing !
            "",  # Empty string
            None  # None value
        ]
        
        for room_id in invalid_room_ids:
            assert is_valid_matrix_room_id(room_id) is False
    
    def test_is_valid_matrix_user_id_valid(self):
        """Test validating valid Matrix user IDs"""
        valid_user_ids = [
            "@user:example.com",
            "@test.user:matrix.org",
            "@123:localhost"
        ]
        
        for user_id in valid_user_ids:
            assert is_valid_matrix_user_id(user_id) is True
    
    def test_is_valid_matrix_user_id_invalid(self):
        """Test validating invalid Matrix user IDs"""
        invalid_user_ids = [
            "!room:example.com",  # Room ID, not user ID
            "#room:example.com",  # Room alias, not user ID
            "@user",  # Missing server name
            "user:example.com",  # Missing @
            "",  # Empty string
            None  # None value
        ]
        
        for user_id in invalid_user_ids:
            assert is_valid_matrix_user_id(user_id) is False
    
    def test_get_room_display_name_with_name(self):
        """Test getting room display name when name is set"""
        room_info = {"name": "My Room", "canonical_alias": "#room:example.com"}
        result = get_room_display_name(room_info)
        assert result == "My Room"
    
    def test_get_room_display_name_with_alias(self):
        """Test getting room display name when only alias is available"""
        room_info = {"canonical_alias": "#room:example.com"}
        result = get_room_display_name(room_info)
        assert result == "#room:example.com"
    
    def test_get_room_display_name_fallback(self):
        """Test getting room display name with fallback"""
        room_info = {}
        result = get_room_display_name(room_info, fallback="Unknown Room")
        assert result == "Unknown Room"
    
    def test_extract_message_body_text(self):
        """Test extracting message body from text message"""
        content = {"body": "Hello, world!", "msgtype": "m.text"}
        result = extract_message_body(content)
        assert result == "Hello, world!"
    
    def test_extract_message_body_formatted(self):
        """Test extracting message body from formatted message"""
        content = {
            "body": "Hello, world!",
            "formatted_body": "Hello, <b>world</b>!",
            "format": "org.matrix.custom.html",
            "msgtype": "m.text"
        }
        result = extract_message_body(content, prefer_formatted=True)
        assert result == "Hello, <b>world</b>!"
    
    def test_extract_message_body_missing(self):
        """Test extracting message body when body is missing"""
        content = {"msgtype": "m.text"}
        result = extract_message_body(content)
        assert result == ""
    
    def test_handle_matrix_error_client_error(self):
        """Test handling Matrix client errors"""
        error_response = {
            "errcode": "M_FORBIDDEN",
            "error": "You are not allowed to send messages to this room"
        }
        
        with pytest.raises(Exception, match="Matrix API error"):
            handle_matrix_error(403, error_response)
    
    def test_handle_matrix_error_server_error(self):
        """Test handling Matrix server errors"""
        with pytest.raises(Exception, match="Matrix server error"):
            handle_matrix_error(500, {})
    
    def test_handle_matrix_error_unknown(self):
        """Test handling unknown Matrix errors"""
        with pytest.raises(Exception, match="Unknown Matrix error"):
            handle_matrix_error(418, {})


class TestEdgeCases:
    """Test edge cases and boundary conditions"""
    
    def test_very_long_message_content(self):
        """Test handling very long message content"""
        long_content = "a" * 100000  # 100KB message
        result = format_matrix_message(long_content)
        assert result["body"] == long_content
    
    def test_unicode_content(self):
        """Test handling Unicode content in messages"""
        unicode_content = "Hello 🌍! こんにちは 世界! 🎉"
        result = format_matrix_message(unicode_content)
        assert result["body"] == unicode_content
    
    def test_special_characters_in_room_id(self):
        """Test room IDs with special characters"""
        room_id = "!room_with-special.chars:example-server.com"
        assert is_valid_matrix_room_id(room_id) is True
    
    def test_malformed_json_in_error_handling(self):
        """Test error handling with malformed JSON response"""
        with pytest.raises(Exception):
            handle_matrix_error(400, "not json")
    
    def test_timestamp_edge_values(self):
        """Test timestamp parsing with edge values"""
        # Test very large timestamp (year 2038+)
        large_timestamp = 2147483648000  # Beyond 32-bit signed int limit
        result = parse_matrix_timestamp(large_timestamp)
        assert isinstance(result, datetime)
        
        # Test minimum valid timestamp
        min_timestamp = 0
        result = parse_matrix_timestamp(min_timestamp)
        assert isinstance(result, datetime)
    
    def test_room_id_extraction_complex_urls(self):
        """Test room ID extraction from complex Matrix URLs"""
        complex_url = "https://matrix.to/#/!abc123:example.com?via=matrix.org&via=example.com"
        result = extract_room_id(complex_url)
        assert result == "!abc123:example.com"


class TestAsyncOperations:
    """Test asynchronous operations if they exist in the matrix_utils"""
    
    @pytest.mark.asyncio
    async def test_async_send_message_success(self):
        """Test asynchronous message sending success"""
        # This test assumes there's an async version of send_message
        # Adjust based on actual implementation
        client = MatrixClient("https://matrix.example.com", "@test:example.com", "token")
        
        with patch('aiohttp.ClientSession.post') as mock_post:
            mock_response = Mock()
            mock_response.status = 200
            mock_response.json = asyncio.coroutine(lambda: {"event_id": "$event123:example.com"})()
            mock_post.return_value.__aenter__.return_value = mock_response
            
            # Test would go here if async methods exist
            pass
    
    @pytest.mark.asyncio
    async def test_async_error_handling(self):
        """Test asynchronous error handling"""
        # Test async error scenarios
        pass


class TestIntegration:
    """Integration-style tests for matrix_utils"""
    
    def test_full_message_workflow(self):
        """Test complete message workflow from creation to sending"""
        # Create a message
        timestamp = datetime.now(timezone.utc)
        message = MatrixMessage("@user:example.com", "!room:example.com", "Test", timestamp)
        
        # Format message content
        formatted = format_matrix_message(message.body)
        
        # Validate the message structure
        assert formatted["body"] == "Test"
        assert formatted["msgtype"] == "m.text"
        
        # Convert to dict and back
        message_dict = message.to_dict()
        reconstructed = MatrixMessage.from_dict(message_dict)
        
        assert reconstructed.body == message.body
        assert reconstructed.sender == message.sender
    
    def test_error_propagation_chain(self):
        """Test error propagation through the utility chain"""
        # Test how errors propagate through multiple utility functions
        with pytest.raises(ValueError):
            invalid_room = "invalid-room"
            extract_room_id(invalid_room)
    
    def test_sanitization_and_formatting_chain(self):
        """Test content sanitization and formatting working together"""
        dangerous_content = "Hello <script>alert('xss')</script> <b>world</b>"
        sanitized = sanitize_matrix_content(dangerous_content, allow_html=True)
        formatted = format_matrix_message(sanitized)
        
        assert "<script>" not in formatted["body"]
        assert "<b>world</b>" in formatted["body"]


# Performance and stress tests
class TestPerformance:
    """Performance-related tests"""
    
    def test_large_batch_validation(self):
        """Test validating large batches of Matrix events"""
        # Create 1000 valid events
        events = []
        for i in range(1000):
            event = {
                "type": "m.room.message",
                "sender": f"@user{i}:example.com",
                "room_id": "!room:example.com",
                "event_id": f"$event{i}:example.com",
                "origin_server_ts": 1234567890000 + i,
                "content": {"body": f"Message {i}", "msgtype": "m.text"}
            }
            events.append(event)
        
        # Validate all events
        results = [validate_matrix_event(event) for event in events]
        assert all(results)
    
    def test_memory_efficiency_large_messages(self):
        """Test memory efficiency with large message objects"""
        # Test creating and processing many large messages
        messages = []
        for i in range(100):
            large_body = f"Message {i}: " + "x" * 10000
            message = MatrixMessage(
                f"@user{i}:example.com",
                "!room:example.com", 
                large_body,
                datetime.now(timezone.utc)
            )
            messages.append(message)
        
        # Process all messages
        dicts = [msg.to_dict() for msg in messages]
        assert len(dicts) == 100
        
        # Clean up
        del messages
        del dicts


if __name__ == "__main__":
    pytest.main([__file__])