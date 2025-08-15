"""
CLI command constants and utilities.

This module provides a centralized registry of all CLI commands to ensure
consistency across error messages, help text, and documentation. Instead of
hardcoding command strings throughout the codebase, all references should
use these constants.

Usage:
    from mmrelay.constants.commands import get_command, suggest_command
    
    # Get a command string
    cmd = get_command('generate_config')  # Returns "mmrelay config generate"
    
    # Generate suggestion messages
    msg = suggest_command('generate_config', 'to create a sample configuration')
"""

# Command registry - single source of truth for CLI command syntax
CLI_COMMANDS = {
    # Config commands
    'generate_config': 'mmrelay config generate',
    'check_config': 'mmrelay config check',
    
    # Auth commands  
    'auth_login': 'mmrelay auth login',
    'auth_status': 'mmrelay auth status',
    
    # Service commands
    'service_install': 'mmrelay service install',
    
    # Main commands
    'start_relay': 'mmrelay',
    'show_version': 'mmrelay --version',
    'show_help': 'mmrelay --help'
}

# Deprecation mappings - maps old flags to new command keys
DEPRECATED_COMMANDS = {
    '--generate-config': 'generate_config',
    '--check-config': 'check_config', 
    '--install-service': 'service_install',
    '--auth': 'auth_login'
}


def get_command(command_key):
    """Get the current command syntax for a given command key.
    
    Args:
        command_key (str): The command key (e.g., 'generate_config')
        
    Returns:
        str: The current command syntax (e.g., 'mmrelay config generate')
        
    Example:
        >>> get_command('generate_config')
        'mmrelay config generate'
    """
    return CLI_COMMANDS.get(command_key, f"<unknown command: {command_key}>")


def get_deprecation_warning(old_flag):
    """Generate a deprecation warning message for an old command flag.
    
    Args:
        old_flag (str): The deprecated flag (e.g., '--generate-config')
        
    Returns:
        str: A formatted deprecation warning message
        
    Example:
        >>> get_deprecation_warning('--generate-config')
        "Warning: --generate-config is deprecated. Use 'mmrelay config generate' instead."
    """
    new_command_key = DEPRECATED_COMMANDS.get(old_flag)
    if new_command_key:
        new_command = get_command(new_command_key)
        return f"Warning: {old_flag} is deprecated. Use '{new_command}' instead."
    return f"Warning: {old_flag} is deprecated."


def suggest_command(command_key, purpose):
    """Generate a suggestion message for a command.
    
    Args:
        command_key (str): The command key
        purpose (str): Description of what the command does (should start with 'to')
        
    Returns:
        str: A formatted suggestion message
        
    Example:
        >>> suggest_command('generate_config', 'to generate a sample configuration file')
        "Run 'mmrelay config generate' to generate a sample configuration file."
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
        
    Example:
        >>> require_command('auth_login', 'to set up credentials.json')
        "Please run 'mmrelay auth login' to set up credentials.json."
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
        
    Example:
        >>> retry_command('auth_login', 'if authentication failed')
        "Try running 'mmrelay auth login' again if authentication failed."
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
        
    Example:
        >>> validate_command('check_config', 'to validate your configuration')
        "Use 'mmrelay config check' to validate your configuration."
    """
    command = get_command(command_key)
    return f"Use '{command}' {purpose}."


# Convenience functions for common commands
def cmd_generate_config():
    """Get the config generate command."""
    return get_command('generate_config')


def cmd_check_config():
    """Get the config check command."""
    return get_command('check_config')


def cmd_auth_login():
    """Get the auth login command."""
    return get_command('auth_login')


def cmd_auth_status():
    """Get the auth status command."""
    return get_command('auth_status')


def cmd_service_install():
    """Get the service install command."""
    return get_command('service_install')


# Common message templates
def msg_suggest_generate_config():
    """Standard message suggesting config generation."""
    return suggest_command('generate_config', 'to generate a sample configuration file')


def msg_suggest_check_config():
    """Standard message suggesting config validation."""
    return validate_command('check_config', 'to validate your configuration')


def msg_require_auth_login():
    """Standard message requiring authentication."""
    return require_command('auth_login', 'to set up credentials.json, or add matrix section to config.yaml')


def msg_retry_auth_login():
    """Standard message suggesting auth retry."""
    return retry_command('auth_login')
