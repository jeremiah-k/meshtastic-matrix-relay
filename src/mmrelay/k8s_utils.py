"""Kubernetes manifest generation utilities for MMRelay."""

import argparse
import importlib.resources
import json
import os
import re
import shutil
import subprocess
from typing import Any

from mmrelay.log_utils import get_logger

_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
_UNRESOLVED_RE = re.compile(r"\{\{[^}]+\}\}")

_MISSING_VARS_MSG = "Missing template variables: {vars}"
_MISSING_VALUE_MSG = "Missing template value for '{key}'"
_UNRESOLVED_PLACEHOLDERS_MSG = "Unresolved template placeholders: {vars}"
_UNRESOLVED_TOKENS_MSG = "Unresolved template placeholders: {tokens}"
_MISSING_CONFIG_KEYS_MSG = "Missing required config keys: {keys}"

logger = get_logger(__name__)

_SERIAL_CONTAINER_DEVICE_PATH = "/dev/ttyUSB0"


def _get_storage_classes_from_kubectl() -> list[tuple[str, bool]] | None:
    """Return storage class names and default flags using kubectl, if available."""
    kubectl = shutil.which("kubectl")
    if not kubectl:
        logger.debug("kubectl not found; skipping storage class discovery")
        return None
    try:
        result = subprocess.run(
            [kubectl, "get", "storageclass", "-o", "json"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as e:
        logger.debug("kubectl execution failed: %s", e)
        return None

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if stderr:
            logger.debug("kubectl get storageclass failed: %s", stderr)
        return None

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.debug("Failed to parse kubectl storageclass JSON output")
        return None

    classes: list[tuple[str, bool]] = []
    for item in payload.get("items", []):
        metadata = item.get("metadata", {}) if isinstance(item, dict) else {}
        name = metadata.get("name")
        if not name:
            continue
        annotations = metadata.get("annotations", {}) or {}
        default_annotation = annotations.get(
            "storageclass.kubernetes.io/is-default-class", ""
        )
        legacy_default_annotation = annotations.get(
            "storageclass.beta.kubernetes.io/is-default-class", ""
        )
        is_default = (
            str(default_annotation).lower() == "true"
            or str(legacy_default_annotation).lower() == "true"
        )
        classes.append((name, is_default))

    return classes or None


def _get_current_namespace_from_kubectl() -> str | None:
    """Return the current namespace from kubectl context, if available."""
    kubectl = shutil.which("kubectl")
    if not kubectl:
        logger.debug("kubectl not found; skipping namespace discovery")
        return None
    try:
        result = subprocess.run(
            [kubectl, "config", "view", "--minify", "-o", "json"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as e:
        logger.debug("kubectl execution failed: %s", e)
        return None

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if stderr:
            logger.debug("kubectl config view failed: %s", stderr)
        return None

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.debug("Failed to parse kubectl config JSON output")
        return None

    contexts = payload.get("contexts", [])
    if not contexts:
        return None

    context = contexts[0].get("context", {}) if isinstance(contexts[0], dict) else {}
    namespace = context.get("namespace")
    if isinstance(namespace, str) and namespace.strip():
        return namespace.strip()
    return None


def _render_matrix_credentials_secret(namespace: str) -> str:
    """Render a Secret manifest for env-var based Matrix credentials."""
    return f"""apiVersion: v1
kind: Secret
metadata:
  name: mmrelay-matrix-credentials
  namespace: {namespace}
  labels:
    app: mmrelay
type: Opaque
stringData:
  # Environment-variable auth for Matrix credentials.
  # Replace the placeholders below with your real values.
  # WARNING: Do not commit this file with real credentials to version control!
  MMRELAY_MATRIX_HOMESERVER: "<your-homeserver-url>"
  MMRELAY_MATRIX_BOT_USER_ID: "<your-bot-user-id>"
  MMRELAY_MATRIX_PASSWORD: "<your-password>"
"""


def _is_valid_k8s_namespace(namespace: str) -> bool:
    """Validate Kubernetes namespace name (DNS subdomain format)."""
    if not namespace or len(namespace) > 253:
        return False
    pattern = r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"
    if not re.match(pattern, namespace):
        return False
    labels = namespace.split("-")
    return all(len(label) <= 63 for label in labels)


def _is_valid_k8s_resource_quantity(size: str) -> bool:
    """Validate Kubernetes resource quantity (e.g., '1Gi', '500Mi')."""
    pattern = r"^[0-9]+(\.[0-9]+)?((E|e|P|p|T|t|G|g|M|m|K|k)(i|I)?)?$"
    return re.match(pattern, size) is not None


def _is_valid_host_path(path: str) -> bool:
    """Validate that a path is a safe hostPath value."""
    if not path or not isinstance(path, str):
        return False
    if ".." in path:
        return False
    if not path.startswith("/"):
        return False
    return True


def get_k8s_template_path(template_name: str) -> str:
    """
    Resolve the filesystem path to a Kubernetes template file bundled in the mmrelay.tools.k8s package.

    Parameters:
        template_name (str): Template filename (for example, "deployment.yaml").

    Returns:
        str: Filesystem path to the specified template file.
    """
    return str(importlib.resources.files("mmrelay.tools.k8s").joinpath(template_name))


def load_template(template_name: str) -> str:
    """
    Load a Kubernetes template by name and return its content.

    The template file is resolved from the mmrelay.tools.k8s templates directory and read using UTF-8 encoding.

    Parameters:
        template_name (str): Name of the template file to load (located in the k8s templates package).

    Returns:
        str: The template file content.
    """
    template_path = get_k8s_template_path(template_name)
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()


def render_template(template: str, variables: dict[str, Any]) -> str:
    """
    Render a template string with variable substitutions.

    Parameters:
        template (str): Template content with {{VARIABLE}} placeholders
        variables (dict): Dictionary of variable names to values.
            - Placeholders on their own line are treated as block substitutions.
              Block placeholders with None or empty string values are skipped entirely.
            - Inline placeholders must always be present in the variables dict.
              Inline placeholders with None or missing keys raise ValueError.
        Block substitutions inherit the line indentation of the placeholder line.

    Returns:
        str: Rendered template
    """
    placeholders = set(_PLACEHOLDER_RE.findall(template))
    missing = sorted(placeholders - set(variables.keys()))
    if missing:
        missing_vars = ", ".join(missing)
        raise ValueError(_MISSING_VARS_MSG.format(vars=missing_vars))

    rendered_lines: list[str] = []
    for line in template.splitlines():
        match = _PLACEHOLDER_RE.fullmatch(line.strip())
        if match:
            key = match.group(1)
            value = variables.get(key)
            if value is None or value == "":
                continue
            value_str = str(value)
            indent = line[: len(line) - len(line.lstrip(" "))]
            for value_line in value_str.splitlines():
                if value_line:
                    rendered_lines.append(f"{indent}{value_line}")
                else:
                    rendered_lines.append("")
            continue
        rendered_lines.append(line)

    rendered = "\n".join(rendered_lines)

    def replace_inline(match: re.Match[str]) -> str:
        key = match.group(1)
        value = variables.get(key)
        if value is None:
            raise ValueError(_MISSING_VALUE_MSG.format(key=key))
        return str(value)

    rendered = _PLACEHOLDER_RE.sub(replace_inline, rendered)

    leftover = sorted(set(_PLACEHOLDER_RE.findall(rendered)))
    if leftover:
        leftover_vars = ", ".join(leftover)
        raise ValueError(_UNRESOLVED_PLACEHOLDERS_MSG.format(vars=leftover_vars))

    unresolved_tokens = sorted(set(_UNRESOLVED_RE.findall(rendered)))
    if unresolved_tokens:
        unresolved = ", ".join(unresolved_tokens)
        raise ValueError(_UNRESOLVED_TOKENS_MSG.format(tokens=unresolved))

    return rendered


def prompt_for_config() -> dict[str, Any]:
    """
    Interactively collect MMRelay deployment settings via console prompts for Kubernetes manifest generation.

    Prompts the user for namespace, container image tag, authentication method, connection type and related connection details, and persistent storage settings. The function returns a dictionary with the collected configuration; keys present depend on choices (e.g., TCP vs serial connection).

    Returns:
        dict: Collected configuration containing:
            - namespace (str): Kubernetes namespace to use.
            - image_tag (str): MMRelay container image tag.
            - use_credentials_file (bool): True if a credentials file (Secret) should be used, False to use environment-variable-based auth.
            - generate_secret_manifest (bool): True if a Secret manifest should be generated.
            - connection_type (str): "tcp" or "serial".
            - meshtastic_host (str): Hostname/IP of Meshtastic device (present when connection_type == "tcp").
            - meshtastic_port (str): Port of Meshtastic device (present when connection_type == "tcp").
            - serial_device (str): Host serial device path (present when connection_type == "serial").
            - storage_class (str): StorageClass name for the persistent volume.
            - storage_size (str): Size for the persistent volume (e.g., "1Gi").
    """
    print("\n🚀 MMRelay Kubernetes Manifest Generator\n")
    print("This wizard will help you generate Kubernetes manifests for MMRelay.")
    print("Press Ctrl+C at any time to cancel.\n")

    config: dict[str, Any] = {}

    # Namespace
    namespace_default = _get_current_namespace_from_kubectl() or "default"
    if not _is_valid_k8s_namespace(namespace_default):
        namespace_default = "default"
    if namespace_default != "default":
        print(f"Detected namespace from kubectl context: {namespace_default}")
    config["namespace"] = (
        input(f"Kubernetes namespace [{namespace_default}]: ").strip()
        or namespace_default
    )
    while not _is_valid_k8s_namespace(config["namespace"]):
        print("Invalid namespace. Kubernetes namespaces must be DNS subdomain format.")
        print("  Example: my-app, my-namespace, production")
        config["namespace"] = (
            input("Kubernetes namespace [default]: ").strip() or "default"
        )

    # Image tag
    config["image_tag"] = input("MMRelay image tag [latest]: ").strip() or "latest"

    # Authentication method
    print("\nAuthentication Method:")
    print("  1. Environment variables (simple, uses K8s secrets)")
    print("  2. Credentials file (advanced, E2EE support via 'mmrelay auth login')")
    auth_choice = input("Choose method [1]: ").strip() or "1"
    if auth_choice not in {"1", "2"}:
        print("Invalid choice; defaulting to 1.")
        auth_choice = "1"
    config["use_credentials_file"] = auth_choice == "2"

    # Secret manifest generation
    print("\nSecret Manifest:")
    print("  Generate a Kubernetes Secret manifest with placeholder values.")
    print("  You can edit it later or create the Secret manually with kubectl.")
    secret_choice = input("Generate Secret manifest file? [Y/n]: ").strip().lower()
    config["generate_secret_manifest"] = secret_choice in {"", "y", "yes"}

    # Connection type
    print("\nMeshtastic Connection Type:")
    print("  1. TCP (network)")
    print("  2. Serial")
    conn_choice = input("Choose connection type [1]: ").strip() or "1"
    if conn_choice not in {"1", "2"}:
        print("Invalid choice; defaulting to 1.")
        conn_choice = "1"
    config["connection_type"] = "tcp" if conn_choice == "1" else "serial"

    if config["connection_type"] == "tcp":
        config["meshtastic_host"] = (
            input("Meshtastic device hostname/IP [meshtastic.local]: ").strip()
            or "meshtastic.local"
        )
        config["meshtastic_port"] = (
            input("Meshtastic device port [4403]: ").strip() or "4403"
        )
    else:
        config["serial_device"] = (
            input("Serial device path [/dev/ttyUSB0]: ").strip() or "/dev/ttyUSB0"
        )
        while not _is_valid_host_path(config["serial_device"]):
            print(f"Invalid device path: {config['serial_device']}")
            config["serial_device"] = (
                input("Serial device path [/dev/ttyUSB0]: ").strip() or "/dev/ttyUSB0"
            )

    # Storage
    storage_classes = _get_storage_classes_from_kubectl()
    storage_class_default = "standard"
    if storage_classes:
        print("\nDetected StorageClasses:")
        for name, is_default in storage_classes:
            suffix = " (default)" if is_default else ""
            print(f"  - {name}{suffix}")
        default_storage_class = next(
            (name for name, is_default in storage_classes if is_default), None
        )
        if default_storage_class:
            storage_class_default = default_storage_class
        else:
            storage_class_default = storage_classes[0][0]
            print(
                f"No default StorageClass detected; using '{storage_class_default}' as the suggested default."
            )

    config["storage_class"] = (
        input(
            f"Storage class for persistent volume [{storage_class_default}]: "
        ).strip()
        or storage_class_default
    )
    config["storage_size"] = (
        input("Storage size for data volume [1Gi]: ").strip() or "1Gi"
    )
    while not _is_valid_k8s_resource_quantity(config["storage_size"]):
        print("Invalid storage size. Use Kubernetes resource quantity format.")
        print("  Examples: 1Gi, 500Mi, 10Gi")
        config["storage_size"] = (
            input("Storage size for data volume [1Gi]: ").strip() or "1Gi"
        )

    return config


def generate_configmap_from_sample(namespace: str, output_path: str) -> str:
    """
    Create a Kubernetes ConfigMap YAML that embeds the project's sample configuration under `data.config.yaml`.

    Parameters:
        namespace (str): Kubernetes namespace to set on the ConfigMap.
        output_path (str): Filesystem path where the generated ConfigMap YAML will be written.

    Returns:
        str: The path to the written ConfigMap file (`output_path`).
    """
    from mmrelay.tools import get_sample_config_path

    sample_config_path = get_sample_config_path()

    with open(sample_config_path, "r", encoding="utf-8") as f:
        sample_config_content = f.read()

    # Create ConfigMap YAML with embedded config
    configmap_content = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: mmrelay-config
  namespace: {namespace}
  labels:
    app: mmrelay
data:
  config.yaml: |
"""
    # Indent each line of the config for proper YAML
    for line in sample_config_content.splitlines():
        configmap_content += f"    {line}\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(configmap_content)

    return output_path


def generate_manifests(config: dict[str, Any], output_dir: str = ".") -> list[str]:
    """
    Generate Kubernetes manifest files based on configuration.

    Generates:
    - PersistentVolumeClaim for data storage
    - ConfigMap from sample_config.yaml (single source of truth)
    - Optional Secret manifest (credentials.json or env-var auth)
    - Deployment with proper volume mounts

    Parameters:
        config (dict): Configuration from prompt_for_config()
        output_dir (str): Directory to write manifest files

    Returns:
        list: Paths to generated manifest files
    """
    required_keys = {
        "namespace",
        "storage_class",
        "storage_size",
        "connection_type",
        "image_tag",
    }
    missing = sorted(required_keys - set(config))
    if missing:
        missing_keys = ", ".join(missing)
        raise ValueError(_MISSING_CONFIG_KEYS_MSG.format(keys=missing_keys))
    os.makedirs(output_dir, exist_ok=True)
    generated_files = []

    # 1. Generate PersistentVolumeClaim
    pvc_template = load_template("persistentvolumeclaim.yaml.tpl")
    pvc_content = render_template(
        pvc_template,
        {
            "NAMESPACE": config["namespace"],
            "STORAGE_CLASS": config["storage_class"],
            "STORAGE_SIZE": config["storage_size"],
        },
    )
    pvc_path = os.path.join(output_dir, "mmrelay-pvc.yaml")
    with open(pvc_path, "w", encoding="utf-8") as f:
        f.write(pvc_content)
    generated_files.append(pvc_path)

    # 2. Generate ConfigMap from sample_config.yaml (single source of truth)
    configmap_path = os.path.join(output_dir, "mmrelay-configmap.yaml")
    generate_configmap_from_sample(config["namespace"], configmap_path)
    generated_files.append(configmap_path)

    # 3. Generate Secret (optional)
    generate_secret_manifest = config.get("generate_secret_manifest")
    if generate_secret_manifest is None:
        generate_secret_manifest = bool(config.get("use_credentials_file", False))

    if generate_secret_manifest:
        if config.get("use_credentials_file", False):
            secret_template = load_template("secret-credentials.yaml.tpl")
            secret_content = render_template(
                secret_template, {"NAMESPACE": config["namespace"]}
            )
            secret_path = os.path.join(output_dir, "mmrelay-secret-credentials.yaml")
        else:
            secret_content = _render_matrix_credentials_secret(config["namespace"])
            secret_path = os.path.join(
                output_dir, "mmrelay-secret-matrix-credentials.yaml"
            )
        with open(secret_path, "w", encoding="utf-8") as f:
            f.write(secret_content)
        generated_files.append(secret_path)

    # 4. Generate Deployment
    serial_device = config.get("serial_device", "/dev/ttyUSB0")
    credentials_volume_mount = "\n".join(
        [
            "- name: credentials",
            "  mountPath: /app/data/credentials.json",
            "  subPath: credentials.json",
            "  readOnly: true",
        ]
    )
    credentials_volume_mount_comment = "\n".join(
        [
            "# For credentials.json authentication, add:",
            "# - name: credentials",
            "#   mountPath: /app/data/credentials.json",
            "#   subPath: credentials.json",
            "#   readOnly: true",
        ]
    )
    credentials_volume = "\n".join(
        [
            "- name: credentials",
            "  secret:",
            "    secretName: mmrelay-credentials-json",
            "    items:",
            "      - key: credentials.json",
            "        path: credentials.json",
        ]
    )
    credentials_volume_comment = "\n".join(
        [
            "# For credentials.json authentication, add:",
            "# - name: credentials",
            "#   secret:",
            "#     secretName: mmrelay-credentials-json",
            "#     items:",
            "#       - key: credentials.json",
            "#         path: credentials.json",
        ]
    )
    serial_volume_mount = "\n".join(
        [
            "- name: serial-device",
            f"  mountPath: {_SERIAL_CONTAINER_DEVICE_PATH}",
        ]
    )
    serial_volume_mount_comment = "\n".join(
        [
            "# For serial connections, add:",
            "# - name: serial-device",
            f"#   mountPath: {_SERIAL_CONTAINER_DEVICE_PATH}",
        ]
    )
    serial_volume = "\n".join(
        [
            "# The host device path (user input) is used in hostPath",
            "# The container device path is fixed for consistency with container OS",
            "- name: serial-device",
            "  hostPath:",
            f"    path: {serial_device}",
            "    type: CharDevice",
        ]
    )
    serial_volume_comment = "\n".join(
        [
            "# For serial connections, add:",
            "# - name: serial-device",
            "#   hostPath:",
            f"#     path: {serial_device}",
            "#     type: CharDevice",
        ]
    )
    env_from_section = "\n".join(
        [
            "# Matrix credentials from Kubernetes Secret (for env var authentication)",
            "# If not using credentials.json, create a secret with:",
            "#   kubectl create secret generic mmrelay-matrix-credentials \\",
            "#     --from-literal=MMRELAY_MATRIX_HOMESERVER=https://matrix.example.org \\",
            "#     --from-literal=MMRELAY_MATRIX_BOT_USER_ID=@bot:matrix.example.org \\",
            "#     --from-literal=MMRELAY_MATRIX_PASSWORD=your_password",
            "envFrom:",
            "  - secretRef:",
            "      name: mmrelay-matrix-credentials",
            "      optional: true",
        ]
    )
    env_from_section_comment = "\n".join(
        [
            "# Matrix credentials from Kubernetes Secret (for env var authentication)",
            "# If not using credentials.json, create a secret with:",
            "#   kubectl create secret generic mmrelay-matrix-credentials \\",
            "#     --from-literal=MMRELAY_MATRIX_HOMESERVER=https://matrix.example.org \\",
            "#     --from-literal=MMRELAY_MATRIX_BOT_USER_ID=@bot:matrix.example.org \\",
            "#     --from-literal=MMRELAY_MATRIX_PASSWORD=your_password",
            "# envFrom:  # Uncomment for environment variable authentication",
            "#   - secretRef:",
            "#       name: mmrelay-matrix-credentials",
            "#       optional: true",
        ]
    )
    if config.get("use_credentials_file", False):
        credentials_volume_mount_block = credentials_volume_mount
        credentials_volume_block = credentials_volume
        env_from_block = env_from_section_comment
    else:
        credentials_volume_mount_block = credentials_volume_mount_comment
        credentials_volume_block = credentials_volume_comment
        env_from_block = env_from_section
    if config["connection_type"] == "serial":
        serial_volume_mount_block = serial_volume_mount
        serial_volume_block = serial_volume
    else:
        serial_volume_mount_block = serial_volume_mount_comment
        serial_volume_block = serial_volume_comment

    # Generate security context based on connection type
    if config["connection_type"] == "serial":
        security_context_block = """securityContext:
  # WARNING: Required for serial device access.
  # This runs the container as root. Review your cluster's security policies.
  runAsUser: 0
  runAsGroup: 0
  supplementalGroups: [20] # 'dialout' group, may vary"""
    else:
        security_context_block = """securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  runAsGroup: 1000
  fsGroup: 1000"""

    deployment_template = load_template("deployment.yaml.tpl")
    deployment_content = render_template(
        deployment_template,
        {
            "NAMESPACE": config["namespace"],
            "IMAGE_TAG": config["image_tag"],
            "CREDENTIALS_VOLUME_MOUNT": credentials_volume_mount_block,
            "CREDENTIALS_VOLUME": credentials_volume_block,
            "ENV_FROM_SECTION": env_from_block,
            "SERIAL_VOLUME_MOUNT": serial_volume_mount_block,
            "SERIAL_VOLUME": serial_volume_block,
            "SECURITY_CONTEXT": security_context_block,
        },
    )

    deployment_path = os.path.join(output_dir, "mmrelay-deployment.yaml")
    with open(deployment_path, "w", encoding="utf-8") as f:
        f.write(deployment_content)
    generated_files.append(deployment_path)

    return generated_files


def check_configmap(configmap_path: str) -> bool:
    """
    Validate configuration embedded in a Kubernetes ConfigMap YAML file.

    Loads a ConfigMap YAML, extracts the embedded config.yaml from the data section,
    and validates it using the same logic as `mmrelay config check`. This allows
    users to validate their ConfigMap before deploying to Kubernetes.

    Parameters:
        configmap_path (str): Path to the ConfigMap YAML file to validate.

    Returns:
        bool: `True` if the ConfigMap is valid and the embedded config passes all
        checks, `False` otherwise.
    """

    from mmrelay.config import validate_yaml_syntax

    # Check if file exists
    if not os.path.isfile(configmap_path):
        print(f"Error: ConfigMap file not found: {configmap_path}")
        return False

    # Load ConfigMap YAML
    try:
        with open(configmap_path, "r", encoding="utf-8") as f:
            configmap_content = f.read()
    except (OSError, UnicodeDecodeError):
        logger.exception("Error reading ConfigMap file")
        return False

    # Validate YAML syntax
    is_valid, message, configmap = validate_yaml_syntax(
        configmap_content, configmap_path
    )
    if not is_valid:
        print(f"Error: YAML Syntax Error in ConfigMap:\n{message}")
        return False

    # Check if it's a ConfigMap
    if not isinstance(configmap, dict):
        print("Error: ConfigMap YAML is empty or not a mapping")
        return False
    if configmap.get("kind") != "ConfigMap":
        print(f"Error: File is not a ConfigMap (kind: {configmap.get('kind')})")
        return False

    # Check for data section
    if "data" not in configmap:
        print("Error: ConfigMap is missing 'data' section")
        return False

    data_section = configmap["data"]
    if not isinstance(data_section, dict):
        print("Error: ConfigMap 'data' section must be a mapping (YAML object)")
        return False

    # Check for config.yaml in data
    if "config.yaml" not in data_section:
        print("Error: ConfigMap data section is missing 'config.yaml' key")
        return False

    # Extract embedded config
    embedded_config_content = data_section["config.yaml"]
    if not isinstance(embedded_config_content, str):
        print("Error: ConfigMap 'config.yaml' value must be a string (YAML text)")
        return False

    print(f"Found embedded configuration in ConfigMap: {configmap_path}")

    # Validate embedded config.yaml
    logger.info("Validating embedded configuration...")
    is_config_valid, config_message, embedded_config = validate_yaml_syntax(
        embedded_config_content, f"{configmap_path}:config.yaml"
    )

    if not is_config_valid:
        print(f"Error: YAML Syntax Error in embedded config.yaml:\n{config_message}")
        return False

    if config_message:
        logger.warning(
            f"YAML Style Warnings in embedded config.yaml:\n{config_message}"
        )

    # Check if embedded config is empty
    if not embedded_config:
        print("Error: Embedded config.yaml is empty or contains only comments")
        return False

    # Import and reuse check_config validation logic
    # Create a temporary file with the embedded config
    import tempfile

    from mmrelay.cli import check_config

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as temp_file:
        temp_file.write(embedded_config_content)
        temp_config_path = temp_file.name

    try:
        # Create mock args pointing to temp config file
        args_mock = argparse.Namespace()
        args_mock.config = temp_config_path
        args_mock.data_dir = None
        args_mock.log_level = None
        args_mock.logfile = None
        args_mock.allow_missing_matrix_auth = True

        # Run validation
        result = check_config(args_mock)

        if result:
            print("ConfigMap configuration is valid!")
            print("Ready to deploy to Kubernetes.")
        else:
            print("Error: ConfigMap configuration has errors.")
            print("Fix the issues above before deploying to Kubernetes.")

        return result
    finally:
        # Clean up temp file
        try:
            os.unlink(temp_config_path)
        except (OSError, PermissionError):
            pass
