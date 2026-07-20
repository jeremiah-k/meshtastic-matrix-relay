"""Tests for the Matrix-only structured traceroute plugin."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.plugins.traceroute_plugin import (
    DEFAULT_TRACEROUTE_HOP_LIMIT,
    TRACEROUTE_USAGE,
    Plugin,
    TraceRouteCommandError,
    _format_result,
    _parse_request,
    _resolve_destination,
    _TraceRequest,
)


def _hop(node_num: int, node_id: str, snr_db: float | None = None) -> SimpleNamespace:
    return SimpleNamespace(node_num=node_num, node_id=node_id, snr_db=snr_db)


def _result(*, with_return: bool = True) -> SimpleNamespace:
    route_back = (
        (_hop(3, "!00000003"), _hop(1, "!00000001", -4.25)) if with_return else None
    )
    return SimpleNamespace(
        route_towards=(
            _hop(1, "!00000001"),
            _hop(2, "!00000002", 5.5),
            _hop(3, "!00000003", None),
        ),
        route_back=route_back,
    )


def _plugin() -> Plugin:
    plugin = Plugin()
    plugin.config = {}
    plugin.send_matrix_message = AsyncMock()
    plugin.send_matrix_reaction = AsyncMock()
    plugin.logger = MagicMock()
    return plugin


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ("!12345678", _TraceRequest("!12345678", None)),
        ("Roof Node --hops 5", _TraceRequest("Roof Node", 5)),
        ('"Roof Node 2" -h 1', _TraceRequest("Roof Node 2", 1)),
    ],
)
def test_parse_request_accepts_ids_names_and_hop_overrides(args, expected):
    assert _parse_request(args) == expected


@pytest.mark.parametrize(
    "args",
    ["", "--hops 3", "!12345678 --hops", "!12345678 --hops 0", "!123 --bad"],
)
def test_parse_request_rejects_invalid_syntax(args):
    with pytest.raises(TraceRouteCommandError):
        _parse_request(args)


def test_resolve_destination_accepts_canonical_and_decimal_ids():
    client = SimpleNamespace(nodes={})
    assert _resolve_destination(client, "!ABCDEF12") == "!abcdef12"
    assert _resolve_destination(client, "305419896") == 0x12345678


def test_resolve_destination_matches_short_and_long_names():
    client = SimpleNamespace(
        nodes={
            "!12345678": {
                "num": 0x12345678,
                "user": {
                    "id": "!12345678",
                    "shortName": "ROOF",
                    "longName": "Rooftop Relay",
                },
            }
        }
    )
    assert _resolve_destination(client, "roof") == "!12345678"
    assert _resolve_destination(client, "Rooftop Relay") == "!12345678"


def test_resolve_destination_rejects_ambiguous_names():
    client = SimpleNamespace(
        nodes={
            "!00000001": {"user": {"shortName": "NODE"}},
            "!00000002": {"user": {"shortName": "node"}},
        }
    )
    with pytest.raises(TraceRouteCommandError, match="ambiguous") as exc_info:
        _resolve_destination(client, "node")
    assert "!00000001" in str(exc_info.value)
    assert "!00000002" in str(exc_info.value)


def test_resolve_destination_retries_live_nodedb_mutation():
    nodes = MagicMock()
    nodes.items.side_effect = [
        RuntimeError("dictionary changed size during iteration"),
        [("!12345678", {"user": {"shortName": "ROOF"}})],
    ]
    assert _resolve_destination(SimpleNamespace(nodes=nodes), "roof") == "!12345678"
    assert nodes.items.call_count == 2


def test_format_result_preserves_direction_and_unknown_snr():
    rendered = _format_result(
        _result(), destination="!00000003", hop_limit=3, channel_index=1
    )
    assert rendered.splitlines() == [
        "Traceroute to !00000003 (hop limit 3, channel 1)",
        "Outbound: !00000001 → !00000002 (5.5 dB) → !00000003 (SNR unknown)",
        "Return: !00000003 → !00000001 (-4.25 dB)",
    ]


def test_format_result_reports_missing_reverse_route():
    rendered = _format_result(
        _result(with_return=False),
        destination="!00000003",
        hop_limit=3,
        channel_index=0,
    )
    assert rendered.endswith("Return: not reported by firmware")


@patch("mmrelay.meshtastic_utils.connect_meshtastic")
def test_generate_response_calls_structured_mtjk_api(mock_connect):
    client = MagicMock()
    client.nodes = {}
    client.localNode.localConfig.lora.hop_limit = 4
    client.requestTraceRoute.return_value = _result()
    mock_connect.return_value = client
    plugin = _plugin()
    plugin.config = {"channel_index": 2}

    response = plugin.generate_response(_TraceRequest("!00000003", None))

    client.requestTraceRoute.assert_called_once_with(
        dest="!00000003", hopLimit=4, channelIndex=2
    )
    assert "hop limit 4, channel 2" in response


@patch("mmrelay.meshtastic_utils.connect_meshtastic")
def test_generate_response_uses_safe_hop_fallback(mock_connect):
    client = MagicMock()
    client.nodes = {}
    client.localNode.localConfig.lora.hop_limit = 0
    client.requestTraceRoute.return_value = _result()
    mock_connect.return_value = client
    plugin = _plugin()

    plugin.generate_response(_TraceRequest("!00000003", None))

    assert client.requestTraceRoute.call_args.kwargs["hopLimit"] == (
        DEFAULT_TRACEROUTE_HOP_LIMIT
    )


@patch("mmrelay.meshtastic_utils.connect_meshtastic")
def test_generate_response_surfaces_mtjk_request_failure(mock_connect):
    client = MagicMock()
    client.nodes = {}
    client.requestTraceRoute.side_effect = RuntimeError("Timed out waiting for route")
    mock_connect.return_value = client
    plugin = _plugin()

    with pytest.raises(TraceRouteCommandError, match="Timed out waiting for route"):
        plugin.generate_response(_TraceRequest("!00000003", 3))


@patch("mmrelay.meshtastic_utils.connect_meshtastic")
def test_generate_response_feature_detects_old_mtjk(mock_connect):
    mock_connect.return_value = SimpleNamespace(nodes={})
    plugin = _plugin()
    with pytest.raises(TraceRouteCommandError, match="companion mtjk"):
        plugin.generate_response(_TraceRequest("!00000003", 3))


@patch("mmrelay.meshtastic_utils.connect_meshtastic")
def test_generate_response_rejects_concurrent_traceroute(mock_connect):
    client = MagicMock()
    client.requestTraceRoute.return_value = _result()
    mock_connect.return_value = client
    plugin = _plugin()
    assert plugin._request_lock.acquire(blocking=False)
    try:
        with pytest.raises(TraceRouteCommandError, match="already in progress"):
            plugin.generate_response(_TraceRequest("!00000003", 3))
    finally:
        plugin._request_lock.release()


def test_plugin_exposes_full_and_short_matrix_commands():
    assert _plugin().get_matrix_commands() == ["traceroute", "trace"]


def test_plugin_is_matrix_only():
    plugin = _plugin()
    assert asyncio.run(plugin.handle_meshtastic_message({}, "", "", "")) is False


def test_handle_room_message_ignores_non_command():
    plugin = _plugin()
    plugin.get_matching_matrix_command_with_args = MagicMock(return_value=None)
    handled = asyncio.run(
        plugin.handle_room_message(MagicMock(), MagicMock(), "ordinary message")
    )
    assert handled is False
    plugin.send_matrix_message.assert_not_awaited()


def test_handle_room_message_sends_structured_result():
    plugin = _plugin()
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("trace", "!00000003 --hops 4")
    )
    plugin.generate_response = MagicMock(return_value="route result")
    room = MagicMock(room_id="!room:example.org")
    event = MagicMock(event_id="$event")

    handled = asyncio.run(plugin.handle_room_message(room, event, "!trace ..."))

    assert handled is True
    plugin.generate_response.assert_called_once_with(_TraceRequest("!00000003", 4))
    plugin.send_matrix_message.assert_awaited_once_with(
        room_id="!room:example.org", message="route result", formatted=False
    )
    plugin.send_matrix_reaction.assert_awaited_once_with(
        "!room:example.org", "$event", "✅"
    )


def test_handle_room_message_returns_usage_error():
    plugin = _plugin()
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("traceroute", "")
    )
    room = MagicMock(room_id="!room:example.org")
    event = MagicMock(event_id="$event")

    handled = asyncio.run(plugin.handle_room_message(room, event, "!traceroute"))

    assert handled is True
    plugin.send_matrix_message.assert_awaited_once_with(
        room_id="!room:example.org", message=TRACEROUTE_USAGE, formatted=False
    )
    plugin.send_matrix_reaction.assert_awaited_once_with(
        "!room:example.org", "$event", "❌"
    )


def test_handle_room_message_surfaces_expected_library_failure():
    plugin = _plugin()
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("traceroute", "!00000003")
    )
    plugin.generate_response = MagicMock(
        side_effect=TraceRouteCommandError("Meshtastic is not connected.")
    )
    room = MagicMock(room_id="!room:example.org")
    event = MagicMock(event_id="$event")

    handled = asyncio.run(plugin.handle_room_message(room, event, "!traceroute ..."))

    assert handled is True
    plugin.send_matrix_message.assert_awaited_once_with(
        room_id="!room:example.org",
        message="Meshtastic is not connected.",
        formatted=False,
    )
    plugin.send_matrix_reaction.assert_awaited_once_with(
        "!room:example.org", "$event", "❌"
    )
