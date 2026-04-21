#!/usr/bin/env python3
"""
Test suite for map plugin functionality.

Tests the map generation plugin including:
- Map generation with various parameters
- Location anonymization
- Image upload and sending
- Command parsing and validation
- Configuration handling
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import s2sphere

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.constants.domain import MATRIX_EVENT_TYPE_ROOM_MESSAGE
from mmrelay.matrix_utils import (
    ImageUploadError,
    send_image,
    send_room_image,
    upload_image,
)
from mmrelay.plugins.map_plugin import (
    DEFAULT_MAP_ZOOM,
    Plugin,
    TextLabel,
    get_map,
    precision_bits_to_meters,
)
from tests.constants import TEST_LAT_SF, TEST_LON_SF


class TestTextLabel(unittest.TestCase):
    """Test cases for TextLabel class."""

    def setUp(self):
        """
        Initialize a TextLabel instance with a San Francisco coordinate and label for use in tests.
        """
        self.latlng = s2sphere.LatLng.from_degrees(TEST_LAT_SF, TEST_LON_SF)
        self.text_label = TextLabel(self.latlng, "Test Label", font_size=12)

    def test_init(self):
        """Test TextLabel initialization."""
        self.assertEqual(self.text_label._latlng, self.latlng)
        self.assertEqual(self.text_label._text, "Test Label")
        self.assertEqual(self.text_label._font_size, 12)
        self.assertEqual(self.text_label._margin, 4)
        self.assertEqual(self.text_label._arrow, 16)

    def test_latlng(self):
        """
        Verify that the latlng property of the TextLabel instance returns the correct coordinate.
        """
        self.assertEqual(self.text_label.latlng(), self.latlng)

    def test_bounds(self):
        """
        Test that the bounds() method returns an s2sphere.LatLngRect instance for the TextLabel.
        """
        bounds = self.text_label.bounds()
        self.assertIsInstance(bounds, s2sphere.LatLngRect)

    def test_extra_pixel_bounds(self):
        """
        Test that extra_pixel_bounds returns a 4-tuple of positive values representing the label's pixel bounds.
        """
        bounds = self.text_label.extra_pixel_bounds()
        self.assertIsInstance(bounds, tuple)
        self.assertEqual(len(bounds), 4)
        # Check that bounds are reasonable
        self.assertGreater(bounds[0], 0)  # left
        self.assertGreater(bounds[1], 0)  # top
        self.assertGreater(bounds[2], 0)  # right

    @patch("staticmaps.PillowRenderer")
    def test_render_pillow(self, mock_renderer_class):
        """
        Tests that the TextLabel's Pillow rendering method calls the expected drawing operations for polygon, line, and text.
        """
        mock_renderer = MagicMock()
        mock_transformer = MagicMock()
        mock_transformer.ll2pixel.return_value = (100, 100)
        mock_renderer.transformer.return_value = mock_transformer
        mock_renderer.offset_x.return_value = 0

        mock_draw = MagicMock()
        mock_draw.textbbox.return_value = (0, 0, 50, 12)
        mock_renderer.draw.return_value = mock_draw

        self.text_label.render_pillow(mock_renderer)

        # Verify drawing methods were called
        mock_draw.polygon.assert_called_once()
        mock_draw.line.assert_called_once()
        mock_draw.text.assert_called_once()

    @patch("mmrelay.plugins.map_plugin.ImageFont.load_default")
    @patch("mmrelay.plugins.map_plugin.ImageFont.truetype")
    def test_render_pillow_falls_back_to_default_font(
        self, mock_truetype, mock_load_default
    ):
        """TextLabel should fall back to ImageFont.load_default when truetype fails."""
        mock_truetype.side_effect = OSError("missing font")
        fallback_font = MagicMock()
        mock_load_default.return_value = fallback_font

        mock_renderer = MagicMock()
        mock_renderer.transformer.return_value.ll2pixel.return_value = (100, 100)
        mock_renderer.offset_x.return_value = 0
        mock_renderer.draw.return_value.textbbox.return_value = (0, 0, 50, 12)

        self.text_label.render_pillow(mock_renderer)

        mock_load_default.assert_called_once_with(size=self.text_label._font_size)

    @patch("staticmaps.SvgRenderer")
    def test_render_svg(self, mock_renderer_class):
        """
        Tests that the SVG rendering of a TextLabel creates the expected SVG path and text elements and adds them to the group.
        """
        mock_renderer = MagicMock()
        mock_transformer = MagicMock()
        mock_transformer.ll2pixel.return_value = (100, 100)
        mock_renderer.transformer.return_value = mock_transformer

        mock_drawing = MagicMock()
        mock_path = MagicMock()
        mock_drawing.path.return_value = mock_path
        mock_drawing.text.return_value = MagicMock()
        mock_renderer.drawing.return_value = mock_drawing

        mock_group = MagicMock()
        mock_renderer.group.return_value = mock_group

        self.text_label.render_svg(mock_renderer)

        # Verify SVG elements were created
        mock_drawing.path.assert_called_once()
        mock_drawing.text.assert_called_once()
        self.assertEqual(mock_group.add.call_count, 2)


class TestGetMap(unittest.TestCase):
    """Test cases for map generation."""

    def setUp(self):
        """
        Initialize test locations for use in map-related test cases.
        """
        self.test_locations = [
            {
                "lat": TEST_LAT_SF,
                "lon": TEST_LON_SF,
                "label": "SF",
                "precisionBits": 14,
            },
            {"lat": 37.7849, "lon": -122.4094, "label": "Oakland"},
        ]

    @patch("staticmaps.Context")
    def test_get_map_default_params(self, mock_context_class):
        """
        Test that get_map generates a map with default zoom and image size, and adds all provided locations.
        """
        mock_context = MagicMock()
        mock_context_class.return_value = mock_context
        mock_context.render_pillow.return_value = MagicMock()

        get_map(self.test_locations)

        mock_context.set_tile_provider.assert_called_once()
        mock_context.set_zoom.assert_not_called()  # Should not be called when zoom is None
        self.assertEqual(mock_context.add_object.call_count, 3)
        mock_context.render_pillow.assert_called_once_with(1000, 1000)

    @patch("staticmaps.Context")
    def test_get_map_custom_params(self, mock_context_class):
        """
        Tests that get_map generates a map image using custom zoom, image size, anonymization, and radius parameters.
        """
        mock_context = MagicMock()
        mock_context_class.return_value = mock_context
        mock_context.render_pillow.return_value = MagicMock()

        get_map(
            self.test_locations,
            zoom=10,
            image_size=(800, 600),
            _anonymize=False,
            _radius=2000,
        )

        mock_context.set_zoom.assert_called_once_with(10)
        mock_context.render_pillow.assert_called_once_with(800, 600)


class TestImageUploadAndSend(unittest.TestCase):
    """Test cases for image upload and sending functionality."""

    def setUp(self):
        """
        Prepare MagicMock and AsyncMock instances for client, image, and upload response used in image upload and send tests.
        """
        # Use MagicMock instead of AsyncMock to prevent coroutine warnings
        self.mock_client = MagicMock()
        self.mock_client.upload = AsyncMock()
        self.mock_client.room_send = AsyncMock()
        self.mock_image = MagicMock()
        self.mock_upload_response = MagicMock()
        self.mock_upload_response.content_uri = "mxc://example.com/test123"

    def tearDown(self):
        """
        Reset mocks and clear references to prevent cross-test contamination and coroutine warnings after each test.
        """
        # Reset AsyncMock instances to prevent test pollution
        # Don't call .close() as it creates coroutines that need to be awaited
        if hasattr(self, "mock_client") and self.mock_client:
            self.mock_client.upload.reset_mock()
            self.mock_client.room_send.reset_mock()

    def test_upload_image(self):
        """
        Asynchronously tests that the image upload function saves an image to a buffer, uploads it via the client, and returns the correct upload response.
        """

        async def run_test() -> None:
            # Mock image save
            """
            Execute an asynchronous test that verifies upload_image uploads image bytes and returns the upload response.

            The test mocks an in-memory image buffer and the Matrix client's upload method, calls upload_image with the mocked client and image, and asserts that the returned value equals the mocked upload response and that client.upload was awaited exactly once.
            """
            mock_buffer = MagicMock()
            mock_buffer.getvalue.return_value = b"fake_image_data"

            with patch("io.BytesIO", return_value=mock_buffer):
                self.mock_client.upload.return_value = (self.mock_upload_response, None)

                result = await upload_image(
                    self.mock_client, self.mock_image, "test.png"
                )

                self.assertEqual(result, self.mock_upload_response)
                self.mock_client.upload.assert_awaited_once()

        asyncio.run(run_test())

    def test_send_room_image(self):
        """
        Asynchronously verifies that an image message is sent to the specified Matrix room with the correct content using the client.
        """

        async def run_test() -> None:
            room_id = "!test:example.com"

            await send_room_image(
                self.mock_client, room_id, self.mock_upload_response, "test.png"
            )

            self.mock_client.room_send.assert_awaited_once_with(
                room_id=room_id,
                message_type=MATRIX_EVENT_TYPE_ROOM_MESSAGE,
                content={
                    "msgtype": "m.image",
                    "url": "mxc://example.com/test123",
                    "body": "test.png",
                },
            )

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.upload_image")
    @patch("mmrelay.matrix_utils.send_room_image")
    def test_send_image(self, mock_send_room_image, mock_upload_image):
        """
        Ensure send_image uploads the image and sends it to the specified room using the provided filename.

        Calls send_image with a mock client, room ID, image, and filename; asserts that upload_image is awaited with the client, image, and filename, and that send_room_image is awaited with the client, room ID, upload response, and filename.
        """

        async def run_test() -> None:
            """
            Verify that send_image uploads the provided image and then sends the resulting upload response to the specified Matrix room.

            The test calls send_image with a mock client, room ID, image, and filename, then asserts that:
            - upload_image was awaited once with the client, image, and filename.
            - send_room_image was awaited once with the client, room ID, upload response, and filename.
            """
            room_id = "!test:example.com"
            mock_upload_image.return_value = self.mock_upload_response

            await send_image(self.mock_client, room_id, self.mock_image, "test.png")

            mock_upload_image.assert_awaited_once_with(
                client=self.mock_client, image=self.mock_image, filename="test.png"
            )
            mock_send_room_image.assert_awaited_once_with(
                self.mock_client,
                room_id,
                upload_response=self.mock_upload_response,
                filename="test.png",
                reply_to_event_id=None,
            )

        asyncio.run(run_test())


class TestMapPlugin(unittest.TestCase):
    """Test cases for the map Plugin class."""

    def setUp(self):
        """
        Set up a Plugin instance with predefined configuration for testing.

        Initializes the Plugin and assigns test-specific configuration values for zoom, image size, anonymization, and radius.
        """
        self.plugin = Plugin()
        self.plugin.send_matrix_reaction = AsyncMock()
        self.plugin.get_require_bot_mention = MagicMock(return_value=False)
        self.plugin.config = {
            "zoom": 10,
            "image_width": 800,
            "image_height": 600,
            "anonymize": True,
            "radius": 2000,
        }

    def tearDown(self):
        """
        Clean up test fixtures after each test to prevent cross-test contamination.

        Resets the plugin instance to ensure that no lingering references or mocks persist between tests.
        """
        # Clean up any references that might hold AsyncMock instances
        pass  # No need to set to None, just let it be cleaned up naturally

    def test_plugin_name(self):
        """
        Verify that the plugin_name property returns "map".
        """
        self.assertEqual(self.plugin.plugin_name, "map")

    def test_description(self):
        """
        Verifies that the plugin's description contains expected keywords related to map functionality.
        """
        description = self.plugin.description
        self.assertIn("Map of mesh radio nodes", description)
        self.assertIn("zoom", description)
        self.assertIn("size", description)

    def test_handle_meshtastic_message(self):
        """
        Tests that handle_meshtastic_message returns False when called with a Meshtastic message.
        """

        async def run_test() -> None:
            """
            Asynchronously tests that the plugin's `handle_meshtastic_message` method returns False when invoked with a sample message.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet=MagicMock(),
                formatted_message="test",
                longname="Test User",
                meshnet_name="TestNet",
            )
            self.assertFalse(result)

        asyncio.run(run_test())

    def test_get_matrix_commands(self):
        """
        Test that the plugin returns the correct list of Matrix commands.
        """
        commands = self.plugin.get_matrix_commands()
        self.assertEqual(commands, ["map"])

    def test_get_mesh_commands(self):
        """
        Test that the plugin's get_mesh_commands method returns an empty list.
        """
        commands = self.plugin.get_mesh_commands()
        self.assertEqual(commands, [])

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.plugins.map_plugin._connect_meshtastic_async")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_basic_map(
        self,
        mock_connect_matrix,
        mock_connect_meshtastic_async,
        mock_get_map,
        _mock_send_image,
    ):
        """
        Verify that receiving a "!map" Matrix room message causes the plugin to generate a map and send it to the room as "location.png".

        Asserts that when the plugin matches a "!map" command it calls get_map once and calls send_image with the Matrix client, the originating room ID, the generated image, and the filename "location.png".
        """

        async def run_test() -> None:
            # Setup mocks
            """
            Asynchronously tests that the plugin processes a "!map" room message by generating a map image and sending it to the Matrix room.

            This test ensures that when a "!map" command is received, the plugin matches the command, generates a map using node positions, and sends the resulting image to the correct Matrix room.
            """
            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map"

            # Use MagicMock instead of AsyncMock to prevent coroutine warnings
            mock_matrix_client = MagicMock()
            mock_matrix_client.room_send = AsyncMock()
            mock_connect_matrix.return_value = mock_matrix_client

            mock_meshtastic_client = MagicMock()
            mock_meshtastic_client.nodes = {
                "node1": {
                    "position": {"latitude": TEST_LAT_SF, "longitude": TEST_LON_SF},
                    "user": {"shortName": "SF"},
                }
            }
            mock_connect_meshtastic_async.return_value = mock_meshtastic_client

            mock_image = MagicMock()
            mock_get_map.return_value = mock_image

            # Mock the matches method to return True
            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "!map"
                )

            self.assertTrue(result)
            mock_get_map.assert_called_once()
            _mock_send_image.assert_called_once_with(
                mock_matrix_client,
                mock_room.room_id,
                mock_image,
                "location.png",
            )
            self.plugin.send_matrix_reaction.assert_called_once_with(
                "!test:example.com", mock_event.event_id, "✅"
            )

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.plugins.map_plugin._connect_meshtastic_async")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_with_zoom(
        self,
        mock_connect_matrix,
        mock_connect_meshtastic_async,
        mock_get_map,
        _mock_send_image,
    ):
        """
        Verify that handling a room command "!map zoom=15" triggers map generation with zoom=15 and that the handler returns True.

        Mocks Matrix and Meshtastic clients and a map image; asserts get_map was called with zoom=15 and the plugin handler returned True.
        """

        async def run_test() -> None:
            """
            Verify the plugin handles a "!map zoom=15" room message and forwards zoom=15 to the map generator.

            Mocks Matrix and Meshtastic clients, simulates a room message event, and asserts the handler returns True and that get_map was called with zoom=15.
            """
            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map zoom=15"

            # Use MagicMock instead of AsyncMock to prevent coroutine warnings
            mock_matrix_client = MagicMock()
            mock_matrix_client.room_send = AsyncMock()
            mock_connect_matrix.return_value = mock_matrix_client

            mock_meshtastic_client = MagicMock()
            # Add mock node with location data
            mock_meshtastic_client.nodes = {
                "!nodeid": {
                    "user": {"shortName": "Test"},
                    "position": {
                        "latitude": TEST_LAT_SF,
                        "longitude": TEST_LON_SF,
                        "precisionBits": 12,
                    },
                }
            }
            mock_connect_meshtastic_async.return_value = mock_meshtastic_client

            mock_image = MagicMock()
            mock_get_map.return_value = mock_image

            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "!map zoom=15"
                )

            self.assertTrue(result)
            # Check that get_map was called with zoom=15
            call_args = mock_get_map.call_args
            self.assertIsNotNone(call_args, "get_map should have been called")
            self.assertEqual(call_args[1]["zoom"], 15)

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.plugins.map_plugin._connect_meshtastic_async")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_with_size(
        self,
        mock_connect_matrix,
        mock_connect_meshtastic_async,
        mock_get_map,
        _mock_send_image,
    ):
        """
        Tests that the plugin processes a "!map" command with a custom image size, ensuring the generated map uses the specified dimensions.

        This test verifies that when a user specifies an image size (e.g., "!map size=500,400"), the map generation function receives the correct `image_size` parameter and the plugin returns True.
        """

        async def run_test() -> None:
            """
            Verify that a "!map size=500,400" room command produces a map request using the specified image size.

            Asserts the plugin handler returns True and that get_map was called with image_size set to (500, 400). The test configures mock Matrix and Meshtastic clients and a single node with location data to exercise the handler's parsing of the size parameter.
            """
            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map size=500,400"

            # Use MagicMock instead of AsyncMock to prevent coroutine warnings
            mock_matrix_client = MagicMock()
            mock_matrix_client.room_send = AsyncMock()
            mock_connect_matrix.return_value = mock_matrix_client

            mock_meshtastic_client = MagicMock()
            # Add mock node with location data
            mock_meshtastic_client.nodes = {
                "!nodeid": {
                    "user": {"shortName": "Test"},
                    "position": {
                        "latitude": TEST_LAT_SF,
                        "longitude": TEST_LON_SF,
                        "precisionBits": 12,
                    },
                }
            }
            mock_connect_meshtastic_async.return_value = mock_meshtastic_client

            mock_image = MagicMock()
            mock_get_map.return_value = mock_image

            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "!map size=500,400"
                )

            self.assertTrue(result)
            # Check that get_map was called with correct image_size
            call_args = mock_get_map.call_args
            self.assertEqual(call_args[1]["image_size"], (500, 400))

        asyncio.run(run_test())

    def test_handle_room_message_no_match(self):
        """
        Test that handle_room_message returns False when the message does not match the map command.

        Verifies that the plugin's handle_room_message method returns False if the incoming message does not correspond to the map command.
        """

        async def run_test() -> None:
            """
            Asynchronously tests that the plugin's handle_room_message method returns False when the message does not match the plugin command.
            """
            mock_room = MagicMock()
            mock_event = MagicMock()
            mock_event.body = "!help"

            with patch.object(self.plugin, "matches", return_value=False):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "!help"
                )

            self.assertFalse(result)

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.plugins.map_plugin._connect_meshtastic_async")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_invalid_zoom(
        self,
        mock_connect_matrix,
        mock_connect_meshtastic_async,
        mock_get_map,
        _mock_send_image,
    ):
        """
        Verify that handling "!map zoom=<invalid>" uses configured zoom fallback.

        Simulates receiving "!map zoom=50", asserts the handler returns True,
        and verifies get_map was invoked with zoom set to configured value 10.
        """

        async def run_test() -> None:
            """
            Verify invalid zoom input falls back to plugin-configured zoom.

            Simulates receiving "!map zoom=50" and asserts get_map uses
            plugin config zoom value (10) instead of the invalid provided value.
            """
            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map zoom=50"  # Invalid zoom > 30

            # Use MagicMock instead of AsyncMock to prevent coroutine warnings
            mock_matrix_client = MagicMock()
            mock_matrix_client.room_send = AsyncMock()
            mock_connect_matrix.return_value = mock_matrix_client

            mock_meshtastic_client = MagicMock()
            # Add mock node with location data
            mock_meshtastic_client.nodes = {
                "!nodeid": {
                    "user": {"shortName": "Test"},
                    "position": {
                        "latitude": TEST_LAT_SF,
                        "longitude": TEST_LON_SF,
                        "precisionBits": 12,
                    },
                }
            }
            mock_connect_meshtastic_async.return_value = mock_meshtastic_client

            mock_image = MagicMock()
            mock_get_map.return_value = mock_image

            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "!map zoom=50"
                )

            self.assertTrue(result)
            # Out-of-range numeric zoom should use configured fallback first.
            call_args = mock_get_map.call_args
            self.assertEqual(call_args[1]["zoom"], 10)

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.plugins.map_plugin._connect_meshtastic_async")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_invalid_config_zoom_falls_back_to_default(
        self,
        mock_connect_matrix,
        mock_connect_meshtastic_async,
        mock_get_map,
        _mock_send_image,
    ):
        """Invalid configured zoom should fall back to default when no zoom arg is provided."""

        async def run_test() -> None:
            self.plugin.config["zoom"] = "bad-zoom"

            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map"

            mock_matrix_client = MagicMock()
            mock_matrix_client.room_send = AsyncMock()
            mock_connect_matrix.return_value = mock_matrix_client

            mock_meshtastic_client = MagicMock()
            mock_meshtastic_client.nodes = {
                "!nodeid": {
                    "user": {"shortName": "Test"},
                    "position": {
                        "latitude": TEST_LAT_SF,
                        "longitude": TEST_LON_SF,
                        "precisionBits": 12,
                    },
                }
            }
            mock_connect_meshtastic_async.return_value = mock_meshtastic_client
            mock_get_map.return_value = MagicMock()

            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "!map"
                )

            self.assertTrue(result)
            self.assertEqual(mock_get_map.call_args.kwargs["zoom"], DEFAULT_MAP_ZOOM)

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.plugins.map_plugin._connect_meshtastic_async")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_oversized_image(
        self,
        mock_connect_matrix,
        mock_connect_meshtastic_async,
        mock_get_map,
        _mock_send_image,
    ):
        """
        Verify that oversized image size parameters in a "!map" room command are capped to 1000x1000 pixels.

        Asserts that the plugin processes the command successfully and calls get_map with image_size set to (1000, 1000).
        """

        async def run_test() -> None:
            """
            Verify that handling a room "!map" command caps oversized image size parameters to the configured maximum.

            Simulates a room message with "size=2000,1500", sets up mocked Matrix and Meshtastic clients and a mocked map image, and asserts the plugin returns success and calls get_map with image_size (1000, 1000).
            """
            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map size=2000,1500"  # Oversized

            # Use MagicMock instead of AsyncMock to prevent coroutine warnings
            mock_matrix_client = MagicMock()
            mock_matrix_client.room_send = AsyncMock()
            mock_connect_matrix.return_value = mock_matrix_client

            mock_meshtastic_client = MagicMock()
            # Add mock node with location data
            mock_meshtastic_client.nodes = {
                "!nodeid": {
                    "user": {"shortName": "Test"},
                    "position": {
                        "latitude": TEST_LAT_SF,
                        "longitude": TEST_LON_SF,
                        "precisionBits": 12,
                    },
                }
            }
            mock_connect_meshtastic_async.return_value = mock_meshtastic_client

            mock_image = MagicMock()
            mock_get_map.return_value = mock_image

            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "!map size=2000,1500"
                )

            self.assertTrue(result)
            # Check that image size was capped at 1000x1000
            call_args = mock_get_map.call_args
            self.assertEqual(call_args[1]["image_size"], (1000, 1000))

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.plugins.map_plugin._connect_meshtastic_async")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_no_locations(
        self,
        mock_connect_matrix,
        mock_connect_meshtastic_async,
        mock_get_map,
        _mock_send_image,
    ):
        """
        Ensure a friendly notice is sent when no nodes contain location data.
        """

        async def run_test() -> None:
            """
            Execute the plugin's room-message handler for a "!map" command when no node locations are available.

            Runs handle_room_message with a mocked Matrix room/event and a Meshtastic client that has nodes without location data, then verifies that the handler returns True, that a user-facing matrix message is sent, and that no map generation or image-sending functions are invoked.
            """
            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map"

            mock_matrix_client = MagicMock()
            mock_matrix_client.room_send = AsyncMock()
            mock_connect_matrix.return_value = mock_matrix_client

            mock_meshtastic_client = MagicMock()
            mock_meshtastic_client.nodes = {"node1": {"user": {"shortName": "n1"}}}
            mock_connect_meshtastic_async.return_value = mock_meshtastic_client

            self.plugin.send_matrix_message = AsyncMock()

            with patch.dict(os.environ, {}, clear=True):
                with patch.object(self.plugin, "matches", return_value=True):
                    result = await self.plugin.handle_room_message(
                        mock_room, mock_event, "!map"
                    )

            self.assertTrue(result)
            self.plugin.send_matrix_message.assert_awaited_once()
            mock_get_map.assert_not_called()
            _mock_send_image.assert_not_called()
            self.plugin.send_matrix_reaction.assert_called_once_with(
                "!test:example.com", mock_event.event_id, "❌"
            )

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.plugins.map_plugin._connect_meshtastic_async")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_image_upload_error(
        self,
        mock_connect_matrix,
        mock_connect_meshtastic_async,
        mock_get_map,
        mock_send_image,
    ):
        """Image upload errors should send a notice and return True."""

        async def run_test() -> None:
            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map"

            mock_matrix_client = MagicMock()
            mock_matrix_client.room_send = AsyncMock()
            mock_connect_matrix.return_value = mock_matrix_client

            mock_meshtastic_client = MagicMock()
            mock_meshtastic_client.nodes = {
                "!nodeid": {
                    "user": {"shortName": "Test"},
                    "position": {
                        "latitude": TEST_LAT_SF,
                        "longitude": TEST_LON_SF,
                        "precisionBits": 12,
                    },
                }
            }
            mock_connect_meshtastic_async.return_value = mock_meshtastic_client
            mock_get_map.return_value = MagicMock()
            mock_send_image.side_effect = ImageUploadError("upload failed")

            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "!map"
                )

            self.assertTrue(result)
            self.assertGreaterEqual(mock_matrix_client.room_send.await_count, 1)
            calls = mock_matrix_client.room_send.call_args_list
            error_call = next(
                (
                    c
                    for c in calls
                    if "failed" in c.kwargs.get("content", {}).get("body", "").lower()
                ),
                None,
            )
            self.assertIsNotNone(error_call)
            self.assertEqual(error_call.kwargs["room_id"], mock_room.room_id)
            self.plugin.send_matrix_reaction.assert_called_once_with(
                "!test:example.com", mock_event.event_id, "❌"
            )

        asyncio.run(run_test())


class TestPrecisionBitsToMeters(unittest.TestCase):
    def test_returns_none_for_zero(self):
        self.assertIsNone(precision_bits_to_meters(0))

    def test_returns_none_for_negative(self):
        self.assertIsNone(precision_bits_to_meters(-5))

    def test_returns_value_for_positive(self):
        result = precision_bits_to_meters(12)
        self.assertIsInstance(result, float)
        self.assertGreater(result, 0)


class TestGetMapEdgeCases(unittest.TestCase):
    @patch("staticmaps.Context")
    def test_get_map_no_valid_locations(self, mock_context_class):
        mock_context = MagicMock()
        mock_context_class.return_value = mock_context
        mock_context.render_pillow.return_value = MagicMock()

        get_map([{"lat": None, "lon": None, "label": "X"}])

        mock_context.set_center.assert_not_called()

    @patch("staticmaps.Context")
    def test_get_map_empty_locations(self, mock_context_class):
        mock_context = MagicMock()
        mock_context_class.return_value = mock_context
        mock_context.render_pillow.return_value = MagicMock()

        get_map([])

        mock_context.set_center.assert_not_called()
        mock_context.render_pillow.assert_called_once_with(1000, 1000)

    @patch("staticmaps.Context")
    def test_get_map_precision_bits_non_numeric(self, mock_context_class):
        mock_context = MagicMock()
        mock_context_class.return_value = mock_context
        mock_context.render_pillow.return_value = MagicMock()

        get_map(
            [
                {
                    "lat": TEST_LAT_SF,
                    "lon": TEST_LON_SF,
                    "label": "X",
                    "precisionBits": "abc",
                }
            ]
        )

        mock_context.add_object.assert_called_once()

    @patch("staticmaps.Context")
    def test_get_map_precision_bits_zero(self, mock_context_class):
        mock_context = MagicMock()
        mock_context_class.return_value = mock_context
        mock_context.render_pillow.return_value = MagicMock()

        get_map(
            [{"lat": TEST_LAT_SF, "lon": TEST_LON_SF, "label": "X", "precisionBits": 0}]
        )

        mock_context.add_object.assert_called_once()

    @patch("staticmaps.Context")
    def test_get_map_with_custom_zoom(self, mock_context_class):
        mock_context = MagicMock()
        mock_context_class.return_value = mock_context
        mock_context.render_pillow.return_value = MagicMock()

        get_map([], zoom=15)

        mock_context.set_zoom.assert_called_once_with(15)

    @patch("staticmaps.Context")
    def test_get_map_with_custom_image_size(self, mock_context_class):
        mock_context = MagicMock()
        mock_context_class.return_value = mock_context
        mock_context.render_pillow.return_value = MagicMock()

        get_map([], image_size=(500, 400))

        mock_context.render_pillow.assert_called_once_with(500, 400)


class TestMapPluginHandleRoomMessage(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()
        self.plugin.send_matrix_reaction = AsyncMock()
        self.plugin.get_require_bot_mention = MagicMock(return_value=False)
        self.plugin.config = {
            "zoom": 10,
            "image_width": 800,
            "image_height": 600,
        }

    def test_get_matrix_commands_none_name(self):
        self.plugin.plugin_name = None
        self.assertEqual(self.plugin.get_matrix_commands(), [])

    def test_handle_room_message_invalid_args(self):
        async def run_test() -> None:
            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map bogus=xyz"

            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "!map bogus=xyz"
                )
            self.assertFalse(result)

        asyncio.run(run_test())

    def test_handle_room_message_extract_args_returns_none(self):
        """Test that extract_command_args returning None causes early return (lines 529-530)."""

        async def run_test() -> None:
            self.plugin.matches = MagicMock(return_value=True)
            self.plugin.extract_command_args = MagicMock(return_value=None)

            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map"

            result = await self.plugin.handle_room_message(
                mock_room, mock_event, "!map"
            )
            self.assertFalse(result)

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.plugins.map_plugin._connect_meshtastic_async")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_config_zoom_out_of_range(
        self, mock_connect_matrix, mock_connect_mesh, mock_get_map, mock_send_image
    ):
        async def run_test() -> None:
            self.plugin.config["zoom"] = 50
            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map"

            mock_matrix_client = MagicMock()
            mock_matrix_client.room_send = AsyncMock()
            mock_connect_matrix.return_value = mock_matrix_client

            mock_mesh = MagicMock()
            mock_mesh.nodes = {
                "n1": {
                    "position": {"latitude": TEST_LAT_SF, "longitude": TEST_LON_SF},
                    "user": {"shortName": "T"},
                }
            }
            mock_connect_mesh.return_value = mock_mesh
            mock_get_map.return_value = MagicMock()

            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "!map"
                )

            self.assertTrue(result)
            self.assertEqual(mock_get_map.call_args.kwargs["zoom"], DEFAULT_MAP_ZOOM)

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.plugins.map_plugin._connect_meshtastic_async")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_image_size_config_fallback(
        self, mock_connect_matrix, mock_connect_mesh, mock_get_map, mock_send_image
    ):
        async def run_test() -> None:
            self.plugin.config["image_width"] = "bad"
            self.plugin.config["image_height"] = "bad"
            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map"

            mock_matrix_client = MagicMock()
            mock_matrix_client.room_send = AsyncMock()
            mock_connect_matrix.return_value = mock_matrix_client

            mock_mesh = MagicMock()
            mock_mesh.nodes = {
                "n1": {
                    "position": {"latitude": TEST_LAT_SF, "longitude": TEST_LON_SF},
                    "user": {"shortName": "T"},
                }
            }
            mock_connect_mesh.return_value = mock_mesh
            mock_get_map.return_value = MagicMock()

            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "!map"
                )

            self.assertTrue(result)
            call_args = mock_get_map.call_args
            self.assertEqual(call_args[1]["image_size"], (1000, 1000))

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.plugins.map_plugin._connect_meshtastic_async")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_matrix_unavailable(
        self, mock_connect_matrix, mock_connect_mesh, mock_get_map, mock_send_image
    ):
        async def run_test() -> None:
            mock_connect_matrix.return_value = None
            self.plugin.send_matrix_message = AsyncMock()

            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map"

            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "!map"
                )

            self.assertTrue(result)
            self.plugin.send_matrix_message.assert_not_awaited()
            self.plugin.send_matrix_reaction.assert_not_called()
            mock_get_map.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.plugins.map_plugin._connect_meshtastic_async")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_meshtastic_unavailable(
        self, mock_connect_matrix, mock_connect_mesh, mock_get_map, mock_send_image
    ):
        async def run_test() -> None:
            mock_matrix_client = MagicMock()
            mock_matrix_client.room_send = AsyncMock()
            mock_connect_matrix.return_value = mock_matrix_client

            mock_connect_mesh.return_value = None
            self.plugin.send_matrix_message = AsyncMock()

            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map"

            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "!map"
                )

            self.assertTrue(result)
            self.plugin.send_matrix_message.assert_awaited_once()
            mock_get_map.assert_not_called()
            self.plugin.send_matrix_reaction.assert_called_once_with(
                "!test:example.com", mock_event.event_id, "❌"
            )

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.plugins.map_plugin._connect_meshtastic_async")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_meshtastic_no_nodes(
        self, mock_connect_matrix, mock_connect_mesh, mock_get_map, mock_send_image
    ):
        async def run_test() -> None:
            mock_matrix_client = MagicMock()
            mock_matrix_client.room_send = AsyncMock()
            mock_connect_matrix.return_value = mock_matrix_client

            mock_mesh = MagicMock()
            mock_mesh.nodes = None
            mock_connect_mesh.return_value = mock_mesh
            self.plugin.send_matrix_message = AsyncMock()

            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map"

            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "!map"
                )

            self.assertTrue(result)
            self.plugin.send_matrix_message.assert_awaited_once()

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.plugins.map_plugin._connect_meshtastic_async")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_generic_exception(
        self, mock_connect_matrix, mock_connect_mesh, mock_get_map, mock_send_image
    ):
        """Test generic exception handler in handle_room_message (lines 664-669)."""
        mock_connect_matrix.side_effect = RuntimeError("unexpected boom")

        mock_room = MagicMock()
        mock_room.room_id = "!test:example.com"
        mock_event = MagicMock()
        mock_event.body = "!map"

        with patch.object(self.plugin, "matches", return_value=True):

            async def run_test() -> None:
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "!map"
                )
                self.assertTrue(result)
                self.plugin.send_matrix_reaction.assert_called_once_with(
                    "!test:example.com", mock_event.event_id, "❌"
                )

            asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
