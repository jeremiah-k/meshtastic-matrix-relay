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

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "test_e2ee_integration.py"


def test_e2ee_integration_script_help_exits_zero():
    """``--help`` should exit 0 and print usage information."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )  # nosec B603 — hardcoded args, no user input

    assert result.returncode == 0, (
        f"Script exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # Help header printed by the manual --help handler.
    assert (
        "E2EE Integration Test" in result.stdout
    ), f"Missing expected help header in stdout.\nstdout: {result.stdout}"
    assert (
        "Usage" in result.stdout
    ), f"Missing 'Usage' section in stdout.\nstdout: {result.stdout}"


def test_e2ee_integration_script_help_no_credentials_required():
    """The ``--help`` path must not attempt a Matrix connection."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )  # nosec B603 — hardcoded args, no user input

    # The help handler returns before any connection attempt.
    # If it tried to connect we'd see connection-related errors in stderr.
    assert result.returncode == 0, (
        f"Script exited {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert (
        "connect" not in result.stderr.lower()
    ), f"Help path unexpectedly attempted a connection.\nstderr: {result.stderr}"
