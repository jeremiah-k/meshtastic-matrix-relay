from datetime import datetime

from mmrelay.plugins.base_plugin import BasePlugin


def get_relative_time(timestamp):
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

    @property
    def description(self):
        return """Show mesh radios and node data

$shortname $longname / $devicemodel / $battery $voltage / $snr / $lastseen
"""

    def generate_response(self):
        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client = connect_meshtastic()

        response = f"Nodes: {len(meshtastic_client.nodes)}\n"

        for _node, info in meshtastic_client.nodes.items():
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

            response += f"{info['user']['shortName']} {info['user']['longName']} / {info['user']['hwModel']} / {battery} {voltage} / {snr} / {last_heard}\n"

        return response

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ):
        return False

    async def handle_room_message(self, room, event, full_message):
        # Pass the event to matches()
        if not self.matches(event):
            return False

        await self.send_matrix_message(
            room_id=room.room_id, message=self.generate_response(), formatted=False
        )

        return True
