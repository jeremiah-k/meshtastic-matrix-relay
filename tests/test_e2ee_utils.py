"""
Tests for e2ee_utils module focusing on credential checking in legacy locations.

This module tests lines 115-122 and 172-182 of e2ee_utils.py:
- get_e2ee_status credential checking in legacy locations
- _check_credentials_available function with deprecation window handling
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from mmrelay.e2ee_utils import (
        get_e2ee_status,
    )

    IMPORTS_AVAILABLE = True
except ImportError:
    # Imports not available; dependent tests will be skipped.
    IMPORTS_AVAILABLE = False


class TestE2EEUtilsCredentialChecking(unittest.TestCase):
    """Test E2EE credential checking in legacy locations (lines 115-122, 172-182)."""

    def setUp(self) -> None:
        """
        Prepare test environment: skip tests if E2EE utilities are unavailable, create a temporary directory with config and credentials paths, and initialize a baseline config used by tests.

        The baseline config enables matrix E2EE, sets a meshtastic meshnet name ("TestNet"), and includes a single matrix room entry ("!room:test.org") mapped to meshtastic channel 0. Temporary paths created: self.temp_dir, self.config_path, and self.credentials_path.
        """
        if not IMPORTS_AVAILABLE:
            self.skipTest("Required imports not available")

        # Create temporary config file
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.yaml")
        self.credentials_path = os.path.join(self.temp_dir, "credentials.json")

        # Basic config
        self.base_config = {
            "matrix": {"e2ee": {"enabled": True}},
            "meshtastic": {"meshnet_name": "TestNet"},
            "matrix_rooms": [{"id": "!room:test.org", "meshtastic_channel": 0}],
        }

    def tearDown(self) -> None:
        """
        Remove temporary test directory created during setUp.

        This deletes directory referenced by self.temp_dir and its contents. Errors
        during removal are ignored (best-effort cleanup).
        """
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("sys.platform", "linux")
    @patch("mmrelay.e2ee_utils.os.path.exists")
    @patch("mmrelay.paths.resolve_all_paths")
    def test_credentials_found_in_legacy_location(
        self, mock_resolve_all_paths, mock_exists
    ) -> None:
        """
        Test credential detection when credentials are found in legacy location (lines 115-122).

        This test verifies that when credentials are not found in primary location
        but exist in a legacy location, get_e2ee_status correctly marks
        credentials as available and stops searching after first match.
        """
        # Mock dependencies as installed
        with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:
            mock_import.side_effect = lambda _: MagicMock()

            # Mock paths_info with legacy sources
            mock_resolve_all_paths.return_value = {
                "credentials_path": "/primary/credentials.json",
                "legacy_sources": ["/legacy1", "/legacy2", "/legacy3"],
            }

            # Mock os.path.exists to find credentials in second legacy location
            def exists_side_effect(path):
                """
                Simulate filesystem existence checks for credential file paths used by the tests.

                Parameters:
                    path (str): Filesystem path to evaluate. The function looks for "credentials.json"
                        and the substrings "primary", "legacy1", "legacy2", or "legacy3" to determine
                        the simulated result.

                Returns:
                    bool: `True` if the path refers to the credentials file in the second legacy
                    location ("legacy2"), `False` otherwise.
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
            status = get_e2ee_status(self.base_config)

            # Verify credentials are marked as available
            self.assertTrue(status["credentials_available"])
            # Verify overall status is ready (other requirements met)
            self.assertEqual(status["overall_status"], "ready")
            # Verify no authentication issue
            self.assertNotIn("Matrix authentication not configured", status["issues"])

            # Verify it stopped checking after first match (legacy2)
            # Should have checked primary, then legacy1, then legacy2
            # But NOT legacy3
            calls = mock_exists.call_args_list
            paths_checked = [call[0][0] for call in calls]
            # Should not have checked legacy3
            self.assertTrue(
                not any("legacy3" in path for path in paths_checked),
                "Should have stopped checking after finding credentials in legacy2",
            )

    @patch("sys.platform", "linux")
    @patch("mmrelay.e2ee_utils.os.path.exists")
    @patch("mmrelay.paths.resolve_all_paths")
    def test_credentials_not_found_in_legacy_locations(
        self, mock_resolve_all_paths, mock_exists
    ) -> None:
        """
        Test credential detection when credentials are not found in any location (lines 115-122).

        This test verifies that when credentials are not found in primary or any
        legacy locations, get_e2ee_status correctly marks credentials as
        unavailable and adds appropriate issue message.
        """
        # Mock dependencies as installed
        with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:
            mock_import.side_effect = lambda _: MagicMock()

            # Mock paths_info with legacy sources
            mock_resolve_all_paths.return_value = {
                "credentials_path": "/primary/credentials.json",
                "legacy_sources": ["/legacy1", "/legacy2"],
            }

            # Mock os.path.exists to return False for all credential paths
            def exists_side_effect(path):
                """
                Side-effect function used in tests to simulate that no file exists at any checked path.

                Parameters:
                    path (str): Filesystem path being queried.

                Returns:
                    bool: `False` for every input path to indicate the file does not exist.
                """
                if "credentials.json" in path:
                    return False
                return False

            mock_exists.side_effect = exists_side_effect

            # Call get_e2ee_status without config_path (uses HOME)
            status = get_e2ee_status(self.base_config)

            # Verify credentials are marked as unavailable
            self.assertFalse(status["credentials_available"])
            # Verify overall status is incomplete (credentials missing)
            self.assertEqual(status["overall_status"], "incomplete")
            # Verify authentication issue is present
            self.assertIn("Matrix authentication not configured", status["issues"])

            # Verify all locations were checked
            calls = mock_exists.call_args_list
            paths_checked = [call[0][0] for call in calls]
            # Should have checked primary and both legacy locations
            self.assertTrue(len(paths_checked) >= 3)

    @patch("sys.platform", "linux")
    @patch("mmrelay.e2ee_utils.os.path.exists")
    @patch("mmrelay.paths.resolve_all_paths")
    @patch("mmrelay.paths.is_deprecation_window_active")
    def test_credentials_in_legacy_during_deprecation_window(
        self, mock_deprecation_active, mock_resolve_all_paths, mock_exists
    ) -> None:
        """
        Test _check_credentials_available finds credentials in legacy location during deprecation window (lines 172-182).

        This test verifies that when deprecation window is active and credentials
        exist in a legacy location, _check_credentials_available returns True.
        """
        # Mock dependencies as installed
        with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:
            mock_import.side_effect = lambda _: MagicMock()

            # Mock deprecation window as active
            mock_deprecation_active.return_value = True

            # Mock paths_info with legacy sources
            mock_resolve_all_paths.return_value = {
                "credentials_path": "/primary/credentials.json",
                "legacy_sources": ["/legacy1", "/legacy2"],
            }

            # Mock os.path.exists to find credentials in second legacy location
            def exists_side_effect(path):
                """
                Simulate os.path.exists for test cases by reporting existence only for the second legacy credentials path.

                Parameters:
                    path (str): Filesystem path to check.

                Returns:
                    True if `path` contains "credentials.json" and "legacy2", `False` otherwise.
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
            from mmrelay.e2ee_utils import _check_credentials_available

            result = _check_credentials_available(self.config_path)

            # Verify credentials were found
            self.assertTrue(result)

            # Verify all locations were checked up to the match
            calls = mock_exists.call_args_list
            paths_checked = [call[0][0] for call in calls]
            # Should have checked primary, legacy1, and legacy2
            self.assertTrue(len(paths_checked) >= 3)

    @patch("sys.platform", "linux")
    @patch("mmrelay.e2ee_utils.os.path.exists")
    @patch("mmrelay.paths.resolve_all_paths")
    @patch("mmrelay.paths.is_deprecation_window_active")
    def test_no_credentials_during_deprecation_window(
        self, mock_deprecation_active, mock_resolve_all_paths, mock_exists
    ) -> None:
        """
        Verify that _check_credentials_available reports credentials absent when the deprecation window is active and no credential files exist.

        Mocks an active deprecation window and multiple legacy sources, asserts the function returns False and that the primary credentials path plus all legacy locations were checked.
        """
        # Mock dependencies as installed
        with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:
            mock_import.side_effect = lambda _: MagicMock()

            # Mock deprecation window as active
            mock_deprecation_active.return_value = True

            # Mock paths_info with legacy sources
            mock_resolve_all_paths.return_value = {
                "credentials_path": "/primary/credentials.json",
                "legacy_sources": ["/legacy1", "/legacy2"],
            }

            # Mock os.path.exists to return False for all credential paths
            def exists_side_effect(path):
                """
                Side-effect function used in tests to simulate that no file exists at any checked path.

                Parameters:
                    path (str): Filesystem path being queried.

                Returns:
                    bool: `False` for every input path to indicate the file does not exist.
                """
                if "credentials.json" in path:
                    return False
                return False

            mock_exists.side_effect = exists_side_effect

            # Import and test _check_credentials_available
            from mmrelay.e2ee_utils import _check_credentials_available

            result = _check_credentials_available(self.config_path)

            # Verify credentials were not found
            self.assertFalse(result)

            # Verify all locations were checked
            calls = mock_exists.call_args_list
            paths_checked = [call[0][0] for call in calls]
            # Should have checked primary and both legacy locations
            self.assertTrue(len(paths_checked) >= 3)

    @patch("sys.platform", "linux")
    @patch("mmrelay.e2ee_utils.os.path.exists")
    @patch("mmrelay.paths.resolve_all_paths")
    @patch("mmrelay.paths.is_deprecation_window_active")
    def test_deprecation_window_not_active(
        self, mock_deprecation_active, mock_resolve_all_paths, mock_exists
    ) -> None:
        """
        Verify _check_credentials_available ignores legacy sources when the deprecation window is inactive.

        Mocks the deprecation window as inactive and supplies legacy_sources; ensures legacy locations are not considered and no credentials are reported found.
        """
        # Mock dependencies as installed
        with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:
            mock_import.side_effect = lambda _: MagicMock()

            # Mock deprecation window as inactive
            mock_deprecation_active.return_value = False

            # Mock paths_info with legacy sources (should not be checked)
            mock_resolve_all_paths.return_value = {
                "credentials_path": "/primary/credentials.json",
                "legacy_sources": ["/legacy1", "/legacy2"],
            }

            # Mock os.path.exists to return False for all paths
            mock_exists.return_value = False

            # Import and test _check_credentials_available
            from mmrelay.e2ee_utils import _check_credentials_available

            result = _check_credentials_available(self.config_path)

            # Verify credentials were not found
            self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
