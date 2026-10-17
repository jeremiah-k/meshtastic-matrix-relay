"""Real nio capability detection contract, including the crypto store.

MMRelay's E2EE readiness is capability-driven: the crypto backend alone
is not enough when the installed nio's SQLite store cannot open on the
platform (mindroom-nio 1.1.x takes a POSIX file lease, so native Windows
cannot open the store). This contract runs MMRelay's real capability
detection in an isolated interpreter — the in-process suite mocks nio —
and cross-checks it against a direct store-open attempt on the installed
SDK, so the windows-latest CI matrix proves the fail-closed outcome on
each arm of the pinned version split instead of assuming it.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - runs contract check script with hardcoded args in isolated mode
import sys
from pathlib import Path

import pytest

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

_CAPABILITIES_CONTRACT_SCRIPT = """
import json
import tempfile

from mmrelay.matrix.compat import detect_matrix_capabilities

caps = detect_matrix_capabilities()

store_actually_opens = False
try:
    from nio.store import SqliteStore

    with tempfile.TemporaryDirectory(prefix="mmrelay-contract-store-") as store_dir:
        store = SqliteStore("@contract:mmrelay.invalid", "CONTRACT", store_dir)
        database = getattr(store, "database", None)
        if database is not None:
            database.close()
    store_actually_opens = True
except Exception:
    store_actually_opens = False

crypto_present = bool(
    caps.vodozemac_available
    or caps.olm_available
    or caps.nio_crypto_encryption_enabled is True
)

print(
    json.dumps(
        {
            "encryption_available": caps.encryption_available,
            "sqlite_store_available": caps.sqlite_store_available,
            "sqlite_store_openable": caps.sqlite_store_openable,
            "crypto_present": crypto_present,
            "store_actually_opens": store_actually_opens,
        }
    )
)
"""


def _run_sdk_script(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a contract script against the real installed SDK in isolation."""
    return subprocess.run(  # nosec B603 - hardcoded interpreter, isolated mode
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
        cwd=str(cwd),
    )


def _skip_unless_e2ee_ready() -> None:
    """Skip on capability: the e2e extra with an enabled crypto backend must be installed."""
    probe = _run_sdk_script(_E2EE_PROBE_SCRIPT, Path("."))
    if probe.returncode != 0:
        reason = (
            "Matrix E2EE SDK contract environment not available: "
            f"{(probe.stderr or probe.stdout).strip() or f'exit {probe.returncode}'}"
        )
        if os.environ.get("MMRELAY_REQUIRE_E2EE_SDK") == "1":
            pytest.fail(reason)
        pytest.skip(reason)


def test_capability_detection_matches_real_store_openability(tmp_path: Path) -> None:
    """Detection reports E2EE available exactly when crypto exists and the store opens."""
    _skip_unless_e2ee_ready()

    result = _run_sdk_script(_CAPABILITIES_CONTRACT_SCRIPT, tmp_path)

    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(result.stdout)

    assert report["crypto_present"] is True
    # The openability probe must tell the truth about the installed SDK.
    assert report["sqlite_store_openable"] == report["store_actually_opens"]
    # Fail closed: a crypto backend without an openable store disables E2EE.
    assert report["encryption_available"] == report["store_actually_opens"]
