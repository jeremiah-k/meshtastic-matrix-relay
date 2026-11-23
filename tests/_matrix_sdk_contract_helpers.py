"""Shared helpers for the real-SDK Matrix E2EE contract tests.

The contract modules must keep module-level ``_run_sdk_script`` and
``_skip_unless_e2ee_ready`` names: ``test_matrix_sdk_contract_enforcement.py``
monkeypatches ``module._run_sdk_script`` per module and expects
``module._skip_unless_e2ee_ready()`` to use the patched runner.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - runs contract check script with hardcoded args in isolated mode
import sys
from collections.abc import Callable
from pathlib import Path

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


def run_sdk_script(
    script: str, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a contract script against the real installed SDK in isolation."""
    return subprocess.run(  # nosec B603 - hardcoded interpreter, isolated mode
        [sys.executable, "-I", "-c", script, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
        cwd=None if cwd is None else str(cwd),
    )


def skip_unless_e2ee_ready(
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Skip on capability: the e2e extra with an enabled crypto backend must be installed."""
    probe = runner(_E2EE_PROBE_SCRIPT)
    if probe.returncode != 0:
        reason = (
            "Matrix E2EE SDK contract environment not available: "
            f"{(probe.stderr or probe.stdout).strip() or f'exit {probe.returncode}'}"
        )
        if os.environ.get("MMRELAY_REQUIRE_E2EE_SDK") == "1":
            pytest.fail(reason)
        pytest.skip(reason)
