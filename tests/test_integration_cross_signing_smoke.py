"""Static validation for the live Matrix cross-signing smoke scenario."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts/ci/run-mmrelay-meshtasticd-integration.sh"


def _extract_verifier_source(script: str) -> str:
    """Return the embedded Python verifier from the integration shell script."""
    function_start = script.index("assert_matrix_device_cross_signed() {")
    python_start = script.index("<<'PY'\n", function_start) + len("<<'PY'\n")
    python_end = script.index("\nPY\n}", python_start)
    return script[python_start:python_end]


def test_integration_script_verifies_server_visible_cross_signing_chain() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "auth login" in script
    assert "/_matrix/client/v3/keys/query" in script
    assert "vodozemac.Ed25519PublicKey.from_base64" in script
    assert "Device is not signed by the self-signing key" in script
    assert "Self-signing key is not signed by the master key" in script
    assert 'run_or_fail "MMRelay A auth login failed"' in script
    assert 'run_or_fail "MMRelay B auth login failed"' in script
    assert "Missing key {key!r} in {json_path}" in script

    compile(_extract_verifier_source(script), "<cross-signing-smoke>", "exec")


def test_embedded_verifier_accepts_canonical_json_bytes() -> None:
    """Execute the verifier in a clean process with the real bindings."""
    pytest.importorskip("vodozemac")
    script = SCRIPT.read_text(encoding="utf-8")
    parsed = ast.parse(_extract_verifier_source(script))
    verifier_function = next(
        node
        for node in parsed.body
        if isinstance(node, ast.FunctionDef) and node.name == "verify_json"
    )
    probe = (
        "import vodozemac\nfrom nio.api import Api\n"
        + ast.unparse(verifier_function)
        + """
from nio.crypto.cross_signing import CrossSigningIdentity

identity = CrossSigningIdentity.generate("@bot:example.org")
payload = identity.self_signing_key_payload()
signature = payload["signatures"][identity.user_id][
    f"ed25519:{identity.master_public_key}"
]
verify_json(identity.master_public_key, payload, signature)
"""
    )
    completed = subprocess.run(
        [sys.executable, "-W", "error", "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
