"""The manifest selects one provider pin for base and encryption-extra installs."""

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
    """Require indexed releases without replacing MMRelay's radio SDK."""
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    assert project["requires-python"] == ">=3.12"
    dependencies = [Requirement(dep) for dep in project["dependencies"]]
    lock = tomllib.loads(
        (Path(__file__).parents[1] / "uv.lock").read_text(encoding="utf-8")
    )
    for package in ("medre", "medre-adapter-matrix"):
        selected = [
            dep for dep in dependencies if canonicalize_name(dep.name) == package
        ]
        assert len(selected) == 1
        (requirement,) = selected
        assert requirement.marker is None
        assert requirement.url is None
        pins = list(requirement.specifier)
        assert len(pins) == 1 and pins[0].operator == "=="
        Version(pins[0].version)  # Reject wildcard and arbitrary equality pins.

        locked = [entry for entry in lock["package"] if entry["name"] == package]
        assert len(locked) == 1
        entry = locked[0]
        assert entry["version"] == pins[0].version
        assert entry["source"] == {"registry": "https://pypi.org/simple"}
        assert entry["sdist"]["hash"].startswith("sha256:")
        assert entry["wheels"]
        assert all(wheel["hash"].startswith("sha256:") for wheel in entry["wheels"])
    assert "medre-adapter-meshtastic" not in {
        canonicalize_name(dep.name) for dep in dependencies
    }
