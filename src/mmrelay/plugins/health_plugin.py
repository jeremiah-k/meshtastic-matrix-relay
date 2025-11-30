import asyncio
import statistics

from nio import MatrixRoom, RoomMessageText

from mmrelay.plugins.base_plugin import BasePlugin


class Plugin(BasePlugin):
    plugin_name = "health"
    is_core_plugin = True

    @property
    def description(self) -> str:
        return "Show mesh health using avg battery, SNR, AirUtil"

    def generate_response(self) -> str:
        """
        Compute and format mesh health metrics from connected Meshtastic nodes.

        Collects battery levels, air utilization (tx), and SNR from discovered nodes, computes node count, average and median values for each metric, and counts nodes with battery <= 10. If no nodes are discovered, returns the literal string "No nodes discovered yet."

        Returns:
            str: A multi-line formatted summary containing:
                - Nodes: total number of nodes
                - Battery: average and median battery percentage
                - Nodes with Low Battery (< 10): count of low-battery nodes
                - Air Util: average and median air utilization
                - SNR: average and median signal-to-noise ratio
        """
        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client = connect_meshtastic()
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

        low_battery = len([n for n in battery_levels if n <= 10])
        radios = len(meshtastic_client.nodes)
        avg_battery = statistics.mean(battery_levels) if battery_levels else 0
        mdn_battery = statistics.median(battery_levels) if battery_levels else 0
        avg_air = statistics.mean(air_util_tx) if air_util_tx else 0
        mdn_air = statistics.median(air_util_tx) if air_util_tx else 0
        avg_snr = statistics.mean(snr) if snr else 0
        mdn_snr = statistics.median(snr) if snr else 0

        return f"""Nodes: {radios}
Battery: {avg_battery:.1f}% / {mdn_battery:.1f}% (avg / median)
Nodes with Low Battery (< 10): {low_battery}
Air Util: {avg_air:.2f} / {mdn_air:.2f} (avg / median)
SNR: {avg_snr:.2f} / {mdn_snr:.2f} (avg / median)
"""

    async def handle_meshtastic_message(
        self, _packet, _formatted_message: str, _longname: str, _meshnet_name: str
    ) -> bool:
        return False

    async def handle_room_message(
        self, room: MatrixRoom, event: RoomMessageText, full_message: str
    ) -> bool:
        if not self.matches(event):
            return False

        response = await asyncio.get_running_loop().run_in_executor(
            None, self.generate_response
        )
        await self.send_matrix_message(room.room_id, response, formatted=False)

        return True
