"""Guard against Windows SDK jobs passing after every real contract was skipped."""

from __future__ import annotations

import subprocess  # nosec B404 - constructs CompletedProcess results; no real subprocess is launched
from types import ModuleType

import pytest

from tests import (
    test_matrix_capabilities_sdk_contract,
    test_matrix_cross_signing_sdk_contract,
    test_matrix_decrypted_event_sdk_contract,
    test_matrix_store_lifecycle_sdk_contract,
)

_CONTRACT_MODULES = (
    test_matrix_capabilities_sdk_contract,
    test_matrix_cross_signing_sdk_contract,
    test_matrix_decrypted_event_sdk_contract,
    test_matrix_store_lifecycle_sdk_contract,
)


@pytest.mark.parametrize("module", _CONTRACT_MODULES)
@pytest.mark.parametrize("required", (False, True))
def test_sdk_probe_failure_is_fatal_only_when_required(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType, required: bool
) -> None:
    """Local SDK-less runs may skip, but the Windows CI contract must fail."""
    monkeypatch.setenv("MMRELAY_REQUIRE_E2EE_SDK", "1" if required else "0")
    monkeypatch.setattr(
        module,
        "_run_sdk_script",
        lambda *args: subprocess.CompletedProcess(
            args=["python"], returncode=2, stdout="", stderr="provider conflict"
        ),
    )
    outcome = pytest.fail.Exception if required else pytest.skip.Exception
    with pytest.raises(outcome, match="provider conflict"):
        module._skip_unless_e2ee_ready()
