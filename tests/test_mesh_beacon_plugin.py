from __future__ import annotations

import asyncio
import base64
import json
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mmrelay.matrix_utils import ImageUploadError
from mmrelay.plugins.mesh_beacon_plugin import (
    MeshBeaconCapabilityError,
    Plugin,
    _age_text,
    _BeaconRecord,
    _channel_number,
    _clean_text,
    _enum_name,
    _has_offer,
    _has_optional_field,
    _is_mesh_beacon_portnum,
    _markdown_text,
    _matrix_room_entries,
    _optional_float,
    _psk_config_value,
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
    monkeypatch.setattr(
        sys.modules["meshtastic.protobuf"], "mesh_beacon_pb2", module, raising=False
    )
    monkeypatch.setattr(
        sys.modules["meshtastic.protobuf.portnums_pb2"].PortNum,
        "MESH_BEACON_APP",
        37,
        raising=False,
    )


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


def _matrix_client(*, encrypted: bool = False) -> Any:
    return SimpleNamespace(
        rooms={"!room:example": SimpleNamespace(encrypted=encrypted)},
        upload=AsyncMock(),
        room_send=AsyncMock(return_value=object()),
    )


def _room(room_id: str = "!room:example", *, can_redact: bool = False) -> Any:
    return SimpleNamespace(
        room_id=room_id,
        power_levels=SimpleNamespace(
            can_user_redact=MagicMock(return_value=can_redact)
        ),
    )


def _event(sender: str = "@user:example") -> Any:
    return SimpleNamespace(sender=sender)


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
    plugin.send_matrix_message = AsyncMock(side_effect=[object(), None, object()])
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
async def test_room_dismissal_suppresses_unchanged_ambient_repeats(
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
    record = plugin._received_beacons[0]
    plugin._dismiss_record(record, "!mesh:example")
    await plugin.handle_meshtastic_message(packet, None, "Alice", None)

    assert plugin.send_matrix_message.await_count == 1
    assert record.dismissed_rooms == ["!mesh:example"]


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


@pytest.mark.no_global_mocks
@pytest.mark.asyncio
async def test_persistence_cannot_land_an_older_snapshot_last() -> None:
    plugin = _plugin()
    record = _record(channel=0)
    plugin._received_beacons = [record]
    first_write_started = threading.Event()
    release_first_write = threading.Event()
    writes: list[list[dict[str, Any]]] = []
    write_calls = 0
    call_lock = threading.Lock()

    def store(
        _key: str, snapshot: list[dict[str, Any]], *, raise_on_error: bool = False
    ) -> None:
        nonlocal write_calls
        with call_lock:
            index = write_calls
            write_calls += 1
        if index == 0:
            first_write_started.set()
            assert release_first_write.wait(timeout=2)
        writes.append(snapshot)

    plugin.set_node_data = store  # type: ignore[method-assign]
    first = asyncio.create_task(plugin._persist_history())
    for _ in range(200):
        if first_write_started.is_set():
            break
        await asyncio.sleep(0.01)
    assert first_write_started.is_set()

    record.count = 2
    record.dismissed_rooms.append("!room:example")
    second = asyncio.create_task(plugin._persist_history())
    await asyncio.sleep(0.05)
    release_first_write.set()
    await asyncio.gather(first, second)

    assert len(writes) == 2
    assert writes[-1][0]["count"] == 2
    assert writes[-1][0]["dismissed_rooms"] == ["!room:example"]


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


@pytest.mark.parametrize(
    ("psk", "expected"),
    [
        (b"", "none"),
        (b"\x00", "none"),
        (b"\x01", "default"),
        (b"\x17", "simple22"),
        (b"top-secret", "base64:dG9wLXNlY3JldA=="),
    ],
)
def test_psk_config_value_uses_meshtastic_cli_forms(psk: bytes, expected: str) -> None:
    assert _psk_config_value(psk) == expected


def test_detail_renders_public_invitation_psk() -> None:
    plugin = _plugin()
    record = _record(_FakeBeacon(psk=b"top-secret"))

    detail = plugin._beacon_detail_text(record)

    assert "**PSK:** `base64:dG9wLXNlY3JldA==`" in detail
    assert "top-secret" not in detail


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
async def test_clear_dismisses_only_room_visible_records_for_moderator(
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

    await plugin.handle_room_message(_room(can_redact=True), _event("@mod:example"), "")

    assert [record.key for record in plugin._received_beacons] == [
        first.key,
        second.key,
    ]
    assert first.dismissed_rooms == ["!room:example"]
    assert second.dismissed_rooms == []
    assert plugin._visible_records("!room:example") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["clear", "dismiss 1"])
async def test_destructive_commands_require_room_moderation_permission(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    plugin = _plugin()
    record = _record(channel=0)
    plugin._received_beacons = [record]
    plugin.send_matrix_message = AsyncMock(return_value=object())
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("beacons", command)
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!room:example", "meshtastic_channel": 0}],
    )

    await plugin.handle_room_message(_room(can_redact=False), _event(), "")

    assert record.dismissed_rooms == []
    plugin.set_node_data.assert_not_called()
    body = plugin.send_matrix_message.await_args.args[1]
    assert "requires Matrix room moderation permission" in body


def test_room_scoped_dismissal_does_not_hide_other_rooms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    record = _record(channel=0)
    plugin._received_beacons = [record]
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [
            {"id": "!one:example", "meshtastic_channel": 0},
            {"id": "!two:example", "meshtastic_channel": 0},
        ],
    )

    plugin._dismiss_record(record, "!one:example")

    assert plugin._visible_records("!one:example") == []
    assert plugin._visible_records("!two:example") == [record]


def test_changed_offer_revives_room_dismissal() -> None:
    plugin = _plugin()
    record, _ = plugin._store_received_beacon(
        sender="Some Node",
        sender_key="123",
        beacon=_FakeBeacon(message="one"),
        source_channel=0,
        rssi=None,
        snr=None,
    )
    record.dismissed_rooms.append("!room:example")

    refreshed, _ = plugin._store_received_beacon(
        sender="Some Node",
        sender_key="123",
        beacon=_FakeBeacon(message="two"),
        source_channel=0,
        rssi=None,
        snr=None,
    )

    assert refreshed is record
    assert record.dismissed_rooms == []


def test_history_loader_accepts_records_without_dismissal_metadata() -> None:
    record = _record()
    stored = record.to_dict()
    stored.pop("dismissed_rooms")
    plugin = _plugin()
    plugin.get_node_data = MagicMock(return_value=[stored])

    plugin._load_history()

    assert plugin._received_beacons[0].dismissed_rooms == []


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
    matrix_client = _matrix_client()
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
        "mmrelay.matrix_utils.connect_matrix", AsyncMock(return_value=_matrix_client())
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.send_image",
        AsyncMock(side_effect=RuntimeError("upload failed")),
    )

    await plugin._send_beacon_qr("!room:example", record)

    assert plugin.send_matrix_message.await_count == 2
    assert "!beacons url ID" in plugin.send_matrix_message.await_args.args[1]


# --- Codecov patch-coverage gap tests ------------------------------------------
#
# These tests target the exact lines the CI coverage upload reports as missed
# for this plugin: helper fail-closed arms, readiness lifecycle outcomes,
# storage failure handling, announce validation and delivery failures, command
# dispatch branches, and the real ChannelSet join-URL construction.


def test_record_loader_rejects_non_mapping_and_offerless_rows() -> None:
    assert _BeaconRecord.from_dict("junk") is None
    assert _BeaconRecord.from_dict(None) is None
    offerless = _record(_FakeBeacon(has_offer=False)).to_dict()
    assert _BeaconRecord.from_dict(offerless) is None


@pytest.mark.parametrize("unusable", ["nope", 5, {"a": 1}])
def test_record_loader_resets_unusable_room_lists(unusable: object) -> None:
    stored = _record().to_dict()
    stored["announced_rooms"] = unusable
    stored["dismissed_rooms"] = unusable

    record = _BeaconRecord.from_dict(stored)

    assert record is not None
    assert record.announced_rooms == []
    assert record.dismissed_rooms == []


def test_optional_float_ignores_none_bool_and_junk() -> None:
    assert _optional_float(None) is None
    assert _optional_float(True) is None
    assert _optional_float("not-a-number") is None
    assert _optional_float("-42.5") == -42.5


def test_channel_number_rejects_bools_negatives_and_junk() -> None:
    assert _channel_number(True) is None
    assert _channel_number(-1) is None
    assert _channel_number("-3") is None
    assert _channel_number("junk") is None
    assert _channel_number(1.5) is None
    assert _channel_number(" 7 ") == 7


def test_portnum_matching_accepts_names_and_numeric_strings() -> None:
    assert _is_mesh_beacon_portnum("mesh_beacon_app") is True
    assert _is_mesh_beacon_portnum("MESH_BEACON_APP") is True
    assert _is_mesh_beacon_portnum("37") is True
    assert _is_mesh_beacon_portnum(37) is True
    assert _is_mesh_beacon_portnum(True) is False
    assert _is_mesh_beacon_portnum(None) is False
    assert _is_mesh_beacon_portnum("nope") is False


def test_proto_field_helpers_fail_closed_on_plain_objects() -> None:
    plain = object()

    assert _has_offer(plain) is False
    assert _has_optional_field(plain, "offer_preset") is False
    assert _enum_name(plain, "offer_region", 1) is None
    assert _enum_name(_FakeBeacon(region=0), "offer_region", 0) is None


def test_clean_text_treats_non_strings_as_empty() -> None:
    assert _clean_text(42) == ""
    assert _clean_text("a\x00b\t c") == "a b c"


def test_psk_config_value_reports_unavailable_for_non_bytes() -> None:
    assert _psk_config_value(None) == "unavailable"
    assert _psk_config_value("key") == "unavailable"


def test_matrix_room_entries_reads_dict_shaped_room_maps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mmrelay.matrix_utils as matrix_utils

    room = {"id": "!a:example", "meshtastic_channel": 0}
    monkeypatch.setattr(matrix_utils, "matrix_rooms", {"first": room})
    assert _matrix_room_entries() == [room]
    monkeypatch.setattr(matrix_utils, "matrix_rooms", "junk")
    assert _matrix_room_entries() == []


def test_age_text_reports_relative_buckets() -> None:
    now = time.time()
    assert _age_text(now - 30) == "just now"
    assert _age_text(now - 90) == "1m ago"
    assert _age_text(now - 3 * 3600) == "3h ago"
    assert _age_text(now - 72 * 3600) == "3d ago"


def test_qr_image_render_pipeline_produces_an_image() -> None:
    from mmrelay.plugins.mesh_beacon_plugin import _qr_image

    # The suite doubles segno/PIL; the subprocess integration contract covers
    # real encoding. This pins the render pipeline (buffer, open, load) itself.
    image = _qr_image("https://meshtastic.org/e/#abc123")

    assert image is not None
    assert image.load is not None


def test_plugin_metadata_surfaces_beacon_commands() -> None:
    plugin = _plugin()

    assert plugin.description
    assert plugin.get_matrix_commands() == ["beacons", "mesh_beacon"]


def test_stop_without_subscription_and_repeated_stop_are_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mmrelay.plugins.mesh_beacon_plugin as plugin_module

    plugin = _plugin()
    plugin.on_stop()

    pub = MagicMock()
    pub.unsubscribe.side_effect = ValueError("no listener for topic")
    monkeypatch.setattr(plugin_module, "pub", pub)
    plugin._connection_subscribed = True

    plugin.on_stop()

    plugin.logger.debug.assert_called_once()
    assert plugin._connection_subscribed is False


@pytest.mark.parametrize(
    ("flags", "supported", "write_error", "expected_log", "expected_writes"),
    [
        (0, True, None, "info", 1),
        (1, True, None, "debug", 0),
        (0, False, None, "warning", 0),
        (0, True, RuntimeError("write failed"), "exception", 1),
    ],
)
def test_ready_event_enables_listening_with_operator_visible_outcome(
    monkeypatch: pytest.MonkeyPatch,
    flags: int,
    supported: bool,
    write_error: RuntimeError | None,
    expected_log: str,
    expected_writes: int,
) -> None:
    plugin = _plugin()
    interface = _Interface(flags=flags, supported=supported)
    if write_error is not None:
        interface.localNode.writeConfig.side_effect = write_error
    monkeypatch.setattr("mmrelay.meshtastic_utils.meshtastic_client", None)

    plugin.start()
    plugin._on_meshtastic_ready(interface)
    plugin.on_stop()

    def _listener_mentions(level: str) -> list[Any]:
        calls = getattr(plugin.logger, level).call_args_list
        return [
            call
            for call in calls
            if any("Mesh Beacon" in str(argument) for argument in call.args)
        ]

    assert len(_listener_mentions(expected_log)) == 1
    for level in ("info", "debug", "warning", "exception"):
        if level != expected_log:
            assert _listener_mentions(level) == []
    assert interface.localNode.writeConfig.call_count == expected_writes


def test_listener_capability_reports_unreadable_module_config() -> None:
    interface = _Interface()
    interface.localNode.moduleConfig.HasField = MagicMock(
        side_effect=ValueError("unreadable")
    )

    with pytest.raises(MeshBeaconCapabilityError, match="did not expose"):
        _plugin().ensure_listening(interface)


def test_history_loader_survives_storage_failures() -> None:
    failing = _plugin()
    failing.get_node_data = MagicMock(side_effect=RuntimeError("db closed"))
    failing._load_history()
    failing.logger.exception.assert_called_once()
    assert failing._received_beacons == []

    junk = _plugin()
    junk.get_node_data = MagicMock(return_value={"not": "a list"})
    junk._load_history()
    assert junk._received_beacons == []


def test_history_loader_caps_restored_rows() -> None:
    rows = [
        {**_record().to_dict(), "sender_key": str(100 + index)} for index in range(30)
    ]
    plugin = _plugin()
    plugin.get_node_data = MagicMock(return_value=rows)

    plugin._load_history()

    assert len(plugin._received_beacons) == 20


@pytest.mark.asyncio
async def test_persist_history_logs_failures_without_raising() -> None:
    plugin = _plugin()
    plugin.set_node_data = MagicMock(side_effect=RuntimeError("disk full"))

    assert await plugin._persist_history() is False

    plugin.logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_beacon_without_protobuf_payload_is_consumed() -> None:
    plugin = _plugin()
    packet = {"decoded": {"portnum": 37, "meshbeacon": {"raw": "not-a-beacon"}}}

    assert await plugin.handle_meshtastic_message(packet, None, None, None) is True

    plugin.logger.warning.assert_called_once()
    assert plugin._received_beacons == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "expected_sends"),
    [
        ({"announce": "yes"}, 0),
        ({"announce": False}, 0),
        ({"announce_repeats": "yes"}, 1),
    ],
)
async def test_announce_settings_are_validated(
    monkeypatch: pytest.MonkeyPatch,
    config: dict[str, Any],
    expected_sends: int,
) -> None:
    plugin = _plugin(**config)
    plugin.send_matrix_message = AsyncMock(return_value=object())
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!mesh:example", "meshtastic_channel": 0}],
    )

    assert (
        await plugin.handle_meshtastic_message(
            _packet(_FakeBeacon()), "Node", None, None
        )
        is True
    )

    assert plugin.send_matrix_message.await_count == expected_sends
    assert len(plugin._received_beacons) == 1
    assert plugin.logger.error.call_count == (
        1 if any(isinstance(value, str) for value in config.values()) else 0
    )


@pytest.mark.asyncio
async def test_announce_send_exception_does_not_block_other_rooms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin.send_matrix_message = AsyncMock(
        side_effect=[RuntimeError("send failed"), object()]
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [
            {"id": "!one:example", "meshtastic_channel": 0},
            {"id": "!two:example", "meshtastic_channel": 0},
        ],
    )

    assert (
        await plugin.handle_meshtastic_message(
            _packet(_FakeBeacon()), "Node", None, None
        )
        is True
    )

    assert plugin.send_matrix_message.await_count == 2
    plugin.logger.exception.assert_called_once()
    assert plugin._received_beacons[0].announced_rooms == ["!two:example"]


@pytest.mark.asyncio
async def test_packet_without_channel_defaults_to_primary_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin.send_matrix_message = AsyncMock(return_value=object())
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!mesh:example", "meshtastic_channel": 0}],
    )
    packet = _packet(_FakeBeacon())
    packet.pop("channel")

    assert await plugin.handle_meshtastic_message(packet, None, None, None) is True

    assert len(plugin._received_beacons) == 1
    assert plugin._received_beacons[0].source_channel == 0
    plugin.send_matrix_message.assert_awaited_once()
    assert plugin.send_matrix_message.await_args.args[0] == "!mesh:example"


def test_room_channel_is_none_for_unmapped_rooms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("mmrelay.matrix_utils.matrix_rooms", [])
    assert _plugin()._room_channel("!unknown:example") is None


def test_unmapped_room_cannot_recall_legacy_unknown_channel_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin._received_beacons = [_record(channel=None)]
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!mapped:example", "meshtastic_channel": 0}],
    )

    assert plugin._visible_records("!unmapped:example") == []


@pytest.mark.asyncio
async def test_non_command_message_is_ignored() -> None:
    plugin = _plugin()
    plugin.get_matching_matrix_command_with_args = MagicMock(return_value=None)

    assert (
        await plugin.handle_room_message(SimpleNamespace(room_id="!r"), _event(), "")
        is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command", ["show nope", "url nope", "qr nope", "dismiss nope"]
)
async def test_unknown_selectors_reply_with_guidance(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    plugin = _plugin()
    plugin._received_beacons = [_record(channel=0)]
    plugin.send_matrix_message = AsyncMock(return_value=object())
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("beacons", command)
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!room:example", "meshtastic_channel": 0}],
    )

    assert (
        await plugin.handle_room_message(_room(can_redact=True), _event(), "") is True
    )

    body = plugin.send_matrix_message.await_args.args[1]
    assert "No saved Mesh Beacon matches" in body


@pytest.mark.asyncio
async def test_dismiss_dispatch_persists_room_tombstone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    record = _record(channel=0)
    plugin._received_beacons = [record]
    plugin.send_matrix_message = AsyncMock(return_value=object())
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("beacons", f"dismiss {record.record_id}")
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!room:example", "meshtastic_channel": 0}],
    )

    await plugin.handle_room_message(_room(can_redact=True), _event(), "")

    assert record.dismissed_rooms == ["!room:example"]
    plugin.set_node_data.assert_called_once()
    body = plugin.send_matrix_message.await_args.args[1]
    assert "Dismissed Mesh Beacon" in body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("argument", "expected_fragment"),
    [("show", "Usage: `!beacons"), ("frobnicate now", "Usage: `!beacons")],
)
async def test_incomplete_and_unknown_commands_show_usage(
    monkeypatch: pytest.MonkeyPatch, argument: str, expected_fragment: str
) -> None:
    plugin = _plugin()
    plugin._received_beacons = [_record(channel=0)]
    plugin.send_matrix_message = AsyncMock(return_value=object())
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("beacons", argument)
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!room:example", "meshtastic_channel": 0}],
    )

    await plugin.handle_room_message(_room(can_redact=True), _event(), "")

    body = plugin.send_matrix_message.await_args.args[1]
    assert expected_fragment in body
    assert "moderator-only" in body


@pytest.mark.asyncio
async def test_bare_command_id_shows_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    record = _record(channel=0)
    plugin._received_beacons = [record]
    plugin.send_matrix_message = AsyncMock(return_value=object())
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("beacons", record.record_id)
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!room:example", "meshtastic_channel": 0}],
    )

    await plugin.handle_room_message(
        SimpleNamespace(room_id="!room:example"), _event(), ""
    )

    body = plugin.send_matrix_message.await_args.args[1]
    assert record.record_id in body
    assert "**PSK:**" in body


@pytest.mark.asyncio
async def test_empty_list_reply_states_empty_inbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin.send_matrix_message = AsyncMock(return_value=object())
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("beacons", "list")
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!room:example", "meshtastic_channel": 0}],
    )

    await plugin.handle_room_message(
        SimpleNamespace(room_id="!room:example"), _event(), ""
    )

    body = plugin.send_matrix_message.await_args.args[1]
    assert "No Mesh Beacon invitations have been captured here yet." in body


def test_find_beacon_record_rejects_junk_and_out_of_range() -> None:
    records = [_record()]

    assert Plugin._find_beacon_record("not-an-id", records) is None
    assert Plugin._find_beacon_record("5", records) is None
    assert Plugin._find_beacon_record("1", records) is records[0]


def test_detail_includes_frequency_slot_and_moderator_actions() -> None:
    record = _record(_FakeBeacon(frequency_slot=20))
    plugin = _plugin()

    detail = plugin._beacon_detail_text(record, can_manage=True)
    announcement = plugin._announcement_text(record)

    assert "**Frequency slot:** `20`" in detail
    assert "!beacons dismiss" in detail
    assert "slot `20`" in announcement


def _real_join_protobuf(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, Any]:
    """Expose the installed client's real protobuf modules for join-URL tests.

    The suite replaces the meshtastic namespace with doubles; briefly import the
    real descriptors, restore the doubles, and attach the real join-URL modules
    to the double parent so in-process calls exercise real serialization.
    """
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "meshtastic" or name.startswith("meshtastic.")
    }
    for name in saved:
        del sys.modules[name]
    try:
        from meshtastic.protobuf import (
            apponly_pb2,
            config_pb2,
            mesh_beacon_pb2,
        )
    finally:
        for name in [
            name
            for name in sys.modules
            if name == "meshtastic" or name.startswith("meshtastic.")
        ]:
            del sys.modules[name]
        sys.modules.update(saved)
    double = sys.modules["meshtastic.protobuf"]
    monkeypatch.setattr(double, "apponly_pb2", apponly_pb2, raising=False)
    monkeypatch.setattr(double, "config_pb2", config_pb2, raising=False)
    monkeypatch.setattr(double, "mesh_beacon_pb2", mesh_beacon_pb2, raising=False)
    return apponly_pb2, config_pb2, mesh_beacon_pb2


def _real_beacon_record(
    mesh_beacon_pb2: Any,
    *,
    name: str = "Community",
    psk: bytes = b"join-key",
    region: int | None = 1,
    preset: int | None = 0,
    slot: int | None = None,
    fallback_region: int | None = 1,
    fallback_preset: int | None = 0,
) -> _BeaconRecord:
    beacon = mesh_beacon_pb2.MeshBeacon()
    beacon.offer_channel.name = name
    beacon.offer_channel.psk = psk
    if region is not None:
        beacon.offer_region = region
    if preset is not None:
        beacon.offer_preset = preset
    if slot is not None and "offer_frequency_slot" in beacon.DESCRIPTOR.fields_by_name:
        beacon.offer_frequency_slot = slot
    return _BeaconRecord(
        sender_key="123",
        sender="Some Node",
        payload_b64=base64.b64encode(beacon.SerializeToString()).decode("ascii"),
        source_channel=0,
        first_seen=1,
        last_seen=2,
        rssi=None,
        snr=None,
        fallback_region=fallback_region,
        fallback_preset=fallback_preset,
    )


def test_join_url_encodes_slot_and_safe_radio_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apponly_pb2, _config_pb2, mesh_beacon_pb2 = _real_join_protobuf(monkeypatch)
    record = _real_beacon_record(mesh_beacon_pb2, slot=42)

    url = Plugin._beacon_join_url(record)

    assert url is not None and url.startswith("https://meshtastic.org/e/#")
    fragment = url.split("#", 1)[1]
    payload = base64.urlsafe_b64decode(fragment + "=" * (-len(fragment) % 4))
    shared = apponly_pb2.ChannelSet.FromString(payload)
    assert shared.settings[0].name == "Community"
    assert shared.settings[0].module_settings.position_precision == 0
    assert shared.lora_config.use_preset is True
    assert shared.lora_config.hop_limit == 3
    assert shared.lora_config.tx_enabled is True
    if "offer_frequency_slot" in mesh_beacon_pb2.MeshBeacon.DESCRIPTOR.fields_by_name:
        assert shared.lora_config.channel_num == 42


def test_join_url_uses_receiver_snapshot_when_beacon_omits_radio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apponly_pb2, config_pb2, mesh_beacon_pb2 = _real_join_protobuf(monkeypatch)
    us = int(config_pb2.Config.LoRaConfig.US)
    long_fast = int(config_pb2.Config.LoRaConfig.LONG_FAST)
    record = _real_beacon_record(
        mesh_beacon_pb2,
        region=None,
        preset=None,
        fallback_region=us,
        fallback_preset=long_fast,
    )

    url = Plugin._beacon_join_url(record)

    assert url is not None
    fragment = url.split("#", 1)[1]
    payload = base64.urlsafe_b64decode(fragment + "=" * (-len(fragment) % 4))
    shared = apponly_pb2.ChannelSet.FromString(payload)
    assert shared.lora_config.region == us
    assert shared.lora_config.modem_preset == long_fast


def test_join_url_omits_lora_config_without_any_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apponly_pb2, _config_pb2, mesh_beacon_pb2 = _real_join_protobuf(monkeypatch)
    record = _real_beacon_record(
        mesh_beacon_pb2, region=None, preset=None, fallback_region=None
    )

    url = Plugin._beacon_join_url(record)

    assert url is not None
    fragment = url.split("#", 1)[1]
    payload = base64.urlsafe_b64decode(fragment + "=" * (-len(fragment) % 4))
    shared = apponly_pb2.ChannelSet.FromString(payload)
    assert not shared.HasField("lora_config")
    assert shared.settings[0].name == "Community"


def test_join_url_fails_closed_for_corrupt_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _real_join_protobuf(monkeypatch)
    record = _record()
    record.payload_b64 = "not base64!"

    assert Plugin._beacon_join_url(record) is None


@pytest.mark.asyncio
async def test_url_dispatch_replies_with_join_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _apponly_pb2, _config_pb2, mesh_beacon_pb2 = _real_join_protobuf(monkeypatch)
    plugin = _plugin()
    record = _real_beacon_record(mesh_beacon_pb2)
    plugin._received_beacons = [record]
    plugin.send_matrix_message = AsyncMock(return_value=object())
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("beacons", f"url {record.record_id}")
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!room:example", "meshtastic_channel": 0}],
    )

    assert (
        await plugin.handle_room_message(
            SimpleNamespace(room_id="!room:example"), _event(), ""
        )
        is True
    )

    body = plugin.send_matrix_message.await_args.args[1]
    assert "meshtastic.org/e/#" in body
    assert "meshtastic --seturl" in body


@pytest.mark.asyncio
async def test_url_dispatch_reports_unencodable_invitations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    record = _record(_FakeBeacon(has_offer=False))
    plugin._received_beacons = [record]
    plugin.send_matrix_message = AsyncMock(return_value=object())
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("beacons", f"url {record.record_id}")
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!room:example", "meshtastic_channel": 0}],
    )

    await plugin.handle_room_message(
        SimpleNamespace(room_id="!room:example"), _event(), ""
    )

    body = plugin.send_matrix_message.await_args.args[1]
    assert "could not be encoded as a safe ChannelSet" in body


@pytest.mark.asyncio
async def test_qr_dispatch_renders_real_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _apponly_pb2, _config_pb2, mesh_beacon_pb2 = _real_join_protobuf(monkeypatch)
    plugin = _plugin()
    record = _real_beacon_record(mesh_beacon_pb2)
    plugin._received_beacons = [record]
    plugin.send_matrix_message = AsyncMock(return_value=object())
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("beacons", f"qr {record.record_id}")
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!room:example", "meshtastic_channel": 0}],
    )
    send_image = AsyncMock()
    monkeypatch.setattr(
        "mmrelay.matrix_utils.connect_matrix", AsyncMock(return_value=_matrix_client())
    )
    monkeypatch.setattr("mmrelay.matrix_utils.send_image", send_image)

    assert (
        await plugin.handle_room_message(
            SimpleNamespace(room_id="!room:example"), _event(), ""
        )
        is True
    )

    send_image.assert_awaited_once()
    assert record.record_id in send_image.await_args.kwargs["filename"]


@pytest.mark.asyncio
async def test_qr_unencodable_invitation_falls_back_to_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin.send_matrix_message = AsyncMock(return_value=object())
    record = _record()
    monkeypatch.setattr(plugin, "_beacon_join_url", lambda _record: None)

    await plugin._send_beacon_qr("!room:example", record)

    body = plugin.send_matrix_message.await_args.args[1]
    assert "could not be encoded as a QR invitation" in body


@pytest.mark.asyncio
async def test_qr_render_failure_falls_back_to_url_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin.send_matrix_message = AsyncMock(return_value=object())
    record = _record()
    monkeypatch.setattr(
        plugin, "_beacon_join_url", lambda _record: "https://meshtastic.org/e/#abc"
    )

    def _boom(_url: str) -> Any:
        raise ImportError("segno missing")

    monkeypatch.setattr("mmrelay.plugins.mesh_beacon_plugin._qr_image", _boom)

    await plugin._send_beacon_qr("!room:example", record)

    plugin.logger.exception.assert_called_once()
    body = plugin.send_matrix_message.await_args.args[1]
    assert "QR rendering failed" in body


@pytest.mark.asyncio
async def test_qr_upload_failure_falls_back_to_url_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin.send_matrix_message = AsyncMock(return_value=object())
    record = _record()
    monkeypatch.setattr(
        plugin, "_beacon_join_url", lambda _record: "https://meshtastic.org/e/#abc"
    )
    monkeypatch.setattr(
        "mmrelay.plugins.mesh_beacon_plugin._qr_image", lambda _url: object()
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.connect_matrix", AsyncMock(return_value=_matrix_client())
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.send_image",
        AsyncMock(side_effect=ImageUploadError("upload rejected")),
    )

    await plugin._send_beacon_qr("!room:example", record)

    plugin.logger.exception.assert_called_once()
    body = plugin.send_matrix_message.await_args.args[1]
    assert "Failed to upload the QR image" in body


@pytest.mark.asyncio
async def test_qr_missing_matrix_client_falls_back_to_url_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin.send_matrix_message = AsyncMock(return_value=object())
    record = _record()
    monkeypatch.setattr(
        plugin, "_beacon_join_url", lambda _record: "https://meshtastic.org/e/#abc"
    )
    monkeypatch.setattr(
        "mmrelay.plugins.mesh_beacon_plugin._qr_image", lambda _url: object()
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.connect_matrix", AsyncMock(return_value=None)
    )

    await plugin._send_beacon_qr("!room:example", record)

    plugin.logger.exception.assert_called_once()
    body = plugin.send_matrix_message.await_args.args[1]
    assert "Failed to upload the QR image" in body


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["dismiss 1", "clear"])
async def test_moderation_reports_database_failure_without_durable_success(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    import sqlite3

    plugin = _plugin()
    record = _record()
    plugin._received_beacons = [record]
    plugin.send_matrix_message = AsyncMock(return_value=object())
    plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("beacons", command)
    )
    # Exercise the plugin/base/database path, failing at the SQLite boundary.
    monkeypatch.setattr(plugin, "set_node_data", Plugin.set_node_data.__get__(plugin))
    manager = SimpleNamespace(
        run_sync=MagicMock(side_effect=sqlite3.OperationalError("disk full"))
    )
    monkeypatch.setattr("mmrelay.db_utils._get_db_manager", lambda: manager)
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "!room:example", "meshtastic_channel": 0}],
    )

    await plugin.handle_room_message(_room(can_redact=True), _event(), "")

    reply = plugin.send_matrix_message.await_args.args[1]
    assert "saving failed" in reply
    assert "may reappear after restart" in reply
    assert "Dismissed" not in reply and "Cleared" not in reply
    assert record.dismissed_rooms == ["!room:example"]
    manager.run_sync.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("upload_failure", [False, True])
async def test_encrypted_qr_preserves_attachment_metadata_and_never_sends_plain_url(
    monkeypatch: pytest.MonkeyPatch, upload_failure: bool
) -> None:
    from PIL import Image

    plugin = _plugin()
    record = _record()
    plugin.send_matrix_message = AsyncMock(return_value=object())
    monkeypatch.setattr(
        plugin, "_beacon_join_url", lambda _: "https://meshtastic.org/e/#abc"
    )
    monkeypatch.setattr(
        "mmrelay.plugins.mesh_beacon_plugin._qr_image",
        lambda _: Image.new("RGB", (8, 8)),
    )
    client = _matrix_client(encrypted=True)
    encryption = {
        "v": "v2",
        "key": {"k": "key"},
        "iv": "iv",
        "hashes": {"sha256": "hash"},
    }
    client.upload.return_value = (
        SimpleNamespace(content_uri="mxc://example/qr"),
        None if upload_failure else encryption,
    )
    plain_send = AsyncMock()
    monkeypatch.setattr(
        "mmrelay.matrix_utils.connect_matrix", AsyncMock(return_value=client)
    )
    monkeypatch.setattr("mmrelay.matrix_utils.send_image", plain_send)

    await plugin._send_beacon_qr("!room:example", record)

    assert client.upload.await_args.kwargs["encrypt"] is True
    plain_send.assert_not_awaited()
    if upload_failure:
        client.room_send.assert_not_awaited()
        assert "!beacons url ID" in plugin.send_matrix_message.await_args.args[1]
    else:
        content = client.room_send.await_args.kwargs["content"]
        assert content["file"] == {**encryption, "url": "mxc://example/qr"}
        assert "url" not in content


@pytest.mark.asyncio
async def test_qr_unknown_room_encryption_refuses_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin.send_matrix_message = AsyncMock(return_value=object())
    client = _matrix_client()
    client.rooms.clear()
    monkeypatch.setattr(
        plugin, "_beacon_join_url", lambda _: "https://meshtastic.org/e/#abc"
    )
    monkeypatch.setattr(
        "mmrelay.plugins.mesh_beacon_plugin._qr_image", lambda _: object()
    )
    monkeypatch.setattr(
        "mmrelay.matrix_utils.connect_matrix", AsyncMock(return_value=client)
    )
    plain_send = AsyncMock()
    monkeypatch.setattr("mmrelay.matrix_utils.send_image", plain_send)

    await plugin._send_beacon_qr("!room:example", _record())

    client.upload.assert_not_awaited()
    plain_send.assert_not_awaited()
    assert "!beacons url ID" in plugin.send_matrix_message.await_args.args[1]
