import io
import json
from datetime import datetime, timedelta
from typing import Any

import matplotlib.pyplot as plt

# matrix-nio is not marked py.typed; keep import-untyped for strict mypy.
from nio import (  # type: ignore[import-untyped]
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)
from PIL import Image

from mmrelay.plugins.base_plugin import BasePlugin


class Plugin(BasePlugin):
    plugin_name = "telemetry"
    is_core_plugin = True
    max_data_rows_per_node = 50

    def commands(self) -> list[str]:
        """
        List supported telemetry metric command names.

        Returns:
            list[str]: Supported telemetry command names: "batteryLevel", "voltage", and "airUtilTx".
        """
        return ["batteryLevel", "voltage", "airUtilTx"]

    @property
    def description(self) -> str:
        """
        Short description of the plugin's visualization purpose.

        Returns:
            str: The text "Graph of avg Mesh telemetry value for last 12 hours".
        """
        return "Graph of avg Mesh telemetry value for last 12 hours"

    def _generate_timeperiods(self, hours: int = 12) -> list[datetime]:
        """
        Generate hourly datetime anchors spanning the past `hours` hours up to the current time.

        Parameters:
            hours (int): Number of hours to look back from now (default 12).

        Returns:
            list[datetime]: Hourly datetime objects from (now - hours) up to and including now.
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
        self,
        packet: dict[str, Any],
        formatted_message: str,
        longname: str,
        meshnet_name: str,
    ) -> bool:
        # Support deviceMetrics only for now
        """
        Record device telemetry from an incoming Meshtastic telemetry packet for the sending node.
        
        When `packet` contains `decoded.portnum == "TELEMETRY_APP"` and `decoded.telemetry.deviceMetrics`, extracts the telemetry timestamp and the `batteryLevel`, `voltage`, and `airUtilTx` fields (each `None` if missing) and appends a telemetry record for the sender identified by `packet["fromId"]`. Other packet contents are not modified.
        
        Parameters:
            packet (dict): Meshtastic packet expected to include `decoded` with `portnum` and `telemetry.deviceMetrics`.
            formatted_message (str): Unused.
            longname (str): Unused.
            meshnet_name (str): Unused.
        
        Returns:
            bool: `False` always; telemetry is recorded but the message is not consumed by this handler.
        """
        _ = formatted_message, longname, meshnet_name
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
                telemetry_data = data if isinstance(data, list) else [data]
            packet_data = packet["decoded"]["telemetry"]
            device_metrics = packet_data["deviceMetrics"]

            telemetry_data.append(
                {
                    "time": packet_data["time"],
                    "batteryLevel": device_metrics.get("batteryLevel"),
                    "voltage": device_metrics.get("voltage"),
                    "airUtilTx": device_metrics.get("airUtilTx"),
                }
            )
            self.set_node_data(meshtastic_id=packet["fromId"], node_data=telemetry_data)
            return False

        # Return False for non-telemetry packets
        return False

    def get_matrix_commands(self) -> list[str]:
        """
        Telemetry command names supported for Matrix messages.

        Returns:
            list[str]: Supported telemetry command names: ["batteryLevel", "voltage", "airUtilTx"].
        """
        return self.commands()

    def get_mesh_commands(self) -> list[str]:
        """
        List supported mesh commands for this plugin.

        Returns:
            list[str]: An empty list indicating the plugin exposes no mesh commands.
        """
        return []

    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
    ) -> bool:
        # Pass the event to matches()
        """
        Handle a Matrix message requesting a telemetry graph and send the generated image to the originating room.

        Parses a telemetry command (one of `batteryLevel`, `voltage`, `airUtilTx`) optionally followed by a node identifier, computes hourly averages for the last 12 hours (for the specified node or network-wide), generates a line plot of those averages, and uploads the image to the originating room. If a requested node has no telemetry data, a user-facing notice is sent instead of an image.

        Parameters:
            room: Matrix room object where the event originated and where the response will be sent.
            event: Matrix event used to determine whether it matches a supported telemetry command.
            full_message (str): Full plaintext message content used to parse the command and optional node identifier.

        Returns:
            `True` if the message matched a telemetry command and either a graph was generated and sent or a notice was sent for a node with no data; `False` otherwise.
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
        hourly_averages: dict[int, list[float]] = {}

        def calculate_averages(node_data_rows: list[dict[str, Any]]) -> None:
            """
            Accumulate per-record telemetry values into hourly bins keyed by indices of the outer `hourly_intervals`.

            Parameters:
                node_data_rows (list[dict[str, Any]]): Records containing a "time" POSIX timestamp (seconds) and a telemetry value under the key named by the enclosing `telemetry_option`; values are appended to the outer `hourly_averages` dictionary for the matching hourly interval.
            """
            for record in node_data_rows:
                record_time = datetime.fromtimestamp(
                    record["time"]
                )  # Replace with your timestamp field name
                telemetry_value = record[
                    telemetry_option
                ]  # Replace with your battery level field name
                for i in range(len(hourly_intervals) - 1):
                    if hourly_intervals[i] <= record_time < hourly_intervals[i + 1]:
                        if telemetry_value is not None:
                            if i not in hourly_averages:
                                hourly_averages[i] = []
                            hourly_averages[i].append(telemetry_value)
                        break

        if node:
            node_data_rows = self.get_node_data(node)
            if node_data_rows:
                calculate_averages(
                    node_data_rows
                    if isinstance(node_data_rows, list)
                    else [node_data_rows]
                )
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