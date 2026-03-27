"""
Test cases for Windows utilities functionality.

This module tests the Windows-specific utilities added for compatibility improvements.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from mmrelay.constants.app import WINDOWS_VTP_FLAG
from mmrelay.windows_utils import (
    check_windows_requirements,
    get_windows_error_message,
    get_windows_install_guidance,
    is_windows,
    setup_windows_console,
)
from mmrelay.windows_utils import (
    test_config_generation_windows as windows_test_config_generation,
)


class TestIsWindows(unittest.TestCase):
    """Test cases for is_windows function."""

    def test_is_windows_true_on_windows(self):
        """Test is_windows returns True on Windows platform."""
        with patch("sys.platform", "win32"):
            self.assertTrue(is_windows())

    def test_is_windows_false_on_linux(self):
        """Test is_windows returns False on Linux platform."""
        with patch("sys.platform", "linux"):
            self.assertFalse(is_windows())

    def test_is_windows_false_on_macos(self):
        """Test is_windows returns False on macOS platform."""
        with patch("sys.platform", "darwin"):
            self.assertFalse(is_windows())


class TestSetupWindowsConsole(unittest.TestCase):
    """Test cases for setup_windows_console function."""

    @patch("sys.platform", "win32")
    def test_setup_windows_console_on_windows(self):
        """Test setup_windows_console runs on Windows without errors."""
        # Since ctypes.windll doesn't exist on Linux, we just test that
        # the function doesn't raise an exception when called on Windows
        try:
            setup_windows_console()
        except Exception as e:
            # Should not raise an exception - it should handle errors gracefully
            self.fail(f"setup_windows_console raised an exception: {e}")

    @patch("sys.platform", "linux")
    @patch("os.system")
    def test_setup_windows_console_on_non_windows(self, mock_system):
        """Test setup_windows_console does nothing on non-Windows."""
        # Execute
        setup_windows_console()

        # Verify os.system was not called
        mock_system.assert_not_called()

    @patch("sys.platform", "win32")
    @patch("os.system", side_effect=Exception("Test error"))
    def test_setup_windows_console_handles_exceptions(self, mock_system):
        """Test setup_windows_console handles exceptions gracefully."""
        # Execute - should not raise exception
        try:
            setup_windows_console()
        except Exception:
            self.fail("setup_windows_console should handle exceptions gracefully")

    def test_setup_windows_console_enables_utf8_and_vt_mode(self):
        """Windows console setup should reconfigure streams and enable VT mode."""
        mock_stdout = MagicMock()
        mock_stderr = MagicMock()
        mock_kernel32 = MagicMock()
        mock_stdout_handle = MagicMock()
        mock_stderr_handle = MagicMock()

        def _get_console_mode(_handle, mode_ref):
            mode_ref._obj.value = 7
            return True

        mock_kernel32.GetStdHandle.side_effect = [
            mock_stdout_handle,
            mock_stderr_handle,
        ]
        mock_kernel32.GetConsoleMode.side_effect = _get_console_mode

        with (
            patch("mmrelay.windows_utils.is_windows", return_value=True),
            patch.object(sys, "stdout", mock_stdout),
            patch.object(sys, "stderr", mock_stderr),
            patch("ctypes.windll", create=True) as mock_windll,
        ):
            mock_windll.kernel32 = mock_kernel32
            setup_windows_console()

        mock_stdout.reconfigure.assert_called_once_with(encoding="utf-8")
        mock_stderr.reconfigure.assert_called_once_with(encoding="utf-8")
        self.assertEqual(mock_kernel32.GetConsoleMode.call_count, 2)
        self.assertEqual(mock_kernel32.SetConsoleMode.call_count, 2)
        set_calls = mock_kernel32.SetConsoleMode.call_args_list
        self.assertIs(set_calls[0].args[0], mock_stdout_handle)
        self.assertEqual(set_calls[0].args[1], 7 | WINDOWS_VTP_FLAG)
        self.assertIs(set_calls[1].args[0], mock_stderr_handle)
        self.assertEqual(set_calls[1].args[1], 7 | WINDOWS_VTP_FLAG)

    def test_setup_windows_console_skips_invalid_std_handles(self):
        """Invalid console handles should skip mode probes cleanly."""
        mock_stdout = MagicMock()
        mock_stderr = MagicMock()
        mock_kernel32 = MagicMock()
        mock_kernel32.GetStdHandle.side_effect = [None, -1]

        with (
            patch("mmrelay.windows_utils.is_windows", return_value=True),
            patch.object(sys, "stdout", mock_stdout),
            patch.object(sys, "stderr", mock_stderr),
            patch("ctypes.windll", create=True) as mock_windll,
        ):
            mock_windll.kernel32 = mock_kernel32
            setup_windows_console()

        mock_kernel32.GetConsoleMode.assert_not_called()
        mock_kernel32.SetConsoleMode.assert_not_called()


class TestGetWindowsErrorMessage(unittest.TestCase):
    """Test cases for get_windows_error_message function."""

    @patch("sys.platform", "win32")
    def test_get_windows_error_message_file_not_found(self):
        """Test Windows error message for FileNotFoundError."""
        error = FileNotFoundError("file not found")

        result = get_windows_error_message(error)

        self.assertIn("File not found", result)
        self.assertIn("Incorrect file path", result)
        self.assertIn("antivirus", result)

    @patch("sys.platform", "win32")
    def test_get_windows_error_message_permission_error(self):
        """Test Windows error message for PermissionError."""
        error = PermissionError("access is denied")

        result = get_windows_error_message(error)

        self.assertIn("Permission denied", result)
        self.assertIn("administrator", result)
        self.assertIn("antivirus", result)

    @patch("sys.platform", "win32")
    def test_get_windows_error_message_os_error(self):
        """Test Windows error message for OSError."""
        error = OSError("Some generic OS error")

        result = get_windows_error_message(error)

        # For generic OS errors, it just returns the string
        self.assertEqual(result, "Some generic OS error")

    @patch("sys.platform", "win32")
    def test_get_windows_error_message_connection_error(self):
        """Test Windows error message for ConnectionError."""
        error = ConnectionError("network is unreachable")

        result = get_windows_error_message(error)

        self.assertIn("Network error", result)
        self.assertIn("Firewall", result)
        self.assertIn("antivirus", result)

    def test_get_windows_error_message_generic_error(self):
        """Test Windows error message for generic Exception."""
        error = Exception("Some generic error")

        result = get_windows_error_message(error)

        self.assertEqual(result, "Some generic error")


class TestCheckWindowsRequirements(unittest.TestCase):
    """Test cases for check_windows_requirements function."""

    @patch("sys.platform", "linux")
    def test_check_windows_requirements_non_windows(self):
        """Test check_windows_requirements returns None on non-Windows."""
        result = check_windows_requirements()
        self.assertIsNone(result)

    @patch("sys.platform", "win32")
    @patch("sys.version_info", (3, 8, 0))
    def test_check_windows_requirements_old_python(self):
        """Test check_windows_requirements warns about old Python."""
        result = check_windows_requirements()

        self.assertIsNotNone(result)
        self.assertIn("Python 3.10+ is required", result)

    @patch("sys.platform", "win32")
    @patch("sys.version_info", (3, 12, 0))
    @patch(
        "os.getcwd",
        return_value="C:\\very\\long\\path\\that\\is\\over\\two\\hundred\\characters\\long\\and\\should\\trigger\\a\\warning\\about\\windows\\path\\length\\limitations\\which\\can\\cause\\various\\issues\\with\\file\\operations\\and\\package\\installations",
    )
    def test_check_windows_requirements_long_path(self, mock_getcwd):
        """Test check_windows_requirements warns about long paths."""
        result = check_windows_requirements()

        self.assertIsNotNone(result)
        self.assertIn("path is very long", result)

    @patch("sys.platform", "win32")
    @patch("sys.version_info", (3, 12, 0))
    @patch("sys.prefix", "/usr")  # Mock to make it look like not in venv
    @patch("sys.base_prefix", "/usr")  # Mock to make it look like not in venv
    @patch("importlib.util.find_spec", return_value=MagicMock())
    def test_check_windows_requirements_all_good(self, mock_find_spec):
        """Test check_windows_requirements returns virtual environment warning."""
        result = check_windows_requirements()

        self.assertIsNotNone(result)
        self.assertIn("Consider using a virtual environment", result)

    def test_check_windows_requirements_returns_none_when_clean(self):
        """No compatibility warnings should return None."""
        with (
            patch("mmrelay.windows_utils.is_windows", return_value=True),
            patch("sys.version_info", (3, 12, 0)),
            patch("sys.base_prefix", "/usr"),
            patch("sys.prefix", "/venv"),
            patch("os.getcwd", return_value="C:\\relay"),
            patch("importlib.util.find_spec", return_value=None),
        ):
            self.assertIsNone(check_windows_requirements())


class TestTestConfigGenerationWindows(unittest.TestCase):
    """Test cases for test_config_generation_windows function."""

    @patch("sys.platform", "linux")
    def test_test_config_generation_windows_non_windows(self):
        """Test test_config_generation_windows returns error on non-Windows."""
        result = windows_test_config_generation(None)

        self.assertIn("error", result)
        self.assertEqual(result["error"], "This function is only for Windows systems")

    @patch("sys.platform", "win32")
    @patch("mmrelay.tools.get_sample_config_path")
    @patch("os.path.exists")
    def test_test_config_generation_windows_success(
        self, mock_exists, mock_get_sample_config_path
    ):
        """Test test_config_generation_windows success case."""
        # Setup mocks
        mock_get_sample_config_path.return_value = "/path/to/sample_config.yaml"
        mock_exists.return_value = True

        with (
            patch("importlib.resources.files") as mock_files,
            patch("mmrelay.config.get_config_paths") as mock_get_config_paths,
            patch("os.makedirs"),
        ):
            mock_joinpath = MagicMock()
            mock_joinpath.read_text.return_value = "sample: config"
            mock_files.return_value.joinpath.return_value = mock_joinpath
            mock_get_config_paths.return_value = ["/path/to/config.yaml"]
            result = windows_test_config_generation(None)

        # Verify
        self.assertEqual(result["overall_status"], "ok")
        self.assertEqual(result["sample_config_path"]["status"], "ok")
        self.assertEqual(result["importlib_resources"]["status"], "ok")

    @patch("sys.platform", "win32")
    @patch("mmrelay.tools.get_sample_config_path", side_effect=OSError("Test error"))
    def test_test_config_generation_windows_handles_exceptions(
        self, mock_get_sample_config_path
    ):
        """Test test_config_generation_windows handles exceptions."""
        result = windows_test_config_generation(None)

        self.assertEqual(result["overall_status"], "partial")
        self.assertEqual(result["sample_config_path"]["status"], "error")

    def test_test_config_generation_windows_sample_path_missing(self):
        """Missing sample config path should mark sample test as error."""
        with tempfile.TemporaryDirectory() as tmp_path:
            missing_sample = os.path.join(tmp_path, "missing_sample.yaml")
            config_path = os.path.join(tmp_path, "config.yaml")
            with (
                patch("mmrelay.windows_utils.is_windows", return_value=True),
                patch(
                    "mmrelay.tools.get_sample_config_path",
                    return_value=missing_sample,
                ),
                patch("os.path.exists", return_value=False),
                patch("importlib.resources.files") as mock_files,
                patch("mmrelay.config.get_config_paths", return_value=[config_path]),
            ):
                mock_joinpath = MagicMock()
                mock_joinpath.read_text.return_value = "sample: config"
                mock_files.return_value.joinpath.return_value = mock_joinpath
                result = windows_test_config_generation(None)

        self.assertEqual(result["sample_config_path"]["status"], "error")
        self.assertEqual(result["overall_status"], "partial")

    def test_test_config_generation_windows_importlib_resources_error(self):
        """importlib.resources errors should be captured in diagnostics."""
        with tempfile.TemporaryDirectory() as tmp_path:
            sample_config = os.path.join(tmp_path, "sample_config.yaml")
            config_path = os.path.join(tmp_path, "config.yaml")
            with (
                patch("mmrelay.windows_utils.is_windows", return_value=True),
                patch(
                    "mmrelay.tools.get_sample_config_path",
                    return_value=sample_config,
                ),
                patch("os.path.exists", return_value=True),
                patch(
                    "importlib.resources.files",
                    side_effect=FileNotFoundError("missing"),
                ),
                patch("mmrelay.config.get_config_paths", return_value=[config_path]),
            ):
                result = windows_test_config_generation(None)

        self.assertEqual(result["importlib_resources"]["status"], "error")
        self.assertEqual(result["overall_status"], "partial")

    def test_test_config_generation_windows_config_paths_error(self):
        """Config path resolution failures should be recorded as errors."""
        with tempfile.TemporaryDirectory() as tmp_path:
            sample_config = os.path.join(tmp_path, "sample_config.yaml")
            with (
                patch("mmrelay.windows_utils.is_windows", return_value=True),
                patch(
                    "mmrelay.tools.get_sample_config_path",
                    return_value=sample_config,
                ),
                patch("os.path.exists", return_value=True),
                patch("importlib.resources.files") as mock_files,
                patch(
                    "mmrelay.config.get_config_paths", side_effect=OSError("paths fail")
                ),
            ):
                mock_joinpath = MagicMock()
                mock_joinpath.read_text.return_value = "sample: config"
                mock_files.return_value.joinpath.return_value = mock_joinpath
                result = windows_test_config_generation(None)

        self.assertEqual(result["config_paths"]["status"], "error")
        self.assertEqual(result["directory_creation"]["status"], "error")

    def test_test_config_generation_windows_creates_missing_dirs(self):
        """Directory creation diagnostic should report created directories."""
        with tempfile.TemporaryDirectory() as tmp_path:
            sample_config = os.path.join(tmp_path, "sample_config.yaml")
            new_dir = os.path.join(tmp_path, "new")
            new_config = os.path.join(tmp_path, "new", "config.yaml")
            with (
                patch("mmrelay.windows_utils.is_windows", return_value=True),
                patch(
                    "mmrelay.tools.get_sample_config_path",
                    return_value=sample_config,
                ),
                patch("importlib.resources.files") as mock_files,
                patch("mmrelay.config.get_config_paths", return_value=[new_config]),
                patch("os.makedirs") as mock_makedirs,
            ):
                mock_joinpath = MagicMock()
                mock_joinpath.read_text.return_value = "sample: config"
                mock_files.return_value.joinpath.return_value = mock_joinpath

                def _exists_side_effect(path: str) -> bool:
                    if path == sample_config:
                        return True
                    return False

                with patch("os.path.exists", side_effect=_exists_side_effect):
                    result = windows_test_config_generation(None)

        mock_makedirs.assert_called_once_with(new_dir, exist_ok=True)
        self.assertEqual(result["directory_creation"]["status"], "ok")
        self.assertIn(new_dir, result["directory_creation"]["details"])

    def test_test_config_generation_windows_directory_creation_oserror(self):
        """Directory creation OSError should be captured as diagnostic error."""
        with tempfile.TemporaryDirectory() as tmp_path:
            sample_config = os.path.join(tmp_path, "sample_config.yaml")
            new_config = os.path.join(tmp_path, "new", "config.yaml")
            with (
                patch("mmrelay.windows_utils.is_windows", return_value=True),
                patch(
                    "mmrelay.tools.get_sample_config_path",
                    return_value=sample_config,
                ),
                patch("importlib.resources.files") as mock_files,
                patch("mmrelay.config.get_config_paths", return_value=[new_config]),
                patch("os.path.exists", side_effect=lambda p: p == sample_config),
                patch("os.makedirs", side_effect=OSError("cannot create")),
            ):
                mock_joinpath = MagicMock()
                mock_joinpath.read_text.return_value = "sample: config"
                mock_files.return_value.joinpath.return_value = mock_joinpath
                result = windows_test_config_generation(None)

        self.assertEqual(result["directory_creation"]["status"], "error")

    # Note: patches builtins.sum to force OSError - implementation uses sum() for status aggregation
    def test_test_config_generation_windows_outer_oserror_sets_overall_error(self):
        """Unexpected outer OSError should mark overall_status=error with details.

        This validates the outer OSError handler by forcing the status aggregation
        helper to raise after inner check handlers have completed.
        """
        with tempfile.TemporaryDirectory() as tmp_path:
            sample_config = os.path.join(tmp_path, "sample_config.yaml")
            config_path = os.path.join(tmp_path, "config.yaml")
            with (
                patch("mmrelay.windows_utils.is_windows", return_value=True),
                patch(
                    "mmrelay.tools.get_sample_config_path",
                    return_value=sample_config,
                ),
                patch("os.path.exists", return_value=True),
                patch("importlib.resources.files") as mock_files,
                patch("mmrelay.config.get_config_paths", return_value=[config_path]),
                patch("builtins.sum", side_effect=OSError("sum failure")),
            ):
                mock_joinpath = MagicMock()
                mock_joinpath.read_text.return_value = "sample: config"
                mock_files.return_value.joinpath.return_value = mock_joinpath
                result = windows_test_config_generation(None)

        self.assertEqual(result["overall_status"], "error")
        self.assertIn("sum failure", result.get("error", ""))

    def test_test_config_generation_windows_error_status_when_all_checks_fail(self):
        """Three or more check errors should produce overall_status='error'."""
        with (
            patch("mmrelay.windows_utils.is_windows", return_value=True),
            patch(
                "mmrelay.tools.get_sample_config_path", side_effect=OSError("sample")
            ),
            patch(
                "importlib.resources.files", side_effect=FileNotFoundError("importlib")
            ),
            patch(
                "mmrelay.config.get_config_paths", side_effect=OSError("config paths")
            ),
        ):
            result = windows_test_config_generation(None)

        self.assertEqual(result["overall_status"], "error")


class TestGetWindowsInstallGuidance(unittest.TestCase):
    """Test cases for get_windows_install_guidance function."""

    def test_get_windows_install_guidance_returns_string(self):
        """Test get_windows_install_guidance returns a string."""
        result = get_windows_install_guidance()

        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_get_windows_install_guidance_contains_key_info(self):
        """Test get_windows_install_guidance contains key information."""
        result = get_windows_install_guidance()

        # Check for key sections
        self.assertIn("pipx install mmrelay", result)
        self.assertIn("pip install --user mmrelay", result)
        self.assertIn("ModuleNotFoundError", result)
        self.assertIn("Access denied", result)
        self.assertIn("SSL certificate", result)
        self.assertIn("Antivirus", result)
        self.assertIn("Long path", result)
        self.assertIn("config diagnose", result)


if __name__ == "__main__":
    unittest.main()
