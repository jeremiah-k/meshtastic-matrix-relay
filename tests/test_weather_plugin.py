#!/usr/bin/env python3
"""
Test suite for the MMRelay weather plugin.

Tests the weather forecast functionality including:
- Weather API integration with Open-Meteo
- Temperature unit conversion (metric/imperial)
- Weather code to text/emoji mapping
- GPS location-based weather requests
- Direct message vs broadcast handling
- Channel enablement checking
- Error handling for API failures
"""

import copy
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.plugins.weather_plugin import Plugin


class TestWeatherPlugin(unittest.TestCase):
    """Test cases for the weather plugin."""

    def setUp(self):
        """
        Initialize the test environment for each test case by creating a Plugin instance with mocked dependencies and sample weather data.
        """
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()
        self.plugin.config = {"units": "metric"}

        # Mock the is_channel_enabled method
        self.plugin.is_channel_enabled = MagicMock(return_value=True)

        # Mock the get_response_delay method
        self.plugin.get_response_delay = MagicMock(return_value=1.0)

        # Sample weather API response for 2 days (48 hours)
        # Current time is set to 10:00
        self.sample_weather_data = {
            "current_weather": {
                "temperature": 22.5,
                "weathercode": 1,
                "is_day": 1,
                "time": "2023-08-20T10:00",
            },
            "hourly": {
                "time": [f"2023-08-20T{h:02d}:00" for h in range(24)]
                + [f"2023-08-21T{h:02d}:00" for h in range(24)],
                "temperature_2m": [
                    15.0,  # 00:00
                    14.5,  # 01:00
                    14.0,  # 02:00
                    13.5,  # 03:00
                    13.0,  # 04:00
                    12.5,  # 05:00
                    12.0,  # 06:00
                    11.5,  # 07:00
                    11.0,  # 08:00
                    12.0,  # 09:00
                    18.0,  # 10:00 (current time)
                    20.0,  # 11:00
                    21.0,  # 12:00 (+2h from current)
                    22.0,  # 13:00
                    22.5,  # 14:00
                    23.0,  # 15:00 (+5h from current)
                    23.5,  # 16:00
                    23.0,  # 17:00
                    22.0,  # 18:00
                    21.0,  # 19:00
                    20.0,  # 20:00
                    19.0,  # 21:00
                    18.0,  # 22:00
                    17.0,  # 23:00
                ]
                + [15.0] * 24,  # Next day data
                "precipitation_probability": [
                    0,   # 00:00
                    0,   # 01:00
                    0,   # 02:00
                    5,   # 03:00
                    5,   # 04:00
                    10,  # 05:00
                    10,  # 06:00
                    15,  # 07:00
                    15,  # 08:00
                    10,  # 09:00
                    10,  # 10:00 (current time)
                    5,   # 11:00
                    5,   # 12:00 (+2h from current)
                    10,  # 13:00
                    15,  # 14:00
                    20,  # 15:00 (+5h from current)
                    25,  # 16:00
                    30,  # 17:00
                    25,  # 18:00
                    20,  # 19:00
                    15,  # 20:00
                    10,  # 21:00
                    5,   # 22:00
                    5,   # 23:00
                ]
                + [0] * 24,  # Next day data
                "weathercode": [
                    1,   # 00:00
                    1,   # 01:00
                    2,   # 02:00
                    2,   # 03:00
                    3,   # 04:00
                    3,   # 05:00
                    45,  # 06:00
                    45,  # 07:00
                    51,  # 08:00
                    51,  # 09:00
                    61,  # 10:00 (current time)
                    61,  # 11:00
                    63,  # 12:00 (+2h from current)
                    63,  # 13:00
                    65,  # 14:00
                    65,  # 15:00 (+5h from current)
                    80,  # 16:00
                    80,  # 17:00
                    81,  # 18:00
                    81,  # 19:00
                    82,  # 20:00
                    82,  # 21:00
                    95,  # 22:00
                    95,  # 23:00
                ]
                + [1] * 24,  # Next day data
                "is_day": [
                    0,   # 00:00 - night
                    0,   # 01:00 - night
                    0,   # 02:00 - night
                    0,   # 03:00 - night
                    0,   # 04:00 - night
                    0,   # 05:00 - night
                    1,   # 06:00 - day
                    1,   # 07:00 - day
                    1,   # 08:00 - day
                    1,   # 09:00 - day
                    1,   # 10:00 (current time) - day
                    1,   # 11:00 - day
                    1,   # 12:00 (+2h from current) - day
                    1,   # 13:00 - day
                    1,   # 14:00 - day
                    1,   # 15:00 (+5h from current) - day
                    1,   # 16:00 - day
                    1,   # 17:00 - day
                    1,   # 18:00 - day
                    0,   # 19:00 - night
                    0,   # 20:00 - night
                    0,   # 21:00 - night
                    0,   # 22:00 - night
                    0,   # 23:00 - night
                ]
                + [1] * 24,  # Next day data (all day)
            },
        }

    def test_plugin_name(self):
        """
        Verify that the plugin's name is set to "weather".
        """
        self.assertEqual(self.plugin.plugin_name, "weather")

    def test_description_property(self):
        """
        Test that the plugin's description property returns the expected string.
        """
        description = self.plugin.description
        self.assertEqual(
            description, "Show weather forecast for a radio node using GPS location"
        )

    def test_get_matrix_commands(self):
        """
        Test that the plugin's get_matrix_commands method returns an empty list.
        """
        commands = self.plugin.get_matrix_commands()
        self.assertEqual(commands, [])

    def test_get_mesh_commands(self):
        """
        Test that the plugin's get_mesh_commands method returns the expected list of mesh commands.
        """
        commands = self.plugin.get_mesh_commands()
        self.assertEqual(commands, ["weather"])

    def test_handle_room_message_always_false(self):
        """
        Test that handle_room_message always returns False asynchronously.
        """

        async def run_test():
            """
            Asynchronously tests that handle_room_message always returns False when called.

            Returns:
                bool: False, indicating the message was not handled.
            """
            result = await self.plugin.handle_room_message(None, None, "message")
            self.assertFalse(result)

        import asyncio

        asyncio.run(run_test())

    @patch("requests.get")
    def test_generate_forecast_metric_units(self, mock_get):
        """
        Test that the weather forecast is generated correctly using metric units.

        Verifies that the plugin requests weather data with the correct API parameters, parses the response, and formats the forecast string with Celsius temperatures, weather descriptions, emojis, and precipitation probabilities.
        """
        mock_response = MagicMock()
        mock_response.json.return_value = self.sample_weather_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        self.plugin.config = {"units": "metric"}

        forecast = self.plugin.generate_forecast(40.7128, -74.0060)

        # Should make API request with correct parameters
        mock_get.assert_called_once()
        # Ensure HTTP errors would surface
        mock_response.raise_for_status.assert_called_once()
        call_args = mock_get.call_args[0][0]
        self.assertIn("latitude=40.7128", call_args)
        self.assertIn(
            "longitude=-74.006", call_args
        )  # May be formatted without trailing zero
        self.assertIn("api.open-meteo.com", call_args)
        self.assertIn("forecast_days=2", call_args)
        self.assertIn("timezone=auto", call_args)

        # Verify timeout is set
        self.assertEqual(mock_get.call_args.kwargs.get("timeout"), 10)

        # Should contain current weather
        self.assertIn("Now: 🌤️ Mainly clear", forecast)
        self.assertIn("22.5°C", forecast)

        # Should contain 2h forecast (index 12: weathercode 63 = Moderate rain)
        self.assertIn("+2h: 🌧️ Moderate rain", forecast)
        self.assertIn("21.0°C", forecast)
        self.assertIn("5%", forecast)

        # Should contain 5h forecast (index 15: weathercode 65 = Heavy rain)
        self.assertIn("+5h: 🌧️ Heavy rain", forecast)
        self.assertIn("23.0°C", forecast)
        self.assertIn("20%", forecast)

    @patch("requests.get")
    def test_generate_forecast_imperial_units(self, mock_get):
        """
        Test that the weather forecast is generated with temperatures converted to Fahrenheit when imperial units are configured.

        Verifies that the forecast output includes correctly converted and rounded Fahrenheit temperatures based on sample weather data.
        """
        mock_response = MagicMock()
        mock_response.json.return_value = self.sample_weather_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        self.plugin.config = {"units": "imperial"}

        forecast = self.plugin.generate_forecast(40.7128, -74.0060)

        # Should convert temperatures to Fahrenheit
        # 22.5°C = 72.5°F, 21.0°C = 69.8°F, 23.0°C = 73.4°F
        self.assertIn("72.5°F", forecast)
        self.assertIn("69.8°F", forecast)
        self.assertIn("73.4°F", forecast)

    @patch("requests.get")
    def test_generate_forecast_time_based_indexing_early_morning(self, mock_get):
        """Test time-based indexing when current time is early morning (2:00 AM)."""
        # Create weather data for early morning scenario
        early_morning_data = {
            "current_weather": {
                "temperature": 15.0,
                "weathercode": 1,
                "is_day": 0,
                "time": "2023-08-20T02:00",  # 2:00 AM
            },
            "hourly": {
                "time": [f"2023-08-20T{h:02d}:00" for h in range(24)],
                "temperature_2m": [10.0 + h for h in range(24)],  # 10.0, 11.0, 12.0, ...
                "precipitation_probability": [h * 2 for h in range(24)],  # 0, 2, 4, ...
                "weathercode": [1] * 24,
                "is_day": [0 if h < 6 or h > 18 else 1 for h in range(24)],  # Night before 6am and after 6pm
            },
        }

        mock_response = MagicMock()
        mock_response.json.return_value = early_morning_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        forecast = self.plugin.generate_forecast(40.7128, -74.0060)

        # Current time is 2:00 AM (index 2)
        # +2h forecast should be 4:00 AM (index 4): temp 14.0°C, precip 8%
        # +5h forecast should be 7:00 AM (index 7): temp 17.0°C, precip 14%
        self.assertIn("15.0°C", forecast)  # Current temp
        self.assertIn("14.0°C", forecast)  # +2h temp
        self.assertIn("17.0°C", forecast)  # +5h temp
        self.assertIn("8%", forecast)      # +2h precipitation
        self.assertIn("14%", forecast)     # +5h precipitation

    @patch("requests.get")
    def test_generate_forecast_time_based_indexing_late_evening(self, mock_get):
        """Test time-based indexing when current time is late evening (22:00)."""
        # Create weather data for late evening scenario
        late_evening_data = {
            "current_weather": {
                "temperature": 25.0,
                "weathercode": 2,
                "is_day": 0,
                "time": "2023-08-20T22:00",  # 10:00 PM
            },
            "hourly": {
                "time": [f"2023-08-20T{h:02d}:00" for h in range(24)]
                + [f"2023-08-21T{h:02d}:00" for h in range(24)],
                "temperature_2m": [15.0 + (h % 12) for h in range(48)],  # Varying temps
                "precipitation_probability": [h for h in range(48)],
                "weathercode": [2] * 48,
                "is_day": [0 if h % 24 < 6 or h % 24 > 18 else 1 for h in range(48)],  # Day/night cycle
            },
        }

        mock_response = MagicMock()
        mock_response.json.return_value = late_evening_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        forecast = self.plugin.generate_forecast(40.7128, -74.0060)

        # Current time is 22:00 (index 22)
        # +2h forecast should be 24:00/00:00 next day (index 24): temp 15.0°C, precip 24%
        # +5h forecast should be 03:00 next day (index 27): temp 18.0°C, precip 27%
        self.assertIn("25.0°C", forecast)  # Current temp (distinct from +5h)
        self.assertIn("15.0°C", forecast)  # +2h temp (next day)
        self.assertIn("18.0°C", forecast)  # +5h temp (next day)
        self.assertIn("24%", forecast)     # +2h precipitation
        self.assertIn("27%", forecast)     # +5h precipitation

    @patch("requests.get")
    def test_generate_forecast_bounds_checking(self, mock_get):
        """Test that forecast indices are properly bounded to prevent array overflow."""
        # Create weather data with limited hours (only 24 hours)
        limited_data = {
            "current_weather": {
                "temperature": 20.0,
                "weathercode": 1,
                "is_day": 1,
                "time": "2023-08-20T21:00",  # 9:00 PM
            },
            "hourly": {
                "time": [f"2023-08-20T{h:02d}:00" for h in range(24)],  # Only 24 hours
                "temperature_2m": [20.0] * 23 + [25.0],
                "precipitation_probability": [10] * 23 + [15],
                "weathercode": [1] * 24,
                "is_day": [0 if h < 6 or h > 18 else 1 for h in range(24)],  # Day/night cycle
            },
        }

        mock_response = MagicMock()
        mock_response.json.return_value = limited_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        forecast = self.plugin.generate_forecast(40.7128, -74.0060)

        # Current time is 21:00 (index 21)
        # +2h would be index 23 (valid)
        # +5h would be index 26 (invalid, should be clamped to 23)
        # Both +2h and +5h should be clamped to the last hour (index 23)
        self.assertIn("20.0°C", forecast)   # Current temp remains 20.0°C
        self.assertIn("25.0°C", forecast)   # Forecast temps use last hour
        self.assertIn("15%", forecast)      # Forecast precip uses last hour

    @patch("requests.get")
    def test_generate_forecast_datetime_parsing_with_timezone(self, mock_get):
        """Test datetime parsing with different timezone formats."""
        timezone_data = {
            "current_weather": {
                "temperature": 25.0,
                "weathercode": 0,
                "is_day": 1,
                "time": "2023-08-20T14:30:00Z",  # UTC timezone format
            },
            "hourly": {
                "time": [f"2023-08-20T{h:02d}:00" for h in range(24)],
                "temperature_2m": [25.0] * 24,
                "precipitation_probability": [5] * 24,
                "weathercode": [0] * 24,
                "is_day": [0 if h < 6 or h > 18 else 1 for h in range(24)],  # Day/night cycle
            },
        }

        mock_response = MagicMock()
        mock_response.json.return_value = timezone_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        # Should not raise an exception and should parse correctly
        forecast = self.plugin.generate_forecast(40.7128, -74.0060)
        self.assertIn("25.0°C", forecast)
        self.assertIn("☀️ Clear sky", forecast)

    @patch("requests.get")
    def test_generate_forecast_timezone_offset_parsing(self, mock_get):
        """Test datetime parsing with timezone offset format."""
        offset_data = {
            "current_weather": {
                "temperature": 22.0,
                "weathercode": 2,
                "is_day": 1,
                "time": "2023-08-20T16:30:00+02:30",  # Timezone offset format
            },
            "hourly": {
                "time": [f"2023-08-20T{h:02d}:00" for h in range(24)],
                "temperature_2m": [22.0] * 24,
                "precipitation_probability": [8] * 24,
                "weathercode": [2] * 24,
                "is_day": [0 if h < 6 or h > 18 else 1 for h in range(24)],  # Day/night cycle
            },
        }

        mock_response = MagicMock()
        mock_response.json.return_value = offset_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        # Should parse timezone offset correctly (16:30 -> hour 16)
        forecast = self.plugin.generate_forecast(40.7128, -74.0060)
        self.assertIn("22.0°C", forecast)
        self.assertIn("⛅️ Partly cloudy", forecast)

    @patch("requests.get")
    def test_generate_forecast_invalid_time_defaults_to_zero(self, mock_get):
        """Test that malformed timestamps default to hour=0 without raising exceptions."""
        invalid_time_data = {
            "current_weather": {
                "temperature": 20.0,
                "weathercode": 1,
                "is_day": 1,
                "time": "not-a-time",  # Invalid timestamp
            },
            "hourly": {
                "time": [f"2023-08-20T{h:02d}:00" for h in range(24)],
                "temperature_2m": [h for h in range(24)],  # 0, 1, 2, ...
                "precipitation_probability": [h for h in range(24)],  # 0, 1, 2, ...
                "weathercode": [1] * 24,
                "is_day": [1] * 24,
            },
        }

        mock_response = MagicMock()
        mock_response.json.return_value = invalid_time_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        # Should not raise; falls back to hour=0 -> +2h index 2, +5h index 5
        forecast = self.plugin.generate_forecast(40.7128, -74.0060)
        self.assertIn("Now:", forecast)
        self.assertIn("20.0°C", forecast)  # Current temp
        self.assertIn("2°C", forecast)     # +2h temp (index 2)
        self.assertIn("5°C", forecast)     # +5h temp (index 5)
        self.assertIn("2%", forecast)      # +2h precipitation
        self.assertIn("5%", forecast)      # +5h precipitation

    @patch("requests.get")
    def test_generate_forecast_http_error(self, mock_get):
        """Test that HTTP errors are handled gracefully."""
        import requests

        # Mock the requests.get to raise an HTTPError
        mock_get.side_effect = requests.exceptions.HTTPError("500 Server Error")

        forecast = self.plugin.generate_forecast(40.7128, -74.0060)
        # HTTP errors are caught and handled gracefully
        # The test should pass with either error message since both indicate proper error handling
        self.assertIn("Error", forecast)

    @patch("requests.get")
    def test_generate_forecast_empty_hourly_data(self, mock_get):
        """Test that empty hourly data is handled gracefully."""
        empty_hourly_data = {
            "current_weather": {
                "temperature": 20.0,
                "weathercode": 1,
                "is_day": 1,
                "time": "2023-08-20T14:00",
            },
            "hourly": {
                "time": [],
                "temperature_2m": [],  # Empty array
                "precipitation_probability": [],
                "weathercode": [],
                "is_day": [],
            },
        }

        mock_response = MagicMock()
        mock_response.json.return_value = empty_hourly_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        forecast = self.plugin.generate_forecast(40.7128, -74.0060)
        self.assertEqual(forecast, "Weather data temporarily unavailable.")

    @patch("requests.get")
    def test_generate_forecast_timestamp_anchoring(self, mock_get):
        """Test that forecast indexing uses timestamp anchoring when available."""
        # Create data where timestamp anchoring would give different results than hour-of-day
        anchoring_data = {
            "current_weather": {
                "temperature": 20.0,
                "weathercode": 1,
                "is_day": 1,
                "time": "2023-08-20T14:00:00",  # 2:00 PM
            },
            "hourly": {
                # Start timestamps at 12:00 instead of 00:00 to test anchoring
                "time": [f"2023-08-20T{h:02d}:00" for h in range(12, 24)],
                "temperature_2m": [15.0 + h for h in range(12)],  # 15.0, 16.0, 17.0, ...
                "precipitation_probability": [h * 3 for h in range(12)],  # 0, 3, 6, ...
                "weathercode": [1] * 12,
                "is_day": [1] * 12,
            },
        }

        mock_response = MagicMock()
        mock_response.json.return_value = anchoring_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        forecast = self.plugin.generate_forecast(40.7128, -74.0060)

        # With timestamp anchoring: 14:00 is at index 2 in the array (12:00, 13:00, 14:00, ...)
        # +2h forecast should be index 4 (16:00): temp 19.0°C, precip 12%
        # +5h forecast should be index 7 (19:00): temp 22.0°C, precip 21%
        self.assertIn("20.0°C", forecast)  # Current temp
        self.assertIn("19.0°C", forecast)  # +2h temp
        self.assertIn("22.0°C", forecast)  # +5h temp
        self.assertIn("12%", forecast)     # +2h precipitation
        self.assertIn("21%", forecast)     # +5h precipitation

    @patch("requests.get")
    def test_generate_forecast_night_weather_codes(self, mock_get):
        """
        Test that the forecast generation uses night-specific weather descriptions and emojis when night weather codes are present in the API response.
        """
        night_weather_data = copy.deepcopy(self.sample_weather_data)
        night_weather_data["current_weather"]["is_day"] = 0  # Night time

        mock_response = MagicMock()
        mock_response.json.return_value = night_weather_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        forecast = self.plugin.generate_forecast(40.7128, -74.0060)

        # Should use night weather descriptions
        self.assertIn("🌙🌤️ Mainly clear", forecast)

    @patch("requests.get")
    def test_generate_forecast_unknown_weather_code(self, mock_get):
        """
        Test that the forecast generation handles unknown weather codes gracefully.

        Mocks the weather API response to include an unrecognized weather code and verifies that the generated forecast string indicates an unknown weather condition.
        """
        unknown_weather_data = self.sample_weather_data.copy()
        unknown_weather_data["current_weather"]["weathercode"] = 999  # Unknown code

        mock_response = MagicMock()
        mock_response.json.return_value = unknown_weather_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        forecast = self.plugin.generate_forecast(40.7128, -74.0060)

        # Should handle unknown weather codes gracefully
        self.assertIn("❓ Unknown", forecast)

    def test_handle_meshtastic_message_not_text_message(self):
        """
        Test that the plugin ignores Meshtastic messages that are not text messages.

        Verifies that handling a Meshtastic packet with a non-text port number results in the handler returning False.
        """
        packet = {
            "decoded": {
                "portnum": "TELEMETRY_APP",  # Not TEXT_MESSAGE_APP
                "data": "some data",
            }
        }

        async def run_test():
            """
            Runs an asynchronous test to verify that handling a Meshtastic message returns False.

            Returns:
                bool: False, indicating the message was not handled.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )
            self.assertFalse(result)

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_handle_meshtastic_message_no_weather_command(self, mock_connect):
        """
        Test that a Meshtastic text message without the "!weather" command is ignored by the plugin.

        Verifies that the plugin's message handler returns False when processing a text message that does not contain the weather command.
        """
        mock_client = MagicMock()
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {
                "portnum": "TEXT_MESSAGE_APP",
                "text": "Hello world",  # No !weather command
            },
            "channel": 0,
        }

        async def run_test():
            """
            Runs an asynchronous test to verify that handling a Meshtastic message returns False.

            Returns:
                bool: False, indicating the message was not handled.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )
            self.assertFalse(result)

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_handle_meshtastic_message_channel_not_enabled(self, mock_connect):
        """
        Test that a "!weather" message on a disabled channel is not processed.

        Verifies that the plugin checks channel enablement, does not handle the message, and returns False when the channel is disabled.
        """
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        self.plugin.is_channel_enabled = MagicMock(return_value=False)

        packet = {
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "!weather"},
            "channel": 0,
        }

        async def run_test():
            """
            Asynchronously runs a test to verify that handling a Meshtastic message returns False and checks channel enablement with default parameters.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )
            self.assertFalse(result)
            self.plugin.is_channel_enabled.assert_called_once_with(
                0, is_direct_message=False
            )

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("requests.get")
    def test_handle_meshtastic_message_direct_message_with_location(
        self, mock_get, mock_connect
    ):
        """
        Tests that a direct Meshtastic message containing the "!weather" command from a node with location data triggers a weather forecast response sent directly to the sender.

        Verifies that the plugin retrieves the node's GPS location, fetches weather data, generates a forecast, and sends a direct message reply. Also checks that channel enablement is validated for direct messages and that the correct recipient and forecast content are used in the response.
        """
        # Mock weather API response
        mock_response = MagicMock()
        mock_response.json.return_value = self.sample_weather_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        # Mock meshtastic client
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_client.nodes = {
            "!12345678": {"position": {"latitude": 40.7128, "longitude": -74.0060}}
        }
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "!weather"},
            "channel": 0,
            "fromId": "!12345678",
            "to": 123456789,  # Direct message to relay
        }

        async def run_test():
            """
            Asynchronously tests that a direct Meshtastic message containing a weather command from a known node with location data triggers the plugin to send a direct forecast response and checks channel enablement for direct messages.

            Verifies that the plugin returns True, sends a direct message with the expected weather forecast, and calls the channel enablement check with the correct parameters.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            self.assertTrue(result)

            # Should send direct message response
            mock_client.sendText.assert_called_once()
            call_args = mock_client.sendText.call_args
            self.assertEqual(call_args.kwargs["destinationId"], "!12345678")
            self.assertIn("Now: 🌤️ Mainly clear", call_args.kwargs["text"])

            # Should check if channel is enabled for direct message
            self.plugin.is_channel_enabled.assert_called_once_with(
                0, is_direct_message=True
            )

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_handle_meshtastic_message_broadcast_no_location(self, mock_connect):
        """
        Test that a broadcast "!weather" message from a node without location data results in an error response.

        Verifies that the plugin sends a broadcast message indicating it cannot determine the location and checks channel enablement for broadcast messages.
        """
        # Mock meshtastic client
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_client.nodes = {
            "!12345678": {
                # No position data
            }
        }
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "!weather"},
            "channel": 0,
            "fromId": "!12345678",
            "to": 4294967295,  # BROADCAST_NUM
        }

        async def run_test():
            """
            Asynchronously tests that the plugin responds with an error message when handling a broadcast weather request without location data.

            Verifies that the plugin sends a broadcast message indicating location cannot be determined, checks channel enablement for broadcast, and returns True.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            self.assertTrue(result)

            # Should send broadcast response with error message
            mock_client.sendText.assert_called_once()
            call_args = mock_client.sendText.call_args
            self.assertEqual(call_args.kwargs["channelIndex"], 0)
            self.assertEqual(call_args.kwargs["text"], "Cannot determine location")

            # Should check if channel is enabled for broadcast
            self.plugin.is_channel_enabled.assert_called_once_with(
                0, is_direct_message=False
            )

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("requests.get")
    def test_handle_meshtastic_message_broadcast_with_location(
        self, mock_get, mock_connect
    ):
        """
        Tests that a broadcast "!weather" message from a node with location data triggers a weather API request and sends a broadcast response with the forecast.

        Verifies that the plugin correctly retrieves the node's location, fetches weather data, formats the forecast, and sends it as a broadcast message on the appropriate channel. Also checks that channel enablement is respected for broadcast messages.
        """
        # Mock weather API response
        mock_response = MagicMock()
        mock_response.json.return_value = self.sample_weather_data
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        # Mock meshtastic client
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_client.nodes = {
            "!12345678": {"position": {"latitude": 40.7128, "longitude": -74.0060}}
        }
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "!weather please"},
            "channel": 1,
            "fromId": "!12345678",
            "to": 4294967295,  # BROADCAST_NUM
        }

        async def run_test():
            """
            Asynchronously tests that a broadcast "!weather" message with location data triggers a weather forecast response.

            Verifies that the plugin sends a broadcast message with the correct weather forecast, checks channel enablement for broadcast, and returns True.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            self.assertTrue(result)

            # Should send broadcast response with weather data
            mock_client.sendText.assert_called_once()
            call_args = mock_client.sendText.call_args
            self.assertEqual(call_args.kwargs["channelIndex"], 1)
            self.assertIn("Now: 🌤️ Mainly clear", call_args.kwargs["text"])

            # Should check if channel is enabled for broadcast
            self.plugin.is_channel_enabled.assert_called_once_with(
                1, is_direct_message=False
            )

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_handle_meshtastic_message_unknown_node(self, mock_connect):
        """
        Test that a weather request from an unknown node returns True without sending a response message.
        """
        # Mock meshtastic client with no nodes
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_client.nodes = {}  # No nodes
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "!weather"},
            "channel": 0,
            "fromId": "!unknown",
            "to": 4294967295,
        }

        async def run_test():
            """
            Runs an asynchronous test to verify that handling a Meshtastic message from an unknown node returns True and does not send any message.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            # Should return True but not send any message (node not found)
            self.assertTrue(result)
            mock_client.sendText.assert_not_called()

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_handle_meshtastic_message_missing_channel(self, mock_connect):
        """
        Test that handling a Meshtastic message without a channel field defaults to channel 0 and checks channel enablement accordingly.
        """
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "!weather"},
            "fromId": "!12345678",
            # No channel field - should default to 0
        }

        async def run_test():
            """
            Runs an asynchronous test to verify that handling a Meshtastic message without a channel field defaults to channel 0 and checks channel enablement accordingly.
            """
            await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            # Should use default channel 0
            self.plugin.is_channel_enabled.assert_called_once_with(
                0, is_direct_message=False
            )

        import asyncio

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
