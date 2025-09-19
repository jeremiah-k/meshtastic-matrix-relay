"""Tests for E2EE graceful degradation in matrix_utils.py."""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from mmrelay.matrix_utils import connect_matrix


class TestE2EEGracefulDegradation(unittest.TestCase):
    """Test E2EE graceful degradation when dependencies are missing."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = {
            "matrix": {
                "homeserver": "https://matrix.example.com",
                "access_token": "test_token",
                "user_id": "@test:example.com",
                "device_id": "TEST_DEVICE",
                "e2ee": {"enabled": True},
            }
        }

    @patch("mmrelay.matrix_utils.logger")
    @patch("mmrelay.matrix_utils.AsyncClient")
    def test_e2ee_graceful_degradation_missing_olm(self, mock_client_class, mock_logger):
        """Test graceful degradation when Olm is missing."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Mock the import error for Olm
        with patch("builtins.__import__", side_effect=ImportError("No module named 'olm'")):
            result = asyncio.run(connect_matrix(self.config))

        # Should return the client despite E2EE failure
        self.assertEqual(result, mock_client)

        # Should log the error and warning
        mock_logger.error.assert_called()
        mock_logger.warning.assert_called_with("E2EE will be disabled for this session.")

    @patch("mmrelay.matrix_utils.logger")
    @patch("mmrelay.matrix_utils.AsyncClient")
    def test_e2ee_graceful_degradation_missing_sqlitestore(self, mock_client_class, mock_logger):
        """Test graceful degradation when SqliteStore is missing."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Mock the import error for SqliteStore
        def mock_import(name, *args, **kwargs):
            if name == "nio.store.SqliteStore":
                raise ImportError("No module named 'nio.store'")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = asyncio.run(connect_matrix(self.config))

        # Should return the client despite E2EE failure
        self.assertEqual(result, mock_client)
        
        # Should log the error and warning
        mock_logger.error.assert_called()
        mock_logger.warning.assert_called_with("E2EE will be disabled for this session.")

    @patch("mmrelay.matrix_utils.logger")
    @patch("mmrelay.matrix_utils.AsyncClient")
    @patch("mmrelay.matrix_utils.os.makedirs")
    def test_e2ee_successful_setup(self, mock_makedirs, mock_client_class, mock_logger):
        """Test successful E2EE setup when dependencies are available."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        with tempfile.TemporaryDirectory() as temp_dir:
            self.config["matrix"]["e2ee"]["store_path"] = temp_dir
            
            # Mock successful imports
            with patch("nio.store.SqliteStore") as mock_store:
                mock_store.return_value = MagicMock()
                result = asyncio.run(connect_matrix(self.config))

        # Should return the client
        self.assertEqual(result, mock_client)
        
        # Should log successful E2EE setup
        mock_logger.info.assert_called_with("End-to-End Encryption (E2EE) is enabled")

    @patch("mmrelay.matrix_utils.logger")
    @patch("mmrelay.matrix_utils.AsyncClient")
    def test_e2ee_disabled_in_config(self, mock_client_class, mock_logger):
        """Test when E2EE is disabled in config."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Disable E2EE in config
        self.config["matrix"]["e2ee"]["enabled"] = False

        result = asyncio.run(connect_matrix(self.config))

        # Should return the client
        self.assertEqual(result, mock_client)
        
        # Should not attempt E2EE setup
        mock_logger.info.assert_not_called()
        mock_logger.error.assert_not_called()
        mock_logger.warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
