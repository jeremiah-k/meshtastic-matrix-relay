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
    """
    Return a user-facing deprecation warning for a deprecated CLI flag.
    
    Looks up a replacement command for the given deprecated flag in DEPRECATED_COMMANDS.
    If a replacement exists, the returned message suggests the full new command (resolved
    via get_command). Otherwise it returns a generic guidance message pointing the user
    to `mmrelay --help`.
    
    Parameters:
        old_flag (str): Deprecated flag (e.g., '--generate-config').
    
    Returns:
        str: Formatted deprecation warning message.
    """
    new_command_key = DEPRECATED_COMMANDS.get(old_flag)
    if new_command_key:
        new_command = get_command(new_command_key)
        return f"Warning: {old_flag} is deprecated. Use '{new_command}' instead."
    return f"Warning: {old_flag} is deprecated. Run 'mmrelay --help' to see the current commands."


def suggest_command(command_key, purpose):
    """
    Return a concise suggestion message that tells the user which CLI command to run.
    
    Parameters:
        command_key (str): Key used to look up the full CLI command in the registry.
        purpose (str): Short phrase describing why to run the command (should start with "to", e.g. "to validate your configuration").
    
    Returns:
        str: Formatted suggestion like "Run '<command>' {purpose}."
    """
    command = get_command(command_key)
    return f"Run '{command}' {purpose}."


def require_command(command_key, purpose):
    """
    Return a user-facing requirement message that instructs running a registered CLI command.
    
    Parameters:
        command_key (str): Key used to look up the command in the CLI registry.
        purpose (str): Short purpose phrase (typically begins with "to"), e.g. "to generate a sample configuration file".
    
    Returns:
        str: Formatted message like "Please run '<full command>' {purpose}."
    
    Raises:
        KeyError: If `command_key` is not found in the command registry.
    """
    command = get_command(command_key)
    return f"Please run '{command}' {purpose}."


def retry_command(command_key, context=""):
    """
    Return a user-facing retry message instructing the user to run the given CLI command again.
    
    Parameters:
        command_key (str): Key from CLI_COMMANDS that identifies the command to show.
        context (str): Optional trailing context to append to the message (e.g., "after fixing X").
    
    Returns:
        str: Formatted message, either "Try running '<command>' again." or "Try running '<command>' again {context}."
    """
    command = get_command(command_key)
    if context:
        return f"Try running '{command}' again {context}."
    else:
        return f"Try running '{command}' again."


def validate_command(command_key, purpose):
    """
    Return a user-facing validation message that references a registered CLI command.
    
    command_key should be a key from the module's command registry (e.g. "check_config"); purpose is a short phrase describing the validation action (e.g. "to validate your configuration"). Returns a string like: "Use '<full-command>' {purpose}."
    """
    command = get_command(command_key)
    return f"Use '{command}' {purpose}."


# Common message templates for frequently used commands
def msg_suggest_generate_config():
    """
    Return a standardized user-facing suggestion to generate a sample configuration file.
    
    This message references the configured "generate_config" CLI command and is suitable for prompts and help text.
    
    Returns:
        str: A sentence instructing the user to run the generate-config command to generate a sample configuration file (e.g., "Run 'mmrelay config generate' to generate a sample configuration file.").
    """
    return suggest_command("generate_config", "to generate a sample configuration file")


def msg_suggest_check_config():
    """
    Return a standardized suggestion prompting the user to validate their configuration.
    
    This helper builds the user-visible message that tells users how to validate their config (e.g. by running the configured "check_config" CLI command).
    
    Returns:
        str: A full sentence suggesting the user run the config validation command.
    """
    return validate_command("check_config", "to validate your configuration")


def msg_require_auth_login():
    """
    Return a standard instruction asking the user to run the authentication command.
    
    This produces a formatted message that tells the user to run the configured "auth_login" CLI command
    to set up credentials.json or to add a Matrix section to config.yaml.
    
    Returns:
        str: A user-facing instruction string.
    """
    return require_command(
        "auth_login", "to set up credentials.json, or add matrix section to config.yaml"
    )


def msg_retry_auth_login():
    """Standard message suggesting auth retry."""
    return retry_command("auth_login")


def msg_run_auth_login():
    """
    Return a user-facing message that instructs running the auth login command to (re)generate credentials.
    
    The message prompts the user to run the authentication/login command again so new credentials (including a device_id) are created.
    
    Returns:
        str: Formatted instruction string for running the auth login command.
    """
    return msg_regenerate_credentials()


def msg_for_e2ee_support():
    """
    Return a user-facing instruction to run the authentication command required for E2EE support.
    
    Returns:
        str: A formatted message instructing the user to run the configured `auth_login` CLI command to enable end-to-end encryption (E2EE) support.
    """
    return f"For E2EE support: run '{get_command('auth_login')}'"


def msg_setup_auth():
    """
    Return a standard instruction directing the user to run the authentication setup command.
    
    The message is formatted as "Setup: <command>", where <command> is the current CLI syntax for the "auth_login" command resolved from the command registry.
    
    Returns:
        str: Formatted setup instruction pointing to the auth login CLI command.
    """
    return f"Setup: {get_command('auth_login')}"


def msg_or_run_auth_login():
    """
    Return a short suggestion offering the `auth_login` command as an alternative to setup.
    
    This function formats and returns a user-facing message that tells the caller to
    run the configured `auth_login` CLI command to create or set up credentials.json.
    
    Returns:
        str: A message of the form "or run '<command>' to set up credentials.json".
    """
    return f"or run '{get_command('auth_login')}' to set up credentials.json"


def msg_setup_authentication():
    """Standard message for authentication setup."""
    return f"Setup authentication: {get_command('auth_login')}"


def msg_regenerate_credentials():
    """
    Return a standardized instruction prompting the user to re-run the authentication command to regenerate credentials that include a `device_id`.
    
    Returns:
        str: Message instructing the user to run the auth login command again to produce new credentials containing a `device_id`.
    """
    return f"Please run '{get_command('auth_login')}' again to generate new credentials that include a device_id."
