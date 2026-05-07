"""
Regression test for the manual E2EE integration script help path.

Ensures ``scripts/test_e2ee_integration.py --help`` runs without Matrix
credentials, network access, or encryption setup, and exits cleanly with
expected help text.  This guards against future import / path regressions
while keeping the script itself outside the pytest suite.
"""

import subprocess  # nosec B404 — test calls script under test with hardcoded args
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "test_e2ee_integration.py"

if not SCRIPT_PATH.exists():
    pytest.skip(
        f"Integration script not found: {SCRIPT_PATH}",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def help_result() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        cwd=str(REPO_ROOT),
    )  # nosec B603


def test_e2ee_integration_script_help_exits_zero(help_result) -> None:
    """``--help`` should exit 0 and print usage information."""
    result = help_result

    assert result.returncode == 0, (
        f"Script exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert (
        "E2EE Integration Test" in result.stdout
    ), f"Missing expected help header in stdout.\nstdout: {result.stdout}"
    assert (
        "Usage" in result.stdout
    ), f"Missing 'Usage' section in stdout.\nstdout: {result.stdout}"


def test_e2ee_integration_script_help_no_credentials_required(help_result) -> None:
    """The ``--help`` path must not attempt a Matrix connection."""
    result = help_result

    forbidden = (
        "credentials.json",
        "access_token",
        "homeserver",
        "Traceback",
        "ConnectionError",
    )
    matches = [marker for marker in forbidden if marker in result.stderr]
    assert not matches, (
        f"Help path produced credentials/network-related stderr ({matches}).\n"
        f"stderr: {result.stderr}"
    )
