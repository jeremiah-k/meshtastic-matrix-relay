from __future__ import annotations

import base64
import json
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mmrelay.plugins.mesh_beacon_plugin import (
    MeshBeaconCapabilityError,
    Plugin,
    _BeaconRecord,
    _markdown_text,
)


class _EnumValue:
    def __init__(self, name: str) -> None:
        self.name = name


class _EnumDescriptor:
    def __init__(self, values: dict[int, str]) -> None:
        self.values_by_number = {
            number: _EnumValue(name) for number, name in values.items()
        }


class _FieldDescriptor:
    def __init__(self, values: dict[int, str]) -> None:
        self.enum_type = _EnumDescriptor(values)


class _ModuleSettings:
    def __init__(self, position_precision: int = 0) -> None:
        self.position_precision = position_precision


class _ChannelSettings:
    def __init__(self, name: str = "LongFast", psk: bytes = b"secret") -> None:
        self.name = name
        self.psk = psk
        self.module_settings = _ModuleSettings(12)


class _FakeBeacon:
    DESCRIPTOR = SimpleNamespace(
        fields_by_name={
            "offer_region": _FieldDescriptor({0: "UNSET", 1: "US"}),
            "offer_preset": _FieldDescriptor({0: "LONG_FAST", 16: "MEDIUM_TURBO"}),
        }
    )

    def __init__(
        self,
        *,
        message: str = "Join us",
        name: str = "LongFast",
        psk: bytes = b"secret",
        region: int = 1,
        preset: int | None = 0,
        frequency_slot: int | None = None,
        has_offer: bool = True,
    ) -> None:
        self.message = message
        self.offer_channel = _ChannelSettings(name, psk)
        self.offer_region = region
        self.offer_preset = preset or 0
        self.offer_frequency_slot = frequency_slot or 0
        self._preset_present = preset is not None
        self._frequency_present = frequency_slot is not None
        self._has_offer = has_offer

    def HasField(self, name: str) -> bool:
        if name == "offer_channel":
            return self._has_offer
        if name == "offer_preset":
            return self._preset_present
        if name == "offer_frequency_slot":
            return self._frequency_present
        if name == "module_settings":
            return True
        raise ValueError(name)

    def SerializeToString(self) -> bytes:
        return json.dumps(
            {
                "message": self.message,
                "name": self.offer_channel.name,
                "psk": base64.b64encode(self.offer_channel.psk).decode(),
                "region": self.offer_region,
                "preset": self.offer_preset if self._preset_present else None,
                "frequency_slot": (
                    self.offer_frequency_slot if self._frequency_present else None
                ),
                "has_offer": self._has_offer,
            },
            sort_keys=True,
        ).encode()

    @classmethod
    def FromString(cls, payload: bytes) -> _FakeBeacon:
        value = json.loads(payload)
        return cls(
            message=value["message"],
            name=value["name"],
            psk=base64.b64decode(value["psk"]),
            region=value["region"],
            preset=value["preset"],
            frequency_slot=value["frequency_slot"],
            has_offer=value["has_offer"],
        )


class _ModuleConfig:
    DESCRIPTOR = SimpleNamespace(fields_by_name={"mesh_beacon": object()})

    def __init__(self, flags: int = 0, supported: bool = True) -> None:
        self.mesh_beacon = SimpleNamespace(flags=flags)
        self.supported = supported

    def HasField(self, name: str) -> bool:
        if name != "mesh_beacon":
            raise ValueError(name)
        return self.supported


class _Interface:
    def __init__(self, flags: int = 0, supported: bool = True) -> None:
        self.localNode = SimpleNamespace(
            moduleConfig=_ModuleConfig(flags, supported),
            localConfig=SimpleNamespace(
                lora=SimpleNamespace(use_preset=True, region=1, modem_preset=0)
            ),
            writeConfig=MagicMock(),
        )


def _install_fake_beacon_proto(monkeypatch: pytest.MonkeyPatch) -> None:
    module = SimpleNamespace(MeshBeacon=_FakeBeacon)
    monkeypatch.setitem(sys.modules, "meshtastic.protobuf.mesh_beacon_pb2", module)
    sys.modules["meshtastic.protobuf"].mesh_beacon_pb2 = module
    sys.modules["meshtastic.protobuf.portnums_pb2"].PortNum.MESH_BEACON_APP = 37


def _plugin(**config: Any) -> Plugin:
    plugin = Plugin()
    plugin.config = {"active": True, **config}
    plugin.logger = MagicMock()
    plugin.set_node_data = MagicMock()
    plugin.get_node_data = MagicMock(return_value=[])
    plugin.get_my_node_id = MagicMock(return_value=999)
    return plugin


def _packet(
    beacon: _FakeBeacon, *, sender: int = 123, channel: int = 0
) -> dict[str, Any]:
    return {
        "from": sender,
        "fromId": "!0000007b",
        "channel": channel,
        "rxRssi": -88,
        "rxSnr": 6.25,
        "decoded": {
            "portnum": 37,
            "meshbeacon": {"raw": beacon},
        },
    }


def _record(beacon: _FakeBeacon | None = None, *, channel: int = 0) -> _BeaconRecord:
    beacon = beacon or _FakeBeacon()
    return _BeaconRecord(
        sender_key="123",
        sender="Some Node",
        payload_b64=base64.b64encode(beacon.SerializeToString()).decode(),
        source_channel=channel,
        first_seen=100,
        last_seen=200,
        rssi=-88,
        snr=6.25,
        fallback_region=1,
        fallback_preset=0,
    )


@pytest.fixture(autouse=True)
def fake_beacon_proto(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_beacon_proto(monkeypatch)


@pytest.mark.parametrize("region", [None, 0])
def test_repeated_offer_preserves_explicit_zero_receiver_region(
    monkeypatch: pytest.MonkeyPatch, region: int | None
) -> None:
    plugin = _plugin()
    record = _record()
    plugin._received_beacons = [record]
    monkeypatch.setattr(plugin, "_receiver_radio_fallback", lambda: (region, 0))

    updated, _ = plugin._store_received_beacon(
        sender=record.sender,
        sender_key=record.sender_key,
        beacon=record.beacon(),
        source_channel=0,
        rssi=None,
        snr=None,
    )

    assert updated.fallback_region == (1 if region is None else 0)


@pytest.mark.no_global_mocks
@pytest.mark.asyncio
async def test_node_lookup_does_not_block_async_beacon_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    plugin = _plugin(announce=False)
    loop_thread = threading.get_ident()
    lookup_threads: list[int] = []

    def lookup() -> int:
        lookup_threads.append(threading.get_ident())
        return 999

    monkeypatch.setattr(plugin, "get_my_node_id", lookup)

    await plugin.handle_meshtastic_message(_packet(_FakeBeacon()), None, None, None)

    assert lookup_threads and lookup_threads[0] != loop_thread
    assert len(plugin._received_beacons) == 1


def test_enables_only_listener_flag() -> None:
    interface = _Interface(flags=0b110)
    plugin = _plugin()

    assert plugin.ensure_listening(interface) is True

    assert interface.localNode.moduleConfig.mesh_beacon.flags == 0b111
    interface.localNode.writeConfig.assert_called_once_with("mesh_beacon")


def test_listener_enablement_is_idempotent() -> None:
    interface = _Interface(flags=0b101)
    plugin = _plugin()

    assert plugin.ensure_listening(interface) is False
    interface.localNode.writeConfig.assert_not_called()


def test_listener_write_failure_restores_cached_flags() -> None:
    interface = _Interface(flags=0b010)
    interface.localNode.writeConfig.side_effect = RuntimeError("transport failed")

    with pytest.raises(RuntimeError, match="transport failed"):
        _plugin().ensure_listening(interface)

    assert interface.localNode.moduleConfig.mesh_beacon.flags == 0b010


@pytest.mark.parametrize(
    "mutation",
    ["no_node", "no_module", "client_schema", "firmware"],
)
def test_listener_capability_checks_fail_closed(mutation: str) -> None:
    interface = _Interface()
    if mutation == "no_node":
        interface.localNode = None
    elif mutation == "no_module":
        interface.localNode.moduleConfig = None
    elif mutation == "client_schema":
        interface.localNode.moduleConfig.DESCRIPTOR = SimpleNamespace(fields_by_name={})
    else:
        interface.localNode.moduleConfig.supported = False

    with pytest.raises(MeshBeaconCapabilityError):
        _plugin().ensure_listening(interface)


def test_start_uses_ready_event_and_existing_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    interface = _Interface(flags=1)
    pub = MagicMock()
    monkeypatch.setattr("mmrelay.plugins.mesh_beacon_plugin.pub", pub)
    monkeypatch.setattr("mmrelay.meshtastic_utils.meshtastic_client", interface)
    plugin._ensure_listening_safely = MagicMock()

    plugin.start()
    plugin.start()

    pub.subscribe.assert_called_once()
    assert plugin._ensure_listening_safely.call_count == 2
    plugin.on_stop()
    pub.unsubscribe.assert_called_once()


@pytest.mark.asyncio
async def test_non_beacon_packet_is_not_claimed() -> None:
    assert (
        await _plugin().handle_meshtastic_message(
            {"decoded": {"portnum": 1}}, None, None, None
        )
        is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [None, {"error": "decode-failed"}, {"raw": _FakeBeacon(has_offer=False)}],
)
async def test_unusable_beacons_are_consumed_without_history(payload: object) -> None:
    plugin = _plugin()
    packet = {"decoded": {"portnum": 37, "meshbeacon": payload}}

    assert await plugin.handle_meshtastic_message(packet, None, None, None) is True
    assert plugin._received_beacons == []


@pytest.mark.asyncio
async def test_own_beacon_is_ignored() -> None:
    plugin = _plugin()
    plugin.get_my_node_id = MagicMock(return_value=123)

    assert await plugin.handle_meshtastic_message(
        _packet(_FakeBeacon()), None, None, None
    )
    assert plugin._received_beacons == []


@pytest.mark.asyncio
async def test_new_invitation_is_saved_and_announced_without_psk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin.send_matrix_message = AsyncMock(return_value=object())
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!mesh:example", "meshtastic_channel": 0}],
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.meshtastic_client", _Interface(flags=1)
    )
    beacon = _FakeBeacon(message="**join** <now>", psk=b"super-secret")

    assert await plugin.handle_meshtastic_message(
        _packet(beacon), None, "Alice", "mesh"
    )

    assert len(plugin._received_beacons) == 1
    record = plugin._received_beacons[0]
    assert record.count == 1
    assert record.fallback_region == 1
    assert record.fallback_preset == 0
    body = plugin.send_matrix_message.await_args.args[1]
    assert "super-secret" not in body
    assert r"\*\*join\*\*" in body
    assert "&lt;now&gt;" in body
    assert record.record_id in body
    assert record.announced_rooms == ["!mesh:example"]
    assert plugin.set_node_data.call_count >= 1


@pytest.mark.asyncio
async def test_periodic_repeat_refreshes_history_without_reannouncing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin.send_matrix_message = AsyncMock(return_value=object())
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!mesh:example", "meshtastic_channel": 0}],
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.meshtastic_client", _Interface(flags=1)
    )
    packet = _packet(_FakeBeacon())

    await plugin.handle_meshtastic_message(packet, None, "Alice", None)
    await plugin.handle_meshtastic_message(packet, None, "Alice", None)

    assert len(plugin._received_beacons) == 1
    assert plugin._received_beacons[0].count == 2
    assert plugin.send_matrix_message.await_count == 1


@pytest.mark.asyncio
async def test_changed_invitation_is_announced_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin.send_matrix_message = AsyncMock(return_value=object())
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!mesh:example", "meshtastic_channel": 0}],
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.meshtastic_client", _Interface(flags=1)
    )

    await plugin.handle_meshtastic_message(
        _packet(_FakeBeacon(message="one")), None, "A", None
    )
    await plugin.handle_meshtastic_message(
        _packet(_FakeBeacon(message="two")), None, "A", None
    )

    assert plugin.send_matrix_message.await_count == 2
    assert plugin._received_beacons[0].count == 2


@pytest.mark.asyncio
async def test_failed_room_delivery_remains_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin.send_matrix_message = AsyncMock(
        side_effect=[object(), None, object()]
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [
            {"id": "!one:example", "meshtastic_channel": 0},
            {"id": "!two:example", "meshtastic_channel": 0},
        ],
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.meshtastic_client", _Interface(flags=1)
    )
    packet = _packet(_FakeBeacon())

    await plugin.handle_meshtastic_message(packet, None, "Alice", None)
    await plugin.handle_meshtastic_message(packet, None, "Alice", None)

    assert plugin.send_matrix_message.await_count == 3
    assert plugin._received_beacons[0].announced_rooms == [
        "!one:example",
        "!two:example",
    ]


@pytest.mark.asyncio
async def test_announce_repeats_posts_every_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin(announce_repeats=True)
    plugin.send_matrix_message = AsyncMock(return_value=object())
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!mesh:example", "meshtastic_channel": 0}],
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.meshtastic_client", _Interface(flags=1)
    )
    packet = _packet(_FakeBeacon())

    await plugin.handle_meshtastic_message(packet, None, "Alice", None)
    await plugin.handle_meshtastic_message(packet, None, "Alice", None)

    assert plugin.send_matrix_message.await_count == 2


@pytest.mark.asyncio
async def test_invalid_relay_channel_fails_closed_for_ambient_announcements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin(relay_channel="not-a-channel")
    plugin.send_matrix_message = AsyncMock(return_value=object())
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!zero:example", "meshtastic_channel": 0}],
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.meshtastic_client", _Interface(flags=1)
    )

    await plugin.handle_meshtastic_message(_packet(_FakeBeacon()), None, "Alice", None)

    plugin.send_matrix_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_relay_channel_routes_ambient_announcements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin(relay_channel=2)
    plugin.send_matrix_message = AsyncMock(return_value=object())
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [
            {"id": "!zero:example", "meshtastic_channel": 0},
            {"id": "!two:example", "meshtastic_channel": 2},
        ],
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.meshtastic_client", _Interface(flags=1)
    )

    await plugin.handle_meshtastic_message(_packet(_FakeBeacon()), None, "Alice", None)

    assert plugin.send_matrix_message.await_args.args[0] == "!two:example"


def test_history_is_bounded_and_deduplicated() -> None:
    plugin = _plugin()
    for sender in range(25):
        plugin._store_received_beacon(
            sender=f"Node {sender}",
            sender_key=str(sender),
            beacon=_FakeBeacon(name=f"Mesh {sender}"),
            source_channel=0,
            rssi=None,
            snr=None,
        )

    assert len(plugin._received_beacons) == 20
    assert plugin._received_beacons[0].sender_key == "24"


def test_history_loader_rejects_corrupt_and_duplicate_rows() -> None:
    record = _record()
    plugin = _plugin()
    plugin.get_node_data = MagicMock(
        return_value=[record.to_dict(), record.to_dict(), {"payload_b64": "%%%"}]
    )

    plugin._load_history()

    assert len(plugin._received_beacons) == 1
    assert plugin._received_beacons[0].record_id == record.record_id


def test_markdown_escape_treats_radio_text_as_data() -> None:
    assert _markdown_text("**bold** [x](https://bad) <tag>") == (
        r"\*\*bold\*\* \[x\]\(https://bad\) &lt;tag&gt;"
    )


def test_room_visibility_follows_channel_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin._received_beacons = [
        _record(channel=0),
        _record(_FakeBeacon(name="Other"), channel=1),
    ]
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [
            {"id": "!zero:example", "meshtastic_channel": 0},
            {"id": "!one:example", "meshtastic_channel": 1},
        ],
    )

    assert [r.source_channel for r in plugin._visible_records("!zero:example")] == [0]
    assert [r.source_channel for r in plugin._visible_records("!one:example")] == [1]


def test_relay_room_can_recall_all_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _plugin(relay_channel=2)
    plugin._received_beacons = [
        _record(channel=0),
        _record(_FakeBeacon(name="Other"), channel=1),
    ]
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!inbox:example", "meshtastic_channel": 2}],
    )

    assert len(plugin._visible_records("!inbox:example")) == 2


def test_detail_never_renders_psk() -> None:
    plugin = _plugin()
    record = _record(_FakeBeacon(psk=b"top-secret"))

    detail = plugin._beacon_detail_text(record)

    assert "top-secret" not in detail
    assert "available only in explicit URL/QR output" in detail


def test_list_uses_stable_ids() -> None:
    record = _record()
    text = _plugin()._beacon_list_text([record])

    assert record.record_id in text
    assert "!beacons url ID" in text


def test_url_output_is_explicit_about_seturl_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    record = _record()
    monkeypatch.setattr(
        plugin, "_beacon_join_url", lambda _record: "https://meshtastic.org/e/#abc"
    )

    text = plugin._beacon_url_text(record)

    assert "meshtastic --seturl 'https://meshtastic.org/e/#abc'" in text
    assert "replaces the channel set" in text


@pytest.mark.asyncio
async def test_room_commands_use_scoped_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin._received_beacons = [
        _record(channel=0),
        _record(_FakeBeacon(name="Private"), channel=1),
    ]
    plugin.send_matrix_message = AsyncMock(return_value=object())
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("beacons", "list")
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!room:example", "meshtastic_channel": 0}],
    )
    room = SimpleNamespace(room_id="!room:example")

    assert await plugin.handle_room_message(room, object(), "!beacons") is True

    body = plugin.send_matrix_message.await_args.args[1]
    assert "LongFast" in body
    assert "Private" not in body


@pytest.mark.asyncio
async def test_clear_removes_only_room_visible_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    first = _record(channel=0)
    second = _record(_FakeBeacon(name="Other"), channel=1)
    plugin._received_beacons = [first, second]
    plugin.send_matrix_message = AsyncMock(return_value=object())
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("beacons", "clear")
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!room:example", "meshtastic_channel": 0}],
    )

    await plugin.handle_room_message(
        SimpleNamespace(room_id="!room:example"), object(), ""
    )

    assert [record.key for record in plugin._received_beacons] == [second.key]


@pytest.mark.asyncio
async def test_qr_command_posts_caption_and_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    record = _record()
    plugin._received_beacons = [record]
    plugin.send_matrix_message = AsyncMock(return_value=object())
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("beacons", f"qr {record.record_id}")
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!room:example", "meshtastic_channel": 0}],
    )
    monkeypatch.setattr(
        plugin, "_beacon_join_url", lambda _record: "https://meshtastic.org/e/#abc"
    )
    monkeypatch.setattr(
        "mmrelay.plugins.mesh_beacon_plugin._qr_image", lambda _url: object()
    )
    matrix_client = object()
    connect = AsyncMock(return_value=matrix_client)
    send_image = AsyncMock()
    monkeypatch.setattr("mmrelay.matrix_utils.connect_matrix", connect)
    monkeypatch.setattr("mmrelay.matrix_utils.send_image", send_image)

    assert await plugin.handle_room_message(
        SimpleNamespace(room_id="!room:example"), object(), ""
    )

    send_image.assert_awaited_once()
    assert send_image.await_args.args[0] is matrix_client
    assert record.record_id in send_image.await_args.kwargs["filename"]


@pytest.mark.asyncio
async def test_qr_unexpected_upload_failure_falls_back_to_url_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    record = _record()
    plugin.send_matrix_message = AsyncMock(return_value=object())
    monkeypatch.setattr(
        plugin, "_beacon_join_url", lambda _record: "https://meshtastic.org/e/#abc"
    )
    monkeypatch.setattr(
        "mmrelay.plugins.mesh_beacon_plugin._qr_image", lambda _url: object()
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.connect_matrix", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.send_image",
        AsyncMock(side_effect=RuntimeError("upload failed")),
    )

    await plugin._send_beacon_qr("!room:example", record)

    assert plugin.send_matrix_message.await_count == 2
    assert "!beacons url ID" in plugin.send_matrix_message.await_args.args[1]
