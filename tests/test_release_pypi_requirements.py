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


@pytest.mark.parametrize(
    "direct",
    [
        "medre [dev] @ https://example.org/medre.whl",
        "medre[dev, test] @ git+https://example.org/medre.git@" + "a" * 40,
    ],
)
def test_gate_detects_valid_direct_references_with_spaced_extras(tmp_path, direct):
    from scripts.ci.check_pypi_requirements import direct_requirements

    manifest = _manifest(tmp_path, base=repr(direct))
    assert direct_requirements(manifest) == [direct]


@pytest.mark.parametrize(
    "project_fields",
    [
        'dependencies="medre @ https://example.org/medre.whl"',
        'dependencies={medre="https://example.org/medre.whl"}',
        'dependencies=["not a valid requirement @"]',
        "dependencies=[42]",
        'optional-dependencies="test"',
        '[project.optional-dependencies]\ntest="medre @ https://example.org/medre.whl"',
    ],
)
def test_gate_fails_closed_on_malformed_requirement_declarations(
    tmp_path, project_fields
):
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('[project]\nname="package"\nversion="0.1.0"\n' + project_fields)
    output = tmp_path / "actions-output"
    result = subprocess.run(  # nosec B603 - repo script and fixture paths only
        [
            sys.executable,
            str(SCRIPT),
            "--pyproject",
            str(manifest),
            "--github-output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Cannot validate PyPI requirements" in result.stderr
    assert not output.exists()


def test_workflow_upload_is_gated_and_preflight_matches_current_manifest(tmp_path):
    workflow = (ROOT / ".github/workflows/pypi-publish.yml").read_text()
    assert "scripts/ci/check_pypi_requirements.py" in workflow
    assert "steps.pypi_requirements.outputs.eligible == 'true'" in workflow
    assert "steps.pypi_wheel.outputs.eligible == 'true'" in workflow
    assert "--wheel dist/*.whl" in workflow
    assert workflow.index("Install build dependencies") < workflow.index(
        "Check distribution requirements for PyPI"
    )
    assert 'python -m pip install "build==1.6.1" "packaging==26.3"' in workflow
    assert '--github-summary "$GITHUB_STEP_SUMMARY"' in workflow
    output = tmp_path / "actions-output"
    subprocess.run(  # nosec B603 - repository script and manifest only
        [
            sys.executable,
            str(SCRIPT),
            "--pyproject",
            str(ROOT / "pyproject.toml"),
            "--github-output",
            str(output),
        ],
        check=True,
    )
    # The coordinated pre-release manifest contains direct Git dependencies.
    # This independent expectation must change when indexed pins replace them.
    assert output.read_text() == "eligible=false\n"


@pytest.mark.parametrize(
    "body", ["", '[other]\nname="missing"\n', 'project = "not-a-table"\n']
)
def test_gate_rejects_missing_or_non_table_project(tmp_path: Path, body: str) -> None:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(body, encoding="utf-8")
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
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "project must be a table" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("use_direct", [False, True])
def test_gate_summary_reports_publish_decision_without_urls(
    tmp_path: Path, use_direct: bool
) -> None:
    requirement = (
        '"medre[dev] @ git+https://github.com/jeremiah-k/medre.git@' + "a" * 40 + '"'
        if use_direct
        else '"click==8.3.1"'
    )
    manifest = _manifest(tmp_path, base=requirement)
    output = tmp_path / "actions-output"
    summary = tmp_path / "actions-summary"
    subprocess.run(  # nosec B603 - repo script path and fixture manifest only
        [
            sys.executable,
            str(SCRIPT),
            "--pyproject",
            str(manifest),
            "--github-output",
            str(output),
            "--github-summary",
            str(summary),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert output.read_text() == f"eligible={'false' if use_direct else 'true'}\n"
    text = summary.read_text()
    if use_direct:
        assert "PyPI upload skipped" in text
        assert "medre" in text
        assert "git+https" not in text
        assert "medre[dev]" not in text
    else:
        assert "PyPI upload eligible" in text


@pytest.mark.parametrize(
    "dynamic",
    [
        'dynamic=["dependencies"]',
        'dynamic=["optional-dependencies"]',
        'dynamic="dependencies"',
    ],
)
def test_gate_rejects_uninspectable_dynamic_dependencies(tmp_path, dynamic):
    from scripts.ci.check_pypi_requirements import direct_requirements

    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        '[project]\nname="candidate"\nversion="1.5.0"\n' + dynamic,
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        direct_requirements(manifest)


def test_manual_publish_must_use_version_tag():
    workflow = (ROOT / ".github/workflows/pypi-publish.yml").read_text(encoding="utf-8")
    assert 'if [ "$REF_TYPE" != "tag" ]' in workflow
    assert 'validate_release_version.py --tag "$RELEASE_TAG"' in workflow


@pytest.mark.parametrize(
    "requirement,expected",
    [
        ("medre @ git+https://github.com/example/medre.git@" + "a" * 40, True),
        ("medre==0.1.0", False),
    ],
)
def test_wheel_gate_checks_built_requires_dist(tmp_path, requirement, expected):
    from zipfile import ZipFile

    from scripts.ci.check_pypi_requirements import direct_wheel_requirements

    wheel = tmp_path / "package.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(
            "package-0.1.0.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: package\nVersion: 0.1.0\n"
            f"Requires-Dist: {requirement}\n",
        )
    assert bool(direct_wheel_requirements(wheel)) is expected


def test_wheel_gate_rejects_missing_metadata(tmp_path):
    from zipfile import ZipFile

    from scripts.ci.check_pypi_requirements import direct_wheel_requirements

    wheel = tmp_path / "invalid.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("README", "no metadata")
    with pytest.raises(ValueError, match="exactly one"):
        direct_wheel_requirements(wheel)


def test_wheel_gate_publish_output_is_false_for_direct_url_wheel(tmp_path):
    """The wheel decision must reach the workflow output the upload step reads."""
    from zipfile import ZipFile

    manifest = _manifest(tmp_path)
    wheel = tmp_path / "package-0.1.0-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(
            "package-0.1.0.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: package\nVersion: 0.1.0\n"
            "Requires-Dist: medre @ git+https://example.org/medre.git@"
            + "a" * 40
            + "\n",
        )
    output = tmp_path / "actions-output"
    subprocess.run(  # nosec B603 - repo script path and fixture wheel only
        [
            sys.executable,
            str(SCRIPT),
            "--pyproject",
            str(manifest),
            "--wheel",
            str(wheel),
            "--github-output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert output.read_text() == "eligible=false\n"
