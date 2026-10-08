"""Real-protobuf contracts for the listening-first Mesh Beacon plugin."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - SDK probes run only the project interpreter
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_PROBE = r"""
import base64
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import tests.mocks
from mmrelay.plugins.mesh_beacon_plugin import Plugin, _BeaconRecord

# The suite imports MMRelay against lightweight Meshtastic doubles. Replace only
# that namespace here so the contract below exercises the installed/client
# protobuf descriptors without requiring radio or Matrix transports.
for name in list(sys.modules):
    if (
        name == "meshtastic"
        or name.startswith("meshtastic.")
        or name == "pubsub"
        or name.startswith("pubsub.")
    ):
        del sys.modules[name]

from meshtastic.protobuf import apponly_pb2, config_pb2, localonly_pb2, mesh_beacon_pb2

module_config = localonly_pb2.LocalModuleConfig()
beacon_config = module_config.mesh_beacon
beacon_config.flags = 0b110
beacon_config.broadcast_message = "preserve me"
beacon_config.broadcast_offer_channel.name = "Offered"
beacon_config.broadcast_offer_channel.psk = b"\x01"
target = beacon_config.broadcast_targets.add()
target.preset = config_pb2.Config.LoRaConfig.MEDIUM_TURBO
target.channel_index = 0
before = localonly_pb2.LocalModuleConfig.FromString(module_config.SerializeToString())

node = SimpleNamespace(
    moduleConfig=module_config,
    localConfig=SimpleNamespace(
        lora=config_pb2.Config.LoRaConfig(
            use_preset=True,
            region=config_pb2.Config.LoRaConfig.US,
            modem_preset=config_pb2.Config.LoRaConfig.LONG_FAST,
        )
    ),
    writeConfig=Mock(),
)
interface = SimpleNamespace(localNode=node)
plugin = Plugin()
plugin.config = {"active": True}

assert plugin.ensure_listening(interface) is True
node.writeConfig.assert_called_once_with("mesh_beacon")
assert module_config.mesh_beacon.flags == (before.mesh_beacon.flags | 1)
expected = localonly_pb2.LocalModuleConfig.FromString(before.SerializeToString())
expected.mesh_beacon.flags |= 1
assert module_config.SerializeToString() == expected.SerializeToString()

# Re-applying listener readiness is a no-op.
node.writeConfig.reset_mock()
assert plugin.ensure_listening(interface) is False
node.writeConfig.assert_not_called()

# Reconstruct a share URL from the exact advertised ChannelSettings. The radio
# portion starts from Meshtastic's safe join defaults instead of copying the
# relay node's current RF pins: use_preset, hop_limit=3, tx_enabled=true.
beacon = mesh_beacon_pb2.MeshBeacon(
    message="Join us",
    offer_region=config_pb2.Config.LoRaConfig.US,
    offer_preset=config_pb2.Config.LoRaConfig.MEDIUM_TURBO,
)
beacon.offer_channel.name = "Community"
beacon.offer_channel.psk = b"join-key"
beacon.offer_channel.module_settings.position_precision = 16
if "offer_frequency_slot" in beacon.DESCRIPTOR.fields_by_name:
    beacon.offer_frequency_slot = 42
record = _BeaconRecord(
    sender_key="123",
    sender="Some Node",
    payload_b64=base64.b64encode(beacon.SerializeToString()).decode("ascii"),
    source_channel=0,
    first_seen=1,
    last_seen=2,
    fallback_region=int(config_pb2.Config.LoRaConfig.US),
    fallback_preset=int(config_pb2.Config.LoRaConfig.LONG_FAST),
)
url = Plugin._beacon_join_url(record)
assert url is not None and url.startswith("https://meshtastic.org/e/#")
fragment = url.split("#", 1)[1]
payload = base64.urlsafe_b64decode(fragment + "=" * (-len(fragment) % 4))
shared = apponly_pb2.ChannelSet.FromString(payload)
assert len(shared.settings) == 1
assert shared.settings[0].name == "Community"
assert shared.settings[0].psk == b"join-key"
assert shared.settings[0].module_settings.position_precision == 0
assert shared.HasField("lora_config")
assert shared.lora_config.use_preset is True
assert shared.lora_config.modem_preset == config_pb2.Config.LoRaConfig.MEDIUM_TURBO
assert shared.lora_config.region == config_pb2.Config.LoRaConfig.US
assert shared.lora_config.hop_limit == 3
assert shared.lora_config.tx_enabled is True
if beacon.HasField("offer_frequency_slot"):
    assert shared.lora_config.channel_num == 42

# When the beacon omits preset/region, use the receiver snapshot captured with
# the invitation so a later recall does not depend on whatever radio is now attached.
beacon2 = mesh_beacon_pb2.MeshBeacon()
beacon2.offer_channel.name = "Fallback"
beacon2.offer_channel.psk = b"\x01"
record2 = _BeaconRecord(
    sender_key="456",
    sender="Another Node",
    payload_b64=base64.b64encode(beacon2.SerializeToString()).decode("ascii"),
    source_channel=0,
    first_seen=1,
    last_seen=2,
    fallback_region=int(config_pb2.Config.LoRaConfig.US),
    fallback_preset=int(config_pb2.Config.LoRaConfig.LONG_FAST),
)
url2 = Plugin._beacon_join_url(record2)
fragment2 = url2.split("#", 1)[1]
payload2 = base64.urlsafe_b64decode(fragment2 + "=" * (-len(fragment2) % 4))
shared2 = apponly_pb2.ChannelSet.FromString(payload2)
assert shared2.lora_config.region == config_pb2.Config.LoRaConfig.US
assert shared2.lora_config.modem_preset == config_pb2.Config.LoRaConfig.LONG_FAST
"""


def test_real_mesh_beacon_listener_and_join_url_contract(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            filter(
                None,
                [
                    str(repository),
                    str(repository / "src"),
                    os.environ.get("MMRELAY_MTJK_SOURCE", ""),
                    os.environ.get("MMRELAY_TEST_SHIMS", ""),
                ],
            )
        ),
        "MMRELAY_HOME": str(tmp_path),
    }
    result = subprocess.run(  # nosec B603 - interpreter and generated probe only
        [sys.executable, "-W", "error", "-c", textwrap.dedent(_PROBE)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_qr_upload_encrypts_png_with_installed_matrix_provider(tmp_path: Path) -> None:
    script = r"""
import asyncio
import base64
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nio import AsyncClient, AsyncClientConfig, UploadResponse
from nio.crypto.attachments import decrypt_attachment
from PIL import Image
from meshtastic.protobuf import mesh_beacon_pb2
from mmrelay.plugins.mesh_beacon_plugin import Plugin, _BeaconRecord

async def main():
    beacon = mesh_beacon_pb2.MeshBeacon()
    beacon.offer_channel.name = "Invitation"
    beacon.offer_channel.psk = b"invitation-key!"
    record = _BeaconRecord(
        sender_key="123", sender="RF node", source_channel=0,
        first_seen=1, last_seen=1,
        payload_b64=base64.b64encode(beacon.SerializeToString()).decode(),
    )
    plugin = Plugin()
    plugin.send_matrix_message = AsyncMock(return_value=object())
    url = plugin._beacon_join_url(record)
    from mmrelay.plugins.mesh_beacon_plugin import _qr_image
    plain = _qr_image(url)
    client = AsyncClient("https://matrix.example", config=AsyncClientConfig(encryption_enabled=False))
    client.restore_login("@relay:example", "TEST", "test-token")
    client.rooms["!room:example"] = SimpleNamespace(encrypted=True)

    async def run_upload() -> bytes:
        uploaded = bytearray()

        async def transport(*args, **kwargs):
            assert kwargs["content_type"] == "application/octet-stream"
            async for chunk in await kwargs["data_provider"](0, 0):
                uploaded.extend(chunk)
            return UploadResponse("mxc://example/qr")

        client._send = transport
        client.room_send = AsyncMock(return_value=object())
        with patch("mmrelay.matrix_utils.connect_matrix", AsyncMock(return_value=client)):
            await plugin._send_beacon_qr("!room:example", record)
        content = client.room_send.await_args.kwargs["content"]
        assert "url" not in content
        info = content["file"]
        assert info["url"] == "mxc://example/qr"
        ciphertext = bytes(uploaded)
        assert ciphertext and not ciphertext.startswith(b"\x89PNG")
        return decrypt_attachment(ciphertext, info["key"]["k"], info["hashes"]["sha256"], info["iv"])

    try:
        png = await run_upload()
        assert png.startswith(b"\x89PNG")
        with Image.open(io.BytesIO(png)) as image:
            assert image.width > 0 and image.height > 0
            # The default caption band labels the offer below the code.
            assert image.height > plain.height and image.width >= plain.width
        plugin.config = {"qr_label": False}
        png_plain = await run_upload()
        with Image.open(io.BytesIO(png_plain)) as image_plain:
            assert (image_plain.width, image_plain.height) == (plain.width, plain.height)
    finally:
        await client.close()

asyncio.run(main())
"""
    environment = {**os.environ, "MMRELAY_HOME": str(tmp_path)}
    result = subprocess.run(  # nosec B603 - interpreter and generated probe only
        [sys.executable, "-W", "error", "-c", textwrap.dedent(script)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
