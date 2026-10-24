"""Weather resolution depends on location context, not NodeDB membership."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from meshtastic import BROADCAST_NUM

from mmrelay.plugins.weather_plugin import Plugin


@pytest.fixture
def weather(monkeypatch):
    """Provide a real command resolver with native sends and network lookups replaced."""
    plugin = Plugin()
    plugin.config = {}
    plugin.logger = Mock()
    plugin.is_channel_enabled = Mock(return_value=True)
    plugin.get_response_delay = Mock(return_value=0)
    plugin.generate_forecast = Mock(return_value="Fixture forecast")
    plugin.generate_marine_forecast = Mock(return_value=None)
    plugin.send_message = Mock()
    client = SimpleNamespace(myInfo=SimpleNamespace(my_node_num=42), nodes={})
    monkeypatch.setattr("mmrelay.meshtastic_utils.connect_meshtastic", lambda: client)
    return plugin, client


def packet(text="!weather", *, direct=False):
    """Use the native numeric sender even before SDK fromId enrichment arrives."""
    return {
        "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": text},
        "from": 99,
        "to": 42 if direct else BROADCAST_NUM,
        "channel": 0,
        "id": 7,
    }


async def test_unknown_sender_can_supply_explicit_coordinates(weather):
    """Coordinates in the command work with an empty NodeDB."""
    plugin, _ = weather
    assert await plugin.handle_meshtastic_message(
        packet("!weather 40,-74"), "", "unknown", "mesh"
    )
    plugin.generate_forecast.assert_called_once_with(
        latitude=40.0, longitude=-74.0, mode="weather"
    )
    plugin.send_message.assert_called_once()
    assert plugin.send_message.call_args.kwargs["text"] == "Fixture forecast"


async def test_unknown_sender_receives_actionable_location_response(weather):
    """A recognized command cannot silently disappear when location is unavailable."""
    plugin, _ = weather
    assert await plugin.handle_meshtastic_message(
        packet(direct=True), "", "unknown", "mesh"
    )
    plugin.generate_forecast.assert_not_called()
    plugin.send_message.assert_called_once()
    args = plugin.send_message.call_args.kwargs
    assert "location" in args["text"].lower()
    assert "!weather" in args["text"]
    assert args["destination_id"] == "!00000063"
    assert args["reply_id"] == 7


async def test_unknown_sender_uses_existing_mesh_location_fallback(weather):
    """Membership in the SDK cache is not required for the configured mesh fallback."""
    plugin, client = weather
    client.nodes = {"!other": {"position": {"latitude": 10.0, "longitude": 20.0}}}
    assert await plugin.handle_meshtastic_message(packet(), "", "unknown", "mesh")
    plugin.generate_forecast.assert_called_once_with(
        latitude=10.0, longitude=20.0, mode="weather"
    )


async def test_direct_request_without_a_sender_is_not_broadcast(weather):
    """Malformed private input cannot fall through to the broadcast send default."""
    plugin, _ = weather
    value = packet("!weather 40,-74", direct=True)
    del value["from"]
    assert not await plugin.handle_meshtastic_message(value, "", "unknown", "mesh")
    plugin.send_message.assert_not_called()
