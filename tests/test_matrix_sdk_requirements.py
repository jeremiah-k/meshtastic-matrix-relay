"""The manifest selects one provider pin for base and encryption-extra installs."""

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

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


def test_medre_dependencies_are_pinned_without_a_competing_radio_adapter() -> None:
    """Accept reviewed source commits or exact indexed releases at the boundary.

    Re-pinning is allowed after review and a fresh compatibility qualification;
    reproducibility and exclusion of the competing Meshtastic adapter remain
    enforceable across future versions.
    """
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    assert project["requires-python"] == ">=3.12"
    dependencies = [Requirement(dep) for dep in project["dependencies"]]
    for package in ("medre", "medre-adapter-matrix"):
        selected = [
            dep for dep in dependencies if canonicalize_name(dep.name) == package
        ]
        assert len(selected) == 1
        (requirement,) = selected
        assert requirement.marker is None
        if requirement.url is not None:
            assert re.fullmatch(
                rf"git\+https://github\.com/jeremiah-k/{package}\.git@[0-9a-f]{{40}}",
                requirement.url,
            )
        else:
            pins = list(requirement.specifier)
            assert len(pins) == 1 and pins[0].operator == "=="
            Version(pins[0].version)  # Reject wildcard and arbitrary equality pins.
    assert "medre-adapter-meshtastic" not in {
        canonicalize_name(dep.name) for dep in dependencies
    }
