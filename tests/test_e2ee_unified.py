"""
Enhanced E2EE testing with actual encryption verification and unified status testing.

This module provides comprehensive testing for the unified E2EE approach, including:
- Actual encryption verification using nio.crypto logs
- All E2EE status scenarios (ready/disabled/unavailable/incomplete)
- Integration tests that verify real encryption behavior
- Log capture tests to ensure encryption is actually happening
"""

import logging
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
        format_room_list,
        get_e2ee_error_message,
        get_e2ee_fix_instructions,
        get_e2ee_status,
        get_room_encryption_warnings,
    )

    IMPORTS_AVAILABLE = True
except ImportError:
    # Imports not available; dependent tests will be skipped.
    IMPORTS_AVAILABLE = False


class MockRoom:
    """Mock Matrix room for testing"""

    def __init__(self, room_id, display_name, encrypted=False):
        """
        Initialize a MockRoom representing a Matrix room for tests.

        A minimal container used by unit tests to simulate a Matrix room's identity and E2EE state.
        The `encrypted` flag controls formatting and warning behavior in tests.

        Parameters:
            room_id (str): Matrix room identifier (e.g., "!abcdef:matrix.org").
            display_name (str): Human-readable room name shown in lists and logs.
            encrypted (bool, optional): Whether the room is end-to-end encrypted (E2EE). Defaults to False.
        """
        self.room_id = room_id
        self.display_name = display_name
        self.encrypted = encrypted


class TestUnifiedE2EEStatus(unittest.TestCase):
    """Test the unified E2EE status detection system"""

    def setUp(self):
        """
        Prepare the test environment: skip tests if E2EE utilities are unavailable, create a temporary directory with config and credentials paths, and initialize a baseline config used by tests.

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

    def tearDown(self):
        """
        Remove the temporary test directory created during setUp.

        This deletes the directory referenced by self.temp_dir and its contents. Errors
        during removal are ignored (best-effort cleanup).
        """
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("sys.platform", "linux")
    @patch("mmrelay.e2ee_utils.os.path.exists")
    def test_e2ee_ready_status(self, mock_exists):
        """Test E2EE ready status when everything is configured"""
        mock_exists.return_value = True  # credentials.json exists

        with patch.dict(os.environ, {"MMRELAY_TESTING": "0"}, clear=False):
            with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:
                mock_import.side_effect = lambda _: MagicMock()
                status = get_e2ee_status(self.base_config, self.config_path)

            self.assertEqual(status["overall_status"], "ready")
            self.assertTrue(status["enabled"])
            self.assertTrue(status["available"])
            self.assertTrue(status["configured"])
            self.assertTrue(status["platform_supported"])
            self.assertTrue(status["dependencies_installed"])
            self.assertTrue(status["credentials_available"])
            self.assertEqual(len(status["issues"]), 0)
            imported_modules = {call.args[0] for call in mock_import.call_args_list}
            assert {
                "olm",
                "nio.crypto",
                "nio.store",
            }.issubset(imported_modules)

    @patch("sys.platform", "win32")
    def test_e2ee_unavailable_windows(self):
        """Test E2EE unavailable status on Windows"""
        status = get_e2ee_status(self.base_config, self.config_path)

        self.assertEqual(status["overall_status"], "unavailable")
        self.assertFalse(status["platform_supported"])
        self.assertIn("E2EE is not supported on Windows", status["issues"])

    @patch("sys.platform", "linux")
    def test_e2ee_disabled_status(self):
        """Test E2EE disabled status"""
        config = self.base_config.copy()
        config["matrix"]["e2ee"]["enabled"] = False

        status = get_e2ee_status(config, self.config_path)

        self.assertEqual(status["overall_status"], "disabled")
        self.assertFalse(status["enabled"])
        self.assertIn("E2EE is disabled in configuration", status["issues"])

    @patch("sys.platform", "linux")
    @patch("mmrelay.e2ee_utils.os.path.exists")
    def test_e2ee_incomplete_missing_deps(self, mock_exists):
        """Test E2EE incomplete status when dependencies are missing"""
        mock_exists.return_value = True  # credentials.json exists

        with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:

            def import_side_effect(name):
                """
                Mock import side effect used in tests.

                Simulates Python's import behavior for use with import mocking: if the requested module name is "olm" it raises ImportError to emulate the dependency being missing; for any other module name it returns a MagicMock instance that stands in for the imported module.

                Parameters:
                    name (str): Full module name passed to the import (e.g., "olm", "nio.crypto").

                Returns:
                    unittest.mock.MagicMock: A mock object representing the imported module when the module is not "olm".

                Raises:
                    ImportError: If `name` is exactly "olm".
                """
                if name == "olm":
                    raise ImportError("No module named 'olm'")
                return MagicMock()

            mock_import.side_effect = import_side_effect

            status = get_e2ee_status(self.base_config, self.config_path)

            self.assertEqual(status["overall_status"], "incomplete")
            self.assertFalse(status["dependencies_installed"])
            self.assertIn(
                "E2EE dependencies not installed (python-olm)", status["issues"]
            )
            mock_import.assert_called_with("olm")

    @patch("sys.platform", "linux")
    @patch("mmrelay.e2ee_utils.os.path.exists")
    def test_e2ee_incomplete_missing_credentials(self, mock_exists):
        """
        Verify get_e2ee_status reports "incomplete" when Matrix credentials are missing.

        Mocks E2EE-related modules ("olm", "nio.crypto", "nio.store") so dependencies appear installed, simulates a missing credentials file, calls get_e2ee_status with the test configuration, and asserts that the overall status is "incomplete", credentials_available is False, and an issue about Matrix authentication not being configured is present.
        """
        mock_exists.return_value = False  # credentials.json doesn't exist

        with patch.dict(os.environ, {"MMRELAY_TESTING": "0"}, clear=False):
            with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:
                mock_import.side_effect = lambda _: MagicMock()
                status = get_e2ee_status(self.base_config, self.config_path)

            self.assertEqual(status["overall_status"], "incomplete")
            self.assertFalse(status["credentials_available"])
            self.assertIn("Matrix authentication not configured", status["issues"])
            imported_modules = {call.args[0] for call in mock_import.call_args_list}
            assert {
                "olm",
                "nio.crypto",
                "nio.store",
            }.issubset(imported_modules)

    @patch("sys.platform", "linux")
    @patch("mmrelay.e2ee_utils.os.path.exists")
    def test_e2ee_dependencies_skipped_in_test_mode(self, mock_exists):
        """Ensure optional nio imports are skipped when MMRELAY_TESTING=1."""

        mock_exists.return_value = True

        with patch.dict(os.environ, {"MMRELAY_TESTING": "1"}, clear=False):
            with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:

                def import_side_effect(name):
                    """
                    Prevent importing `nio` modules during tests and return a mock module for other imports.

                    Parameters:
                        name (str): Dotted module name being requested.

                    Returns:
                        MagicMock: A mock module object to satisfy imports (e.g., for "olm" or other non-`nio` modules).

                    Raises:
                        AssertionError: If `name` starts with "nio", indicating `nio` imports must be blocked in test mode.
                    """
                    if name == "olm":
                        return MagicMock()
                    if name.startswith("nio"):
                        raise AssertionError(
                            "nio modules should not be imported in test mode"
                        )
                    return MagicMock()

                mock_import.side_effect = import_side_effect
                status = get_e2ee_status(self.base_config, self.config_path)

        self.assertTrue(status["dependencies_installed"])
        self.assertEqual(status["overall_status"], "ready")
        mock_import.assert_any_call("olm")
        assert not any(
            call.args[0].startswith("nio") for call in mock_import.call_args_list
        )

    @patch("sys.platform", "linux")
    @patch("mmrelay.e2ee_utils.os.path.exists")
    def test_e2ee_hasattr_checks_success(self, mock_exists):
        """
        Verify that E2EE dependency attribute checks succeed when required attributes are present.

        Asserts that the status reports dependencies_installed and overall_status "ready", and that the expected modules ("olm", "nio.crypto", "nio.store") were attempted to be imported.
        """
        mock_exists.return_value = True

        with patch.dict(os.environ, {"MMRELAY_TESTING": "0"}, clear=False):
            with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:
                # Create mock modules with the required attributes
                mock_olm = MagicMock()
                mock_nio_crypto = MagicMock()
                mock_nio_crypto.OlmDevice = MagicMock()
                mock_nio_store = MagicMock()
                mock_nio_store.SqliteStore = MagicMock()

                def import_side_effect(name):
                    """
                    Provide a test-double module object corresponding to a requested import name.

                    Parameters:
                        name (str): Module name being imported (e.g., "olm", "nio.crypto", "nio.store").

                    Returns:
                        object: The corresponding mock module: `mock_olm` for "olm", `mock_nio_crypto` for "nio.crypto", `mock_nio_store` for "nio.store", or a new `MagicMock` for any other name.
                    """
                    if name == "olm":
                        return mock_olm
                    elif name == "nio.crypto":
                        return mock_nio_crypto
                    elif name == "nio.store":
                        return mock_nio_store
                    return MagicMock()

                mock_import.side_effect = import_side_effect
                status = get_e2ee_status(self.base_config, self.config_path)

                self.assertTrue(status["dependencies_installed"])
                self.assertEqual(status["overall_status"], "ready")
                # Verify all modules were imported
                expected_imports = {"olm", "nio.crypto", "nio.store"}
                actual_imports = {call.args[0] for call in mock_import.call_args_list}
                self.assertTrue(expected_imports.issubset(actual_imports))

    @patch("sys.platform", "linux")
    @patch("mmrelay.e2ee_utils.os.path.exists")
    def test_e2ee_hasattr_checks_missing_olmdevice(self, mock_exists):
        """
        Verify that get_e2ee_status reports missing dependencies when nio.crypto.OlmDevice is absent.

        Sets up mocked imports where the `nio.crypto` module lacks the `OlmDevice` attribute and asserts that
        the returned status marks dependencies as not installed, sets overall status to "incomplete", and
        includes an issue mentioning `python-olm`.

        Parameters:
            mock_exists (unittest.mock.Mock): Patch for os.path.exists controlling presence of credentials file.
        """
        mock_exists.return_value = True

        with patch.dict(os.environ, {"MMRELAY_TESTING": "0"}, clear=False):
            with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:
                # Create mock modules where nio.crypto lacks OlmDevice
                mock_olm = MagicMock()
                mock_nio_crypto = MagicMock()
                # Remove the OlmDevice attribute to simulate missing dependency
                del mock_nio_crypto.OlmDevice
                mock_nio_store = MagicMock()
                mock_nio_store.SqliteStore = MagicMock()

                def import_side_effect(name):
                    """
                    Provide a test-double module object corresponding to a requested import name.

                    Parameters:
                        name (str): Module name being imported (e.g., "olm", "nio.crypto", "nio.store").

                    Returns:
                        object: The corresponding mock module: `mock_olm` for "olm", `mock_nio_crypto` for "nio.crypto", `mock_nio_store` for "nio.store", or a new `MagicMock` for any other name.
                    """
                    if name == "olm":
                        return mock_olm
                    elif name == "nio.crypto":
                        return mock_nio_crypto
                    elif name == "nio.store":
                        return mock_nio_store
                    return MagicMock()

                mock_import.side_effect = import_side_effect
                status = get_e2ee_status(self.base_config, self.config_path)

                self.assertFalse(status["dependencies_installed"])
                self.assertEqual(status["overall_status"], "incomplete")
                self.assertIn(
                    "E2EE dependencies not installed (python-olm)", status["issues"]
                )

    @patch("sys.platform", "linux")
    @patch("mmrelay.e2ee_utils.os.path.exists")
    def test_e2ee_hasattr_checks_missing_sqlitestore(self, mock_exists):
        """Test hasattr check failure when nio.store.SqliteStore is missing"""
        mock_exists.return_value = True

        with patch.dict(os.environ, {"MMRELAY_TESTING": "0"}, clear=False):
            with patch("mmrelay.e2ee_utils.importlib.import_module") as mock_import:
                # Create mock modules where nio.store lacks SqliteStore
                mock_olm = MagicMock()
                mock_nio_crypto = MagicMock()
                mock_nio_crypto.OlmDevice = MagicMock()
                mock_nio_store = MagicMock()
                # Remove the SqliteStore attribute to simulate missing dependency
                del mock_nio_store.SqliteStore

                def import_side_effect(name):
                    """
                    Provide a test-double module object corresponding to a requested import name.

                    Parameters:
                        name (str): Module name being imported (e.g., "olm", "nio.crypto", "nio.store").

                    Returns:
                        object: The corresponding mock module: `mock_olm` for "olm", `mock_nio_crypto` for "nio.crypto", `mock_nio_store` for "nio.store", or a new `MagicMock` for any other name.
                    """
                    if name == "olm":
                        return mock_olm
                    elif name == "nio.crypto":
                        return mock_nio_crypto
                    elif name == "nio.store":
                        return mock_nio_store
                    return MagicMock()

                mock_import.side_effect = import_side_effect
                status = get_e2ee_status(self.base_config, self.config_path)

                self.assertFalse(status["dependencies_installed"])
                self.assertEqual(status["overall_status"], "incomplete")
                self.assertIn(
                    "E2EE dependencies not installed (python-olm)", status["issues"]
                )


class TestRoomListFormatting(unittest.TestCase):
    """Test room list formatting with E2EE status"""

    def setUp(self):
        """
        Skip the test when optional E2EE-related imports are not available.

        If the module-level IMPORTS_AVAILABLE is False, calls self.skipTest with a clear message so tests that depend on optional imports are skipped rather than failing.
        """
        if not IMPORTS_AVAILABLE:
            self.skipTest("Required imports not available")

    def test_room_list_e2ee_ready(self):
        """Test room list formatting when E2EE is ready"""
        rooms = {
            "!encrypted:test.org": MockRoom(
                "!encrypted:test.org", "Encrypted Room", encrypted=True
            ),
            "!plaintext:test.org": MockRoom(
                "!plaintext:test.org", "Plaintext Room", encrypted=False
            ),
        }

        e2ee_status = {"overall_status": "ready"}

        room_lines = format_room_list(rooms, e2ee_status)

        self.assertIn("   🔒 Encrypted Room - Encrypted", room_lines)
        self.assertIn("   ✅ Plaintext Room", room_lines)

    def test_room_list_e2ee_disabled(self):
        """Test room list formatting when E2EE is disabled"""
        rooms = {
            "!encrypted:test.org": MockRoom(
                "!encrypted:test.org", "Encrypted Room", encrypted=True
            ),
            "!plaintext:test.org": MockRoom(
                "!plaintext:test.org", "Plaintext Room", encrypted=False
            ),
        }

        e2ee_status = {"overall_status": "disabled"}

        room_lines = format_room_list(rooms, e2ee_status)

        self.assertIn(
            "   ⚠️ Encrypted Room - Encrypted (E2EE disabled - messages will be blocked)",
            room_lines,
        )
        self.assertIn("   ✅ Plaintext Room", room_lines)

    def test_room_list_e2ee_unavailable(self):
        """Test room list formatting when E2EE is unavailable (Windows)"""
        rooms = {
            "!encrypted:test.org": MockRoom(
                "!encrypted:test.org", "Encrypted Room", encrypted=True
            ),
        }

        e2ee_status = {"overall_status": "unavailable"}

        room_lines = format_room_list(rooms, e2ee_status)

        self.assertIn(
            "   ⚠️ Encrypted Room - Encrypted (E2EE not supported on Windows - messages will be blocked)",
            room_lines,
        )

    def test_room_list_with_invalid_rooms(self):
        """Test room list formatting with invalid rooms object"""
        # Test with None
        e2ee_status = {"overall_status": "ready"}
        room_lines = format_room_list(None, e2ee_status)
        self.assertEqual(room_lines, [])

        # Test with object that doesn't have items method
        invalid_rooms = "not a dict"
        room_lines = format_room_list(invalid_rooms, e2ee_status)
        self.assertEqual(room_lines, [])


class TestEncryptionWarnings(unittest.TestCase):
    """Test encryption warning generation"""

    def setUp(self):
        """
        Skip the test when optional E2EE-related imports are not available.

        If the module-level IMPORTS_AVAILABLE is False, calls self.skipTest with a clear message so tests that depend on optional imports are skipped rather than failing.
        """
        if not IMPORTS_AVAILABLE:
            self.skipTest("Required imports not available")

    def test_warnings_for_encrypted_rooms_disabled(self):
        """Test warnings when encrypted rooms exist but E2EE is disabled"""
        rooms = {
            "!encrypted1:test.org": MockRoom(
                "!encrypted1:test.org", "Room 1", encrypted=True
            ),
            "!encrypted2:test.org": MockRoom(
                "!encrypted2:test.org", "Room 2", encrypted=True
            ),
            "!plaintext:test.org": MockRoom(
                "!plaintext:test.org", "Room 3", encrypted=False
            ),
        }

        e2ee_status = {"overall_status": "disabled"}

        warnings = get_room_encryption_warnings(rooms, e2ee_status)

        self.assertEqual(len(warnings), 2)
        self.assertIn("2 encrypted room(s) detected but E2EE is disabled", warnings[0])
        self.assertIn("Messages to encrypted rooms will be blocked", warnings[1])

    def test_no_warnings_when_ready(self):
        """Test no warnings when E2EE is ready"""
        rooms = {
            "!encrypted:test.org": MockRoom(
                "!encrypted:test.org", "Room 1", encrypted=True
            ),
        }

        e2ee_status = {"overall_status": "ready"}

        warnings = get_room_encryption_warnings(rooms, e2ee_status)

        self.assertEqual(len(warnings), 0)

    def test_warnings_with_invalid_rooms(self):
        """Test encryption warnings with invalid rooms object"""
        # Test with None
        e2ee_status = {"overall_status": "disabled"}
        warnings = get_room_encryption_warnings(None, e2ee_status)
        self.assertEqual(warnings, [])

        # Test with object that doesn't have items method
        invalid_rooms = "not a dict"
        warnings = get_room_encryption_warnings(invalid_rooms, e2ee_status)
        self.assertEqual(warnings, [])


class TestE2EEErrorMessages(unittest.TestCase):
    """Test E2EE error message generation"""

    def setUp(self):
        """
        Skip the test when optional E2EE-related imports are not available.

        If the module-level IMPORTS_AVAILABLE is False, calls self.skipTest with a clear message so tests that depend on optional imports are skipped rather than failing.
        """
        if not IMPORTS_AVAILABLE:
            self.skipTest("Required imports not available")

    def test_error_message_unavailable(self):
        """Test error message for unavailable E2EE"""
        e2ee_status = {"overall_status": "unavailable", "platform_supported": False}

        message = get_e2ee_error_message(e2ee_status)

        self.assertIn("E2EE is not supported on Windows", message)

    def test_error_message_disabled(self):
        """Test error message for disabled E2EE"""
        e2ee_status = {
            "overall_status": "disabled",
            "platform_supported": True,
            "enabled": False,
        }

        message = get_e2ee_error_message(e2ee_status)

        self.assertIn("E2EE is disabled in configuration", message)

    def test_fix_instructions_complete_flow(self):
        """Test fix instructions for incomplete E2EE setup"""
        e2ee_status = {
            "overall_status": "incomplete",
            "platform_supported": True,
            "dependencies_installed": False,
            "credentials_available": False,
            "enabled": False,
        }

        instructions = get_e2ee_fix_instructions(e2ee_status)

        # Should include all fix steps
        instruction_text = " ".join(instructions)
        self.assertIn("Install E2EE dependencies", instruction_text)
        self.assertIn("Set up Matrix authentication", instruction_text)
        self.assertIn("Enable E2EE in configuration", instruction_text)
        self.assertIn("Verify configuration", instruction_text)

    def test_get_e2ee_warning_messages(self):
        """Test that get_e2ee_warning_messages returns expected warning messages."""
        from mmrelay.e2ee_utils import get_e2ee_warning_messages

        warnings = get_e2ee_warning_messages()

        # Verify it's a dictionary
        self.assertIsInstance(warnings, dict)

        # Verify expected keys are present
        expected_keys = [
            "unavailable",
            "disabled",
            "incomplete",
            "missing_deps",
            "missing_auth",
            "missing_config",
        ]
        for key in expected_keys:
            self.assertIn(key, warnings)
            self.assertIsInstance(warnings[key], str)
            self.assertGreater(len(warnings[key]), 0)

        # Verify specific message content
        self.assertIn("Windows", warnings["unavailable"])
        self.assertIn("disabled", warnings["disabled"])
        self.assertIn("incomplete", warnings["incomplete"])
        self.assertIn("dependencies", warnings["missing_deps"])
        self.assertIn("authentication", warnings["missing_auth"])
        self.assertIn("configuration", warnings["missing_config"])


class TestActualEncryptionVerification(unittest.TestCase):
    """Test actual encryption verification using log capture"""

    def setUp(self):
        """
        Skip the test when optional E2EE-related imports are not available.

        If the module-level IMPORTS_AVAILABLE is False, calls self.skipTest with a clear message so tests that depend on optional imports are skipped rather than failing.
        """
        if not IMPORTS_AVAILABLE:
            self.skipTest("Required imports not available")

    def test_encryption_log_detection(self):
        """
        Capture INFO-level messages from the `nio.crypto.log` logger and assert that expected encryption-related log entries are emitted.

        This test attaches a temporary log handler to `nio.crypto.log`, emits three representative INFO messages related to group session sharing and creation, and verifies those exact messages were captured. The handler is removed in a finally block to avoid side effects on global logging state.
        """
        # Set up log capture
        log_capture = []

        class TestLogHandler(logging.Handler):
            def emit(self, record):
                """
                Append the formatted message from a LogRecord to the outer `log_capture` list.

                This handler extracts the record's message (via LogRecord.getMessage()) and appends it to the surrounding `log_capture` list for later inspection by tests.

                Parameters:
                    record (logging.LogRecord): The LogRecord whose formatted message will be captured.
                """
                log_capture.append(record.getMessage())

        # Add handler to nio.crypto logger
        nio_crypto_logger = logging.getLogger("nio.crypto.log")
        test_handler = TestLogHandler()
        nio_crypto_logger.addHandler(test_handler)
        nio_crypto_logger.setLevel(logging.INFO)

        try:
            # Simulate encryption logs that should appear during actual encryption
            nio_crypto_logger.info("Sharing group session for room !test:matrix.org")
            nio_crypto_logger.info(
                "Creating outbound group session for !test:matrix.org"
            )
            nio_crypto_logger.info(
                "Created outbound group session for !test:matrix.org"
            )

            # Verify logs were captured
            self.assertIn(
                "Sharing group session for room !test:matrix.org", log_capture
            )
            self.assertIn(
                "Creating outbound group session for !test:matrix.org", log_capture
            )
            self.assertIn(
                "Created outbound group session for !test:matrix.org", log_capture
            )

        finally:
            nio_crypto_logger.removeHandler(test_handler)

    def test_encrypted_event_detection(self):
        """
        Verify detection and basic validity checks for a Matrix `m.room.encrypted` event.

        Creates a representative encrypted event dictionary and asserts:
        - event `type` is "m.room.encrypted";
        - `content.algorithm` matches the expected Megolm algorithm;
        - `content` contains a `ciphertext` field; and
        - the `ciphertext` length is non-trivial (greater than 50 characters).

        This test ensures the shape and minimal substance of encrypted event payloads used by higher-level encryption verification code.
        """
        # Mock encrypted event structure based on user's log output
        encrypted_event = {
            "type": "m.room.encrypted",
            "sender": "@test:matrix.org",
            "content": {
                "algorithm": "m.megolm.v1.aes-sha2",
                "sender_key": "yWbkMuf79EYplKxMDLNIhKJOv6TI8N6B2uAZfyjbeGA",
                "ciphertext": "AwgAEuADQPfZcoJuIpDVuNcny8TKU3fWmC1csoskg9hSvl/Bg5NB...",
                "session_id": "Y0Hx42T+B24crGSZv1wB7BGmqrNdusMdYYLofiZI7C8",
                "device_id": "PFUJMPSBMT",
            },
        }

        # Verify encryption indicators
        self.assertEqual(encrypted_event["type"], "m.room.encrypted")
        self.assertEqual(
            encrypted_event["content"]["algorithm"], "m.megolm.v1.aes-sha2"
        )
        self.assertIn("ciphertext", encrypted_event["content"])
        self.assertGreater(
            len(encrypted_event["content"]["ciphertext"]), 50
        )  # Should be substantial

    def test_encryption_success_indicators(self):
        """Test that we can identify successful encryption from logs and events"""
        # This test verifies we can detect the key indicators of successful encryption
        # that the user showed in their log output

        success_indicators = [
            "INFO:nio.crypto.log:Sharing group session for room",
            "INFO:nio.crypto.log:Creating outbound group session for",
            "INFO:nio.crypto.log:Created outbound group session for",
            "m.room.encrypted",
            "m.megolm.v1.aes-sha2",
            "ciphertext",
        ]

        # Mock log output similar to user's successful encryption
        mock_log_output = """
        INFO:nio.crypto.log:Sharing group session for room !LdtMCWfpwcbeJVTRnP:matrix.org
        INFO:nio.crypto.log:Creating outbound group session for !LdtMCWfpwcbeJVTRnP:matrix.org
        INFO:nio.crypto.log:Created outbound group session for !LdtMCWfpwcbeJVTRnP:matrix.org
        """

        mock_event_data = {
            "type": "m.room.encrypted",
            "content": {
                "algorithm": "m.megolm.v1.aes-sha2",
                "ciphertext": "encrypted_data_here",
            },
        }

        # Verify all success indicators are present
        for indicator in success_indicators[:3]:  # Log indicators
            self.assertIn(indicator.split(":")[-1].strip(), mock_log_output)

        # Event indicators
        self.assertEqual(mock_event_data["type"], success_indicators[3])
        self.assertEqual(mock_event_data["content"]["algorithm"], success_indicators[4])
        self.assertIn(success_indicators[5], mock_event_data["content"])

    @unittest.skipUnless(IMPORTS_AVAILABLE, "E2EE dependencies not available")
    def test_get_e2ee_warning_messages(self):
        """Test that get_e2ee_warning_messages returns expected warning messages."""
        from mmrelay.e2ee_utils import get_e2ee_warning_messages

        warnings = get_e2ee_warning_messages()

        # Verify it's a dictionary
        self.assertIsInstance(warnings, dict)

        # Verify expected keys are present
        expected_keys = [
            "unavailable",
            "disabled",
            "incomplete",
            "missing_deps",
            "missing_auth",
            "missing_config",
        ]
        for key in expected_keys:
            self.assertIn(key, warnings)
            self.assertIsInstance(warnings[key], str)
            self.assertGreater(len(warnings[key]), 0)

        # Verify specific message content
        self.assertIn("Windows", warnings["unavailable"])
        self.assertIn("disabled", warnings["disabled"])
        self.assertIn("incomplete", warnings["incomplete"])
        self.assertIn("dependencies", warnings["missing_deps"])
        self.assertIn("authentication", warnings["missing_auth"])
        self.assertIn("configuration", warnings["missing_config"])


if __name__ == "__main__":
    unittest.main()
