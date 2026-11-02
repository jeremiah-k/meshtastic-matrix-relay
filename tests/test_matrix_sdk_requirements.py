"""The manifest selects one provider pin for base and encryption-extra installs."""

import tomllib
from pathlib import Path

from tests.helpers import select_mindroom_requirements


def test_manifest_selects_one_provider_pin_for_base_and_encryption_extra() -> None:
    manifest = Path(__file__).parents[1] / "pyproject.toml"
    project = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]
    for requirements in [
        project["dependencies"],
        project["optional-dependencies"]["e2e"],
    ]:
        selected = select_mindroom_requirements(requirements)
        assert len(selected) == 1
        assert selected[0].marker is None
        specifiers = list(selected[0].specifier)
        assert len(specifiers) == 1
        assert specifiers[0].operator == "=="
    assert project["requires-python"] == ">=3.12"
