import asyncio
from datetime import datetime
from typing import Any

# matrix-nio is not marked py.typed; keep import-untyped for strict mypy.
from nio import (  # type: ignore[import-untyped]
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
        Provide the plugin description and the output format used when listing mesh nodes.

        Returns:
            A multiline string describing the plugin and the node line format. The string includes placeholders:
            `$shortname`, `$longname`, `$devicemodel`, `$battery`, `$voltage`, `$snr`, `$hops`, and `$lastseen`.
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

        response = f"Nodes: {len(meshtastic_client.nodes)}\n"

        for _node, info in meshtastic_client.nodes.items():
            hops = "? hops away"
            if "hopsAway" in info and info["hopsAway"] is not None:
                if info["hopsAway"] == 0:
                    hops = "direct"
                elif info["hopsAway"] == 1:
                    hops = "1 hop away"
                else:
                    hops = f"{info['hopsAway']} hops away"

            snr = ""
            if "snr" in info and info["snr"] is not None:
                snr = f"{info['snr']} dB "

            last_heard = None
            if "lastHeard" in info and info["lastHeard"] is not None:
                last_heard = get_relative_time(info["lastHeard"])

            voltage = "?V"
            battery = "?%"
            if "deviceMetrics" in info:
                if (
                    "voltage" in info["deviceMetrics"]
                    and info["deviceMetrics"]["voltage"] is not None
                ):
                    voltage = f"{info['deviceMetrics']['voltage']}V "
                if (
                    "batteryLevel" in info["deviceMetrics"]
                    and info["deviceMetrics"]["batteryLevel"] is not None
                ):
                    battery = f"{info['deviceMetrics']['batteryLevel']}% "

            response += f"{info['user']['shortName']} {info['user']['longName']} / {info['user']['hwModel']} / {battery} {voltage} / {snr} / {hops} / {last_heard}\n"

        return response

    async def handle_meshtastic_message(
        self, packet: Any, formatted_message: str, longname: str, meshnet_name: str
    ) -> bool:
        """
        Handle an incoming Meshtastic packet message; currently does not process or consume the message.

        Parameters:
            packet: Raw Meshtastic packet data received from the mesh.
            formatted_message (str): Human-readable representation of the packet payload.
            longname (str): Full device name of the packet sender.
            meshnet_name (str): Name of the mesh network that the packet originated from.

        Returns:
            `False` indicating that the plugin did not handle the message.
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
        Handle an incoming Matrix room event and send the nodes summary when the plugin matches.

        Parameters:
            room (Any): Matrix room object where the event occurred; used to send the response.
            event (Any): Incoming event evaluated by self.matches() to decide whether to handle the message.
            full_message (str): Raw message text (not used by this handler).

        Returns:
            bool: `True` if the event was handled and a response was sent, `False` otherwise.
        """
        if not self.matches(event):
            return False

        response = await asyncio.to_thread(self.generate_response)
        await self.send_matrix_message(
            room_id=room.room_id, message=response, formatted=False
        )

        return True
