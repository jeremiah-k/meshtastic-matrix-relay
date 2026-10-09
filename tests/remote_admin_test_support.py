"""Real mtjk fixtures shared by remote-admin executor and integration tests."""

import sys

import pytest

REMOTE_NODE_NUM = 0xA4BF9D0C
REMOTE_NODE_ID = "!a4bf9d0c"
LOCAL_NODE_NUM = REMOTE_NODE_NUM + 1


def _mocked_dependency_names() -> list[str]:
    prefixes = ("meshtastic", "bleak", "pubsub")
    return [
        name
        for name in sys.modules
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes)
    ]


@pytest.fixture(autouse=True)
def real_mtjk():
    """Seat the real pinned mtjk package (and its deps) for this module's tests.

    The global test mocks replace sys.modules entries such as "meshtastic"
    and "bleak" with modules whose __getattr__ fabricates MagicMocks, which
    defeats submodule imports like meshtastic.cli.parser. The executor's
    contract is defined by the real parser and dispatch seams, so drop the
    mocked entries, import the real packages, and restore the mocks after.
    """
    saved = {name: sys.modules.pop(name) for name in _mocked_dependency_names()}
    try:
        import meshtastic.cli.parser  # noqa: F401  # real package from site-packages

        yield
    finally:
        for name in _mocked_dependency_names():
            del sys.modules[name]
        sys.modules.update(saved)


def real_interface():
    from meshtastic.mesh_interface import MeshInterface
    from meshtastic.protobuf import channel_pb2, mesh_pb2

    interface = MeshInterface()
    interface.myInfo = mesh_pb2.MyNodeInfo(my_node_num=LOCAL_NODE_NUM)
    interface.localNode.nodeNum = LOCAL_NODE_NUM
    interface.localNode.channels = [
        channel_pb2.Channel(index=0, role=channel_pb2.Channel.Role.PRIMARY)
    ]
    interface.nodes = {REMOTE_NODE_ID: {"num": REMOTE_NODE_NUM}}
    interface.nodesByNum = {REMOTE_NODE_NUM: interface.nodes[REMOTE_NODE_ID]}
    interface._get_or_create_by_num(REMOTE_NODE_NUM)["adminSessionPassKey"] = b"session"
    return interface
