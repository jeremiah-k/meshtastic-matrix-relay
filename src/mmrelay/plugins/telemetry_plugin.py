import io
import json
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
from PIL import Image

from mmrelay.plugins.base_plugin import BasePlugin


class Plugin(BasePlugin):
    plugin_name = "telemetry"
    is_core_plugin = True
    max_data_rows_per_node = 50

    def commands(self):
        """
        Return the list of supported telemetry metric command names.

        Returns:
            list[str]: Supported telemetry commands: "batteryLevel", "voltage", and "airUtilTx".
        """
        return ["batteryLevel", "voltage", "airUtilTx"]

    @property
    def description(self) -> str:
        """
        Short description of the plugin's visualization purpose.

        Returns:
            str: Description text "Graph of avg Mesh telemetry value for last 12 hours".
        """
        return "Graph of avg Mesh telemetry value for last 12 hours"

    def _generate_timeperiods(self, hours=12):
        # Calculate the start and end times
        """
        Generate a list of hourly datetime anchors spanning the past `hours` hours up to now.

        Parameters:
                hours (int): Number of hours to look back from the current time (default 12).

        Returns:
                hourly_intervals (list[datetime.datetime]): List of datetime objects at hourly intervals from (now - hours) up to and including the current time.
        """
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours)

        # Create a list of hourly intervals for the last 12 hours
        hourly_intervals = []
        current_time = start_time
        while current_time <= end_time:
            hourly_intervals.append(current_time)
            current_time += timedelta(hours=1)
        return hourly_intervals

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ) -> bool:
        # Support deviceMetrics only for now
        """
        Process an incoming Meshtastic packet and record deviceMetrics telemetry for the sender when present.

        If the packet contains `decoded.telemetry.deviceMetrics` and `decoded.portnum == "TELEMETRY_APP"`, extracts `time`, `batteryLevel`, `voltage`, and `airUtilTx` (each telemetry field defaults to 0 if missing) and appends a telemetry record to the sender's node data via `set_node_data`.

        Parameters:
            packet (dict): Meshtastic packet dictionary; expected to contain `decoded` with `portnum` and a `telemetry` object that includes `deviceMetrics`. Other parameters (formatted_message, longname, meshnet_name) are not inspected by this function.

        Returns:
            False
        """
        if (
            "decoded" in packet
            and "portnum" in packet["decoded"]
            and packet["decoded"]["portnum"] == "TELEMETRY_APP"
            and "telemetry" in packet["decoded"]
            and "deviceMetrics" in packet["decoded"]["telemetry"]
        ):
            telemetry_data = []
            data = self.get_node_data(meshtastic_id=packet["fromId"])
            if data:
                telemetry_data = data
            packet_data = packet["decoded"]["telemetry"]

            telemetry_data.append(
                {
                    "time": packet_data["time"],
                    "batteryLevel": (
                        packet_data["deviceMetrics"]["batteryLevel"]
                        if "batteryLevel" in packet_data["deviceMetrics"]
                        else 0
                    ),
                    "voltage": (
                        packet_data["deviceMetrics"]["voltage"]
                        if "voltage" in packet_data["deviceMetrics"]
                        else 0
                    ),
                    "airUtilTx": (
                        packet_data["deviceMetrics"]["airUtilTx"]
                        if "airUtilTx" in packet_data["deviceMetrics"]
                        else 0
                    ),
                }
            )
            self.set_node_data(meshtastic_id=packet["fromId"], node_data=telemetry_data)
            return False

        # Return False for non-telemetry packets
        return False

    def get_matrix_commands(self):
        """
        Return the telemetry command names supported for Matrix commands.

        Returns:
            list[str]: A list of supported command names: ["batteryLevel", "voltage", "airUtilTx"].
        """
        return ["batteryLevel", "voltage", "airUtilTx"]

    def get_mesh_commands(self):
        """
        List the supported mesh commands for this plugin.

        Returns:
            list: An empty list indicating no mesh commands are supported.
        """
        return []

    async def handle_room_message(self, room, event, full_message) -> bool:
        # Pass the event to matches()
        """
        Handle a Matrix room message requesting a telemetry graph and send the generated image to the originating room.

        Parses a telemetry command (`!batteryLevel`, `!voltage`, or `!airUtilTx`) optionally followed by a node identifier, computes hourly averages for the last 12 hours (per-node or network-wide), generates a line plot of those averages, and uploads the image to the room.

        Parameters:
            room: Matrix room object where the event originated; used to determine the destination room_id.
            event: Matrix event used to detect whether the message matches a supported telemetry command.
            full_message (str): Full plaintext message content used to parse the command and optional node identifier.

        Returns:
            `true` if the message matched a telemetry command and the graph was generated and sent (or a user-facing notification was sent when a requested node had no data); `false` otherwise.
        """
        if not self.matches(event):
            return False

        parsed_command = self.get_matching_matrix_command(event)
        if not parsed_command:
            return False

        args = self.extract_command_args(parsed_command, full_message) or ""
        telemetry_option = parsed_command
        node = args or None

        hourly_intervals = self._generate_timeperiods()
        from mmrelay.matrix_utils import connect_matrix

        matrix_client = await connect_matrix()
        if matrix_client is None:
            self.logger.warning(
                "Matrix client unavailable; skipping telemetry graph generation"
            )
            return False

        # Compute the hourly averages for each node
        hourly_averages = {}

        def calculate_averages(node_data_rows):
            for record in node_data_rows:
                record_time = datetime.fromtimestamp(
                    record["time"]
                )  # Replace with your timestamp field name
                telemetry_value = record[
                    telemetry_option
                ]  # Replace with your battery level field name
                for i in range(len(hourly_intervals) - 1):
                    if hourly_intervals[i] <= record_time < hourly_intervals[i + 1]:
                        if i not in hourly_averages:
                            hourly_averages[i] = []
                        hourly_averages[i].append(telemetry_value)
                        break

        if node:
            node_data_rows = self.get_node_data(node)
            if node_data_rows:
                calculate_averages(node_data_rows)
            else:
                await self.send_matrix_message(
                    room.room_id,
                    f"No telemetry data found for node '{node}'.",
                    formatted=False,
                )
                return True
        else:
            for node_data_json in self.get_data():
                node_data_rows = json.loads(node_data_json[0])
                calculate_averages(node_data_rows)

        # Compute the final hourly averages
        final_averages = {}
        for i, interval in enumerate(hourly_intervals[:-1]):
            if i in hourly_averages:
                final_averages[interval] = sum(hourly_averages[i]) / len(
                    hourly_averages[i]
                )
            else:
                final_averages[interval] = 0.0

        # Extract the hourly intervals and average values into separate lists
        hourly_intervals = list(final_averages.keys())
        average_values = list(final_averages.values())

        # Convert the hourly intervals to strings
        hourly_strings = [hour.strftime("%H") for hour in hourly_intervals]

        # Create the plot
        fig, ax = plt.subplots()
        ax.plot(hourly_strings, average_values)

        # Set the plot title and axis labels
        if node:
            title = f"{node} Hourly {telemetry_option} Averages"
        else:
            title = f"Network Hourly {telemetry_option} Averages"
        ax.set_title(title)
        ax.set_xlabel("Hour")
        ax.set_ylabel(f"{telemetry_option}")

        # Rotate the x-axis labels for readability
        plt.xticks(rotation=45)

        # Save the plot as a PIL image
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        img = Image.open(buf)
        pil_image = Image.frombytes(mode="RGBA", size=img.size, data=img.tobytes())

        from mmrelay.matrix_utils import ImageUploadError, send_image

        try:
            await send_image(matrix_client, room.room_id, pil_image, "graph.png")
        except ImageUploadError:
            self.logger.exception("Failed to send telemetry graph")
            await matrix_client.room_send(
                room_id=room.room_id,
                message_type="m.room.message",
                content={
                    "msgtype": "m.notice",
                    "body": "Failed to generate graph: Image upload failed.",
                },
            )
            return False
        return True
