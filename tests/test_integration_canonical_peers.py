"""CI injects the production canonical contract rather than flat relay claims."""

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/ci/run-mmrelay-meshtasticd-integration.sh"
)


def test_injected_peer_fixture_uses_real_renderer_and_keeps_packet_zero(
    monkeypatch, tmp_path
):
    script = SCRIPT.read_text(encoding="utf-8")
    start = script.index("matrix_send_mesh_origin_message() {")
    begin = script.index("<<'PY'\n", start) + len("<<'PY'\n")
    end = script.index("\nPY\n}", begin)
    put = Mock(
        return_value=SimpleNamespace(
            status_code=200, json=lambda: {"event_id": "$injected"}
        )
    )
    monkeypatch.setattr("requests.put", put)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fixture",
            "https://matrix.example.org",
            "test-token",
            "!room:example.org",
            "Authored body",
            "Remote",
            "Alice",
            "AL",
            "0",
            "fixture",
        ],
    )
    probe = tmp_path / "canonical_peer_fixture.py"
    probe.write_text(script[begin:end], encoding="utf-8")
    runpy.run_path(str(probe), run_name="__main__")
    content = put.call_args.kwargs["json"]
    source = content["medre"]["metadata"]["bridge_context"]["source"]
    assert content["body"] == "Authored body"
    assert source["sender_name"] == "Alice" and source["sender_short_name"] == "AL"
    assert source["channel"] == "0" and source["native_ref"]["native_message_id"] == "0"
    assert source["native_facts"]["fields"]["portnum"] == 1
    assert not any(key.startswith("meshtastic_") for key in content)
