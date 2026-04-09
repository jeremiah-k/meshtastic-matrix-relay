"""
Tests for e2ee_utils module focusing on credential checking in legacy locations.

This module tests lines 115-122 and 172-182 of e2ee_utils.py:
- get_e2ee_status credential checking in legacy locations
- _check_credentials_available function with deprecation window handling
"""

import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from mmrelay.constants.app import CONFIG_FILENAME, CREDENTIALS_FILENAME
from mmrelay.constants.messages import MSG_E2EE_NO_AUTH
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
            - config_path (str): Path within temp_dir for the config file.
            - credentials_path (str): Path within temp_dir for the credentials file.
            - base_config (dict): Minimal configuration dict with E2EE enabled, a meshtastic meshnet_name,
              and one example matrix room mapping.

    Notes:
        The temporary directory is removed after the fixture is finished.
    """
    # Create temporary directory
    temp_dir = tempfile.mkdtemp()
    config_path = os.path.join(temp_dir, CONFIG_FILENAME)
    credentials_path = os.path.join(temp_dir, "matrix", CREDENTIALS_FILENAME)

    # Basic config
    base_config = {
        "matrix": {"e2ee": {"enabled": True}},
        "meshtastic": {"meshnet_name": "TestNet"},
        "matrix_rooms": [{"id": "!room:test.org", "meshtastic_channel": 0}],
    }

    yield temp_dir, config_path, credentials_path, base_config

    # Cleanup
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
            "credentials_path": f"/primary/matrix/{CREDENTIALS_FILENAME}",
            "legacy_sources": ["/legacy1", "/legacy2", "/legacy3"],
            "legacy_active": True,
        }

        # Mock os.path.exists to find credentials in second legacy location
        def exists_side_effect(path):
            """
            Simulate os.path.exists responses for test credential paths, returning True only for the legacy2 credentials file.

            Parameters:
                path (str): Path to check; the function looks for the credentials filename and the substrings "primary", "legacy1", "legacy2", or "legacy3" to decide the simulated result.

            Returns:
                bool: True if the path refers to the credentials file in "legacy2", False otherwise.
            """
            if CREDENTIALS_FILENAME in path:
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
        assert MSG_E2EE_NO_AUTH not in status["issues"]

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
            "credentials_path": f"/primary/matrix/{CREDENTIALS_FILENAME}",
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
        assert MSG_E2EE_NO_AUTH in status["issues"]

        # Verify all locations were checked
        calls = mock_exists.call_args_list
        paths_checked = [call[0][0] for call in calls]
        # Should have checked primary and both legacy locations
        assert len(paths_checked) >= 3
        assert f"/primary/matrix/{CREDENTIALS_FILENAME}" in paths_checked


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

    # Mock deprecation window as active
    mock_deprecation_active.return_value = True

    # Mock paths_info with legacy sources
    mock_resolve_all_paths.return_value = {
        "credentials_path": f"/primary/matrix/{CREDENTIALS_FILENAME}",
        "legacy_sources": ["/legacy1", "/legacy2"],
    }

    # Mock os.path.exists to find credentials in second legacy location
    def exists_side_effect(path):
        """
        Simulate os.path.exists for tests by returning True only for the second legacy credentials path.

        Parameters:
            path (str): Filesystem path to check.

        Returns:
            bool: True if `path` contains the credentials filename and "legacy2", False otherwise.
        """
        if CREDENTIALS_FILENAME in path:
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
    assert f"/primary/matrix/{CREDENTIALS_FILENAME}" in paths_checked


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

    # Mock deprecation window as active
    mock_deprecation_active.return_value = True

    # Mock paths_info with legacy sources
    mock_resolve_all_paths.return_value = {
        "credentials_path": f"/primary/matrix/{CREDENTIALS_FILENAME}",
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
    assert f"/primary/matrix/{CREDENTIALS_FILENAME}" in paths_checked


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

    # Mock deprecation window as inactive
    mock_deprecation_active.return_value = False

    # Mock paths_info with legacy sources (should not be checked)
    mock_resolve_all_paths.return_value = {
        "credentials_path": f"/primary/matrix/{CREDENTIALS_FILENAME}",
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
    assert f"/primary/matrix/{CREDENTIALS_FILENAME}" in paths_checked
    assert not any("legacy" in path for path in paths_checked)


# ---- Additional coverage tests for e2ee_utils.py uncovered lines ----


class TestCheckCredentialsAvailable:
    """Tests for _check_credentials_available edge cases."""

    @patch("mmrelay.e2ee_utils.os.path.exists")
    @patch("mmrelay.e2ee_utils.resolve_all_paths")
    def test_paths_info_none_triggers_resolve(self, mock_resolve, mock_exists):
        """When paths_info is None, resolve_all_paths should be called."""
        mock_resolve.return_value = {
            "credentials_path": "/found/creds.json",
            "home": None,
        }
        mock_exists.return_value = True

        result = _check_credentials_available(None, paths_info=None)
        assert result is True
        mock_resolve.assert_called_once()

    @patch("mmrelay.e2ee_utils.os.path.exists")
    @patch("mmrelay.e2ee_utils.resolve_all_paths")
    def test_home_string_legacy_same_home_path(self, mock_resolve, mock_exists):
        """When home is a string and primary not found, check legacy same-home location."""
        call_count = {"n": 0}

        def exists_side_effect(path):
            if "primary" in path:
                return False
            if CREDENTIALS_FILENAME in path and "home_root" in path:
                return True
            return False

        mock_resolve.return_value = {
            "credentials_path": "/primary/matrix/credentials.json",
            "home": "/home_root",
        }
        mock_exists.side_effect = exists_side_effect

        result = _check_credentials_available(None)
        assert result is True

    @patch("mmrelay.e2ee_utils.os.path.exists")
    @patch("mmrelay.e2ee_utils.resolve_all_paths")
    @patch("mmrelay.e2ee_utils.is_deprecation_window_active", return_value=True)
    def test_legacy_sources_during_deprecation_window(
        self, mock_deprecation, mock_resolve, mock_exists
    ):
        """Credentials found in legacy source during deprecation window."""
        mock_resolve.return_value = {
            "credentials_path": "/primary/matrix/credentials.json",
            "home": None,
            "legacy_sources": ["/legacy1"],
        }
        mock_exists.side_effect = lambda p: "legacy1" in p and CREDENTIALS_FILENAME in p

        result = _check_credentials_available(None)
        assert result is True


class MockRoom:
    def __init__(self, room_id, display_name="Test", encrypted=False):
        self.room_id = room_id
        self.display_name = display_name
        self.encrypted = encrypted


class TestRoomEncryptionWarnings:
    """Tests for get_room_encryption_warnings."""

    def test_no_encrypted_rooms(self):
        """No warnings when there are no encrypted rooms."""
        from mmrelay.e2ee_utils import get_room_encryption_warnings

        rooms = {"!r1:t": MockRoom("!r1:t", encrypted=False)}
        status = {"overall_status": "disabled"}
        result = get_room_encryption_warnings(rooms, status)
        assert result == []

    def test_unavailable_status_encrypted_rooms(self):
        """Warning for encrypted rooms on unsupported platform."""
        from mmrelay.e2ee_utils import get_room_encryption_warnings

        rooms = {"!r1:t": MockRoom("!r1:t", encrypted=True)}
        status = {"overall_status": "unavailable"}
        result = get_room_encryption_warnings(rooms, status)
        assert len(result) == 2
        assert "will be blocked" in result[1]

    def test_disabled_status_encrypted_rooms(self):
        """Warning for encrypted rooms when E2EE is disabled."""
        from mmrelay.e2ee_utils import get_room_encryption_warnings

        rooms = {"!r1:t": MockRoom("!r1:t", encrypted=True)}
        status = {"overall_status": "disabled"}
        result = get_room_encryption_warnings(rooms, status)
        assert len(result) == 2
        assert "will be blocked" in result[1]

    def test_incomplete_status_encrypted_rooms(self):
        """Warning for encrypted rooms when E2EE is incomplete."""
        from mmrelay.e2ee_utils import get_room_encryption_warnings

        rooms = {"!r1:t": MockRoom("!r1:t", encrypted=True)}
        status = {"overall_status": "incomplete"}
        result = get_room_encryption_warnings(rooms, status)
        assert len(result) == 2
        assert "may be blocked" in result[1]


class TestFormatRoomList:
    """Tests for format_room_list."""

    def test_incomplete_status_encrypted_room(self):
        """Encrypted room with incomplete status should show incomplete warning."""
        from mmrelay.e2ee_utils import format_room_list

        rooms = {"!r1:t": MockRoom("!r1:t", "MyRoom", encrypted=True)}
        status = {"overall_status": "incomplete"}
        result = format_room_list(rooms, status)
        assert len(result) == 1
        assert "incomplete" in result[0].lower()


class TestGetE2eeErrorMessage:
    """Tests for get_e2ee_error_message."""

    def test_ready_returns_empty(self):
        """Ready status should return empty string."""
        from mmrelay.e2ee_utils import get_e2ee_error_message

        result = get_e2ee_error_message({"overall_status": "ready"})
        assert result == ""

    def test_platform_not_supported(self):
        """Unavailable platform should return unavailable message."""
        from mmrelay.e2ee_utils import get_e2ee_error_message

        result = get_e2ee_error_message(
            {"overall_status": "unavailable", "platform_supported": False}
        )
        assert "Windows" in result

    def test_not_enabled(self):
        """Disabled E2EE should return disabled message."""
        from mmrelay.e2ee_utils import get_e2ee_error_message

        result = get_e2ee_error_message(
            {"overall_status": "disabled", "enabled": False, "platform_supported": True}
        )
        assert "disabled" in result.lower()

    def test_missing_deps(self):
        """Missing dependencies should return deps message."""
        from mmrelay.e2ee_utils import get_e2ee_error_message

        result = get_e2ee_error_message(
            {
                "overall_status": "incomplete",
                "enabled": True,
                "platform_supported": True,
                "dependencies_installed": False,
            }
        )
        assert "dependencies" in result.lower()

    def test_missing_credentials(self):
        """Missing credentials should return auth message."""
        from mmrelay.e2ee_utils import get_e2ee_error_message

        result = get_e2ee_error_message(
            {
                "overall_status": "incomplete",
                "enabled": True,
                "platform_supported": True,
                "dependencies_installed": True,
                "credentials_available": False,
            }
        )
        assert "auth" in result.lower() or "credentials" in result.lower()

    def test_generic_incomplete(self):
        """Generic incomplete should return incomplete message."""
        from mmrelay.e2ee_utils import get_e2ee_error_message

        result = get_e2ee_error_message(
            {
                "overall_status": "incomplete",
                "enabled": True,
                "platform_supported": True,
                "dependencies_installed": True,
                "credentials_available": True,
            }
        )
        assert "incomplete" in result.lower()


class TestGetE2eeFixInstructions:
    """Tests for get_e2ee_fix_instructions."""

    def test_ready_status(self):
        """Ready status should return confirmation."""
        from mmrelay.e2ee_utils import get_e2ee_fix_instructions

        result = get_e2ee_fix_instructions({"overall_status": "ready"})
        assert len(result) == 1
        assert "fully configured" in result[0]

    def test_missing_deps_step(self):
        """Missing deps should include install step."""
        from mmrelay.e2ee_utils import get_e2ee_fix_instructions

        result = get_e2ee_fix_instructions(
            {
                "overall_status": "incomplete",
                "platform_supported": True,
                "dependencies_installed": False,
                "credentials_available": True,
                "enabled": True,
            }
        )
        assert any("Install E2EE" in step for step in result)

    def test_missing_credentials_step(self):
        """Missing credentials should include auth step."""
        from mmrelay.e2ee_utils import get_e2ee_fix_instructions

        result = get_e2ee_fix_instructions(
            {
                "overall_status": "incomplete",
                "platform_supported": True,
                "dependencies_installed": True,
                "credentials_available": False,
                "enabled": True,
            }
        )
        assert any("Matrix authentication" in step for step in result)

    def test_not_enabled_step(self):
        """Not enabled should include config step."""
        from mmrelay.e2ee_utils import get_e2ee_fix_instructions

        result = get_e2ee_fix_instructions(
            {
                "overall_status": "disabled",
                "platform_supported": True,
                "dependencies_installed": True,
                "credentials_available": True,
                "enabled": False,
            }
        )
        assert any("Enable E2EE" in step for step in result)
