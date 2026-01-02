import asyncio
import math
import re
from datetime import datetime
from typing import Any

import requests  # type: ignore[import-untyped]
from meshtastic.mesh_interface import BROADCAST_NUM  # type: ignore[import-untyped]

from mmrelay.constants.formats import TEXT_MESSAGE_APP
from mmrelay.constants.messages import PORTNUM_TEXT_MESSAGE_APP
from mmrelay.constants.plugins import MAX_FORECAST_LENGTH
from mmrelay.plugins.base_plugin import BasePlugin


class Plugin(BasePlugin):
    plugin_name = "weather"
    is_core_plugin = True
    mesh_commands = ("weather", "hourly", "daily")

    # No __init__ method needed with the simplified plugin system
    # The BasePlugin will automatically use the class-level plugin_name

    @property
    def description(self) -> str:
        """
        Indicates the plugin provides weather forecasts for a radio node based on GPS coordinates.

        Returns:
            str: Short human-readable description of the plugin.
        """
        return "Show weather forecast for a radio node using GPS location"

    def _normalize_mode(self, mode: str) -> str:
        """
        Normalize a mode/command string to one of the supported forecast modes.

        Returns:
            str: One of "weather", "hourly", or "daily". Unrecognized or empty input defaults to "weather".
        """
        cmd = (mode or "weather").lower()
        if cmd == "hourly":
            return "hourly"
        if cmd == "daily":
            return "daily"
        return "weather"

    def generate_forecast(
        self, latitude: float, longitude: float, mode: str = "weather"
    ) -> str:
        """
        Generate a concise one-line weather forecast for the given GPS coordinates.

        Supports multiple modes:
        - "weather": current conditions only (no future slots)
        - "hourly": current + compact near-term view (+3h, +6h, +12h)
        - "daily": daily summary for the next few days (up to 5)

        Parameters:
            latitude (float): Latitude in decimal degrees.
            longitude (float): Longitude in decimal degrees.
            mode (str): One of "weather", "hourly", or "daily".

        Returns:
            str: A one-line forecast string on success. On recoverable failures returns one of:
                 - "Weather data temporarily unavailable." (missing hourly data),
                 - "Error fetching weather data." (network/HTTP/request errors),
                 - "Error parsing weather data." (malformed or unexpected API response).

        Notes:
            - The function attempts to anchor forecasts to hourly timestamps when available; if a timestamp match cannot be found it falls back to hour-of-day indexing.
            - Network/request-related errors and parsing errors are handled as described above; unexpected exceptions are re-raised.
        """
        mode_key = self._normalize_mode(mode)

        units = self.config.get("units", "metric")  # Default to metric
        temperature_unit = "°C" if units == "metric" else "°F"
        daily_days = 5 if mode_key == "daily" else 3

        hourly_config = {
            "weather": {
                "slots": ["now"],
                "offsets": [],
            },
            "hourly": {
                # Keep the hourly forecast compact to fit mesh payload limits
                "slots": ["+3h", "+6h", "+12h"],
                "offsets": [3, 6, 12],
            },
        }
        mode_offsets = hourly_config.get(mode_key, hourly_config["weather"])
        offsets = mode_offsets["offsets"]  # type: ignore[index]

        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={latitude}&longitude={longitude}&"
            f"hourly=temperature_2m,precipitation_probability,weathercode,is_day,"
            f"relativehumidity_2m,windspeed_10m,winddirection_10m&"
            f"daily=weathercode,temperature_2m_max,temperature_2m_min&"
            f"forecast_days={daily_days}&timezone=auto&current_weather=true"
        )

        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
        except (requests.exceptions.RequestException, AttributeError):
            self.logger.exception("Error fetching weather data")
            return "Error fetching weather data."

        try:
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

            # Guard against empty hourly series before clamping
            temps = data["hourly"].get("temperature_2m") or []
            precips = data["hourly"].get("precipitation_probability") or []
            humidities = data["hourly"].get("relativehumidity_2m") or []
            windspeeds = data["hourly"].get("windspeed_10m") or []
            winddirs = data["hourly"].get("winddirection_10m") or []
            if not temps:
                self.logger.warning("No hourly temperature data returned.")
                return "Weather data temporarily unavailable."
            max_index = len(temps) - 1

            # Build index map for requested offsets
            index_map: dict[str, int] = {
                f"+{offset}h": min(base_index + offset, max_index) for offset in offsets
            }
            index_map["now"] = min(base_index, max_index)

            def _safe_get(seq: Any, idx: Any) -> Any:
                """
                Safely retrieve an item from a sequence or mapping by index/key.

                Parameters:
                    seq: Sequence or mapping to index into; may be None or not subscriptable.
                    idx: Index or key used to access `seq`.

                Returns:
                    The value at `seq[idx]` if accessible, or `None` if the index/key is missing or `seq` cannot be indexed.
                """
                try:
                    return seq[idx]
                except (IndexError, TypeError, KeyError):
                    return None

            def get_hourly(idx: int) -> tuple[Any, Any, Any, Any, Any, Any, Any]:
                """
                Fetch hourly weather values at the given hourly index from the parsed API data arrays.

                Parameters:
                        idx (int): Hour offset index into the hourly arrays (0-based).

                Returns:
                        tuple: (temperature, precipitation, weather_code, is_day, humidity, wind_speed, wind_direction) where each element is the value at `idx` or `None` if that value is unavailable.
                """
                temp = _safe_get(temps, idx)
                precip = _safe_get(precips, idx)
                wcode = _safe_get(data["hourly"].get("weathercode", []), idx)
                is_day_hour = (
                    _safe_get(data["hourly"].get("is_day", []), idx)
                    if data["hourly"].get("is_day")
                    else is_day
                )
                humidity = _safe_get(humidities, idx)
                wind = _safe_get(windspeeds, idx)
                wind_dir = _safe_get(winddirs, idx)
                return temp, precip, wcode, is_day_hour, humidity, wind, wind_dir

            forecast_hours = {
                label: get_hourly(idx) for label, idx in index_map.items()
            }

            if units == "imperial":
                if current_temp is not None:
                    current_temp = current_temp * 9 / 5 + 32
                imperial_forecasts = {}
                for key, values in forecast_hours.items():
                    t, p, w, dflag, *rest = values
                    if t is not None:
                        t = t * 9 / 5 + 32
                    imperial_forecasts[key] = (t, p, w, dflag, *rest)
                forecast_hours = imperial_forecasts

            if current_temp is not None:
                current_temp = round(current_temp, 1)
            forecast_hours = {
                key: (
                    round(t, 1) if t is not None else None,
                    p,
                    w,
                    dflag,
                    *rest,
                )
                for key, (t, p, w, dflag, *rest) in forecast_hours.items()
            }

            # Generate one-line weather forecast
            if mode_key == "daily":
                return self._build_daily_forecast(
                    data, units, temperature_unit, daily_days
                )

            if mode_key == "weather":
                now_slot = forecast_hours.get("now")
                humidity = now_slot[4] if now_slot else None
                wind_speed = now_slot[5] if now_slot else None
                wind_dir = now_slot[6] if now_slot else None
                precip_now = now_slot[1] if now_slot else None
                wind_unit = "km/h"
                if units == "imperial" and wind_speed is not None:
                    wind_speed = wind_speed * 0.621371
                    wind_unit = "mph"
                parts = [
                    (
                        f"Now: {self._weather_code_to_text(current_weather_code, is_day)} - "
                        f"{current_temp}{temperature_unit}"
                        if current_temp is not None
                        else f"Now: {self._weather_code_to_text(current_weather_code, is_day)} - Data unavailable"
                    )
                ]
                if humidity is not None:
                    parts.append(f"Humidity {round(humidity)}%")
                if wind_speed is not None:
                    wind_part = f"Wind {round(wind_speed, 1)}{wind_unit}"
                    if wind_dir is not None:
                        wind_part += f" {round(wind_dir)}°"
                    parts.append(wind_part)
                if precip_now is not None:
                    parts.append(f"Precip {precip_now}%")
                return self._trim_to_max_bytes(" | ".join(parts))

            slots = hourly_config.get(mode_key, hourly_config["weather"])["slots"]  # type: ignore[index]
            return self._build_hourly_forecast(
                current_temp,
                current_weather_code,
                is_day,
                forecast_hours,
                temperature_unit,
                slots,
            )

        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            self.logger.exception("Malformed weather data")
            return "Error parsing weather data."
        except Exception:
            raise

    def _build_daily_forecast(
        self,
        data: dict[str, Any],
        units: str,
        temperature_unit: str,
        daily_days: int,
    ) -> str:
        """
        Builds a concise multi-day weather summary for display.

        Parameters:
            data (dict): Parsed Open-Meteo response containing daily keys like `weathercode`, `temperature_2m_max`, `temperature_2m_min`, and `time`.
            units (str): Unit system from configuration; when equal to `"imperial"`, temperatures are converted from Celsius to Fahrenheit.
            temperature_unit (str): Temperature unit symbol to append (e.g., "°C" or "°F").
            daily_days (int): Maximum number of days to include in the summary.

        Returns:
            str: A pipe-separated string with one segment per day (e.g., "Mon: ☀️ 20.0°C/10.0°C | Tue: …"), or "Weather data temporarily unavailable." if no valid daily entries; output is truncated to 200 characters.
        """
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
        return self._trim_to_max_bytes(" | ".join(segments))

    def _build_hourly_forecast(
        self,
        current_temp: float | None,
        current_weather_code: int,
        is_day: int,
        forecast_hours: dict[str, Any],
        temperature_unit: str,
        slots: list[str],
    ) -> str:
        """
        Builds a concise hourly forecast string starting with a "Now" segment followed by the requested slot segments.

        Parameters:
            current_temp (float | None): Current temperature or None if unavailable.
            current_weather_code (int): Weather code used to produce the descriptive text for the current time.
            is_day (int): Day/night indicator (0/1) for the current time used to select day/night descriptions.
            forecast_hours (dict): Mapping from slot label to a tuple with at least (temperature, precipitation_percent, weather_code, is_day). Missing values may be None.
            temperature_unit (str): Unit symbol appended to temperatures (e.g., "°C" or "°F").
            slots (list[str]): Ordered slot labels to include after "Now" (for example ["+2h", "+5h", "+12h"]).

        Returns:
            str: A compact forecast string where each segment is formatted as "<label>: <description> - <temp><unit> <precip>%" and segments with missing data show "Data unavailable"; the result is trimmed to the plugin's configured maximum length.
        """
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
            temp, precip, wcode, slot_is_day, *_rest = forecast_hours[slot]
            if temp is None or precip is None or wcode is None:
                forecast += f" | {slot}: Data unavailable"
            else:
                forecast += (
                    f" | {slot}: {self._weather_code_to_text(wcode, slot_is_day)} - "
                    f"{temp}{temperature_unit} {precip}%"
                )

        return self._trim_to_max_bytes(forecast)

    @staticmethod
    def _trim_to_max_bytes(text: str) -> str:
        """
        Trim a UTF-8 string so its UTF-8 encoding does not exceed MAX_FORECAST_LENGTH bytes.

        Parameters:
            text (str): Input string to trim.

        Returns:
            str: The original string if its UTF-8 encoding is within the limit, otherwise a truncated string whose UTF-8 encoding is at most MAX_FORECAST_LENGTH bytes (any partial trailing UTF-8 character is removed).
        """
        encoded = text.encode("utf-8")
        if len(encoded) <= MAX_FORECAST_LENGTH:
            return text
        # Truncate byte string and decode, ignoring partial trailing characters.
        return encoded[:MAX_FORECAST_LENGTH].decode("utf-8", "ignore")

    @staticmethod
    def _weather_code_to_text(weather_code: int, is_day: int) -> str:
        """
        Map an Open-Meteo numeric weather code and day/night indicator to a short emoji-prefixed description.

        Parameters:
            weather_code (int): Open-Meteo weather code.
            is_day (int): Day indicator (truthy for day, falsy for night).

        Returns:
            str: A brief human-readable description prefixed with an emoji (e.g., "☀️ Clear sky"), or "❓ Unknown" if the code is not recognized.
        """
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
        self,
        packet: dict[str, Any],
        formatted_message: str,
        longname: str,
        meshnet_name: str,
    ) -> bool:
        """
        Handle an incoming Meshtastic text message and respond with a weather forecast when a supported command is detected.

        Parses and validates the incoming packet and command, resolves a location (from command arguments, the sender node, or the mesh), generates a forecast, and sends the response either as a direct message or a channel broadcast as appropriate.

        Returns:
            bool: `True` if the packet was handled (a response was sent or the request was acknowledged as handled); `False` if the packet is not a relevant text message or command for this plugin.
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
            # Some radios send placeholder destination IDs; treat as broadcast to avoid dropping valid requests
            is_direct_message = False

        # Pass is_direct_message to is_channel_enabled
        if not self.is_channel_enabled(channel, is_direct_message=is_direct_message):  # type: ignore[no-untyped-call]
            # Channel not enabled for plugin
            return False

        # Log that the plugin is processing the message
        self.logger.info(
            f"Processing message from {longname} on channel {channel} with plugin '{self.plugin_name}'"
        )

        fromId = packet.get("fromId")
        if not fromId or fromId not in meshtastic_client.nodes:
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
            weather_notice = await asyncio.to_thread(
                self.generate_forecast,
                latitude=coords[0],
                longitude=coords[1],
                mode=mode,
            )

        # Wait for the response delay
        await asyncio.sleep(self.get_response_delay())  # type: ignore[no-untyped-call]

        if is_direct_message:
            # Respond via DM
            self.send_message(
                text=weather_notice,
                destination_id=fromId,
            )
        else:
            # Respond in the same channel (broadcast)
            self.send_message(
                text=weather_notice,
                channel=channel,
            )
        return True

    def get_matrix_commands(self) -> list[str]:
        """
        List command names the plugin exposes to Matrix integrations.

        Returns:
            list[str]: Command strings supported by this plugin.
        """
        return list(self.mesh_commands)

    def get_mesh_commands(self) -> list[str]:
        """
        List available mesh commands exposed by this plugin.

        Returns:
            list[str]: A copy of the plugin's mesh command names.
        """
        return list(self.mesh_commands)

    async def handle_room_message(
        self, room: Any, event: Any, full_message: str
    ) -> bool:
        """
        Handle a Matrix room message invoking the weather plugin and post a forecast to the room.

        Parses the room message for a supported weather command, resolves coordinates from command arguments, mesh-derived location, or geocoding, generates a forecast for the resolved coordinates, and sends the forecast back to the Matrix room. If a location cannot be determined, posts "Cannot determine location" to the room.

        Parameters:
            room: The Matrix room object where the event originated.
            event: The Matrix event object to evaluate for a plugin match.
            full_message (str): The raw message text used to extract command arguments.

        Returns:
            bool: `True` if the event matched the plugin and was handled (a response was sent or attempted), `False` if the event did not match and was not handled.
        """
        if not self.matches(event):  # type: ignore[no-untyped-call]
            return False

        parsed_command = self.get_matching_matrix_command(event)
        if not parsed_command:
            return False
        args_text = self.extract_command_args(parsed_command, full_message) or ""

        coords = await self._resolve_location_from_args(args_text)

        if coords is None:
            from mmrelay.meshtastic_utils import connect_meshtastic

            meshtastic_client = await asyncio.to_thread(connect_meshtastic)
            if meshtastic_client is None:
                self.logger.error(
                    "Meshtastic client unavailable; cannot determine mesh location."
                )
                coords = None
            else:
                coords = self._determine_mesh_location(meshtastic_client)

        if coords is None:
            await self.send_matrix_message(  # type: ignore[no-untyped-call]
                room.room_id,
                "Cannot determine location",
                formatted=False,
            )
            return True

        forecast = await asyncio.to_thread(
            self.generate_forecast,
            latitude=coords[0],
            longitude=coords[1],
            mode=parsed_command,
        )
        await self.send_matrix_message(room.room_id, forecast, formatted=False)  # type: ignore[no-untyped-call]
        return True

    async def _resolve_location_from_args(
        self, arg_text: str | None
    ) -> tuple[float, float] | None:
        """
        Resolve geographic coordinates from an argument string by parsing numeric overrides or falling back to geocoding.

        Parameters:
            arg_text (str | None): Free-form location text or a latitude/longitude override (e.g., "12.34, -56.78" or "City, Country").

        Returns:
            tuple[float, float] | None: (latitude, longitude) in decimal degrees if resolved, otherwise `None`.
        """
        if not arg_text:
            return None
        coords = self._parse_location_override(arg_text)
        if coords is not None:
            return coords
        return await asyncio.to_thread(self._geocode_location, arg_text)

    def _determine_mesh_location(
        self, meshtastic_client: Any
    ) -> tuple[float, float] | None:
        """
        Compute an approximate mesh location by averaging known node coordinates.

        Parameters:
            meshtastic_client: An object exposing a `nodes` mapping where each value may be a dict containing a `position` dict with numeric `latitude` and `longitude`.

        Returns:
            tuple[float, float] | None: A (latitude, longitude) pair representing the averaged position across available nodes, or `None` if no valid node coordinates are found. The longitude average correctly handles antimeridian wrapping.
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
        """
        Parse a message for a supported mesh command prefixed with '!'.

        This matches messages that optionally begin with whitespace, followed by '!' and one of the plugin's supported commands (case-insensitive), optionally followed by arguments. Leading/trailing whitespace around the returned args is removed.

        Parameters:
            message (str): The incoming message text to parse.

        Returns:
            tuple[str | None, str | None]: `(command, args)` where `command` is the matched command in lowercase and `args` is the trimmed argument string. Returns `(None, None)` if `message` is not a string or does not contain a supported command.
        """
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
        """
        Parse a latitude/longitude string in "lat,lon" or "lat lon" format into a (latitude, longitude) tuple.

        Parameters:
            arg_text (str): Text containing latitude and longitude separated by a comma or whitespace.

        Returns:
            tuple[float, float] | None: (latitude, longitude) if parsing succeeds and values are within valid ranges; otherwise None.
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
        Resolve a free-form location string to geographic coordinates using the Open-Meteo geocoding API.

        Queries the Open-Meteo geocoding endpoint and returns the first result's latitude and longitude as floats.

        Returns:
            tuple[float, float] | None: A (latitude, longitude) pair as floats if a result is found, `None` otherwise.
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
        except requests.exceptions.RequestException:
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
