"""Exercise real protobuf messages and pubsub without the suite's global mocks."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - Isolate real dependencies from global test mocks.
import sys
from pathlib import Path

import pytest

_PROBE = """
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from meshtastic.protobuf import channel_pb2, config_pb2, module_config_pb2
from pubsub import pub

from mmrelay import meshtastic_utils
from mmrelay.plugins.mesh_beacon_plugin import MeshBeaconConfigError, Plugin

file_proto = descriptor_pb2.FileDescriptorProto()
module_config_pb2.DESCRIPTOR.CopyToProto(file_proto)
module = next(m for m in file_proto.message_type if m.name == "ModuleConfig")
beacon = next(m for m in module.nested_type if m.name == "MeshBeaconConfig")
target = next(m for m in beacon.nested_type if m.name == "BroadcastTarget")

def remove_optional_field(message, name):
    field = next((field for field in message.field if field.name == name), None)
    if field is None:
        return
    oneof_index = field.oneof_index if field.proto3_optional else None
    for index, candidate in enumerate(message.field):
        if candidate.name == name:
            del message.field[index]
            break
    if oneof_index is not None:
        del message.oneof_decl[oneof_index]
        for candidate in message.field:
            if (
                candidate.HasField("oneof_index")
                and candidate.oneof_index > oneof_index
            ):
                candidate.oneof_index -= 1

def add_optional_uint32(message, name, number):
    if any(field.name == name for field in message.field):
        return
    message.oneof_decl.add(name="_" + name)
    message.field.add(
        name=name, number=number, type=13, label=1,
        proto3_optional=True, oneof_index=len(message.oneof_decl) - 1,
    )

if sys.argv[1] == "initial":
    remove_optional_field(beacon, "broadcast_offer_frequency_slot")
    remove_optional_field(target, "frequency_slot")
else:
    add_optional_uint32(beacon, "broadcast_offer_frequency_slot", 2)
    add_optional_uint32(target, "frequency_slot", 5)

pool = descriptor_pool.DescriptorPool()
def add_dependencies(descriptor):
    for dependency in descriptor.dependencies:
        add_dependencies(dependency)
    if descriptor.name != module_config_pb2.DESCRIPTOR.name:
        pool.AddSerializedFile(descriptor.serialized_pb)
add_dependencies(module_config_pb2.DESCRIPTOR)
pool.Add(file_proto)
beacon_type = message_factory.GetMessageClass(
    pool.FindMessageTypeByName(
        module_config_pb2.ModuleConfig.MeshBeaconConfig.DESCRIPTOR.full_name
    )
)

beacon = beacon_type(flags=13)
if sys.argv[1] == "frequency_slots":
    beacon.broadcast_offer_frequency_slot = 23
    beacon.broadcast_targets.add(preset=4, channel_index=0, frequency_slot=12)
node = SimpleNamespace(
    moduleConfig=SimpleNamespace(mesh_beacon=beacon, HasField=lambda name: True),
    localConfig=SimpleNamespace(lora=config_pb2.Config.LoRaConfig(
        use_preset=True, region=1, modem_preset=0,
    )),
    channels=[channel_pb2.Channel(
        index=0, role=channel_pb2.Channel.PRIMARY,
        settings=channel_pb2.ChannelSettings(name="Home", psk=b"\\x01"),
    )],
    writeConfig=Mock(),
)
interface = SimpleNamespace(localNode=node, get_allowed_modem_presets=lambda r: (0, 4))
plugin = Plugin()
plugin.config = {
    "broadcast": True, "message": "é" * 30, "offer_channel_index": 0,
    "targets": [{"preset": "MEDIUM_FAST", "channel_index": 0}],
}
meshtastic_utils.meshtastic_client = None
plugin.start()
try:
    pub.sendMessage("meshtastic.connection.established", interface=interface)
    node.writeConfig.assert_called_once_with("mesh_beacon")
    assert beacon.flags == 15
    assert len(beacon.broadcast_message.encode("utf-8")) == 60
    assert beacon.HasField("broadcast_offer_preset")  # LONG_FAST is numeric zero.
    assert beacon.broadcast_offer_preset == 0
    assert beacon.broadcast_targets[0].HasField("channel_index")  # Slot zero is explicit.
    assert beacon.broadcast_targets[0].channel_index == 0
    if sys.argv[1] == "frequency_slots":
        assert not beacon.HasField("broadcast_offer_frequency_slot")
        assert not beacon.broadcast_targets[0].HasField("frequency_slot")
    encoded = beacon.SerializeToString()
    assert beacon_type.FromString(encoded) == beacon
    pub.sendMessage("meshtastic.connection.established", interface=interface)
    assert node.writeConfig.call_count == 1

    # A failed transport write restores the real protobuf cache and can be retried.
    plugin.config["message"] = "Updated"
    node.writeConfig.side_effect = RuntimeError("radio write failed")
    pub.sendMessage("meshtastic.connection.established", interface=interface)
    assert beacon.SerializeToString() == encoded
    node.writeConfig.side_effect = None
    pub.sendMessage("meshtastic.connection.established", interface=interface)
    assert beacon.broadcast_message == "Updated"
    assert node.writeConfig.call_count == 3
finally:
    plugin.stop()
plugin.config["message"] = "Stopped"
pub.sendMessage("meshtastic.connection.established", interface=interface)
assert beacon.broadcast_message == "Updated"
assert node.writeConfig.call_count == 3

if sys.argv[1] == "initial":
    # A new firmware slot arrives as an unknown field to an older client.
    # Deepcopy preserves it, so reusing it while changing the offer is unsafe.
    unknown_beacon = beacon_type.FromString(beacon.SerializeToString() + b"\\x10\\x17")
    node.moduleConfig.mesh_beacon = unknown_beacon
    try:
        plugin.configure_firmware(interface)
    except MeshBeaconConfigError as error:
        assert "unrecognized beacon settings" in str(error)
    else:
        raise AssertionError("Old client applied a stale unknown frequency slot")
    assert node.writeConfig.call_count == 3
    plugin.config["broadcast"] = False
    assert plugin.configure_firmware(interface)
    assert not unknown_beacon.flags & 2
    assert unknown_beacon.SerializeToString().endswith(b"\\x10\\x17")
"""


@pytest.mark.integration
@pytest.mark.parametrize("schema", ["initial", "frequency_slots"])
def test_beacon_connection_lifecycle_with_real_protobufs(
    schema: str, tmp_path: Path
) -> None:
    """Both schemas preserve presence, rollback, reconnect, and subscription cleanup."""
    repository = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "PYTHONPATH": str(repository / "src"),
        "MMRELAY_HOME": str(tmp_path),
    }
    # Fixed interpreter and literal probe; neither shell execution nor external code.
    completed = subprocess.run(  # nosec B603
        [sys.executable, "-W", "error", "-c", _PROBE, schema],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
