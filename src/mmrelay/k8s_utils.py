"""Kubernetes manifest generation utilities for MMRelay."""

import importlib.resources
import os
import sys
from typing import Any

import yaml


def get_k8s_template_path(template_name: str) -> str:
    """
    Get the filesystem path to a Kubernetes template file.

    Parameters:
        template_name (str): Name of the template file (e.g., "deployment.yaml")

    Returns:
        str: Filesystem path to the template file
    """
    return str(importlib.resources.files("mmrelay.tools.k8s").joinpath(template_name))


def load_template(template_name: str) -> str:
    """
    Load a Kubernetes template file as a string.

    Parameters:
        template_name (str): Name of the template file

    Returns:
        str: Template content
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
    Interactively prompt user for Kubernetes deployment configuration.

    Returns:
        dict: Configuration values for manifest generation
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
    print("  1. Environment variables (recommended for Kubernetes)")
    print("  2. Credentials file (from 'mmrelay auth login')")
    auth_choice = input("Choose method [1]: ").strip() or "1"
    config["auth_method"] = "env" if auth_choice == "1" else "credentials"

    # Connection type
    print("\nMeshtastic Connection Type:")
    print("  1. TCP (network)")
    print("  2. Serial")
    conn_choice = input("Choose connection type [1]: ").strip() or "1"
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

    # E2EE
    e2ee_choice = (
        input("\nEnable End-to-End Encryption (E2EE)? [y/N]: ").strip().lower()
    )
    config["enable_e2ee"] = e2ee_choice in ["y", "yes"]

    return config


def generate_manifests(config: dict[str, Any], output_dir: str = ".") -> list[str]:
    """
    Generate Kubernetes manifest files based on configuration.

    Parameters:
        config (dict): Configuration from prompt_for_config()
        output_dir (str): Directory to write manifest files

    Returns:
        list: Paths to generated manifest files
    """
    os.makedirs(output_dir, exist_ok=True)
    generated_files = []

    # Generate PersistentVolumeClaim
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

    # Generate ConfigMap (basic config structure)
    configmap_template = load_template("configmap.yaml")
    configmap_content = render_template(
        configmap_template,
        {
            "NAMESPACE": config["namespace"],
            "CONNECTION_TYPE": config["connection_type"],
            "MESHTASTIC_HOST": config.get("meshtastic_host", "meshtastic.local"),
            "MESHTASTIC_PORT": config.get("meshtastic_port", "4403"),
            "SERIAL_DEVICE": config.get("serial_device", "/dev/ttyUSB0"),
            "E2EE_ENABLED": str(config["enable_e2ee"]).lower(),
        },
    )
    configmap_path = os.path.join(output_dir, "mmrelay-configmap.yaml")
    with open(configmap_path, "w", encoding="utf-8") as f:
        f.write(configmap_content)
    generated_files.append(configmap_path)

    # Generate Secret based on auth method
    if config["auth_method"] == "env":
        secret_template = load_template("secret-password.yaml")
        secret_content = render_template(
            secret_template, {"NAMESPACE": config["namespace"]}
        )
        secret_path = os.path.join(output_dir, "mmrelay-secret.yaml")
    else:
        secret_template = load_template("secret-credentials.yaml")
        secret_content = render_template(
            secret_template, {"NAMESPACE": config["namespace"]}
        )
        secret_path = os.path.join(output_dir, "mmrelay-secret-credentials.yaml")

    with open(secret_path, "w", encoding="utf-8") as f:
        f.write(secret_content)
    generated_files.append(secret_path)

    # Generate Deployment
    deployment_template = load_template("deployment.yaml")
    deployment_content = render_template(
        deployment_template,
        {
            "NAMESPACE": config["namespace"],
            "IMAGE_TAG": config["image_tag"],
        },
    )

    # Handle auth method sections
    if config["auth_method"] == "env":
        # Uncomment the envFrom section
        deployment_content = deployment_content.replace(
            "# AUTHENTICATION_ENV_SECTION\n        # For environment variable authentication, uncomment:\n        # envFrom:\n        #   - secretRef:\n        #       name: mmrelay-matrix-credentials",
            "envFrom:\n          - secretRef:\n              name: mmrelay-matrix-credentials",
        )
    else:
        # Uncomment credentials volume mount and volume
        deployment_content = deployment_content.replace(
            "# CREDENTIALS_VOLUME_MOUNT\n          # For credentials.json authentication, uncomment:\n          # - name: credentials\n          #   mountPath: /app/data/credentials.json\n          #   subPath: credentials.json\n          #   readOnly: true",
            "- name: credentials\n            mountPath: /app/data/credentials.json\n            subPath: credentials.json\n            readOnly: true",
        )
        deployment_content = deployment_content.replace(
            "# CREDENTIALS_VOLUME\n        # For credentials.json authentication, uncomment:\n        # - name: credentials\n        #   secret:\n        #     secretName: mmrelay-credentials-json\n        #     items:\n        #       - key: credentials.json\n        #         path: credentials.json",
            "- name: credentials\n          secret:\n            secretName: mmrelay-credentials-json\n            items:\n              - key: credentials.json\n                path: credentials.json",
        )

    # Handle serial connection
    if config["connection_type"] == "serial":
        serial_device = config.get("serial_device", "/dev/ttyUSB0")
        deployment_content = deployment_content.replace(
            "# SERIAL_VOLUME_MOUNT\n          # For serial connections, uncomment and adjust the device path:\n          # - name: serial-device\n          #   mountPath: /dev/ttyUSB0",
            f"- name: serial-device\n            mountPath: {serial_device}",
        )
        deployment_content = deployment_content.replace(
            "# SERIAL_VOLUME\n        # For serial connections, uncomment and adjust:\n        # - name: serial-device\n        #   hostPath:\n        #     path: /dev/ttyUSB0\n        #     type: CharDevice",
            f"- name: serial-device\n          hostPath:\n            path: {serial_device}\n            type: CharDevice",
        )

    deployment_path = os.path.join(output_dir, "mmrelay-deployment.yaml")
    with open(deployment_path, "w", encoding="utf-8") as f:
        f.write(deployment_content)
    generated_files.append(deployment_path)

    return generated_files


def generate_config_only(output_path: str = "config.yaml") -> str:
    """
    Generate a sample config.yaml file from the template.

    Parameters:
        output_path (str): Path where config file should be written

    Returns:
        str: Path to generated config file
    """
    from mmrelay.tools import get_sample_config_path

    sample_config_path = get_sample_config_path()

    # Check if output file already exists
    if os.path.exists(output_path):
        print(f"⚠️  Warning: {output_path} already exists")
        overwrite = input("Overwrite existing file? [y/N]: ").strip().lower()
        if overwrite not in ["y", "yes"]:
            print("Cancelled.")
            return ""

    # Copy sample config to output path
    with open(sample_config_path, "r", encoding="utf-8") as src:
        config_content = src.read()

    with open(output_path, "w", encoding="utf-8") as dst:
        dst.write(config_content)

    print(f"✅ Generated sample config at: {output_path}")
    print("\n📝 Next steps:")
    print(f"   1. Edit {output_path} with your Matrix and Meshtastic settings")
    print("   2. Create a Kubernetes ConfigMap from this file:")
    print(f"      kubectl create configmap mmrelay-config --from-file={output_path}")

    return output_path
