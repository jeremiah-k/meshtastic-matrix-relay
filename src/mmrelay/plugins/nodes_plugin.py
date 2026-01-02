import asyncio
from datetime import datetime
from typing import Any

from mmrelay.plugins.base_plugin import BasePlugin


def get_relative_time(timestamp: float) -> str:
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
        return """Show mesh radios and node data

$shortname $longname / $devicemodel / $battery $voltage / $snr / $hops / $lastseen
"""

    def generate_response(self) -> str:
        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client = connect_meshtastic()  # type: ignore[no-untyped-call]
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
            meshnet_name (str): Name of the mesh network the packet originated from.

        Returns:
            `False` indicating the plugin did not handle the message.
        """
        return False

    async def handle_room_message(
        self, room: Any, event: Any, full_message: str
    ) -> bool:  # noqa: ARG002
        # Pass the event to matches()
        """
        Handle an incoming room message and respond with the nodes summary when the plugin matches the event.

        Parameters:
            room: The Matrix room object where the event occurred; used to send the response.
            event: The incoming event evaluated by self.matches() to decide whether to handle the message.
            full_message: The raw message text (not used by this handler).

        Returns:
            bool: `True` if the event was handled and a response was sent, `False` otherwise.
        """
        if not self.matches(event):  # type: ignore[no-untyped-call]
            return False

        response = await asyncio.to_thread(self.generate_response)
        await self.send_matrix_message(  # type: ignore[no-untyped-call]
            room_id=room.room_id, message=response, formatted=False
        )

        return True
