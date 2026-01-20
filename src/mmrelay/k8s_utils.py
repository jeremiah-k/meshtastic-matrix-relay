"""Kubernetes manifest generation utilities for MMRelay."""

import importlib.resources
import os
from typing import Any


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
        variables (dict): Dictionary of variable names to values

    Returns:
        str: Rendered template
    """
    rendered = template
    for key, value in variables.items():
        placeholder = f"{{{{{key}}}}}"
        rendered = rendered.replace(placeholder, str(value))
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

    config = {}

    # Namespace
    config["namespace"] = input("Kubernetes namespace [default]: ").strip() or "default"

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

    # Storage
    config["storage_class"] = (
        input("Storage class for persistent volume [standard]: ").strip() or "standard"
    )
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
    for line in sample_config_content.split("\n"):
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
    - Secret for credentials.json (if using credentials file auth)
    - Deployment with proper volume mounts

    Parameters:
        config (dict): Configuration from prompt_for_config()
        output_dir (str): Directory to write manifest files

    Returns:
        list: Paths to generated manifest files
    """
    os.makedirs(output_dir, exist_ok=True)
    generated_files = []

    # 1. Generate PersistentVolumeClaim
    pvc_template = load_template("persistentvolumeclaim.yaml")
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

    # 3. Generate Secret (only if using credentials file)
    if config.get("use_credentials_file", False):
        secret_template = load_template("secret-credentials.yaml")
        secret_content = render_template(
            secret_template, {"NAMESPACE": config["namespace"]}
        )
        secret_path = os.path.join(output_dir, "mmrelay-secret-credentials.yaml")
        with open(secret_path, "w", encoding="utf-8") as f:
            f.write(secret_content)
        generated_files.append(secret_path)

    # 4. Generate Deployment
    deployment_template = load_template("deployment.yaml")
    deployment_content = render_template(
        deployment_template,
        {
            "NAMESPACE": config["namespace"],
            "IMAGE_TAG": config["image_tag"],
        },
    )

    # Handle credentials file mounting (if enabled)
    if config.get("use_credentials_file", False):
        original_content = deployment_content
        deployment_content = deployment_content.replace(
            "# CREDENTIALS_VOLUME_MOUNT\n            # For credentials.json authentication, uncomment:\n            # - name: credentials\n            #   mountPath: /app/data/credentials.json\n            #   subPath: credentials.json\n            #   readOnly: true",
            "- name: credentials\n            mountPath: /app/data/credentials.json\n            subPath: credentials.json\n            readOnly: true",
        )
        if deployment_content == original_content:
            raise ValueError(
                "Failed to replace CREDENTIALS_VOLUME_MOUNT marker in deployment template. "
                "The template structure may have changed."
            )
        original_content = deployment_content
        deployment_content = deployment_content.replace(
            "# CREDENTIALS_VOLUME\n        # For credentials.json authentication, uncomment:\n        # - name: credentials\n        #   secret:\n        #     secretName: mmrelay-credentials-json\n        #     items:\n        #       - key: credentials.json\n        #         path: credentials.json",
            "- name: credentials\n          secret:\n            secretName: mmrelay-credentials-json\n            items:\n              - key: credentials.json\n                path: credentials.json",
        )
        if deployment_content == original_content:
            raise ValueError(
                "Failed to replace CREDENTIALS_VOLUME marker in deployment template. "
                "The template structure may have changed."
            )

    # Handle serial connection (if enabled)
    if config["connection_type"] == "serial":
        serial_device = config.get("serial_device", "/dev/ttyUSB0")
        original_content = deployment_content
        deployment_content = deployment_content.replace(
            "# SERIAL_VOLUME_MOUNT\n            # For serial connections, uncomment and adjust the device path:\n            # - name: serial-device\n            #   mountPath: /dev/ttyUSB0",
            f"- name: serial-device\n            mountPath: {serial_device}",
        )
        if deployment_content == original_content:
            raise ValueError(
                "Failed to replace SERIAL_VOLUME_MOUNT marker in deployment template. "
                "The template structure may have changed."
            )
        original_content = deployment_content
        deployment_content = deployment_content.replace(
            "# SERIAL_VOLUME\n        # For serial connections, uncomment and adjust:\n        # - name: serial-device\n        #   hostPath:\n        #     path: /dev/ttyUSB0\n        #     type: CharDevice",
            f"- name: serial-device\n          hostPath:\n            path: {serial_device}\n            type: CharDevice",
        )
        if deployment_content == original_content:
            raise ValueError(
                "Failed to replace SERIAL_VOLUME marker in deployment template. "
                "The template structure may have changed."
            )

    deployment_path = os.path.join(output_dir, "mmrelay-deployment.yaml")
    with open(deployment_path, "w", encoding="utf-8") as f:
        f.write(deployment_content)
    generated_files.append(deployment_path)

    return generated_files