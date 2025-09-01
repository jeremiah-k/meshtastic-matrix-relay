import statistics

from mmrelay.plugins.base_plugin import BasePlugin


class Plugin(BasePlugin):
    plugin_name = "health"

    @property
    def description(self):
        return "Show mesh health using avg battery, SNR, AirUtil"

    def generate_response(self):
        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client = connect_meshtastic()
        battery_levels = []
        air_util_tx = []
        snr = []

        for _node, info in meshtastic_client.nodes.items():
            if "deviceMetrics" in info:
                if "batteryLevel" in info["deviceMetrics"]:
                    battery_levels.append(info["deviceMetrics"]["batteryLevel"])
                if "airUtilTx" in info["deviceMetrics"]:
                    air_util_tx.append(info["deviceMetrics"]["airUtilTx"])
            if "snr" in info:
                snr.append(info["snr"])
        print(str(snr))
        # filter out none type values from snr and air_util_tx just in case
        air_util_tx = [value for value in air_util_tx if value is not None]
        snr = [value for value in snr if value is not None]

        low_battery = len([n for n in battery_levels if n <= 10])
        radios = len(meshtastic_client.nodes)
        avg_battery = statistics.mean(battery_levels) if battery_levels else 0
        mdn_battery = statistics.median(battery_levels)
        avg_air = statistics.mean(air_util_tx) if air_util_tx else 0
        mdn_air = statistics.median(air_util_tx)
        avg_snr = statistics.mean(snr) if snr else 0
        mdn_snr = statistics.median(snr)

        return f"""Nodes: {radios}
Battery: {avg_battery:.1f}% / {mdn_battery:.1f}% (avg / median)
Nodes with Low Battery (< 10): {low_battery}
Air Util: {avg_air:.2f} / {mdn_air:.2f} (avg / median)
SNR: {avg_snr:.2f} / {mdn_snr:.2f} (avg / median)
"""

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ):
        return False

    async def handle_room_message(self, room, event, full_message):

        if not self.matches(event):
            return False

        await self.send_matrix_message(
            room.room_id, self.generate_response(), formatted=False
        )

        return True
