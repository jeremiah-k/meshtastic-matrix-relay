"""Static validation for the live Matrix cross-signing smoke scenario."""

from __future__ import annotations

from pathlib import Path

SCRIPT = Path("scripts/ci/run-mmrelay-meshtasticd-integration.sh")


def test_integration_script_verifies_server_visible_cross_signing_chain() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "auth login" in script
    assert "/_matrix/client/v3/keys/query" in script
    assert "vodozemac.Ed25519PublicKey.from_base64" in script
    assert "Device is not signed by the self-signing key" in script
    assert "Self-signing key is not signed by the master key" in script

    function_start = script.index("assert_matrix_device_cross_signed() {")
    python_start = script.index("<<'PY'\n", function_start) + len("<<'PY'\n")
    python_end = script.index("\nPY\n}", python_start)
    compile(script[python_start:python_end], "<cross-signing-smoke>", "exec")
