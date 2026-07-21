"""Regression tests for MMRelay's supported Python runtime contract."""

from __future__ import annotations

import tomllib
from pathlib import Path

from mmrelay.constants.app import MIN_PYTHON_VERSION

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "test-and-coverage.yml"


def _project_metadata() -> dict[str, object]:
    with PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    project = data.get("project")
    assert isinstance(project, dict)
    return project


def test_python_311_floor_is_consistent_across_runtime_and_metadata() -> None:
    project = _project_metadata()

    assert project["requires-python"] == ">=3.11"
    assert MIN_PYTHON_VERSION == (3, 11)

    classifiers = project.get("classifiers")
    assert isinstance(classifiers, list)
    assert "Programming Language :: Python :: 3.10" not in classifiers
    for minor in range(11, 15):
        assert f"Programming Language :: Python :: 3.{minor}" in classifiers


def test_supported_ci_matrix_runs_real_e2ee_dependencies() -> None:
    workflow = TEST_WORKFLOW.read_text(encoding="utf-8")

    assert 'python-version: ["3.11", "3.12", "3.13", "3.14"]' in workflow
    assert 'python-version: ["3.10"' not in workflow
    assert "pip install -e '.[test,e2e]'" in workflow


def test_matplotlib_pin_matches_the_python_311_dependency_line() -> None:
    project = _project_metadata()
    dependencies = project.get("dependencies")

    assert isinstance(dependencies, list)
    assert "matplotlib==3.11.1" in dependencies
