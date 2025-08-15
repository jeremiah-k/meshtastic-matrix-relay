"""
CLI utilities and command registry.

This module provides a centralized registry of all CLI commands to ensure
consistency across error messages, help text, and documentation. It's separate
from cli.py to avoid circular dependencies when other modules need to reference
CLI commands.

Usage:
    from mmrelay.cli_utils import get_command, suggest_command

    # Get a command string
    cmd = get_command('generate_config')  # Returns "mmrelay config generate"

    # Generate suggestion messages
    msg = suggest_command('generate_config', 'to create a sample configuration')
"""

# Command registry - single source of truth for CLI command syntax
CLI_COMMANDS = {
    # Config commands
    "generate_config": "mmrelay config generate",
    "check_config": "mmrelay config check",
    # Auth commands
    "auth_login": "mmrelay auth login",
    "auth_status": "mmrelay auth status",
    # Service commands
    "service_install": "mmrelay service install",
    # Main commands
    "start_relay": "mmrelay",
    "show_version": "mmrelay --version",
    "show_help": "mmrelay --help",
}

# Deprecation mappings - maps old flags to new command keys
DEPRECATED_COMMANDS = {
    "--generate-config": "generate_config",
    "--check-config": "check_config",
    "--install-service": "service_install",
    "--auth": "auth_login",
}


def get_command(command_key):
    """Get the current command syntax for a given command key.

    Args:
        command_key (str): The command key (e.g., 'generate_config')

    Returns:
        str: The current command syntax (e.g., 'mmrelay config generate')

    Raises:
        KeyError: If the command key is not found in the registry
    """
    if command_key not in CLI_COMMANDS:
        raise KeyError(f"Unknown CLI command key: {command_key}")
    return CLI_COMMANDS[command_key]


def get_deprecation_warning(old_flag):
    """Generate a deprecation warning message for an old command flag.

    Args:
        old_flag (str): The deprecated flag (e.g., '--generate-config')

    Returns:
        str: A formatted deprecation warning message
    """
    new_command_key = DEPRECATED_COMMANDS.get(old_flag)
    if new_command_key:
        new_command = get_command(new_command_key)
        return f"Warning: {old_flag} is deprecated. Use '{new_command}' instead."
    return f"Warning: {old_flag} is deprecated. Run 'mmrelay --help' to see the current commands."


def suggest_command(command_key, purpose):
    """Generate a suggestion message for a command.

    Args:
        command_key (str): The command key
        purpose (str): Description of what the command does (should start with 'to')

    Returns:
        str: A formatted suggestion message
    """
    command = get_command(command_key)
    return f"Run '{command}' {purpose}."


def require_command(command_key, purpose):
    """Generate a requirement message for a command.

    Args:
        command_key (str): The command key
        purpose (str): Description of what the command does (should start with 'to')

    Returns:
        str: A formatted requirement message
    """
    command = get_command(command_key)
    return f"Please run '{command}' {purpose}."


def retry_command(command_key, context=""):
    """Generate a retry message for a command.

    Args:
        command_key (str): The command key
        context (str): Optional context for why to retry

    Returns:
        str: A formatted retry message
    """
    command = get_command(command_key)
    if context:
        return f"Try running '{command}' again {context}."
    else:
        return f"Try running '{command}' again."


def validate_command(command_key, purpose):
    """Generate a validation message for a command.

    Args:
        command_key (str): The command key
        purpose (str): Description of what to validate

    Returns:
        str: A formatted validation message
    """
    command = get_command(command_key)
    return f"Use '{command}' {purpose}."


# Common message templates for frequently used commands
def msg_suggest_generate_config():
    """Standard message suggesting config generation."""
    return suggest_command("generate_config", "to generate a sample configuration file")


def msg_suggest_check_config():
    """Standard message suggesting config validation."""
    return validate_command("check_config", "to validate your configuration")


def msg_require_auth_login():
    """Standard message requiring authentication."""
    return require_command(
        "auth_login", "to set up credentials.json, or add matrix section to config.yaml"
    )


def msg_retry_auth_login():
    """Standard message suggesting auth retry."""
    return retry_command("auth_login")


def msg_run_auth_login():
    """Standard message for running auth login."""
    return msg_regenerate_credentials()


def msg_for_e2ee_support():
    """Standard message for E2EE support setup."""
    return f"For E2EE support: run '{get_command('auth_login')}'"


def msg_setup_auth():
    """Standard message for auth setup."""
    return f"Setup: {get_command('auth_login')}"


def msg_or_run_auth_login():
    """Standard message suggesting auth login as alternative."""
    return f"or run '{get_command('auth_login')}' to set up credentials.json"


def msg_setup_authentication():
    """Standard message for authentication setup."""
    return f"Setup authentication: {get_command('auth_login')}"


def msg_regenerate_credentials():
    """Standard message for regenerating credentials."""
    return f"Please run '{get_command('auth_login')}' again to generate new credentials that include a device_id."
