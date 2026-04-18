"""
Tests for shared version helper behavior.
"""

from unittest.mock import Mock, patch

import mmrelay._version as version_helper


def test_get_version_prefers_metadata() -> None:
    """
    Installed package metadata should take precedence.
    """
    with (
        patch.object(version_helper, "_version_from_metadata", return_value="9.9.9"),
        patch.object(version_helper, "_version_from_pyproject", return_value="1.2.3"),
    ):
        assert version_helper.get_version() == "9.9.9"


def test_get_version_falls_back_to_pyproject() -> None:
    """
    Source checkout pyproject version should be used when metadata is unavailable.
    """
    with (
        patch.object(version_helper, "_version_from_metadata", return_value=None),
        patch.object(version_helper, "_version_from_pyproject", return_value="1.2.3"),
    ):
        assert version_helper.get_version() == "1.2.3"


def test_get_version_uses_unknown_sentinel_when_unavailable() -> None:
    """
    Unknown sentinel should be returned when no version source is available.
    """
    with (
        patch.object(version_helper, "_version_from_metadata", return_value=None),
        patch.object(version_helper, "_version_from_pyproject", return_value=None),
    ):
        assert version_helper.get_version() == "0+unknown"


def test_version_from_pyproject_reads_real_toml(tmp_path) -> None:
    """
    pyproject parsing should read [project].version from a real file.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "mmrelay"\nversion = "4.5.6"\n',
        encoding="utf-8",
    )
    with patch.object(version_helper, "_find_pyproject_toml", return_value=pyproject):
        assert version_helper._version_from_pyproject() == "4.5.6"


def test_version_from_pyproject_regex_fallback_without_tomllib(tmp_path) -> None:
    """
    Python 3.10 fallback should parse version when tomllib is unavailable.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "mmrelay"\nversion = "7.8.9"\n',
        encoding="utf-8",
    )
    with (
        patch.object(version_helper, "_find_pyproject_toml", return_value=pyproject),
        patch.object(version_helper, "tomllib", new=None),
    ):
        assert version_helper._version_from_pyproject() == "7.8.9"


def test_version_from_pyproject_regex_fallback_allows_inline_comment(tmp_path) -> None:
    """
    Python 3.10 fallback should parse version lines with inline comments.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "mmrelay"\nversion = "7.8.9"  # comment\n',
        encoding="utf-8",
    )
    with (
        patch.object(version_helper, "_find_pyproject_toml", return_value=pyproject),
        patch.object(version_helper, "tomllib", new=None),
    ):
        assert version_helper._version_from_pyproject() == "7.8.9"


def test_version_from_pyproject_tomllib_missing_version_does_not_fallback(
    tmp_path,
) -> None:
    """
    When tomllib is available and project.version is missing, fallback scanner is not used.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "mmrelay"\ndynamic = ["version"]\n',
        encoding="utf-8",
    )
    mock_tomllib = Mock()
    mock_tomllib.load.return_value = {
        "project": {"name": "mmrelay", "dynamic": ["version"]}
    }
    with (
        patch.object(version_helper, "_find_pyproject_toml", return_value=pyproject),
        patch.object(version_helper, "tomllib", new=mock_tomllib),
        patch.object(
            type(pyproject),
            "read_text",
            side_effect=AssertionError("fallback scanner should not run"),
        ),
    ):
        assert version_helper._version_from_pyproject() is None


def test_version_from_pyproject_returns_none_for_malformed_toml(tmp_path) -> None:
    """
    Malformed pyproject TOML should safely return None.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "mmrelay"\nversion = [\n',
        encoding="utf-8",
    )
    with patch.object(version_helper, "_find_pyproject_toml", return_value=pyproject):
        assert version_helper._version_from_pyproject() is None
