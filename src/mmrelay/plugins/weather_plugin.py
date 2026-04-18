import asyncio
import math
import re
from datetime import datetime
from typing import Any

import requests
from meshtastic.mesh_interface import BROADCAST_NUM
from nio import (
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

from mmrelay.constants.formats import (
    CELSIUS_TO_FAHRENHEIT_MULTIPLIER,
    DEFAULT_CHANNEL,
    DEFAULT_TEXT_ENCODING,
    DEGREE_SYMBOL,
    ENCODING_ERROR_IGNORE,
    FAHRENHEIT_OFFSET,
    KM_TO_MILES_FACTOR,
    LATITUDE_MAX,
    LATITUDE_MIN,
    LONGITUDE_MAX,
    LONGITUDE_MIN,
    TEXT_MESSAGE_APP,
)
from mmrelay.constants.messages import PORTNUM_TEXT_MESSAGE_APP
from mmrelay.constants.plugins import (
    DAILY_FORECAST_DAYS,
    GEOCODING_RESULT_COUNT,
    HOURLY_CONFIG,
    HOURLY_FORECAST_DAYS,
    MAX_FORECAST_LENGTH,
    OPEN_METEO_CURRENT_WEATHER_FLAG,
    OPEN_METEO_DAILY_FIELDS,
    OPEN_METEO_FORECAST_API_URL,
    OPEN_METEO_GEOCODING_API_URL,
    OPEN_METEO_HOURLY_FIELDS,
    OPEN_METEO_TIMEZONE_AUTO,
    WEATHER_API_TIMEOUT_SECONDS,
    WEATHER_CODE_TEXT_MAPPING,
    WEATHER_COMMANDS,
    WEATHER_MODE_CURRENT,
    WEATHER_MODE_DAILY,
    WEATHER_SLOT_NOW,
    WEATHER_UNITS_IMPERIAL,
    WEATHER_UNITS_METRIC,
)
from mmrelay.plugins.base_plugin import BasePlugin

CANONICAL_WEATHER_MODE = WEATHER_MODE_CURRENT


class Plugin(BasePlugin):
    plugin_name = "weather"
    is_core_plugin = True
    mesh_commands = WEATHER_COMMANDS

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
        Normalize a command string to a supported forecast mode.

        Returns:
            str: A valid mode from WEATHER_COMMANDS. Unrecognized or empty inputs yield the default.
        """
        cmd = (mode or CANONICAL_WEATHER_MODE).lower()
        if cmd in WEATHER_COMMANDS:
            return cmd
        return CANONICAL_WEATHER_MODE

    def generate_marine_forecast(
        self,
        latitude: float,
        longitude: float,
        mode: str = WEATHER_MODE_CURRENT,
        units: str = "metric",
    ) -> str | None:
        """
        Fetch marine weather data (wave height, period, and direction) for given coordinates.

        Queries the Open-Meteo marine weather API.  The query parameters mirror the
        terrestrial forecast mode so that marine data is always temporally consistent
        with the rest of the response:

        - ``current`` → ``current`` endpoint (instantaneous conditions).
        - ``hourly`` → ``hourly`` endpoint, sliced at the same +3h/+6h/+12h offsets
          derived from ``HOURLY_CONFIG`` and anchored to the current hour via
          ``datetime.now()``.
        - ``daily`` → ``daily`` endpoint (dominant direction and max height/period per day).

        The API returns no data for land coordinates, so a ``None`` return value
        naturally indicates that the location is not at sea (or that data is unavailable).

        Parameters:
            latitude (float): Latitude in decimal degrees.
            longitude (float): Longitude in decimal degrees.
            mode (str): Forecast mode — ``WEATHER_MODE_DAILY`` uses the daily endpoint,
                        ``WEATHER_MODE_CURRENT`` uses the current-conditions endpoint,
                        and any other value (e.g. ``hourly``) uses the hourly endpoint.
            units (str): Unit system — ``"imperial"`` converts wave heights to feet,
                         ``"metric"`` (default) keeps metres.

        Returns:
            str | None: Formatted marine forecast string when wave data is available,
                        or ``None`` if the coordinates are on land, the API returns no
                        data, or the request fails.

                        Current example (metric):
                            ``"🌊 Sea State: Waves 1.5m (8.0s) 185°"``
                        Current example (imperial):
                            ``"🌊 Sea State: Waves 4.9ft (8.0s) 185°"``
                        Hourly example:
                            ``"🌊 Waves: Now 1.5m (8.0s) 185° | +3h 1.8m (8.2s) 190°"``
                        Daily example:
                            ``"🌊 Waves: Mon 1.5m (8.0s) 185° | Tue …"``
        """
        try:
            mode_key = self._normalize_mode(mode)
            if mode_key == WEATHER_MODE_DAILY:
                url = (
                    f"https://marine-api.open-meteo.com/v1/marine?"
                    f"latitude={latitude}&longitude={longitude}&"
                    f"daily=wave_height_max,wave_direction_dominant,wave_period_max&"
                    f"timezone=auto&length_unit={units}"
                )
            elif mode_key == WEATHER_MODE_CURRENT:
                url = (
                    f"https://marine-api.open-meteo.com/v1/marine?"
                    f"latitude={latitude}&longitude={longitude}&"
                    f"current=wave_height,wave_direction,wave_period&"
                    f"timezone=auto&length_unit={units}"
                )
            else:
                url = (
                    f"https://marine-api.open-meteo.com/v1/marine?"
                    f"latitude={latitude}&longitude={longitude}&"
                    f"hourly=wave_height,wave_direction,wave_period&"
                    f"current=time&"
                    f"timezone=auto&length_unit={units}"
                )

            response = requests.get(url, timeout=WEATHER_API_TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()

            if mode_key == WEATHER_MODE_DAILY:
                return self._format_daily_marine(data, units)
            if mode_key == WEATHER_MODE_CURRENT:
                return self._format_current_marine(data, units)

            # Hourly mode: anchor to the current hour using the API's own current time
            # (in the location's timezone via timezone=auto) rather than the server's
            # local time, which may differ from the queried location.
            current_time_str = data.get("current", {}).get("time")
            base_index = 0
            if current_time_str:
                try:
                    current_time = datetime.fromisoformat(
                        current_time_str.replace("Z", "+00:00")
                    )
                    base_key = current_time.replace(
                        minute=0, second=0, microsecond=0
                    ).strftime("%Y-%m-%dT%H:00")
                    hourly_times = data.get("hourly", {}).get("time", [])
                    if hourly_times:
                        base_index = hourly_times.index(base_key)
                except (ValueError, AttributeError):
                    base_index = datetime.now().hour

            mode_offsets = HOURLY_CONFIG.get(mode_key, HOURLY_CONFIG[WEATHER_MODE_CURRENT])
            offsets = [o for o in mode_offsets.get("offsets", ()) if isinstance(o, int)]

            return self._format_hourly_marine(data, base_index, offsets, units)

        except requests.exceptions.RequestException:
            self.logger.debug(
                "Error fetching marine weather data for coordinates %f, %f",
                latitude,
                longitude,
            )
            return None
        except Exception:
            self.logger.exception(
                "Unexpected error processing marine weather data for coordinates %f, %f",
                latitude,
                longitude,
            )
            return None

    def _format_current_marine(self, data: dict, units: str = "metric") -> str | None:
        """Format current marine API response into a single-line string."""
        current = data.get("current", {})
        wave_height = current.get("wave_height")
        wave_dir = current.get("wave_direction")
        wave_period = current.get("wave_period")

        if wave_height is None:
            return None

        height_unit = "ft" if units == WEATHER_UNITS_IMPERIAL else "m"
        parts = [f"🌊 Sea State: Waves {round(wave_height, 1)}{height_unit}"]
        if wave_period is not None:
            parts[0] += f" ({round(wave_period, 1)}s)"
        if wave_dir is not None:
            parts.append(f"{round(wave_dir)}{DEGREE_SYMBOL}")

        return " ".join(parts)

    def _format_daily_marine(self, data: dict, units: str = "metric") -> str | None:
        """Format daily marine API response into a multi-day single-line string."""
        daily = data.get("daily", {})
        heights = daily.get("wave_height_max") or []
        directions = daily.get("wave_direction_dominant") or []
        periods = daily.get("wave_period_max") or []
        times = daily.get("time") or []

        if not heights:
            return None

        height_unit = "ft" if units == WEATHER_UNITS_IMPERIAL else "m"
        day_parts = []
        for i, height in enumerate(heights):
            if height is None:
                continue
            try:
                label = datetime.fromisoformat(times[i]).strftime("%a")
            except (IndexError, ValueError):
                label = f"D{i}"

            part = f"{label} {round(height, 1)}{height_unit}"
            period = periods[i] if i < len(periods) else None
            direction = directions[i] if i < len(directions) else None
            if period is not None:
                part += f" ({round(period, 1)}s)"
            if direction is not None:
                part += f" {round(direction)}{DEGREE_SYMBOL}"
            day_parts.append(part)

        if not day_parts:
            return None

        return self._trim_to_max_bytes("🌊 Waves: " + " | ".join(day_parts))

    def _format_hourly_marine(
        self, data: dict, base_index: int, offsets: list[int], units: str = "metric"
    ) -> str | None:
        """Format hourly marine API response into a multi-slot single-line string."""
        hourly = data.get("hourly", {})
        heights = hourly.get("wave_height") or []
        directions = hourly.get("wave_direction") or []
        periods = hourly.get("wave_period") or []

        if not heights:
            return None

        now_height = heights[base_index] if base_index < len(heights) else None
        if now_height is None:
            return None

        height_unit = "ft" if units == WEATHER_UNITS_IMPERIAL else "m"

        def _fmt_slot(label: str, idx: int) -> str | None:
            h = heights[idx] if idx < len(heights) else None
            if h is None:
                return None
            part = f"{label} {round(h, 1)}{height_unit}"
            p = periods[idx] if idx < len(periods) else None
            d = directions[idx] if idx < len(directions) else None
            if p is not None:
                part += f" ({round(p, 1)}s)"
            if d is not None:
                part += f" {round(d)}{DEGREE_SYMBOL}"
            return part

        slot_parts = [s for s in [
            _fmt_slot("Now", base_index),
            *[_fmt_slot(f"+{o}h", min(base_index + o, len(heights) - 1)) for o in offsets],
        ] if s is not None]

        if not slot_parts:
            return None

        return self._trim_to_max_bytes("🌊 Waves: " + " | ".join(slot_parts))

    def generate_forecast(
        self, latitude: float, longitude: float, mode: str = CANONICAL_WEATHER_MODE
    ) -> str:
        """
        Generate a concise one-line terrestrial weather forecast for the given GPS coordinates and mode.

        Parameters:
            latitude (float): Latitude in decimal degrees.
            longitude (float): Longitude in decimal degrees.
            mode (str): One of "weather", "hourly", or "daily" specifying the forecast format.

        Returns:
            str: A single-line forecast string on success. On recoverable failures returns one of:
                 - "Weather data temporarily unavailable." (missing hourly data),
                 - "Error fetching weather data." (network or request errors),
                 - "Error parsing weather data." (malformed or unexpected API response).
        """
        mode_key = self._normalize_mode(mode)

        raw_units = self.config.get("units", WEATHER_UNITS_METRIC)
        units = (
            raw_units.strip().lower()
            if isinstance(raw_units, str)
            else WEATHER_UNITS_METRIC
        )
        if units not in {WEATHER_UNITS_METRIC, WEATHER_UNITS_IMPERIAL}:
            units = WEATHER_UNITS_METRIC
        temperature_unit = "°C" if units == WEATHER_UNITS_METRIC else "°F"
        daily_days = (
            DAILY_FORECAST_DAYS
            if mode_key == WEATHER_MODE_DAILY
            else HOURLY_FORECAST_DAYS
        )

        mode_offsets = HOURLY_CONFIG.get(mode_key, HOURLY_CONFIG[WEATHER_MODE_CURRENT])
        offsets = [
            offset
            for offset in mode_offsets.get("offsets", ())
            if isinstance(offset, int)
        ]

        hourly_fields = ",".join(OPEN_METEO_HOURLY_FIELDS)
        daily_fields = ",".join(OPEN_METEO_DAILY_FIELDS)
        url = (
            f"{OPEN_METEO_FORECAST_API_URL}?"
            f"latitude={latitude}&longitude={longitude}&"
            f"hourly={hourly_fields}&"
            f"daily={daily_fields}&"
            f"forecast_days={daily_days}&{OPEN_METEO_TIMEZONE_AUTO}&{OPEN_METEO_CURRENT_WEATHER_FLAG}"
        )

        try:
            response = requests.get(url, timeout=WEATHER_API_TIMEOUT_SECONDS)
            response.raise_for_status()
        except requests.exceptions.RequestException:
            self.logger.exception("Error fetching weather data")
            return "Error fetching weather data."

        try:
            data = response.json()

            # Daily fast-path - check before parsing current/hourly data
            if mode_key == WEATHER_MODE_DAILY:
                terrestrial_forecast = self._build_daily_forecast(
                    data, units, temperature_unit, daily_days
                )
                return terrestrial_forecast
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
                    "Unexpected current_weather.time '%s': %s. Defaulting to hour=0.",
                    current_time_str,
                    ex,
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
            index_map[WEATHER_SLOT_NOW] = min(base_index, max_index)

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
                Return the hourly weather values at the specified index from the parsed weather data arrays.

                Parameters:
                    idx (int): Zero-based index into the hourly arrays.

                Returns:
                    tuple: (temperature, precipitation, weather_code, is_day, humidity, wind_speed, wind_direction)
                    Each element is the value at `idx`, or `None` if that entry is unavailable.
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

            if units == WEATHER_UNITS_IMPERIAL:
                if current_temp is not None:
                    current_temp = (
                        current_temp * CELSIUS_TO_FAHRENHEIT_MULTIPLIER
                        + FAHRENHEIT_OFFSET
                    )
                imperial_forecasts = {}
                for key, values in forecast_hours.items():
                    t, p, w, dflag, *rest = values
                    if t is not None:
                        t = t * CELSIUS_TO_FAHRENHEIT_MULTIPLIER + FAHRENHEIT_OFFSET
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

            if mode_key == WEATHER_MODE_CURRENT:
                now_slot = forecast_hours.get(WEATHER_SLOT_NOW)
                humidity = now_slot[4] if now_slot else None
                wind_speed = now_slot[5] if now_slot else None
                wind_dir = now_slot[6] if now_slot else None
                precip_now = now_slot[1] if now_slot else None
                wind_unit = "km/h"
                if units == WEATHER_UNITS_IMPERIAL and wind_speed is not None:
                    wind_speed = wind_speed * KM_TO_MILES_FACTOR
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
                        wind_part += f" {round(wind_dir)}{DEGREE_SYMBOL}"
                    parts.append(wind_part)
                if precip_now is not None:
                    parts.append(f"Precip {precip_now}%")

                terrestrial_forecast = self._trim_to_max_bytes(" | ".join(parts))
                return terrestrial_forecast

            slots = [
                slot
                for slot in HOURLY_CONFIG.get(
                    mode_key, HOURLY_CONFIG[WEATHER_MODE_CURRENT]
                ).get("slots", ())
                if isinstance(slot, str)
            ]
            terrestrial_forecast = self._build_hourly_forecast(
                current_temp,
                current_weather_code,
                is_day,
                forecast_hours,
                temperature_unit,
                slots,
            )

            return terrestrial_forecast

        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            self.logger.exception("Malformed weather data")
            return "Error parsing weather data."

    def _build_daily_forecast(
        self,
        data: dict[str, Any],
        units: str,
        temperature_unit: str,
        daily_days: int,
    ) -> str:
        """
        Create a concise multi-day weather summary string for display.

        Parameters:
            data (dict): Parsed Open-Meteo response containing a "daily" mapping with keys `weathercode`, `temperature_2m_max`, `temperature_2m_min`, and `time`.
            units (str): Unit system from configuration; when `"imperial"`, temperatures are converted from Celsius to Fahrenheit.
            temperature_unit (str): Temperature unit symbol to append to values (e.g., "°C" or "°F").
            daily_days (int): Maximum number of days to include in the summary.

        Returns:
            str: A pipe-separated segment for each day (e.g., "Mon: ☀️ 20.0°C/10.0°C | Tue: …"), or "Weather data temporarily unavailable." if no valid daily entries; output is truncated to the plugin's maximum allowed length.
        """
        daily_codes = data.get("daily", {}).get("weathercode") or []
        daily_max = data.get("daily", {}).get("temperature_2m_max") or []
        daily_min = data.get("daily", {}).get("temperature_2m_min") or []
        daily_times = data.get("daily", {}).get("time") or []
        if units == WEATHER_UNITS_IMPERIAL:
            daily_max = [
                (
                    t * CELSIUS_TO_FAHRENHEIT_MULTIPLIER + FAHRENHEIT_OFFSET
                    if t is not None
                    else None
                )
                for t in daily_max
            ]
            daily_min = [
                (
                    t * CELSIUS_TO_FAHRENHEIT_MULTIPLIER + FAHRENHEIT_OFFSET
                    if t is not None
                    else None
                )
                for t in daily_min
            ]
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
        encoded = text.encode(DEFAULT_TEXT_ENCODING)
        if len(encoded) <= MAX_FORECAST_LENGTH:
            return text
        # Truncate byte string and decode, ignoring partial trailing characters.
        return encoded[:MAX_FORECAST_LENGTH].decode(
            DEFAULT_TEXT_ENCODING, ENCODING_ERROR_IGNORE
        )

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
        # Format: "DAY:text|NIGHT:text", "BOTH:text", or plain text
        text = WEATHER_CODE_TEXT_MAPPING.get(weather_code)
        if text is None:
            return "❓ Unknown"
        if text.startswith("DAY:"):
            if is_day:
                return text[4:].split("|")[0]
            # Night: extract text after "|NIGHT:" or fall back to day text
            parts = text.split("|NIGHT:")
            return parts[1] if len(parts) > 1 else parts[0][4:]
        elif text.startswith("BOTH:"):
            return text[5:]
        return text

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
        # Keep parameter names for compatibility with keyword calls in tests.
        _ = formatted_message, meshnet_name
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

        channel = packet.get(
            "channel", DEFAULT_CHANNEL
        )  # Default to channel 0 if not provided

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
        if not self.is_channel_enabled(channel, is_direct_message=is_direct_message):
            # Channel not enabled for plugin
            return False

        # Log that the plugin is processing the message
        self.logger.info(
            "Processing message from %s on channel %s with plugin '%s'",
            longname,
            channel,
            self.plugin_name,
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
        mode = parsed_command
        reply_id = packet.get("id")
        if coords:
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
            self.send_message(
                text=weather_notice,
                destination_id=fromId,
                reply_id=reply_id,
            )
        else:
            # Respond in the same channel (broadcast)
            self.send_message(
                text=weather_notice,
                channel=channel,
                reply_id=reply_id,
            )

        # Fetch marine data and send as a separate message (Meshtastic has ~200 byte limit)
        if coords:
            raw_units = self.config.get("units", WEATHER_UNITS_METRIC)
            units = (
                raw_units.strip().lower()
                if isinstance(raw_units, str)
                else WEATHER_UNITS_METRIC
            )
            if units not in {WEATHER_UNITS_METRIC, WEATHER_UNITS_IMPERIAL}:
                units = WEATHER_UNITS_METRIC
            marine = await asyncio.to_thread(
                self.generate_marine_forecast,
                coords[0],
                coords[1],
                mode,
                units,
            )
            if marine:
                await asyncio.sleep(self.get_response_delay())
                if is_direct_message:
                    self.send_message(
                        text=marine,
                        destination_id=fromId,
                        reply_id=reply_id,
                    )
                else:
                    self.send_message(
                        text=marine,
                        channel=channel,
                        reply_id=reply_id,
                    )

        return True

    def get_matrix_commands(self) -> list[str]:
        """
        List mesh commands exposed to Matrix integrations.

        Returns:
            list[str]: A copy of the plugin's mesh command strings.
        """
        return list(self.mesh_commands)

    def get_mesh_commands(self) -> list[str]:
        """
        List available mesh commands exposed by this plugin.

        Returns:
            A copy of the plugin's mesh command names.
        """
        return list(self.mesh_commands)

    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
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
        if not self.matches(event):
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
            await self.send_matrix_message(
                room.room_id,
                "Cannot determine location",
                formatted=False,
                reply_to_event_id=event.event_id,
            )
            return True

        terrestrial = await asyncio.to_thread(
            self.generate_forecast,
            latitude=coords[0],
            longitude=coords[1],
            mode=parsed_command,
        )
        raw_units = self.config.get("units", WEATHER_UNITS_METRIC)
        units = (
            raw_units.strip().lower()
            if isinstance(raw_units, str)
            else WEATHER_UNITS_METRIC
        )
        if units not in {WEATHER_UNITS_METRIC, WEATHER_UNITS_IMPERIAL}:
            units = WEATHER_UNITS_METRIC
        marine = await asyncio.to_thread(
            self.generate_marine_forecast,
            coords[0],
            coords[1],
            parsed_command,
            units,
        )
        if marine:
            full_forecast = f"{terrestrial} | {marine}"
        else:
            full_forecast = terrestrial
        await self.send_matrix_message(room.room_id, full_forecast, formatted=False, reply_to_event_id=event.event_id)
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
        Compute an approximate mesh location by averaging coordinates of nodes with valid positions.

        Only nodes whose `position` contains numeric `latitude` and `longitude` are considered. Longitudes are averaged on the unit circle to correctly handle antimeridian wrapping.

        Parameters:
            meshtastic_client: Object exposing a `nodes` mapping whose values may include a `position` dict with numeric `latitude` and `longitude`.

        Returns:
            A (latitude, longitude) tuple representing the averaged position across available nodes, or None if no valid node coordinates are found.
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
        parsed = self.parse_mesh_bang_command(message, self.mesh_commands)
        if parsed is None:
            return None, None
        cmd, args = parsed
        return cmd.lower(), args

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
        if not (
            LATITUDE_MIN <= lat <= LATITUDE_MAX
            and LONGITUDE_MIN <= lon <= LONGITUDE_MAX
        ):
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

        url = OPEN_METEO_GEOCODING_API_URL
        try:
            response = requests.get(
                url,
                params={
                    "name": query,
                    "count": GEOCODING_RESULT_COUNT,
                    "format": "json",
                },
                timeout=WEATHER_API_TIMEOUT_SECONDS,
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
