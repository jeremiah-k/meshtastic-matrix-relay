"""Installed mindroom-nio cross-signing contract guard.

Ports the no-homeserver cross-signing SDK contract from the MEDRE
windows-e2ee-contract recipe: it proves the installed provider can really
generate and verify a cross-signing identity with the vodozemac backend,
not merely that modules import. The real SDK runs in an isolated
interpreter because the in-process test suite mocks ``nio``.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - runs contract check script with hardcoded args in isolated mode
import sys

import pytest

# Probes the installed provider distribution and its crypto capability.
# Exit codes: 0 = ready, 2 = e2e extra absent / provider conflict, 3 = crypto disabled.
_E2EE_PROBE_SCRIPT = """
from importlib import metadata

try:
    metadata.version("mindroom-nio")
except metadata.PackageNotFoundError:
    raise SystemExit(2)
try:
    metadata.version("matrix-nio")
except metadata.PackageNotFoundError:
    pass
else:
    raise SystemExit(2)
from nio.crypto import ENCRYPTION_ENABLED
raise SystemExit(0 if ENCRYPTION_ENABLED else 3)
"""

# Generates a real cross-signing identity, signs the self-signing key
# payload with the master key, signs a device-keys payload, and verifies
# both signatures with vodozemac primitives — no homeserver involved.
_CROSS_SIGNING_SCRIPT = """
import inspect

import nio
import vodozemac
from nio import AsyncClient
from nio.api import Api
from nio.crypto import ENCRYPTION_ENABLED, OlmAccount
from nio.crypto.cross_signing import CrossSigningIdentity

assert ENCRYPTION_ENABLED is True

ensure = getattr(AsyncClient, "ensure_cross_signing", None)
send = getattr(AsyncClient, "send", None)
identity = getattr(AsyncClient, "cross_signing_identity", None)
assert callable(ensure)
assert callable(send)
assert isinstance(identity, property)
assert "password" in inspect.signature(ensure).parameters
assert list(inspect.signature(send).parameters)[:5] == [
    "self",
    "method",
    "path",
    "data",
    "headers",
]
assert callable(getattr(AsyncClient, "_upload_own_device_signature", None))
assert callable(getattr(CrossSigningIdentity, "load", None))
assert callable(getattr(CrossSigningIdentity, "generate", None))
assert callable(getattr(CrossSigningIdentity, "self_signing_key_payload", None))
assert callable(getattr(CrossSigningIdentity, "signed_device_payload", None))

account = OlmAccount()
identity_keys = account.identity_keys
assert identity_keys["ed25519"] and identity_keys["curve25519"]

cross_signing = CrossSigningIdentity.generate("@bot:example.org")
self_signing = cross_signing.self_signing_key_payload()
signature_b64 = self_signing["signatures"][cross_signing.user_id][
    f"ed25519:{cross_signing.master_public_key}"
]
unsigned = {
    key: value
    for key, value in self_signing.items()
    if key not in ("signatures", "unsigned")
}
message = Api.to_canonical_json(unsigned).encode("utf-8")
master_key = vodozemac.Ed25519PublicKey.from_base64(cross_signing.master_public_key)
master_key.verify_signature(
    message, vodozemac.Ed25519Signature.from_base64(signature_b64)
)

device_payload = cross_signing.signed_device_payload(
    {
        "user_id": cross_signing.user_id,
        "device_id": "CONTRACT",
        "keys": {"ed25519:CONTRACT": identity_keys["ed25519"]},
    }
)
device_signature_b64 = device_payload["signatures"][cross_signing.user_id][
    f"ed25519:{cross_signing.self_signing_public_key}"
]
device_unsigned = {
    key: value
    for key, value in device_payload.items()
    if key not in ("signatures", "unsigned")
}
device_message = Api.to_canonical_json(device_unsigned).encode("utf-8")
self_signing_key = vodozemac.Ed25519PublicKey.from_base64(
    cross_signing.self_signing_public_key
)
self_signing_key.verify_signature(
    device_message, vodozemac.Ed25519Signature.from_base64(device_signature_b64)
)
"""


def _run_sdk_script(script: str) -> subprocess.CompletedProcess[str]:
    """Run a contract script against the real installed SDK in isolation."""
    return subprocess.run(  # nosec B603 - hardcoded interpreter, isolated mode
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )


def _skip_unless_e2ee_ready() -> None:
    """Skip on capability: the e2e extra with an enabled crypto backend must be installed."""
    probe = _run_sdk_script(_E2EE_PROBE_SCRIPT)
    if probe.returncode != 0:
        reason = (
            "Matrix E2EE SDK contract environment not available: "
            f"{(probe.stderr or probe.stdout).strip() or f'exit {probe.returncode}'}"
        )
        if os.environ.get("MMRELAY_REQUIRE_E2EE_SDK") == "1":
            pytest.fail(reason)
        pytest.skip(reason)


def test_mindroom_nio_cross_signing_contract() -> None:
    """The installed provider exposes and exercises MMRelay's cross-signing surface."""
    _skip_unless_e2ee_ready()

    result = _run_sdk_script(_CROSS_SIGNING_SCRIPT)

    assert result.returncode == 0, result.stderr or result.stdout
