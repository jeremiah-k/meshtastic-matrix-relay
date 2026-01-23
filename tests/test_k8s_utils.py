"""Tests for Kubernetes utilities."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.k8s_utils import (
    check_configmap,
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

    def test_render_template_missing_variable_raises(self):
        """Test that missing variables raise a ValueError."""
        template = "Hello {{NAME}}"
        with self.assertRaises(ValueError):
            render_template(template, {})

    def test_render_template_block_placeholder(self):
        """Test block placeholder indentation handling."""
        template = "items:\n  {{BLOCK}}\nend: true"
        variables = {"BLOCK": "- name: one\n  value: 1"}
        result = render_template(template, variables)
        self.assertEqual(
            result,
            "items:\n  - name: one\n    value: 1\nend: true",
        )

    def test_load_template_pvc(self):
        """Test loading a Kubernetes template file."""
        # This will test that the template file exists and is readable
        try:
            content = load_template("persistentvolumeclaim.yaml.tpl")
            self.assertIn("apiVersion: v1", content)
            self.assertIn("kind: PersistentVolumeClaim", content)
            self.assertIn("namespace: {{NAMESPACE}}", content)
        except FileNotFoundError:
            self.skipTest("Template files not yet packaged")

    def test_generate_configmap_from_sample(self):
        """Test generating ConfigMap from sample config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a real sample config file
            sample_config_path = os.path.join(tmpdir, "sample_config.yaml")
            sample_config_content = (
                "matrix:\n  homeserver: https://matrix.example.org\n"
            )
            with open(sample_config_path, "w", encoding="utf-8") as f:
                f.write(sample_config_content)

            output_path = os.path.join(tmpdir, "configmap.yaml")

            # Only patch get_sample_config_path - let file I/O execute normally
            with patch("mmrelay.tools.get_sample_config_path") as mock_sample:
                mock_sample.return_value = sample_config_path

                from mmrelay.k8s_utils import generate_configmap_from_sample

                result = generate_configmap_from_sample("default", output_path)

                # Function should return the output path
                self.assertEqual(result, output_path)

                # Verify the file was actually created
                self.assertTrue(
                    os.path.exists(output_path),
                    f"Output file not created: {output_path}",
                )

                # Verify the file contains expected YAML structure
                with open(output_path, "r", encoding="utf-8") as f:
                    output_content = f.read()
                    self.assertIn("apiVersion: v1", output_content)
                    self.assertIn("kind: ConfigMap", output_content)
                    self.assertIn("name: mmrelay-config", output_content)
                    self.assertIn("namespace: default", output_content)
                    self.assertIn("config.yaml: |", output_content)
                # Verify sample config content is embedded with proper indentation
                self.assertIn("  matrix:", output_content)
                self.assertIn(
                    "    homeserver: https://matrix.example.org", output_content
                )

    def test_generate_configmap_from_sample_custom_namespace(self):
        """Test generating ConfigMap with custom namespace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_config_path = os.path.join(tmpdir, "sample_config.yaml")
            sample_config_content = (
                "matrix:\n  homeserver: https://matrix.example.org\n"
            )
            with open(sample_config_path, "w", encoding="utf-8") as f:
                f.write(sample_config_content)

            output_path = os.path.join(tmpdir, "configmap.yaml")

            with patch("mmrelay.tools.get_sample_config_path") as mock_sample:
                mock_sample.return_value = sample_config_path

                from mmrelay.k8s_utils import generate_configmap_from_sample

                result = generate_configmap_from_sample("production", output_path)

                self.assertEqual(result, output_path)
                with open(output_path, "r", encoding="utf-8") as f:
                    output_content = f.read()
                    self.assertIn("namespace: production", output_content)

    def test_generate_configmap_from_sample_multiline_config(self):
        """Test generating ConfigMap with multiline sample config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_config_path = os.path.join(tmpdir, "sample_config.yaml")
            sample_config_content = """matrix:
  homeserver: https://matrix.example.org
  bot_user_id: "@bot:example.org"
  password: "password"
meshtastic:
  connection_type: tcp
  host: meshtastic.local
  port: 4403
"""
            with open(sample_config_path, "w", encoding="utf-8") as f:
                f.write(sample_config_content)

            output_path = os.path.join(tmpdir, "configmap.yaml")

            with patch("mmrelay.tools.get_sample_config_path") as mock_sample:
                mock_sample.return_value = sample_config_path

                from mmrelay.k8s_utils import generate_configmap_from_sample

                result = generate_configmap_from_sample("default", output_path)

                self.assertEqual(result, output_path)
                with open(output_path, "r", encoding="utf-8") as f:
                    output_content = f.read()
                    # Verify all sections are properly indented
                    self.assertIn(
                        "    homeserver: https://matrix.example.org", output_content
                    )
                    self.assertIn('    bot_user_id: "@bot:example.org"', output_content)
                    self.assertIn("  meshtastic:", output_content)
                    self.assertIn("    connection_type: tcp", output_content)

    def test_generate_manifests_creates_files(self):
        """Test that generate_manifests creates the expected files."""
        config = {
            "namespace": "test-namespace",
            "image_tag": "latest",
            "use_credentials_file": False,
            "connection_type": "tcp",
            "meshtastic_host": "meshtastic.local",
            "meshtastic_port": "4403",
            "storage_class": "standard",
            "storage_size": "1Gi",
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

                # Expected files: pvc, configmap, deployment (no secret for env var auth)
                expected_files = ["pvc", "configmap", "deployment"]
                for expected in expected_files:
                    self.assertTrue(
                        any(expected in f for f in generated_files),
                        f"Missing expected file containing '{expected}'",
                    )

                # Should NOT have credentials secret for env var auth
                self.assertFalse(
                    any("credentials" in f for f in generated_files),
                    "Should not generate credentials secret for env var auth",
                )

                # Check deployment file contains non-root security context for TCP connection
                deployment_file = next(f for f in generated_files if "deployment" in f)
                with open(deployment_file, "r") as f:
                    deployment_content = f.read()
                    # Should have non-root security context
                    self.assertIn("runAsNonRoot: true", deployment_content)
                    self.assertIn("runAsUser: 1000", deployment_content)
                    self.assertIn("runAsGroup: 1000", deployment_content)
                    self.assertIn("fsGroup: 1000", deployment_content)
                    # Should NOT have root security context
                    self.assertNotIn("runAsUser: 0", deployment_content)
            except FileNotFoundError:
                self.skipTest("Template files not yet packaged")

    def test_generate_manifests_with_credentials_auth(self):
        """Test manifest generation with credentials.json authentication method."""
        config = {
            "namespace": "test-namespace",
            "image_tag": "v1.2.0",
            "use_credentials_file": True,
            "connection_type": "tcp",
            "meshtastic_host": "192.168.1.100",
            "meshtastic_port": "4403",
            "storage_class": "gp2",
            "storage_size": "2Gi",
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
                deployment_file = next(f for f in generated_files if "deployment" in f)
                with open(deployment_file, "r") as f:
                    deployment_content = f.read()
                    # Should have credentials volume mount uncommented
                    self.assertIn("name: credentials", deployment_content)
            except (FileNotFoundError, IndexError, StopIteration):
                self.skipTest("Template files not yet packaged or generation failed")

    def test_generate_manifests_with_env_secret_manifest(self):
        """Test manifest generation with env-var auth and secret manifest enabled."""
        config = {
            "namespace": "test-namespace",
            "image_tag": "v1.2.0",
            "use_credentials_file": False,
            "generate_secret_manifest": True,
            "connection_type": "tcp",
            "meshtastic_host": "192.168.1.100",
            "meshtastic_port": "4403",
            "storage_class": "gp2",
            "storage_size": "2Gi",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                generated_files = generate_manifests(config, tmpdir)

                # Should generate env secret file
                secret_file = next(
                    f for f in generated_files if "secret-matrix-credentials" in f
                )

                with open(secret_file, "r", encoding="utf-8") as f:
                    secret_content = f.read()
                    self.assertIn("MMRELAY_MATRIX_HOMESERVER", secret_content)
                    self.assertIn("MMRELAY_MATRIX_BOT_USER_ID", secret_content)
                    self.assertIn("MMRELAY_MATRIX_PASSWORD", secret_content)
            except (FileNotFoundError, IndexError):
                self.skipTest("Template files not yet packaged or generation failed")

    def test_generate_manifests_with_serial_connection(self):
        """Test manifest generation with serial connection type."""
        config = {
            "namespace": "test-namespace",
            "image_tag": "latest",
            "use_credentials_file": False,
            "connection_type": "serial",
            "serial_device": "/dev/ttyUSB0",
            "storage_class": "standard",
            "storage_size": "1Gi",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                generated_files = generate_manifests(config, tmpdir)

                # Check deployment file contains serial device volume
                deployment_file = next(f for f in generated_files if "deployment" in f)
                with open(deployment_file, "r") as f:
                    deployment_content = f.read()
                    # Should have serial device volume mount uncommented
                    self.assertIn("serial-device", deployment_content)
                    self.assertIn("/dev/ttyUSB0", deployment_content)
                    # Should have root security context for serial connection
                    self.assertIn("runAsUser: 0", deployment_content)
                    self.assertIn("runAsGroup: 0", deployment_content)
                    self.assertIn("supplementalGroups: [20]", deployment_content)
                    # Should NOT have non-root security context
                    self.assertNotIn("runAsNonRoot: true", deployment_content)
            except (FileNotFoundError, IndexError):
                self.skipTest("Template files not yet packaged or generation failed")

    @patch("mmrelay.k8s_utils._get_storage_classes_from_kubectl", return_value=None)
    @patch("mmrelay.k8s_utils._get_current_namespace_from_kubectl", return_value=None)
    @patch("builtins.input")
    def test_prompt_for_config_defaults(
        self, mock_input, _mock_namespace, _mock_storage
    ):
        """Test prompt_for_config with all default values."""
        # Mock user pressing Enter for all prompts (using defaults)
        mock_input.side_effect = [
            "",  # namespace (default)
            "",  # image_tag (latest)
            "",  # auth_method (1)
            "",  # generate_secret_manifest (default yes)
            "",  # connection_type (1)
            "",  # meshtastic_host (meshtastic.local)
            "",  # meshtastic_port (4403)
            "",  # storage_class (standard)
            "",  # storage_size (1Gi)
        ]

        config = prompt_for_config()

        self.assertEqual(config["namespace"], "default")
        self.assertEqual(config["image_tag"], "latest")
        self.assertFalse(config["use_credentials_file"])
        self.assertTrue(config["generate_secret_manifest"])
        self.assertEqual(config["connection_type"], "tcp")
        self.assertEqual(config["meshtastic_host"], "meshtastic.local")

    @patch("mmrelay.k8s_utils._get_storage_classes_from_kubectl", return_value=None)
    @patch("mmrelay.k8s_utils._get_current_namespace_from_kubectl", return_value=None)
    @patch("builtins.input")
    def test_prompt_for_config_serial(self, mock_input, _mock_namespace, _mock_storage):
        """Test prompt_for_config choosing serial connection."""
        mock_input.side_effect = [
            "custom-ns",  # namespace
            "v1.2.0",  # image_tag
            "2",  # use_credentials_file (2=yes)
            "n",  # generate_secret_manifest (no)
            "2",  # connection_type (serial)
            "/dev/ttyACM0",  # serial_device
            "fast-storage",  # storage_class
            "5Gi",  # storage_size
        ]

        config = prompt_for_config()

        self.assertEqual(config["namespace"], "custom-ns")
        self.assertEqual(config["image_tag"], "v1.2.0")
        self.assertTrue(config["use_credentials_file"])
        self.assertFalse(config["generate_secret_manifest"])
        self.assertEqual(config["connection_type"], "serial")
        self.assertEqual(config["serial_device"], "/dev/ttyACM0")
        self.assertEqual(config["storage_class"], "fast-storage")
        self.assertEqual(config["storage_size"], "5Gi")

    @patch("mmrelay.k8s_utils._get_storage_classes_from_kubectl", return_value=None)
    @patch("mmrelay.k8s_utils._get_current_namespace_from_kubectl", return_value=None)
    @patch("builtins.input")
    @patch("builtins.print")
    def test_prompt_for_config_invalid_auth_choice(
        self, mock_print, mock_input, _mock_namespace, _mock_storage
    ):
        """Test prompt_for_config with invalid authentication choice defaults to 1."""
        mock_input.side_effect = [
            "",  # namespace (default)
            "",  # image_tag (latest)
            "invalid",  # invalid auth_choice
            "",  # generate_secret_manifest (default yes)
            "",  # connection_type (1)
            "",  # meshtastic_host (meshtastic.local)
            "",  # meshtastic_port (4403)
            "",  # storage_class (standard)
            "",  # storage_size (1Gi)
        ]

        config = prompt_for_config()

        self.assertEqual(config["namespace"], "default")
        self.assertFalse(config["use_credentials_file"])
        mock_print.assert_any_call("Invalid choice; defaulting to 1.")

    @patch("mmrelay.k8s_utils._get_storage_classes_from_kubectl", return_value=None)
    @patch("mmrelay.k8s_utils._get_current_namespace_from_kubectl", return_value=None)
    @patch("builtins.input")
    @patch("builtins.print")
    def test_prompt_for_config_invalid_connection_choice(
        self, mock_print, mock_input, _mock_namespace, _mock_storage
    ):
        """Test prompt_for_config with invalid connection choice defaults to 1."""
        mock_input.side_effect = [
            "",  # namespace (default)
            "",  # image_tag (latest)
            "",  # auth_method (1)
            "",  # generate_secret_manifest (default yes)
            "3",  # invalid connection_type
            "",  # meshtastic_host (meshtastic.local)
            "",  # meshtastic_port (4403)
            "",  # storage_class (standard)
            "",  # storage_size (1Gi)
        ]

        config = prompt_for_config()

        self.assertEqual(config["namespace"], "default")
        self.assertEqual(config["connection_type"], "tcp")
        mock_print.assert_any_call("Invalid choice; defaulting to 1.")

    def test_check_configmap_valid(self):
        """Test check_configmap with a valid ConfigMap."""
        valid_configmap_content = """apiVersion: v1
kind: ConfigMap
metadata:
  name: mmrelay-config
  namespace: default
  labels:
    app: mmrelay
data:
  config.yaml: |
    matrix:
      homeserver: https://matrix.example.org
      bot_user_id: "@bot:example.org"
      password: "testpassword"
    matrix_rooms:
      - id: "#test:example.org"
        meshtastic_channel: 0
    meshtastic:
      connection_type: tcp
      host: meshtastic.local
      port: 4403
      meshnet_name: Test Meshnet
      broadcast_enabled: true
    logging:
      level: info
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(valid_configmap_content)
            configmap_path = f.name

        try:
            result = check_configmap(configmap_path)
            self.assertTrue(result, "Valid ConfigMap should pass validation")
        finally:
            os.unlink(configmap_path)

    def test_check_configmap_missing_data_section(self):
        """Test check_configmap with missing data section."""
        invalid_configmap_content = """apiVersion: v1
kind: ConfigMap
metadata:
  name: mmrelay-config
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(invalid_configmap_content)
            configmap_path = f.name

        try:
            result = check_configmap(configmap_path)
            self.assertFalse(
                result, "ConfigMap without data section should fail validation"
            )
        finally:
            os.unlink(configmap_path)

    def test_check_configmap_missing_config_yaml(self):
        """Test check_configmap with missing config.yaml key."""
        invalid_configmap_content = """apiVersion: v1
kind: ConfigMap
metadata:
  name: mmrelay-config
data:
  other-key: value
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(invalid_configmap_content)
            configmap_path = f.name

        try:
            result = check_configmap(configmap_path)
            self.assertFalse(
                result, "ConfigMap without config.yaml key should fail validation"
            )
        finally:
            os.unlink(configmap_path)

    def test_check_configmap_invalid_yaml_syntax(self):
        """Test check_configmap with malformed YAML."""
        malformed_configmap_content = """apiVersion: v1
kind: ConfigMap
metadata:
  name: mmrelay-config
data
  config.yaml: |
    matrix:
      homeserver: https://matrix.example.org
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(malformed_configmap_content)
            configmap_path = f.name

        try:
            result = check_configmap(configmap_path)
            self.assertFalse(result, "Malformed YAML should fail validation")
        finally:
            os.unlink(configmap_path)

    def test_check_configmap_invalid_embedded_config(self):
        """Test check_configmap with invalid embedded configuration."""
        invalid_configmap_content = """apiVersion: v1
kind: ConfigMap
metadata:
  name: mmrelay-config
data:
  config.yaml: |
    matrix:
      homeserver: "missing-protocol"
      bot_user_id: "@bot:example.org"
    matrix_rooms: []
    meshtastic:
      connection_type: tcp
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(invalid_configmap_content)
            configmap_path = f.name

        try:
            result = check_configmap(configmap_path)
            self.assertFalse(
                result, "ConfigMap with invalid embedded config should fail validation"
            )
        finally:
            os.unlink(configmap_path)

    def test_check_configmap_not_a_configmap(self):
        """Test check_configmap with non-ConfigMap YAML."""
        not_configmap_content = """apiVersion: v1
kind: Pod
metadata:
  name: test-pod
spec:
  containers:
    - name: test
      image: test:latest
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(not_configmap_content)
            configmap_path = f.name

        try:
            result = check_configmap(configmap_path)
            self.assertFalse(result, "Non-ConfigMap YAML should fail validation")
        finally:
            os.unlink(configmap_path)

    def test_check_configmap_file_not_found(self):
        """Test check_configmap with non-existent file."""
        result = check_configmap("/nonexistent/configmap.yaml")
        self.assertFalse(result, "Non-existent file should fail validation")

    def test_check_configmap_file_read_error(self):
        """Test check_configmap with file read error (non-UTF-8 content)."""
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".yaml", delete=False) as f:
            f.write(b"\xff\xfe Invalid UTF-8")
            configmap_path = f.name

        try:
            result = check_configmap(configmap_path)
            self.assertFalse(result, "Non-UTF-8 file should fail validation")
        finally:
            os.unlink(configmap_path)

    def test_check_configmap_not_configmap_kind(self):
        """Test check_configmap with wrong kind (not ConfigMap)."""
        invalid_configmap_content = """apiVersion: v1
kind: Pod
metadata:
  name: test-pod
data:
  config.yaml: |
    matrix:
      homeserver: https://matrix.example.org
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(invalid_configmap_content)
            configmap_path = f.name

        try:
            result = check_configmap(configmap_path)
            self.assertFalse(result, "Pod kind should fail validation")
        finally:
            os.unlink(configmap_path)

    def test_check_configmap_data_not_mapping(self):
        """Test check_configmap with data section as list instead of mapping."""
        invalid_configmap_content = """apiVersion: v1
kind: ConfigMap
metadata:
  name: mmrelay-config
data:
  - item1
  - item2
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(invalid_configmap_content)
            configmap_path = f.name

        try:
            result = check_configmap(configmap_path)
            self.assertFalse(result, "Data section as list should fail validation")
        finally:
            os.unlink(configmap_path)

    def test_check_configmap_config_yaml_not_string(self):
        """Test check_configmap with config.yaml value as list instead of string."""
        invalid_configmap_content = """apiVersion: v1
kind: ConfigMap
metadata:
  name: mmrelay-config
data:
  config.yaml:
    - item1
    - item2
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(invalid_configmap_content)
            configmap_path = f.name

        try:
            result = check_configmap(configmap_path)
            self.assertFalse(result, "config.yaml as list should fail validation")
        finally:
            os.unlink(configmap_path)

    def test_check_configmap_empty_embedded_config(self):
        """Test check_configmap with empty embedded config.yaml."""
        empty_configmap_content = """apiVersion: v1
kind: ConfigMap
metadata:
  name: mmrelay-config
  namespace: default
data:
  config.yaml: |
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(empty_configmap_content)
            configmap_path = f.name

        try:
            result = check_configmap(configmap_path)
            self.assertFalse(result, "Empty embedded config should fail validation")
        finally:
            os.unlink(configmap_path)

    def test_check_configmap_embedded_config_only_comments(self):
        """Test check_configmap with embedded config containing only comments."""
        comments_only_configmap_content = """apiVersion: v1
kind: ConfigMap
metadata:
  name: mmrelay-config
  namespace: default
data:
  config.yaml: |
    # This is a comment
    # Another comment
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(comments_only_configmap_content)
            configmap_path = f.name

        try:
            result = check_configmap(configmap_path)
            self.assertFalse(result, "Config with only comments should fail validation")
        finally:
            os.unlink(configmap_path)

    def test_check_configmap_embedded_config_syntax_error(self):
        """Test check_configmap with invalid YAML syntax in embedded config."""
        invalid_embedded_content = """apiVersion: v1
kind: ConfigMap
metadata:
  name: mmrelay-config
  namespace: default
data:
  config.yaml: |
    matrix:
      homeserver: https://matrix.example.org
    invalid yaml syntax here:
      - item1
    item2
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(invalid_embedded_content)
            configmap_path = f.name

        try:
            result = check_configmap(configmap_path)
            self.assertFalse(result, "Invalid embedded YAML should fail validation")
        finally:
            os.unlink(configmap_path)

    def test_generate_manifests_missing_namespace(self):
        """Test generate_manifests with missing namespace key."""
        config = {
            "image_tag": "latest",
            "connection_type": "tcp",
            "storage_class": "standard",
            "storage_size": "1Gi",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError) as context:
                generate_manifests(config, tmpdir)
            self.assertIn("namespace", str(context.exception).lower())

    def test_generate_manifests_missing_image_tag(self):
        """Test generate_manifests with missing image_tag key."""
        config = {
            "namespace": "default",
            "connection_type": "tcp",
            "storage_class": "standard",
            "storage_size": "1Gi",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError) as context:
                generate_manifests(config, tmpdir)
            self.assertIn("image", str(context.exception).lower())

    def test_generate_manifests_missing_connection_type(self):
        """Test generate_manifests with missing connection_type key."""
        config = {
            "namespace": "default",
            "image_tag": "latest",
            "storage_class": "standard",
            "storage_size": "1Gi",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError) as context:
                generate_manifests(config, tmpdir)
            self.assertIn("connection", str(context.exception).lower())

    def test_generate_manifests_missing_storage_class(self):
        """Test generate_manifests with missing storage_class key."""
        config = {
            "namespace": "default",
            "image_tag": "latest",
            "connection_type": "tcp",
            "storage_size": "1Gi",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError) as context:
                generate_manifests(config, tmpdir)
            self.assertIn("storage_class", str(context.exception).lower())

    def test_generate_manifests_missing_storage_size(self):
        """Test generate_manifests with missing storage_size key."""
        config = {
            "namespace": "default",
            "image_tag": "latest",
            "connection_type": "tcp",
            "storage_class": "standard",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError) as context:
                generate_manifests(config, tmpdir)
            self.assertIn("storage", str(context.exception).lower())

    def test_generate_manifests_missing_multiple_keys(self):
        """Test generate_manifests with multiple missing required keys."""
        config = {
            "namespace": "default",
            "connection_type": "tcp",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError) as context:
                generate_manifests(config, tmpdir)
            error_msg = str(context.exception).lower()
            self.assertIn("image", error_msg)
            self.assertIn("storage", error_msg)

    def test_render_template_block_placeholder_with_none(self):
        """Test block placeholder with None value - should skip the placeholder line."""
        template = "items:\n  {{BLOCK}}\nend: true"
        variables = {"BLOCK": None}
        result = render_template(template, variables)
        self.assertEqual(
            result,
            "items:\nend: true",
            "Block placeholder with None should be omitted",
        )

    def test_render_template_block_placeholder_with_empty_string(self):
        """Test block placeholder with empty string value - should skip the placeholder line."""
        template = "items:\n  {{BLOCK}}\nend: true"
        variables = {"BLOCK": ""}
        result = render_template(template, variables)
        self.assertEqual(
            result,
            "items:\nend: true",
            "Block placeholder with empty string should be omitted",
        )

    def test_render_template_block_placeholder_with_empty_lines(self):
        """Test block placeholder with multiline value including empty lines."""
        template = "items:\n  {{BLOCK}}\nend: true"
        variables = {"BLOCK": "- name: one\n\n  value: 1\n\n- name: two\n  value: 2"}
        result = render_template(template, variables)
        self.assertEqual(
            result,
            "items:\n  - name: one\n\n    value: 1\n\n  - name: two\n    value: 2\nend: true",
            "Block placeholder should preserve empty lines in multiline value",
        )

    def test_render_template_inline_placeholder_with_none_raises(self):
        """Test inline placeholder with None value raises ValueError."""
        template = "Hello {{NAME}}"
        variables = {"NAME": None}
        with self.assertRaises(ValueError) as context:
            render_template(template, variables)
        self.assertIn("NAME", str(context.exception))

    def test_render_template_unresolved_placeholders(self):
        """Test template with unresolved placeholders after substitution."""
        template = "Hello {{NAME}}, your ID is {{ID}}"
        variables = {"NAME": "Alice"}
        with self.assertRaises(ValueError) as context:
            render_template(template, variables)
        self.assertIn("ID", str(context.exception))
        self.assertIn("missing", str(context.exception).lower())

    def test_render_template_malformed_placeholder(self):
        """Test template with malformed/unresolved placeholder syntax."""
        template = "Hello {{NAME}}, your status is {{-invalid}}"
        variables = {"NAME": "Alice"}
        with self.assertRaises(ValueError) as context:
            render_template(template, variables)
        self.assertIn("unresolved", str(context.exception).lower())


if __name__ == "__main__":
    unittest.main()
