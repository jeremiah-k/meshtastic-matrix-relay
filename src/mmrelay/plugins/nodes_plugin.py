import asyncio
from datetime import datetime
from typing import Any

# matrix-nio is not marked py.typed; keep import-untyped for strict mypy.
from nio import (
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

from mmrelay.plugins.base_plugin import BasePlugin


def get_relative_time(timestamp: float) -> str:
    """
    Convert a POSIX timestamp into a concise, human-readable relative time string.

    Parameters:
        timestamp (float): POSIX timestamp (seconds since the epoch) to compare with the current time.

    Returns:
        str: A relative time description:
                - "Just now" for times less than 60 seconds ago
                - "<N> minutes ago" for times between 60 seconds and 1 hour
                - "<N> hours ago" for times between 1 hour and 24 hours
                - "<N> days ago" for times between 1 day and 7 days
                - a formatted date "Mon DD, YYYY" for times older than 7 days
    """
    now = datetime.now()
    dt = datetime.fromtimestamp(timestamp)

    # Calculate the time difference between the current time and the given timestamp
    delta = now - dt

    # Extract the relevant components from the time difference
    days = delta.days
    seconds = delta.seconds

    # Convert the time difference into a relative timeframe
    if days > 7:
        return dt.strftime(
            "%b %d, %Y"
        )  # Return the timestamp in a specific format if it's older than 7 days
    elif days >= 1:
        return f"{days} days ago"
    elif seconds >= 3600:
        hours = seconds // 3600
        return f"{hours} hours ago"
    elif seconds >= 60:
        minutes = seconds // 60
        return f"{minutes} minutes ago"
    else:
        return "Just now"


class Plugin(BasePlugin):
    plugin_name = "nodes"
    is_core_plugin = True

    @property
    def description(self) -> str:
        """
        Provide the plugin description and the node-list line format.

        The returned string contains a human-readable description followed by an example node line format using these placeholders: $shortname, $longname, $devicemodel, $battery, $voltage, $snr, $hops, $lastseen.

        Returns:
            A multiline string with the plugin description and the node output format.
        """
        return """Show mesh radios and node data

$shortname $longname / $devicemodel / $battery $voltage / $snr / $hops / $lastseen
"""

    def generate_response(self) -> str:
        """
        Builds a textual summary of known Meshtastic nodes and their reported metrics.

        The returned string begins with "Nodes: <count>" and includes one line per node with short name, long name, hardware model, battery percentage, voltage, SNR (in dB) when available, hop distance, and last-heard relative time. If the Meshtastic device cannot be contacted, returns the error message "Unable to connect to Meshtastic device."

        Returns:
            response (str): The multi-line nodes summary or an error message when no Meshtastic client is available.
        """
        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client = connect_meshtastic()
        if meshtastic_client is None:
            return "Unable to connect to Meshtastic device."

        node_lines: list[str] = []
        valid_node_count = 0

        for _node, info in meshtastic_client.nodes.items():
            if not isinstance(info, dict):
                continue

            user = info.get("user")
            user_info = user if isinstance(user, dict) else {}
            short_name = user_info.get("shortName", "Unknown")
            long_name = user_info.get("longName", "Unknown")
            hw_model = user_info.get("hwModel", "Unknown")

            hops = "? hops away"
            hops_away = info.get("hopsAway")
            if hops_away is not None:
                if hops_away == 0:
                    hops = "direct"
                elif hops_away == 1:
                    hops = "1 hop away"
                else:
                    hops = f"{hops_away} hops away"

            snr = ""
            snr_value = info.get("snr")
            if snr_value is not None:
                snr = f"{snr_value} dB"

            last_heard = "?"
            last_heard_timestamp = info.get("lastHeard")
            if last_heard_timestamp is not None:
                try:
                    last_heard = get_relative_time(float(last_heard_timestamp))
                except (TypeError, ValueError, OverflowError, OSError):
                    last_heard = "?"

            voltage = "?V"
            battery = "?%"
            device_metrics = info.get("deviceMetrics")
            if isinstance(device_metrics, dict):
                voltage_value = device_metrics.get("voltage")
                if voltage_value is not None:
                    voltage = f"{voltage_value}V"
                battery_level = device_metrics.get("batteryLevel")
                if battery_level is not None:
                    battery = f"{battery_level}%"

            node_lines.append(
                f"{short_name} {long_name} / {hw_model} / "
                f"{battery} {voltage} / {snr} / {hops} / {last_heard}\n"
            )
            valid_node_count += 1

        response = f"Nodes: {valid_node_count}\n"
        return response + "".join(node_lines)

    async def handle_meshtastic_message(
        self, packet: Any, formatted_message: str, longname: str, meshnet_name: str
    ) -> bool:
        """
        Handle an incoming Meshtastic packet without processing it.

        Parameters:
            packet (Any): Raw Meshtastic packet data received from the mesh.
            formatted_message (str): Human-readable representation of the packet payload.
            longname (str): Full device name of the packet sender.
            meshnet_name (str): Name of the mesh network that the packet originated from.

        Returns:
            bool: `False` indicating the plugin did not handle the message.
        """
        # Preserve API surface; arguments are currently unused.
        _ = packet, formatted_message, longname, meshnet_name
        return False

    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
    ) -> bool:
        # Pass the event to matches()
        """
        Handle a Matrix room event and send the nodes summary when the event matches plugin criteria.

        Parameters:
            room (MatrixRoom): The Matrix room where the event occurred; used as the destination for the response.
            event (RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote): Incoming event evaluated to determine whether this plugin should handle it.
            full_message (str): The raw message text; present for signature compatibility and not used by this handler.

        Returns:
            bool: `True` if the event was handled and a response was sent, `False` otherwise.
        """
        if not self.matches(event):
            return False
        _ = full_message

        response = await asyncio.to_thread(self.generate_response)
        await self.send_matrix_message(
            room_id=room.room_id, message=response, formatted=False
        )

        return True
