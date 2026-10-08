"""Tests for release tag and package-version validation."""

from pathlib import Path

import pytest

from scripts.ci.validate_release_version import (
    normalize_release_tag,
    read_project_version,
    validate_release_version,
)


def _write_pyproject(path: Path, version: str) -> Path:
    pyproject_path = path / "pyproject.toml"
    pyproject_path.write_text(
        f'[project]\nname = "mmrelay"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    return pyproject_path


@pytest.mark.parametrize("tag", ["1.4.0", "v1.4.0"])
def test_normalize_release_tag_accepts_supported_tags(tag: str) -> None:
    assert normalize_release_tag(tag) == "1.4.0"


@pytest.mark.parametrize("tag", ["", "v", "release-1.4.0", "1.4", "1.4.0+local"])
def test_normalize_release_tag_rejects_unsupported_tags(tag: str) -> None:
    with pytest.raises(ValueError, match="not a supported version tag"):
        normalize_release_tag(tag)


def test_read_project_version_reads_pep621_metadata(tmp_path: Path) -> None:
    assert read_project_version(_write_pyproject(tmp_path, "1.4.0")) == "1.4.0"


def test_validate_release_version_accepts_exact_match(tmp_path: Path) -> None:
    pyproject_path = _write_pyproject(tmp_path, "1.4.0")
    assert validate_release_version("v1.4.0", pyproject_path) == "1.4.0"


def test_validate_release_version_rejects_mismatch(tmp_path: Path) -> None:
    pyproject_path = _write_pyproject(tmp_path, "1.4.0")
    with pytest.raises(ValueError, match="mismatch"):
        validate_release_version("1.4.1", pyproject_path)
