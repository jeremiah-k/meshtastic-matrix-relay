import asyncio
import statistics
from typing import TYPE_CHECKING, Any

# matrix-nio is not marked py.typed; keep import-untyped for strict mypy.
from nio import (  # type: ignore[import-untyped]
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

from mmrelay.plugins.base_plugin import BasePlugin

if TYPE_CHECKING:
    from meshtastic.mesh_interface import MeshInterface  # type: ignore[import-untyped]


class Plugin(BasePlugin):
    plugin_name = "health"
    is_core_plugin = True

    @property
    def description(self) -> str:
        """
        Return a brief human-readable description of the plugin's purpose.

        Returns:
            description (str): A short description indicating the plugin shows mesh health via average battery, SNR, and air utilization.
        """
        return "Show mesh health using avg battery, SNR, AirUtil"

    def generate_response(self) -> str:
        r"""
        Produce a concise multi-line health summary for the mesh using metrics reported by discovered Meshtastic nodes.

        The returned text reports total node count and, when available, average and median values for battery percentage, air utilization (tx), and SNR, plus a count of nodes with battery <= 10%.

        Returns:
            str: A multi-line human-readable summary. Typical content:
                - Nodes: total number of nodes
                - Battery: average% / median% (avg / median) or "Battery: N/A"
                - Nodes with Low Battery (<= 10): count (0 if no battery data)
                - Air Util: average / median (avg / median) or "Air Util: N/A"
                - SNR: average / median (avg / median) or "SNR: N/A"
            Special return values:
                - "Unable to connect to Meshtastic device." if a Meshtastic client cannot be obtained.
                - "No nodes discovered yet." if the client has no discovered nodes.
                - "Nodes: <count>\nNo nodes with health metrics found." if nodes exist but none report any tracked metrics.
        """
        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client: MeshInterface | None = connect_meshtastic()
        if meshtastic_client is None:
            return "Unable to connect to Meshtastic device."
        battery_levels = []
        air_util_tx = []
        snr = []

        if not meshtastic_client.nodes:
            return "No nodes discovered yet."

        for _node, info in meshtastic_client.nodes.items():
            if "deviceMetrics" in info:
                if "batteryLevel" in info["deviceMetrics"]:
                    battery_levels.append(info["deviceMetrics"]["batteryLevel"])
                if "airUtilTx" in info["deviceMetrics"]:
                    air_util_tx.append(info["deviceMetrics"]["airUtilTx"])
            if "snr" in info:
                snr.append(info["snr"])

        # filter out None values from metrics just in case
        battery_levels = [value for value in battery_levels if value is not None]
        air_util_tx = [value for value in air_util_tx if value is not None]
        snr = [value for value in snr if value is not None]

        # Check if any health metrics are available
        if not battery_levels and not air_util_tx and not snr:
            radios = len(meshtastic_client.nodes)
            return f"Nodes: {radios}\nNo nodes with health metrics found."

        low_battery = len([n for n in battery_levels if n <= 10])
        radios = len(meshtastic_client.nodes)
        avg_battery = statistics.mean(battery_levels) if battery_levels else 0
        mdn_battery = statistics.median(battery_levels) if battery_levels else 0
        avg_air = statistics.mean(air_util_tx) if air_util_tx else 0
        mdn_air = statistics.median(air_util_tx) if air_util_tx else 0
        avg_snr = statistics.mean(snr) if snr else 0
        mdn_snr = statistics.median(snr) if snr else 0

        # Format metrics conditionally
        if air_util_tx:
            air_util_line = f"Air Util: {avg_air:.2f} / {mdn_air:.2f} (avg / median)"
        else:
            air_util_line = "Air Util: N/A"

        if snr:
            snr_line = f"SNR: {avg_snr:.2f} / {mdn_snr:.2f} (avg / median)"
        else:
            snr_line = "SNR: N/A"

        # Format battery conditionally
        if battery_levels:
            battery_line = (
                f"Battery: {avg_battery:.1f}% / {mdn_battery:.1f}% (avg / median)"
            )
        else:
            battery_line = "Battery: N/A"
            low_battery = 0  # No low battery nodes if no battery data

        return f"""Nodes: {radios}
 {battery_line}
 Nodes with Low Battery (<= 10): {low_battery}
 {air_util_line}
 {snr_line}"""

    async def handle_meshtastic_message(
        self,
        packet: dict[str, Any],
        formatted_message: str,
        longname: str,
        meshnet_name: str,
    ) -> bool:
        """
        Indicates that this plugin does not handle incoming Meshtastic packets.

        Parameters:
            packet: The raw Meshtastic packet payload.
            formatted_message (str): Human-readable representation of the packet.
            longname (str): Display name of the sending node.
            meshnet_name (str): Name of the mesh network the packet originated from.

        Returns:
            bool: `False` since this plugin does not process Meshtastic messages.
        """
        # Keep parameter names for compatibility with keyword calls in tests.
        _ = packet, formatted_message, longname, meshnet_name
        return False

    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
    ) -> bool:
        """
        Handle a Matrix room message that matches this plugin and send a mesh health summary to the room.

        If the event matches this plugin, generate the mesh health summary off the event loop and send it to the room.

        Parameters:
            room (MatrixRoom): The room where the message was received.
            event (RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote):
                The Matrix message event used to determine whether the plugin should run.
            full_message (str): The full text of the received message.

        Returns:
            True if the message matched this plugin and was handled, False otherwise.
        """
        if not self.matches(event):
            return False
        _ = full_message

        response = await asyncio.to_thread(self.generate_response)
        await self.send_matrix_message(room.room_id, response, formatted=False)

        return True
