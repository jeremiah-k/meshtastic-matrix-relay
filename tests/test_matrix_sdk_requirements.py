"""The manifest selects one provider pin for base and encryption-extra installs."""

import tomllib
from pathlib import Path

from tests.helpers import select_mindroom_requirements


def test_manifest_selects_one_provider_pin_for_base_and_encryption_extra() -> None:
    """Assert one exact non-0.x provider pin is shared by the base and e2e extras."""
    manifest = Path(__file__).parents[1] / "pyproject.toml"
    project = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]
    pins: set[str] = set()
    for requirements in [
        project["dependencies"],
        project["optional-dependencies"]["e2e"],
    ]:
        selected = select_mindroom_requirements(requirements)
        assert len(selected) == 1
        requirement = selected[0]
        assert requirement.marker is None
        specifiers = list(requirement.specifier)
        assert len(specifiers) == 1
        assert specifiers[0].operator == "=="
        pins.add(specifiers[0].version)
    assert len(pins) == 1, pins
    (pin,) = pins
    assert not pin.startswith("0."), pin
    assert project["requires-python"] == ">=3.12"
