"""Python-specific provider selection must match the encryption extra."""

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement


@pytest.mark.parametrize(
    "python_version,expected",
    [("3.11", "0.40.0"), ("3.12", "1.1.2"), ("3.13", "1.1.2"), ("3.14", "1.1.2")],
)
def test_python_selects_one_provider_for_base_and_encryption_extra(
    python_version: str, expected: str
) -> None:
    manifest = Path(__file__).parents[1] / "pyproject.toml"
    project = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]
    for requirements in [
        project["dependencies"],
        project["optional-dependencies"]["e2e"],
    ]:
        selected = []
        for value in requirements:
            requirement = Requirement(value)
            if requirement.name == "mindroom-nio" and (
                requirement.marker is None
                or requirement.marker.evaluate({"python_version": python_version})
            ):
                selected.append(requirement)
        assert len(selected) == 1
        assert str(selected[0].specifier) == "==" + expected
    assert project["requires-python"] == ">=3.11"
