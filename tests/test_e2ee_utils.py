"""
Tests for e2ee_utils module focusing on credential checking in legacy locations.

This module tests lines 115-122 and 172-182 of e2ee_utils.py:
- get_e2ee_status credential checking in legacy locations
- _check_credentials_available function with deprecation window handling
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from mmrelay.e2ee_utils import (
    _check_credentials_available,
    get_e2ee_status,
)


@pytest.fixture
def e2ee_test_config():
    """
    Provide a temporary directory, file paths, and a baseline Matrix E2EE configuration for tests.

    Yields:
        tuple: (temp_dir, config_path, credentials_path, base_config)
            - temp_dir (str): Path to the temporary directory created for the test.
            - config_path (str): Path within temp_dir for the config file (config.yaml).
            - credentials_path (str): Path within temp_dir for the credentials file (credentials.json).
            - base_config (dict): Minimal configuration dict with E2EE enabled, a meshtastic meshnet_name,
              and one example matrix room mapping.

    Notes:
        The temporary directory is removed after the fixture is finished.
    """
    # Create temporary directory
    temp_dir = tempfile.mkdtemp()
    config_path = os.path.join(temp_dir, "config.yaml")
    credentials_path = os.path.join(temp_dir, "matrix", "credentials.json")

    # Basic config
    base_config = {
        "matrix": {"e2ee": {"enabled": True}},
        "meshtastic": {"meshnet_name": "TestNet"},
        "matrix_rooms": [{"id": "!room:test.org", "meshtastic_channel": 0}],
    }

    yield temp_dir, config_path, credentials_path, base_config

    # Cleanup
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


@patch("sys.platform", "linux")
@patch("mmrelay.e2ee_utils.os.path.exists")
@patch("mmrelay.e2ee_utils.resolve_all_paths")
@patch("mmrelay.e2ee_utils.is_deprecation_window_active")
def test_credentials_found_in_legacy_location(
    mock_deprecation_active, mock_resolve_all_paths, mock_exists, e2ee_test_config
) -> None:
    """
    Test credential detection when credentials are found in legacy location (lines 115-122).

    This test verifies that when credentials are not found in primary location
    but exist in a legacy location, get_e2ee_status correctly marks
    credentials as available and stops searching after first match.
    """
    _temp_dir, _config_path, _credentials_path, base_config = e2ee_test_config

    # Mock dependencies as installed
    with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:
        mock_import.side_effect = lambda _: MagicMock()

        mock_deprecation_active.return_value = True

        # Mock paths_info with legacy sources
        mock_resolve_all_paths.return_value = {
            "credentials_path": "/primary/matrix/credentials.json",
            "legacy_sources": ["/legacy1", "/legacy2", "/legacy3"],
            "legacy_active": True,
        }

        # Mock os.path.exists to find credentials in second legacy location
        def exists_side_effect(path):
            """
            Simulate os.path.exists responses for test credential paths, returning True only for the legacy2 credentials file.

            Parameters:
                path (str): Path to check; the function looks for "credentials.json" and the substrings "primary", "legacy1", "legacy2", or "legacy3" to decide the simulated result.

            Returns:
                bool: True if the path refers to the credentials file in "legacy2", False otherwise.
            """
            if "credentials.json" in path:
                # Primary location doesn't have it
                if "primary" in path:
                    return False
                # First legacy doesn't have it
                elif "legacy1" in path:
                    return False
                # Second legacy has it - should find this
                elif "legacy2" in path:
                    return True
                # Third legacy should not be checked (short-circuited)
                elif "legacy3" in path:
                    return False
            return False

        mock_exists.side_effect = exists_side_effect

        # Call get_e2ee_status without config_path (uses HOME)
        status = get_e2ee_status(base_config)

        # Verify credentials are marked as available
        assert status["credentials_available"] is True
        # Verify overall status is ready (other requirements met)
        assert status["overall_status"] == "ready"
        # Verify no authentication issue
        assert "Matrix authentication not configured" not in status["issues"]

        # Verify it stopped checking after first match (legacy2)
        # Should have checked primary, then legacy1, then legacy2
        # But NOT legacy3
        calls = mock_exists.call_args_list
        paths_checked = [call[0][0] for call in calls]
        # Should not have checked legacy3
        assert not any(
            "legacy3" in path for path in paths_checked
        ), "Should have stopped checking after finding credentials in legacy2"


@patch("sys.platform", "linux")
@patch("mmrelay.e2ee_utils.os.path.exists")
@patch("mmrelay.e2ee_utils.resolve_all_paths")
@patch("mmrelay.e2ee_utils.is_deprecation_window_active")
def test_credentials_not_found_in_legacy_locations(
    mock_deprecation_active, mock_resolve_all_paths, mock_exists, e2ee_test_config
) -> None:
    """
    Test credential detection when credentials are not found in any location (lines 115-122).

    This test verifies that when credentials are not found in primary or any
    legacy locations, get_e2ee_status correctly marks credentials as
    unavailable and adds appropriate issue message.
    """
    _temp_dir, _config_path, _credentials_path, base_config = e2ee_test_config

    # Mock dependencies as installed
    with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:
        mock_import.side_effect = lambda _: MagicMock()

        mock_deprecation_active.return_value = True

        # Mock paths_info with legacy sources
        mock_resolve_all_paths.return_value = {
            "credentials_path": "/primary/matrix/credentials.json",
            "legacy_sources": ["/legacy1", "/legacy2"],
            "legacy_active": True,
        }

        # Mock os.path.exists to return False for all credential paths
        def exists_side_effect(_path):
            """
            Always indicate that the given filesystem path does not exist.

            Returns:
                False for any input path.
            """
            return False

        mock_exists.side_effect = exists_side_effect

        # Call get_e2ee_status without config_path (uses HOME)
        status = get_e2ee_status(base_config)

        # Verify credentials are marked as unavailable
        assert status["credentials_available"] is False
        # Verify overall status is incomplete (credentials missing)
        assert status["overall_status"] == "incomplete"
        # Verify authentication issue is present
        assert "Matrix authentication not configured" in status["issues"]

        # Verify all locations were checked
        calls = mock_exists.call_args_list
        paths_checked = [call[0][0] for call in calls]
        # Should have checked primary and both legacy locations
        assert len(paths_checked) >= 3
        assert "/primary/matrix/credentials.json" in paths_checked


@patch("sys.platform", "linux")
@patch("mmrelay.e2ee_utils.os.path.exists")
@patch("mmrelay.e2ee_utils.resolve_all_paths")
@patch("mmrelay.e2ee_utils.is_deprecation_window_active")
def test_credentials_in_legacy_during_deprecation_window(
    mock_deprecation_active, mock_resolve_all_paths, mock_exists, e2ee_test_config
) -> None:
    """
    Test _check_credentials_available finds credentials in legacy location during deprecation window (lines 172-182).

    This test verifies that when deprecation window is active and credentials
    exist in a legacy location, _check_credentials_available returns True.
    """
    _temp_dir, config_path, _credentials_path, _base_config = e2ee_test_config

    # Mock dependencies as installed
    with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:
        mock_import.side_effect = lambda _: MagicMock()

        # Mock deprecation window as active
        mock_deprecation_active.return_value = True

        # Mock paths_info with legacy sources
        mock_resolve_all_paths.return_value = {
            "credentials_path": "/primary/matrix/credentials.json",
            "legacy_sources": ["/legacy1", "/legacy2"],
        }

        # Mock os.path.exists to find credentials in second legacy location
        def exists_side_effect(path):
            """
            Simulate os.path.exists for tests by returning True only for the second legacy credentials path.

            Parameters:
                path (str): Filesystem path to check.

            Returns:
                bool: True if `path` contains "credentials.json" and "legacy2", False otherwise.
            """
            if "credentials.json" in path:
                # Primary doesn't have it
                if "primary" in path:
                    return False
                # First legacy doesn't have it
                elif "legacy1" in path:
                    return False
                # Second legacy has it
                elif "legacy2" in path:
                    return True
            return False

        mock_exists.side_effect = exists_side_effect

        # Import and test _check_credentials_available
        result = _check_credentials_available(config_path)

        # Verify credentials were found
        assert result is True

        # Verify all locations were checked up to match
        calls = mock_exists.call_args_list
        paths_checked = [call[0][0] for call in calls]
        # Should have checked primary, legacy1, and legacy2
        assert len(paths_checked) >= 3
        assert "/primary/matrix/credentials.json" in paths_checked


@patch("sys.platform", "linux")
@patch("mmrelay.e2ee_utils.os.path.exists")
@patch("mmrelay.e2ee_utils.resolve_all_paths")
@patch("mmrelay.e2ee_utils.is_deprecation_window_active")
def test_no_credentials_during_deprecation_window(
    mock_deprecation_active, mock_resolve_all_paths, mock_exists, e2ee_test_config
) -> None:
    """
    Verify that _check_credentials_available reports credentials absent when deprecation window is active and no credential files exist.

    Mocks an active deprecation window and multiple legacy sources, asserts function returns False and that primary credentials path plus all legacy locations were checked.
    """
    _temp_dir, config_path, _credentials_path, _base_config = e2ee_test_config

    # Mock dependencies as installed
    with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:
        mock_import.side_effect = lambda _: MagicMock()

        # Mock deprecation window as active
        mock_deprecation_active.return_value = True

        # Mock paths_info with legacy sources
        mock_resolve_all_paths.return_value = {
            "credentials_path": "/primary/matrix/credentials.json",
            "legacy_sources": ["/legacy1", "/legacy2"],
        }

        # Mock os.path.exists to return False for all credential paths
        def exists_side_effect(_path):
            """
            Always indicate that the given filesystem path does not exist.

            Returns:
                False for any input path.
            """
            return False

        mock_exists.side_effect = exists_side_effect

        # Import and test _check_credentials_available
        result = _check_credentials_available(config_path)

        # Verify credentials were not found
        assert result is False

        # Verify all locations were checked
        calls = mock_exists.call_args_list
        paths_checked = [call[0][0] for call in calls]
        # Should have checked primary and both legacy locations
        assert len(paths_checked) >= 3
        assert "/primary/matrix/credentials.json" in paths_checked


@patch("sys.platform", "linux")
@patch("mmrelay.e2ee_utils.os.path.exists")
@patch("mmrelay.e2ee_utils.resolve_all_paths")
@patch("mmrelay.e2ee_utils.is_deprecation_window_active")
def test_deprecation_window_not_active(
    mock_deprecation_active, mock_resolve_all_paths, mock_exists, e2ee_test_config
) -> None:
    """
    Verify _check_credentials_available ignores legacy sources when deprecation window is inactive.

    Mocks deprecation window as inactive and supplies legacy_sources; ensures legacy locations are not considered and no credentials are reported found.
    """
    _temp_dir, config_path, _credentials_path, _base_config = e2ee_test_config

    # Mock dependencies as installed
    with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:
        mock_import.side_effect = lambda _: MagicMock()

        # Mock deprecation window as inactive
        mock_deprecation_active.return_value = False

        # Mock paths_info with legacy sources (should not be checked)
        mock_resolve_all_paths.return_value = {
            "credentials_path": "/primary/matrix/credentials.json",
            "legacy_sources": ["/legacy1", "/legacy2"],
        }

        # Mock os.path.exists to return False for all paths
        mock_exists.return_value = False

        # Import and test _check_credentials_available
        result = _check_credentials_available(config_path)

        # Verify credentials were not found
        assert result is False

        calls = mock_exists.call_args_list
        paths_checked = [call[0][0] for call in calls]
        assert "/primary/matrix/credentials.json" in paths_checked
        assert not any("legacy" in path for path in paths_checked)
