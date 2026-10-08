from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import html
import io
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, cast

from google.protobuf.message import DecodeError
from nio import (
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
    RoomSendError,
)
from pubsub import pub

from mmrelay.constants.meshtastic import MESHTASTIC_READY_TOPIC
from mmrelay.plugins.base_plugin import BasePlugin

_LISTEN_FLAG = 1
_MAX_STORED_BEACONS = 20
_HISTORY_STORAGE_KEY = "__history__"
_JOIN_URL_PREFIX = "https://meshtastic.org/e/#"
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+.!|>~-])")


class _MeshBeaconConfigProtocol(Protocol):
    flags: int


class _ModuleConfigProtocol(Protocol):
    mesh_beacon: _MeshBeaconConfigProtocol
    DESCRIPTOR: Any

    def HasField(self, field_name: str) -> bool: ...


class _LoraConfigProtocol(Protocol):
    use_preset: bool
    region: int
    modem_preset: int


class _LocalConfigProtocol(Protocol):
    lora: _LoraConfigProtocol


class _LocalNodeProtocol(Protocol):
    moduleConfig: _ModuleConfigProtocol | None
    localConfig: _LocalConfigProtocol | None

    def writeConfig(self, config_name: str) -> None: ...


class _MeshInterfaceProtocol(Protocol):
    localNode: _LocalNodeProtocol | None


class MeshBeaconCapabilityError(RuntimeError):
    """Raised when the connected client or firmware cannot expose Mesh Beacon."""


@dataclass
class _BeaconRecord:
    sender_key: str
    sender: str
    payload_b64: str
    source_channel: int | None
    first_seen: float
    last_seen: float
    count: int = 1
    rssi: float | None = None
    snr: float | None = None
    fallback_region: int | None = None
    fallback_preset: int | None = None
    announced_rooms: list[str] = field(default_factory=list)
    dismissed_rooms: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        beacon = self.beacon()
        return f"{self.sender_key}:{beacon.offer_channel.name}"

    @property
    def record_id(self) -> str:
        return hashlib.sha256(self.key.encode("utf-8")).hexdigest()[:8]

    def beacon(self) -> Any:
        from meshtastic.protobuf import mesh_beacon_pb2

        payload = base64.b64decode(self.payload_b64, validate=True)
        return mesh_beacon_pb2.MeshBeacon.FromString(payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: object) -> _BeaconRecord | None:
        if not isinstance(value, Mapping):
            return None
        announced = value.get("announced_rooms", [])
        if not isinstance(announced, Sequence) or isinstance(
            announced, (str, bytes, bytearray)
        ):
            announced = []
        dismissed = value.get("dismissed_rooms", [])
        if not isinstance(dismissed, Sequence) or isinstance(
            dismissed, (str, bytes, bytearray)
        ):
            dismissed = []
        try:
            record = cls(
                sender_key=str(value["sender_key"]),
                sender=str(value["sender"]),
                payload_b64=str(value["payload_b64"]),
                source_channel=_channel_number(value.get("source_channel")),
                first_seen=float(value["first_seen"]),
                last_seen=float(value["last_seen"]),
                count=max(1, int(value.get("count", 1))),
                rssi=_optional_float(value.get("rssi")),
                snr=_optional_float(value.get("snr")),
                fallback_region=_channel_number(value.get("fallback_region")),
                fallback_preset=_channel_number(value.get("fallback_preset")),
                announced_rooms=[
                    room_id for room_id in announced if isinstance(room_id, str)
                ],
                dismissed_rooms=[
                    room_id for room_id in dismissed if isinstance(room_id, str)
                ],
            )
            beacon = record.beacon()
        except (KeyError, TypeError, ValueError, binascii.Error, DecodeError):
            return None
        if not _has_offer(beacon):
            return None
        return record


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, str, bytes, bytearray)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _channel_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        try:
            number = int(value.strip())
        except ValueError:
            return None
        return number if number >= 0 else None
    return None


def _is_mesh_beacon_portnum(value: object) -> bool:
    from meshtastic.protobuf import portnums_pb2

    expected = int(portnums_pb2.PortNum.MESH_BEACON_APP)
    if isinstance(value, str):
        return value.upper() == "MESH_BEACON_APP" or value == str(expected)
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return int(value) == expected
    return False


def _has_offer(beacon: Any) -> bool:
    try:
        return bool(beacon.HasField("offer_channel"))
    except (AttributeError, TypeError, ValueError):
        return False


def _has_optional_field(message: Any, field_name: str) -> bool:
    try:
        return bool(message.HasField(field_name))
    except (AttributeError, TypeError, ValueError):
        return False


def _enum_name(message: Any, field_name: str, number: int) -> str | None:
    try:
        descriptor = message.DESCRIPTOR.fields_by_name[field_name]
        enum = descriptor.enum_type
        value = enum.values_by_number.get(number) if enum is not None else None
        if value is None or value.name in {"UNSET", "UNKNOWN"}:
            return None
        return str(value.name)
    except (AttributeError, KeyError, TypeError):
        return None


def _clean_text(value: object, *, limit: int = 180) -> str:
    if not isinstance(value, str):
        return ""
    collapsed = " ".join(value.replace("\x00", " ").split())
    return collapsed[:limit]


def _markdown_text(value: object, *, limit: int = 180) -> str:
    clean = html.escape(_clean_text(value, limit=limit), quote=False)
    return _MARKDOWN_SPECIAL.sub(r"\\\1", clean)


def _psk_config_value(psk: object) -> str:
    """Render a channel PSK in the same durable forms accepted by Meshtastic CLI."""
    if not isinstance(psk, bytes):
        return "unavailable"
    if len(psk) == 0 or psk == b"\x00":
        return "none"
    if len(psk) == 1:
        if psk == b"\x01":
            return "default"
        return f"simple{psk[0] - 1}"
    return "base64:" + base64.b64encode(psk).decode("ascii")


def _matrix_room_entries() -> list[dict[str, Any]]:
    from mmrelay import matrix_utils

    rooms = matrix_utils.matrix_rooms
    if isinstance(rooms, dict):
        values = rooms.values()
    elif isinstance(rooms, list):
        values = rooms
    else:
        return []
    return [room for room in values if isinstance(room, dict)]


def _age_text(timestamp: float) -> str:
    seconds = max(0, int(time.time() - timestamp))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _qr_image(url: str, label: str | None = None) -> Any:
    import segno
    from PIL import Image

    code = segno.make(url, error="H", micro=False)
    buffer = io.BytesIO()
    # Scale 4 keeps a full offer URL near 260 px on a side, a comfortable
    # inline size for Matrix clients; error correction H survives it.
    code.save(buffer, kind="png", scale=4, border=4)
    buffer.seek(0)
    image = Image.open(buffer)
    image.load()
    if label is None or not label.strip():
        return image

    # segno renders only the symbol, so composite a caption band below the
    # quiet zone; scanning is unaffected by pixels outside the border. A
    # font or canvas failure must never cost the QR itself.
    try:
        from PIL import ImageDraw, ImageFont

        try:
            font = ImageFont.load_default(size=16)
        except TypeError:
            font = ImageFont.load_default()
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = (
            int(v) for v in draw.textbbox((0, 0), label, font=font)
        )
        text_width = right - left
        text_height = bottom - top
        padding = 8
        band_height = text_height + padding * 2
        canvas_width = max(image.width, text_width + padding * 2)
        canvas = Image.new("RGB", (canvas_width, image.height + band_height), "white")
        canvas.paste(image.convert("RGB"), ((canvas_width - image.width) // 2, 0))
        caption = ImageDraw.Draw(canvas)
        caption.text(
            ((canvas_width - text_width) // 2 - left, image.height + padding - top),
            label,
            fill="black",
            font=font,
        )
    except Exception:
        return image
    return canvas


def _qr_label(record: _BeaconRecord) -> str | None:
    """Compose an optional QR caption from the advertised offer fields.

    Mirrors the join-URL resolution: beacon values when the firmware
    carries them, receiver fallbacks when it omits them. Frequency is
    omitted unless a firmware advertises the slot for it.
    """
    try:
        beacon = record.beacon()
    except Exception:
        return None
    parts: list[str] = []
    if _has_optional_field(beacon, "offer_channel"):
        name = _clean_text(beacon.offer_channel.name, limit=24)
        if name:
            parts.append(name)
    try:
        region = int(beacon.offer_region)
    except (AttributeError, TypeError, ValueError):
        region = 0
    if region == 0:
        fallback_region = record.fallback_region
        region = fallback_region if fallback_region is not None else 0
    if _has_optional_field(beacon, "offer_preset"):
        preset: int | None = int(beacon.offer_preset)
    else:
        preset = record.fallback_preset
    region_name = _enum_name(beacon, "offer_region", region) if region else None
    preset_name = (
        _enum_name(beacon, "offer_preset", preset) if preset is not None else None
    )
    radio = " / ".join(value for value in (region_name, preset_name) if value)
    if radio:
        parts.append(radio)
    if _has_optional_field(beacon, "offer_frequency_slot"):
        slot = int(beacon.offer_frequency_slot)
        if slot > 0:
            parts.append(f"slot {slot}")
    label = " · ".join(parts)
    return label or None


class Plugin(BasePlugin):
    plugin_name = "mesh_beacon"
    is_core_plugin = True
    max_data_rows_per_node = _MAX_STORED_BEACONS

    def __init__(self, plugin_name: str | None = None) -> None:
        super().__init__(plugin_name)
        self._connection_subscribed = False
        self._history_loaded = False
        self._listener_lock = threading.Lock()
        self._history_lock = threading.RLock()
        self._persist_lock = threading.Lock()
        self._received_beacons: list[_BeaconRecord] = []

    @property
    def description(self) -> str:
        return "Capture Mesh Beacon invitations and render safe join affordances"

    def get_matrix_commands(self) -> list[str]:
        return ["beacons", "mesh_beacon"]

    def start(self) -> None:
        """Load standing invitations and make a ready connected radio listen."""
        super().start()
        self._load_history()
        if not self._connection_subscribed:
            pub.subscribe(self._on_meshtastic_ready, MESHTASTIC_READY_TOPIC)
            self._connection_subscribed = True

        from mmrelay import meshtastic_utils

        interface = cast(
            _MeshInterfaceProtocol | None, meshtastic_utils.meshtastic_client
        )
        if interface is not None and interface.localNode is not None:
            self._ensure_listening_safely(interface)

    def on_stop(self) -> None:
        if not self._connection_subscribed:
            return
        try:
            pub.unsubscribe(self._on_meshtastic_ready, MESHTASTIC_READY_TOPIC)
        except Exception:
            self.logger.debug(
                "Mesh Beacon readiness callback was already unsubscribed",
                exc_info=True,
            )
        self._connection_subscribed = False

    def _on_meshtastic_ready(self, interface: _MeshInterfaceProtocol) -> None:
        self._ensure_listening_safely(interface)

    def _ensure_listening_safely(self, interface: _MeshInterfaceProtocol) -> None:
        try:
            with self._listener_lock:
                changed = self.ensure_listening(interface)
        except MeshBeaconCapabilityError as exc:
            self.logger.warning("Mesh Beacon listener unavailable: %s", exc)
        except Exception:
            self.logger.exception("Failed to enable Mesh Beacon listening")
        else:
            if changed:
                self.logger.info("Submitted Mesh Beacon listener enablement to radio")
            else:
                self.logger.debug("Mesh Beacon listening is already enabled")

    def ensure_listening(self, interface: _MeshInterfaceProtocol) -> bool:
        """Set only the listen flag and preserve every other Mesh Beacon setting."""
        local_node = interface.localNode
        if local_node is None:
            raise MeshBeaconCapabilityError("connected interface has no local node")
        module_config = local_node.moduleConfig
        if module_config is None:
            raise MeshBeaconCapabilityError("module configuration is not available")
        fields = getattr(
            getattr(module_config, "DESCRIPTOR", None), "fields_by_name", {}
        )
        if "mesh_beacon" not in fields:
            raise MeshBeaconCapabilityError(
                "the installed Meshtastic client does not expose mesh_beacon"
            )
        try:
            supported = bool(module_config.HasField("mesh_beacon"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise MeshBeaconCapabilityError(
                "the connected radio did not expose Mesh Beacon configuration"
            ) from exc
        if not supported:
            raise MeshBeaconCapabilityError(
                "the connected firmware did not expose Mesh Beacon configuration"
            )

        beacon_config = module_config.mesh_beacon
        original_flags = int(beacon_config.flags)
        if original_flags & _LISTEN_FLAG:
            return False
        beacon_config.flags = original_flags | _LISTEN_FLAG
        try:
            local_node.writeConfig("mesh_beacon")
        except Exception:
            beacon_config.flags = original_flags
            raise
        return True

    def _load_history(self) -> None:
        if self._history_loaded:
            return
        self._history_loaded = True
        try:
            stored = self.get_node_data(_HISTORY_STORAGE_KEY)
        except Exception:
            self.logger.exception("Failed to load saved Mesh Beacon invitations")
            return
        if not isinstance(stored, list):
            return
        records: list[_BeaconRecord] = []
        keys: set[str] = set()
        for item in stored:
            record = _BeaconRecord.from_dict(item)
            if record is None or record.key in keys:
                continue
            records.append(record)
            keys.add(record.key)
            if len(records) >= _MAX_STORED_BEACONS:
                break
        with self._history_lock:
            self._received_beacons = records

    def _history_snapshot(self) -> list[dict[str, Any]]:
        with self._history_lock:
            return [record.to_dict() for record in self._received_beacons]

    def _persist_history_sync(self) -> None:
        """Serialize snapshots with their writes so older state cannot land last."""
        with self._persist_lock:
            snapshot = self._history_snapshot()
            self.set_node_data(_HISTORY_STORAGE_KEY, snapshot, raise_on_error=True)

    async def _persist_history(self) -> bool:
        try:
            await asyncio.to_thread(self._persist_history_sync)
        except Exception:
            self.logger.exception("Failed to persist Mesh Beacon invitations")
            return False
        return True

    @staticmethod
    def _receiver_radio_fallback() -> tuple[int | None, int | None]:
        from mmrelay import meshtastic_utils

        interface = cast(
            _MeshInterfaceProtocol | None, meshtastic_utils.meshtastic_client
        )
        local_node = interface.localNode if interface is not None else None
        local_config = local_node.localConfig if local_node is not None else None
        lora = local_config.lora if local_config is not None else None
        if lora is None or not bool(getattr(lora, "use_preset", False)):
            return None, None
        region = _channel_number(getattr(lora, "region", None))
        preset = _channel_number(getattr(lora, "modem_preset", None))
        return region, preset

    def _store_received_beacon(
        self,
        *,
        sender: str,
        sender_key: str,
        beacon: Any,
        source_channel: int | None,
        rssi: float | None,
        snr: float | None,
    ) -> tuple[_BeaconRecord, bool]:
        payload_b64 = base64.b64encode(beacon.SerializeToString()).decode("ascii")
        identity = f"{sender_key}:{beacon.offer_channel.name}"
        fallback_region, fallback_preset = self._receiver_radio_fallback()
        now = time.time()
        with self._history_lock:
            for index, record in enumerate(self._received_beacons):
                if record.key != identity:
                    continue
                payload_changed = record.payload_b64 != payload_b64
                record.sender = sender
                record.payload_b64 = payload_b64
                record.source_channel = source_channel
                record.last_seen = now
                record.count += 1
                record.rssi = rssi if rssi is not None else record.rssi
                record.snr = snr if snr is not None else record.snr
                record.fallback_region = (
                    fallback_region
                    if fallback_region is not None
                    else record.fallback_region
                )
                record.fallback_preset = (
                    fallback_preset
                    if fallback_preset is not None
                    else record.fallback_preset
                )
                if payload_changed:
                    record.announced_rooms.clear()
                    record.dismissed_rooms.clear()
                self._received_beacons.insert(0, self._received_beacons.pop(index))
                return record, False

            record = _BeaconRecord(
                sender_key=sender_key,
                sender=sender,
                payload_b64=payload_b64,
                source_channel=source_channel,
                first_seen=now,
                last_seen=now,
                rssi=rssi,
                snr=snr,
                fallback_region=fallback_region,
                fallback_preset=fallback_preset,
            )
            self._received_beacons.insert(0, record)
            del self._received_beacons[_MAX_STORED_BEACONS:]
            return record, True

    async def handle_meshtastic_message(
        self,
        packet: dict[str, Any],
        formatted_message: str | None,
        longname: str | None,
        meshnet_name: str | None,
    ) -> bool:
        """Capture actionable decoded Mesh Beacon invitations."""
        _ = formatted_message, meshnet_name
        decoded = packet.get("decoded")
        if not isinstance(decoded, Mapping) or not _is_mesh_beacon_portnum(
            decoded.get("portnum")
        ):
            return False

        payload = decoded.get("meshbeacon")
        if not isinstance(payload, Mapping):
            self.logger.warning("Received Mesh Beacon packet without decoded payload")
            return True
        if payload.get("error"):
            self.logger.warning("Could not decode received Mesh Beacon")
            return True
        beacon = payload.get("raw")
        if beacon is None or not hasattr(beacon, "SerializeToString"):
            self.logger.warning(
                "Decoded Mesh Beacon did not include its protobuf payload"
            )
            return True
        if not _has_offer(beacon):
            self.logger.debug("Ignoring Mesh Beacon without a channel offer")
            return True

        sender_num = packet.get("from")
        if isinstance(sender_num, int) and sender_num == await asyncio.to_thread(
            self.get_my_node_id
        ):
            return True
        sender_key = str(
            sender_num if sender_num is not None else packet.get("fromId") or "unknown"
        )
        sender = (
            _clean_text(longname or packet.get("fromId") or sender_key, limit=80)
            or sender_key
        )
        packet_channel = packet["channel"] if "channel" in packet else 0
        record, _is_new = self._store_received_beacon(
            sender=sender,
            sender_key=sender_key,
            beacon=beacon,
            source_channel=_channel_number(packet_channel),
            rssi=_optional_float(packet.get("rxRssi")),
            snr=_optional_float(packet.get("rxSnr")),
        )
        await self._persist_history()

        announce = self.config.get("announce", True)
        if not isinstance(announce, bool):
            self.logger.error("mesh_beacon.announce must be true or false")
            return True
        if not announce:
            return True
        repeats = self.config.get("announce_repeats", False)
        if not isinstance(repeats, bool):
            self.logger.error("mesh_beacon.announce_repeats must be true or false")
            repeats = False

        rooms = self._announcement_rooms(record.source_channel)
        if not rooms:
            self.logger.debug(
                "Captured Mesh Beacon %s from %s; no Matrix room is mapped "
                "for announcement",
                record.record_id,
                sender,
            )
            return True

        body = self._announcement_text(record)
        changed = False
        for room_id in rooms:
            with self._history_lock:
                if room_id in record.dismissed_rooms:
                    continue
                if not repeats and room_id in record.announced_rooms:
                    continue
            try:
                response = await self.send_matrix_message(room_id, body, formatted=True)
            except Exception:
                self.logger.exception(
                    "Failed to announce Mesh Beacon in Matrix room %s", room_id
                )
                continue
            if isinstance(response, RoomSendError) or response is None:
                self.logger.warning(
                    "Failed to announce Mesh Beacon in Matrix room %s", room_id
                )
                continue
            with self._history_lock:
                if room_id not in record.announced_rooms:
                    record.announced_rooms.append(room_id)
                    changed = True
        if changed:
            await self._persist_history()
        return True

    def _configured_relay_channel(self) -> int | None:
        configured = self.config.get("relay_channel")
        if configured is None:
            return None
        channel = _channel_number(configured)
        if channel is None:
            self.logger.error(
                "mesh_beacon.relay_channel must be a non-negative integer"
            )
            return -1
        return channel

    def _announcement_rooms(self, source_channel: int | None) -> list[str]:
        relay_channel = self._configured_relay_channel()
        target_channel = relay_channel if relay_channel is not None else source_channel
        if target_channel is None:
            return []
        return [
            room["id"]
            for room in _matrix_room_entries()
            if _channel_number(room.get("meshtastic_channel")) == target_channel
            and isinstance(room.get("id"), str)
            and room["id"]
        ]

    @staticmethod
    def _room_channel(room_id: str) -> int | None:
        for room in _matrix_room_entries():
            if room.get("id") == room_id:
                return _channel_number(room.get("meshtastic_channel"))
        return None

    def _visible_records(self, room_id: str) -> list[_BeaconRecord]:
        room_channel = self._room_channel(room_id)
        if room_channel is None:
            return []
        relay_channel = self._configured_relay_channel()
        with self._history_lock:
            records = [
                record
                for record in self._received_beacons
                if room_id not in record.dismissed_rooms
            ]
        if relay_channel is not None and room_channel == relay_channel:
            return records
        return [record for record in records if record.source_channel == room_channel]

    @staticmethod
    def _can_manage_history(room: MatrixRoom, sender: object) -> bool:
        """Use the room's moderation policy for room-scoped destructive actions."""
        if not isinstance(sender, str) or not sender:
            return False
        try:
            return bool(room.power_levels.can_user_redact(sender))
        except (AttributeError, TypeError, ValueError):
            return False

    def _announcement_text(self, record: _BeaconRecord) -> str:
        beacon = record.beacon()
        name = _markdown_text(beacon.offer_channel.name or "Default", limit=80)
        sender = _markdown_text(record.sender, limit=80)
        lines = ["### 📡 Mesh invitation", f"**{name}** from **{sender}**"]
        message = _markdown_text(beacon.message, limit=180)
        if message:
            lines.append(f"> {message}")
        metadata = self._radio_and_signal_text(record, beacon)
        if metadata:
            lines.append(metadata)
        lines.append(
            f"Saved as `{record.record_id}` · `!beacons show {record.record_id}` · "
            f"`!beacons qr {record.record_id}`"
        )
        return "\n\n".join(lines)

    @staticmethod
    def _radio_and_signal_text(record: _BeaconRecord, beacon: Any) -> str:
        parts: list[str] = []
        region = _enum_name(beacon, "offer_region", int(beacon.offer_region))
        preset = (
            _enum_name(beacon, "offer_preset", int(beacon.offer_preset))
            if _has_optional_field(beacon, "offer_preset")
            else None
        )
        radio = " / ".join(value for value in (region, preset) if value)
        if radio:
            parts.append(f"`{radio}`")
        if _has_optional_field(beacon, "offer_frequency_slot"):
            slot = int(beacon.offer_frequency_slot)
            if slot > 0:
                parts.append(f"slot `{slot}`")
        signal: list[str] = []
        if record.rssi is not None:
            signal.append(f"RSSI {record.rssi:g} dBm")
        if record.snr is not None:
            signal.append(f"SNR {record.snr:g} dB")
        if signal:
            parts.append(" · ".join(signal))
        return " · ".join(parts)

    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
    ) -> bool:
        """Render saved invitations and explicit join artifacts on command."""
        _ = full_message
        parsed = self.get_matching_matrix_command_with_args(event)
        if parsed is None:
            return False
        _command, args = parsed
        parts = args.split()
        subcommand = parts[0].lower() if parts else "list"
        argument = parts[1] if len(parts) > 1 else ""
        records = self._visible_records(room.room_id)
        can_manage = self._can_manage_history(room, getattr(event, "sender", None))

        if subcommand in {"list", "help"}:
            reply = self._beacon_list_text(records)
        elif subcommand in {"show", "detail"} and argument:
            record = self._find_beacon_record(argument, records)
            reply = (
                self._beacon_detail_text(record, can_manage=can_manage)
                if record is not None
                else self._unknown_beacon_text(argument)
            )
        elif subcommand in {"url", "join"} and argument:
            record = self._find_beacon_record(argument, records)
            reply = (
                self._beacon_url_text(record)
                if record is not None
                else self._unknown_beacon_text(argument)
            )
        elif subcommand == "qr" and argument:
            record = self._find_beacon_record(argument, records)
            if record is None:
                reply = self._unknown_beacon_text(argument)
            else:
                await self._send_beacon_qr(room.room_id, record)
                return True
        elif subcommand == "dismiss" and argument:
            record = self._find_beacon_record(argument, records)
            if record is None:
                reply = self._unknown_beacon_text(argument)
            elif not can_manage:
                reply = self._management_denied_text()
            else:
                self._dismiss_record(record, room.room_id)
                if await self._persist_history():
                    reply = (
                        f"Dismissed Mesh Beacon `{record.record_id}` from this room."
                    )
                else:
                    reply = self._persistence_failed_text()
        elif subcommand == "clear":
            if not can_manage:
                reply = self._management_denied_text()
            else:
                count = self._clear_visible_records(records, room.room_id)
                if await self._persist_history():
                    suffix = "s" if count != 1 else ""
                    reply = (
                        f"Cleared {count} saved Mesh Beacon invitation{suffix} "
                        "from this room."
                    )
                else:
                    reply = self._persistence_failed_text()
        elif len(parts) == 1:
            record = self._find_beacon_record(subcommand, records)
            reply = (
                self._beacon_detail_text(record, can_manage=can_manage)
                if record is not None
                else self._usage_text(can_manage=can_manage)
            )
        else:
            reply = self._usage_text(can_manage=can_manage)

        await self.send_matrix_message(room.room_id, reply, formatted=True)
        return True

    @staticmethod
    def _find_beacon_record(
        selector: str, records: Sequence[_BeaconRecord]
    ) -> _BeaconRecord | None:
        normalized = selector.strip().lower().removeprefix("#")
        for record in records:
            if record.record_id == normalized:
                return record
        try:
            index = int(normalized)
        except ValueError:
            return None
        if 1 <= index <= len(records):
            return records[index - 1]
        return None

    def _dismiss_record(self, target: _BeaconRecord, room_id: str) -> None:
        with self._history_lock:
            if room_id not in target.dismissed_rooms:
                target.dismissed_rooms.append(room_id)

    def _clear_visible_records(
        self, visible: Sequence[_BeaconRecord], room_id: str
    ) -> int:
        with self._history_lock:
            for record in visible:
                if room_id not in record.dismissed_rooms:
                    record.dismissed_rooms.append(room_id)
        return len(visible)

    @staticmethod
    def _persistence_failed_text() -> str:
        return (
            "Mesh Beacon invitations are hidden in memory, but saving failed. "
            "They may reappear after restart; check storage before restarting."
        )

    @staticmethod
    def _management_denied_text() -> str:
        return (
            "Dismissing saved Mesh Beacon invitations requires Matrix room "
            "moderation permission (the ability to redact events)."
        )

    @staticmethod
    def _unknown_beacon_text(selector: str) -> str:
        safe = _markdown_text(selector, limit=40)
        return (
            f"No saved Mesh Beacon matches `{safe}`. "
            "Use `!beacons` to list invitations."
        )

    @staticmethod
    def _usage_text(*, can_manage: bool = False) -> str:
        commands = (
            "Usage: `!beacons [list]`, `!beacons show ID`, `!beacons url ID`, "
            "or `!beacons qr ID`"
        )
        if can_manage:
            commands += ", plus moderator-only `!beacons dismiss ID` / `!beacons clear`"
        return commands + "."

    @staticmethod
    def _beacon_list_text(records: Sequence[_BeaconRecord]) -> str:
        if not records:
            return (
                "### 📡 Mesh invitations\n\n"
                "No Mesh Beacon invitations have been captured here yet."
            )
        lines = ["### 📡 Mesh invitations", ""]
        for index, record in enumerate(records, 1):
            beacon = record.beacon()
            name = _markdown_text(beacon.offer_channel.name or "Default", limit=70)
            sender = _markdown_text(record.sender, limit=70)
            lines.append(
                f"{index}. **{name}** · {sender} · `{record.record_id}` · "
                f"{record.count}× · {_age_text(record.last_seen)}"
            )
        lines.extend(
            [
                "",
                "Use `!beacons show ID` for details, `!beacons url ID` for a "
                "Meshtastic share URL/`--seturl`, or `!beacons qr ID` for a QR code.",
            ]
        )
        return "\n".join(lines)

    def _beacon_detail_text(
        self, record: _BeaconRecord, *, can_manage: bool = False
    ) -> str:
        beacon = record.beacon()
        name = _markdown_text(beacon.offer_channel.name or "Default", limit=80)
        sender = _markdown_text(record.sender, limit=80)
        lines = [
            f"### 📡 {name}",
            f"**From:** {sender}  ",
            f"**ID:** `{record.record_id}`  ",
        ]
        message = _markdown_text(beacon.message, limit=240)
        if message:
            lines.append(f"**Message:** {message}  ")
        region = _enum_name(beacon, "offer_region", int(beacon.offer_region))
        preset = (
            _enum_name(beacon, "offer_preset", int(beacon.offer_preset))
            if _has_optional_field(beacon, "offer_preset")
            else None
        )
        if region:
            lines.append(f"**Region:** `{region}`  ")
        if preset:
            lines.append(f"**Preset:** `{preset}`  ")
        if _has_optional_field(beacon, "offer_frequency_slot"):
            slot = int(beacon.offer_frequency_slot)
            if slot > 0:
                lines.append(f"**Frequency slot:** `{slot}`  ")
        lines.append(f"**PSK:** `{_psk_config_value(beacon.offer_channel.psk)}`  ")
        if record.source_channel is not None:
            lines.append(f"**Received on local channel:** `{record.source_channel}`  ")
        if record.rssi is not None or record.snr is not None:
            signal = []
            if record.rssi is not None:
                signal.append(f"RSSI {record.rssi:g} dBm")
            if record.snr is not None:
                signal.append(f"SNR {record.snr:g} dB")
            lines.append(f"**Signal:** {' · '.join(signal)}  ")
        lines.append(
            f"**Seen:** {record.count}× · first {_age_text(record.first_seen)} · "
            f"last {_age_text(record.last_seen)}"
        )
        actions = (
            f"`!beacons url {record.record_id}` · " f"`!beacons qr {record.record_id}`"
        )
        if can_manage:
            actions += f" · `!beacons dismiss {record.record_id}`"
        lines.extend(
            [
                "",
                "_Mesh Beacons are unsigned, zero-hop RF advertisements. Review the "
                "invitation before applying it._",
                "",
                actions,
            ]
        )
        return "\n".join(lines)

    def _beacon_url_text(self, record: _BeaconRecord) -> str:
        url = self._beacon_join_url(record)
        if url is None:
            return (
                f"Mesh Beacon `{record.record_id}` could not be encoded as a "
                "safe ChannelSet."
            )
        name = _markdown_text(record.beacon().offer_channel.name or "Default", limit=80)
        return "\n".join(
            [
                f"### 🔗 {name}",
                "",
                url,
                "",
                "Equivalent CLI command:",
                f"`meshtastic --seturl '{url}'`",
                "",
                "_This URL contains the advertised channel credentials. "
                "`--seturl` replaces "
                "the channel set and may retune the radio. Review it before applying._",
            ]
        )

    @staticmethod
    def _beacon_join_url(record: _BeaconRecord) -> str | None:
        from meshtastic.protobuf import apponly_pb2, config_pb2

        try:
            beacon = record.beacon()
            if not _has_offer(beacon):
                return None
            channel_set = apponly_pb2.ChannelSet()
            settings = channel_set.settings.add()
            settings.CopyFrom(beacon.offer_channel)
            settings.module_settings.position_precision = 0

            region = int(beacon.offer_region)
            if region == int(config_pb2.Config.LoRaConfig.RegionCode.UNSET):
                fallback_region = record.fallback_region
                region = fallback_region if fallback_region is not None else 0
            preset: int | None
            if _has_optional_field(beacon, "offer_preset"):
                preset = int(beacon.offer_preset)
            else:
                preset = record.fallback_preset

            if region > 0 and preset is not None:
                lora = channel_set.lora_config
                lora.use_preset = True
                lora.modem_preset = cast(
                    "config_pb2.Config.LoRaConfig.ModemPreset.ValueType", preset
                )
                lora.region = cast(
                    "config_pb2.Config.LoRaConfig.RegionCode.ValueType", region
                )
                lora.hop_limit = 3
                lora.tx_enabled = True
                if _has_optional_field(beacon, "offer_frequency_slot"):
                    slot = int(beacon.offer_frequency_slot)
                    if slot > 0:
                        lora.channel_num = slot
            payload = channel_set.SerializeToString()
        except (AttributeError, TypeError, ValueError, binascii.Error, DecodeError):
            return None
        encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        return _JOIN_URL_PREFIX + encoded

    async def _send_beacon_qr(self, room_id: str, record: _BeaconRecord) -> None:
        url = self._beacon_join_url(record)
        if url is None:
            await self.send_matrix_message(
                room_id,
                f"Mesh Beacon `{record.record_id}` could not be encoded as a "
                "QR invitation.",
                formatted=True,
            )
            return
        qr_label = self.config.get("qr_label", True)
        if not isinstance(qr_label, bool):
            self.logger.error("mesh_beacon.qr_label must be true or false")
            qr_label = False
        label = _qr_label(record) if qr_label else None
        try:
            if label:
                image = await asyncio.to_thread(_qr_image, url, label)
            else:
                image = await asyncio.to_thread(_qr_image, url)
        except (ImportError, OSError, ValueError):
            self.logger.exception("Failed to render Mesh Beacon QR image")
            await self.send_matrix_message(
                room_id,
                "QR rendering failed; use the URL from `!beacons url ID` instead.",
                formatted=True,
            )
            return

        from mmrelay.matrix_utils import ImageUploadError, connect_matrix, send_image

        try:
            matrix_client = await connect_matrix()
            if matrix_client is None:
                raise ImageUploadError("Matrix client unavailable")
            await self.send_matrix_message(
                room_id,
                f"### 📱 Mesh Beacon `{record.record_id}`\n\n"
                "Scan to review this invitation in a Meshtastic client.",
                formatted=True,
            )
            room = matrix_client.rooms.get(room_id)
            if room is None or not isinstance(room.encrypted, bool):
                raise ImageUploadError("Room encryption state unavailable")
            filename = f"mesh-beacon-{record.record_id}.png"
            if room.encrypted:
                buffer = io.BytesIO()
                await asyncio.to_thread(image.save, buffer, format="PNG")
                buffer.seek(0)
                response, encryption = await matrix_client.upload(
                    buffer,
                    content_type="image/png",
                    filename=filename,
                    filesize=len(buffer.getbuffer()),
                    encrypt=True,
                )
                content_uri = getattr(response, "content_uri", None)
                if (
                    not isinstance(content_uri, str)
                    or not content_uri
                    or not encryption
                ):
                    raise ImageUploadError(response)
                content = {
                    "msgtype": "m.image",
                    "body": filename,
                    "file": {**encryption, "url": content_uri},
                    "info": {"mimetype": "image/png"},
                }
                result = await matrix_client.room_send(
                    room_id=room_id,
                    message_type="m.room.message",
                    content=content,
                )
                if isinstance(result, RoomSendError):
                    raise ImageUploadError(result)
            else:
                await send_image(matrix_client, room_id, image, filename=filename)
        except ImageUploadError:
            self.logger.exception("Failed to send Mesh Beacon QR image")
            await self.send_matrix_message(
                room_id,
                "Failed to upload the QR image; use `!beacons url ID` instead.",
                formatted=True,
            )
        except Exception:
            self.logger.exception(
                "Unexpected failure while sending Mesh Beacon QR image"
            )
            await self.send_matrix_message(
                room_id,
                "Failed to send the QR image; use `!beacons url ID` instead.",
                formatted=True,
            )
