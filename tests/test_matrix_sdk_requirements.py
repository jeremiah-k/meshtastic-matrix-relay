"""Python-specific provider selection must match the encryption extra."""

import tomllib
from pathlib import Path

import pytest

from tests.helpers import select_mindroom_requirements


@pytest.mark.parametrize(
    "python_version,expected",
    [("3.11", "0.40.0"), ("3.12", None), ("3.13", None), ("3.14", None)],
)
def test_python_selects_one_provider_for_base_and_encryption_extra(
    python_version: str, expected: str | None
) -> None:
    manifest = Path(__file__).parents[1] / "pyproject.toml"
    project = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]
    for requirements in [
        project["dependencies"],
        project["optional-dependencies"]["e2e"],
    ]:
        selected = select_mindroom_requirements(
            requirements, {"python_version": python_version}
        )
        assert len(selected) == 1
        if expected is None:
            # Above 3.12 the pin follows the renovate-managed 1.1.x line;
            # only the bridge pin below 3.12 is an exact policy choice.
            assert not str(selected[0].specifier).startswith("==0.")
        else:
            assert str(selected[0].specifier) == "==" + expected
    assert project["requires-python"] == ">=3.11"
