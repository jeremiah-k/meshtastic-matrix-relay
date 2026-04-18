"""
Tests for shared version helper behavior.
"""

from unittest.mock import patch

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
