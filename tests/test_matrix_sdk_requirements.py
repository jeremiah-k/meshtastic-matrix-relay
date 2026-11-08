"""The manifest selects one provider pin for base and encryption-extra installs."""

import re
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


def test_medre_dependencies_use_immutable_git_pins_without_radio_adapter() -> None:
    """Keep the MMRelay/MEDRE room schema boundary pinned without freezing a SHA.

    Re-pinning is allowed after review and a fresh compatibility qualification;
    the source form and exclusion of the competing Meshtastic adapter remain
    enforceable across future versions.
    """
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())[
        "project"
    ]
    assert project["requires-python"] == ">=3.12"
    dependencies = project["dependencies"]
    pins = {dep.partition(" @ ")[0]: dep for dep in dependencies if " @ " in dep}
    for package in ("medre", "medre-adapter-matrix"):
        pattern = (
            rf"{package} @ git\+https://github\.com/jeremiah-k/"
            rf"{package}\.git@[0-9a-f]{{40}}"
        )
        assert re.fullmatch(pattern, pins[package])
    assert not any(
        dep.lower().replace("_", "-").startswith("medre-adapter-meshtastic")
        for dep in dependencies
    )
