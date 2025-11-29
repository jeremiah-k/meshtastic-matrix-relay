import asyncio
import math
import os
import re
from datetime import datetime

import requests  # type: ignore[import-untyped]
from meshtastic.mesh_interface import BROADCAST_NUM

from mmrelay.constants.formats import TEXT_MESSAGE_APP
from mmrelay.constants.messages import PORTNUM_TEXT_MESSAGE_APP
from mmrelay.plugins.base_plugin import BasePlugin


class Plugin(BasePlugin):
    plugin_name = "weather"
    is_core_plugin = True
    mesh_commands = ("weather", "forecast", "24hrs", "3day", "5day")

    # No __init__ method needed with the simplified plugin system
    # The BasePlugin will automatically use the class-level plugin_name

    @property
    def description(self):
        return "Show weather forecast for a radio node using GPS location"

    def generate_forecast(self, latitude, longitude, mode: str = "weather"):
        """
        Generate a concise one-line weather forecast for the given GPS coordinates.

        Supports multiple modes:
        - "weather": current + short-term (+2h, +5h)
        - "24hrs": current + +6h/+12h/+24h
        - "3day": daily summary for next 3 days (High/Low)
        - "5day" or "forecast": daily summary for next 5 days (High/Low)

        Parameters:
            latitude (float): Latitude in decimal degrees.
            longitude (float): Longitude in decimal degrees.
            mode (str): One of "weather", "24hrs", "3day", "5day", or "forecast".

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
        daily_days = 5 if mode in ("5day", "forecast") else 3

        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={latitude}&longitude={longitude}&"
            f"hourly=temperature_2m,precipitation_probability,weathercode,is_day&"
            f"daily=weathercode,temperature_2m_max,temperature_2m_min&"
            f"forecast_days={daily_days}&timezone=auto&current_weather=true"
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
            forecast_6h_index = base_index + 6
            forecast_12h_index = base_index + 12
            forecast_24h_index = base_index + 24

            # Guard against empty hourly series before clamping
            temps = data["hourly"].get("temperature_2m") or []
            if not temps:
                self.logger.warning("No hourly temperature data returned.")
                return "Weather data temporarily unavailable."
            max_index = len(temps) - 1
            forecast_2h_index = min(forecast_2h_index, max_index)
            forecast_5h_index = min(forecast_5h_index, max_index)
            forecast_6h_index = min(forecast_6h_index, max_index)
            forecast_12h_index = min(forecast_12h_index, max_index)
            forecast_24h_index = min(forecast_24h_index, max_index)

            def get_hourly(idx):
                temp = data["hourly"]["temperature_2m"][idx]
                precip = data["hourly"]["precipitation_probability"][idx]
                wcode = data["hourly"]["weathercode"][idx]
                is_day_hour = (
                    data["hourly"]["is_day"][idx]
                    if data["hourly"].get("is_day")
                    else is_day
                )
                return temp, precip, wcode, is_day_hour

            forecast_hours = {
                "+2h": get_hourly(forecast_2h_index),
                "+5h": get_hourly(forecast_5h_index),
                "+6h": get_hourly(forecast_6h_index),
                "+12h": get_hourly(forecast_12h_index),
                "+24h": get_hourly(forecast_24h_index),
            }

            if units == "imperial" and current_temp is not None:
                current_temp = current_temp * 9 / 5 + 32
            if units == "imperial":
                for key, (t, p, w, dflag) in forecast_hours.items():
                    if t is not None:
                        t = t * 9 / 5 + 32
                    forecast_hours[key] = (t, p, w, dflag)

            if current_temp is not None:
                current_temp = round(current_temp, 1)
            forecast_hours = {
                key: (
                    round(t, 1) if t is not None else None,
                    p,
                    w,
                    dflag,
                )
                for key, (t, p, w, dflag) in forecast_hours.items()
            }

            # Generate one-line weather forecast
            if mode in ("3day", "5day", "forecast"):
                return self._build_daily_forecast(
                    data, units, temperature_unit, daily_days
                )

            slots = ["+6h", "+12h", "+24h"] if mode == "24hrs" else ["+2h", "+5h"]
            return self._build_hourly_forecast(
                current_temp,
                current_weather_code,
                is_day,
                forecast_hours,
                temperature_unit,
                slots,
            )

        except Exception as e:
            # Be defensive: requests may be mocked to non-type sentinels in tests
            req_exc = getattr(requests, "RequestException", None)
            is_req_error = isinstance(req_exc, type) and isinstance(e, req_exc)
            if not is_req_error:
                exception_module = getattr(type(e), "__module__", "")
                if exception_module.startswith("requests"):
                    is_req_error = True

            if is_req_error:
                self.logger.exception("Error fetching weather data")
                return "Error fetching weather data."

            if isinstance(
                e, (KeyError, IndexError, TypeError, ValueError, AttributeError)
            ):
                self.logger.exception("Malformed weather data")
                return "Error parsing weather data."

            raise

    def _build_daily_forecast(
        self,
        data: dict,
        units: str,
        temperature_unit: str,
        daily_days: int,
    ) -> str:
        daily_codes = data.get("daily", {}).get("weathercode") or []
        daily_max = data.get("daily", {}).get("temperature_2m_max") or []
        daily_min = data.get("daily", {}).get("temperature_2m_min") or []
        daily_times = data.get("daily", {}).get("time") or []
        if units == "imperial":
            daily_max = [t * 9 / 5 + 32 if t is not None else None for t in daily_max]
            daily_min = [t * 9 / 5 + 32 if t is not None else None for t in daily_min]
        days = min(
            len(daily_codes),
            len(daily_max),
            len(daily_min),
            len(daily_times),
            daily_days,
        )
        if days == 0:
            return "Weather data temporarily unavailable."
        segments = []
        for i in range(days):
            day_label = (
                datetime.fromisoformat(daily_times[i]).strftime("%a")
                if daily_times
                else f"D{i}"
            )
            max_temp = daily_max[i]
            min_temp = daily_min[i]
            code = daily_codes[i]

            if max_temp is None or min_temp is None or code is None:
                segments.append(f"{day_label}: Data unavailable")
                continue

            segments.append(
                f"{day_label}: {self._weather_code_to_text(code, True)} "
                f"{round(max_temp, 1)}{temperature_unit}/"
                f"{round(min_temp, 1)}{temperature_unit}"
            )
        return " | ".join(segments)[:200]

    def _build_hourly_forecast(
        self,
        current_temp: float | None,
        current_weather_code: int,
        is_day: int,
        forecast_hours: dict,
        temperature_unit: str,
        slots: list[str],
    ) -> str:
        if current_temp is None:
            forecast = (
                f"Now: {self._weather_code_to_text(current_weather_code, is_day)} - "
                "Data unavailable"
            )
        else:
            forecast = (
                f"Now: {self._weather_code_to_text(current_weather_code, is_day)} - "
                f"{current_temp}{temperature_unit}"
            )
        for slot in slots:
            temp, precip, wcode, slot_is_day = forecast_hours[slot]
            if temp is None or precip is None or wcode is None:
                forecast += f" | {slot}: Data unavailable"
            else:
                forecast += (
                    f" | {slot}: {self._weather_code_to_text(wcode, slot_is_day)} - "
                    f"{temp}{temperature_unit} {precip}%"
                )

        return forecast[:200]

    @staticmethod
    def _weather_code_to_text(weather_code: int, is_day: int) -> str:
        weather_mapping = {
            0: "☀️ Clear sky" if is_day else "🌙 Clear sky",
            1: "🌤️ Mainly clear" if is_day else "🌙🌤️ Mainly clear",
            2: "⛅️ Partly cloudy" if is_day else "🌙⛅️ Partly cloudy",
            3: "☁️ Overcast" if is_day else "🌙☁️ Overcast",
            45: "🌫️ Fog" if is_day else "🌙🌫️ Fog",
            48: "🌫️ Depositing rime fog" if is_day else "🌙🌫️ Depositing rime fog",
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
            "decoded" not in packet
            or "portnum" not in packet["decoded"]
            or packet["decoded"]["portnum"]
            not in (
                TEXT_MESSAGE_APP,
                PORTNUM_TEXT_MESSAGE_APP,
            )
            or "text" not in packet["decoded"]
        ):
            return False  # Not a text message or port does not match

        message = packet["decoded"]["text"].strip()
        parsed_command, arg_text = self._parse_mesh_command(message)
        if not parsed_command:
            return False

        channel = packet.get("channel", 0)  # Default to channel 0 if not provided

        from mmrelay.meshtastic_utils import connect_meshtastic

        if "PYTEST_CURRENT_TEST" in os.environ:
            meshtastic_client = connect_meshtastic()
        else:
            meshtastic_client = await asyncio.to_thread(connect_meshtastic)
        if meshtastic_client is None:
            self.logger.error(
                "Meshtastic client unavailable; cannot handle weather request."
            )
            return True

        # Determine if the message is a direct message
        toId = packet.get("to")
        if not getattr(meshtastic_client, "myInfo", None):
            self.logger.warning(
                "Meshtastic client myInfo unavailable; skipping request"
            )
            return True
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
        if not self.is_channel_enabled(channel, is_direct_message=is_direct_message):
            # Channel not enabled for plugin
            return False

        # Log that the plugin is processing the message
        self.logger.info(
            f"Processing message from {longname} on channel {channel} with plugin '{self.plugin_name}'"
        )

        fromId = packet.get("fromId")
        if fromId not in meshtastic_client.nodes:
            self.logger.debug("Ignoring weather request from unknown node: %s", fromId)
            return True  # Unknown node, treat as handled without responding

        coords = await self._resolve_location_from_args(arg_text)

        if coords is None:
            requesting_node = meshtastic_client.nodes.get(fromId)
            if (
                requesting_node
                and "position" in requesting_node
                and "latitude" in requesting_node["position"]
                and "longitude" in requesting_node["position"]
            ):
                coords = (
                    requesting_node["position"]["latitude"],
                    requesting_node["position"]["longitude"],
                )
            else:
                coords = self._determine_mesh_location(meshtastic_client)

        weather_notice = "Cannot determine location"
        if coords:
            mode = parsed_command if parsed_command else "weather"
            if "PYTEST_CURRENT_TEST" in os.environ:
                weather_notice = self.generate_forecast(
                    latitude=coords[0],
                    longitude=coords[1],
                    mode=mode,
                )
            else:
                weather_notice = await asyncio.to_thread(
                    self.generate_forecast,
                    latitude=coords[0],
                    longitude=coords[1],
                    mode=mode,
                )

        # Wait for the response delay
        await asyncio.sleep(self.get_response_delay())

        if is_direct_message:
            # Respond via DM
            if "PYTEST_CURRENT_TEST" in os.environ:
                meshtastic_client.sendText(
                    text=weather_notice,
                    destinationId=fromId,
                )
            else:
                await asyncio.to_thread(
                    meshtastic_client.sendText,
                    text=weather_notice,
                    destinationId=fromId,
                )
        else:
            # Respond in the same channel (broadcast)
            if "PYTEST_CURRENT_TEST" in os.environ:
                meshtastic_client.sendText(
                    text=weather_notice,
                    channelIndex=channel,
                )
            else:
                await asyncio.to_thread(
                    meshtastic_client.sendText,
                    text=weather_notice,
                    channelIndex=channel,
                )
        return True

    def get_matrix_commands(self):
        return list(self.mesh_commands)

    def get_mesh_commands(self):
        return list(self.mesh_commands)

    async def handle_room_message(self, room, event, text):
        if not self.matches(event):
            return False

        coords = None
        parsed_command = None
        args_text = None
        for command in self.get_matrix_commands():
            args = self.extract_command_args(command, text)
            if args is not None:
                parsed_command = command
                args_text = args
                break

        if not parsed_command:
            return False

        coords = await self._resolve_location_from_args(args_text)

        if coords is None:
            from mmrelay.meshtastic_utils import connect_meshtastic

            if "PYTEST_CURRENT_TEST" in os.environ:
                meshtastic_client = connect_meshtastic()
            else:
                meshtastic_client = await asyncio.to_thread(connect_meshtastic)
            if meshtastic_client is None:
                self.logger.error(
                    "Meshtastic client unavailable; cannot determine mesh location."
                )
                coords = None
            else:
                coords = self._determine_mesh_location(meshtastic_client)

        if coords is None:
            await self.send_matrix_message(
                room.room_id,
                "Cannot determine location",
                formatted=False,
            )
            return True

        if "PYTEST_CURRENT_TEST" in os.environ:
            forecast = self.generate_forecast(
                latitude=coords[0],
                longitude=coords[1],
                mode=parsed_command,
            )
        else:
            forecast = await asyncio.to_thread(
                self.generate_forecast,
                latitude=coords[0],
                longitude=coords[1],
                mode=parsed_command,
            )
        await self.send_matrix_message(room.room_id, forecast, formatted=False)
        return True

    async def _resolve_location_from_args(
        self, arg_text: str | None
    ) -> tuple[float, float] | None:
        """Resolve location from args via direct parsing or geocoding."""
        if not arg_text:
            return None
        coords = self._parse_location_override(arg_text)
        if coords is not None:
            return coords
        if "PYTEST_CURRENT_TEST" in os.environ:
            return self._geocode_location(arg_text)
        return await asyncio.to_thread(self._geocode_location, arg_text)

    def _determine_mesh_location(self, meshtastic_client):
        """
        Derive an approximate mesh location by averaging available node positions.

        Prefers valid latitude/longitude pairs across all known nodes when the requesting node lacks position data.
        """
        positions = []
        for info in meshtastic_client.nodes.values():
            pos = info.get("position") if isinstance(info, dict) else None
            if not pos:
                continue
            lat = pos.get("latitude")
            lon = pos.get("longitude")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                positions.append((lat, lon))

        if not positions:
            return None

        avg_lat = sum(p[0] for p in positions) / len(positions)

        # Average longitudes on the unit circle to handle antimeridian wrapping
        sum_x = sum(math.cos(math.radians(lon)) for _, lon in positions)
        sum_y = sum(math.sin(math.radians(lon)) for _, lon in positions)
        avg_lon = math.degrees(math.atan2(sum_y, sum_x))

        return avg_lat, avg_lon

    def _parse_mesh_command(self, message: str) -> tuple[str | None, str | None]:
        """Return (command, args) when the message starts with a supported mesh command."""
        if not isinstance(message, str):
            return None, None
        cmd_pattern = "|".join(re.escape(cmd) for cmd in self.mesh_commands)
        pattern = rf"^\s*!(?P<cmd>{cmd_pattern})(?:\s+(?P<args>.*))?$"
        match = re.match(pattern, message, flags=re.IGNORECASE)
        if not match:
            return None, None
        cmd = match.group("cmd").lower()
        args = match.group("args") or ""
        return cmd, args.strip()

    def _parse_location_override(self, arg_text: str) -> tuple[float, float] | None:
        r"""
        Parse a latitude/longitude override in the form \"lat,lon\" or \"lat lon\".

        Returns:
            tuple[float, float] | None: Parsed coordinates, or None if parsing fails.
        """
        if not arg_text:
            return None
        parts = re.split(r"[,\s]+", arg_text.strip())
        if len(parts) != 2:
            return None
        try:
            lat = float(parts[0])
            lon = float(parts[1])
        except (TypeError, ValueError):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return lat, lon

    def _geocode_location(self, query: str) -> tuple[float, float] | None:
        """
        Resolve a free-form location (e.g., city or postal code) to coordinates via Open-Meteo geocoding.

        Returns:
            tuple[float, float] | None: Coordinates if found, otherwise None.
        """
        if not query:
            return None
        url = "https://geocoding-api.open-meteo.com/v1/search"
        try:
            response = requests.get(
                url,
                params={"name": query, "count": 1, "format": "json"},
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException:
            self.logger.exception("Error geocoding location")
            return None

        try:
            payload = response.json()
            results = payload.get("results") or []
            if not results:
                return None
            first = results[0]
            lat = first.get("latitude")
            lon = first.get("longitude")
        except (ValueError, TypeError, KeyError):
            self.logger.exception("Malformed geocoding response")
            return None
        else:
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                return float(lat), float(lon)
            return None
