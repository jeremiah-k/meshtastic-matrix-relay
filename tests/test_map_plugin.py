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

import pytest
import s2sphere
import staticmaps

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.matrix_utils import (
    send_image,
    send_room_image,
    upload_image,
)
from mmrelay.plugins.map_plugin import (
    Plugin,
    TextLabel,
    anonymize_location,
    get_map,
    textsize,
)


class TestTextLabel(unittest.TestCase):
    """Test cases for TextLabel class."""

    def setUp(self):
        """
        Initialize a TextLabel instance with a San Francisco coordinate and label for use in tests.
        """
        self.latlng = s2sphere.LatLng.from_degrees(37.7749, -122.4194)  # San Francisco
        self.text_label = TextLabel(self.latlng, "Test Label", fontSize=12)

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


class TestTextSizeFunction(unittest.TestCase):
    """Test cases for textsize function."""

    def test_textsize(self):
        """
        Tests that the textsize function returns the correct width and height based on the drawing context's text bounding box.
        """
        mock_draw = MagicMock()
        mock_draw.textbbox.return_value = (0, 0, 100, 20)

        width, height = textsize(mock_draw, "Test text")

        self.assertEqual(width, 100)
        self.assertEqual(height, 20)
        mock_draw.textbbox.assert_called_once_with((0, 0), "Test text")


class TestAnonymizeLocation(unittest.TestCase):
    """Test cases for location anonymization."""

    def test_anonymize_location_default_radius(self):
        """
        Verify that anonymize_location returns unchanged coordinates (firmware handles precision).
        """
        lat, lon = 37.7749, -122.4194
        new_lat, new_lon = anonymize_location(lat, lon)

        self.assertAlmostEqual(new_lat, lat)
        self.assertAlmostEqual(new_lon, lon)

    def test_anonymize_location_custom_radius(self):
        """
        Verify that anonymize_location ignores custom radius and returns original coordinates.
        """
        lat, lon = 37.7749, -122.4194
        radius = 5000  # 5km
        new_lat, new_lon = anonymize_location(lat, lon, radius)

        self.assertAlmostEqual(new_lat, lat)
        self.assertAlmostEqual(new_lon, lon)

    def test_anonymize_location_zero_radius(self):
        """
        Verify that anonymizing a location with a zero radius returns coordinates nearly identical to the original.
        """
        lat, lon = 37.7749, -122.4194
        new_lat, new_lon = anonymize_location(lat, lon, 0)

        # With zero radius, coordinates should be very close to original
        self.assertAlmostEqual(new_lat, lat, places=5)
        self.assertAlmostEqual(new_lon, lon, places=5)


class TestGetMap(unittest.TestCase):
    """Test cases for map generation."""

    def setUp(self):
        """
        Initialize test locations for use in map-related test cases.
        """
        self.test_locations = [
            {"lat": 37.7749, "lon": -122.4194, "label": "SF", "precisionBits": 14},
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
        mock_context.set_zoom.assert_called_once_with(None)
        self.assertGreaterEqual(
            mock_context.add_object.call_count, len(self.test_locations)
        )
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
            anonymize=False,
            radius=2000,
        )

        mock_context.set_zoom.assert_called_once_with(10)
        mock_context.render_pillow.assert_called_once_with(800, 600)

    @patch("mmrelay.plugins.map_plugin.anonymize_location")
    @patch("staticmaps.Context")
    def test_get_map_with_anonymization(self, mock_context_class, mock_anonymize):
        """
        Tests that anonymize_location is no longer invoked even when anonymization flag is passed.
        """
        mock_context = MagicMock()
        mock_context_class.return_value = mock_context
        mock_context.render_pillow.return_value = MagicMock()
        mock_anonymize.return_value = (37.7750, -122.4195)

        get_map(self.test_locations, anonymize=True, radius=5000)

        mock_anonymize.assert_not_called()


@pytest.mark.usefixtures("mock_event_loop")
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
        self.mock_client.upload.reset_mock()
        self.mock_client.room_send.reset_mock()
        # Clear references to prevent pollution
        self.mock_client = None
        self.mock_image = None
        self.mock_upload_response = None

    def test_upload_image(self):
        """
        Asynchronously tests that the image upload function saves an image to a buffer, uploads it via the client, and returns the correct upload response.
        """

        async def run_test():
            # Mock image save
            """
            Asynchronously tests that the upload_image function uploads an image using the client and returns the correct upload response.
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

        async def run_test():
            """
            Asynchronously verifies that an image message is sent to a Matrix room with the expected content and parameters.
            """
            room_id = "!test:example.com"

            await send_room_image(
                self.mock_client, room_id, self.mock_upload_response, "test.png"
            )

            self.mock_client.room_send.assert_awaited_once_with(
                room_id=room_id,
                message_type="m.room.message",
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

        async def run_test():
            """
            Verify image upload and room-send functions are invoked with the expected arguments during the image sending workflow.

            Sets up a mock upload response, calls send_image with a test filename, and asserts that upload_image and send_room_image were awaited with the correct parameters.
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
            )

        asyncio.run(run_test())


@pytest.mark.usefixtures("mock_event_loop")
class TestMapPlugin(unittest.TestCase):
    """Test cases for the map Plugin class."""

    def setUp(self):
        """
        Set up a Plugin instance with predefined configuration for testing.

        Initializes the Plugin and assigns test-specific configuration values for zoom, image size, anonymization, and radius.
        """
        self.plugin = Plugin()
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
        self.plugin = None

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

        async def run_test():
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
    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_basic_map(
        self,
        mock_connect_matrix,
        mock_connect_meshtastic,
        mock_get_map,
        mock_send_image,
    ):
        """
        Verify that receiving a "!map" Matrix room message causes the plugin to generate a map and send it to the room as "location.png".

        Asserts that when the plugin matches a "!map" command it calls get_map once and calls send_image with the Matrix client, the originating room ID, the generated image, and the filename "location.png".
        """

        async def run_test():
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
                    "position": {"latitude": 37.7749, "longitude": -122.4194},
                    "user": {"shortName": "SF"},
                }
            }
            mock_connect_meshtastic.return_value = mock_meshtastic_client

            mock_image = MagicMock()
            mock_get_map.return_value = mock_image

            # Mock the matches method to return True
            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "user: !map"
                )

            self.assertTrue(result)
            mock_get_map.assert_called_once()
            mock_send_image.assert_called_once_with(
                mock_matrix_client, mock_room.room_id, mock_image, "location.png"
            )

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_with_zoom(
        self,
        mock_connect_matrix,
        mock_connect_meshtastic,
        mock_get_map,
        mock_send_image,
    ):
        """
        Verifies that a "!map zoom=15" room command causes map generation to be invoked with zoom=15 and that the handler returns True.

        Mocks Matrix and Meshtastic clients, simulates a room message containing "zoom=15", and asserts get_map was called with the zoom parameter set to 15 and the plugin handler returned True.
        """

        async def run_test():
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
            mock_meshtastic_client.nodes = {}
            mock_connect_meshtastic.return_value = mock_meshtastic_client

            mock_image = MagicMock()
            mock_get_map.return_value = mock_image

            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "user: !map zoom=15"
                )

            self.assertTrue(result)
            # Check that get_map was called with zoom=15
            call_args = mock_get_map.call_args
            self.assertEqual(call_args[1]["zoom"], 15)

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_with_size(
        self,
        mock_connect_matrix,
        mock_connect_meshtastic,
        mock_get_map,
        mock_send_image,
    ):
        """
        Tests that the plugin processes a "!map" command with a custom image size, ensuring the generated map uses the specified dimensions.

        This test verifies that when a user specifies an image size (e.g., "!map size=500,400"), the map generation function receives the correct `image_size` parameter and the plugin returns True.
        """

        async def run_test():
            """
            Asynchronously tests that the plugin processes a "!map size=500,400" command by generating a map image with the specified size and returns True.

            Verifies that the `get_map` function receives the correct `image_size` parameter when a custom size is provided in the command.
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
            mock_meshtastic_client.nodes = {}
            mock_connect_meshtastic.return_value = mock_meshtastic_client

            mock_image = MagicMock()
            mock_get_map.return_value = mock_image

            with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "1"}, clear=False):
                with patch.object(self.plugin, "matches", return_value=True):
                    result = await self.plugin.handle_room_message(
                        mock_room, mock_event, "user: !map size=500,400"
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

        async def run_test():
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
    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_invalid_zoom(
        self,
        mock_connect_matrix,
        mock_connect_meshtastic,
        mock_get_map,
        mock_send_image,
    ):
        """
        Verify that an invalid zoom parameter in a "!map" command is replaced with the default zoom when handling a room message.

        Simulates a room event containing "!map zoom=50", awaits handle_room_message, asserts it returns True, and confirms get_map was invoked with zoom equal to 8.
        """
        """
        Run the asynchronous scenario that verifies an invalid zoom parameter is reset.
        
        Simulates a "!map zoom=50" room event, awaits handle_room_message, asserts a truthy result, and confirms get_map was invoked with zoom equal to 8.
        """

        async def run_test():
            """
            Verify that an invalid zoom parameter in a "!map" command is replaced with the default zoom level.

            Simulates receiving "!map zoom=50" and asserts that get_map is called with the default zoom (8) rather than the invalid value.
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
            mock_meshtastic_client.nodes = {}
            mock_connect_meshtastic.return_value = mock_meshtastic_client

            mock_image = MagicMock()
            mock_get_map.return_value = mock_image

            with patch.object(self.plugin, "matches", return_value=True):
                result = await self.plugin.handle_room_message(
                    mock_room, mock_event, "user: !map zoom=50"
                )

            self.assertTrue(result)
            # Check that zoom was reset to default (8)
            call_args = mock_get_map.call_args
            self.assertEqual(call_args[1]["zoom"], 8)

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_oversized_image(
        self,
        mock_connect_matrix,
        mock_connect_meshtastic,
        mock_get_map,
        mock_send_image,
    ):
        """
        Tests that the plugin caps oversized image size parameters to 1000x1000 pixels when handling a "!map" command in a room message.

        Ensures that if a user requests an image larger than the allowed maximum, the plugin enforces the size limit before generating and sending the map image.
        """

        async def run_test():
            """
            Asynchronously tests that the plugin caps oversized image size parameters in the "!map" command to the maximum allowed dimensions when handling a room message.

            Verifies that the map is generated with the capped image size and that the command is processed successfully.
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
            mock_meshtastic_client.nodes = {}
            mock_connect_meshtastic.return_value = mock_meshtastic_client

            mock_image = MagicMock()
            mock_get_map.return_value = mock_image

            with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "1"}, clear=False):
                with patch.object(self.plugin, "matches", return_value=True):
                    result = await self.plugin.handle_room_message(
                        mock_room, mock_event, "user: !map size=2000,1500"
                    )

            self.assertTrue(result)
            # Check that image size was capped at 1000x1000
            call_args = mock_get_map.call_args
            self.assertEqual(call_args[1]["image_size"], (1000, 1000))

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.send_image")
    @patch("mmrelay.plugins.map_plugin.get_map")
    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("mmrelay.matrix_utils.connect_matrix")
    def test_handle_room_message_no_locations(
        self,
        mock_connect_matrix,
        mock_connect_meshtastic,
        mock_get_map,
        mock_send_image,
    ):
        """
        Ensure a friendly notice is sent when no nodes contain location data.
        """

        async def run_test():
            mock_room = MagicMock()
            mock_room.room_id = "!test:example.com"
            mock_event = MagicMock()
            mock_event.body = "!map"

            mock_matrix_client = MagicMock()
            mock_matrix_client.room_send = AsyncMock()
            mock_connect_matrix.return_value = mock_matrix_client

            mock_meshtastic_client = MagicMock()
            mock_meshtastic_client.nodes = {"node1": {"user": {"shortName": "n1"}}}
            mock_connect_meshtastic.return_value = mock_meshtastic_client

            self.plugin.send_matrix_message = AsyncMock()

            with patch.dict(os.environ, {}, clear=True):
                with patch.object(self.plugin, "matches", return_value=True):
                    result = await self.plugin.handle_room_message(
                        mock_room, mock_event, "!map"
                    )

            self.assertTrue(result)
            self.plugin.send_matrix_message.assert_awaited_once()
            mock_get_map.assert_not_called()
            mock_send_image.assert_not_called()

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
