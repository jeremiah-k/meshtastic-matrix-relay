"""Ensure GitHub releases cannot accidentally upload direct-URL metadata to PyPI."""

from __future__ import annotations

import subprocess  # nosec B404 - release gate tests run only the repo's own CI script
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci" / "check_pypi_requirements.py"


def _manifest(
    tmp_path: Path, *, base: str = '"click==8.3.1"', extra: str = '"pytest==9.2.0"'
) -> Path:
    target = tmp_path / "pyproject.toml"
    target.write_text(
        '[project]\nname="package"\nversion="0.1.0"\n'
        f"dependencies=[{base}]\n[project.optional-dependencies]\n"
        f"test=[{extra}]\n",
        encoding="utf-8",
    )
    return target


@pytest.mark.parametrize("extra", [False, True])
def test_gate_skips_direct_reference_in_any_published_requirement_group(
    tmp_path, extra
):
    direct = '"medre @ git+https://github.com/jeremiah-k/medre.git@' + "a" * 40 + '"'
    manifest = _manifest(
        tmp_path,
        base=direct if not extra else '"click==8.3.1"',
        extra=direct if extra else '"pytest==9.2.0"',
    )
    output = tmp_path / "actions-output"
    result = subprocess.run(  # nosec B603 - repo script path and fixture manifest only
        [
            sys.executable,
            str(SCRIPT),
            "--pyproject",
            str(manifest),
            "--github-output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert output.read_text() == "eligible=false\n"
    assert "Skipping PyPI upload" in result.stdout


def test_gate_allows_only_index_resolvable_requirements(tmp_path):
    manifest = _manifest(tmp_path)
    output = tmp_path / "actions-output"
    subprocess.run(  # nosec B603 - repo script path and fixture manifest only
        [
            sys.executable,
            str(SCRIPT),
            "--pyproject",
            str(manifest),
            "--github-output",
            str(output),
        ],
        check=True,
    )
    assert output.read_text() == "eligible=true\n"


def test_workflow_upload_is_gated_and_actual_release_is_ineligible():
    from scripts.ci.check_pypi_requirements import direct_requirements

    workflow = (ROOT / ".github/workflows/pypi-publish.yml").read_text()
    assert "scripts/ci/check_pypi_requirements.py" in workflow
    assert "steps.pypi_requirements.outputs.eligible == 'true'" in workflow
    actual = direct_requirements(ROOT / "pyproject.toml")
    assert any(req.startswith("medre @ ") for req in actual)
    assert any(req.startswith("medre-adapter-matrix @ ") for req in actual)
