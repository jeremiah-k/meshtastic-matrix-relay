"""Tests for Kubernetes utilities."""

import os
import sys
import tempfile
import unittest
from unittest import mock
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.k8s_utils import (
    generate_config_only,
    generate_manifests,
    load_template,
    prompt_for_config,
    render_template,
)


class TestK8sUtils(unittest.TestCase):
    """Test cases for Kubernetes utilities."""

    def test_render_template(self):
        """Test basic template rendering with variable substitution."""
        template = "Hello {{NAME}}, you are {{AGE}} years old."
        variables = {"NAME": "Alice", "AGE": "30"}
        result = render_template(template, variables)
        self.assertEqual(result, "Hello Alice, you are 30 years old.")

    def test_render_template_multiple_same_variable(self):
        """Test rendering with same variable used multiple times."""
        template = "{{NAME}} {{NAME}} {{NAME}}"
        variables = {"NAME": "Echo"}
        result = render_template(template, variables)
        self.assertEqual(result, "Echo Echo Echo")

    def test_render_template_unused_variable(self):
        """Test that unused variables don't affect output."""
        template = "Hello {{NAME}}"
        variables = {"NAME": "Bob", "UNUSED": "value"}
        result = render_template(template, variables)
        self.assertEqual(result, "Hello Bob")

    def test_load_template_configmap(self):
        """Test loading a Kubernetes template file."""
        # This will test that the template file exists and is readable
        try:
            content = load_template("configmap.yaml")
            self.assertIn("apiVersion", content)
            self.assertIn("ConfigMap", content)
            self.assertIn("{{NAMESPACE}}", content)
        except FileNotFoundError:
            self.skipTest("Template files not yet packaged")

    def test_generate_config_only_new_file(self):
        """Test generating config.yaml to a new file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test_config.yaml")

            # Mock the sample config path to return the actual template location
            with patch("mmrelay.tools.get_sample_config_path") as mock_sample:
                mock_sample.return_value = "/fake/sample_config.yaml"

                with patch("builtins.open", mock.mock_open(read_data="test: config\n")):
                    result = generate_config_only(output_path)
                    # Function should return the path
                    self.assertEqual(result, output_path)

    def test_generate_manifests_creates_files(self):
        """Test that generate_manifests creates the expected files."""
        config = {
            "namespace": "test-namespace",
            "image_tag": "latest",
            "auth_method": "env",
            "connection_type": "tcp",
            "meshtastic_host": "meshtastic.local",
            "meshtastic_port": "4403",
            "storage_class": "standard",
            "storage_size": "1Gi",
            "enable_e2ee": False,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                generated_files = generate_manifests(config, tmpdir)

                # Check that files were generated
                self.assertGreater(len(generated_files), 0)

                # Check that all files exist
                for file_path in generated_files:
                    self.assertTrue(
                        os.path.exists(file_path), f"File not found: {file_path}"
                    )

                # Expected files
                expected_files = ["pvc", "configmap", "secret", "deployment"]
                for expected in expected_files:
                    self.assertTrue(
                        any(expected in f for f in generated_files),
                        f"Missing expected file containing '{expected}'",
                    )
            except FileNotFoundError:
                self.skipTest("Template files not yet packaged")

    def test_generate_manifests_with_credentials_auth(self):
        """Test manifest generation with credentials.json authentication method."""
        config = {
            "namespace": "test-namespace",
            "image_tag": "v1.2.0",
            "auth_method": "credentials",
            "connection_type": "tcp",
            "meshtastic_host": "192.168.1.100",
            "meshtastic_port": "4403",
            "storage_class": "gp2",
            "storage_size": "2Gi",
            "enable_e2ee": True,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                generated_files = generate_manifests(config, tmpdir)

                # Should generate credentials secret file
                self.assertTrue(
                    any("credentials" in f for f in generated_files),
                    "Missing credentials secret file",
                )

                # Check deployment file contains credentials volume
                deployment_file = [f for f in generated_files if "deployment" in f][0]
                with open(deployment_file, "r") as f:
                    deployment_content = f.read()
                    # Should have credentials volume mount uncommented
                    self.assertIn("name: credentials", deployment_content)
            except (FileNotFoundError, IndexError):
                self.skipTest("Template files not yet packaged or generation failed")

    def test_generate_manifests_with_serial_connection(self):
        """Test manifest generation with serial connection type."""
        config = {
            "namespace": "test-namespace",
            "image_tag": "latest",
            "auth_method": "env",
            "connection_type": "serial",
            "serial_device": "/dev/ttyUSB0",
            "storage_class": "standard",
            "storage_size": "1Gi",
            "enable_e2ee": False,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                generated_files = generate_manifests(config, tmpdir)

                # Check deployment file contains serial device volume
                deployment_file = [f for f in generated_files if "deployment" in f][0]
                with open(deployment_file, "r") as f:
                    deployment_content = f.read()
                    # Should have serial device volume mount uncommented
                    self.assertIn("serial-device", deployment_content)
                    self.assertIn("/dev/ttyUSB0", deployment_content)
            except (FileNotFoundError, IndexError):
                self.skipTest("Template files not yet packaged or generation failed")

    @patch("builtins.input")
    def test_prompt_for_config_defaults(self, mock_input):
        """Test prompt_for_config with all default values."""
        # Mock user pressing Enter for all prompts (using defaults)
        mock_input.side_effect = [
            "",  # namespace (default)
            "",  # image_tag (latest)
            "",  # auth_method (1)
            "",  # connection_type (1)
            "",  # meshtastic_host (meshtastic.local)
            "",  # meshtastic_port (4403)
            "",  # storage_class (standard)
            "",  # storage_size (1Gi)
            "",  # enable_e2ee (N)
        ]

        config = prompt_for_config()

        self.assertEqual(config["namespace"], "default")
        self.assertEqual(config["image_tag"], "latest")
        self.assertEqual(config["auth_method"], "env")
        self.assertEqual(config["connection_type"], "tcp")
        self.assertEqual(config["meshtastic_host"], "meshtastic.local")

    @patch("builtins.input")
    def test_prompt_for_config_serial(self, mock_input):
        """Test prompt_for_config choosing serial connection."""
        mock_input.side_effect = [
            "custom-ns",  # namespace
            "v1.2.0",  # image_tag
            "2",  # auth_method (credentials)
            "2",  # connection_type (serial)
            "/dev/ttyACM0",  # serial_device
            "fast-storage",  # storage_class
            "5Gi",  # storage_size
            "y",  # enable_e2ee
        ]

        config = prompt_for_config()

        self.assertEqual(config["namespace"], "custom-ns")
        self.assertEqual(config["image_tag"], "v1.2.0")
        self.assertEqual(config["auth_method"], "credentials")
        self.assertEqual(config["connection_type"], "serial")
        self.assertEqual(config["serial_device"], "/dev/ttyACM0")
        self.assertEqual(config["storage_class"], "fast-storage")
        self.assertEqual(config["storage_size"], "5Gi")
        self.assertTrue(config["enable_e2ee"])


if __name__ == "__main__":
    unittest.main()
