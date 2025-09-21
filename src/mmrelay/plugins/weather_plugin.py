import asyncio
from datetime import datetime

import requests
from meshtastic.mesh_interface import BROADCAST_NUM

from mmrelay.constants.formats import TEXT_MESSAGE_APP
from mmrelay.plugins.base_plugin import BasePlugin


class Plugin(BasePlugin):
    plugin_name = "weather"

    # No __init__ method needed with the simplified plugin system
    # The BasePlugin will automatically use the class-level plugin_name

    @property
    def description(self):
        return "Show weather forecast for a radio node using GPS location"

    def generate_forecast(self, latitude, longitude):
        """
        Generate a concise one-line weather forecast for the given GPS coordinates.

        Queries the Open-Meteo API for current conditions and hour-aligned forecasts at approximately +2h and +5h, formats temperatures according to self.config.get("units", "metric") ("metric" -> °C, "imperial" -> °F), and returns a single-line summary like:
        "Now: ☀️ Clear sky - 12.3°C | +2h: 🌧️ Light rain - 13.1°C 20% | +5h: ⛅️ Partly cloudy - 10.8°C 5%".

        Parameters:
            latitude (float): Latitude in decimal degrees.
            longitude (float): Longitude in decimal degrees.

        Returns:
            str: A one-line forecast string on success. On recoverable failures returns one of:
                 - "Weather data temporarily unavailable." (missing hourly data),
                 - "Error fetching weather data." (network/HTTP/request errors),
                 - "Error parsing weather data." (malformed or unexpected API response).

        Notes:
            - The function attempts to anchor forecasts to hourly timestamps when available; if a timestamp match cannot be found it falls back to hour-of-day indexing.
            - Network/request-related errors and parsing errors are handled as described above; unexpected exceptions are re-raised.
        """
        units = self.config.get("units", "metric")  # Default to metric
        temperature_unit = "°C" if units == "metric" else "°F"

        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={latitude}&longitude={longitude}&"
            f"hourly=temperature_2m,precipitation_probability,weathercode,is_day&"
            f"forecast_days=2&timezone=auto&current_weather=true"
        )

        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            # Extract relevant weather data
            current_temp = data["current_weather"]["temperature"]
            current_weather_code = data["current_weather"]["weathercode"]
            is_day = data["current_weather"]["is_day"]
            current_time_str = data["current_weather"]["time"]

            # Parse current time to get the hour with defensive handling
            current_hour = 0
            current_time = None
            try:
                current_time = datetime.fromisoformat(
                    current_time_str.replace("Z", "+00:00")
                )
                current_hour = current_time.hour
            except ValueError as ex:
                self.logger.warning(
                    f"Unexpected current_weather.time '{current_time_str}': {ex}. Defaulting to hour=0."
                )

            # Calculate indices for +2h and +5h forecasts
            # Try to anchor to hourly timestamps for robustness, fall back to hour-of-day
            base_index = current_hour
            hourly_times = data["hourly"].get("time", [])
            if hourly_times and current_time:
                try:
                    # Normalize current time to the hour and find it in hourly timestamps
                    base_key = current_time.replace(
                        minute=0, second=0, microsecond=0
                    ).strftime("%Y-%m-%dT%H:00")
                    base_index = hourly_times.index(base_key)
                except (ValueError, AttributeError):
                    # Fall back to hour-of-day if hourly timestamps are unavailable/mismatched
                    self.logger.warning(
                        "Could not find current time in hourly timestamps. "
                        "Falling back to hour-of-day indexing, which may be inaccurate."
                    )

            forecast_2h_index = base_index + 2
            forecast_5h_index = base_index + 5

            # Guard against empty hourly series before clamping
            temps = data["hourly"].get("temperature_2m") or []
            if not temps:
                self.logger.warning("No hourly temperature data returned.")
                return "Weather data temporarily unavailable."
            max_index = len(temps) - 1
            forecast_2h_index = min(forecast_2h_index, max_index)
            forecast_5h_index = min(forecast_5h_index, max_index)

            forecast_2h_temp = data["hourly"]["temperature_2m"][forecast_2h_index]
            forecast_2h_precipitation = data["hourly"]["precipitation_probability"][
                forecast_2h_index
            ]
            forecast_2h_weather_code = data["hourly"]["weathercode"][forecast_2h_index]
            # Get hour-specific day/night flag for +2h forecast
            forecast_2h_is_day = (
                data["hourly"]["is_day"][forecast_2h_index]
                if data["hourly"].get("is_day")
                else is_day
            )

            forecast_5h_temp = data["hourly"]["temperature_2m"][forecast_5h_index]
            forecast_5h_precipitation = data["hourly"]["precipitation_probability"][
                forecast_5h_index
            ]
            forecast_5h_weather_code = data["hourly"]["weathercode"][forecast_5h_index]
            # Get hour-specific day/night flag for +5h forecast
            forecast_5h_is_day = (
                data["hourly"]["is_day"][forecast_5h_index]
                if data["hourly"].get("is_day")
                else is_day
            )

            if units == "imperial":
                # Convert temperatures from Celsius to Fahrenheit
                current_temp = current_temp * 9 / 5 + 32
                forecast_2h_temp = forecast_2h_temp * 9 / 5 + 32
                forecast_5h_temp = forecast_5h_temp * 9 / 5 + 32

            current_temp = round(current_temp, 1)
            forecast_2h_temp = round(forecast_2h_temp, 1)
            forecast_5h_temp = round(forecast_5h_temp, 1)

            def weather_code_to_text(weather_code, is_day):
                weather_mapping = {
                    0: "☀️ Clear sky" if is_day else "🌙 Clear sky",
                    1: "🌤️ Mainly clear" if is_day else "🌙🌤️ Mainly clear",
                    2: "⛅️ Partly cloudy" if is_day else "🌙⛅️ Partly cloudy",
                    3: "☁️ Overcast" if is_day else "🌙☁️ Overcast",
                    45: "🌫️ Fog" if is_day else "🌙🌫️ Fog",
                    48: (
                        "🌫️ Depositing rime fog" if is_day else "🌙🌫️ Depositing rime fog"
                    ),
                    51: "🌧️ Light drizzle",
                    53: "🌧️ Moderate drizzle",
                    55: "🌧️ Dense drizzle",
                    56: "🌧️ Light freezing drizzle",
                    57: "🌧️ Dense freezing drizzle",
                    61: "🌧️ Light rain",
                    63: "🌧️ Moderate rain",
                    65: "🌧️ Heavy rain",
                    66: "🌧️ Light freezing rain",
                    67: "🌧️ Heavy freezing rain",
                    71: "❄️ Light snow fall",
                    73: "❄️ Moderate snow fall",
                    75: "❄️ Heavy snow fall",
                    77: "❄️ Snow grains",
                    80: "🌧️ Light rain showers",
                    81: "🌧️ Moderate rain showers",
                    82: "🌧️ Violent rain showers",
                    85: "❄️ Light snow showers",
                    86: "❄️ Heavy snow showers",
                    95: "⛈️ Thunderstorm",
                    96: "⛈️ Thunderstorm with slight hail",
                    99: "⛈️ Thunderstorm with heavy hail",
                }

                return weather_mapping.get(weather_code, "❓ Unknown")

            # Generate one-line weather forecast
            forecast = (
                f"Now: {weather_code_to_text(current_weather_code, is_day)} - "
                f"{current_temp}{temperature_unit} | "
            )
            forecast += (
                f"+2h: {weather_code_to_text(forecast_2h_weather_code, forecast_2h_is_day)} - "
                f"{forecast_2h_temp}{temperature_unit} {forecast_2h_precipitation}% | "
            )
            forecast += (
                f"+5h: {weather_code_to_text(forecast_5h_weather_code, forecast_5h_is_day)} - "
                f"{forecast_5h_temp}{temperature_unit} {forecast_5h_precipitation}%"
            )

            return forecast

        except Exception as e:
            # Handle HTTP/network errors from requests
            # Handle requests-related exceptions using safe attribute checking
            try:
                # Check if this is a requests exception by checking the module
                if hasattr(requests, "RequestException") and isinstance(
                    e, requests.RequestException
                ):
                    self.logger.exception("Error fetching weather data")
                    return "Error fetching weather data."
            except (AttributeError, TypeError):
                # Fallback to string-based detection if isinstance fails
                exception_module = getattr(type(e), "__module__", "")
                if "requests" in exception_module:
                    self.logger.exception("Error fetching weather data")
                    return "Error fetching weather data."

            # Handle data parsing errors
            if isinstance(
                e, (KeyError, IndexError, TypeError, ValueError, AttributeError)
            ):
                self.logger.exception("Malformed weather data")
                return "Error parsing weather data."
            else:
                # Re-raise unexpected exceptions
                raise

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ):
        """
        Processes incoming Meshtastic text messages and responds with a weather forecast if the plugin command is detected.

        Checks if the message is a valid text message on the expected port, verifies channel and command enablement, retrieves the sender's GPS location, generates a weather forecast, and sends the response either as a direct message or broadcast depending on the message type.

        Returns:
            bool: True if the message was handled and a response was sent; False otherwise.
        """
        if (
            "decoded" in packet
            and "portnum" in packet["decoded"]
            and packet["decoded"]["portnum"] == TEXT_MESSAGE_APP
            and "text" in packet["decoded"]
        ):
            message = packet["decoded"]["text"].strip()
            channel = packet.get("channel", 0)  # Default to channel 0 if not provided

            from mmrelay.meshtastic_utils import connect_meshtastic

            meshtastic_client = connect_meshtastic()

            # Determine if the message is a direct message
            toId = packet.get("to")
            myId = meshtastic_client.myInfo.my_node_num  # Get relay's own node number

            if toId == myId:
                # Direct message to us
                is_direct_message = True
            elif toId == BROADCAST_NUM:
                is_direct_message = False
            else:
                # Message to someone else; we may ignore it
                is_direct_message = False

            # Pass is_direct_message to is_channel_enabled
            if not self.is_channel_enabled(
                channel, is_direct_message=is_direct_message
            ):
                # Channel not enabled for plugin
                return False

            if f"!{self.plugin_name}" not in message.lower():
                return False

            # Log that the plugin is processing the message
            self.logger.info(
                f"Processing message from {longname} on channel {channel} with plugin '{self.plugin_name}'"
            )

            fromId = packet.get("fromId")
            if fromId in meshtastic_client.nodes:
                weather_notice = "Cannot determine location"
                requesting_node = meshtastic_client.nodes.get(fromId)
                if (
                    requesting_node
                    and "position" in requesting_node
                    and "latitude" in requesting_node["position"]
                    and "longitude" in requesting_node["position"]
                ):
                    weather_notice = self.generate_forecast(
                        latitude=requesting_node["position"]["latitude"],
                        longitude=requesting_node["position"]["longitude"],
                    )

                # Wait for the response delay
                await asyncio.sleep(self.get_response_delay())

                if is_direct_message:
                    # Respond via DM
                    meshtastic_client.sendText(
                        text=weather_notice,
                        destinationId=fromId,
                    )
                else:
                    # Respond in the same channel (broadcast)
                    meshtastic_client.sendText(
                        text=weather_notice,
                        channelIndex=channel,
                    )
            return True
        else:
            return False  # Not a text message or port does not match

    def get_matrix_commands(self):
        return []

    def get_mesh_commands(self):
        return [self.plugin_name]

    async def handle_room_message(self, room, event, full_message):
        return False  # Not handling Matrix messages in this plugin
