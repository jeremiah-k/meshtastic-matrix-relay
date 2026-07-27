"""Regression tests for MMRelay's supported Python runtime contract."""

from __future__ import annotations

import configparser
import json
import shlex
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from mmrelay.constants.app import MIN_PYTHON_VERSION

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "test-and-coverage.yml"
MYPY_CONFIG = ROOT / "mypy.ini"
PYRIGHT_CONFIG = ROOT / "pyrightconfig.json"
RUFF_CONFIG = ROOT / ".trunk" / "configs" / "ruff.toml"

_PYTHON_CLASSIFIER_PREFIX = "Programming Language :: Python :: "
_MATRIX_EXPRESSION = "${{ matrix.python-version }}"


def _project_metadata() -> dict[str, object]:
    with PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    project = data.get("project")
    assert isinstance(project, dict)
    return project


def _test_job() -> dict[str, Any]:
    workflow = yaml.safe_load(TEST_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs.get("test")
    assert isinstance(job, dict)
    return job


def _python_classifiers(project: Mapping[str, object]) -> tuple[tuple[int, int], ...]:
    classifiers = project.get("classifiers")
    assert isinstance(classifiers, list)

    versions: list[tuple[int, int]] = []
    for classifier in classifiers:
        if not isinstance(classifier, str):
            continue
        value = classifier.removeprefix(_PYTHON_CLASSIFIER_PREFIX)
        if value == classifier:
            continue
        components = value.split(".")
        if len(components) != 2 or not all(part.isdigit() for part in components):
            continue
        versions.append((int(components[0]), int(components[1])))

    assert versions, "No minor-version Python classifiers are declared"
    assert len(versions) == len(set(versions)), "Duplicate Python classifiers"
    return tuple(sorted(versions))


def _matrix_python_versions(job: Mapping[str, object]) -> tuple[tuple[int, int], ...]:
    strategy = job.get("strategy")
    assert isinstance(strategy, dict)
    matrix = strategy.get("matrix")
    assert isinstance(matrix, dict)
    raw_versions = matrix.get("python-version")
    assert isinstance(raw_versions, list)

    versions: list[tuple[int, int]] = []
    for raw_version in raw_versions:
        major, separator, minor = str(raw_version).partition(".")
        assert separator, raw_version
        assert major.isdigit(), raw_version
        assert minor.isdigit(), raw_version
        versions.append((int(major), int(minor)))
    assert len(versions) == len(set(versions)), "Duplicate Python versions in CI matrix"
    return tuple(sorted(versions))


def _workflow_steps(job: Mapping[str, object]) -> tuple[dict[str, Any], ...]:
    steps = job.get("steps")
    assert isinstance(steps, list)
    parsed_steps = tuple(step for step in steps if isinstance(step, dict))
    assert len(parsed_steps) == len(steps), "Every workflow step must be a mapping"
    return parsed_steps


def _step_using(job: Mapping[str, object], action: str) -> dict[str, Any]:
    matches = [
        step
        for step in _workflow_steps(job)
        if str(step.get("uses", "")).startswith(f"{action}@")
    ]
    assert len(matches) == 1, f"Expected exactly one workflow step using {action}"
    return matches[0]


def _cache_step(job: Mapping[str, object], path: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for step in _workflow_steps(job):
        if not str(step.get("uses", "")).startswith("actions/cache@"):
            continue
        options = step.get("with")
        if isinstance(options, dict) and options.get("path") == path:
            matches.append(step)
    assert len(matches) == 1, f"Expected exactly one cache step for {path}"
    return matches[0]


def _editable_install_extras(script: str) -> set[str]:
    """Return extras requested by the script's editable project install."""
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        arguments = shlex.split(line)
        if "install" not in arguments or "-e" not in arguments:
            continue
        editable_index = arguments.index("-e")
        assert editable_index + 1 < len(arguments)
        target = arguments[editable_index + 1]
        if not target.startswith(".[") or not target.endswith("]"):
            continue
        return {extra.strip() for extra in target[2:-1].split(",") if extra.strip()}
    return set()


def test_python_floor_is_consistent_across_runtime_and_metadata() -> None:
    project = _project_metadata()
    floor_text = ".".join(str(component) for component in MIN_PYTHON_VERSION)

    assert project["requires-python"] == f">={floor_text}"

    declared_versions = _python_classifiers(project)
    assert min(declared_versions) == MIN_PYTHON_VERSION
    assert all(major == MIN_PYTHON_VERSION[0] for major, _minor in declared_versions)

    highest_minor = max(minor for _major, minor in declared_versions)
    expected_versions = tuple(
        (MIN_PYTHON_VERSION[0], minor)
        for minor in range(MIN_PYTHON_VERSION[1], highest_minor + 1)
    )
    assert declared_versions == expected_versions


def test_supported_ci_matrix_matches_declared_versions_and_runs_e2ee() -> None:
    project = _project_metadata()
    job = _test_job()

    assert _matrix_python_versions(job) == _python_classifiers(project)

    checkout_step = _step_using(job, "actions/checkout")
    checkout_options = checkout_step.get("with")
    assert isinstance(checkout_options, dict)
    assert checkout_options.get("persist-credentials") is False

    setup_step = _step_using(job, "actions/setup-python")
    setup_options = setup_step.get("with")
    assert isinstance(setup_options, dict)
    assert setup_options.get("python-version") == _MATRIX_EXPRESSION

    assert any(
        _editable_install_extras(script) >= {"test", "e2e"}
        for step in _workflow_steps(job)
        if isinstance((script := step.get("run")), str)
    )

    cache_step = _cache_step(job, "~/.cache/pip")
    cache_options = cache_step.get("with")
    assert isinstance(cache_options, dict)
    assert _MATRIX_EXPRESSION in str(cache_options.get("key", ""))

    restore_keys = cache_options.get("restore-keys")
    assert isinstance(restore_keys, str)
    assert any(
        _MATRIX_EXPRESSION in line for line in restore_keys.splitlines() if line.strip()
    )


def test_static_analysis_targets_the_minimum_supported_python() -> None:
    floor_text = ".".join(str(component) for component in MIN_PYTHON_VERSION)

    mypy_config = configparser.ConfigParser()
    assert mypy_config.read(MYPY_CONFIG, encoding="utf-8") == [str(MYPY_CONFIG)]
    assert mypy_config["mypy"].get("python_version") == floor_text

    pyright_config = json.loads(PYRIGHT_CONFIG.read_text(encoding="utf-8"))
    assert pyright_config["pythonVersion"] == floor_text

    with RUFF_CONFIG.open("rb") as handle:
        ruff_config = tomllib.load(handle)
    assert ruff_config["target-version"] == f"py{floor_text.replace('.', '')}"


def test_matplotlib_pin_matches_the_python_311_dependency_line() -> None:
    project = _project_metadata()
    dependencies = project.get("dependencies")

    assert isinstance(dependencies, list)
    assert "matplotlib==3.11.1" in dependencies
