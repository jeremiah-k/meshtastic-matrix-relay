"""
Test for _find_credentials_json_path() to ensure legacy search works.

This is a minimal deterministic test to verify the fix for the indentation bug
that made the legacy credentials loop unreachable.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.cli import _find_credentials_json_path


class TestFindCredentialsPath(unittest.TestCase):
    def test_legacy_credentials_search(self):
        """
        Test that credentials.json in a legacy root are found when HOME creds don't exist.

        This test verifies the fix for the indentation bug that made the legacy
        loop unreachable. The search order should be:
        1) Config-adjacent (only if config_path provided)
        2) HOME credentials
        3) Legacy roots

        When HOME credentials are missing, it should fall through to search legacy.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            home = os.path.join(tmpdir, "home")
            legacy = os.path.join(tmpdir, "legacy")
            os.makedirs(home)
            os.makedirs(legacy)

            legacy_creds = os.path.join(legacy, "credentials.json")
            with open(legacy_creds, "w") as f:
                f.write('{"homeserver": "https://matrix.org", "access_token": "test"}')

            with patch("mmrelay.paths.resolve_all_paths") as mock_resolve:
                mock_resolve.return_value = {
                    "credentials_path": os.path.join(home, "credentials.json"),
                    "legacy_sources": [legacy],
                }

                result = _find_credentials_json_path(None)

                self.assertEqual(result, legacy_creds)


if __name__ == "__main__":
    unittest.main()
